//! W0a pin 4 — PIT replay harness (deterministic).
//! stdin rows → identical stamped output on every run: arrival is pinned to
//! (ts + 1) instead of wall clock, so replay is a pure function of input.
//! Gate: two runs' outputs must be byte-identical (`cmp`).

use rail::stamp_row;
use rail::RawRow;
use std::io::{BufRead, Write};

fn main() {
    let stdin = std::io::stdin();
    let mut out = std::io::stdout().lock();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let f: Vec<&str> = line.split_whitespace().collect();
        if f.len() != 4 {
            writeln!(out, "ERR fields").unwrap();
            continue;
        }
        let Ok(ts) = f[1].parse::<u64>() else { writeln!(out, "ERR parse").unwrap(); continue };
        let Ok(price) = f[2].parse::<f64>() else { writeln!(out, "ERR parse").unwrap(); continue };
        let raw = RawRow { symbol: f[0], ts_nanos: ts, price, rights: f[3] };
        match stamp_row(&raw, ts + 1) {
            Ok(r) => writeln!(out, "OK {} ts={} arr={} price={} rights={:?}",
                r.symbol, r.ts_nanos, r.arrival_nanos, r.price, r.rights).unwrap(),
            Err(e) => writeln!(out, "ERR reject(reason={e:?})").unwrap(),
        }
    }
}
