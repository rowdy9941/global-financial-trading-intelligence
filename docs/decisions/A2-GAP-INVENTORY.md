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

**A-1 · Runtime residency** *(unblocks everything else)*
1. `cell`: replace `while ticks < ticks_want` with a run-forever loop; `CELL_TICKS=0` must mean
   "unbounded", not "exit immediately"; add a `--once` flag for gates so existing fixtures keep
   working unchanged.
2. `e7`: same, and **delete the stdin `"GO"` handshake** — a service must not wait for a gate to
   say go. Move the handshake into the gate's own wait loop.
3. SIGTERM/SIGINT handlers in `rail`, `cell`, `e7`: stop reading, drain, flush, exit 0.
4. `wire.rs`: `set_read_timeout` (or a poll loop) so a blocked read can observe the shutdown flag.
5. One JSON heartbeat line per service per N seconds: `{"svc","uptime_s","ticks","decisions","fills","kill","version"}`.
6. Gate: `start → wait for heartbeat → SIGTERM → exit 0` for all three, repeated 3×.

**A-2 · Runtime profile in Compose**
7. Add `rail`, `cell`, `e7` as Compose services on a `runtime` profile with
   `restart: unless-stopped`, `depends_on: {condition: service_healthy}`, CPU pinning per the
   blueprint's council fix A2.
8. Add `restart: unless-stopped` to postgres/questdb/nats.
9. Gate: `docker kill` each service → it returns healthy within N seconds, recorded.

**A-3 · `gfti` CLI** (one entry point, shells that already exist stay as implementation details)
10. `gfti install` (wraps the installer), `up`, `down`, `status`, `doctor`, `logs`, `smoke`.
11. `gfti doctor` = prerequisite + port + tier probe (see A-6).
12. Gate: every action in the blueprint's workflow list is reachable from `gfti` alone.

**A-4 · One config file**
13. `configs/gfti.toml` — services, ports, paths, DB DSNs, engine env, subject prefix, log level,
    tier. `.env` remains only for secrets and is generated/checked by `gfti doctor`.
14. Every binary reads its env from the same generated `.env`; no per-binary ad-hoc defaults that
    disagree with the file.
15. Gate: `gfti doctor` prints the effective config with secrets redacted; changing one value in
    the file changes behaviour of ≥2 services.

**A-5 · Health view**
16. `gfti status` — per service: up/down, restart count, last heartbeat age, last fill, kill state,
    queue depth, D1 write rate, NATS stream depth.
17. Gate: stop `cell` with `docker pause` → `gfti status` goes red within one interval; resume → green.

**A-6 · Cross-OS install**
18. Implement the `--tier` probe A0 §2 already promises: A (Linux native) / B (macOS) / C (Windows
    WSL2). Refuse loudly below the minimum, with the exact missing prerequisite.
19. POSIX `sh` installer + `gfti.ps1` PowerShell shim; no bash-isms in the POSIX path.
20. macOS runner in CI as tier B (clean install + `gfti doctor` + engine unit tests; **not** the
    latency gates).
21. Windows tier C documented as WSL2; CI on `windows-latest` limited to the CLI's non-Docker paths.
22. Gate: CI green on ≥2 OS families, and the clean-install-twice rule from A0 §2 runs on tier A.

**A-7 · Make the rail the only writer (fixes I1 / R1)**
23. Collectors hand rows to the rail instead of writing the lake: either the rail gains a
    `--ingest-file` mode, or the Python collectors publish to a local socket the rail owns.
24. The rail then writes D0 (via `d0`), D1 (via `ilp`), D2 (JetStream) and D3 provenance — one
    writer, one lineage stamp, per the storage contract.
25. Gate: every new dataset in `d3.datasets` has a rail-written provenance row; a direct
    collector→Parquet write is refused/flagged by `gfti doctor`.
26. This is the item that turns invariant R1 from a comment into a property. It is Phase A, not B,
    because "everything wired" is exactly this.

**A-8 · Make D2 a real store**
27. Declare JetStream streams for `market.*`, `signal.*`, `order.*`, `risk.*`, `ledger.*`, `seat.*`
    with retention, and a durable consumer per engine.
28. Publish `signal.*` (E2 output), `risk.*` (E5 verdicts), `ledger.*` (E7 fills), `seat.*` (seat
    events) — today only `market.spot.*` and `order.spot.*` exist.
29. Version every payload (invariant R6): `{"v":1, ...}`, reject unknown versions.
30. Gate: stream count > 0, replay a stream and get identical decisions.

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
| B7 | FANOUT p99 budget on a 4-core box (1.52–2.50 ms vs 2.00 ms) — needs a re-baseline decision | G-RAIL stability |

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
