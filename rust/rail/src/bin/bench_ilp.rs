//! W0a bench: QuestDB ILP line-protocol encode cost (release build). This is
//! the CPU cost the D1 flush adds per row on top of stamping — measured here,
//! asserted sanely in unit tests (dev profile), verified for bytes in the TCP
//! e2e unit test against an in-process fake server.

use rail::ilp::{ilp_line, IlpBuf};
use std::time::Instant;

fn main() {
    let rows: u32 = std::env::args()
        .nth(1)
        .and_then(|a| a.parse().ok())
        .unwrap_or(1_000_000);
    let mut durations: Vec<u64> = Vec::with_capacity(rows as usize);
    for i in 0..rows {
        let ts = 1_772_000_000_000_000_000u64 + u64::from(i) * 1_000_000_000;
        let sym = ["NIFTY", "USDJPY", "BTC_USDT"][i as usize % 3];
        let t0 = Instant::now();
        let line = ilp_line("market_spot", sym, ts, ts + 1, 100.0 + i as f64 / 100.0);
        durations.push(t0.elapsed().as_nanos() as u64);
        if i == 0 {
            // shape printed once for the gate doc, verbatim
            println!("sample_line = {line}");
        }
    }
    durations.sort_unstable();
    let n = durations.len();
    let avg = durations.iter().sum::<u64>() / n as u64;
    let pick = |q: f64| durations[(((n as f64) * q) as usize).min(n - 1)];
    // also measure batched buffer+flush-through to a Vec sink (syscall-free)
    let mut sink: Vec<u8> = Vec::with_capacity(1 << 20);
    let mut buf = IlpBuf::new("market_spot");
    let t0 = Instant::now();
    for i in 0..rows {
        let ts = 1_772_000_000_000_000_000u64 + u64::from(i) * 1_000_000_000;
        let sym = ["NIFTY", "USDJPY", "BTC_USDT"][i as usize % 3];
        buf.push(sym, ts, ts + 1, 100.0 + i as f64 / 100.0);
    }
    let flushed = buf.flush_to(&mut sink).unwrap_or(0);
    let pipe_ns = t0.elapsed().as_nanos() as u64;
    println!(
        "{{\"rows\":{n},\"ilp_ns_per_row_avg\":{avg},\"p50\":{},\"p99\":{},\"p999\":{},\"buf_push_ns_per_row\":{},\"rows_per_sec_equivalent\":{}}}",
        pick(0.50),
        pick(0.99),
        pick(0.999),
        pipe_ns / n as u64,
        1_000_000_000 / avg.max(1),
    );
    // flushed count is real output logged once — evidence, not noise
    println!("flushed_rows={flushed} sink_bytes={}", sink.len());
}
