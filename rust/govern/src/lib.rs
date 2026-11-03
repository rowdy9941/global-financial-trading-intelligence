//! E5 governor — deterministic pre-trade ladder (A0 baseline §3; engine §lib
//! semantics preserved verbatim so the cell binary stays behavior-identical).
//!
//! Ladder order is itself the contract (R14 fail-closed ordering):
//!   1. kill state   — first, always; `risk_allowed=false` refuses even if
//!      the signal is None.
//!   2. signal check — NoSignal for Action::None.
//!   3. size cap     — qty must be finite and within the cap.
//!
//! The sizing ladder (real-world sizing) lands in W3; flat 1.0 stays until
//! then (explicitly an estimate, never silently promoted to "real").

/// E2-driven action (re-exported into the engine crate era interface).
#[derive(Debug, Clone, PartialEq)]
pub enum Action {
    Buy,
    Sell,
    None,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Verdict {
    Allow { side: Action, qty: f64 },
    RefuseKill,
    RefuseSize { max: f64 },
    NoSignal,
}

/// E5 — govern: kill first (fail-closed), then deterministic size cap.
/// Qty is flat 1.0 (sizing skeleton); W3 replaces it with the real ladder.
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
    Verdict::Allow {
        side: a.clone(),
        qty,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kill_ladder_is_first_and_fail_closed() {
        assert_eq!(
            govern(&Action::Buy, false, 10.0),
            Verdict::RefuseKill,
            "engine kill state must refuse even a valid signal"
        );
        assert_eq!(
            govern(&Action::None, false, 10.0),
            Verdict::RefuseKill,
            "fail-closed ordering: kill refusal outranks NoSignal"
        );
    }

    #[test]
    fn no_signal_is_neither_allow_nor_refuse() {
        assert_eq!(govern(&Action::None, true, 10.0), Verdict::NoSignal);
    }

    #[test]
    fn size_cap_refuses_and_reports_the_cap() {
        assert_eq!(
            govern(&Action::Sell, true, 0.5),
            Verdict::RefuseSize { max: 0.5 }
        );
    }

    #[test]
    fn non_finite_cap_never_allows() {
        // NaN != NaN, so assert with a pattern, not PartialEq
        assert!(
            matches!(
                govern(&Action::Buy, true, f64::NAN),
                Verdict::RefuseSize { max } if max.is_nan()
            ),
            "a non-finite cap must refuse, never degenerate into allow"
        );
    }

    #[test]
    fn open_and_within_cap_allows() {
        assert_eq!(
            govern(&Action::Buy, true, 10.0),
            Verdict::Allow {
                side: Action::Buy,
                qty: 1.0
            }
        );
        assert_eq!(
            govern(&Action::Sell, true, 1.0),
            Verdict::Allow {
                side: Action::Sell,
                qty: 1.0
            }
        );
    }
}
