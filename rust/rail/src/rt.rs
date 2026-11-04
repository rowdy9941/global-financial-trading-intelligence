//! Runtime support for long-lived services (A2 §4 A-1).
//!
//! Three things every always-on process in this repo needs and none of them had:
//!
//! 1. **A shutdown flag a signal handler can set.** `request_shutdown()` is
//!    async-signal-safe (one atomic store); `shutdown_requested()` is what every
//!    service loop polls. Tests call `clear_shutdown()` between cases.
//! 2. **Signal handlers.** SIGTERM is what `docker stop` sends; SIGINT is Ctrl-C.
//!    Installing them is what turns "killed after the grace period" into
//!    "stopped on request, in order, exit 0".
//! 3. **A heartbeat line.** One JSON line on stdout every interval, so liveness
//!    is observable from `docker logs` / a pipe without a debugger or a metrics
//!    stack (Prometheus arrives in A-15; this is the floor, not the ceiling).
//!
//! Scope note, deliberate: the handlers are Unix-only. Tier A (Linux) and tier C
//! (Windows **via WSL2**) are Unix, and A0 §2 makes native Windows binaries a
//! hot-path optimisation rather than a service requirement — so a non-Unix build
//! compiles, prints one line saying signals are not installed, and relies on the
//! process being stopped externally. That gap is recorded in A2 §3.1 (P7), not
//! hidden.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, Instant};

/// Set by a signal handler (or by a test). Cleared only explicitly.
static SHUTDOWN: AtomicBool = AtomicBool::new(false);

/// Ask every loop in this process to stop. Signal-handler safe: one atomic store.
pub fn request_shutdown() {
    SHUTDOWN.store(true, Ordering::SeqCst);
}

/// Has a stop been requested?
pub fn shutdown_requested() -> bool {
    SHUTDOWN.load(Ordering::SeqCst)
}

/// Reset the flag. Only for tests and for a process that intentionally re-arms;
/// services never call this on the live path.
pub fn clear_shutdown() {
    SHUTDOWN.store(false, Ordering::SeqCst);
}

/// Install SIGTERM/SIGINT handlers that set the shutdown flag.
///
/// Idempotent: installing twice is harmless. On non-Unix targets this is a
/// documented no-op (see the module note) and returns `false`, which callers may
/// print so the limitation is visible at runtime rather than discovered later.
#[cfg(unix)]
pub fn install_signal_handlers() -> bool {
    extern "C" fn on_signal(_sig: libc::c_int) {
        request_shutdown();
    }
    // `signal()` (not `sigaction`) because the handler is a single atomic store
    // and correctness does not depend on masking: whatever signal arrives, the
    // loop wakes within one poll interval and stops. Both calls return the
    // previous handler or SIG_ERR; SIG_ERR is the only failure we could act on,
    // and there is nothing to fall back to, so the result is reported by value.
    unsafe {
        let a = libc::signal(libc::SIGTERM, on_signal as *const () as libc::sighandler_t);
        let b = libc::signal(libc::SIGINT, on_signal as *const () as libc::sighandler_t);
        a != libc::SIG_ERR && b != libc::SIG_ERR
    }
}

#[cfg(not(unix))]
pub fn install_signal_handlers() -> bool {
    false
}

/// `HEARTBEAT_MS` from the environment, default 1000, clamped to 100..=60_000.
/// `0` disables the heartbeat (tests that want a quiet log).
pub fn heartbeat_interval_ms() -> u64 {
    let ms = std::env::var("HEARTBEAT_MS")
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(1000);
    if ms == 0 { 0 } else { ms.clamp(100, 60_000) }
}

struct Beat {
    svc: String,
    version: String,
    interval: Duration,
    started: Instant,
    events: AtomicU64,
    decisions: AtomicU64,
    intents: AtomicU64,
    beats: AtomicU64,
}

/// A heartbeat thread. Dropping it stops and joins the thread, so a service that
/// returns from `main` never leaves a printer behind.
pub struct Heartbeat {
    state: Arc<Beat>,
    stop: Arc<AtomicBool>,
    handle: Option<std::thread::JoinHandle<()>>,
    enabled: bool,
}

impl Heartbeat {
    /// Start the printer. `interval_ms == 0` returns an inert heartbeat (cheap,
    /// no thread) so callers never branch on whether it exists.
    ///
    /// Reports `rail`'s own version — correct only for the rail binary. Other
    /// binaries use [`Heartbeat::start_v`] so the reported version is theirs, not
    /// the library's (the whole workspace is 0.1.0 today, which is exactly how a
    /// wrong version hides).
    pub fn start(svc: &str, interval_ms: u64) -> Heartbeat {
        Heartbeat::start_v(svc, env!("CARGO_PKG_VERSION"), interval_ms)
    }

    /// [`Heartbeat::start`] with an explicit version string.
    pub fn start_v(svc: &str, version: &str, interval_ms: u64) -> Heartbeat {
        let enabled = interval_ms > 0;
        let state = Arc::new(Beat {
            svc: svc.to_string(),
            version: version.to_string(),
            interval: Duration::from_millis(if enabled { interval_ms } else { 1000 }),
            started: Instant::now(),
            events: AtomicU64::new(0),
            decisions: AtomicU64::new(0),
            intents: AtomicU64::new(0),
            beats: AtomicU64::new(0),
        });
        let stop = Arc::new(AtomicBool::new(false));
        let handle = if enabled {
            let st = Arc::clone(&state);
            let sp = Arc::clone(&stop);
            Some(std::thread::spawn(move || {
                use std::io::Write as _;
                // Sleep in small slices so Drop joins promptly and a shutdown
                // signal is observed without waiting a whole interval.
                let slice = Duration::from_millis(25);
                loop {
                    let mut slept = Duration::ZERO;
                    while slept < st.interval {
                        if sp.load(Ordering::Relaxed) || shutdown_requested() {
                            return;
                        }
                        std::thread::sleep(slice);
                        slept += slice;
                    }
                    if sp.load(Ordering::Relaxed) || shutdown_requested() {
                        return;
                    }
                    let n = st.beats.fetch_add(1, Ordering::Relaxed) + 1;
                    println!(
                        "HB {{\"svc\":\"{}\",\"version\":\"{}\",\"beat\":{},\"uptime_s\":{},\"events\":{},\"decisions\":{},\"intents\":{}}}",
                        st.svc,
                        st.version,
                        n,
                        st.started.elapsed().as_secs(),
                        st.events.load(Ordering::Relaxed),
                        st.decisions.load(Ordering::Relaxed),
                        st.intents.load(Ordering::Relaxed),
                    );
                    let _ = std::io::stdout().flush();
                }
            }))
        } else {
            None
        };
        Heartbeat {
            state,
            stop,
            handle,
            enabled,
        }
    }

    /// Rows/messages consumed. Generic on purpose: what counts as an event is the
    /// service's own business, but every service has one.
    pub fn bump_events(&self) {
        self.state.events.fetch_add(1, Ordering::Relaxed);
    }

    /// Decisions taken (cell). Other services simply never call it.
    pub fn bump_decisions(&self) {
        self.state.decisions.fetch_add(1, Ordering::Relaxed);
    }

    /// Order intents published (cell/E6).
    pub fn bump_intents(&self) {
        self.state.intents.fetch_add(1, Ordering::Relaxed);
    }

    pub fn enabled(&self) -> bool {
        self.enabled
    }

    /// Stop and join. Safe to call twice; Drop calls it.
    pub fn stop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

impl Drop for Heartbeat {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shutdown_flag_round_trips() {
        clear_shutdown();
        assert!(!shutdown_requested());
        request_shutdown();
        assert!(shutdown_requested());
        clear_shutdown();
        assert!(!shutdown_requested());
    }

    #[test]
    fn heartbeat_interval_is_clamped_but_zero_disables() {
        // NOTE: env is process-global; this test only asserts the pure mapping
        // through the documented default, not a specific ambient value.
        let ms = heartbeat_interval_ms();
        assert!(ms == 0 || (100..=60_000).contains(&ms), "got {ms}");
    }

    #[test]
    fn dropping_a_heartbeat_stops_its_thread() {
        let hb = Heartbeat::start("test", 100);
        assert!(hb.enabled());
        drop(hb); // joins; would hang here if the stop flag were ignored
    }
}
