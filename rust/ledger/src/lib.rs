//! R-W0b — D4 append-only ledger + kill-cascade client (blueprint §2.2, §6).
//!
//! Contracts (all enforced, not conventional):
//! * **Append-only**: UPDATE/DELETE denied at the DATABASE level (triggers in
//!   compose/sql/001_d3_d4_init.sql). This crate simply never issues them.
//! * **Fail-closed kill**: readers of the kill state must treat *unreachable*
//!   as ENGAGED (weakest evidence wins); a broken DB must never look "open".
//! * **Signed rows**: every fill carries an HMAC-SHA256 over its canonical
//!   string under the role secret; verification is recomputation (§9 roles).

use hmac::{Hmac, Mac};
use hmac::digest::KeyInit;
use sha2::{Digest, Sha256};
type HmacSha256 = Hmac<Sha256>;

pub use postgres::Client;

/// Connect to the D3/D4 Postgres. `LEDGER_URL` env overrides; dev default
/// matches compose/.env.example placeholders (dev-local only).
pub fn connect() -> std::result::Result<Client, postgres::Error> {
    let url = std::env::var("LEDGER_URL").unwrap_or_else(|_| {
        "postgres://gfti_app:gfti_dev_pass@127.0.0.1:5432/gfti".to_string()
    });
    Client::connect(&url, postgres::NoTls)
}

/// Initialize schema from the canonical SQL file (single source of truth).
pub fn init(c: &mut Client, sql_path: &str) -> Result<(), String> {
    let sql = std::fs::read_to_string(sql_path).map_err(|e| e.to_string())?;
    c.batch_execute(&sql).map_err(|e| e.to_string())
}

// ---------------------------------------------------------------- signing --

/// Canonical fill string — what a fill signature covers (business fields only;
/// `ts_est` is DB-generated, keeping the canonical string reconstructable).
/// Changing this is a breaking audit change and must not be silent.
pub fn fill_canonical(source: &str, symbol: &str, side: &str, qty: f64, px: f64) -> String {
    format!("fill|{source}|{symbol}|{side}|{qty:.8}|{px:.8}")
}

/// Role secret lookup: env `LEDGER_SECRET_<ROLE>` (uppercase, dashes→underscores).
pub fn role_secret(role: &str) -> Option<String> {
    let key = format!(
        "LEDGER_SECRET_{}",
        role.to_uppercase().replace(['-', ' '], "_")
    );
    std::env::var(&key).ok().filter(|s| !s.is_empty())
}

/// HMAC-SHA256 hex under the role secret. Role secret never persisted.
pub fn sign(canonical: &str, role_secret: &str) -> String {
    let mut mac = HmacSha256::new_from_slice(role_secret.as_bytes())
        .expect("hmac accepts any key length");
    mac.update(canonical.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

/// Role registration helper (owner/bootstrap only). Secret never stored —
/// only its sha256 (order-audit: prove which secret made a row later).
pub fn ensure_role(c: &mut Client, role_name: &str, secret: &str) -> Result<(), String> {
    let mut h = Sha256::new();
    h.update(secret.as_bytes());
    let hash = hex::encode(h.finalize());
    c.execute(
        "INSERT INTO d3.roles (role_name, canon_claim, secret_hash)
         VALUES ($1, FALSE, $2)
         ON CONFLICT (role_name) DO UPDATE SET secret_hash = EXCLUDED.secret_hash",
        &[&role_name, &hash],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

// ------------------------------------------------------------------ fills --

/// Append one signed fill row. Returns (fill_id, signature). qty/px are bound
/// as TEXT and cast server-side (`$n::text::numeric`) — exact NUMERIC storage,
/// and the decimal text is exactly the canonical `.8` formatting the signature
/// covers, so verification and storage can never drift apart.
pub fn append_fill(
    c: &mut Client,
    source: &str,
    symbol: &str,
    side: &str,
    qty: f64,
    px: f64,
    role_name: &str,
) -> Result<(i64, String), String> {
    let secret = role_secret(role_name)
        .ok_or_else(|| format!("no secret for role {role_name} (env missing)"))?;
    if !matches!(side, "buy" | "sell") {
        return Err(format!("side must be buy|sell, got {side}"));
    }
    if !(qty > 0.0 && px > 0.0 && qty.is_finite() && px.is_finite()) {
        return Err("qty/px must be positive finite".to_string());
    }
    let sig = sign(&fill_canonical(source, symbol, side, qty, px), &secret);
    let qty_text = format!("{qty:.8}");
    let px_text = format!("{px:.8}");
    let row = c
        .query_one(
            "INSERT INTO d4.fills (source, symbol, side, qty, px, signer_role, sig)
             VALUES ($1, $2, $3, $4::text::numeric, $5::text::numeric, $6, $7)
             RETURNING fill_id",
            &[&source, &symbol, &side, &qty_text, &px_text, &role_name, &sig],
        )
        .map_err(|e| e.to_string())?;
    let id: i64 = row.get(0);
    Ok((id, sig))
}

/// Batched append: ONE transaction per call → ONE commit = one WAL flush for
/// the whole batch; one prepared statement reused for every row. Signing (and
/// validation) happens BEFORE the transaction opens, so DB time is pure insert.
/// Returns per-row signatures in input order. This is the production append
/// path; `append_fill` stays for single-row callers.
pub fn append_fills_batch(
    c: &mut Client,
    rows: &[(&str, &str, &str, f64, f64,)], // (source, symbol, side, qty, px) — one owner role
    role_name: &str,
) -> Result<Vec<String>, String> {
    let secret = role_secret(role_name)
        .ok_or_else(|| format!("no secret for role {role_name} (env missing)"))?;
    let mut sigs: Vec<String> = Vec::with_capacity(rows.len());
    for &(source, symbol, side, qty, px) in rows {
        if !matches!(side, "buy" | "sell") {
            return Err(format!("side must be buy|sell, got {side}"));
        }
        if !(qty > 0.0 && px > 0.0 && qty.is_finite() && px.is_finite()) {
            return Err("qty/px must be positive finite".to_string());
        }
        sigs.push(sign(&fill_canonical(source, symbol, side, qty, px), &secret));
    }
    let mut txn = c.transaction().map_err(errstr)?; // one commit = one WAL flush
    let stmt = txn
        .prepare(
            "INSERT INTO d4.fills (source, symbol, side, qty, px, signer_role, sig)
             VALUES ($1, $2, $3, $4::text::numeric, $5::text::numeric, $6, $7)
             RETURNING fill_id",
        )
        .map_err(errstr)?;
    for (idx, &(source, symbol, side, qty, px)) in rows.iter().enumerate() {
        let row = txn
            .query_one(
                &stmt,
                &[
                    &source,
                    &symbol,
                    &side,
                    &format!("{qty:.8}"),
                    &format!("{px:.8}"),
                    &role_name,
                    &sigs[idx],
                ],
            )
            .map_err(errstr)?;
        let _fill_id: i64 = row.get(0); // consume so pg doesn't drop the result
    }
    txn.commit().map_err(errstr)?;
    Ok(sigs)
}

/// Verify every fill's signature by recomputation. Returns (ok, n_fills, bad).
pub fn verify_fills(c: &mut Client) -> Result<(bool, i64, i64), String> {
    // one role at a time to keep memory flat; 5-year ledger scoping later
    let nrole: i64 = c
        .query_one("SELECT count(*) FROM d3.roles", &[])
        .map_err(errstr)?
        .get(0);
    let mut total: i64 = 0;
    let mut bad: i64 = 0;
    for ridx in 0..nrole {
        let rows = c
            .query(
                "SELECT role_name FROM d3.roles ORDER BY role_name OFFSET $1 LIMIT 1",
                &[&ridx],
            )
            .map_err(errstr)?;
        let role: String = rows[0].get(0);
        let Some(secret) = role_secret(&role) else { continue };
        let fr = c
            .query(
                "SELECT fill_id, source, symbol, side, qty::float8, px::float8, sig
                 FROM d4.fills WHERE signer_role = $1",
                &[&role],
            )
            .map_err(errstr)?;
        for r in fr {
            total += 1;
            let (_, src, sym, side, qty, px, sig) = (
                r.get::<_, i64>(0),
                r.get::<_, String>(1),
                r.get::<_, String>(2),
                r.get::<_, String>(3),
                r.get::<_, f64>(4),
                r.get::<_, f64>(5),
                r.get::<_, String>(6),
            );
            if sign(&fill_canonical(&src, &sym, &side, qty, px), &secret) != sig {
                bad += 1;
            }
        }
    }
    Ok((bad == 0, total, bad))
}

/// Simple per-symbol gross P&L from signed fills (POSITIVE=credit signed side).
/// gross = Σ(part sign)·qty·px across all rows of that symbol.
pub fn gross_pnl(c: &mut Client) -> Result<Vec<(String, i64, f64)>, String> {
    let rows = c
        .query(
            "SELECT symbol, count(*), sum(CASE WHEN side='sell' THEN qty*px ELSE -qty*px END)::float8
             FROM d4.fills GROUP BY symbol ORDER BY symbol",
            &[],
        )
        .map_err(errstr)?;
    Ok(rows
        .iter()
        .map(|r| (r.get(0), r.get(1), r.get(2)))
        .collect())
}

/// Land a recon verdict row (D4 pnl_recon) — recon is itself append-only.
pub fn record_recon(
    c: &mut Client,
    verdict: &str,
    n_fills: i64,
    gross: f64,
    payload: &str,
) -> Result<(), String> {
    if !matches!(verdict, "match" | "mismatch") {
        return Err("verdict must be match|mismatch".to_string());
    }
    c.execute(
        "INSERT INTO d4.pnl_recon (window_from, window_to, gross_pnl, n_fills, recon_verdict, payload)
         VALUES (now() - interval '1 hour', now(), $1::text::numeric, $2, $3, $4)",
        &[&format!("{gross:.8}"), &n_fills, &verdict, &payload],
    )
    .map_err(errstr)?;
    Ok(())
}

// ------------------------------------------------------------------- kill --

#[derive(Debug, Clone, PartialEq)]
pub enum Kill {
    /// No explicit state yet → engines treat as ENGAGED (fail-closed default).
    FailsafeEngaged,
    Engaged { level: String, reason: String, source: String },
    Open,
}

impl Kill {
    pub fn risk_allowed(&self) -> bool {
        matches!(self, Kill::Open)
    }
}

/// Read the latest kill event. Any DB error collapses to FailsafeEngaged —
/// the only way this function returns Open is a row explicitly saying so.
pub fn kill_state(c: &mut Client) -> Kill {
    let rows = c
        .query(
            "SELECT engaged, level, reason, source FROM d4.kill_events
             ORDER BY engaged_at DESC, evt_id DESC LIMIT 1",
            &[],
        )
        .unwrap_or_default();
    match rows.first() {
        None => Kill::FailsafeEngaged,
        Some(r) => match r.get::<_, bool>(0) {
            true => Kill::Engaged {
                level: r.get(1),
                reason: r.get(2),
                source: r.get(3),
            },
            false => Kill::Open,
        },
    }
}

/// Append a kill event (append-only; the flip is a row, not an UPDATE).
pub fn kill_flip(
    c: &mut Client,
    engaged: bool,
    level: &str,
    source: &str,
    reason: &str,
) -> Result<(), String> {
    if !matches!(level, "L0" | "L1" | "L2" | "L3" | "L4") {
        return Err(format!("bad level {level}"));
    }
    c.execute(
        "INSERT INTO d4.kill_events (level, engaged, source, reason) VALUES ($1, $2, $3, $4)",
        &[&level, &engaged, &source, &reason],
    )
    .map_err(errstr)?;
    Ok(())
}

fn errstr(e: postgres::Error) -> String {
    e.to_string()
}
