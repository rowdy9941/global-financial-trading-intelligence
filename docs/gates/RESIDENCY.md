# RESIDENCY — services, not fixtures (A2 §4 A-1)

The rail, the decision cell and the ledger cell were **fixtures**: every one of them
was written to be driven once by a gate and then exit. That is fine for a pin and fatal
for a product, because a fixture cannot be supervised, restarted, drained, or asked
whether it is alive.

This gate is the regression fence for the runtime contract. Runner:
`python/gates/rt_gates.py` (wired into `G-ENGINE` as a part, so it runs in
`status.py --full`).

```bash
python3 python/gates/rt_gates.py            # ALL
python3 python/gates/rt_gates.py RESIDENT   # one case
```

## 1 · What was measured missing (before)

| Symptom | Cause |
|---|---|
| `CELL_TICKS=0` exited immediately | `while ticks < ticks_want` — `0 < 0` is false, so "no limit" meant "do nothing" |
| `e7` could not start without a gate | it blocked in `stdin.read_line()` waiting for `"GO"`, and the gate wrote that file |
| SIGTERM always became SIGKILL | `rail` blocked in `read_line`; `cell`/`e7` blocked in `next_message`, so no loop ever observed a flag |
| Rows in flight were lost on stop | nothing drained; the process died where it stood |
| `cell` panicked (rc 101) at shutdown with zero decisions | `e12_ns[((len * q).min(len - 1.0))]` indexed element 0 of an empty `Vec` |

The last one only exists because a resident cell may be stopped **before its first
decision** — it is the fixture→service transition exposing a latent bug. Fixed by
returning `0` for an empty sample:

```text
LEDGER-CELL-SUMMARY {"ticks":0,"warm_ticks":0,"decisions":0,...,"e12_p50":0,"e12_p99":0,...}
```

## 2 · The contract now

| Env | Meaning |
|---|---|
| `CELL_TICKS=0` / `E7_EXPECT=0` | **unbounded** — run until a signal. Any other value keeps the old bounded gate semantics exactly |
| `E7_REFUSE_EXIT` | `1` (default) = a refused intent exits `7` — the W0b drill pin. `0` = refuse and continue, which is what a resident ledger cell must do (one refused intent must not take the cell down for the session) |
| `RAIL_TICK_MS` | main-loop wake interval (default 200 ms); also the worst-case signal latency |
| `HEARTBEAT_MS` | heartbeat period (default 1000, clamped 100–60000, `0` disables) |

Behaviour:

* **Signals.** `rt::install_signal_handlers` sets one `AtomicBool` from SIGTERM/SIGINT.
  Every loop polls it. `docker stop` now gets an ordered exit instead of a fall through
  to SIGKILL.
* **Drain.** `rail` reads stdin on a **thread** into a channel; on a stop it stops
  accepting, drains what the channel already holds (rows that arrived before the signal
  are real data), flushes, prints `RAIL-SUMMARY`, exits 0.
* **Idle.** `wire::next_message_timeout` returns `Ok(false)` on timeout, keeping partial
  protocol lines in `self.buf`, so an MSG header split across TCP segments by a timeout
  cannot desync the stream (unit-tested).
* **Liveness.** One `HB {...}` line per interval:
  `{"svc","version","beat","uptime_s","events","decisions","intents"}`.
* **No handshake.** The `"GO"` read is gone from `e7`. Its real readiness barrier —
  subscribe + ledger connect — was always ahead of it; the gate's fixed sleep is what
  actually sequenced the drill.

Deliberate deviation from A2 §4 item 5, recorded rather than silently dropped: the
blueprint's heartbeat field list is `{svc,uptime_s,ticks,decisions,fills,kill,version}`.
`ticks`/`fills` are per-service and collapse into `events`; `decisions`/`version` are
kept; **`kill` is not in the heartbeat**, because reading it means a Postgres round trip
inside a liveness printer — a heartbeat that blocks on the database reports "dead"
exactly when the database is the thing that is broken. Kill visibility belongs in the
health view (A-11/A-15) where the failure is distinguishable from process death.

## 3 · Evidence (verbatim, 2026-11-04, tier A, 4 cores)

```text
RT-RESIDENT {"binary": "cell", "env": {"CELL_TICKS": 0}, "heartbeat_seen": true, "alive_while_unbounded": true, "sigterm_rc": 0, "exit_within_grace_ms": 214.1, "fatal_lines": [], "ok": true}
RT-IDLE {"binary": "e7", "env": {"E7_EXPECT": 0, "stdin": "/dev/null"}, "idle_s": 3.0, "alive_after_idle": true, "heartbeats": 11, "sigterm_rc": 0, "exit_within_grace_ms": 214.1, "fatal_lines": [], "ok": true}
RT-E7 {"ready_without_handshake": true, "fill_signed": true, "fills": 1, "intent_subject": "order.spot.RTGATE", "sigterm_rc": 0, "exit_within_grace_ms": 214.5, "done_line": "LEDGER-E7-DONE {\"fills\":1,\"expected\":0,\"refused\":0,\"stopped\":\"signal\"}", "ok": true}
RT-RAIL {"rows_in": 5, "ok_lines": 5, "accepted": 5, "rejected": 0, "stopped": "signal", "stdin_closed_before_exit": false, "sigterm_rc": 0, "exit_within_grace_ms": 64.0, "ok": true}
RT-HEARTBEAT {"beats": 4, "uptime_s": [0, 0, 0, 1], "uptime_monotonic": true, "uptime_advanced_past_1s": true, "required_fields": true, "sigterm_rc": 0, "ok": true}
SUMMARY {"RESIDENT": true, "IDLE": true, "E7": true, "RAIL": true, "HEARTBEAT": true, "ok": true}
```

What each line proves, and why the case exists at all:

| Case | Proves |
|---|---|
| `RESIDENT` | `CELL_TICKS=0` is "unbounded", not "exit now": the process is still alive after its first heartbeat and exits **0** on SIGTERM |
| `IDLE` | 3 s of no traffic with `stdin=/dev/null` leaves `e7` running and beating — the removed handshake was not the readiness barrier |
| `E7` | after removing `"GO"`, a real intent on `order.spot.RTGATE` is still **signed** (1 fill, `bad_sigs=0` via the W0b recon gate below) |
| `RAIL` | 5 rows in → 5 `OK`, then SIGTERM **with stdin still open** — the exit came from the signal, not EOF, and the count is exact |
| `HEARTBEAT` | the beat repeats and `uptime_s` advances; a single line would prove only that a printer ran once |

Regression check that the bounded paths did not change (the W0b engine pin, same
binaries, `CELL_TICKS=220`, `E7_EXPECT=4`):

```text
W0B-G7-ENGINE-CELL {"phaseA": {"decisions": 4, "refuse_kill": 4, "intents_published": 0, "ok": true}, "phaseB": {"decisions": 4, "allows": 4, "intents_published": 4, "e12_p50_ns": 557, "e12_p99_ns": 715, "budget_ns": 1000000, "fills_signed": 4, "bad_sigs": 0, "ok": true}, "ok": true}
```

## 4 · Portability

The handlers are **Unix-only** (`[target.'cfg(unix)'.dependencies] libc` in
`rust/rail/Cargo.toml`, one dependency, one reason: std has no signal API). Tier A
(Linux) and tier C (Windows **via WSL2**) are Unix, and tier B services run in a Linux
container. A non-Unix build compiles, prints one line that signals are unavailable, and
relies on external termination; `rt_gates.py` reports those cases as `skipped` (never as
passing) on a non-POSIX host.

Still open, deliberately: `rail` reads rows from **stdin**. That is A-10's problem (a
socket/queue ingest), not residency — the process contract is now correct regardless of
which descriptor delivers the rows.

## 5 · Not done here

* No supervision: `restart: unless-stopped` and the Compose `runtime` profile are **A-2**.
* No single entry point: `gfti up/down/status` is **A-3**.
* No metrics endpoint: the heartbeat is the floor; Prometheus/OTel arrive in **A-15**.
