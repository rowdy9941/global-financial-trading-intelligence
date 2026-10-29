//! R1 rail — minimal loop binary (W0a skeleton).
//!
//! Reads one whitespace-separated row per stdin line:
//! `symbol ts_nanos price rights_class`
//! Publishes stamped rows to `market.spot.<SYMBOL>` — fail-closed: if NATS is
//! unreachable the rail exits non-zero rather than running silently detached.

use rail::{stamp_row, wire::Nats, RawRow};
use std::io::{self, BufRead, Write};

const DEFAULT_NATS: &str = "nats://127.0.0.1:4222";

fn main() {
    let nats_url = std::env::var("RAIL_NATS_URL").unwrap_or_else(|_| DEFAULT_NATS.to_string());
    let mut nats = match Nats::connect(&nats_url, "rail") {
        Ok(n) => n,
        Err(e) => {
            eprintln!("rail: FAIL-CLOSED — cannot reach bus {nats_url}: {e}");
            std::process::exit(2);
        }
    };
    let stdin = io::stdin();
    let mut out = io::stdout().lock();
    let mut accepted: u32 = 0;
    let mut rejected: u32 = 0;
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let f: Vec<&str> = line.split_whitespace().collect();
        if f.len() != 4 {
            writeln!(out, "ERR fields(false=want symbol ts_nanos price rights)").unwrap();
            rejected += 1;
            continue;
        }
        let ts = f[1].parse::<u64>().ok();
        let price = f[2].parse::<f64>().ok();
        let (ts, price) = match (ts, price) {
            (Some(a), Some(b)) => (a, b),
            _ => {
                writeln!(out, "ERR parse(ts_nanos=nanos-since-epoch, price=float)").unwrap();
                rejected += 1;
                continue;
            }
        };
        let raw = RawRow { symbol: f[0], ts_nanos: ts, price, rights: f[3] };
        // arrival clock is now, at the rail's boundary
        let arrival = match std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
        {
            Ok(d) => d.as_nanos() as u64,
            Err(_) => {
                eprintln!("clock failure — refusing row (fail-closed)");
                rejected += 1;
                continue;
            }
        };
        match stamp_row(&raw, arrival) {
            Ok(r) => {
                // The publish ack (PONG) is the rail's proof the bus took the
                // row; only after that do we report OK.
                match nats.publish(
                    &format!("market.spot.{}", r.symbol),
                    format!("{} {} {}", r.ts_nanos, r.arrival_nanos, r.price).as_bytes(),
                ) {
                    Ok(()) => {
                        writeln!(
                            out,
                            "OK {} ts={} arr={} price={} rights={:?} pub=acked",
                            r.symbol, r.ts_nanos, r.arrival_nanos, r.price, r.rights
                        )
                        .unwrap();
                        accepted += 1;
                    }
                    Err(e) => {
                        writeln!(out, "ERR publish-fail({e}) — not stamped live").unwrap();
                        rejected += 1;
                    }
                }
            }
            Err(e) => {
                writeln!(out, "ERR reject(reason={e:?})").unwrap();
                rejected += 1;
            }
        }
    }
    eprintln!("rail: accepted={accepted} rejected={rejected}");
    // lint: keep process exit code success when loop drained
    let _ = out.flush();
}
