//! E6 router — paper/shadow intent routing with IDEMPOTENT client order IDs
//! (A0 baseline §3; live mode is deliberately absent from this enum until W3's
//! owner gates land — the type system refuses what the owner has not approved).
//!
//! Contracts this crate owns:
//! * the E6 intent-payload wire format — the single `"<side> <qty:.8>
//!   <px:.8> <decision_id>"` line E7 consumes (engine re-exports it);
//! * idempotent identity: (seat, discriminator, decision_id) maps to exactly
//!   one client order ID for the router's lifetime — a duplicate route of the
//!   same decision_id is refused, never re-sent, so the venue can never see
//!   one logical intent twice;
//! * shadow has zero order authority BY CONSTRUCTION — it never returns a
//!   publishable payload, it only records the identity.
//!
//! Transport stays with the cell binary via rail (W0b/W1 shape); this crate
//! is the pure decision layer. The wire-stable ledger-grade ID lands with the
//! W2 adapter spike (std-hasher ID is build-stable, noted as an open item,
//! not an undocumented approximation).

use std::collections::HashSet;
use std::hash::{Hash, Hasher};

use govern::{Action, Verdict};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Publishes to the bus; fills land in the D4 ledger like live, but the
    /// venue is simulated (W0b E7 consumer) — real ledger semantics, no cash.
    Paper,
    /// Records the decision WITHOUT publishing — zero order authority.
    Shadow,
}

#[derive(Debug, Clone, PartialEq)]
pub enum RouteRefusal {
    /// Same client order ID routed again — idempotent-retry boundary.
    Duplicate { client_order_id: u64 },
    /// The in-flight cap is a deterministic guard (config sanity), not a
    /// rate limiter.
    InFlightCap { cap: usize },
    /// Shadow never publishes. A governed refusal for an already-seen
    /// identity is still a Duplicate — check order matters.
    GovernedRefusal { client_order_id: u64 },
}

#[derive(Debug, Clone, PartialEq)]
pub struct Intent {
    pub client_order_id: u64,
    pub side: Action,
    pub qty: f64,
    pub px: f64,
    /// THE wire line (E6 format contract); E7 parses this from `order.*`.
    pub payload: String,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Routed {
    /// Publish `payload` on `order.<venue>.<SYM>` (cell does the transport).
    Publish(Intent),
    /// Record only — no publishable payload exists in shadow mode.
    Record { client_order_id: u64 },
}

/// Client order ID = one deterministic hash of (seat, discriminator,
/// decision_id) — the single logical identity of the intent.
pub fn client_order_id(seat: &str, discriminator: &str, decision_id: u64) -> u64 {
    let mut h = std::collections::hash_map::DefaultHasher::new();
    seat.hash(&mut h);
    discriminator.hash(&mut h);
    h.write_u64(decision_id);
    h.finish()
}

/// E6 intent payload — THE format owner (engine re-exports as
/// `engine::intent_payload`; cell + E7 keep their exact wire behavior).
pub fn intent_payload(side: &Action, qty: f64, px: f64, decision_id: u64) -> String {
    let side = match side {
        Action::Buy => "buy",
        Action::Sell => "sell",
        Action::None => "hold",
    };
    format!("{side} {qty:.8} {px:.8} {decision_id}")
}

/// Static routing config; `State::new` validates it fail-closed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Router {
    mode: Mode,
    seat: String,
    discriminator: String,
    max_in_flight: usize,
}

impl Router {
    pub fn paper(seat: &str, discriminator: &str, max_in_flight: usize) -> Self {
        Self {
            mode: Mode::Paper,
            seat: seat.into(),
            discriminator: discriminator.into(),
            max_in_flight,
        }
    }

    pub fn shadow(seat: &str, discriminator: &str, max_in_flight: usize) -> Self {
        Self {
            mode: Mode::Shadow,
            seat: seat.into(),
            discriminator: discriminator.into(),
            max_in_flight,
        }
    }

    pub fn validate(&self) -> Result<(), &'static str> {
        if self.seat.is_empty() || self.discriminator.is_empty() {
            return Err("empty seat or discriminator");
        }
        if self.max_in_flight == 0 {
            return Err("in-flight cap must be positive");
        }
        if self.max_in_flight > 1_000_000 {
            return Err("in-flight cap absurdly large");
        }
        Ok(())
    }
}

/// Mutable routing state: idempotency set + in-flight counter.
pub struct State {
    router: Router,
    seen: HashSet<u64>,
    in_flight: usize,
}

impl State {
    pub fn new(router: Router) -> Result<Self, &'static str> {
        router.validate()?;
        Ok(Self {
            router,
            seen: HashSet::new(),
            in_flight: 0,
        })
    }

    pub fn mode(&self) -> Mode {
        self.router.mode
    }

    pub fn in_flight(&self) -> usize {
        self.in_flight
    }

    /// Route one governed verdict at the observed price. Idempotent: the same
    /// `decision_id` refused forever after the first route of that identity.
    /// A governed refusal (kill/size/no-signal) also consumes the identity —
    /// a retried refusal is a duplicate, never a second decision.
    pub fn route(
        &mut self,
        verdict: &Verdict,
        px: f64,
        decision_id: u64,
    ) -> Result<Routed, RouteRefusal> {
        let id = client_order_id(&self.router.seat, &self.router.discriminator, decision_id);
        if self.seen.contains(&id) {
            return Err(RouteRefusal::Duplicate {
                client_order_id: id,
            });
        }
        self.seen.insert(id);
        let (side, qty) = match verdict {
            Verdict::Allow { side, qty } => (side.clone(), *qty),
            Verdict::RefuseKill | Verdict::RefuseSize { .. } | Verdict::NoSignal => {
                return Err(RouteRefusal::GovernedRefusal {
                    client_order_id: id,
                });
            }
        };
        if self.in_flight >= self.router.max_in_flight {
            return Err(RouteRefusal::InFlightCap {
                cap: self.router.max_in_flight,
            });
        }
        self.in_flight += 1;
        match self.router.mode {
            Mode::Shadow => Ok(Routed::Record {
                client_order_id: id,
            }),
            Mode::Paper => {
                let payload = intent_payload(&side, qty, px, decision_id);
                Ok(Routed::Publish(Intent {
                    client_order_id: id,
                    side,
                    qty,
                    px,
                    payload,
                }))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn payload_format_is_the_e6_wire_contract() {
        assert_eq!(
            intent_payload(&Action::Buy, 1.0, 1.5, 7),
            "buy 1.00000000 1.50000000 7"
        );
        assert_eq!(
            intent_payload(&Action::Sell, 0.25, 67944.0, 42),
            "sell 0.25000000 67944.00000000 42"
        );
    }

    #[test]
    fn id_is_deterministic_per_identity() {
        let a = client_order_id("seat-x", "sma-cross", 42);
        assert_eq!(a, client_order_id("seat-x", "sma-cross", 42));
        assert_ne!(a, client_order_id("seat-y", "sma-cross", 42));
        assert_ne!(a, client_order_id("seat-x", "sma-cross", 43));
        assert_ne!(a, client_order_id("seat-x", "other-disc", 42));
    }

    #[test]
    fn paper_allow_publishes_canonical_payload_with_the_observed_px() {
        let mut st = State::new(Router::paper("s1", "sma-cross", 10)).unwrap();
        let v = Verdict::Allow {
            side: Action::Buy,
            qty: 1.0,
        };
        let out = st.route(&v, 1.5, 7).expect("paper allow must publish");
        let Routed::Publish(intent) = out else {
            panic!("paper must publish")
        };
        assert_eq!(
            intent.client_order_id,
            client_order_id("s1", "sma-cross", 7)
        );
        assert_eq!(intent.px, 1.5);
        assert_eq!(intent.payload, "buy 1.00000000 1.50000000 7");
        assert_eq!(intent.payload, intent_payload(&Action::Buy, 1.0, 1.5, 7));
        assert_eq!(st.in_flight(), 1);
    }

    #[test]
    fn duplicate_route_of_same_decision_refused() {
        let mut st = State::new(Router::paper("s", "d", 10)).unwrap();
        let v = Verdict::Allow {
            side: Action::Buy,
            qty: 1.0,
        };
        let want = client_order_id("s", "d", 1);
        assert!(st.route(&v, 1.0, 1).is_ok());
        assert_eq!(
            st.route(&v, 1.0, 1).unwrap_err(),
            RouteRefusal::Duplicate {
                client_order_id: want
            }
        );
    }

    #[test]
    fn shadow_never_publishes_but_still_records_identity() {
        let mut st = State::new(Router::shadow("s", "d", 10)).unwrap();
        let v = Verdict::Allow {
            side: Action::Sell,
            qty: 1.0,
        };
        let out = st.route(&v, 100.0, 3).unwrap();
        assert!(matches!(out, Routed::Record { .. }));
        assert!(matches!(
            st.route(&v, 100.0, 3),
            Err(RouteRefusal::Duplicate { .. })
        ));
    }

    #[test]
    fn governed_refusals_consume_the_identity_not_flight() {
        let mut st = State::new(Router::paper("s", "d", 10)).unwrap();
        assert!(matches!(
            st.route(&Verdict::RefuseKill, 1.0, 11),
            Err(RouteRefusal::GovernedRefusal { .. })
        ));
        assert!(matches!(
            st.route(&Verdict::RefuseSize { max: 1.0 }, 1.0, 12),
            Err(RouteRefusal::GovernedRefusal { .. })
        ));
        assert!(matches!(
            st.route(&Verdict::NoSignal, 1.0, 13),
            Err(RouteRefusal::GovernedRefusal { .. })
        ));
        assert_eq!(st.in_flight(), 0, "refusals are never orders");
        assert!(matches!(
            st.route(&Verdict::RefuseKill, 1.0, 11),
            Err(RouteRefusal::Duplicate { .. })
        ));
    }

    #[test]
    fn in_flight_cap_is_deterministic_and_not_decremented_by_refusals() {
        let mut st = State::new(Router::paper("s", "d", 1)).unwrap();
        let v = Verdict::Allow {
            side: Action::Buy,
            qty: 1.0,
        };
        assert!(st.route(&v, 1.0, 1).is_ok());
        assert!(matches!(
            st.route(&v, 1.0, 2),
            Err(RouteRefusal::InFlightCap { cap: 1 })
        ));
    }

    #[test]
    fn router_config_refusals_fail_closed() {
        assert_eq!(
            Router::paper("", "d", 5).validate(),
            Err("empty seat or discriminator")
        );
        assert_eq!(
            Router::shadow("s", "", 5).validate(),
            Err("empty seat or discriminator")
        );
        assert_eq!(
            Router::paper("s", "d", 0).validate(),
            Err("in-flight cap must be positive")
        );
        assert_eq!(
            Router::paper("s", "d", 2_000_000).validate(),
            Err("in-flight cap absurdly large")
        );
        assert_eq!(Router::shadow("s", "d", 5).validate(), Ok(()));
    }

    #[test]
    fn state_construction_refuses_invalid_router() {
        assert!(State::new(Router::paper("s", "d", 0)).is_err());
        assert!(State::new(Router::paper("", "d", 5)).is_err());
    }
}
