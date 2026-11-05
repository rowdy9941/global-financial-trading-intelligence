# G-STATUS — the aggregate gate report

`python/gates/status.py` is the single place that answers "is end-to-end done?".
It maps the eight gates frozen in
[`docs/decisions/A0-DECISION-BASELINE.md`](../decisions/A0-DECISION-BASELINE.md) §7
onto the actual runners in this repo, executes them, and reports one verdict per gate.

```bash
cd python/train
uv run python3 ../gates/status.py --full --install     # everything (≈15 min)
uv run python3 ../gates/status.py --full              # skip the two release installs
uv run python3 ../gates/status.py --json              # machine-readable
```

Exit code is `1` if **any gate is `FAIL`**, `0` otherwise. A `blocked` gate does *not*
set the exit code — it is an honesty marker, not a pass, and it is printed as such.

## Verdict vocabulary

| Verdict | Meaning |
|---|---|
| `OK` | Every part of the gate ran and its runner printed its own pass marker |
| `FAIL` | A part ran and failed. This is a code defect that can be fixed here |
| `blocked` | A part cannot run without an owner action (credentials, a buy, an identity decision). Never reported as green |
| `not-run` | Deliberately skipped because `--full`/`--install` was not passed |

Two rules the aggregator enforces, both learned from real failures:

1. **A runner's own marker decides its verdict.** `status.py` never infers success
   from exit code alone.
2. **Zero tests is not a pass.** `cargo test` exits `0` when a package has no tests, so
   the cargo parts sum `test result:` lines and require `passed > 0` (rail = 9,
   exec = 11). A crate that loses all its tests goes red here.

Every runner's raw stdout is kept at `${TMPDIR}/gfti-gate-status/<runner>-<case>.log`.
A gate failure that leaves no trace is not actionable, so the files are never discarded.

## Latest full run

`status.py --full --install`, 2026-11-04, 4-core box.

| # | Gate | Verdict | Evidence |
|---|---|---|---|
| 1 | **G-INSTALL** | `OK` | `install.sh --verify` ×2 — two consecutive runs · `gfti` CLI contract 7/7 (`cli_gates.py`, A2 A-3) · one-config contract 6/6 (`config_gates.py`, A2 A-4): `CONFIG` `RENDER` `PIVOT` `PARITY` `SECRETS` `DOCTOR` — a `:-` fallback or a bare `${VAR}` turns `RENDER` red · health view 3/3 (`health_gates.py`, A2 A-5): `FIELDS` `STALE` `HONEST` — `docker pause` on a live service turns the view red, names it and exits non-zero, then recovers on `unpause` |
| 2 | **G-STORE** | `OK` | 6 migrations applied twice, no error · append-only · 10× restart · sign-recon · `d1_gates.py ALL` |
| 3 | **G-RAIL** | `OK` | `cargo test -p rail` 9 passed · FANOUT **p99 1.61 ms vs 2.00 ms**, p50 310 µs, subs ready 54 ms, 20,000/20,000 rows |
| 4 | **G-ENGINE** | `OK` | ENGINE · kill cascade · residency (`rt_gates.py` 6/6 incl. COMPOSE supervision) · `cargo test -p exec` 11 passed |
| 5 | **G-DATA** | `blocked` | capture green; D3 `BTC-USDC/1m` = 4,440 rows, **`BTC-USDC/1d` = 1,347 (native)**, `BTC-USDC/1d-derived` = 2, `BTC-USD/1d` = 1,346 — blocked only by the Kraken archive |
| 6 | **G-RESEARCH** | `OK` | `w1_gates.py ALL` all cases true |
| 7 | **G-PILOT-E2E** | `blocked` | fixture P1/P2/P3 green · real-data P1 green · P2/P3 await owner inputs |
| 8 | **G-CI-OS** | `OK` | CI run `41790857772` green on `6b466c6` (the code change; the docs-only follow-up re-runs the same workflow) |

Five gates are locally closable and closed. Two are blocked on the owner, by design;
neither is a code defect and neither is reported as green.

The P1 daily identity is now the venue-native series rather than a 2-bar projection, so
the `training` blocker is gone: the daily lane runs on 1,347 bars and reports **DSR
0.4894 as-is**, which is a refusal to promote, not a pass. The canonicalization also had
to survive its own scheduler: `hl_capture.sh` re-registers both keys every 15 minutes, so
the projection was moved to `-derived` and the native row verified afterwards to still
read `rows=1347 sha=9c2376…` with an unchanged `captured_at`. See
[`P1-DATA.md`](P1-DATA.md) §3.1.

G-CI-OS only flips to `OK` once the workflow completes on the *exact* commit being
reported — the aggregator refuses to certify a tree CI has never seen, and printed
`stale: tree HEAD is …` until this commit's run went green.

## Gate 3 — FANOUT is the marginal gate, and it is measured, not asserted

FANOUT publishes 20,000 rows across four subscriptions and asserts a **p99 ≤ 2.00 ms**
transit budget (A0 §7). On this 4-core box it is genuinely close to the line:

| Circumstance | worst p99 | Rows delivered |
|---|---|---|
| Isolated, quiet box | 1.52 ms | 20,000 / 20,000 |
| Isolated, quiet box | 1.98 ms | 20,000 / 20,000 |
| Isolated, quiet box | 1.67 ms | 20,000 / 20,000 |
| Full aggregator run, installs competing | 2.50 ms | 20,000 / 20,000 |

### 2026-11-04 — one of those failures was the gate's own fault

A run reported **p99 1.07 s** with all 20,000 rows delivered and p50 382 µs. A
400× tail with an unchanged median is not a slow wire; it is a startup race. The
drill slept a fixed `time.sleep(1.0)` and assumed four `rail_sub` processes had
bound by then. When one bound later, every message published meanwhile scored as
if it had crossed the wire a second late.

This is the same defect class as WATCHDOG: **a sleep standing in for a state**. The
fix polls the NATS monitor endpoint compose already publishes (`nats -m 8222`,
bound to `127.0.0.1`) and refuses to publish until all four subscriptions exist; if
they never bind it fails loudly rather than measuring the race. `subs_ready_ms` is
recorded in the verdict so the wait is visible.

Result: **3/3 green**, p99 **1.50 / 1.55 / 1.60 ms**, p50 316–353 µs, subscribers
ready in 8–10 ms.

Three things follow, and all of them are now encoded rather than remembered:

- **The budget was never breached by dropped data.** Every run delivered every row;
  the remaining failures are p99 tail latency under CPU contention (p50 stayed at
  ~311–353 µs throughout). The aggregator now says exactly that, so a `ROWS MISSING`
  failure can never be confused with a `budget missed` failure again.
- **Ordering matters.** `install.sh` does a release build of the workspace and two
  pilot-E2E passes. When it ran before FANOUT, FANOUT inherited a saturated CPU and
  failed; with the installs ordered last, FANOUT runs on a quieter box and passes.
- **Readiness must be observed, not assumed** — the one failure mode the gate itself
  contributed to is now closed.

This is **not** a claim that 2.00 ms has comfortable headroom. The budget itself is
*not* being relaxed unilaterally — that is an owner decision with the table above as
input. If a target box has ≥8 cores the margin widens on its own.

## Gate 1 — WATCHDOG was flaky; the drill was fixed, not the assertion

`W0B-G6-WATCHDOG` observed the kill cascade and reported `false` then `true` on
identical code two minutes apart, with the engage tick detected at 244 ms one run and
1,536 ms the next. Root cause was the drill, not the watchdog:

- the sequence was **sleep-driven**, so a scheduling stall silently consumed the ticks
  the assertion needed;
- after making it **state-driven**, the engaged window collapsed to ~60 ms — shorter
  than one 250 ms poll — so the watchdog never sampled it (2 of 3 runs failed);
- the fix keeps the assertion intact and makes the drill wait for the observer: hold
  `engaged` until the watchdog's own log contains it, and widen the observation window
  to 8 s.

Result: **3/3 green**, engage detected at 191 / 436 / 217 ms, 26–28 ticks per run.
The gate now asserts the same thing it always did; it just stopped racing its observer.

## What is actually blocked, and by whom

Nothing below is a code defect, and none of it is reported as green.

| Gate | Blocker | Owner action required |
|---|---|---|
| **G-DATA** | Kraken full-trades archive (~28 GB) not fetched | download it, or accept the exclusion on record |
| **G-PILOT-E2E** / P2 | NSE `option-chain-indices` returns **404** from this box; no verified NIFTY option-chain dataset exists | provide broker credentials (Upstox is the candidate) |
| **G-PILOT-E2E** / P3 | Gold venue and feed identity are not frozen | decide the venue/feed, then it becomes collectable |
| **training** | **not a data blocker any more** — the canonical `BTC-USDC/1d` holds 1,347 bars and the daily lane runs (4 folds, pooled OOS Sharpe 0.8145). What stands between that and promotion is the gate itself: **DSR 0.4894** against a 0.95 threshold, and DSR certification is separately blocked by `metrics.DSR_CERTIFICATION_BLOCKER` (no validated reference implementation/test vectors) | nothing to unblock on the data side; promotion needs a candidate that clears the gate, and certification needs the reference vectors |

The P1 identity is not negotiable and is not a blocker being worked around:
`hyperliquid/BTC-USDC/1m` is intraday, `hyperliquid/BTC-USDC/1d` is the venue-native
daily series (1,347 bars), `hyperliquid/BTC-USDC/1d-derived` is the 2-row projection
of the intraday capture, and `hyperliquid/BTC-USD/1d` is a legacy separate identity.
None is ever relabelled as another.

## Known debt

- `python/gates/w0b_gates.py` carries **22 pre-existing ruff findings**
  (`subprocess.run` without `check=`, `SIM115`). They predate the gates CI lane and
  are deliberately not swept into it; the lane lints only the files it owns.
- The tick recorder remains Python. Measured rate is **0.88 trades/s average, p95 29/s,
  peak 436/s** — three orders of magnitude below where the GIL would matter, and it
  makes no decisions. It must move into the Rust `rail` if a tick ever feeds a *live*
  decision rather than backfill.
