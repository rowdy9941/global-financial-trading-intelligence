//! R1 rail — ingest contract core (W0a skeleton).
//!
//! Validate → as-of stamp → rights-tag. This is the *only* sanctioned entry
//! point of market data into the system (FULL-SYSTEM-BLUEPRINT §3.1: D1 has ONE
//! writer, and it is this).
//!
//! std-only at this stage. No `unwrap`/`panic` in library paths.

pub mod ilp;
pub mod wire;

use std::time::{SystemTime, UNIX_EPOCH};

/// Rights classes (D3 `source_registry.rights_class`). Unknown class ⇒ reject.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RightsClass {
    /// Full internal use incl. Mode-T cells.
    Public,
    /// Internal research only — never touches the hot path.
    PublicResearchOnly,
    /// Reader-ever Volatile — eval, display-only; agents must not trade on it.
    Walled,
}

impl RightsClass {
    pub fn parse(s: &str) -> Option<RightsClass> {
        match s {
            "public" => Some(RightsClass::Public),
            "public_research_only" => Some(RightsClass::PublicResearchOnly),
            "walled" => Some(RightsClass::Walled),
            _ => None,
        }
    }
}

/// One row handed to the rail by a collector (B1) or a venue feed.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RawRow<'a> {
    /// Venue/souce symbol key, ASCII, non-empty. (Per-source schema mapping is
    /// the collector's job; the rail only enforces the generic contract.)
    pub symbol: &'a str,
    /// Event time claimed by the source, in nanoseconds since the epoch.
    /// MUST be monotonically sane, not in the future, not absurdly old.
    pub ts_nanos: u64,
    pub price: f64,
    pub rights: &'a str,
}

/// A validated, stamped row ready for the bus (D2) and stores (D1/D0).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StampedRow<'a> {
    pub symbol: &'a str,
    /// The as-of stamp the rail assigns: the source event time, confirmed.
    pub ts_nanos: u64,
    /// Rail arrival time — audit/provenance (R5), never used for decisions.
    pub arrival_nanos: u64,
    pub price: f64,
    pub rights: RightsClass,
}

pub const MAX_FUTURE_SKEW_NANOS: u64 = 5_000_000_000; // 5 s
/// ~2014-05 (1.4e18 ns) — 6–10 y backfill must clear it; older = malformed feed.
pub const MIN_TS_NANOS: u64 = 1_400_000_000_000_000_000;

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0)
}

/// Max absolute price sanity. |price| ≤ 10^9 covers NSE/CDM/global venues.
pub const MAX_ABS_PRICE: f64 = 1_000_000_000.0;

/// Validate one raw row against the ingest contract; return the stamped row.
pub fn stamp_row<'a>(raw: &RawRow<'a>, arrival: u64) -> Result<StampedRow<'a>, Reject> {
    if raw.symbol.is_empty() || raw.symbol.len() > 32 {
        return Err(Reject::Symbol);
    }
    if !raw
        .symbol
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'_')
    {
        return Err(Reject::Symbol);
    }
    if arrival < MIN_TS_NANOS || arrival > now_nanos() + MAX_FUTURE_SKEW_NANOS {
        return Err(Reject::Clock);
    }
    if raw.ts_nanos > arrival + MAX_FUTURE_SKEW_NANOS {
        return Err(Reject::FutureAsOf);
    }
    if raw.ts_nanos < MIN_TS_NANOS {
        return Err(Reject::AncientAsOf);
    }
    if !raw.price.is_finite() || raw.price.abs() > MAX_ABS_PRICE || raw.price <= 0.0 {
        return Err(Reject::Price);
    }
    let rights = RightsClass::parse(raw.rights).ok_or(Reject::Rights)?;
    Ok(StampedRow {
        symbol: raw.symbol,
        ts_nanos: raw.ts_nanos,
        arrival_nanos: arrival,
        price: raw.price,
        rights,
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reject {
    /// Missing, oversized, or non-ASCII symbol key.
    Symbol,
    /// rails arrival clock is impossible.
    Clock,
    /// Source as-of is in the future beyond allowed skew.
    FutureAsOf,
    /// Source as-of is too old to be believable (malformed, not history).
    AncientAsOf,
    /// Price non-finite / non-positive / out of sanity bounds.
    Price,
    /// Rights class not registered in D3.
    Rights,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row<'a>(price: f64, ts: u64, rights: &'a str) -> RawRow<'a> {
        RawRow { symbol: "NIFTY", ts_nanos: ts, price, rights }
    }
    const T0: u64 = 1_772_000_000_000_000_000; // ≈ 2026-02-20

    #[test]
    fn accepts_healthy_row_and_stamps() {
        let r = stamp_row(&row(25_310.5, T0, "public"), T0).expect("healthy row");
        assert_eq!(r.symbol, "NIFTY");
        assert_eq!(r.ts_nanos, T0);
        assert_eq!(r.arrival_nanos, T0);
        assert_eq!(r.rights, RightsClass::Public);
    }

    #[test]
    fn rejects_future_as_of_beyond_skew() {
        let r = row(25_310.5, T0 + MAX_FUTURE_SKEW_NANOS + 1, "public");
        assert_eq!(stamp_row(&r, T0), Err(Reject::FutureAsOf));
    }

    #[test]
    fn rejects_nonfinite_and_nonpositive_price() {
        for p in [f64::NAN, f64::INFINITY, 0.0, -1.0, MAX_ABS_PRICE + 1.0] {
            let r = row(p, T0, "public");
            assert_eq!(stamp_row(&r, T0), Err(Reject::Price), "price {p}");
        }
    }

    #[test]
    fn rejects_unknown_rights_class() {
        let r = row(25_310.5, T0, "public_walled_future_class");
        assert_eq!(stamp_row(&r, T0), Err(Reject::Rights));
    }

    #[test]
    fn walled_class_is_parsed_and_tagged() {
        let r = stamp_row(&row(25_310.5, T0, "walled"), T0).expect("walled");
        assert_eq!(r.rights, RightsClass::Walled);
    }

    #[test]
    fn symbol_contract_enforced() {
        let bad = RawRow { symbol: "NIFTY;", ts_nanos: T0, price: 25_310.5, rights: "public" };
        assert_eq!(stamp_row(&bad, T0), Err(Reject::Symbol));
        let empty = RawRow { symbol: "", ts_nanos: T0, price: 25_310.5, rights: "public" };
        assert_eq!(stamp_row(&empty, T0), Err(Reject::Symbol));
    }
}
