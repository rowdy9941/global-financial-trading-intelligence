//! Versioned payload envelopes (invariant R6).
//!
//! Every message on the bus carries `"v":<n>` and a `"kind"`, and a reader that
//! does not know the version — or the kind, or a field — must **refuse** it rather
//! than guess at its shape. That rule is only worth anything if it lives in one
//! place, so this module is the sole encoder/decoder for `market.*`, `signal.*`,
//! `order.*`, `risk.*`, `ledger.*` and `seat.*` payloads: the rail and the cells
//! write them, the cells and E7 read them.
//!
//! Why hand-rolled and not serde: the hot-path crates keep their dependency surface
//! at zero until a gate forces a crate (`rail/Cargo.toml` policy note). The shapes
//! are flat, fixed and mostly numeric, so a strict parser is smaller than a
//! dependency and — importantly — can *fail* on an unknown version, which is the
//! requirement. A permissive reader would accept a v2 payload and mis-money it.
//!
//! Deliberately strict: no duplicate keys, no unknown keys, no unknown kind, no
//! whitespace tolerance beyond JSON's own. "Best effort" parsing is how a schema
//! break becomes a silent wrong number.

use std::collections::BTreeMap;

/// The version this build speaks. Bump only with a matching set of arms.
pub const SCHEMA_VERSION: u32 = 1;

/// Why a payload was refused. The variant is part of the evidence: a gate can tell
/// "unknown version" (a schema break) from "malformed" (a corrupt message).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PayloadError {
    UnknownVersion(u32),
    MissingVersion,
    Malformed(&'static str),
}

impl std::fmt::Display for PayloadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PayloadError::UnknownVersion(v) => write!(f, "unknown-version({v})"),
            PayloadError::MissingVersion => write!(f, "missing-version"),
            PayloadError::Malformed(what) => write!(f, "malformed({what})"),
        }
    }
}

// ---------------------------------------------------------------- frames

/// A decoded v1 `market.*` tick.
#[derive(Debug, Clone, PartialEq)]
pub struct Tick {
    pub symbol: String,
    pub ts_nanos: u64,
    pub arrival_nanos: u64,
    pub price: f64,
}

/// A decoded v1 `order.*` intent (E6 → E7). `side` is one of buy/sell/hold.
#[derive(Debug, Clone, PartialEq)]
pub struct Intent {
    pub side: String,
    pub qty: f64,
    pub px: f64,
    pub decision_id: u64,
}

/// A decoded v1 `signal.*` message (E2 output).
#[derive(Debug, Clone, PartialEq)]
pub struct Signal {
    pub symbol: String,
    pub action: String,
    pub sma_fast: f64,
    pub sma_slow: f64,
    pub ts_nanos: u64,
}

/// A decoded v1 `risk.*` message (E5 verdict).
#[derive(Debug, Clone, PartialEq)]
pub struct Risk {
    pub symbol: String,
    pub verdict: String,
    pub decision_id: u64,
    pub ts_nanos: u64,
}

/// A decoded v1 `ledger.*` message (E7 signed fill).
#[derive(Debug, Clone, PartialEq)]
pub struct Fill {
    pub symbol: String,
    pub side: String,
    pub qty: f64,
    pub px: f64,
    pub decision_id: u64,
    pub fill_id: u64,
    pub sig: String,
}

/// A decoded v1 `seat.*` lifecycle event.
#[derive(Debug, Clone, PartialEq)]
pub struct Seat {
    pub seat: String,
    pub event: String,
    pub ts_nanos: u64,
}

// ---------------------------------------------------------------- codec

type Map = BTreeMap<String, String>;

/// Parse an object into its fields, enforcing version and kind before anything
/// else looks at a value. `v` and `kind` are consumed here.
fn parse(s: &str, kind: &str) -> Result<Map, PayloadError> {
    let s = s.trim();
    let body = s
        .strip_prefix('{')
        .and_then(|x| x.strip_suffix('}'))
        .ok_or(PayloadError::Malformed("not-an-object"))?;
    let mut m = Map::new();
    for raw in split_fields(body) {
        let (k, v) = raw
            .split_once(':')
            .ok_or(PayloadError::Malformed("field-without-colon"))?;
        let k = k.trim().trim_matches('"').to_string();
        if k.is_empty() {
            return Err(PayloadError::Malformed("empty-key"));
        }
        if m.insert(k, v.trim().to_string()).is_some() {
            return Err(PayloadError::Malformed("duplicate-field"));
        }
    }
    let raw_v = m.remove("v").ok_or(PayloadError::MissingVersion)?;
    let v: u32 = raw_v
        .parse()
        .map_err(|_| PayloadError::Malformed("v-not-a-number"))?;
    if v != SCHEMA_VERSION {
        return Err(PayloadError::UnknownVersion(v));
    }
    let raw_kind = m.remove("kind").ok_or(PayloadError::Malformed("missing-kind"))?;
    if unquote(&raw_kind)? != kind {
        return Err(PayloadError::Malformed("kind-mismatch"));
    }
    Ok(m)
}

/// Nothing may be left over: a field this build does not know is a newer schema
/// wearing an old version label.
fn done(m: Map) -> Result<(), PayloadError> {
    if m.is_empty() {
        Ok(())
    } else {
        Err(PayloadError::Malformed("unknown-field"))
    }
}

fn take_str(m: &mut Map, key: &'static str) -> Result<String, PayloadError> {
    let v = m.remove(key).ok_or(PayloadError::Malformed("missing-field"))?;
    unquote(&v)
}

fn take_u64(m: &mut Map, key: &'static str) -> Result<u64, PayloadError> {
    let v = m.remove(key).ok_or(PayloadError::Malformed("missing-field"))?;
    v.parse()
        .map_err(|_| PayloadError::Malformed("field-not-a-u64"))
}

fn take_f64(m: &mut Map, key: &'static str) -> Result<f64, PayloadError> {
    let v = m.remove(key).ok_or(PayloadError::Malformed("missing-field"))?;
    v.parse()
        .map_err(|_| PayloadError::Malformed("field-not-a-number"))
}

fn unquote(v: &str) -> Result<String, PayloadError> {
    let inner = v
        .strip_prefix('"')
        .and_then(|x| x.strip_suffix('"'))
        .ok_or(PayloadError::Malformed("field-not-a-string"))?;
    unescape(inner)
}

fn quoted(s: &str) -> String {
    format!("\"{}\"", escape(s))
}

/// Split on commas that are not inside a string literal.
fn split_fields(body: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut in_str = false;
    let mut escaped = false;
    for ch in body.chars() {
        match ch {
            '\\' if in_str && !escaped => {
                escaped = true;
                cur.push(ch);
            }
            '"' if !escaped => {
                in_str = !in_str;
                cur.push(ch);
            }
            ',' if !in_str => out.push(std::mem::take(&mut cur)),
            _ => {
                escaped = false;
                cur.push(ch);
            }
        }
    }
    if !cur.trim().is_empty() {
        out.push(cur);
    }
    out
}

/// JSON string escaping for string fields. Symbols/seats are identity-validated
/// upstream, so this is belt-and-braces rather than the safety boundary.
fn escape(s: &str) -> String {
    let mut o = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            c if (c as u32) < 0x20 => o.push_str(&format!("\\u{:04x}", c as u32)),
            c => o.push(c),
        }
    }
    o
}

fn unescape(s: &str) -> Result<String, PayloadError> {
    let mut o = String::with_capacity(s.len());
    let mut it = s.chars();
    while let Some(c) = it.next() {
        if c != '\\' {
            o.push(c);
            continue;
        }
        match it.next() {
            Some('"') => o.push('"'),
            Some('\\') => o.push('\\'),
            Some('n') => o.push('\n'),
            Some('t') => o.push('\t'),
            Some('u') => {
                let hex: String = (0..4).filter_map(|_| it.next()).collect();
                let code =
                    u32::from_str_radix(&hex, 16).map_err(|_| PayloadError::Malformed("bad-escape"))?;
                o.push(char::from_u32(code).ok_or(PayloadError::Malformed("bad-codepoint"))?);
            }
            _ => return Err(PayloadError::Malformed("bad-escape")),
        }
    }
    Ok(o)
}

// ---------------------------------------------------------------- tick

/// Field order is fixed so two runs are byte-identical.
pub fn encode_tick(t: &Tick) -> String {
    format!(
        "{{\"v\":{SCHEMA_VERSION},\"kind\":\"tick\",\"symbol\":{},\"ts\":{},\"arrival\":{},\"price\":{}}}",
        quoted(&t.symbol),
        t.ts_nanos,
        t.arrival_nanos,
        t.price
    )
}

pub fn decode_tick(s: &str) -> Result<Tick, PayloadError> {
    let mut m = parse(s, "tick")?;
    let out = Tick {
        symbol: take_str(&mut m, "symbol")?,
        ts_nanos: take_u64(&mut m, "ts")?,
        arrival_nanos: take_u64(&mut m, "arrival")?,
        price: take_f64(&mut m, "price")?,
    };
    done(m)?;
    Ok(out)
}

// ---------------------------------------------------------------- intent

pub fn encode_intent(i: &Intent) -> String {
    format!(
        "{{\"v\":{SCHEMA_VERSION},\"kind\":\"intent\",\"side\":{},\"qty\":{},\"px\":{},\"dec\":{}}}",
        quoted(&i.side),
        i.qty,
        i.px,
        i.decision_id
    )
}

pub fn decode_intent(s: &str) -> Result<Intent, PayloadError> {
    let mut m = parse(s, "intent")?;
    let side = take_str(&mut m, "side")?;
    if !matches!(side.as_str(), "buy" | "sell" | "hold") {
        return Err(PayloadError::Malformed("unknown-side"));
    }
    let out = Intent {
        side,
        qty: take_f64(&mut m, "qty")?,
        px: take_f64(&mut m, "px")?,
        decision_id: take_u64(&mut m, "dec")?,
    };
    done(m)?;
    Ok(out)
}

// ---------------------------------------------------------------- signal

pub fn encode_signal(s: &Signal) -> String {
    format!(
        "{{\"v\":{SCHEMA_VERSION},\"kind\":\"signal\",\"symbol\":{},\"action\":{},\"sma_fast\":{},\"sma_slow\":{},\"ts\":{}}}",
        quoted(&s.symbol),
        quoted(&s.action),
        s.sma_fast,
        s.sma_slow,
        s.ts_nanos
    )
}

pub fn decode_signal(s: &str) -> Result<Signal, PayloadError> {
    let mut m = parse(s, "signal")?;
    let out = Signal {
        symbol: take_str(&mut m, "symbol")?,
        action: take_str(&mut m, "action")?,
        sma_fast: take_f64(&mut m, "sma_fast")?,
        sma_slow: take_f64(&mut m, "sma_slow")?,
        ts_nanos: take_u64(&mut m, "ts")?,
    };
    done(m)?;
    Ok(out)
}

// ---------------------------------------------------------------- risk

pub fn encode_risk(r: &Risk) -> String {
    format!(
        "{{\"v\":{SCHEMA_VERSION},\"kind\":\"risk\",\"symbol\":{},\"verdict\":{},\"dec\":{},\"ts\":{}}}",
        quoted(&r.symbol),
        quoted(&r.verdict),
        r.decision_id,
        r.ts_nanos
    )
}

pub fn decode_risk(s: &str) -> Result<Risk, PayloadError> {
    let mut m = parse(s, "risk")?;
    let out = Risk {
        symbol: take_str(&mut m, "symbol")?,
        verdict: take_str(&mut m, "verdict")?,
        decision_id: take_u64(&mut m, "dec")?,
        ts_nanos: take_u64(&mut m, "ts")?,
    };
    done(m)?;
    Ok(out)
}

// ---------------------------------------------------------------- fill

pub fn encode_fill(f: &Fill) -> String {
    format!(
        "{{\"v\":{SCHEMA_VERSION},\"kind\":\"fill\",\"symbol\":{},\"side\":{},\"qty\":{},\"px\":{},\"dec\":{},\"fill_id\":{},\"sig\":{}}}",
        quoted(&f.symbol),
        quoted(&f.side),
        f.qty,
        f.px,
        f.decision_id,
        f.fill_id,
        quoted(&f.sig)
    )
}

pub fn decode_fill(s: &str) -> Result<Fill, PayloadError> {
    let mut m = parse(s, "fill")?;
    let out = Fill {
        symbol: take_str(&mut m, "symbol")?,
        side: take_str(&mut m, "side")?,
        qty: take_f64(&mut m, "qty")?,
        px: take_f64(&mut m, "px")?,
        decision_id: take_u64(&mut m, "dec")?,
        fill_id: take_u64(&mut m, "fill_id")?,
        sig: take_str(&mut m, "sig")?,
    };
    done(m)?;
    Ok(out)
}

// ---------------------------------------------------------------- seat

pub fn encode_seat(s: &Seat) -> String {
    format!(
        "{{\"v\":{SCHEMA_VERSION},\"kind\":\"seat\",\"seat\":{},\"event\":{},\"ts\":{}}}",
        quoted(&s.seat),
        quoted(&s.event),
        s.ts_nanos
    )
}

pub fn decode_seat(s: &str) -> Result<Seat, PayloadError> {
    let mut m = parse(s, "seat")?;
    let out = Seat {
        seat: take_str(&mut m, "seat")?,
        event: take_str(&mut m, "event")?,
        ts_nanos: take_u64(&mut m, "ts")?,
    };
    done(m)?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_tick() -> Tick {
        Tick {
            symbol: "BTC_USDC".into(),
            ts_nanos: 1_762_300_000_000_000_000,
            arrival_nanos: 1_762_300_000_000_000_100,
            price: 68_123.5,
        }
    }

    #[test]
    fn round_trips_a_v1_tick() {
        let t = sample_tick();
        assert_eq!(decode_tick(&encode_tick(&t)), Ok(t));
    }

    #[test]
    fn encoding_starts_with_the_version_and_kind() {
        assert!(encode_tick(&sample_tick()).starts_with("{\"v\":1,\"kind\":\"tick\","));
    }

    #[test]
    fn an_unknown_version_is_refused_not_tolerated() {
        let t = encode_tick(&sample_tick()).replacen("\"v\":1", "\"v\":2", 1);
        assert_eq!(decode_tick(&t), Err(PayloadError::UnknownVersion(2)));
    }

    #[test]
    fn a_missing_version_is_refused() {
        let t = encode_tick(&sample_tick()).replace("\"v\":1,", "");
        assert_eq!(decode_tick(&t), Err(PayloadError::MissingVersion));
    }

    #[test]
    fn an_extra_field_is_a_schema_break() {
        let t = encode_tick(&sample_tick()).replacen('}', ",\"qty\":5}", 1);
        assert_eq!(decode_tick(&t), Err(PayloadError::Malformed("unknown-field")));
    }

    #[test]
    fn a_duplicate_field_is_refused() {
        let t = encode_tick(&sample_tick()).replacen('}', ",\"price\":1}", 1);
        assert_eq!(
            decode_tick(&t),
            Err(PayloadError::Malformed("duplicate-field"))
        );
    }

    #[test]
    fn the_old_whitespace_payload_is_not_silently_accepted() {
        assert_eq!(
            decode_tick("1762300000000000000 1762300000000000001 68123.5"),
            Err(PayloadError::Malformed("not-an-object"))
        );
    }

    #[test]
    fn a_tick_cannot_be_read_as_an_intent() {
        assert_eq!(
            decode_intent(&encode_tick(&sample_tick())),
            Err(PayloadError::Malformed("kind-mismatch"))
        );
    }

    #[test]
    fn a_quoted_symbol_with_a_comma_does_not_break_the_split() {
        let t = Tick {
            symbol: "A,B".into(),
            ..sample_tick()
        };
        assert_eq!(decode_tick(&encode_tick(&t)), Ok(t));
    }

    #[test]
    fn round_trips_a_v1_intent() {
        let i = Intent {
            side: "buy".into(),
            qty: 1.5,
            px: 68_123.5,
            decision_id: 42,
        };
        assert_eq!(decode_intent(&encode_intent(&i)), Ok(i));
    }

    #[test]
    fn an_unknown_intent_side_is_refused() {
        let t = encode_intent(&Intent {
            side: "short".into(),
            qty: 1.0,
            px: 2.0,
            decision_id: 1,
        });
        assert_eq!(decode_intent(&t), Err(PayloadError::Malformed("unknown-side")));
    }

    #[test]
    fn round_trips_the_signal_risk_fill_and_seat_frames() {
        let sig = Signal {
            symbol: "NIFTY".into(),
            action: "buy".into(),
            sma_fast: 100.25,
            sma_slow: 100.5,
            ts_nanos: 7,
        };
        assert_eq!(decode_signal(&encode_signal(&sig)), Ok(sig));

        let risk = Risk {
            symbol: "NIFTY".into(),
            verdict: "allow".into(),
            decision_id: 9,
            ts_nanos: 8,
        };
        assert_eq!(decode_risk(&encode_risk(&risk)), Ok(risk));

        let fill = Fill {
            symbol: "NIFTY".into(),
            side: "sell".into(),
            qty: 0.25,
            px: 99.5,
            decision_id: 9,
            fill_id: 12,
            sig: "deadbeef".into(),
        };
        assert_eq!(decode_fill(&encode_fill(&fill)), Ok(fill));

        let seat = Seat {
            seat: "engine-cell".into(),
            event: "up".into(),
            ts_nanos: 10,
        };
        assert_eq!(decode_seat(&encode_seat(&seat)), Ok(seat));
    }

    #[test]
    fn every_frame_refuses_version_two() {
        let two = |s: String| s.replacen("\"v\":1", "\"v\":2", 1);
        assert!(decode_signal(&two(encode_signal(&Signal {
            symbol: "X".into(),
            action: "hold".into(),
            sma_fast: 1.0,
            sma_slow: 2.0,
            ts_nanos: 1,
        })))
        .is_err());
        assert!(decode_seat(&two(encode_seat(&Seat {
            seat: "s".into(),
            event: "up".into(),
            ts_nanos: 1,
        })))
        .is_err());
    }
}
