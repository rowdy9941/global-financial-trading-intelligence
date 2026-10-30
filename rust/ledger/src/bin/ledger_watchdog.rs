//! LEDGER-WATCHDOG — out-of-band kill-state monitor (blueprint §9 safety comms).
//!
//! No app dependency: connects straight to the D4 store and *observes* only
//! (appends nothing, mutates nothing). Every tick prints one JSON line; the
//! engine and ops treat any tick that is not `open` as REFUSE (fail-closed
//! interpretation of missing/degraded evidence).
//!
//! Flags: --interval-ms N (default 500) --timeout-ms T (0 = run forever)
//!        --strict (exit 7 if ANY tick was not open — gate mode)
//!
//! Exit: 0 if strict=off (always observes) · 7 if strict on and bad ticks seen.
use ledger::*;
use std::time::{Duration, Instant};

fn now_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

fn main() {
    let mut interval_ms = 500u64;
    let mut timeout_ms = 0u64;
    let mut strict = false;
    let a: Vec<String> = std::env::args().collect();
    let mut i = 1;
    while i < a.len() {
        match a[i].as_str() {
            "--interval-ms" => {
                i += 1;
                interval_ms = a.get(i).and_then(|s| s.parse().ok()).unwrap_or(500);
            }
            "--timeout-ms" => {
                i += 1;
                timeout_ms = a.get(i).and_then(|s| s.parse().ok()).unwrap_or(0);
            }
            "--strict" => strict = true,
            other => {
                eprintln!("LEDGER-WATCHDOG unknown arg {other}");
                std::process::exit(2);
            }
        }
        i += 1;
    }
    let start = Instant::now();
    let mut ticks = 0u32;
    let mut bad_ticks = 0u32;
    loop {
        let t = now_ms();
        match connect() {
            Err(e) => {
                bad_ticks += 1;
                println!(
                    "LEDGER-WATCHDOG {{\"tunix_ms\":{t},\"state\":\"failsafe\",\"risk_allowed\":false,\"err\":{e:?}}}"
                );
            }
            Ok(mut c) => match kill_state(&mut c) {
                Kill::Open => {
                    println!("LEDGER-WATCHDOG {{\"tunix_ms\":{t},\"state\":\"open\",\"risk_allowed\":true}}");
                }
                Kill::FailsafeEngaged => {
                    bad_ticks += 1;
                    println!("LEDGER-WATCHDOG {{\"tunix_ms\":{t},\"state\":\"failsafe\",\"risk_allowed\":false}}");
                }
                Kill::Engaged { level, reason, source } => {
                    bad_ticks += 1;
                    println!(
                        "LEDGER-WATCHDOG {{\"tunix_ms\":{t},\"state\":\"engaged\",\"level\":{level:?},\"source\":{source:?},\"reason\":{reason:?},\"risk_allowed\":false}}"
                    );
                }
            },
        }
        ticks += 1;
        std::io::Write::flush(&mut std::io::stdout()).expect("flush stdout");
        if timeout_ms > 0 && start.elapsed() >= Duration::from_millis(timeout_ms) {
            break;
        }
        std::thread::sleep(Duration::from_millis(interval_ms));
        if timeout_ms > 0 && start.elapsed() >= Duration::from_millis(timeout_ms) {
            break;
        }
    }
    println!("LEDGER-WATCHDOG-DONE {{\"ticks\":{ticks},\"bad_ticks\":{bad_ticks},\"strict\":{strict}}}");
    if strict && bad_ticks > 0 {
        std::process::exit(7);
    }
}
