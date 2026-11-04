//! E7 ledger cell skeleton — separate async process per §2.1: consumes
//! `order.spot.<SYM>` order intents produced by E6, appends SIGNED fill rows
//! to the D4 append-only ledger (owner role via LEDGER_SECRET_OWNER env).
//!
//! Env: RAIL_NATS_URL · LEDGER_SECRET_OWNER · E7_SUBJECT (default
//! `order.spot.>`) · E7_EXPECT (stop after n signed fills; **0 = run forever**)
//! · E7_REFUSE_EXIT (default 1: a refused intent exits 7 — the gate pin.
//! `0` refuses and continues, which is what a resident service must do, since
//! exiting on one refused intent would take the ledger cell down for the whole
//! session) · HEARTBEAT_MS.
//!
//! A-1 residency: the historical `stdin` "GO" handshake is GONE. It made E7 a
//! fixture — unstartable except by the gate that wrote the file — and the
//! subscribe/connect above it already provided the readiness barrier the
//! handshake was standing in for. The receive loop now polls with a timeout so
//! SIGTERM is observed without waiting for the next order intent.
use ledger::*;
use rail::rt::{self, Heartbeat};
use rail::wire::Nats;
use std::time::Duration;

/// Receive poll interval. Also the worst-case SIGTERM latency.
const RECV_TICK: Duration = Duration::from_millis(200);

fn main() {
    rt::install_signal_handlers();
    let hb = Heartbeat::start_v("e7", env!("CARGO_PKG_VERSION"), rt::heartbeat_interval_ms());

    let nats_url =
        std::env::var("RAIL_NATS_URL").unwrap_or_else(|_| "nats://127.0.0.1:4222".into());
    let subj = std::env::var("E7_SUBJECT").unwrap_or_else(|_| "order.spot.>".into());
    let want: u64 = std::env::var("E7_EXPECT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(1);
    // 0 = run forever (resident). Before A-1 the default was 1 and any other
    // value simply looped that many times then exited.
    let unbounded = want == 0;
    let refuse_exits = std::env::var("E7_REFUSE_EXIT")
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .map(|v| v != 0)
        .unwrap_or(true);

    let mut nats = match Nats::connect(&nats_url, "engine-e7") {
        Ok(n) => n,
        Err(e) => {
            eprintln!("e7: FAIL-CLOSED — no bus {nats_url}: {e}");
            std::process::exit(2);
        }
    };
    if let Err(e) = nats.subscribe(&subj) {
        eprintln!("e7: subscribe fail: {e}");
        std::process::exit(2);
    }
    let mut c = match connect() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("e7: ledger connect fail: {e}");
            std::process::exit(2);
        }
    };

    let mut symsubj = String::new();
    let mut payload: Vec<u8> = Vec::new();
    let mut got = 0u64;
    let mut refused = 0u64;
    while {
        if rt::shutdown_requested() {
            false
        } else if unbounded {
            true
        } else {
            got < want
        }
    } {
        match nats.next_message_timeout(&mut symsubj, &mut payload, Some(RECV_TICK)) {
            Ok(true) => {}
            // Idle tick: the timeout exists so SIGTERM is observed without
            // waiting for the next intent.
            Ok(false) => continue,
            Err(e) => {
                eprintln!("e7: recv fail: {e}");
                std::process::exit(2);
            }
        }
        let text = String::from_utf8_lossy(&payload).to_string();
        let f: Vec<&str> = text.split_whitespace().collect();
        if f.len() != 4 {
            eprintln!("e7: skip malformed intent {text:?}");
            continue;
        }
        let (side, qty_s, px_s, dec_id) = (
            f[0].to_string(),
            f[1].to_string(),
            f[2].to_string(),
            f[3].to_string(),
        );
        let symbol = symsubj.rsplit('.').next().unwrap_or("UNKNOWN").to_string();
        let qty: f64 = match qty_s.parse() {
            Ok(q) => q,
            Err(_) => continue,
        };
        let px: f64 = match px_s.parse() {
            Ok(p) => p,
            Err(_) => continue,
        };
        // fail-closed: refuse intents while the kill state is not open
        if !matches!(kill_state(&mut c), Kill::Open) {
            eprintln!("e7: REFUSE intent dec={dec_id} — kill state not open (fail-closed)");
            refused += 1;
            hb.bump_events();
            if refuse_exits {
                // Gate pin: a never-signed intent is a hard stop in the drill.
                std::process::exit(7);
            }
            continue;
        }
        match append_fill(&mut c, "cell-e6", &symbol, &side, qty, px, "owner") {
            Ok((fill_id, sig)) => {
                println!(
                    "LEDGER-E7-FILL {{\"dec_id\":{dec_id},\"symbol\":\"{symbol}\",\"fill_id\":{fill_id},\"sig16\":\"{}\"}}",
                    &sig[..16]
                );
                use std::io::Write as _;
                let _ = std::io::stdout().flush();
                got += 1;
                hb.bump_events();
                hb.bump_intents();
            }
            Err(e) => {
                eprintln!("e7: append fail: {e}");
                std::process::exit(2);
            }
        }
    }
    let stopped = if rt::shutdown_requested() {
        "signal"
    } else {
        "count"
    };
    eprintln!(
        "LEDGER-E7-DONE {{\"fills\":{got},\"expected\":{want},\"refused\":{refused},\"stopped\":\"{stopped}\"}}"
    );
}
