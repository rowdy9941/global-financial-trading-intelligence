//! ledger_fill — append n signed synthetic fills via the batched hot path
//! (append_fills_batch: one transaction + one prepared statement per 1000-row
//! chunk; one WAL flush per chunk — the anti-pattern measured before this was
//! 25.8 ms/row with an autocommit per row). Keeps p50/p99 of per-batch cost.
use ledger::*;
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let (n, source) = match (args.get(1).and_then(|a| a.parse::<u32>().ok()), args.get(2)) {
        (Some(n), Some(s)) => (n, s.clone()),
        _ => { eprintln!("usage: ledger_fill <n> <source>"); std::process::exit(2); }
    };
    let mut c = match connect() {
        Ok(c) => c,
        Err(e) => { eprintln!("ledger_fill: connect failed: {e}"); std::process::exit(2); }
    };
    let chunk = 1000usize;
    let t_all = Instant::now();
    let mut batch_ns: Vec<u64> = Vec::new();
    let mut per_row_ns: Vec<u64> = Vec::new();
    let mut done = 0u32;
    while done < n {
        let m = chunk.min((n - done) as usize);
        let rows: Vec<(&str, &str, &str, f64, f64)> = (0..m)
            .map(|i| {
                let idx = (done as usize) + i;
                let side = if idx.is_multiple_of(2) { "buy" } else { "sell" };
                (
                    source.as_str(),
                    "NIFTY",
                    side,
                    0.5 + (idx % 700) as f64 / 1000.0,
                    100.0 + (idx % 48_000) as f64 / 100.0,
                )
            })
            .collect();
        let t0 = Instant::now();
        match append_fills_batch(&mut c, &rows, "owner") {
            Ok(_) => {
                let dt = t0.elapsed().as_nanos() as u64;
                batch_ns.push(dt);
                per_row_ns.push(dt / m as u64);
            }
            Err(e) => { eprintln!("ledger_fill: {e}"); std::process::exit(2); }
        }
        done += m as u32;
    }
    let wall = t_all.elapsed().as_nanos() as u64;
    batch_ns.sort_unstable();
    per_row_ns.sort_unstable();
    let pick = |v: &[u64], q: f64| v[(((v.len() as f64) * q) as usize).min(v.len() - 1)];
    println!(
        "{{\"rows\":{n},\"chunks\":{},\"wall_ns\":{wall},\"batch_ns_avg\":{},\"per_row_ns_avg\":{},\"per_row_p50\":{},\"per_row_p99\":{},\"rows_per_sec\":{}}}",
        batch_ns.len(),
        batch_ns.iter().sum::<u64>() / batch_ns.len() as u64,
        wall / n.max(1) as u64,
        pick(&per_row_ns, 0.50),
        pick(&per_row_ns, 0.99),
        1_000_000_000u64 * n.max(1) as u64 / wall.max(1),
    );
}
