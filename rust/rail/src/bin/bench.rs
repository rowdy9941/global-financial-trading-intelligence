//! W0a pin 5 — G1 concurrency bench stub: how much stamp headroom one rail
//! core has (in-loop cost per row, single-threaded conservative bound).
//! Prints a JSON line for docs/gates/W0a.md.

use rail::{stamp_row, RawRow};
use std::time::Instant;

fn main() {
    let rows: u32 = std::env::args().nth(1).and_then(|a| a.parse().ok()).unwrap_or(1_000_000);
    let mut durations: Vec<u64> = Vec::with_capacity(rows as usize);
    // synthetic feed: mixed acceptance and sized symbols; arrival = ts+1 (deterministic, not clock-bound)
    for i in 0..rows {
        let ts = 1_772_000_000_000_000_000u64 + (i as u64) * 1_000_000_000;
        let price = 100.0 + (i % 50_000) as f64 / 100.0;
        let sym = if i % 3 == 0 { "NIFTY" } else if i % 3 == 1 { "USDJPY" } else { "BTC_USDT" };
        let raw = RawRow { symbol: sym, ts_nanos: ts, price, rights: "public" };
        let t0 = Instant::now();
        let _ = stamp_row(&raw, ts + 1);
        durations.push(t0.elapsed().as_nanos() as u64);
    }
    durations.sort_unstable();
    let n = durations.len();
    let avg = durations.iter().sum::<u64>() / n as u64;
    let pick = |q: f64| durations[(((n as f64) * q) as usize).min(n - 1)];
    println!(
        "{{\"rows\":{n},\"ns_per_row_avg\":{avg},\"p50\":{},\"p99\":{},\"p999\":{},\"rows_per_sec_equivalent\":{}}}",
        pick(0.50), pick(0.99), pick(0.999),
        // perfect linear single core: 1e9 / avg ns per row
        1_000_000_000u64 / avg.max(1),
    );
}
