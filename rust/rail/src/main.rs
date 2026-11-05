//! R1 rail — ingest loop.
//!
//! Reads one whitespace-separated row per stdin line:
//! `symbol ts_nanos price rights_class`
//! Publishes stamped rows to `<MARKET_PREFIX>.<SYMBOL>` (default `market.spot`,
//! from `configs/gfti.toml [subjects].market`) — fail-closed: if NATS is
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
const DEFAULT_MARKET_PREFIX: &str = "market.spot";

/// The publish namespace. One config value (`[subjects].market`) reaches both the
/// publisher here and the subscriber (`CELL_SUBJECT`), so they cannot drift; the
/// bench uses the same knob to publish into a namespace no live service consumes,
/// which is what keeps its latency measurement free of a co-tenant's load.
fn market_prefix() -> String {
    match std::env::var("MARKET_PREFIX") {
        Ok(v)
            if !v.is_empty()
                && v.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'_')
                && !v.starts_with('.')
                && !v.ends_with('.') =>
        {
            v
        }
        _ => DEFAULT_MARKET_PREFIX.to_string(),
    }
}

/// `--ingest-file PATH` (A-7, item 23): batch ingress of a capture file through
/// the *same* validate→stamp→publish path as stdin, so a collector never writes
/// D0/D2/D3 itself. Anything else on the command line is a usage error — the rail
/// is a service, and a silently ignored argument is a silently wrong ingest.
fn ingest_file_arg() -> Option<String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.as_slice() {
        [] => None,
        [flag, path] if flag == "--ingest-file" && !path.is_empty() => Some(path.clone()),
        [flag] if flag == "--help" || flag == "-h" => {
            println!("usage: rail [--ingest-file PATH]   (default: rows on stdin)");
            std::process::exit(0);
        }
        _ => {
            eprintln!("rail: usage: rail [--ingest-file PATH]  (default: stdin)");
            std::process::exit(2);
        }
    }
}

/// Drain a reader into the queue. `None` = EOF (the normal end of a finite feed).
fn feed<R: BufRead>(reader: R, tx: &std::sync::mpsc::Sender<Option<String>>) {
    for line in reader.lines() {
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
}

fn main() {
    let ingest_file = ingest_file_arg();
    let signals = rt::install_signal_handlers();
    let hb = Heartbeat::start("rail", rt::heartbeat_interval_ms());
    if !signals {
        eprintln!("rail: signal handlers unavailable on this target — stop via stdin EOF");
    }

    let nats_url = std::env::var("RAIL_NATS_URL").unwrap_or_else(|_| DEFAULT_NATS.to_string());
    let prefix = market_prefix();
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

    // stdin (or the batch file) → channel. The reader is constructed *inside*
    // the thread: `StdinLock` is not `Send`, so it can never be moved in.
    let (tx, rx) = channel::<Option<String>>();
    let file_opened = if let Some(path) = &ingest_file {
        match std::fs::File::open(path) {
            Ok(f) => Some(f),
            Err(e) => {
                eprintln!("rail: --ingest-file {path}: {e}");
                std::process::exit(2);
            }
        }
    } else {
        None
    };
    std::thread::spawn(move || match file_opened {
        Some(f) => feed(std::io::BufReader::new(f), &tx),
        None => feed(std::io::stdin().lock(), &tx),
    });

    // NOT a held `StdoutLock`: a resident process has a heartbeat thread that also
    // prints, and a lock held across this loop starves it forever (the rail's beat
    // appeared only after shutdown — caught by the Compose healthcheck in A-2). One
    // lock per row is free next to the publish-with-ack this loop already waits for.
    fn emit(line: &str) {
        let mut o = std::io::stdout().lock();
        let _ = writeln!(o, "{line}");
        let _ = o.flush();
    }
    let mut accepted: u32 = 0;
    let mut rejected: u32 = 0;
    let mut by_symbol: std::collections::BTreeMap<String, u32> = std::collections::BTreeMap::new();
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
            emit("ERR fields(false=want symbol ts_nanos price rights)");
            rejected += 1;
            continue;
        }
        let (ts, price) = match (f[1].parse::<u64>(), f[2].parse::<f64>()) {
            (Ok(a), Ok(b)) => (a, b),
            _ => {
                emit("ERR parse(ts_nanos=nanos-since-epoch, price=float)");
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
                    &format!("{prefix}.{}", r.symbol),
                    format!("{} {} {}", r.ts_nanos, r.arrival_nanos, r.price).as_bytes(),
                ) {
                    Ok(()) => {
                        emit(&format!(
                            "OK {} ts={} arr={} price={} rights={:?} pub=acked",
                            r.symbol, r.ts_nanos, r.arrival_nanos, r.price, r.rights
                        ));
                        accepted += 1;
                        *by_symbol.entry(r.symbol.to_string()).or_insert(0) += 1;
                        hb.bump_events();
                    }
                    Err(e) => {
                        emit(&format!("ERR publish-fail({e}) — not stamped live"));
                        rejected += 1;
                    }
                }
            }
            Err(e) => {
                emit(&format!("ERR reject(reason={e:?})"));
                rejected += 1;
            }
        }
    }

    let stopped = if rt::shutdown_requested() {
        "signal"
    } else {
        "eof"
    };
    // A-7: the machine-readable ingest receipt. `d0_dump` cannot produce this — it
    // only sees the rows that survived — so the counts that a D3 provenance row
    // must carry (accepted *and* rejected) come from here, on stdout, framed.
    let symbols: Vec<String> = by_symbol
        .iter()
        .map(|(k, v)| format!("\"{k}\":{v}"))
        .collect();
    let source = match &ingest_file {
        Some(p) => format!("\"file\":\"{}\"", p.replace('\\', "/")),
        None => "\"file\":null".to_string(),
    };
    emit(&format!(
        "RAIL-INGEST {{\"accepted\":{accepted},\"rejected\":{rejected},\"stopped\":\"{stopped}\",{source},\"by_symbol\":{{{}}}}}",
        symbols.join(",")
    ));
    eprintln!(
        "RAIL-SUMMARY {{\"accepted\":{accepted},\"rejected\":{rejected},\"stopped\":\"{stopped}\"}}"
    );
}
