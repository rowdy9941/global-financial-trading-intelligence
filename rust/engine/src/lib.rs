//! E-cell skeleton library (blueprint §3 cells table, §11 memory).
//!
//! Skeleton scope = the DETERMINISTIC decision spine with real gates:
//! * E1 featurize — in-process pre-warmed state (ring-buffer SMAs), µs-class.
//! * E2 evaluate — frozen-artifact skeleton: one compiled rule
//!   (FAST_LEN/SLOW_LEN/EPS constants). Full D5-signed compilation arrives
//!   in W1; the interface stays.
//! * E5 govern — deterministic ladder: kill-state first (fail-closed, cached
//!   w/ background refresh), then size cap.
//! * E6 route — publish `order.spot.<SYM>` intent (parsed by E7).
//! * E7 ledger — separate process (order.* → signed D4 fills).

// E1 landed in its own crate: runtime window featurizer driven by the
// D5-signed constant set (see featurize + evaluate). The engine re-exports
// the SAME implementation with the skeleton defaults, so every cell, gate,
// and test keeps referring to a single E1.
pub use featurize::{Features, Featurizer, Frozen};

/// E2 skeleton constants (the dev "frozen artifact" — replaced by
/// evaluate::verify + Frozen at boot once the hot path awaits).
pub const FAST_LEN: usize = 5;
pub const SLOW_LEN: usize = 20;
pub const EPS: f64 = 1e-4;

pub use govern::{Action, Verdict, govern};

/// E2 — evaluate (skeleton rule over the shared Action vocabulary).
pub fn evaluate(f: &Features) -> Action {
    if f.sma_fast > f.sma_slow * (1.0 + EPS) {
        Action::Buy
    } else if f.sma_fast < f.sma_slow * (1.0 - EPS) {
        Action::Sell
    } else {
        Action::None
    }
}

/// E6 order intent payload (line `"<side> <qty:.8> <px:.8> <decision_id>"` —
/// lowercase side; symbol rides the `order.spot.<SYM>` subject).
pub fn intent_payload(side: &Action, qty: f64, px: f64, decision_id: u64) -> String {
    let side = match side {
        Action::Buy => "buy",
        Action::Sell => "sell",
        Action::None => "hold",
    };
    format!("{side} {qty:.8} {px:.8} {decision_id}")
}
