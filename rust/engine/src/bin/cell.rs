//! Engine cell skeleton — one cell: E1 featurize → E2 evaluate → E5 govern →
//! E6 publish `order.spot.<SYM>` intents.
//!
//! Env: RAIL_NATS_URL · LEDGER_URL (kill-state source) · CELL_SUBJECT (default
//! market.spot.NIFTY) · CELL_TICKS (stop after n; **0 = run forever**) ·
//! CELL_DECIDE_EVERY (cadence) · CELL_MAX_QTY · KILL_REFRESH_MS (cache refresh;
//! fail-closed on error) · HEARTBEAT_MS.
//!
//! Residency (A2 §4 A-1): with `CELL_TICKS=0` this is a service. The receive
//! loop uses a 200 ms read timeout so it can observe SIGTERM, and prints one
//! `HB {...}` line per heartbeat interval. `CELL_TICKS=N` keeps the exact
//! gate semantics it always had (exit after N ticks) — the gates are unchanged.
//!
//! Kill semantics (blueprint §6/§9/§11): a synchronous first refresh happens
//! BEFORE any tick; then a background thread keeps the cache alive. ANY refresh
//! error or staleness > 3× refresh interval flips the cache to REFUSE
//! (fail-closed) until a clean refresh. Decisions read ONLY the cache — no
//! per-decision DB hit (µs-class E5 per §9).
use engine::{Action, Featurizer, Verdict, govern, intent_payload};
use exec::{RouteRefusal, Routed, Router, State};
use rail::rt::{self, Heartbeat};
use rail::wire::Nats;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, Instant};

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// Receive poll interval. Also the worst-case SIGTERM latency.
const RECV_TICK: Duration = Duration::from_millis(200);

fn refresh_once(allowed: &AtomicBool, refreshed: &AtomicU64) {
    let ok = ledger::connect()
        .ok()
        .map(|mut c| matches!(ledger::kill_state(&mut c), ledger::Kill::Open));
    allowed.store(ok.unwrap_or(false), Ordering::Relaxed);
    refreshed.store(now_ms(), Ordering::Relaxed);
}

fn main() {
    rt::install_signal_handlers();
    let hb = Heartbeat::start_v(
        "cell",
        env!("CARGO_PKG_VERSION"),
        rt::heartbeat_interval_ms(),
    );
    let nats_url =
        std::env::var("RAIL_NATS_URL").unwrap_or_else(|_| "nats://127.0.0.1:4222".into());
    let subject = std::env::var("CELL_SUBJECT").unwrap_or_else(|_| "market.spot.NIFTY".into());
    let ticks_want: u64 = std::env::var("CELL_TICKS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(200);
    // 0 = run forever. Before A-1 this exited immediately, which is the exact
    // opposite of what a service needs.
    let unbounded = ticks_want == 0;
    let decide_every: u64 = std::env::var("CELL_DECIDE_EVERY")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(50);
    let max_qty: f64 = std::env::var("CELL_MAX_QTY")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(10.0);
    let refresh_ms: u64 = std::env::var("KILL_REFRESH_MS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(250);

    // kill cache — fail-closed default; SYNCHRONOUS first refresh before I/O
    let allowed = Arc::new(AtomicBool::new(false));
    let refreshed_ms = Arc::new(AtomicU64::new(0));
    refresh_once(&allowed, &refreshed_ms);
    {
        let allowed = Arc::clone(&allowed);
        let refreshed = Arc::clone(&refreshed_ms);
        std::thread::spawn(move || {
            loop {
                std::thread::sleep(Duration::from_millis(refresh_ms));
                refresh_once(&allowed, &refreshed);
            }
        });
    }

    let mut nats = match Nats::connect(&nats_url, "engine-cell") {
        Ok(n) => n,
        Err(e) => {
            eprintln!("cell: FAIL-CLOSED — no bus {nats_url}: {e}");
            std::process::exit(2);
        }
    };
    if let Err(e) = nats.subscribe(&subject) {
        eprintln!("cell: subscribe fail: {e}");
        std::process::exit(2);
    }

    // E6 router (paper mode — the only route this binary may take; live is
    // a W3 owner-gated vocabulary, absent from exec's enum). Idempotent:
    // (seat, subject, decision_id) never publishes twice.
    let max_in_flight: usize = std::env::var("CELL_MAX_INFLIGHT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(10);
    let mut e6 = match State::new(Router::paper("engine-cell", &subject, max_in_flight)) {
        Ok(st) => st,
        Err(e) => {
            eprintln!("cell: E6 router refuse (fail-closed): {e}");
            std::process::exit(2);
        }
    };

    let mut feats = Featurizer::skeleton();
    let mut symsubj = String::new();
    let mut payload: Vec<u8> = Vec::new();
    let mut ticks = 0u64;
    let mut warm_ticks = 0u64;
    let mut dec = 0u64;
    let (mut allows, mut refuse_kill, mut refuse_size, mut no_signal) = (0u64, 0u64, 0u64, 0u64);
    let mut intents_published = 0u64;
    let (mut e6_dupe, mut e6_cap) = (0u64, 0u64);
    let mut e12_ns: Vec<u64> = Vec::new();
    let t_start = Instant::now();

    while {
        if rt::shutdown_requested() {
            false
        } else if unbounded {
            true
        } else {
            ticks < ticks_want
        }
    } {
        match nats.next_message_timeout(&mut symsubj, &mut payload, Some(RECV_TICK)) {
            Ok(true) => {}
            // Idle tick: the receive timeout exists so SIGTERM is observed
            // within RECV_TICK instead of after the next market print.
            Ok(false) => continue,
            Err(e) => {
                eprintln!("cell: recv fail: {e}");
                std::process::exit(2);
            }
        }
        let text = String::from_utf8_lossy(&payload).to_string();
        let f: Vec<&str> = text.split_whitespace().collect();
        if f.len() < 3 {
            continue;
        }
        let Ok(px) = f[2].parse::<f64>() else {
            continue;
        };
        ticks += 1;
        hb.bump_events();
        let Some(feat) = feats.push(px) else {
            continue;
        };
        warm_ticks += 1;
        if !warm_ticks.is_multiple_of(decide_every) {
            continue;
        }

        // ---- decision window (E2 + E5 internal, µs-class per §9) ----
        let t0 = Instant::now();
        let action = engine::evaluate(&feat);
        let stale_ms = now_ms().saturating_sub(refreshed_ms.load(Ordering::Relaxed));
        let fresh = stale_ms <= refresh_ms.saturating_mul(3);
        let risk_ok = fresh && allowed.load(Ordering::Relaxed);
        let verdict = govern(&action, risk_ok, max_qty);
        let e12 = t0.elapsed().as_nanos() as u64;
        e12_ns.push(e12);

        let mut line = format!(
            "LEDGER-CELL-DECISION {{\"dec_id\":{dec},\"subject\":\"{subject}\",\"e12_ns\":{e12},\"sma_fast\":{:.6},\"sma_slow\":{:.6},\"tunix_ms\":{},",
            feat.sma_fast,
            feat.sma_slow,
            now_ms()
        );
        match &verdict {
            Verdict::Allow { side, qty } => {
                allows += 1;
                // E6 route (exec crate): allowances go THROUGH the router,
                // never around it. A duplicate decision_id is refused here —
                // the same logical intent can never reach the venue twice.
                match e6.route(&verdict, px, dec) {
                    Ok(Routed::Publish(intent)) => {
                        debug_assert_eq!(intent.payload, intent_payload(side, *qty, px, dec));
                        // fail-closed cell: a publish failure kills the cell,
                        // it never "drops a decision"
                        let order_sym = subject.rsplit('.').next().unwrap_or("X").to_string();
                        if let Err(e) = nats.publish(
                            &format!("order.spot.{order_sym}"),
                            intent.payload.as_bytes(),
                        ) {
                            eprintln!("cell: E6 publish fail (fail-closed): {e}");
                            std::process::exit(3);
                        }
                        intents_published += 1;
                        hb.bump_intents();
                        let side_s = if matches!(side, Action::Buy) {
                            "buy"
                        } else {
                            "sell"
                        };
                        line.push_str(&format!("\"verdict\":\"allow\",\"side\":\"{side_s}\",\"qty\":{qty:.8},\"coi\":{},\"intent_published\":true}}", intent.client_order_id));
                    }
                    Ok(Routed::Record { .. }) => {
                        // unreachable in paper mode; refuse to publish anyway
                        line.push_str("\"verdict\":\"allow\",\"intent_published\":false,\"note\":\"e6_record\"}");
                    }
                    Err(RouteRefusal::Duplicate { .. }) => {
                        e6_dupe += 1;
                        line.push_str("\"verdict\":\"allow\",\"intent_published\":false,\"refuse\":\"e6_duplicate\"}");
                    }
                    Err(RouteRefusal::InFlightCap { cap }) => {
                        e6_cap += 1;
                        line.push_str(&format!("\"verdict\":\"allow\",\"intent_published\":false,\"refuse\":\"e6_inflight_cap\",\"cap\":{cap}}}"));
                    }
                    Err(RouteRefusal::GovernedRefusal { .. }) => {
                        // unreachable on an Allow verdict; static contract
                        eprintln!("cell: E6 governed-refusal on Allow (contract bug)");
                        std::process::exit(3);
                    }
                }
            }
            Verdict::RefuseKill => {
                refuse_kill += 1;
                line.push_str("\"verdict\":\"refuse_kill\",\"intent_published\":false}");
            }
            Verdict::RefuseSize { max } => {
                refuse_size += 1;
                line.push_str(&format!(
                    "\"verdict\":\"refuse_size\",\"max_qty\":{max},\"intent_published\":false}}"
                ));
            }
            Verdict::NoSignal => {
                no_signal += 1;
                line.push_str("\"verdict\":\"no_signal\",\"intent_published\":false}");
            }
        }
        println!("{line}");
        use std::io::Write as _;
        let _ = std::io::stdout().flush();
        hb.bump_decisions();
        dec += 1;
    }
    e12_ns.sort_unstable();
    // A-1 bug: a resident cell stopped before its first decision has an EMPTY
    // e12_ns. The old percentile expression evaluated `len - 1.0` on 0 and
    // indexed element 0 of an empty slice — a panic (rc 101) at shutdown, i.e.
    // exactly the "the service cannot stop cleanly" failure A-1 exists to fix.
    let pick = |q: f64| -> u64 {
        if e12_ns.is_empty() {
            return 0;
        }
        let last = (e12_ns.len() - 1) as f64;
        e12_ns[((e12_ns.len() as f64) * q).min(last) as usize]
    };
    eprintln!(
        "LEDGER-CELL-SUMMARY {{\"ticks\":{ticks},\"warm_ticks\":{warm_ticks},\"decisions\":{dec},\"allows\":{allows},\"refuse_kill\":{refuse_kill},\"refuse_size\":{refuse_size},\"no_signal\":{no_signal},\"intents_published\":{intents_published},\"e6_duplicate_refused\":{e6_dupe},\"e6_inflight_refused\":{e6_cap},\"e12_p50\":{},\"e12_p99\":{},\"budget_ns\":1000000,\"wall_ms\":{}}}",
        pick(0.5),
        pick(0.99),
        t_start.elapsed().as_millis() as u64
    );
}
