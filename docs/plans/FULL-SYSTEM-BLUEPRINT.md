# FULL-SYSTEM-BLUEPRINT — complete build document (Session 46, 2026-10-29)

> The **complete, self-contained** edition of the system plan: structure · architecture ·
> workflows · blueprint · build instructions for every part — data gathering, databases,
> engines, non-LLM & LLM agents, trading workflows, research→backtest→alpha→paper→live,
> training/self-learning/self-upgrade, cell improvement, cross-cell testing & merging,
> swarm agents, comms, memory of every agent + memory management, chat with us,
> monitor/control, performance/accuracy/latency/error/bug/issue management — with
> pinpoint instructions to build, as pdf/md/html.
> Companion (executive overview): `FULL-SYSTEM-MASTER-PLAN.md`. Decisions trace to the
> four specialist docs and Sessions 1–45 in `planing conva/PLANNING-CONVERSATION-LOG.md`.
> Labels: ESTIMATE = projected/to-measure · MEASURED = on record · everything else = a
> locked decision from a source session.

---

## §1 · What we are building (one screen)

A private, from-scratch global intelligence ecosystem with a dedicated trading stack:

| Row | Contains | Rule |
|---|---|---|
| Row 1 — public | Terminal · News portal · Media hub (LAST) | rights firewall; consumes engines only; never feeds back |
| Row 2 — foundation | Financial · News · World engines + shared machinery (registry, schemas, provenance, quality, bus, APIs, rights) | collect once, reuse many |
| Row 3 — private | **FinOS (trading stack — this plan's core)** · Cockpit (LLM agents, memory, approvals) · World Monitor (LAST) | intelligence never in fast paths |

**Invariants (non-negotiable):** R1 collect-once · R2 rights at the boundary · R3 LLM
never in hot paths · R4 PIT/survivorship discipline · R5 provenance everywhere · R6
versioned contracts · R7 train→eval→paper→approve→deploy · R8 HFT honesty · R9–R14
regulatory set · guards G1–G6 · kill cascade L0–L4 with out-of-band kill.

## §2 · Complete structure

### 2.1 Monorepo layout (design target — created at W0a kickoff)

```
mani/
  rust/                  # hot-path kernel (Rust, edition 2024)
    rail/                # R1: validate · as-of · rights-tag · fan-out
    featurize/           # E1: in-process, pre-warmed state
    evaluate/            # E2: loads D5 artifacts, compiled formulas/ONNX
    govern/              # E5: deterministic rules
    exec/                # E6: scheduler, paper/shadow/live modes, adapters
    ledger/              # E7: append-only writer
  python/
    research/            # Mode R stack (PySR, MAPIE, arch, qlib as libs)
    gates/               # E4: CPCV · DSR · crisis replay · swarm harness
    seats/               # LLM seat runtimes (build/review/…/cockpit)
    tools/               # ops scripts, calendar, reconcile
  configs/               # subjects, schemas, rights classes, calendars
  compose/               # container stack (stores, bus, cockpit)
  vault/                 # -> gfi Obsidian vault (docs, digests, decisions)
  graphify-out/          # code knowledge graph (auto, --update)
  docs/                  # the five specialist plans + this blueprint
```

Language boundary: Rust = R1/E1/E2/E5/E6/E7 (kernel); Python = research/gates/seats
(edge). Contracts across the boundary (§3.3); no framework in the hot path.

### 2.2 Service inventory (Compose wraps; W0a)

Postgres (D3+D4, one instance two schemas) · QuestDB (D1) · NATS JetStream (D2) ·
rail process (R1) · cockpit chat server · Opensearch-free; plus build workers (Graphify
`--update`, seat runs) — **rail is also able to run bare-metal outside Compose** for the
tick path; Compose defines a cpu-pinned deployment (council fix A2).

## §3 · Full architecture

### 3.1 Store ladder (every store, one job)

| ID | Tech | Holds | Writers | Readers | Class |
|---|---|---|---|---|---|
| D0 | Parquet NVMe + object offsite | years of ticks/bars/filings/news/world | R1 (+collectors in B1) | E4 replays, D6 | bulk |
| D1 | **QuestDB** (final; CH fallback) | live window days–weeks | R1 only | E1 pre-warm, dashboards, PIT replay | ms |
| D2 | NATS JetStream | every live event, as-of stamped | R1 | Mode T cells, seats | µs–ms fan-out |
| D3 | Postgres | registry·schemas·rights·symbols·calendars·limits·`llm_tasks` | our services | everyone | ms |
| D4 | Postgres **append-only** | fills·P&L·slippage·G4 trials·audit·CA ≥5 yr | E7, G4, seats-audit | research labels, ops | ms async |
| D5 | fs + PG index | frozen artifacts (feature-hash+schema-signed), scores, citing-artifacts | gates+owner | E2 at boot | ms boot |
| D6 | DuckDB | vectorized research scans | one process | Mode R | s–min |
| — | graphify-out/graph.json | code+docs knowledge graph | Graphify --update | seats (recall) | ms |

Design rules: hot path NEVER queries a store per tick (rule 2) · research reads
columnar only (rule 3) · D4/D5 append-only/signed at DB-role level (rule 4) · cold in
D0, warm in D1, hot in RAM, truth in D3/D4/D5.

### 3.2 Engine stack (every job, one engine)

| Engine | What | Where |
|---|---|---|
| E1 | features: bars·ticks·Greeks·indicators·microstructure·pattern flags (Rust, pre-warmed) | hot |
| E2 | frozen-artifact evaluator each tick (Rust+compiled) | hot |
| E3 | training orchestrator (SB3/PyTorch wrapper, nightly offline, Colab T4 lane) | offline |
| E4 | THE gate: E4-SIM swarm (10⁴ worlds) · E4-BT vectorized+event-driven(fill model) · E4-WF walk-forward+CPCV · DSR≥0.95 · crisis replay | offline→ |
| E5 | governor: stops·size caps·news ladder·kill-switch·ban (zero ML) | hot |
| E6 | execution: PAPER · SHADOW (zero order authority) · LIVE (adapters Upstox/cTrader/Hyperliquid; Nautilus under interface, swap-in) | hot |
| E7 | ledger: fills vs expected·slippage·per-style cost curves→D4 | async |

Ladder (a candidate's life, same signed artifact at every rung, same E1 code —
bit-for-bit): E4-SIM (minutes) → E4-BT vectorized (seconds) → E4-BT event (fills) →
E4-WF CPCV+DSR → E6-PAPER (days) → E6-SHADOW (3–6 mo) → owner (≤5/week) → E6-LIVE →
E7 → decay monitor → Mode R.

### 3.3 Adopt-vs-build + five interfaces

**Build (no OSS substitute):** R1 rail · E1 · E2 · E3 wrapper · E4 gate · E5 · E6 ·
E7+cost model+CA exporter · D5/G4 · PIT replay · rights firewall.
**Adopt:** Parquet · QuestDB · NATS · Postgres · DuckDB · PySR · MAPIE · arch ·
sklearn/PyTorch/SB3 · NumPy/Numba · qlib (pin; W1) · hftbacktest (fill-model checklist;
crypto-direct only) · Nautilus (LGPL fallback behind the interface) · + (from the 13
repos: bridge guard/approval/cron from brigade, swarm DAG from ruflo, personas,
day-trader checks, quant-mind schema, Vibe anti-fabrication, timecopilot gate pattern,
Graphify) — full map in `LLM-FLEET-BLUEPRINT.md` §3.1.

**Contracts:** 1 storage (rail-only writes) · 2 research (libs output candidates,
never artifacts) · 3 artifact (D5 signs → E2 compiles) · 4 execution (E6 only order
path) · 5 audit (everything → D4/G4).

## §4 · Data gathering ("grathing") pipeline

| Element | Plan |
|---|---|
| Priority | **data first → agents second → history grows third** (S42 owner order) |
| Tiers | T1 full tick+depth = tradable set (0.1–0.3 TB/yr ESTIMATE) · T2 world/news daily/1-min+reference (50–300 GB/yr) · T3 world pin-to-pin = per-research-need on demand, never blanket |
| Catalog | master TODO 1,114 rows / 18 volumes (S43) drives collectors; every row has license class + verify state |
| Registry | D3 source-registry seeded from the TODO: source_url, fetch time, parser version, rights class (R5/rule) |
| B1 gate | pull 6–10y of market+news+world → B-GATE: domain backfill verified + gaps table open before ANY agent trains |
| Blocked sources | rows keep verify/quote actions; explicitly marked in the gaps table, never silently skipped |
| Free/paid split | free verified bundle first; paid MVP rows collide at O12 (PIT fundamentals) and O15 (NSE membership) |
| Storage target | 16 TB accepted (S43); D0 footprint 4–16 TB range; object offsite mandatory before 100s-of-GB backfills |

## §5 · Trading workflows (per market, locked fleet §2–§5)

| Market | Venue | Works like | Exits/guards |
|---|---|---|---|
| Stocks ≤59 (login + optionable weight names) | Upstox | options **BUYING only**, same-day: E5 spread check → news ladder → 0DTE only long-gamma rule D4 | rule ladder 1–6 + kill; no short gamma |
| FX Team 1 (7 pairs + 4 commodities) | cTrader ECN | 3 styles/pair (session·carry·news), regime router picks ≤1 | cascade-flatten · weekend-gap guard · funding-window print · kill |
| FX Team 2 (XAU/USD + WTI) | cTrader, **separate account** | pure scalping push-mode (requires tick+depth microstructure food = E1 block) | session-closed flat; every-tick stop |
| Crypto (BTC/ETH/HYPE) | Hyperliquid perps | 4 styles: funding · liquidation · weekend · PAIR stat-arb | funding-window · 5-liquidity-gate exit (no bid → underlying hedge close-only) · kill |

Common: E2 evaluates frozen artifact each tick → E5 governs size/stops → E6 routes
(validated, idempotent client IDs) → fill async to E7 ledger → D4 · reconcile-before-
orders on restart (invariant) · full replay of any decision on demand.

## §6 · Research → alpha workflow (gaps & alpha finding — the machine that finds trades)

1. **COLLECT** (R1-stamped) → **LABEL** triple-barrier, net-of-costs, from the **signal
   log** (all signals, taken or not — not just fills).
2. **FIND ALPHA** in Mode R (offline swarm): GP formula search (PySR) · ML discovery
   (E3) · meta-labeling (López de Prado pattern; single-source evidence — adopt
   cautiously C14) · factor/parameter sweeps → screened by E4-SIM (10⁴ worlds).
3. **GAP FINDING 1 (research gaps):** G4 coverage maps = swarm survival fractions show
   untested parameter regions → the next search batch targets those cells.
4. **GAP FINDING 2 (data gaps):** fleet §9.1 register + coverage per symbol/time →
   collector tasks, in-DB gap calendar; untested data shapes are tickets, not guesses.
5. **GATE:** E4-BT (vectorized → event-driven with fill model per hftbacktest checklist)
   → E4-WF CPCV (purge = label horizon + embargo; folds day-grouped across symbols;
   untouched final holdout) → **DSR ≥ 0.95 vs decorrelated trials (G4)** → crisis
   replay (EOD index-option history) → net-of-cost. Multiple-testing honesty: 10³
   candidates/style cap; failure = scope does not trade.
6. **PAPER** (E6-PAPER, real path sim fills) → **SHADOW** (3–6 mo, zero order
   authority) → **owner approves ≤5/week** → D5 signed → live on next boot.
7. **MONITOR:** rolling IC · PSI/edge decay (net-IC vs frozen) → retrain/retire.

Scores from LLM seats ride this ladder identically: no score is trusted before the **E2
edge-lift gate** shows it lifts measured edge in replay repeatedly (S44/§4).

## §7 · Training · self-learning · self-upgrade · cell improvement · cross-cell

| Mechanism | How |
|---|---|
| Training | E3 nightly on Colab T4 (verified lane) or local; every run G4-numbered; nothing enters Mode T without §6 gates |
| Self-learning | weekly replay-test batch: every still-trusted artifact re-scored on last windows; decay list → research tickets; retraining triggered by **edge decay**, never raw PSI (C9) |
| Self-upgrade (ceiling) | "upgrade" = new frozen artifacts through the full gate. NEVER: live code rewrite (G1), order placement from research (G3), LLM in R1/R2 (R3), style enable without E4 (R7/R-full r7) |
| Cell improvement | shadow feedback + E7 slippage curves tune exits/cost model via normal PRs (LLM build seat) — same gates, no live bypass |
| Cross-cell testing | monthly: overlapping artifacts (same family, many cells) → union-instrument test set → re-clear FULL E4 on the union |
| Cross-cell merging | on pass: merged artifact replaces **per-cell** retires (owner-approved); G4 logs lineage; rollback = D5 prior version |
| Swarm testing | cross-style/cross-market swarm sims in E4-SIM (correlation, exposure, drawdown interaction) before any merge lands |

## §8 · Swarm agents (what "swarm" means here — both kinds)

| Kind | What | Powered by |
|---|---|---|
| **Fleet swarm** | 10⁴ deterministic simulated worlds for any strategy/question (vectorized NumPy/Numba) — E4-SIM | our engine, seed+param-hash replay |
| **LLM crew swarm** | multi-seat work on one mission (research envelope → analysis → scoring → ops) with DAG deps | ruflo `@claude-flow/swarm` task-orchestrator contract (ported) · Vibe-Trading preset patterns (tool/skill whitelists, anti-fabrication) · agency-agents personas · our NATS+Postgres queue |
| **Cross-cell swarms** | any new style tested against existing cells' exposure in sim before merge | §7 swarm testing |

Crew config schema: `crew.yaml` (roster of seat ids · depends_on DAG · tool whitelist ·
out_artifact schema · budget cap · approval level). CI lint enforces roster/administrative
fields (agency-agents runbook contract pattern).

## §9 · Communication & wiring (the whole wire)

**Five transports only:** 1) NATS subjects — `market.*` (R1) · `signal.*`/`order.*`/
`risk.*`/`ledger.*` (R2) · `seat.*` (LLM); queue-groups = load-balance, durable
consumers = independent replay, partitioned per-key ordering. 2) Files — D0/D5,
single-writer. 3) SQL — D1/D3/D4 concurrent servers (headroom = G1 bench). 4) Embedded
DuckDB — single D6 process, never critical path. 5) In-proc calls — E1/E4 workers, µs.

**Latency budgets (ESTIMATE until gates measure):**

| Hop | Budget |
|---|---|
| E1/E2 decision (pre-warmed) | <1 ms; µs-class cell internal |
| R1→D2 fan-out → cell receive | ≤ ~2 ms P99 (W0b measure) |
| E1→E5→E6→wire (order) | <5 ms internal; **venue RTT dominates** (Upstox ~45 ms vendor-claim · Hyperliquid ~600–700 ms public API · cTrader GA-routed) |
| D1 reads | boot/replay/dashboards only — never per-tick |
| seat tasks | seconds–minutes, intentionally async |
| research scans | seconds–minutes off-peak |

**Safety comms:** out-of-band watchdog process (no app dependency) · venue-side
max-loss order · one-action mobile kill · heartbeat to off-box.

## §10 · Every agent's memory + memory management

### 10.1 Non-LLM fleet (75 brains)
| Type | Where | Writer |
|---|---|---|
| working | cell process state (tick/feature/position/DD state) | the cell itself |
| episodic | **E7 ledger** + **G4 trial log** + shadow history | E7/G4 |
| semantic | **R3 registry**: frozen artifacts, cost model, calendars, limits, ban status, regime→style map | gates+owner only |
| procedural | the code (formulas, exits ladder, governor rules, runbook) | git PR only |

### 10.2 LLM seats
| Type | Where | Writer |
|---|---|---|
| working | task lease payload (`llm_tasks`) | seat, on lease |
| episodic | D4 audit rows (every run: tokens, result, artifacts) | seat runtime |
| semantic | D5 artifacts + graph.json + vault notes | versioned commits only |
| procedural | skill files | PR + review |
| owner rules | vault note | cockpit seat only |

### 10.3 Management policies
Write authority is **enforced, not prose** (DB roles; no seat can UPDATE D4; no seat
can write D5 except gated artifact path). Retention: D4 ≥5 yr; G4 forever; `llm_tasks`
trim after N days (digest summarization first); vault is curated by hand/cockpit.
Recall stack: Graphify graph for code/architecture; vault digest notes for decisions;
substrate (brigade Tideline patterns vs ruflo memory) decided by **W0c toy-shootout**
(measured). Obsidian = declarative decision memory already living in `gfi/`; formal
note types: decision · incident · runbook · research-digest · training-record.

## §11 · Chat with us + full control/monitor panel (cockpit)

**Chat interface (owner ⇄ system):**
- One keyboard surface (opencode/claude session as seat-12) + one web panel (OpenStock-
  pattern UX ideas re-implemented; session-clock, alert schema, cache pools as concepts).
- Every reply labeled ESTIMATE/MEASURED; every quote carries its artifact id.
- Owner actions by tier: L0 chat answers · L1 sandbox checks · **L2 sign-offs** (news
  ladder up/down, budget raise, ticket triage) · **L3 live-decisions** (promotions,
  kill re-arm, deploy) — approval cards in chat; the ≤5/week promotion quota lives here.
- Everything chat says/does streams to D4 + weekly digest → vault.

**Monitor surfaces (G2):**
| Surface | Data |
|---|---|
| Cell health | per-cell heartbeat, state (E1/E2/E5), last decision id, kill-status |
| Portfolio | D4-derived exposure/net across the 3 sub-accounts |
| Market state | session clock per venue, kill-state, ban status |
| Data | gap calendar, D0/D1 usage vs 16-TB, collector backlog |
| LLM layer | token burn/seat/day (G9), eval-harness replays, failed tasks |
| Research | G4 trial counts, DSR status of trusted set, coverage map heat |
| Ops | incident queue, restart/restore drill results, backup freshness |

**Execution/kill:** cockpit never sits in the order path; the L0–L4 cascade and the
out-of-band watchdog do. Cockpit *asks* E5/E6 orbitally (commands to gate; never
direct).

## §12 · Performance, accuracy, latency, error, bug & issue management

| Discipline | Mechanism |
|---|---|
| Latency | per-hop budgets (§9) measured at gates (W0a/W0b) into D3 dashboards; venue RTT reported with vendor-claim labels |
| Accuracy | replay determinism bit-for-bit; eval-harness stubs (timecopilot FunctionModel pattern) test seat flows without any provider; accepted-model stiction via drift triggers |
| Errors/bugs | ticket taxonomy (bug · incident · data-gap · drift · ops) → build seat reproduces with **flight-recorder** (E1/E2/E5/E7 traces replay to same decision id) → fix+regression test → gates re-run |
| Error budget | per-lane error budget (order path, rail, seat runtime) — p95/p99 targets tracked; budget breach = auto shadow-mode for that lane (ESTIMATE, tune at W0c) |
| Issue mgmt | cockpit queue surfaces open tickets with owner-assignable priority; weekly digest includes burn-down |
| Chaos | 10×-restart · feed-stale · venue-down · kill-switch drills monthly (runbook §6A) |
| Code gates | `cargo clippy -- -D warnings` + `cargo test` · `tsc --noEmit` (TS seats) · `uv run pytest` (Python) · review + cybersec seats · secret scan pre-commit |

## §13 · Build order with pin-point instructions (waves + gates)

| Wave | Build | Exit pins (must be real command output) |
|---|---|---|
| W0a | monorepo skeleton §2.1 · Compose stack · R1 rail · D0/D1 pick (5 gates) · PIT replay · G1 concurrency bench | replay identical ×2 · D1 gates file · bench JSON |
| B1 | collectors market/news/world · 6–10y fill per tiers · D3 gaps table | B-GATE: rows verified · gaps open-but-justified · no training before |
| W0b | D4/D5 roles+signing · E1/E2/E5/E7 skeletons · L0–L4 + watchdog · IPC fan-out bench | restart ×10 · fan-out p99 · kill drill log |
| W0c | LLM control plane: `llm_tasks` · seat runtimes on brigade-guards/ruflo-DAG port · approval bridge L0–L3 · Graphify+Obsidian fold-in · G1 vault · G2/G9 dashboards · substrate shootout · cockpit v1 (chat only) | toy-build cold start · 10× Compose restart · Graphify delta measured · shootout numbers |
| W1 | D6 research cache · PySR/MAPIE/arch wiring · E4-SIM/BT/WF · E6-PAPER · first full style through §6 to D5 | pipeline run log · D5 signed artifact |
| W2 | cost model verified (O8) · cTrader adapter · proton JetStream-MV trial (own gates) · seats news/data-QA/compliance live · reconcile E7 vs broker | paper fills + ledger match |
| W3 | first cells shadow · NY4 lane planning · Live requires owner L3 + shadow history | shadow P&L/IC files |
| W4–W5 | tick+depth feeds → E1 microstructure · Hyperliquid node option · crypto cells · ML lane (E3) live | microstructure replay · HL colo decision |
| Stage 6 | cockpit GA (panel + chat approvals; O16 resolved) | L3 flow demo · weekly digest in vault |
| Stages 7–10 | Terminal/Portal/Media (rights firewall) · World Monitor LAST · HFT subsystem only if measured | Row-1 replica isolated · no hot-path impact test |

**Week-1 instructions (concrete):** create monorepo §2.1 skeleton · bring up Compose
stack with Postgres/QuestDB/NATS + healthchecks · write R1 rail minimal loop (validate
→ stamp → publish to `market.*` + dump D0 parquet) · replay harness CLI · G1 bench
harness stub · `docs/` import (5 plans + this one) · vault note types created ·
Graphify baseline graph build (`graphify` installed at W0a; `--update` mode understood) · open the
W0a gate document to record every measurement as it happens.

## §14 · Risk register (unchanged standings)

single-box fragility (offsite + compose snapshots before big backfills) · venue RTT
domination · license drift re-verify cadence · India data gaps pre-2022 (O12/O15) ·
single-source research evidence (C14) · new-code failure modes of the cockpit layer
(reviewed at trading-code parity) · LLM seat cost/perf oscillation → budget caps + EN
plugboard G9.

## §15 · Open decisions (status)

Carried from log with owners/waves: O3 F&O granularity · O4 off-index names · O5 first
style · O6 FX scalping add · O7 hedge executor · O8 cTrader prod limits · O9 Upstox
algo-tag · O10 order-balance · O11 HL FIU-IND/FEMA/tax · O12 PIT fundamentals · O13
ToS checklist · **O14 model routing/privacy (W0c)** · **O15 NSE membership vendor
(W2)** · **O16 cockpit runtime (W0c)** · ~~O1 resolved (S43)~~.

## §16 · Sources

All content synthesizes locked decisions from: FULL-SYSTEM-PLAN.md ·
AGENT-FLEET-BLUEPRINT.md · ENGINE-DATABASE-PLAN.md (incl. §19 TSDB final) ·
LLM-FLEET-BLUEPRINT.md · plan log S1–S45. No new external claims; every prior vendor
or author-reported number remains labeled as in its source doc.

*Session 46 log entry + commit hash recorded in the planning log.*
