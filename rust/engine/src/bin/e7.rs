//! E7 ledger cell skeleton — separate async process per §2.1: consumes
//! `order.spot.<SYM>` order intents produced by E6, appends SIGNED fill rows
//! to the D4 append-only ledger (owner role via LEDGER_SECRET_OWNER env).
//!
//! Env: RAIL_NATS_URL · LEDGER_SECRET_OWNER · E7_SUBJECT (default
//! `order.spot.>`) · E7_LEDGER_PREFIX (default `ledger`: where signed fills are
//! published as v1 `ledger.*` events, A2 A-8 item 28) · E7_EXPECT (stop after n signed fills; **0 = run forever**)
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
    // A-8 item 28: the fill is not only a D4 row, it is a bus event, so D2 holds
    // the same fact the ledger does and a replay can reconstruct it.
    let ledger_prefix = std::env::var("E7_LEDGER_PREFIX").unwrap_or_else(|_| "ledger".into());
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
    let mut intent_rejects = 0u64;
    let mut ledger_events_failed = 0u64;
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
        // R6: refuse what this build does not speak. Signed-fill integrity is the
        // reason: guessing at a future payload's numbers would sign a wrong fill.
        let text = String::from_utf8_lossy(&payload).to_string();
        let intent = match rail::payload::decode_intent(&text) {
            Ok(i) => i,
            Err(e) => {
                intent_rejects += 1;
                if intent_rejects <= 3 || intent_rejects.is_multiple_of(100) {
                    eprintln!("e7: refusing intent ({e}) — total {intent_rejects}");
                }
                continue;
            }
        };
        let dec_id = intent.decision_id;
        let side = intent.side;
        let (qty, px) = (intent.qty, intent.px);
        let symbol = symsubj.rsplit('.').next().unwrap_or("UNKNOWN").to_string();
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
                let event = rail::payload::encode_fill(&rail::payload::Fill {
                    symbol: symbol.clone(),
                    side: side.clone(),
                    qty,
                    px,
                    decision_id: dec_id,
                    fill_id: fill_id as u64,
                    sig: sig.clone(),
                });
                if let Err(e) = nats.publish(&format!("{ledger_prefix}.{symbol}"), event.as_bytes())
                {
                    // The D4 row is already committed; killing the process here
                    // would re-sign on restart. Counted, printed, and asserted
                    // zero by `d2_gates.py` instead of being swallowed.
                    ledger_events_failed += 1;
                    eprintln!("e7: ledger event publish failed (fill_id={fill_id}): {e}");
                }
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
        "LEDGER-E7-DONE {{\"fills\":{got},\"expected\":{want},\"refused\":{refused},\"payload_version_rejects\":{intent_rejects},\"ledger_events_failed\":{ledger_events_failed},\"stopped\":\"{stopped}\"}}"
    );
}
