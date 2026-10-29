//! W0a wire bench: producer-side publish throughput, two ack regimes against a
//! live server:
//!   per   — one PING ack per message (live-rail default, fail-closed per row)
//!   batch — one PING ack per BATCH msgs (backfill path, fail-closed per batch)
//! Same server, same payloads; the difference is pure protocol overhead.

use rail::wire::Nats;
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n = args.get(1).and_then(|a| a.parse::<u32>().ok()).unwrap_or_default();
    let (mode, url, bs) = (
        args.get(2).cloned().unwrap_or_default(),
        args.get(3).cloned().unwrap_or_default(),
        args.get(4).and_then(|v| v.parse::<u32>().ok()).unwrap_or(64),
    );
    if n == 0 || url.is_empty() || !(mode == "per" || mode == "batch") {
        eprintln!("usage: bench_wire <rows> <per|batch> <nats-url> [batch-size]");
        eprintln!("(batch mode takes an optional 4th arg: batch size, default 64)");
        std::process::exit(2);
    }
    if mode == "batch" {
        eprintln!("batch-size={bs}"); // careful: env-style knobs must not disappear silently
    }
    let mut nats = match Nats::connect(&url, "bench_wire") {
        Ok(v) => v,
        Err(e) => {
            eprintln!("bench: cannot connect {url}: {e}");
            std::process::exit(2);
        }
    };
    // warm-up: connection + server path before the measured window
    if let Err(e) = nats.publish("bench.warm", b"0 0 0.0") {
        eprintln!("bench: warm publish failed: {e}");
        std::process::exit(2);
    }
    let sym = "NIFTY";
    let t0 = Instant::now();
    match mode.as_str() {
        "per" => {
            for i in 0..n {
                let sub = format!("bench.{sym}");
                nats.publish(&sub, payload(i).as_bytes()).expect("publish");
            }
        }
        "batch" => {
            let mut batch: Vec<(String, Vec<u8>)> = Vec::with_capacity(bs as usize);
            for i in 0..n {
                batch.push((format!("bench.{sym}"), payload(i).into_bytes()));
                if batch.len() == bs as usize {
                    nats.publish_batch(&batch).expect("publish batch");
                    batch.clear();
                }
            }
            if !batch.is_empty() {
                nats.publish_batch(&batch).expect("publish batch tail");
            }
        }
        _ => {
            eprintln!("unknown mode {mode} (per|batch)");
            std::process::exit(2);
        }
    }
    let dt_ns = t0.elapsed().as_nanos() as u64;
    let per_row = dt_ns / u64::from(n);
    println!(
        "{{\"mode\":\"{mode}\",\"rows\":{n},\"total_ns\":{dt_ns},\"ns_per_row\":{per_row},\"rows_per_sec\":{}}}",
        u64::from(n) * 1_000_000_000u64 / dt_ns.max(1)
    );
}

fn payload(i: u32) -> String {
    let ts = 1_772_000_000_000_000_000u64 + u64::from(i) * 1_000_000_000;
    format!("{ts} {} 1234.5", ts + 1)
}
