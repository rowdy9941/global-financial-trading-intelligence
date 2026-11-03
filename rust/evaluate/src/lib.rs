//! E2 artifact loader — verify a D5-signed artifact file and expose its
//! frozen parameters (A0 baseline §3: "E2 loads only signed artifacts").
//!
//! Canonical-row scheme = the W1 gate's exactly (docs/gates
//! W1-GATE-ARTIFACT): HMAC-SHA256 over the 9-field canonical JSON row.
//! Python's shortest-roundtrip float repr is reproduced by `py_float_repr`
//! (Rust `{}` agrees on all shortest-roundtrip values; the only divergence —
//! integral floats, which Python spells `3.0` — is handled explicitly; wf
//! values are rounded to 4 decimals upstream so both sides print identically).

use hmac::{Hmac, KeyInit, Mac};
use serde_json::Value;
use sha2::Sha256;
use std::path::Path;

type HmacSha256 = Hmac<Sha256>;

#[derive(Debug)]
pub enum VerifyError {
    /// File unreadable / not an artifact at the given path.
    Load(String),
    /// Schema wrong — schema errors are refuses, never sig errors.
    Schema(String),
    /// HMAC mismatch (tamper, wrong secret, or other byte drift).
    SigMismatch,
}

impl std::fmt::Display for VerifyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            VerifyError::Load(p) => write!(f, "artifact load: {p}"),
            VerifyError::Schema(m) => write!(f, "artifact schema: {m}"),
            VerifyError::SigMismatch => {
                write!(f, "artifact sig mismatch (tampered or wrong secret)")
            }
        }
    }
}

impl std::error::Error for VerifyError {}

/// E2 frozen constants handed to the hot path.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Frozen {
    pub fast: usize,
    pub slow: usize,
    pub eps: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Artifact {
    pub artifact_id: String,
    pub feature_hash: String,
    pub dataset_key: String,
    pub dataset_sha256: String,
    pub frozen: Frozen,
}

/// Python `repr(float)` parity for the canonical D5 row (crate docs).
fn py_float_repr(v: f64) -> String {
    if v.is_finite() && v.trunc() == v && v.abs() < 1e16 {
        format!("{v:.1}")
    } else {
        format!("{v}")
    }
}

fn hjay(s: &str) -> String {
    serde_json::to_string(s).unwrap_or_else(|_| {
        serde_json::to_string(&Value::String(s.to_string())).unwrap_or_else(|_| format!("\"{s}\""))
    })
}

#[allow(clippy::too_many_arguments)]
fn build_row(
    dk: &str,
    sha: &str,
    disc: &str,
    eps: f64,
    fast: f64,
    slow: f64,
    oos: f64,
    dsr: f64,
    folds: f64,
) -> String {
    format!(
        "{{\"dataset_key\":{},\"dataset_sha256\":{},\"discriminator\":{},\
         \"eps\":{},\"fast\":{},\"slow\":{},\"wf_dsr\":{},\"wf_folds\":{},\
         \"wf_oos_sharpe\":{}}}",
        hjay(dk),
        hjay(sha),
        hjay(disc),
        py_float_repr(eps),
        fast as u64,
        slow as u64,
        py_float_repr(dsr),
        folds as u64,
        py_float_repr(oos),
    )
}

/// Verify sig over the canonical row and return the frozen artifact.
pub fn verify(path: &Path, secret: &[u8]) -> Result<Artifact, VerifyError> {
    let raw = std::fs::read_to_string(path)
        .map_err(|e| VerifyError::Load(format!("{}: {e}", path.display())))?;
    let v: Value =
        serde_json::from_str(&raw).map_err(|e| VerifyError::Schema(format!("parse: {e}")))?;

    let field = |k: &str| -> Result<&Value, VerifyError> {
        v.get(k)
            .ok_or_else(|| VerifyError::Schema(format!("missing field {k:?}")))
    };
    let sig = field("sig")?
        .as_str()
        .ok_or_else(|| VerifyError::Schema("sig is not a string".into()))?;
    let params = field("params")?;
    let wf = field("wf")?;
    let num = |o: &Value, k: &str, g: &str| -> Result<f64, VerifyError> {
        o.get(k)
            .and_then(Value::as_f64)
            .ok_or_else(|| VerifyError::Schema(format!("{g}.{k} is not numeric")))
    };
    let eps = num(params, "eps", "params")?;
    let fast = num(params, "fast", "params")?;
    let slow = num(params, "slow", "params")?;
    let oos = num(wf, "oos_sharpe", "wf")?;
    let dsr = num(wf, "dsr", "wf")?;
    let folds = num(wf, "folds", "wf")?;
    let get = |k: &str| -> Result<&str, VerifyError> {
        field(k)?
            .as_str()
            .ok_or_else(|| VerifyError::Schema(format!("{k} is not a string")))
    };

    let row = build_row(
        get("dataset_key")?,
        get("dataset_sha256")?,
        get("discriminator")?,
        eps,
        fast,
        slow,
        oos,
        dsr,
        folds,
    );
    let mut mac = HmacSha256::new_from_slice(secret)
        .map_err(|e| VerifyError::Schema(format!("key error: {e}")))?;
    mac.update(row.as_bytes());
    let want: Vec<u8> = hex::decode(sig).map_err(|_| VerifyError::SigMismatch)?;
    if mac.verify_slice(&want).is_err() {
        return Err(VerifyError::SigMismatch);
    }

    Ok(Artifact {
        artifact_id: get("artifact_id")?.to_string(),
        feature_hash: get("feature_hash")?.to_string(),
        dataset_key: get("dataset_key")?.to_string(),
        dataset_sha256: get("dataset_sha256")?.to_string(),
        frozen: Frozen {
            fast: fast as usize,
            slow: slow as usize,
            eps,
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const SECRET: &[u8] = b"dev-fixture-secret";

    fn fixture() -> std::path::PathBuf {
        let mut p = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        p.push("tests/artifact.json");
        p
    }

    #[test]
    fn verifies_the_signed_fixture_and_exposes_frozen_params() {
        let a = verify(&fixture(), SECRET).expect("fixture must verify");
        assert_eq!(a.artifact_id, "sma-cross-hyperliquid_BTC-USD_1d-fix");
        assert_eq!(a.feature_hash, "5b808ce04af0b7e9e187ed31458aa4a4");
        assert_eq!(a.dataset_key, "hyperliquid/BTC-USD/1d");
        assert!(matches!(
            a.frozen,
            Frozen {
                fast: 3,
                slow: 40,
                eps
            } if eps == 0.0
        ));
    }

    #[test]
    fn wrong_secret_is_refused() {
        assert!(matches!(
            verify(&fixture(), b"other"),
            Err(VerifyError::SigMismatch)
        ));
    }

    #[test]
    fn tampered_evidence_is_refused() {
        let tmp = std::env::temp_dir().join("gfti_e2_tampered.json");
        let mut v: Value =
            serde_json::from_str(&std::fs::read_to_string(fixture()).unwrap()).unwrap();
        v["wf"]["oos_sharpe"] = serde_json::json!(0.999); // one value flip
        std::fs::write(&tmp, serde_json::to_string(&v).unwrap()).unwrap();
        assert!(matches!(
            verify(&tmp, SECRET),
            Err(VerifyError::SigMismatch)
        ));
        let _ = std::fs::remove_file(&tmp);
    }

    #[test]
    fn schema_errors_do_not_become_sig_errors() {
        let tmp = std::env::temp_dir().join("gfti_e2_broken.json");
        std::fs::write(&tmp, "{\"sig\":\"aa\"}").unwrap();
        assert!(matches!(verify(&tmp, SECRET), Err(VerifyError::Schema(_))));
        let _ = std::fs::remove_file(&tmp);
    }

    #[test]
    fn load_errors_are_their_own_variant() {
        let missing = std::env::temp_dir().join("gfti_e2_absent.json");
        let _ = std::fs::remove_file(&missing);
        assert!(matches!(
            verify(&missing, SECRET),
            Err(VerifyError::Load(_))
        ));
    }
}
