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

use std::collections::VecDeque;

/// E2 constants — skeleton "frozen artifact".
pub const FAST_LEN: usize = 5;
pub const SLOW_LEN: usize = 20;
pub const EPS: f64 = 1e-4;

/// E1 — per-symbol featurizer: two ring SMAs (fast/slow) + last price.
#[derive(Debug)]
pub struct Featurizer {
    fast: VecDeque<f64>,
    slow: VecDeque<f64>,
    last: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Features {
    pub sma_fast: f64,
    pub sma_slow: f64,
    pub last: f64,
}

impl Default for Featurizer {
    fn default() -> Self {
        Self::new()
    }
}

impl Featurizer {
    pub fn new() -> Self {
        Self {
            fast: VecDeque::with_capacity(FAST_LEN),
            slow: VecDeque::with_capacity(SLOW_LEN),
            last: 0.0,
        }
    }

    /// E1 update: O(1) amortized. Returns Some(features) once warm.
    pub fn on_tick(&mut self, px: f64) -> Option<Features> {
        self.last = px;
        self.fast.push_back(px);
        if self.fast.len() > FAST_LEN {
            self.fast.pop_front();
        }
        self.slow.push_back(px);
        if self.slow.len() > SLOW_LEN {
            self.slow.pop_front();
        }
        if self.fast.len() < FAST_LEN || self.slow.len() < SLOW_LEN {
            return None; // pre-warming; no decisions until warm
        }
        Some(Features {
            sma_fast: self.fast.iter().sum::<f64>() / FAST_LEN as f64,
            sma_slow: self.slow.iter().sum::<f64>() / SLOW_LEN as f64,
            last: px,
        })
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Action {
    Buy,
    Sell,
    None,
}

/// E2 — evaluate (skeleton rule).
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

#[derive(Debug, Clone, PartialEq)]
pub enum Verdict {
    Allow { side: Action, qty: f64 },
    RefuseKill,
    RefuseSize { max: f64 },
    NoSignal,
}

/// E5 — govern: kill first (fail-closed), then deterministic size cap.
/// Sizing skeleton is flat 1.0; the real sizing ladder lands in W3.
pub fn govern(action: &Action, risk_allowed: bool, max_qty: f64) -> Verdict {
    if !risk_allowed {
        return Verdict::RefuseKill;
    }
    let a = match action {
        Action::None => return Verdict::NoSignal,
        a => a,
    };
    let qty = 1.0;
    if !(qty > 0.0 && qty <= max_qty && qty.is_finite()) {
        return Verdict::RefuseSize { max: max_qty };
    }
    Verdict::Allow { side: a.clone(), qty }
}
