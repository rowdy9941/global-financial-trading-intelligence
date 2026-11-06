# A2 — Gap inventory against the full blueprint

**Measured** 2026-11-04 on `efd8610` · 4-core Linux box · all numbers below come from commands
run against this tree and its live stack, not from memory.

**Source of truth for "what should exist":** `~/Documents/The Complete GFI FinOS.md`
(1,330 lines, 10 parts). It is the merged form of the 15 plan documents and is treated here as
the specification.

**Scope rule (owner's order, recorded in [A1](A1-FULL-SOFTWARE-ROADMAP.md)):** build the whole
system as one installable software first; market/other data second; training the 73-agent
non-LLM fleet last. So every item below is tagged **A** (required to close the software),
**B** (data, after A), or **C** (training/bug-fix, last).

---

## 0 · Method, and what "exists" means here

| Question | How it was answered |
|---|---|
| Which files exist? | `git ls-files` (164 tracked) · `find rust -name '*.rs'` (28, 3,589 LOC, 16 binaries) · `find python -name '*.py'` (40, 12,029 LOC, 13 test modules) |
| Which stores are real? | `docker compose ps` · Postgres `\dt d3.*`/`d5.*` + row counts · QuestDB `select table_name from tables()` · NATS `/jsz?streams=1` |
| Which wires carry traffic? | `grep` for literal NATS subjects in Rust · `crontab -l` · `ps -eo pid,cmd` |
| Which OSS is integrated? | `grep -rn -i "nautilus\|pysr\|mapie\|vectorbt\|qlib\|backtrader\|ccxt\|polars" --include='*.py' --include='*.rs' --include='*.toml'` → **0 hits** |
| What is the installer? | read `bootstrap/install.sh` end to end |

**"Exists" in this document always means "code that runs on the live path".** A type, a test, or a
gate fixture that is never invoked by a supervised process is counted as *missing* and is called
out as such.

---

## 1 · Verdict at a glance

The blueprint has nine layers. Counting the discrete requirements inside each:

| Layer | Blueprint requirement | Built and wired | Verdict |
|---|---|---|---|
| **Packaging** | one installable software, any OS | 0 of 6 | **not started** |
| **Stores** | D0–D6, seven stores, one writer | 4 of 7 (D0, D3, D4, D5 partial) | **partial** |
| **Rail + backfill** | R1 single writer, 8 backfill domains | 1 of 8 domains, and the rail is bypassed | **wrong shape** |
| **Engines** | E1–E7 + E4 sub-engines | E1, E2, E5, E6, E7 (fixtures) · E4-WF only · **E3 absent, E4-SIM/BT absent** | **partial** |
| **Agents** | 73 non-LLM brains / 480–545 cells | 0 | **not started** |
| **LLM seats** | 12 seats, control plane | task queue + noop worker only | **skeleton only** |
| **OSS** | 13 repos adopted at specific slots | **0** | **not started** |
| **Safety** | 9 invariants, 6 guards, L0–L4, out-of-band | kill cascade + watchdog + append-only ledger real; guards not enacted | **partial** |
| **Ops** | Prometheus · OTel · Grafana, G2/G9 dashboards | 0 | **not started** |

Headline numbers: **164 tracked files**, **8 Rust crates / 16 binaries**, **3 Compose services**,
**7 registered datasets**, **0 symbols in the registry**, **0 NATS streams**, **0 OSS engines**,
**0 agents**, **0 seats running as a seat**.

---

## 2 · What is genuinely finished (do not rebuild it)

Stated first so the TODO below is not misread as "nothing works". These are measured and gated:

1. **D4 append-only ledger** — UPDATE/DELETE denied by DB triggers (`compose/sql/001_d3_d4_init.sql`),
   signed rows, `verify_fills` recomputation (`rust/ledger/src/lib.rs:169`).
2. **Kill state, fail-closed** — `Kill::Open` is the only "risk allowed" answer and any DB error
   collapses to `FailsafeEngaged` (`rust/ledger/src/lib.rs:253-292`). Gate `W0B-G2` proves the flip.
3. **Out-of-band watchdog** — `ledger_watchdog` observes D4 directly, no app dependency,
   exit 7 in strict mode (`rust/ledger/src/bin/ledger_watchdog.rs`).
4. **D0 Parquet writer** — deterministic ordering, read-back verified, SNAPPY, 289 ns/row
   (`rust/d0/src/lib.rs`, `d0_dump`).
5. **R1 validation core** — rights class parse, as-of skew rejection, stamp
   (`rust/rail/src/lib.rs:14-120`), 9 rail tests.
6. **E6 idempotent identity + paper/shadow by construction** — shadow cannot return a publishable
   payload (`rust/exec/src/lib.rs:87-170`), 11 exec tests.
7. **E4-WF walk-forward + DSR as-is** — CPCV folds, embargo, 27 trials, DSR 0.4894 refusing
   promotion (`python/train/walk_forward.py`, `python/train/metrics.py`).
8. **Eight-gate aggregator** with the "zero tests is not a pass" rule
   (`python/gates/status.py`, `docs/gates/STATUS.md`).
9. **CI on Linux** — rust, python, sql-idempotence jobs green.

Everything in that list stays. The work below builds around it.

---

## 3 · Gap inventory

Each row: **ID · blueprint requirement · what actually exists · verdict**.
Verdicts: `missing` (no code) · `fixture` (code that only runs inside a gate) · `wrong-shape`
(mechanism exists but on the wrong side of a contract) · `partial` (real but incomplete).

### 3.1 Packaging and runtime — the "one software" layer

| ID | Requirement | What exists | Verdict |
|---|---|---|---|
| P1 | One entry point (`gfti up/down/status/install/doctor`) | nine separate ways to start things: `cargo run`, `uv run`, `docker compose`, `bootstrap/install.sh`, cron, `subprocess` inside gates | `missing` |
| P2 | Every long-lived process supervised | **no `restart:` policy on any service**; `cell` exits after `CELL_TICKS` (default 200, `rust/engine/src/bin/cell.rs:110`), `e7` after `E7_EXPECT` (`rust/engine/src/bin/e7.rs:42`), and `e7` blocks on a stdin `"GO"` handshake (`e7.rs:37`) that only a gate writes | `missing` |
| P3 | Engine services declared in the runtime | Compose has **3 services** — postgres, questdb, nats. No rail, no engine, no e7, no seat worker | `missing` |
| P4 | One config source | `compose/.env` holds exactly one key (`POSTGRES_PASSWORD`); everything else is per-binary env (`RAIL_NATS_URL`, `LEDGER_URL`, `CELL_*`, `KILL_REFRESH_MS`, `E7_*`) plus cron arguments. `configs/` is **empty** (tracked dir, 0 files) | `missing` |
| P5 | Health view ("is it running") | `python/gates/status.py` answers "is it built". Nothing answers liveness, queue depth, kill state, last fill | `missing` |
| P6 | Cross-OS install, `--tier` probe | `bootstrap/install.sh` is bash, `need docker/cargo/uv`, no `--tier` argument although A0 §2 promises one. CI matrix is `os: [ubuntu-latest]` (`.github/workflows/ci.yml:13`) | `missing` |
| P7 | Windows path | nothing; no PowerShell, no WSL2 branch | `missing` |
| P8 | Graceful shutdown | no SIGTERM handler in `rail`, `cell`, `e7`; NATS socket has no read timeout (`rust/rail/src/wire.rs`) so a blocked `next_message` cannot be interrupted | `missing` |

### 3.2 Stores D0–D6

| ID | Requirement | What exists | Verdict |
|---|---|---|---|
| S1 | **D0** Parquet lake, years of ticks/bars/filings/news | real writer, and 7 datasets on disk (`d0/hyperliquid`, `d0/nse_fo`, `d0/histdata`) — market only, 0 filings/news/world | `partial` |
| S2 | **D1** QuestDB live window (ticks/bars/chains/Greeks) | QuestDB is up and healthy, but `select table_name from tables()` returns **9 tables, all `gate_*` / `weather`**. No live market table exists; `d1_ingest` writes synthetic rows only | `fixture` |
| S3 | **D2** NATS JetStream, durable, replay, queue groups, six subject families | NATS runs with `-js`, but `/jsz` reports **streams 0, consumers 0, messages 0**. Code publishes literal `market.spot.*` and `order.spot.*` only — `signal.*`, `risk.*`, `ledger.*`, `seat.*` have **no publisher anywhere** | `wrong-shape` |
| S4 | **D3** reference store (sources, provenance, rights, symbols, calendars, cost model, venue limits, cockpit tables) | 9 tables. `d3.symbols` = **0 rows**; no calendars table; no venue-limits table; no cost model table | `partial` |
| S5 | **D4** append-only ledger + trial log + ≥5 yr | `d4.fills` (8 rows), `kill_events`, `approvals`, `pnl_recon`. **No G4 trial log table** — the 27 walk-forward trials are counted in memory for DSR but never numbered into D4 | `partial` |
| S6 | **D5** artifact registry, signed, rollback-able | `d5.artifacts` (44 rows) + files in `d5/`, HMAC + feature hash + dataset sha + DSR as-is, append-only. But **status is never `frozen`** and nothing loads D5 at boot except the W1 gate — `evaluate::verify` is called only from tests and `w1_gates.py`, never by `cell` | `partial` |
| S7 | **D6** DuckDB research cache | DuckDB is a Python dependency used ad hoc inside `walk_forward.py:104`. There is no D6 store, no build step, no process, no ownership rule | `wrong-shape` |

### 3.3 R1 rail and B1 backfill

| ID | Requirement | What exists | Verdict |
|---|---|---|---|
| R1 | **Only the rail writes raw market data** (invariant R1, interface ①) | **It does not.** Every registered dataset was written by `python/collect/*` straight to Parquet (`hl_trades_ws.py` → `d0/hyperliquid/BTC-USDC/trades/*.jsonl.gz`, `hyperliquid_daily.py` → parquet, `nse_fo_bhavcopy.py` → parquet, `histdata_daily.py` → parquet) and then registered by `register_b1.py`. `rail` is a stdin loop (`rust/rail/src/main.rs:1-40`) invoked only by benches and gates | `wrong-shape` — the single biggest architectural gap |
| R2 | Validate → as-of stamp → rights tag → fan-out → **all** stores | validation and stamp exist (`stamp_row`), fan-out to NATS exists. No rail→D0 write, no rail→D1 write, no rail→D3 provenance write | `partial` |
| R3 | Two-Clicks lineage per row (source-URL + fetch-ts + parser-version + rights-class) | lineage is per **dataset** in `d3.datasets` (`source`, `license_class`, `sha256`, `captured_at`); not per row | `partial` |
| R4 | Honest freshness (declared latency + publication lag) | not modelled anywhere | `missing` |
| R5 | Rights classes enforced mechanically | `RightsClass` enum with 3 variants (`public`, `public_research_only`, `walled`) vs blueprint's 6 classes including `no-local-store`, `commercial-redistribution-ok`, `blocked-never-commercial` | `partial` |
| R6 | B1 backfill, 8 domains in order | domain 3 (crypto) partial, domain 2 (India EOD bhavcopy) partial. **Benchmark bundle, FX tick, GDELT, filings, power/weather, registries/legal = 0** | `partial` — **Phase B** |
| R7 | B-GATE (rows verified · gaps populated · blocked handled · PIT certified · provenance complete) | `b1_gates.py` + `d3.gaps` + `verified` flag exist and run; PIT certification is not implemented (no vintage columns) | `partial` |

### 3.4 Engines E1–E7

| ID | Requirement | What exists | Verdict |
|---|---|---|---|
| E1 | Feature engine, pre-warmed RAM, <1 ms p99, bars+ticks+Greeks+indicators+microstructure+pattern flags | two SMAs over a ring buffer, `Featurizer::skeleton()` and D5-driven lengths (`rust/featurize/src/lib.rs`). No Greeks, no microstructure, no pattern flags | `partial` |
| E2 | Load **only** signed D5 artifacts at boot | `evaluate::verify` (HMAC over canonical row) exists, **but `cell` uses `engine::evaluate(&feat)`, a compiled constant rule** (`rust/engine/src/lib.rs:28`); the signed path is never on the runtime path | `fixture` |
| E3 | ML lane (model inference/prediction trunk) | nothing | `missing` |
| E4-SIM | 10⁴ deterministic simulated worlds, NumPy/Numba, seed+param-hash replay | nothing | `missing` |
| E4-BT vectorized | VectorBT / qlib / numba prototype over D6 | nothing | `missing` |
| E4-BT event | Nautilus kernel, honest FillModel/LatencyModel, hftbacktest checklist | nothing | `missing` |
| E4-WF | CPCV purge + embargo, day-grouped folds, holdout, DSR ≥0.95, crisis replay, net-of-cost | folds + embargo + DSR as-is exist (`python/train/walk_forward.py`). **No crisis replay. Costs are a flat 8 bps, not the venue model.** | `partial` |
| E5 | Governor, deterministic ladder, venue risk class + per-class caps | kill-state → signal → size cap (`rust/govern/src/lib.rs`). Flat qty 1.0, single cap, no venue risk class, no per-class exposure | `partial` |
| E6 | Adapter to venue APIs, sole order path, paper+shadow, Nautilus swap-in for crypto | `Router::paper` only on the runtime path (`cell.rs:90`); `Router::shadow` is **constructed only in tests**; no venue client of any kind — "execution" is publishing a text line to NATS | `fixture` |
| E7 | Fill consumer → signed D4, decay watch, audit trail | appends signed fills, batch + single (`rust/ledger/src/lib.rs:80-167`); no decay monitoring, no reconcile-before-orders at startup | `partial` |
| — | Reconcile-before-orders (startup + restart invariant) | `ledger_recon` exists as a CLI, but no service calls it before accepting an order | `missing` at the runtime |

### 3.5 Agents — 73 non-LLM brains, 480–545 cells

| ID | Requirement | What exists | Verdict |
|---|---|---|---|
| A1 | Universe: ≤59 stocks / 11 FX T1 / 2 FX T2 / 3 crypto | `d3.symbols` **0 rows**. The universe is not even data yet | `missing` |
| A2 | 73 agent instances = instrument × style; ≤75 active | nothing | `missing` |
| A3 | Cell registry: one style per symbol (G5), built only when E4 promotes | nothing | `missing` |
| A4 | Per-style rule ladders, session clocks, spread checks, news ladder, ODTE long-gamma | nothing | `missing` |
| A5 | T1→T2→T3 tranche gating for the 73-cell cold start | nothing | `missing` |
| A6 | Fleet control: cell state map, spawn/kill authority | nothing beyond the global kill switch | `missing` |

This entire layer is **Phase C** by the owner's instruction — but A1/A2 (universe data, cell
registry schema) are **Phase A**, because "everything wired" means the registry and the spawn
contract must exist and be exercised with a synthetic fixture before real training starts.

### 3.6 LLM seats — 12

| ID | Requirement | What exists | Verdict |
|---|---|---|---|
| L1 | Durable task queue with lease/claim/renew/finish | real: `d3.llm_tasks` + functions + `d3_seat_runtime` least-privilege role (`compose/sql/004_w0c_llm_tasks.sql`), gate `w0c_seats.py` proves DML denial | `done` |
| L2 | Seat runtime that executes real handlers | `python/seats/queue_worker.py` executes **`noop` only** (`DEFAULT_ALLOWED_TASK_TYPES = ("noop",)`) | `fixture` |
| L3 | Twelve defined seats with roles/triggers/blocked-from | nothing — no seat definition file, no prompt/skill pack, no per-seat credential | `missing` |
| L4 | L0–L3 approval bridge for seat actions; every action to D4 audit | approval queue exists (`d3.tickets`, `d4.approvals`, `ledger_appr`), but no seat writes to it | `missing` |
| L5 | Token/cost metering per seat (G9) | nothing | `missing` |
| L6 | Cockpit chat + reports + weekly digest to vault | nothing | `missing` |

`W0c.md` lists these as its own remaining increments; A1 defers them until Phase A closes.
Note the exception: **L1 is done and must not be rebuilt.**

### 3.7 Open-source engines — 13 repos, 0 wired

| ID | Repo | Declared slot | Reality |
|---|---|---|---|
| O1 | NautilusTrader (LGPL-3.0) | E4-BT event kernel + E6 crypto adapter | **absent**; not a dependency |
| O2 | VectorBT open (Apache-2.0) | E4-BT vectorized (C9 candidate) | **absent** |
| O3 | qlib (MIT, pinned) | E4 harness (A6 candidate) | **absent** |
| O4 | PySR (Apache-2.0) | symbolic search in Mode R | **absent** |
| O5 | MAPIE (BSD-3) | conformal intervals shipped with the artifact | **absent** |
| O6 | arch (permissive) | MCS/SPA + GARCH | **absent** |
| O7 | SALib (permissive) | Sobol sensitivity | **absent** |
| O8 | QuantLib (modified BSD) | Greeks/vol | **absent** |
| O9 | ccxt (MIT) | crypto data Row 2 | **absent** |
| O10 | pandera (MIT) | R1 validation (bench vs Rust) | **absent** |
| O11 | Spotware OpenApiPy + cTraderFixPy | cTrader build blocks | **absent** |
| O12 | brigade (MIT) | boss loop: loop-guards, approval bridge, cron metering | **absent** |
| O13 | rufio (MIT) | swarm DAG contract | **absent** |
| O14 | agency-agents (MIT, 267 personas) | seat role library | **absent** |
| O15 | day-trader-skill (11 SKILL.md) | cockpit skill pack | **absent** |
| O16 | TradingAgents / Vibe-Trading / quant-mind / timecopilot | seat 5–7 patterns | **absent** |
| O17 | Graphify (Apache-2.0) | code graph memory | **absent** |
| O18 | proton (Apache-2.0) | D2 streaming MV, W2 trial | **absent** |
| O19 | Prometheus / OpenTelemetry / Grafana | ops | **absent** |

Proof, not impression: `grep -rn -i "nautilus|pysr|mapie|vectorbt|qlib|backtrader|ccxt|polars"`
over all `.py`/`.rs`/`.toml`/`.yml` returns **zero hits**, and `python/train/pyproject.toml`
declares exactly two dependencies — `duckdb`, `pyarrow`.

### 3.8 Safety — invariants, guards, cascade

| ID | Requirement | What exists | Verdict |
|---|---|---|---|
| X1 | R1 collect-once/reuse-many | violated — see R1 above (collectors write the lake directly) | `wrong-shape` |
| X2 | R2 rights-firewall at Row-1 boundary only | Row 1 does not exist yet; rights classes exist but are not enforced at any boundary | `missing` |
| X3 | R3 intelligence never in hot path | structurally true (no LLM code at all) but not *enforced* — nothing prevents a Python seat from publishing to `order.*` | `partial` |
| X4 | R4 PIT/survivorship discipline | as-of skew check in the rail only; no vintage/restatement columns, no survivorship-free universe | `partial` |
| X5 | R5 provenance everywhere | per-dataset, not per-row | `partial` |
| X6 | R6 versioned event contracts | no schema/version on any NATS payload (free-text `<side> <qty> <px> <decision_id>`) | `missing` |
| X7 | R7 train→eval→paper→approve→deploy | ladder rungs 4 (WF) and 7 (approval) exist; 1,2,3,5,6 absent | `partial` |
| X8 | R8 HFT honesty | rail/engine budgets measured on Linux only, correctly labelled | `done` (documentation discipline holds) |
| X9 | R9–R14 regulatory (Upstox algo tag, HL FIU-IND/FEMA, ToS checklist) | nothing | `missing` |
| X10 | **G1** cells cannot edit their own artifacts | D5 is append-only at DB level, but there is no cell/artifact ownership boundary because there are no cells | `missing` |
| X11 | **G2** no search code in Mode T | not enforced; nothing separates Mode R and Mode T binaries | `missing` |
| X12 | **G3** research has no order path | research lane (`python/train`) has no NATS publisher today — true by absence, not by control | `partial` |
| X13 | **G4** every trial logged (DSR denominator) | 27 trials counted in-process; `d5.artifacts` stores the DSR but there is no numbered trial table | `partial` |
| X14 | **G5** one active style per symbol | no styles, no enforcement | `missing` |
| X15 | **G6** Mode R and Mode T separate processes | separate *languages*, not enforced processes; `walk_forward.py` and `cell` share the same DB with no role split | `partial` |
| X16 | Kill cascade L0→L4 | one global state with a free-text `level` column; **no per-level semantics**, no symbol/venue/market scoping (`rust/ledger/src/lib.rs:253`) | `partial` |
| X17 | Out-of-band watchdog + off-box heartbeat | watchdog process real and gated; **no off-box heartbeat**, no venue-side max-loss order, no mobile kill | `partial` |
| X18 | Monthly chaos drill (10× restart, feed-stale, venue-down, kill-switch) | 10× restart exists as a gate; the other three never rehearsed | `partial` |

**Naming collision worth fixing:** the blueprint's fleet guards are G1–G6, while the gate documents
also use G1–G6 for *gate cases* (`W0B-G1` bench, `W0B-G2` kill drill, …). Two different numbering
schemes share six identifiers. The inventory above uses `X10–X15` for the guards to keep them
apart; the TODO should rename one scheme.

### 3.9 The five interfaces

| ID | Contract | Reality |
|---|---|---|
| I1 | **Storage** — only the rail writes raw market data | **violated** (R1) |
| I2 | **Research** — OSS runs as libraries in Mode R, outputs candidates, never artifacts | no OSS, no Mode R process; `walk_forward.py` writes candidate JSON that a gate signs |
| I3 | **Artifact** — E4 passes → D5 signs → E2 loads at boot | D5 signing real; **E2 never loads at boot** |
| I4 | **Execution** — our E6 → venue APIs, the sole order path | **no venue API client exists**; E6 emits text to NATS |
| I5 | **Audit** — everything → D4 + G4 trial log, ≥5 yr, CA export | fills yes; **trial log no; decay/scoring/seat audit no; CA export no** |

### 3.10 Venues

| ID | Venue | Declared use | Reality |
|---|---|---|---|
| V1 | Hyperliquid | crypto perps, 3 symbols, 4 styles | **data only**, via Python websocket collector; no order path, no FIU-IND/FEMA decision (O11) |
| V2 | Upstox (NSE/BSE) | stocks options-buying, ≤59 symbols | **nothing**; EOD bhavcopy only; live chain 404s from this box; algo-tag question (O9) open |
| V3 | cTrader (FX ECN) | FX Team 1 (11) + Team 2 (2) | **nothing**; production limits question (O8) open |

### 3.11 Ops

| ID | Requirement | Reality |
|---|---|---|
| Z1 | Metrics (Prometheus) | none — no `/metrics` anywhere |
| Z2 | Tracing (OTel) | none |
| Z3 | Dashboards (Grafana internal, G2/G9) | none |
| Z4 | Log aggregation / rotation | Compose stdout only; cron logs to `~/.cache/gfti/*.log` with no rotation |
| Z5 | Backups (single-box fragility mitigation) | none; `pgdata`/`qdbdata` are named volumes with no dump and no offsite copy |

### 3.12 Row 1 — public products (Stage 7–10)

Terminal, News Portal, Media Hub, rights firewall, World Monitor. **Correctly absent.** They are
the last stage in the blueprint and are out of scope for Phase A/B/C; they only become possible
once Row 2 and Row 3 actually produce governed data.

---

## 4 · The TODO, in build order

Ordering rule: **nothing in B starts before A closes; nothing in C starts before B closes.** Inside
A, the order is forced by dependency — a service cannot be supervised before it is resident, and
cannot be resident before it can stop.

### Phase A — one installable software

**A-1 · Runtime residency** *(unblocks everything else)* — **DONE 2026-11-04**
1. `cell`: replace `while ticks < ticks_want` with a run-forever loop; `CELL_TICKS=0` must mean
   "unbounded", not "exit immediately"; add a `--once` flag for gates so existing fixtures keep
   working unchanged.
2. `e7`: same, and **delete the stdin `"GO"` handshake** — a service must not wait for a gate to
   say go. Move the handshake into the gate's own wait loop.
3. SIGTERM/SIGINT handlers in `rail`, `cell`, `e7`: stop reading, drain, flush, exit 0.
4. `wire.rs`: `set_read_timeout` (or a poll loop) so a blocked read can observe the shutdown flag.
5. One JSON heartbeat line per service per N seconds: `{"svc","uptime_s","ticks","decisions","fills","kill","version"}`.
6. Gate: `start → wait for heartbeat → SIGTERM → exit 0` for all three, repeated 3×.

Delivered: items 1–6 plus one bug the transition exposed (a `cell` stopped before its
first decision panicked at shutdown: percentile over an empty sample). Bounded gate
semantics are unchanged, so **no `--once` flag was added** — the existing
`CELL_TICKS=N` / `E7_EXPECT=N` envs already are the bounded mode, and a second way to say
the same thing would be a third contract to keep in sync. Heartbeat fields deviate from
item 5 on purpose (`kill` needs a DB round trip inside a liveness printer; see
[RESIDENCY](../gates/RESIDENCY.md) §2). Fence: `python/gates/rt_gates.py`, part of
`G-ENGINE`. Evidence: [RESIDENCY](../gates/RESIDENCY.md) §3.

**A-2 · Runtime profile in Compose** — **DONE 2026-11-05**
7. Add `rail`, `cell`, `e7` as Compose services on a `runtime` profile with
   `restart: unless-stopped`, `depends_on: {condition: service_healthy}`, CPU pinning per the
   blueprint's council fix A2.
8. Add `restart: unless-stopped` to postgres/questdb/nats.
9. Gate: `docker kill` each service → it returns healthy within N seconds, recorded.

Delivered: one image (`compose/runtime.Dockerfile`, multi-stage, non-root, no toolchain in
the runtime layer), the `runtime` profile with the three services on `restart:
unless-stopped` + `depends_on: service_healthy`, `restart` added to the three stores, and
`HEARTBEAT_*`/`HEARTBEAT_FILE` so a **container healthcheck reads a fresh beat** instead of
asking whether a pid exists. Two corrections to item 9/7 recorded rather than glossed:
**`docker kill` is not a restart primitive** — Docker treats `kill` and `stop` as manual
stops and the policy does not apply (measured: killed container stayed `exited` with
restarts 0, while a natural exit in the same setup restarted with restarts 1), so the gate
proves supervision with a daemon probe plus `docker stop`/`docker start` into `healthy`;
and **CPU pinning is not hardcoded**, because a cpuset naming a core the host lacks makes
the stack fail to start on a 2-core laptop — the portability A0 §2 requires. Pin via an
owner override file. The profile also caught a real bug: `rail` held a `StdoutLock` across
its loop, which starved the heartbeat thread until shutdown (the beat only appeared after
`RAIL-SUMMARY`); it now locks per row. Evidence: [RESIDENCY](../gates/RESIDENCY.md) §6.

**A-3 · `gfti` CLI** — **DONE 2026-11-05**
10. `gfti install` (wraps the installer), `up`, `down`, `status`, `doctor`, `logs`, `smoke`.
11. `gfti doctor` = prerequisite + port + tier probe (see A-6).
12. Gate: every action in the blueprint's workflow list is reachable from `gfti` alone.

Delivered: `python/gfti/cli.py` (stdlib only — an installer cannot require the toolchain
it installs), `bootstrap/gfti` (POSIX `sh`; resolves its own symlink chain, so `install.sh`
links it into `~/.local/bin` and a `git pull` updates the command), `python/gates/cli_gates.py`
(7 cases, part of `G-INSTALL`), and `install.sh --tier=A|B|C` (asserted, refused on
mismatch, rather than parsed and ignored — item 11's probe half; the tier *matrix* is A-6).
Item 10 said seven verbs; three more were added because item 12 cannot be honest without
them: `verify` (re-run the gate suite — WORKFLOW 4's "CI gates rerun"), `ingest`/`research`
(the only routes to WORKFLOW 1 and 2/5), `kill` (WORKFLOW 8), and `workflows`, a table of
the blueprint's eight workflows → verb → status. Status is a claim about the tree, not the
design: **4 ready, 2 partial, 2 missing** — `swarm` and `cockpit` exit **3** and name their
gap. There is no pretend path and no exit-0 no-op. Exit codes are the contract
(`0 ok · 1 failed · 2 environment · 3 not implemented`) and the gate asserts on them.

Two bugs found by building the front door, both fixed:

* **Gates were cwd-relative.** `w0b_gates.py` computed `REPO` from `Path(__file__).parent`
  without `.resolve()`, so invoked as `../gates/w0b_gates.py` it looked for
  `python/train/compose/.env`. Six gate files had the same shape; all now resolve.
* **The A-2 runtime profile poisoned `G-ENGINE`.** With `cell` (subscribed
  `market.spot.NIFTY`) and `e7` (subscribed `order.spot.>`) resident, the gate's own ticks
  and intents were consumed **twice**: the resident pair signed fills with the container's
  secret, so `ledger_recon` reported `bad_sigs: 8` for a healthy engine and `G-ENGINE` went
  red. A gate must not share a subject space with a live service. `cell` gained
  `CELL_INTENT_PREFIX` (default `order.spot`, the production shape); gates now use
  `order.w0b.>` / `order.rtgate.>` and a `W0BNIFTY` symbol the resident services do not
  subscribe to. Evidence: [RESIDENCY](../gates/RESIDENCY.md) §8.

Evidence (verbatim):
`SUMMARY {"DOCTOR": true, "HELP": true, "REFUSE": true, "SHIM": true, "SMOKE": true, "STATUS": true, "WF": true, "ok": true}`


**A-4 · One config file** — **DONE 2026-11-05**
13. `configs/gfti.toml` — services, ports, paths, DB DSNs, engine env, subject prefix, log level,
    tier. `.env` remains only for secrets and is generated/checked by `gfti doctor`.
14. Every binary reads its env from the same generated `.env`; no per-binary ad-hoc defaults that
    disagree with the file.
15. Gate: `gfti doctor` prints the effective config with secrets redacted; changing one value in
    the file changes behaviour of ≥2 services.

Delivered: `configs/gfti.toml` as the **only** source for the 24 non-secret values;
`python/gfti/gfti_config.py` (stdlib `tomllib`, no third-party dependency — same rule as the
CLI); the `gfti config` verb (`--json` for machines, `--sh` for a POSIX shell to `eval`);
`compose/docker-compose.yml` converted to `${VAR:?}` throughout with **no `:-` fallback
anywhere**; `install.sh` step 2/7 `eval`s `config --sh`, so the installer and `gfti up` hand
Compose the same environment by construction rather than by discipline; `python/gates/_env.py`
gains `config_env(repo)`, which every gate now calls instead of carrying its own copy of the
ports/credentials; and `python/gates/config_gates.py` (six cases, part of `G-INSTALL`):
`CONFIG` (JSON is complete and names its source), `RENDER` (Compose renders **with** the
environment and **refuses** without it), `PIVOT` (one value in the file moves ≥2 services),
`PARITY` (`--sh` reproduces `--json` exactly), `SECRETS` (the secret file is a closed set),
`DOCTOR` (the human view redacts what the JSON keeps).

Five corrections, recorded because each one was a live defect rather than a design choice:

* **A `:-` fallback is a second source of truth.** `RENDER`'s first version only proved that
  *some* variable was required — with nine of them, re-introducing one fallback still left the
  bare render failing, so the gate stayed green. It now scans the YAML for `:-` and for bare
  `${NAME}` and fails on either. That scan immediately found a **credential with a default**:
  `LEDGER_SECRET_OWNER: ${LEDGER_SECRET_OWNER:-dev-local-runtime-owner}` in four places. A
  defaulted secret in a public file is a leak, not a convenience, so the key became required in
  `[secrets]` (`optional = []`, and `declared_secrets` no longer treats an empty list as falsy).
* **`--install` was dropping assertions.** `status.py` replaced `g1.parts` with the install run
  instead of appending, so release certification quietly stopped running the CLI and config
  contracts. Fixed; `--install` now adds.
* **Every gate held a stale copy of the stack.** `rt_gates`, `w0b_gates`, `d1_gates`,
  `pilot_e2e`, `status.py`, `register_b1.py`, `walk_forward.py`, `kraken_full_trades.py` and the
  W0c seat worker each built their own `-U gfti_app -d gfti` / port literals. All now go through
  `gfti_config.psql_argv()` + `process_env()`. Two real failures fell out of the conversion: the
  W0c seat worker could not `exec` at all (compose refused to interpolate), and the D1 restore
  drill was **restoring onto a live server** — 105 rows where 100 were expected — because its
  un-enved `compose stop` silently failed. The drill now stops with the rendered env, archives
  the volume from a throwaway container (a stopped container cannot be `docker exec`'d, and a
  live one yields a torn `tar: file changed as we read it`), and restarts in a `finally`.
* **`MARKET_PREFIX` was a config key nothing read.** Wiring it into `rail` fixed a second
  subject-space collision of the A-3 kind: the fan-out bench published on `market.spot.*`, which
  the resident `cell` also consumes, so the bench measured the live stack (worst p99 2.2–2.9 ms)
  rather than the rail (1.6–1.8 ms with the stack stopped, same binaries). The bench now owns
  `market.spot.bench.*`, and the same knob isolates each pilot's publish namespace — the cause of
  P2 flaking only inside `ALL`. `MARKET_PREFIX` now reaches publisher (`rail`) and subscriber
  (`CELL_SUBJECT`) from one TOML value, which `PIVOT` asserts.
* **FANOUT's publish check could never match.** It looked for the text `accepted=20000`, but
  `rail` reports that only inside `RAIL-SUMMARY {"accepted":20000,…}`; the gate was
  unsatisfiable regardless of timing. It parses the JSON now. The 2 ms p99 contract is
  unchanged, and the bench reports every attempt (best of up to three) with the host's CPU
  count and load average, so a slow pass cannot hide behind a lucky one.

Evidence (verbatim):
`SUMMARY {"CONFIG": true, "DOCTOR": true, "PARITY": true, "PIVOT": true, "RENDER": true, "SECRETS": true, "ok": true}`
· negative test: re-introducing one `:-` fallback turns `CONFIG-RENDER` red
(`fallbacks=['POSTGRES_USER']`, rc=1) and restoring the file turns it green again.
· parity: `gfti config --json` and `eval "$(gfti config --sh)"` agree on all 24 keys and both
carry `POSTGRES_PASSWORD`.

**A-5 · Health view** — **DONE 2026-11-05**
16. `gfti status` — per service: up/down, restart count, last heartbeat age, last fill, kill state,
    queue depth, D1 write rate, NATS stream depth.
17. Gate: stop `cell` with `docker pause` → `gfti status` goes red within one interval; resume → green.

Delivered: `gfti status` (and its `--json` form) now carries all eight facts per service plus four
store blocks (`d4`, `queue`, `kill`, `d1`, `d2`), one `docker inspect` per run, one psql round trip
for the ledger, one QuestDB REST probe and one NATS `/jsz` probe. `python/gates/health_gates.py`
wires it into `G-INSTALL` as a third part: `FIELDS` (every fact present and every `up` consistent
with that row's own state/paused/heartbeat/health), `STALE` (`docker pause` on a live service → red,
named, non-zero exit, then green again after `unpause`), `HONEST`.

Three corrections, each a real defect this package forced into the open:

* **`docker pause` is invisible to `compose ps`, and `docker exec` cannot see it either.** A paused
  container still reports `state=running` with its last health verdict, so the existing view showed
  green while the process did nothing; and `heartbeat_age()` reads the beat with `docker exec`, which
  *hangs* on a paused container until its timeout. The view therefore reads state/paused/health/
  restarts from `docker inspect` (which answers either way) and only execs for the heartbeat when the
  container is not paused. `up` is derived once — `state == running and not paused and heartbeat
  within 3× the interval and health in ("", healthy)` — and the gate re-derives it from the row to
  prove the two agree.
* **`gfti status` claimed `ok` from `state`/`health` alone, while `cli_gates` re-derived `ok` with its
  own copy of that rule.** Two definitions of green is the same defect as two config files. Both now
  read the single derived `up` boolean.
* **D1 has no live table until A-9 and D2 has no declared streams until A-8.** Printing `0 rows/s`
  for a store that holds nothing is a fabricated measurement, so `d1` reports `null` **with the
  reason** (`no live D1 table yet (A-9): QuestDB holds 9 fixture table(s), no `ticks``), and `HONEST`
  fails any null without a reason and any block that carries both a reason and values. When A-9 adds
  `ticks`, the same code starts reporting rows and rows/s on its own.

Evidence (verbatim):
`SUMMARY {"FIELDS": true, "HONEST": true, "STALE": true, "ok": true}`
· `HEALTH-STALE ok — paused gfti-rail-1: red=True named=True saw_paused=True saw_down=True exit_red=True recovered=True`
· `HEALTH-HONEST ok — d1={'live_table': None, 'reason': 'no live D1 table yet (A-9): …', 'rows': None, 'rows_per_s': None} d2={'messages': 0, 'reason': None, 'streams': 0} problems=[]`
· `OK 1 G-INSTALL: gfti CLI contract=true | one config file=true | health view=true`
· human view while up: `cell running healthy yes 0 1s 7 0 off 0`.

**A-6 · Cross-OS install** — **DONE 2026-11-05**
18. Implement the `--tier` probe A0 §2 already promises: A (Linux native) / B (macOS) / C (Windows
    WSL2). Refuse loudly below the minimum, with the exact missing prerequisite.
19. POSIX `sh` installer + `gfti.ps1` PowerShell shim; no bash-isms in the POSIX path.
20. macOS runner in CI as tier B (clean install + `gfti doctor` + engine unit tests; **not** the
    latency gates).
21. Windows tier C documented as WSL2; CI on `windows-latest` limited to the CLI's non-Docker paths.
22. Gate: CI green on ≥2 OS families, and the clean-install-twice rule from A0 §2 runs on tier A.

Delivered: `gfti tier [--json]` is the probe, and `bootstrap/install.sh` **asks it** instead of
carrying its own `uname` table — the installer, `gfti doctor` and the shim now cannot disagree about
the tier, which was the same two-definitions defect as two config files. `install.sh` is
`#!/bin/sh` with no bash-only token, resolves its own path through a symlink chain, and probes the
prerequisites by **collecting** them: a host without docker *and* cargo gets one message listing both
and exits 2 before anything is touched (`MISS docker`, `MISS cargo`, `MISS uv`, then
`tier A is not installable — missing: docker cargo uv`). `cmd_install` shells out with `sh`, and on
native Windows refuses with the WSL2 route instead of raising `OSError`.
`bootstrap/gfti.ps1` is the Windows twin of the sh shim — same tier, same `config --json` byte for
byte. `docs/install-tiers.md` is the operator half: prerequisites, per-tier commands, and the parts
CI does **not** claim.

CI is now three families, job ids prefixed so the claim is machine-checkable:
`ubuntu-rust` / `ubuntu-python` / `ubuntu-sql`, `macos-tier-b` (cargo test + python lanes + PROBE/
POSIX/PS1; **no** latency gates), `windows-tier-c` (ps1 shim, tier/config/workflows verbs, and the
native-install refusal). Hosted macOS runners have no Docker daemon, so the macOS job does not claim
a Docker-backed clean install — that stays an owner action, stated in `docs/install-tiers.md`.
`G-CI-OS` reads `gh run view --json jobs` and requires **green on ≥2 OS families on the exact
release commit**, so the YAML alone cannot satisfy it.

`python/gates/tier_gates.py` (5 cases, wired into `G-INSTALL`) is the gate. `POSIX` is the case that
matters: besides `dash -n`, it copies *only* a shell and python into a temp PATH and runs the
installer there — bash is absent, docker/cargo/uv are absent, and the run must still name every
missing prerequisite and exit 2.

The first `windows-tier-c` run **found a real product defect**, which is the point of adding a
family rather than a comment: `gfti --help` died with `UnicodeEncodeError: 'charmap' codec can't
encode character '\u2192'` because every console on Windows defaults to cp1252 and the CLI's help
text is full of `→`. `_safe_console()` now sets `errors="replace"` on stdout/stderr at entry — the
user's encoding (and `PYTHONIOENCODING`) stays authoritative, an unrepresentable glyph degrades to
`?`, and a help screen can no longer abort a run. The new `ENCODING` case reproduces that crash on
tier A by running three verbs under `PYTHONIOENCODING=cp1252` and `ascii`.
(`config` is excluded from that list on purpose: its exit code depends on whether `compose/.env`
exists, so failing it in a secret-less CI checkout would say nothing about encoding.)

Evidence (verbatim):
`SUMMARY {"CIOS": true, "ENCODING": true, "POSIX": true, "PROBE": true, "PS1": true, "ok": true}`
· `TIER-POSIX ok — posix=['sh', 'dash'] bare-PATH names all 3 missing, rc=2`
· `TIER-PROBE ok — tier=A shim/doctor agree, --tier=B refused rc=2`
· `TIER-PS1 ok — pwsh pwsh: tier A, config parity on 24 keys`
· `CLI-HELP ok — rc=0 verbs=14` (the `tier` verb is in the front door)
· `sh bootstrap/install.sh` under `/bin/sh` → `install: OK`, 10.5 s warm.
· bare-PATH transcript: `MISS docker … MISS cargo … MISS uv … install.sh: tier A is not installable
  — missing: docker cargo uv` `rc=2`.

**A-7 · Make the rail the only writer (fixes I1 / R1)** — **DONE 2026-11-05**
23. Collectors hand rows to the rail instead of writing the lake: either the rail gains a
    `--ingest-file` mode, or the Python collectors publish to a local socket the rail owns.
24. The rail then writes D0 (via `d0`), D1 (via `ilp`), D2 (JetStream) and D3 provenance — one
    writer, one lineage stamp, per the storage contract.
25. Gate: every new dataset in `d3.datasets` has a rail-written provenance row; a direct
    collector→Parquet write is refused/flagged by `gfti doctor`.
26. This is the item that turns invariant R1 from a comment into a property. It is Phase A, not B,
    because "everything wired" is exactly this.

Delivered: `rail --ingest-file PATH` reads a capture file through the *same*
validate→stamp→publish path as stdin and prints a framed receipt
(`RAIL-INGEST {"accepted":N,"rejected":M,"stopped":"eof","file":"…","by_symbol":{…}}`) next to its
`OK` rows. That receipt is the point: a collector can only report how many rows it *wrote*, while
the rail reports how many it *accepted* and *rejected at its own boundary* — evidence from a second
party. `d0_dump --stdin` skips the framed line by name (and nothing else), so
`rail --ingest-file f | d0_dump --stdin out.parquet` is still one pipe.

`python/collect/rail_ingest.py` is the collector-side driver: rows → `rail --ingest-file` →
`d0_dump` → summary JSON carrying the receipt. It refuses to emit a summary if the rail's accepted
count disagrees with the rows it submitted or if the rail rejected anything ("a partial batch is not
a dataset"). Batch ingress publishes into `market.ingest.<SYMBOL>`, never `market.spot.*`,
so a historical replay cannot look like a live feed to a running consumer; `register_b1.py` asserts
the prefix starts with `market.` and records it.

Migration `007_rail_writer.sql` gives the manifest a writer: `d3.datasets.writer`
(`rail | legacy-collector`, closed by CHECK), the receipt counts/digest, an append-only
`d3.rail_ingests` history, a single-row `d3.rail_contract` cutoff, and `d3.v_r1_violations` =
legacy rows captured *after* the cutoff with no declared reason. `register_b1.py` now requires one
of exactly two things: a rail receipt, or `--legacy-writer-reason` (≥20 chars, recorded
append-only in `d3.legacy_writes`). Neither ⇒ refused before any SQL runs.

Why an exception exists at all, stated plainly: the bar collectors (`binance_daily`,
`hyperliquid_daily`, `resample_1m_to_1d`) emit OHLCV, and the rail's contract is single-price. Making
them "comply" by pushing only the close price would delete data — a worse defect than the one being
fixed. So they register as `legacy-collector` with a recorded reason, the debt is a countable number
in `d3.v_r1_writer_mix`, and `gfti doctor` prints it (`rail_writer` check). The remaining work — an
explicit rail bar schema — is tracked as **A-7b** below.

Two real defects were found by building this, not by reasoning about it:
· The manifest row and its writer evidence were written in **two** psql calls, so the R1 view could
  observe a legacy row whose declared reason had not landed yet. The 15-minute capture cron
  overlapped a gate run and produced exactly that false violation. Both inserts are now one
  transaction.
· The A-7 proof cannot use "two runs are byte-identical": `arrival_nanos` is the rail's real
  boundary clock and *must* differ between runs. The R1 `INGEST` case therefore asserts identical
  `(symbol, ts_nanos, price)` payloads and a differing arrival stamp — the weaker claim that is
  actually true.

Evidence (verbatim):
`R1-INGEST ok   rail accepted 200 rejected 1; D0 wrote 200 rows, payload identical across two runs (arrival stamp differs by design)`
· `R1-REFUSE ok   refused without a receipt (rc!=0, message names the rail); with a declared reason it proceeds to the artifact check`
· `R1-DECLARED ok   view empty (1 rail-written, 6 declared legacy); an undeclared write inside a rolled-back txn was flagged`
· `R1-DOCTOR ok   doctor rail_writer ok — 1 rail-written, 6 declared legacy write(s), 0 undeclared`
· real capture through the new path: 16 recorded Hyperliquid trade segments → `rail --ingest-file`
  → `d0_dump` → `d0/hyperliquid/BTC-USDC/tick.parquet`, 199,818 rows, `writer='rail'`,
  `rail_accepted=199998`, `rail_rejected=0`, D3 row `verified=t`, zero open gaps.
· live cron (`scripts/hl_capture.sh`) re-run end-to-end after the change: `hl_capture: OK …`,
  `register_b1` accepted the declared-reason path, `d3.v_r1_violations` stayed at 0.

**A-7b · Rail bar schema (follow-on, tracked)**
The rail's ingest contract is `symbol ts_nanos price rights`. Bar captures carry OHLCV, so they
currently register as declared legacy writes. A-7b adds a second record type (`bars`, with
open/high/low/close/volume) and a matching D0 schema, then converts the three bar collectors and
deletes `d3.legacy_writes` from the happy path. Until then the exception is visible, bounded, and
counted — not silent.

**A-8 · Make D2 a real store**
27. Declare JetStream streams for `market.*`, `signal.*`, `order.*`, `risk.*`, `ledger.*`, `seat.*`
    with retention, and a durable consumer per engine.
28. Publish `signal.*` (E2 output), `risk.*` (E5 verdicts), `ledger.*` (E7 fills), `seat.*` (seat
    events) — today only `market.spot.*` and `order.spot.*` exist.
29. Version every payload (invariant R6): `{"v":1, ...}`, reject unknown versions.
30. Gate: stream count > 0, replay a stream and get identical decisions.

**Delivered 2026-11-06.** Both halves of the item were missing for the same reason: D2 was a
*claim*. The stack ran `nats-server -js`, so `/jsz` answered `"streams": 0` while the docs said D2
retained the bus, and no payload carried a version while six namespaces were documented as existing.

Streams and durables are now **data in `configs/gfti.toml [d2]`** (6 streams: MARKET/SIGNAL/ORDER/
RISK/LEDGER/SEAT, `file` storage, `max_age_s` 86400 for market/signal and 604800 for the decision
and money streams; 2 durables: `cell-market` on MARKET, `e7-order` on ORDER) and `python/gfti/d2.py`
applies them — `gfti d2 apply|status` is the 15th verb. Why the declaration belongs in the config
rather than in Python: a stream the code creates is invisible to the operator, and A-4 had already
made this file the only source for non-secrets. `[d2]` is structured data, so it is the one section
outside the sparse `SCHEMA` and it validates itself in `d2.declarations` (upper-case unique names,
non-empty subjects, a consumer must name a declared stream).

Three design decisions worth recording:

- **`rail::payload` is the single versioned codec** (invariant R6) for `tick|intent|signal|risk|fill|
  seat`, all `{"v":1,"kind":…,…}`, strict in both directions: no duplicate fields, no unknown fields,
  no unknown version, no unknown kind. `exec::intent_payload` now *delegates* to it instead of
  defining a second wire format — two definitions of one contract is the same defect as two config
  files, and it had already put E7's parser on a format the cell could drift from.
- **Durables use `deliver_policy=new`**, not `all`: an engine must not replay yesterday's market data
  into a live decision at boot. Historical replay is an explicit, ephemeral read (`d2.fetch`), never
  a side effect of starting a service.
- **The FANOUT bench moved out of `market.>`** to `bench.market.spot.*`. Once a MARKET stream declared
  `market.>`, the 20,000 synthetic rows of every FANOUT run were written into the durable store
  (`/jsz` messages 565 → 40,565 across two attempts) and made `gfti d2 status` message counts
  meaningless. This is A0's existing rule — *a gate must not share a subject space with a live
  service* — applied to a service that had just started existing.

Two real defects were found by running the gate, not by reasoning about it:

- **A bounded run could hang forever.** `CELL_TICKS=N` counted only *accepted* payloads, so with 2 of
  5 messages refused the loop waited for ticks that would never arrive (measured: VERSION timed out
  after 90 s). It is now bounded by messages *seen*.
- **A subscriber was billing its parser to the bus.** `rail_sub` stamped its clock *after* decoding,
  so the transit it reported included its own JSON parse. Stamped before decode now; a measurement
  must not include the instrument.

Evidence (verbatim):
`SUMMARY {"CONSUMERS": true, "REPLAY": true, "STREAMS": true, "VERSION": true, "ok": true}`
· `D2-STREAMS ok   6 declared streams exist on the server (/jsz streams=6); 6/6 publisher namespaces captured (market→MARKET, signal→SIGNAL, order→ORDER, risk→RISK, ledger→LEDGER, seat→SEAT); an undeclared namespace does not resolve`
· `D2-VERSION ok   cell refused 2×v2 and accepted 3×v1 on market.bench.<RUN>.version`
· `D2-REPLAY ok   60 retained messages read twice identically; cell decided 8× on each pass with identical (sma, verdict, side, qty) and differing timing`
· `D2-CONSUMERS ok   cell-market on MARKET received a message published to its filter (pending 185→186), recreated cleanly (pending 0, consumers applied 2)`
· `gfti d2 apply` is convergent: an existing stream is compared on its *declared* keys only
  (`name, subjects, retention, max_age, storage`) and updated, so a config change lands instead of
  being ignored because "the stream exists"; a durable whose `deliver_policy` changed is deleted and
  recreated, because the server answers `deliver policy can not be updated`.
· codec cost, measured with a micro-benchmark rather than assumed: **encode 0.83 µs/msg, decode
  3.36 µs/msg** (112-byte tick, release build) vs 0.41 µs for the old `split_whitespace`.

**A-9 · D1 and D6 become defined stores**
31. Rail writes a real live table to QuestDB (`ticks`, `bars`), not just `gate_*`.
32. D6 becomes an explicit, read-only materialization step (a `gfti research build` that produces a
    DuckDB file from D0), owned by one process, never in a critical path.

**A-10 · Engines that are not fixtures**
33. `cell` loads a signed D5 artifact at boot via `evaluate::verify`; refuse to start if the
    signature or feature hash does not match (this is E2's whole contract).
34. `Router::shadow` reachable at runtime (`--mode shadow`) with zero order authority enforced in
    the type system, not by convention.
35. Add the missing engine skeletons so the interfaces exist: **E3** (model inference, even a
    mean-reversion stub), **E4-SIM** (N deterministic worlds), **E4-BT vectorized**,
    **E4-BT event** (event loop with FillModel/LatencyModel traits).
36. Reconcile-before-orders at service start: `cell` and `e7` call `ledger_recon` and refuse to
    trade on mismatch.
37. Gate: boot with a tampered artifact → refuse; boot with a valid one → run; restart mid-flight
    → reconcile before the first order.

**A-11 · Guards as code (X10–X15)**
38. G1: no process running as the cell role can write `d5.*` (already true at DB level — add the
    assertion to a gate).
39. G2/G6: Mode R and Mode T become separate Compose services with separate DB roles; a Mode T
    role cannot read the research schema; a Mode R role cannot publish `order.*`.
40. G3: assert the research role has no NATS permission object.
41. G4: `d4.trials` (append-only, numbered) — every grid trial, every fold, every parameter set.
    `walk_forward.py` becomes a caller of it. DSR's denominator stops being a literal in memory.
42. G5: `d3.cells` registry with a unique partial index on `(symbol) WHERE active`.
43. Gate: one negative test per guard — the violation must fail, not warn.

**A-12 · OSS wiring, behind the contracts (no training)**
44. Each OSS engine lands behind its declared interface with a **smoke fixture**: tiny synthetic
    dataset in, expected shape out. Not trained models — wiring proof.
    `nautilus` (E4-BT event + E6 crypto, unmodified, LGPL) · `vectorbt` or `qlib` (E4-BT vec) ·
    `pysr`/`mapie`/`arch`/`salib` (Mode R research contract) · `quantlib` (Greeks) · `ccxt`
    (crypto data) · `pandera` (rail validation parity bench) · `brigade`/`rufio`/`agency-agents`
    (cockpit contracts) · `graphify` (code graph).
45. Record per repo: license, exact slot, entry point, and whether it can be swapped out.
46. Gate: `gfti selftest --oss` runs every adapter against its fixture and prints a pass line per
    engine; a missing dependency is a loud refuse, never a silent skip.

**A-13 · Venue adapters (contract only, all disabled)**
47. `exec::Venue` trait — `submit`, `cancel`, `reconcile`, `capabilities`. Implementations:
    `paper` (now), `hyperliquid` (read-only + order path behind an owner flag), `upstox`,
    `ctrader` — each refusing to construct without credentials.
48. Gate: the trait is exercised by paper; the other three refuse cleanly and are listed by
    `gfti doctor` as "declared · unconfigured", never as available.

**A-14 · Seats and fleet contracts (definitions, not models)**
49. `configs/seats.toml` — the 12 seats with role, trigger, produced artifact, and blocked-from.
50. `queue_worker.py` gains a handler registry; `noop` stays as the only enabled handler.
51. Fleet contract: `d3.cells` (A-11 G5) plus a spawn/kill contract the fleet-control seat will use.
52. Gate: each seat definition is loadable and validated; an unknown handler fails bounded.

**A-15 · Ops**
53. `/metrics` on every service (Prometheus text format); OTel traces for the tick→fill path.
54. Grafana (internal only) with the two required dashboards: G2 (ops) and G9 (token cost).
55. `gfti backup` / `gfti restore` for `pgdata` + `qdbdata` + `d0/` + `d5/`.
56. Gate: metrics present for every service; a restore into a clean stack reproduces row counts.

**A-16 · The exit gate**
57. On a clean checkout: `gfti install && gfti up && gfti status && gfti smoke` — twice, identical
    transcript, on tier A. Then the same on tier B in CI.
58. `gfti smoke` = one synthetic tick through rail → D2 → cell → E2 → E5 → E6 → e7 → D4 → status,
    with the kill switch engaged and released mid-run.

### Phase B — data (starts only after A-16 is green)

1. B1 backfill in the blueprint's order: benchmark bundle → India EOD → crypto tick → FX tick →
   GDELT → filings → power/weather → registries.
2. P1 BTC/USDC tick-by-tick already exists; extend to full depth + funding + liquidation.
3. P2 NIFTY — **owner blocker**: broker OAuth (Upstox candidate). Until then bhavcopy is EOD and
   must never be labelled live chain.
4. P3 Gold — **owner blocker**: freeze the identity (`histdata/XAU-USD/1d` vs `ctrader/XAUUSD/1m`
   vs `dukascopy/XAUUSD/tick`).
5. Kraken archive (~28 GB, 1.8 GB fetched) to close G-DATA.
6. B-GATE per domain: rows verified, gaps populated, blocked handled, PIT certified, provenance
   complete. Zero training before this passes.

### Phase C — train, find bugs, fix (last)

1. Cost model per venue: fees, spread, funding, impact — replacing the flat 8 bps.
2. Promotion ladder rungs 1→3 (E4-SIM, E4-BT vec, E4-BT event) then 5→6 (paper, shadow).
3. DSR certification with reference implementation + test vectors (currently a
   `metrics.DSR_CERTIFICATION_BLOCKER`).
4. Train the 73-agent fleet in tranches T1→T2→T3, one style per symbol.
5. Decay monitoring + retrain triggers; retirement per cell.
6. Bug loop: reproduce from D4/flight-recorder → fix → regression case → gate rerun.

---

## 5 · Owner blockers (cannot be closed by code)

| # | Blocker | Blocks |
|---|---|---|
| B1 | Upstox OAuth login + algo-tag decision (O9) | P2 NIFTY, stocks universe, 59 symbols |
| B2 | Gold venue/feed identity decision (D-4 in A1) | P3, FX Team 1/2 gold and WTI |
| B3 | NSE index membership vendor / survivorship-free list (O15) | stock universe honesty |
| B4 | cTrader production limits (O8, demo vs live) | FX Team 1/2 sizing |
| B5 | Hyperliquid FIU-IND/FEMA/tax position (O11) | any HL order path |
| B6 | DSR certification reference (no test vectors exist anywhere in the repo) | promotion beyond WF |
| B7 | FANOUT p99 budget on a 4-core box — **owner re-baseline decision, not a code defect** (see below) | G-RAIL stability |

B7 evidence gathered 2026-11-06 while closing A-8, because A-8 was the first change that could
plausibly have moved this pin and it had to be cleared or named:

- **D2 persistence is not the cause.** A controlled A/B/A/B (MARKET stream present vs deleted,
  alternating so load drift is not attributed to the store) gave captured `3.51 / 1.93 ms` and
  `1.98 ms`, uncaptured `1.93 ms` and `2.07 / 1.72 ms`. Uncapped runs crossed 2 ms too.
- **The codec is not the cause.** 3.36 µs/msg decode cannot produce a ~400 µs p50 or a 2 ms p99.
- **Capacity is the cause.** Same binaries: `1.85–1.98 ms` best-of-N at load1 ≤ 4; `2.08 / 2.09 /
  2.14 ms` (3/3 fail) immediately after `d1_gates.py ALL` (ten QuestDB restarts) at load1 4.95; and
  `1.93 ms` on attempt 1 with the three idle hot-path containers stopped. The floor on this host has
  drifted from the recorded 1.50–1.60 ms (2026-11-04) to ~1.85–1.95 ms — about 5% of headroom left
  under a 2.00 ms budget.
- The gate now **settles load (bounded 120 s) and runs a reported-but-unscored warm-up pass**, so it
  stops inheriting contention it did not create and stops scoring page-cache/governor ramp. The
  **2.00 ms budget itself is unchanged** — `docs/gates/STATUS.md` records that relaxing it is an
  owner decision, and that is where this now stands.

---

## 6 · Out of scope, by design

- **Row 1** (Terminal · News Portal · Media Hub · World Monitor) — Stage 7–10, after everything.
- **Latency parity off Linux** — Docker on macOS/Windows gives install parity only. The µs budgets
  (90 ns/row, FANOUT p99 ≤ 2 ms) are Linux-only claims forever.
- **Live trading** — no owner approval path is enabled, and the artifact ladder has not been
  climbed. `Mode` has no `Live` variant, deliberately.
- **Profitability** — the system can measure expectancy and refuse to promote. It cannot promise
  profit. The current candidate is DSR 0.4894 and is refused.

---

## 7 · How to read progress against this document

Every TODO item above gets a gate. A gate is green only when a real command printed its marker on
the tree being reported, per the rule already enforced by `python/gates/status.py`. Nine of the
items in §4 are currently `missing` in a way that makes the product un-runnable (P1–P8, A-16);
those are the only ones that matter until they are closed.
