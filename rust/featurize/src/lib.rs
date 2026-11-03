//! E1 featurizer — pre-warmed ring windows for two MAs (fast/slow), runtime
//! lengths driven by a D5-signed `Frozen` constant set. The DEFAULT skeleton
//! lengths (5/20) match the W0b/W1-proven cell exactly; the signed artifact
//! path selects e.g. 3/40 at boot.
//!
//! Parity rule (bit-exact): sums re-iterate the window in insertion order —
//! the same op sequence as the engine skeleton — instead of an incremental
//! running sum (float-associativity drift would silently change boundaries).
//! O(window) per tick is fine at 1 µs-class scale; the W1-GATE-REPLAY p99 was
//! 724 ns with the skeleton, and O(40) + a few adds stays in budget.

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Frozen {
    pub fast: usize,
    pub slow: usize,
    pub eps: f64,
}

impl Frozen {
    /// Skeleton defaults — the W0b/W1 dev path (engine::FAST_LEN/SLOW_LEN/EPS).
    pub const SKELETON: Self = Self {
        fast: 5,
        slow: 20,
        eps: 1e-4,
    };

    /// Fail-closed constructor: non-sane lengths or eps are refuses, not
    /// fall-backs (a signed artifact must never silently degrade here).
    pub fn validate(self) -> Result<(), &'static str> {
        if self.fast == 0 || self.slow == 0 {
            return Err("zero-length MA window");
        }
        if self.fast > self.slow {
            return Err("fast window exceeds slow window");
        }
        if self.slow > 10_000 {
            return Err("slow window absurdly large (refuse > 10k ticks)");
        }
        if !(self.eps.is_finite() && self.eps >= 0.0) {
            return Err("eps must be finite and non-negative");
        }
        Ok(())
    }
}

impl Default for Frozen {
    fn default() -> Self {
        Self::SKELETON
    }
}

/// E1 per-symbol featurizer: two O(window) ring SMAs + last price.
#[derive(Debug)]
pub struct Featurizer {
    frozen: Frozen,
    fast: std::collections::VecDeque<f64>,
    slow: std::collections::VecDeque<f64>,
    last: f64,
    n: usize,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Features {
    pub sma_fast: f64,
    pub sma_slow: f64,
    pub last: f64,
}

impl Featurizer {
    /// Validated construction; a non-sane `Frozen` refuses (Result, not panic).
    pub fn new(frozen: Frozen) -> Result<Self, &'static str> {
        frozen.validate()?;
        Ok(Self {
            fast: std::collections::VecDeque::with_capacity(frozen.fast),
            slow: std::collections::VecDeque::with_capacity(frozen.slow),
            last: 0.0,
            frozen,
            n: 0,
        })
    }

    /// Skeleton default (par with the engine cell's proven constants).
    pub fn skeleton() -> Self {
        Self::new(Frozen::SKELETON).expect("SKELETON constants are valid by construction")
    }

    /// E1 update: returns Some(features) once BOTH windows are warm.
    pub fn push(&mut self, px: f64) -> Option<Features> {
        self.last = px;
        self.n += 1;
        self.fast.push_back(px);
        if self.fast.len() > self.frozen.fast {
            self.fast.pop_front();
        }
        self.slow.push_back(px);
        if self.slow.len() > self.frozen.slow {
            self.slow.pop_front();
        }
        if self.fast.len() < self.frozen.fast || self.slow.len() < self.frozen.slow {
            return None; // pre-warm; no decisions until warm
        }
        Some(Features {
            sma_fast: self.fast.iter().sum::<f64>() / self.frozen.fast as f64,
            sma_slow: self.slow.iter().sum::<f64>() / self.frozen.slow as f64,
            last: px,
        })
    }

    /// Ticks remaining before BOTH windows are warm (0 once warm).
    pub fn ticks_to_warm(&self) -> usize {
        self.frozen.slow.saturating_sub(self.n)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_refuses_non_sane_constants() {
        assert_eq!(
            "zero-length MA window",
            Frozen {
                fast: 0,
                slow: 20,
                eps: 0.0
            }
            .validate()
            .unwrap_err()
        );
        assert_eq!(
            "fast window exceeds slow window",
            Frozen {
                fast: 40,
                slow: 3,
                eps: 0.0
            }
            .validate()
            .unwrap_err()
        );
        assert!(matches!(
            Frozen {
                fast: 3,
                slow: 40,
                eps: f64::NAN
            }
            .validate(),
            Err("eps must be finite and non-negative")
        ));
        assert_eq!(
            (),
            Frozen {
                fast: 3,
                slow: 40,
                eps: 0.0
            }
            .validate()
            .unwrap()
        );
    }

    #[test]
    fn pre_warm_then_emit_at_slow_len() {
        let slow = 4usize;
        let mut f = Featurizer::new(Frozen {
            fast: 2,
            slow,
            eps: 0.0,
        })
        .unwrap();
        for c in 1..=slow - 1 {
            assert!(f.push(c as f64).is_none(), "push {c}: not warm yet");
        }
        let fx = f.push(10.0).expect("warm on slow-th tick");
        assert_eq!(fx.last, 10.0);
        assert_eq!(fx.sma_slow, (1.0 + 2.0 + 3.0 + 10.0) / 4.0);
        assert_eq!(fx.sma_fast, (3.0 + 10.0) / 2.0);
    }

    #[test]
    fn skeleton_matches_engine_constants() {
        assert_eq!(
            Frozen::SKELETON,
            Frozen {
                fast: 5,
                slow: 20,
                eps: 1e-4
            }
        );
    }

    #[test]
    fn steady_state_sums_match_window_compute_in_exact_op_order() {
        let mut f = Featurizer::skeleton();
        // warm at the slow length (push #20 makes slow hold 1..=20)
        for c in 1..=19 {
            f.push(c as f64);
        }
        let fx = f.push(20.0).expect("warm exactly at the slow length");
        // slow window 1..=20 (exact op order): (1+…+20)/20 = 10.5
        assert_eq!(fx.sma_slow, 210.0 / 20.0);
        assert_eq!(fx.sma_fast, (16.0 + 17.0 + 18.0 + 19.0 + 20.0) / 5.0);

        let fx = f.push(21.0).expect("steady-state push");
        // slow window slid to 2..=21: (2+…+21)/20 = 11.5
        assert_eq!(fx.sma_slow, 230.0 / 20.0);
        assert_eq!(fx.sma_fast, (17.0 + 18.0 + 19.0 + 20.0 + 21.0) / 5.0);
    }

    #[test]
    fn ticks_to_warm_counts_down_once() {
        let f = Featurizer::skeleton();
        assert_eq!(f.ticks_to_warm(), 20);
        let mut f = Featurizer::skeleton();
        for c in 0..7 {
            f.push(c as f64);
        }
        assert_eq!(f.ticks_to_warm(), 13);
    }
}
