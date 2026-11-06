//! Engine cell skeleton — one cell: E1 featurize → E2 evaluate → E5 govern →
//! E6 publish `order.spot.<SYM>` intents.
//!
//! Env: RAIL_NATS_URL · LEDGER_URL (kill-state source) · CELL_SUBJECT (default
//! market.spot.NIFTY) · CELL_SIGNAL_PREFIX / CELL_RISK_PREFIX / CELL_SEAT_PREFIX
//! (A2 A-8 item 28: the cell publishes its own E2 signal, its E5 verdict and its
//! seat lifecycle, so D2 holds the decision trail and not only the orders) ·
//! CELL_TICKS (stop after n; **0 = run forever**) ·
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
    // Intent namespace. Default is the production shape `order.spot.<SYM>`; a gate
    // or a second strategy sets `CELL_INTENT_PREFIX` so its intents are invisible to
    // the resident E7 (which subscribes `order.spot.>`), instead of sharing a subject
    // space with a live service and having both sign fills into the same table.
    let intent_prefix = std::env::var("CELL_INTENT_PREFIX").unwrap_or_else(|_| "order.spot".into());
    let signal_prefix = std::env::var("CELL_SIGNAL_PREFIX").unwrap_or_else(|_| "signal.spot".into());
    let risk_prefix = std::env::var("CELL_RISK_PREFIX").unwrap_or_else(|_| "risk.spot".into());
    let seat_prefix = std::env::var("CELL_SEAT_PREFIX").unwrap_or_else(|_| "seat".into());
    let seat = "engine-cell";
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
    let mut seen = 0u64;
    let mut warm_ticks = 0u64;
    let mut version_rejects = 0u64;
    let mut events_failed = 0u64;
    let mut dec = 0u64;
    let (mut allows, mut refuse_kill, mut refuse_size, mut no_signal) = (0u64, 0u64, 0u64, 0u64);
    let mut intents_published = 0u64;
    let (mut e6_dupe, mut e6_cap) = (0u64, 0u64);
    let mut e12_ns: Vec<u64> = Vec::new();
    let t_start = Instant::now();

    // Seat lifecycle → `seat.<seat>`. Published once at start (after the bus is
    // usable) and once at shutdown, so D2 shows when this cell had authority.
    let tick_symbol = subject.rsplit('.').next().unwrap_or("X").to_string();
    let publish_seat = |nats: &mut Nats, event: &str| -> Result<(), String> {
        nats.publish(
            &format!("{seat_prefix}.{seat}"),
            rail::payload::encode_seat(&rail::payload::Seat {
                seat: seat.to_string(),
                event: event.to_string(),
                ts_nanos: now_ms() * 1_000_000,
            })
            .as_bytes(),
        )
        .map_err(|e| e.to_string())
    };
    if let Err(e) = publish_seat(&mut nats, "up") {
        eprintln!("cell: seat event fail (fail-closed): {e}");
        std::process::exit(3);
    }

    while {
        if rt::shutdown_requested() {
            false
        } else if unbounded {
            true
        } else {
            // Bound by MESSAGES seen, not by messages accepted. A refused payload is
            // still a consumed message; counting only accepted ones made
            // `CELL_TICKS=N` wait forever when one payload in the batch was refused
            // (measured by `d2_gates.py VERSION`: 3 accepted of 5 published ⇒ hang).
            seen < ticks_want
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
        // R6: the payload carries its own version, and this build refuses what it
        // does not speak. Skipping is deliberate — mis-parsing a future payload's
        // numbers would be a money bug, while a counter plus a stderr line makes a
        // schema mismatch visible without killing a resident cell.
        seen += 1;
        let tick = match rail::payload::decode_tick(&String::from_utf8_lossy(&payload)) {
            Ok(t) => t,
            Err(e) => {
                version_rejects += 1;
                if version_rejects <= 3 || version_rejects.is_multiple_of(100) {
                    eprintln!("cell: refusing payload ({e}) — total {}", version_rejects);
                }
                continue;
            }
        };
        let px = tick.price;
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

        // A-8 item 28: the decision trail goes on the bus, not only to stdout, so
        // D2 can replay what the cell concluded instead of only what it ordered.
        // A publish failure here is counted and printed, never swallowed; the
        // intent path below stays fail-closed because an order is not telemetry.
        let action_s = match &action {
            Action::Buy => "buy",
            Action::Sell => "sell",
            Action::None => "hold",
        };
        let verdict_s = match &verdict {
            Verdict::Allow { .. } => "allow",
            Verdict::RefuseKill => "refuse_kill",
            Verdict::RefuseSize { .. } => "refuse_size",
            Verdict::NoSignal => "no_signal",
        };
        let decision_events = [
            (
                format!("{signal_prefix}.{tick_symbol}"),
                rail::payload::encode_signal(&rail::payload::Signal {
                    symbol: tick_symbol.clone(),
                    action: action_s.to_string(),
                    sma_fast: feat.sma_fast,
                    sma_slow: feat.sma_slow,
                    ts_nanos: now_ms() * 1_000_000,
                }),
            ),
            (
                format!("{risk_prefix}.{tick_symbol}"),
                rail::payload::encode_risk(&rail::payload::Risk {
                    symbol: tick_symbol.clone(),
                    verdict: verdict_s.to_string(),
                    decision_id: dec,
                    ts_nanos: now_ms() * 1_000_000,
                }),
            ),
        ];
        for (subj, body) in decision_events {
            if let Err(e) = nats.publish(&subj, body.as_bytes()) {
                events_failed += 1;
                eprintln!("cell: decision event publish failed ({subj}): {e}");
            }
        }

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
                        if let Err(e) = nats.publish(
                            &format!("{intent_prefix}.{tick_symbol}"),
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
    if let Err(e) = publish_seat(&mut nats, "down") {
        events_failed += 1;
        eprintln!("cell: seat down event failed: {e}");
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
        "LEDGER-CELL-SUMMARY {{\"ticks\":{ticks},\"received\":{seen},\"warm_ticks\":{warm_ticks},\"decisions\":{dec},\"allows\":{allows},\"refuse_kill\":{refuse_kill},\"refuse_size\":{refuse_size},\"no_signal\":{no_signal},\"intents_published\":{intents_published},\"payload_version_rejects\":{version_rejects},\"events_failed\":{events_failed},\"e6_duplicate_refused\":{e6_dupe},\"e6_inflight_refused\":{e6_cap},\"e12_p50\":{},\"e12_p99\":{},\"budget_ns\":1000000,\"wall_ms\":{}}}",
        pick(0.5),
        pick(0.99),
        t_start.elapsed().as_millis() as u64
    );
}
