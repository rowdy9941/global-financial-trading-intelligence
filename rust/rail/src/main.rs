//! R1 rail — ingest loop.
//!
//! Reads one whitespace-separated row per stdin line:
//! `symbol ts_nanos price rights_class`
//! Publishes stamped rows to `market.spot.<SYMBOL>` — fail-closed: if NATS is
//! unreachable the rail exits non-zero rather than running silently detached.
//!
//! A-1 residency: stdin is drained by a reader thread into a bounded channel so
//! the main loop wakes every `RAIL_TICK_MS` (default 200 ms) to observe the
//! shutdown flag and emit a heartbeat. A blocking `read_line` in the main loop
//! could never do either — SIGTERM would always fall through to SIGKILL.
//!
//! Shutdown order: stop accepting new rows, drain what the channel already holds
//! (rows that arrived before the signal are real data and are neither dropped nor
//! double-stamped), flush, print the summary, exit 0.

use rail::rt::{self, Heartbeat};
use rail::{RawRow, stamp_row, wire::Nats};
use std::io::{BufRead, Write};
use std::sync::mpsc::{RecvTimeoutError, channel};
use std::time::Duration;

const DEFAULT_NATS: &str = "nats://127.0.0.1:4222";
const DEFAULT_TICK_MS: u64 = 200;

fn main() {
    let signals = rt::install_signal_handlers();
    let hb = Heartbeat::start("rail", rt::heartbeat_interval_ms());
    if !signals {
        eprintln!("rail: signal handlers unavailable on this target — stop via stdin EOF");
    }

    let nats_url = std::env::var("RAIL_NATS_URL").unwrap_or_else(|_| DEFAULT_NATS.to_string());
    let tick_ms: u64 = std::env::var("RAIL_TICK_MS")
        .ok()
        .and_then(|s| s.parse().ok())
        .filter(|v| *v > 0)
        .unwrap_or(DEFAULT_TICK_MS);

    let mut nats = match Nats::connect(&nats_url, "rail") {
        Ok(n) => n,
        Err(e) => {
            eprintln!("rail: FAIL-CLOSED — cannot reach bus {nats_url}: {e}");
            std::process::exit(2);
        }
    };

    // stdin → channel. `None` = EOF (the normal end of a finite feed).
    let (tx, rx) = channel::<Option<String>>();
    std::thread::spawn(move || {
        for line in std::io::stdin().lock().lines() {
            match line {
                Ok(l) => {
                    if tx.send(Some(l)).is_err() {
                        return; // main loop gone
                    }
                }
                Err(_) => break,
            }
        }
        let _ = tx.send(None);
    });

    let mut out = std::io::stdout().lock();
    let mut accepted: u32 = 0;
    let mut rejected: u32 = 0;
    let tick = Duration::from_millis(tick_ms);

    loop {
        // Once a stop is requested we stop waiting and only drain.
        let next = if rt::shutdown_requested() {
            // `try_recv().ok()` folds "empty" and "disconnected" into the same
            // `None`: once a stop is requested there is nothing left to wait for.
            rx.try_recv().ok()
        } else {
            match rx.recv_timeout(tick) {
                Ok(m) => Some(m),
                Err(RecvTimeoutError::Timeout) => continue, // heartbeat / signal check
                Err(RecvTimeoutError::Disconnected) => None,
            }
        };
        let Some(msg) = next else { break };
        let Some(line) = msg else { break }; // EOF

        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let f: Vec<&str> = line.split_whitespace().collect();
        if f.len() != 4 {
            writeln!(out, "ERR fields(false=want symbol ts_nanos price rights)").ok();
            rejected += 1;
            continue;
        }
        let (ts, price) = match (f[1].parse::<u64>(), f[2].parse::<f64>()) {
            (Ok(a), Ok(b)) => (a, b),
            _ => {
                writeln!(out, "ERR parse(ts_nanos=nanos-since-epoch, price=float)").ok();
                rejected += 1;
                continue;
            }
        };
        let raw = RawRow {
            symbol: f[0],
            ts_nanos: ts,
            price,
            rights: f[3],
        };
        // arrival clock is now, at the rail's boundary
        let arrival = match std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH) {
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
                        .ok();
                        accepted += 1;
                        hb.bump_events();
                    }
                    Err(e) => {
                        writeln!(out, "ERR publish-fail({e}) — not stamped live").ok();
                        rejected += 1;
                    }
                }
            }
            Err(e) => {
                writeln!(out, "ERR reject(reason={e:?})").ok();
                rejected += 1;
            }
        }
    }

    let stopped = if rt::shutdown_requested() {
        "signal"
    } else {
        "eof"
    };
    eprintln!(
        "RAIL-SUMMARY {{\"accepted\":{accepted},\"rejected\":{rejected},\"stopped\":\"{stopped}\"}}"
    );
    let _ = out.flush();
}
