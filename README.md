# Global financial trading intelligence

**One shared data foundation · three parallel engines · a private governed trading stack · a 12-seat LLM cockpit that runs the whole thing with the owner in the chat.**

This repository is the build home of the entire system. The complete plan set lives in
[`docs/plans/`](docs/plans/) (15 PDFs + markdown sources). This README is the **full,
in-depth plan with the complete visual diagrams** — the same 10 architecture pages of
[FINOS-TRADING-PLAN.pdf](docs/plans/FINOS-TRADING-PLAN.pdf), embedded here with the
full structures spelled out under each.

> Status: **W0a DONE · W0b (ledger + kill-cascade) DONE-at-gate level** — MEASURED:
> rail ingest contract 6/6 tests · **90 ns/row (p99 182 ns) ≈ 11.1 M rows/s/core** ·
> batched-flush publish 2.7–3.0× · D0 Parquet **289 ns/row** · ILP encode
> **639 ns/row** · Compose stack (Postgres 16 · QuestDB 7.4.2 · NATS 2.10) 3×
> healthy · five D1 store gates green (500k burst exact · determinism identical ·
> pre-warm 154 ms cold / 6.6 ms warm · restore reverts markers · 10× restart
> invariant) · **W0b**: D4 append-only enforced by DB triggers (real trigger text
> in evidence) · kill drill with fail-closed rc7 · Postgres restart ×10 ·
> batched signed appends **~0.4–0.5 ms/row** (vs 25.8 ms/row autocommit baseline)
> with 20,000/20,000 signatures verified · fan-out bench 4 cells exact-count,
> **worst p99 1.68 ms ≤ 2 ms budget** · out-of-band watchdog (fail-closed drills,
> live cascade detection in 140 ms) · **E-cell skeletons G7**: E1+E2+E5 p50
> 525 ns / p99 7.5 µs, fail-closed phase 4/4 refuse + 0 intents, open phase
> 4 allows → 4 signed fills, bad_sigs 0 · **approval flow G8**: L1/L2/L3
> ladder enforced (seat-overreach rc 8, double-decide rc 9, C8 quota 5
> grants/week then rc 7) — **W0b complete** (G1–G8). **B1 started**:
> real 9.2y capture (BTCUSDT 1d, 3,362 bars from 2017-08-17, key-free
> source, integrity PASS, dual-run determinism, D3 registry verified,
> training-lock enforced) — see `docs/gates/W0b.md`, `docs/gates/B1.md`.

> Labels used everywhere below: **ESTIMATE** = projected, measured at its named gate ·
> **MEASURED** = real recorded output · unlabelled = a locked decision from the sessions.

---

## Repository structure

Directory structure as it actually exists on disk, verified **2026-11-04** by walking the
tree — not copied from the plan. Every path exists; descriptions come from each module's
own docstring. `*` / `†` = local artifact, deliberately **not** tracked.

### Scale

| Measure | Value |
|---|---|
| Tracked files | **163** |
| Rust | 45 files · **28 `.rs`** · 3,589 lines |
| Python | 48 files · **40 `.py`** · 12,029 lines |
| SQL migrations | 6 files · 629 lines |
| Docs | 54 files (32 plan docs · 10 diagrams · 8 gate records · 1 baseline · 2 specs · 1 structure note) |
| Untracked data on disk | `d0/` 135 MB · `data/d0/` 1.8 GB · `d5/` 152 KB |

Two artifacts are deliberately **not** counted above: `rust/target/` (build output) and
`python/*/​.venv/` (per-package virtualenvs). Both are ignored and neither is part of the
design.

### Tree

```
global-financial-trading-intelligence/
│
├── README.md                         the plan, the ten diagrams, the honesty register
├── .gitignore                        what may never be committed (see "Ignored" below)
├── .gitattributes                    line-ending + binary handling
│
├── bootstrap/
│   └── install.sh                    one installer: cargo build → migrations → --verify gates
│                                     (--verify runs W1 and BOTH pilot-E2E arms: fixture + real P1)
│
├── rust/                             hot-path kernel — one cargo workspace, edition 2024, LTO release
│   ├── Cargo.toml                    workspace: 8 members · resolver 2 · release lto=true
│   ├── Cargo.lock
│   │
│   ├── rail/                         R1 ingest rail
│   │   ├── src/lib.rs        (191)   crate root, public surface
│   │   ├── src/main.rs        (98)   reads one row per stdin line → rail
│   │   ├── src/wire.rs       (234)   the wire protocol
│   │   ├── src/ilp.rs        (190)   ILP-TCP writer (QuestDB ingest)
│   │   └── src/bin/
│   │       ├── rail_sub.rs    (65)   subscriber: subject fan-out
│   │       ├── d1_ingest.rs   (52)   D1 ingest exerciser (production ILP path, synthetic rows)
│   │       ├── replay.rs      (48)   deterministic replay from captured rows
│   │       ├── bench.rs       (47)   throughput bench
│   │       ├── bench_ilp.rs   (51)   ILP path bench
│   │       └── bench_wire.rs  (81)   wire codec bench
│   │
│   ├── d0/                           cold store — columnar parquet
│   │   ├── src/lib.rs        (232)   stamped rows → parquet; sort-stable by ts_nanos
│   │   └── src/bin/d0_dump.rs (39)   CLI dumper
│   │
│   ├── ledger/                       D3 registry · D4 append-only ledger · kill cascade · recon
│   │   ├── src/lib.rs        (450)   registry, append-only enforcement, kill state machine
│   │   └── src/bin/                  seven binaries, one per operational verb:
│   │       ├── ledger_init.rs     (33)   apply schema / bootstrap
│   │       ├── ledger_fill.rs     (71)   write a signed fill (HMAC)
│   │       ├── ledger_kill.rs     (30)   engage | resolve the kill cascade
│   │       ├── ledger_check.rs    (35)   exit 0 = open · exit 7 = engaged/failsafe (fail-closed)
│   │       ├── ledger_recon.rs    (52)   reconcile positions against fills
│   │       ├── ledger_watchdog.rs (98)   out-of-band observer, --strict exits 7 on DB loss
│   │       └── ledger_appr.rs    (104)   approval flow (L1/L2/L3 ladder)
│   │
│   ├── engine/                       E3 decision engine — the cell spine
│   │   ├── src/lib.rs         (41)   re-exports the spine
│   │   └── src/bin/
│   │       ├── cell.rs       (222)   THE decision cell: E1→E2→E5→E6 per tick
│   │       └── e7.rs          (93)   E7 ledger process: order.* → signed D4 fills
│   │
│   ├── featurize/                    E1 — in-process pre-warmed state (ring-buffer SMAs), µs-class
│   │   └── src/lib.rs        (220)
│   │
│   ├── evaluate/                     E2 — frozen-artifact evaluator
│   │   ├── src/lib.rs        (231)
│   │   └── tests/artifact.json       signed artifact fixture
│   │
│   ├── govern/                       E5 — deterministic governor, sole Allow/Deny authority
│   │   └── src/lib.rs        (109)
│   │
│   ├── exec/                         E6 — execution state
│   │   ├── src/lib.rs        (341)   `enum Mode { Paper, Shadow }` — **live absent at type level**
│   │   └── tests/spine.rs    (131)   integration test
│   │
│   └── (each crate also carries Cargo.toml; several keep a .gitkeep from scaffold time)
│
├── python/                           the edge — off the decision path, contracts across the wall
│   ├── collect/                      data acquisition + its tests (5,138 lines)
│   │   ├── hyperliquid_daily.py    (508)  HL BTC-USD/1d perp candles (REST candleSnapshot)
│   │   ├── hl_trades_ws.py         (427)  HL trade-tick recorder — the ONLY route to genuine ticks
│   │   ├── kraken_full_trades.py   (547)  Kraken public full-trade archive → verify → convert
│   │   ├── nse_fo_bhavcopy.py      (376)  NSE F&O bhavcopy — NIFTY index-options history
│   │   ├── histdata_daily.py       (380)  HistData.com XAU/USD M1 + derived daily (GOLD fallback)
│   │   ├── binance_daily.py        (182)  Binance daily bars (B1 opener, free tier)
│   │   ├── derive_1m_from_trades.py(315)  ticks → 1m, then cross-checks the VENUE's own candle
│   │   ├── resample_1m_to_1d.py    (215)  1m → 1d projection (complete days kept, edges dropped)
│   │   ├── recover_trades_segments.py(159) salvage ticks after an ungraceful kill
│   │   ├── register_b1.py          (350)  collector run → D3 (manifest + gap calendar)
│   │   ├── pyproject.toml / uv.lock
│   │   └── test_*.py                9 files, 1,700 lines — one per collector
│   │
│   ├── gates/                        every gate runner (3,509 lines)
│   │   ├── status.py               (626)  **G-STATUS**: the 8 A0 gates → one verdict each
│   │   ├── w0b_gates.py            (543)  W0b: append-only · kill drill · RESTART ×10 · recon ·
│   │   │                                  FANOUT (2 ms p99 budget) · WATCHDOG · ENGINE · approval
│   │   ├── w1_gates.py             (491)  W1: DSR certification · crisis replay · cost cap
│   │   ├── pilot_e2e.py            (378)  G-PILOT-E2E: fixture arm (P1/P2/P3) + --real P1 arm
│   │   ├── w0c_seats.py            (432)  W0c: seat-role permissions (d3_seat_runtime)
│   │   ├── w0c_tasks.py            (410)  W0c: D3 durable seat-task queue
│   │   ├── b1_gates.py             (269)  B1: capture → verify → registry → B-GATE
│   │   ├── d1_gates.py             (247)  W0a pin 2: five D1 (QuestDB) store gates
│   │   ├── _env.py                  (71)  shared harness: scratch dirs, release dirs
│   │   ├── test_status.py           (97)  self-tests for the aggregator (5 cases)
│   │   └── .gitkeep
│   │
│   ├── train/                        research lane (nightly, off-peak)
│   │   ├── walk_forward.py         (341)  E4-WF walk-forward trainer (the W1 non-LLM lane)
│   │   ├── features.py             (537)  causal, source-aware resampling + indicators
│   │   ├── validation.py           (156)  calendar-day grouped combinatorial validation + sealed holdout
│   │   ├── metrics.py              (226)  cost-aware metrics; DSR disabled until a published reference
│   │   ├── alpha_search.py         (214)  preregistered, append-only attempt accounting
│   │   ├── colab_runner.py         (524)  optional Colab T4 execution lane
│   │   ├── __init__.py               (0)
│   │   ├── pyproject.toml / uv.lock
│   │   └── test_*.py                3 files, 971 lines
│   │
│   ├── seats/
│   │   ├── queue_worker.py         (337)  seat-side task worker (role d3_seat_runtime, typed exits)
│   │   └── .gitkeep
│   │
│   ├── research/                     RESERVED — empty (.gitkeep only)
│   └── tools/                        RESERVED — empty (.gitkeep only)
│
├── scripts/
│   ├── hl_capture.sh                 1m capture every 15 min: flock + --merge + derive 1d
│   └── hl_trades.sh                  tick recorder, 55-min sessions under flock
│
├── compose/
│   ├── docker-compose.yml            postgres · questdb · nats
│   ├── .env.example                  required local values (no secrets committed)
│   ├── README.md                     stack notes
│   └── sql/                          ordered, idempotent migrations
│       ├── 001_d3_d4_init.sql        D3 registry + D4 append-only ledger
│       ├── 002_w0b_approval_flow.sql L1/L2/L3 approval ladder
│       ├── 003_b1_data_plane.sql     B1 data plane
│       ├── 004_w0c_llm_tasks.sql     seat-task queue + roles
│       ├── 005_w1_artifacts.sql      W1 signed artifacts
│       └── 006_d3_intraday_metadata.sql  intraday identity (the 1m/1d split)
│
├── docs/
│   ├── decisions/
│   │   └── A0-DECISION-BASELINE.md   THE authoritative baseline: §2 OS tiers · §7 gates 1-8 ·
│   │                                 §8 phase order · §9 decision log
│   ├── gates/                        one record per gate, with measured output
│   │   ├── STATUS.md                 G-STATUS aggregate: current verdict per gate
│   │   ├── W0a.md  W0b.md  W0c.md    store · runtime pins · seat/task control plane
│   │   ├── B1.md                     data plane
│   │   ├── P1-DATA.md                Hyperliquid BTC-USDC: retention, merge fix, ticks, venue check
│   │   ├── P2-DATA.md                NIFTY options — blocked on broker credentials
│   │   └── PILOT-E2E.md              the end-to-end fixture + real-data arms
│   ├── plans/                        32 documents: 16 md + 15 pdf + 1 self-contained html
│   │   ├── FULL-SYSTEM-BLUEPRINT.md/.pdf      ← the pinned build doc (cited as "blueprint §N")
│   │   ├── FULL-SYSTEM-MASTER-PLAN.md/.pdf    overview
│   │   ├── FULL-SYSTEM-PLAN.md/.pdf
│   │   ├── FINOS-TRADING-PLAN.md/.pdf/.html   the visual edition (renders the 10 diagrams)
│   │   ├── ENGINE-DATABASE-PLAN.md/.pdf
│   │   ├── LLM-FLEET-BLUEPRINT.md/.pdf
│   │   ├── AGENT-FLEET-BLUEPRINT.md/.pdf
│   │   ├── AGENT-ECOSYSTEM-MASTER.md/.pdf
│   │   ├── AGENT-PRODUCTION-BUILD-PLAN.md/.pdf
│   │   ├── FIRST-BATCH-NON-LLM-AGENTS.md/.pdf
│   │   ├── DATA-GATHERING-PLAN-V3.md/.pdf
│   │   ├── DATA-PLAN-3SYMBOLS.md               (md only — no pdf)
│   │   ├── DATASET-TODO.md/.pdf
│   │   ├── FX-SCALPING-BUILD-PLAN.md/.pdf
│   │   ├── OPTIONS-BUYING-SCOPE.md/.pdf
│   │   └── QUANT-TRADING-ALPHA-EXPLAINED.md/.pdf
│   ├── img/                          10 PNGs, finos-p01..p10 — the architecture pages
│   └── superpowers/
│       ├── plans/2026-11-02-intraday-alpha-research.md
│       └── specs/2026-11-02-intraday-alpha-research-design.md
│
├── .github/workflows/ci.yml          three jobs: rust (fmt/clippy/test) · python
│                                     (train · collect · collect-lint · gates) · sql-idempotence
│
├── configs/                          RESERVED — empty (subject/schema config not yet authored)
│
├── d0/ †                             135 MB, 16 files — captured market data, hashed by D3
│   ├── hyperliquid/
│   │   ├── BTC-USDC/
│   │   │   ├── 1m.parquet            the P1 intraday identity
│   │   │   ├── 1d.parquet            VENUE-NATIVE full daily history (1,347 bars, 2023-02-26→)
│   │   │   ├── 1d-derived.parquet    DERIVED from 1m (complete days only, 2 bars so far)
│   │   │   ├── 1m-ticks-verified.parquet  bars whose maker ticks matched the venue
│   │   │   └── trades/               tick segments, JSONL.gz, one per hour (+ .corrupt backups
│   │   │                             kept from the 2026-11-03 corruption incident)
│   │   └── BTC-USD/
│   │       └── 1d.parquet            the LEGACY daily identity — never relabelled
│   ├── nse_fo/NIFTY-OPT/
│   │   └── 1d.parquet                133 MB — NIFTY option chain history
│   └── histdata/XAU-USD/
│       └── 1d.parquet                GOLD fallback series
│
├── d5/ †                             152 KB — 37 signed artifacts from the eval league
│
└── data/d0/ †                        1.8 GB — local scratch tree
    ├── binance/{BTCUSDT,ETHUSDT,SOLUSDT}/
    └── kraken/archive/
        └── Kraken_Trades_Full.zip.part00.partial    INCOMPLETE download
```

† = local artifact, deliberately untracked. Not a build output, not committed, and no
claim about its contents is ever made from having the bytes in git.

Crate dependency graph, the `.gitignore` rationale, and the "things that look missing but
aren't" notes are in [`docs/STRUCTURE.md`](docs/STRUCTURE.md).

---

## 0 · Run it on any OS (Windows · macOS · Linux)

The whole control-plane infra is **Docker-based by design** — the same commands work
everywhere there is [Docker Engine](https://docs.docker.com/engine/install/) or
[Docker Desktop](https://docs.docker.com/desktop/). No OS-specific steps, no bash
scripts, no `pip install` into system Python.

**Prerequisites (all three OSes):**

| Tool | Why |
|---|---|
| Docker Engine 24+ *or* Docker Desktop | Postgres 16 · QuestDB 7.4.2 · NATS 2.10 stack |
| [`uv`](https://docs.astral.sh/uv/getting-started/installation/) | Python runner + dependency manager (gates, collectors, seats) |
| [`rustup`/cargo](https://rustup.rs/) | the Rust rail (W0b components) — `cargo build --release` in `rust/` |

**Same commands on every OS:**

```bash
cd compose
cp .env.example .env              # dev-local placeholder password, edit if you like
docker compose up -d
docker compose ps                 # wait until all three are healthy

cd ../rust && cargo build --release && cd ..   # only the gates that use the rail

python3 python/gates/w0c_tasks.py     # or any gate: paths are resolved OS-portably
```

Portability notes:

- Gates write scratch artifacts under the OS temp directory (`gfti-gates/`,
  see `python/gates/_env.py`) — never a hardcoded `/tmp` path.
- Rust binary names (`ledger_init`, `d1_ingest`, `rail`, …) resolve as `*.exe`
  automatically on Windows (`ReleaseDir` in `_env.py`).
- `.gitattributes` pins LF line endings so a Windows checkout cannot corrupt
  SQL/TSV fixtures.
- Execution-venue note: cTrader Open API is Linux/macOS/Windows-native from Python.
  MT5 *automation* stays Windows-terminal-only (the MT5 *terminal* itself now installs
  on macOS/Linux under Wine, but the `MetaTrader5` Python package ships Windows-only
  wheels) — the plan's venue decision (O2 = cTrader) is what keeps this repo OS-portable.


---

## 1 · What the system is (the one-page picture)

![Master map — three rows, one rail](docs/img/finos-p01.png)

**Raw 1 — public content** (built LAST in the whole project): Terminal · News portal ·
Media hub. It *reads* the engines through the rights firewall and never feeds anything
back into trading.

**Row 2 — shared intelligence foundation**: the Financial engine + News engine + World
engine and the shared machinery (registry, schemas, provenance, quality, bus, APIs,
rights). Rule: **collect once, reuse many**.

**Row 3 — private**: **FinOS** (the trading stack — the core of this repo) ·
**Cockpit** (the LLM layer: 12 seats, memory, approvals, chat with the owner) ·
**World Monitor** (last of all).

| Workforce | Size | What it does |
|---|---|---|
| Non-LLM fleet — *the hands* | **75 research brains** → **480–545 executor cells** (ceiling), **≤75 active at once** | deterministic trading execution; every artifact is bit-for-bit replayable |
| LLM cockpit — *the management layer* | **12 seats** | organize · score · monitor · control · train · chat — decide-and-explain, **never the hot path** |
| Gates | the promotion ladder | DSR ≥ 0.95 + CPCV + crisis replay + 10³-candidates/style cap → owner approves ≤5/week → live |

**Roster note:** the owner's "73" was the old symbols×1-style count. The locked roster
is **75 research brains** (NIFTY+SENSEX+optionable weight-stocks = ≤59 · FX Team 1 = 11 ·
FX Team 2 = 2 · crypto = 3).

### Non-negotiable invariants (printed on every page of the build)

| # | Invariant |
|---|---|
| R1 | collect once / reuse many |
| R2 | rights firewall at the Row-1 boundary only |
| **R3** | **intelligence (LLM) never in fast paths** |
| R4 | point-in-time + survivorship discipline |
| R5 | provenance everywhere |
| R6 | versioned contracts |
| R7 | train → eval → paper → approve → deploy |
| R8 | HFT honesty: venue RTT dominates; internal order budget **<5 ms** |
| R9–R14 | regulatory set (India algo tagging, FIU-IND/FEMA, ≥5-yr records, …) |
| G1–G6 | guards: no self-editing artifacts · no search code live · research can't order · every trial logged · one style/symbol · Mode R/T separate processes |
| L0–L4 | kill cascade cell → symbol → team → market → global + **out-of-band kill that works without the app** |

---

## 2 · The data pipeline (in motion → at rest → insight → action)

![Data pipeline](docs/img/finos-p02.png)

| Band | Exactly what lives there |
|---|---|
| **Data in motion** | venue feeds (Upstox WS+SSE for NSE/BSE · cTrader Open API for FX ECN · Hyperliquid perps) · news + world collectors (B1 wave; T3 world data pinned to research need, never blanket) · filings/reference/cyclical fundamentals (PIT as-reported — O12 open) — everything crosses the **R1 rail**: validate · as-of stamp · rights-tag |
| **The bus** | **NATS JetStream (D2)** — subjects `market.*` `signal.*` `order.*` `risk.*` `ledger.*` `seat.*` · queue-groups = load balancing · durable consumers = replay · partitioned per-key ordering · fan-out **≤ ~2 ms P99** (ESTIMATE → measured W0b) |
| **Data at rest** | **D0** Parquet lake (NVMe + offsite object; 16-TB target accepted) · **D1** **QuestDB** warm window (§19 final; ClickHouse fallback if W0a fails twice; TimescaleDB rejected — `tsl/` license wall) · **D3** Postgres reference/registry (+ `llm_tasks`) · **D4** Postgres **append-only** ledger (fills · P&L · slippage · G4 trials · ≥5 yr) |
| **Data at insight** | **D6** DuckDB scans (one process, columnar only) · **E4** gate stack · scoring + research seats (citations required; **10³ candidates/style cap**) |
| **Data at action** | **E5** governor (zero-ML) · **E6** execution (PAPER/SHADOW/LIVE) · **E7** ledger → D4 · cockpit approvals |

Store rules: the **hot path never queries a store per tick** · cold in D0, warm in D1,
hot in RAM, truth in D3/D4/D5 · a candidate's label triple-barrier net-of-costs comes
from the *signal log*, not fills.

---

## 3 · The runtime: three layers, one wall

![Runtime layers](docs/img/finos-p03.png)

| Layer | Members | Position |
|---|---|---|
| **Rust kernel** (edition 2024) | `rail/` R1 · `featurize/` E1 (pre-warmed RAM at boot) · `evaluate/` E2 (frozen-artifact evaluator, compiled/ONNX) · `govern/` E5 · `exec/` E6 + adapters · `ledger/` E7 | **hot path** — no framework, no store query per tick |
| **Python edge** | `research/` (PySR · MAPIE · arch · sklearn · PyTorch · SB3) · `gates/` (E4: CPCV · DSR · crisis replay · E4-SIM NumPy/Numba 10⁴ worlds) · `tools/` | offline nightly |
| **LLM seat layer** | the 12 seats + brigade loop-guard ports + ruflo DAG runner | approvals, PRs, findings — through the L0–L3 bridge **only** |

Crossings between layers = the **five versioned contracts**: storage · research ·
artifact · execution · audit. Monorepo (`rust/` `python/` `configs/` `compose/`), a
wall, not a shared runtime.

**Adopt vs build**

| | Items |
|---|---|
| **Build** (no substitute) | R1 rail · E1 · E2 · E5 · E6 adapters · E7 + cost model · D5/G4 · PIT replay · rights firewall · cockpit layer |
| **Adopt** (license-checked) | Parquet · QuestDB · NATS · Postgres · DuckDB · PySR · MAPIE · arch · sklearn/PyTorch/SB3 · Nautilus behind the E6 interface · Graphify as local tool |
| **Port patterns** (13-repo deep-read) | brigade loop-guards/approval-bridge/cron · ruflo task DAG · agency personas · day-trader deterministic checks · quant-mind citation schema · Vibe-Trading anti-fabrication · timecopilot stub-gate |
| **Rejected on record** | AI-Trader (no LICENSE) · nixtla SDK (cloud-locked; free models live in statsforecast/mlforecast) · laya-mlx runtime (darwin/arm64 only) · OpenStock code (AGPL — ideas only) |

---

## 4 · The execution path (latency bands)

![Execution path with latency bands](docs/img/finos-p04.png)

| Band | Content | Budget |
|---|---|---|
| **HOT** | E5 risk **first** (pre-trade size/limits/spread/news ladder) → E1 pre-warmed features → E2 frozen-artifact evaluation → daily circuit kill | decision **<1 ms** · R1→cell fan-out **≤ ~2 ms P99** (W0b) · order path E1→E5→E6→wire **<5 ms internal** — **venue RTT dominates (R8)** |
| **WARM** | E6 order state machine · lifecycle · replay(es) · position reconciler (**reconcile-before-orders** invariant) · D4 position ledger | ms-class |
| **COLD** | G4 research + strategy library · E3 training (Colab T4 verified lane) · D5 feature store + model registry | seconds–minutes, off-peak |

| Venue | RTT | Note |
|---|---|---|
| Upstox | ~45 ms | **vendor-claim, labeled** · algo-tag digit pending (O9) |
| cTrader ECN | GA-routed | prod limits O8 (500/min is demo) · NY4 lane planning W3 |
| Hyperliquid | ~600–700 ms public API | **MEASURED** public figure · node option W4–W5 |

---

## 5 · The wire — five transports, nothing else

![Streaming platform](docs/img/finos-p05.png)

| # | Transport | Job |
|---|---|---|
| 1 | **NATS subjects** | the live wire — µs–ms fan-out · load-balance · durable replay |
| 2 | Files | D0/D5 — single-writer discipline |
| 3 | SQL | D1/D3/D4 concurrent servers (headroom = G1 bench) |
| 4 | Embedded DuckDB | single D6 process — never critical path |
| 5 | In-proc calls | E1/E4 workers — µs-class inside one kernel process |

Plus: schema registry = the five contracts (enforced at publish) · security layer =
rights firewall + no secrets in configs + append-only DB roles · observability =
per-hop metrics + flight-recorder traces + G2 dashboards + G9 token burn · the
cockpit L0–L3 bridge is the API-gateway analog · **proton** JetStream streaming-MVs =
W2 trial layer on top (own gates), **not** a D1 change.

---

## 6 · Engines E1–E7 and the candidate ladder

![Engine stack and candidate ladder](docs/img/finos-p06.png)

| Engine | One line |
|---|---|
| E1 featurize | bars · ticks · Greeks · indicators · microstructure — Rust, pre-warmed at boot |
| E2 evaluate | frozen-artifact evaluator every tick |
| E3 train | nightly, Colab T4 verified lane · every run G4-numbered |
| **E4 gate** | E4-SIM (10⁴ deterministic worlds) · E4-BT (vectorized → event-driven w/ fill model) · E4-WF (CPCV purge+embargo, day-grouped folds, untouched holdout) · **DSR ≥ 0.95** · crisis replay |
| E5 govern | stops · size caps · news ladder · kill-switch — **zero ML** |
| E6 execute | PAPER · SHADOW (zero order authority, physically) · LIVE adapters — the only engine that ever sends an order |
| E7 ledger | fills vs expected · slippage curves → D4 |

**A candidate's life (same signed artifact, same E1 code at every rung — bit-for-bit):**
E4-SIM (minutes) → E4-BT vectorized (seconds) → E4-BT event (fill model) → E4-WF
(CPCV + DSR + crisis) → **PAPER** (days) → **SHADOW** (3–6 months) → owner gate
(≤5 approvals/week) → **D5 signed → LIVE** → E7 decay watch → monitor / retrain /
retire (per-cell, owner-approved, G4 lineage).

---

## 7 · The workforces — and the ten operating loops

![Workforces and operating loops](docs/img/finos-p07.png)

**The hands (deterministic fleet)**

| Market | Venue | The work | Exits/guards |
|---|---|---|---|
| Stocks ≤59 | Upstox | options **BUYING only**, same-day | rule ladder 1–6 + kill; no short gamma |
| FX Team 1 (11) | cTrader ECN | 3 styles/pair (session · carry · news); regime router picks ≤1 | cascade-flatten · weekend-gap · funding-window · kill |
| FX Team 2 (2) | cTrader **separate account** | pure scalping push-mode (needs tick+depth) | session-closed flat |
| Crypto (3) | Hyperliquid perps | funding · liquidation · weekend · PAIR stat-arb | liquidity-gate exit · kill |

**The brain's management layer (12 seats)**: build · review · cybersec ·
data-QA · news · research · scoring · ops · fleet-control · training · compliance ·
cockpit/comm — findings and PRs only; **no market writes outside L0–L3, no
frozen-artifact edits without E4, no orders from research**.

**Ten operating loops** bind fleet ⇄ seats: build · fix-bug · promote · research ·
news · data-QA · ops/kill · train · monitor · self-learn. Nothing a seat says becomes
live without a gate.

**Swarms (both kinds)**: fleet swarm = E4-SIM 10⁴ worlds · LLM crew swarm = ruflo-port
DAG with `crew.yaml` (roster · depends_on · tool whitelist · budget cap · approval lid,
CI-linted) · cross-cell swarms test any new style against existing exposure before a
merge lands.

---

## 8 · Memory — every agent, and the control plane

![Memory and control plane](docs/img/finos-p08.png)

| Memory type | Fleet (75 brains / 480–545 hands) | LLM seats (12) |
|---|---|---|
| working | cell process state (tick/feature/position/DD) | task-lease payload in `llm_tasks` |
| episodic | E7 ledger + G4 trial log + shadow history | D4 audit rows (tokens · result · artifacts) |
| semantic | **R3 registry**: frozen artifacts · calendars · limits · ban status · regime→style map | D5 artifacts + Graphify knowledge graph + vault notes |
| procedural | the code + runbooks (git PR only) | skill files (PR + review) |
| owner rules | — | vault note, cockpit-seat-writes-only |

Write authority is **enforced at the DB-role level** (no seat can UPDATE D4; D5 writes
only via the gated artifact path) · retention: D4 ≥5 yr · G4 forever · `llm_tasks`
trimmed after digest · Obsidian note types (decision · incident · runbook ·
research-digest · training-record) · recall substrate decided by the **W0c measured
shootout**.

**Control plane:** owner tiers **L0** chat answers → **L1** sandbox checks →
**L2** sign-offs (news ladder, budgets, triage) → **L3** live decisions (promote, kill
re-arm, deploy) as approval cards in chat · kill cascade **L0–L4** + out-of-band
watchdog + venue-side max-loss order + one-action mobile kill — all work without the
app · monthly chaos drills (10× restart · feed-stale · venue-down · restore).

**Cockpit monitor surfaces (G2):** cell health · portfolio · market state · data
(gap calendar + 16-TB tracker) · LLM layer (token burn/seat/day) · research (trial
counts, DSR status, coverage heat) · ops (incident queue, drill results, backup
freshness). Chat: every reply labeled ESTIMATE/MEASURED; every quote carries its
artifact id; weekly digest auto-writes to the vault.

---

## 9 · The build order — waves with exit pins

![Build waves](docs/img/finos-p09.png)

| Wave | Builds | Exit pin (real command output, filed) |
|---|---|---|
| **W0a** | monorepo skeleton · Compose (Postgres · QuestDB · NATS) · R1 rail · 5 D1 gates (burst · determinism · pre-warm · restore · 10× restart) | replay twice bit-identical · bench JSON |
| **B1** | collectors market/news/world · 6–10 y backfill per tiers · in-DB gaps table | **B-GATE verified before ANY agent trains** |
| **W0b** | D4/D5 roles+signing · E1/E2/E5/E7 skeletons · L0–L4 + watchdog · fan-out bench | restart ×10 · fan-out p99 file · kill drill log |
| **W0c** | LLM control plane · `llm_tasks` · Graphify+Obsidian · cockpit v1 | cold start · 10× Compose restart · substrate numbers |
| **W1** | D6 cache · PySR/MAPIE/arch · E4-SIM/BT/WF · E6-PAPER | one full style through the ladder to a signed D5 artifact |
| **W2** | cost model (O8) · cTrader adapter · proton MV trial · seats live | paper fills + E7↔broker reconcile |
| **W3** | first cells to shadow · NY4 lane | shadow P&L/IC files — no live claims |
| **W4–W5** | tick+depth → microstructure · Hyperliquid node · crypto cells · ML lane | microstructure replay · colo decision |
| **Stage 6** | cockpit GA (O16 resolved) | L3 flow demo · weekly digest in vault |
| **Stages 7–10** | Terminal/Portal/Media (rights-firewalled) · World Monitor LAST · HFT only if measured | Row-1 never touches the hot path |

> **These are capability waves, not the order of work.** What must be true before the next kind of
> work starts — one installable software first, then tick data, then training — is fixed in
> [`docs/decisions/A1-FULL-SOFTWARE-ROADMAP.md`](docs/decisions/A1-FULL-SOFTWARE-ROADMAP.md).
> W0c/W2/W3 do not start before Phase A closes.

**Week-1 concrete instructions:** create the skeleton · Compose with healthchecks ·
R1 minimal loop (validate → stamp → `market.*` → D0 parquet) · replay-harness CLI ·
G1 bench stub · import the plans into `docs/` · create vault note types · Graphify
baseline · open the W0a gate document.

---

## 10 · Honesty register — the fine print this build runs on

![Honesty register](docs/img/finos-p10.png)

- **Vendor/author claims stay labeled, never repeated as fact:** Upstox ~45 ms RTT
  (vendor-claim) · "80% token cut"/"71.5× fewer tokens" (author-reported, unreproducible
  by the source's own README) → replaced by **our own W0c fit-test** · Graphify Cloud
  upsell rejected.
- **Measured by us, at each gate:** W0a's 5 store gates · W0b fan-out + restart ×10 ·
  W0c recall deltas + substrate shootout + cockpit cold start · W1 DSR/crisis/cap ·
  W2 ingest parity.
- **Open decisions carried to their waves:** O3 F&O granularity · O4 off-index names ·
  O5 first style · O6 FX-scalping add · O7 hedge executor · O8 cTrader prod limits ·
  O9 Upstox algo-tag · O10 order-balance · O11 HL FIU-IND/FEMA/tax · O12 PIT
  fundamentals · O13 ToS checklist · **O14** model routing (W0c) · **O15** NSE
  membership vendor (W2) · **O16** cockpit runtime (W0c).
- This plan set contains **zero unverifiable performance claims** — anything
  vendor- or author-reported keeps its label until we measure it.
- **Where the current verdicts live:** `python/gates/status.py` runs the eight A0 §7
  gates and reports `ok` / `FAIL` / `blocked` / `not-run` per gate; the evidence table,
  the FANOUT latency measurements and every owner-blocked item are recorded in
  [`docs/gates/STATUS.md`](docs/gates/STATUS.md). A gate that cannot run is printed as
  `blocked`, never as green.

---

*No secrets in this repository (scanned at seed time). License: to be decided by the
owner — unlicensed means all rights reserved.*
