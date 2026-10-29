# FULL-SYSTEM-MASTER-PLAN — one document, the whole machine (Session 45, 2026-10-29)

> Status: **PLAN** — the single entry-point that unifies the five specialist docs into
> one build plan: structure, architecture, workflows, memory, comms, quality gates,
> and the stage-by-stage build instructions. Nothing here relaxes any existing
> decision; it consolidates them and adds the connective tissue that was missing
> (end-to-end workflows, the agent-teams operating model, monitor/control chat, and
> per-stage validation gates). Labels: **ESTIMATE** = projected, to be measured;
> **MEASURED** = command output on record; everything else traces a source doc.

---

## §0 · Reading map — where each decision lives

| Topic | Source of truth | This doc's role |
|---|---|---|
| Whole-ecosystem rows, R1–R8 rules, 10-stage order | `FULL-SYSTEM-PLAN.md` | restated §1/§2 |
| The 75 non-LLM trading cells, guards, runbooks, training | `AGENT-FLEET-BLUEPRINT.md` | restated §3/§6/§7-lite |
| Store ladder D0–D7, engines E1–E7, wiring, Nautilus verdicts | `ENGINE-DATABASE-PLAN.md` (+§19 TSDB final) | restated §4/§5 |
| LLM control-plane (12 seats, orchestration, scoring gate) | `LLM-FLEET-BLUEPRINT.md` | restated §3/§7-lite |
| Dataset gathering (1,114-row master TODO) | `Research/data/todo/` master + §17/§18 of engine plan | consumed §4-B1 |
| **Everything together — workflows, teams, memory, comms, gates** | **this document** | the missing "how it all runs and builds" layer |

Committed sessions live in `planing conva/PLANNING-CONVERSATION-LOG.md` (S1→S45);
open decisions O3–O16 are reproduced in §14 with owners/waves.

## §1 · The one-page system picture

```
                        ┌──────────────────────────────────────────────┐
                        │ COCKPIT (chat) — owner ⇄ LLM control plane   │
                        │ L0 read · L1 sandbox · L2 sign-off · L3 owner │
                        └───────┬───────────────────────────┬──────────┘
                                │ NATS subjects             │ Postgres tasks
      ┌─────────────────────────▼─────────────┐   ┌─────────▼───────────────┐
      │  RAIL: R1 ingest (validate·as-of·\\\   │   │ LLM SEATS (12): build/  │
      │  rights-tag) → D2 JetStream fan-out    │   │ review/cybersec/data-QA/│
      │  → D1 QuestDB (warm) → D0 Parquet      │   │ news/research/scoring/  │
      │  (cold) → D3 Postgres (reference)      │   │ ops/fleet-ctl/train/    │
      └───────┬────────────────────────────────┘   │ compliance/cockpit      │
              │ boot pre-warm (not per-tick)       └─────────┬───────────────┘
      ┌───────▼───────────────────────────────────┐          │ D5 artifacts only
      │ E1 features (Rust, in-mem) → E2 signal →  │   (never the hot path; R3)
      │ E5 governor → E6 exec → venue adapters    │
      │ (Upstox·cTrader·Hyperliquid) → fills → E7 │
      │ → D4 append-only ledger                   │
      └───────┬───────────────────────────────────┘
              │ nightly: D0/D1 → D6 DuckDB → Mode R (75 research agents, offline)
              ▼ candidates → E4 gate (CPCV+DSR≥0.95+crisis replay+crisis paper)
              → owner approves (≤5/week) → D5 signed → Mode T boots it
```

Three rows still hold (FULL-SYSTEM-PLAN §2): Row 1 = Terminal + News portal + Media
hub (public, rights-firewalled, LAST); Row 2 = the three engines (Financial · News ·
World) on the rail above; Row 3 = FinOS (this trading system) + Cockpit + World
Monitor (last of all).

## §2 · The two non-negotiable rule families

**Architecture rules R1–R8** (FULL-SYSTEM-PLAN §3): collect once/reuse many · rights
firewall at Row 1 boundary only · **intelligence never in fast paths** · PIT +
survivorship discipline · provenance everywhere · versioned contracts · deployment
gates (train→eval→paper→approve→deploy) · HFT honesty (venue RTT dominates; internal
budget <5 ms; retail feeds are research-grade).

**Fleet guardrails** (AGENT-FLEET-BLUEPRINT): guards G1–G6 — G1 cells cannot edit
their own artifacts · G2 no search code in Mode T · G3 research can't place orders ·
G4 every trial logged · G5 one active style per symbol · G6 Mode R/T separate
processes — plus the L0–L4 kill cascade (cell→symbol→team→market→global) and an
out-of-band watchdog kill that works without the app · startup invariant:
reconcile-before-orders · idempotent order IDs · D4/D5 append-only/signed at the
DB-role level.

**Regulatory R9–R14** and the venue-specific non-negotiables stay as in
AGENT-FLEET-BLUEPRINT §7 (Upstox algo-tagging is O9; Hyperliquid FIU-IND/FEMA is
O11; ToS/ban checklist is O13).

## §3 · The workforces — "one team of agents"

### 3.A The non-LLM fleet: 75 brains, ~480–545 hands, ≤75 active

| Market | Symbols | Hands (cells ceiling) | Exits |
|---|---|---|---|
| Stocks (NSE/BSE, options buying only) | ≤59 | ~440–500 | rule ladder 1–6 + kill |
| FX Team 1 (cTrader ECN) | 11 | 29 | cascade-flatten, funding-window print, kill |
| FX Team 2 (separate account, pure scalping) | 2 | 2 | push-mode, session-closed flat |
| Crypto (Hyperliquid perps) | 3 | 10–12 | funding/liquidation/weekend/PAIR exits |

*Owner says "73" — that was the old symbols×1-style count (FLEET §1 note). The locked
roster is **75 research brains**; the hands (cells) get built only when E4 promotes a
style, and G5 keeps exactly one alive per symbol.*

### 3.B The LLM control plane: 12 seats (all R3; never the hot path)

Row extracted verbatim in `LLM-FLEET-BLUEPRINT.md` §2: build · review · cybersec ·
data-QA · news · research/analysis · scoring · ops · fleet-control · training ·
compliance · cockpit/comms. Hard ceilings: seats produce **PRs/findings/artifacts/asks
for approval** — never market writes outside L0–L3, never frozen-artifact edits
without E4, never an order from research.

### 3.C How the three work together (the operating loop)

| Loop | Trigger | LLM seat | Fleet side | Output |
|---|---|---|---|---|
| Build | owner or W-wave task | build seat writes code+tests | never runs live | PR → review seat → cybersec seat → owner merge |
| Bug/issue fix | alert or owner chat | build seat reproduces (flight-recorder replay) | cell frozen for the fix window | fix PR + regression test |
| Score/promote | scheduled research | scoring seat prepares D5 candidate | E4/E2/E5 run the gates | owner approval (≤5/week) |
| Research | nightly or on ask | research seat (citations required) | E1/E4 replay | cited artifact in D5 |
| News/event | market hours | news seat digests | news ladder + kill-floor checks R3 records | digest + R3 record |
| Data-QA | nightly + rail alerts | data-QA seat | gap calendar | tickets, never silent fixes |
| Ops/kill | any L0–L4 trigger | ops seat narrates | E5/E7 run it | incident report + runbook update |
| Train | weekly + drift | training seat | G4/D5 machinery | candidate artifacts through E4 |
| Monitor | cockpit session | fleet-control seat | NATS state map | chat + FL0–L4 queue |
| Self-learn | weekly | training + research | replay metrics | proposal set only |

**The honest division:** LLM seats *decide-and-explain*, the deterministic fleet
*executes-and-proves*. Nothing an LLM seat says becomes live unless a gate (E4/E2,
DSR, edge-lift, L0–L3) says so.

## §4 · What the 13 owner-named repos contribute (deep-read verdicts — measured 2026-10-29)

Per-repo rows with file-level cites live in `LLM-FLEET-BLUEPRINT.md` §3.1; summary:

| Contributes to | Repo | Mechanism | Constraints honored |
|---|---|---|---|
| Boss-loop contract | ruflo (`@claude-flow/swarm`) | DAG orchestrator, retries, cycle-detection | in-proc bus = we keep NATS+Postgres durable queue ourselves |
| Loop discipline | brigade | loop-guards: budget/no-progress/repetition/**independent done-checks**/completion veto | brand-scrubbed fork → vendor-pinned; gateway-runtime decision = open O16 |
| Approval contract | brigade `approval-bridge` | request/resolve tokens for L0–L3 | mechanism = ours |
| Scheduled seats | brigade cron/isolated-agent + metering | headless scheduling pattern | — |
| Role library | agency-agents (267 personas, already wired in this workspace) | prompt-only, runbooks.json roster contract | sanitize marketing prose |
| Decision procedure | day-trader-skill | 11 SKILL.md + deterministic timemachine/risk/size (315/315 tests pass) | US/long-only scope: merge our instruments (S44 note) |
| Typed schemas | TradingAgents `agents/schemas.py` | Pydantic signed decision outputs | strip vendors/runtime |
| Prompt packs | Vibe-Trading `swarm/presets/*.yaml` | hard anti-fabrication data-citation rules | prose not typed → local schema pass |
| Research grounding | quant-mind | cited-artifact schema (chunk+page+quote+`as_of`); BM25/agentic retrieval | processor, not corpus |
| Forecast gate | timecopilot | forced beat-SeasonalNaive via output-validator; FunctionModel stub testing = our eval-harness template | Pre-Alpha; don't wholesale-install |
| Streaming layer candidate | proton | **native NATS JetStream external streams** + streaming materialized views | trial at W2, NOT a D1 change (QuestDB stays D1 per §19) |
| Cockpit UI ideas | OpenStock | session-clock, alert schema, cache-pool | **AGPL — ideas only, no code lift** |
| Decision-head contract | laya-mlx | choice/score/noul + abstention + calibration clamps | **runtime REJECT** (MLX darwin/arm64 only, CUDA unsupported in code) |
| REJECTS | AI-Trader (no LICENSE file — README badge links to nothing; platform, not library) · nixtla (cloud-locked `api.nixtla.io` SDK; free models live in `statsforecast`/`mlforecast`) | — | license/hardware/cloud |
| Memory recall | Graphify (owner addition) | local AST graph, 0-token code mode, opencode skill install | Cloud upsell rejected; "71.5×" claim = author-reported (§9) |

## §5 · The data plane (consolidated, decision-final)

| Store | Engine | Holds | Decided |
|---|---|---|---|
| D0 | Parquet on NVMe + object backup | years of ticks/bars/filings/news/world captures | §18 16-TB target, ESTIMATE-range 4–16 TB |
| D1 | **QuestDB** (final §19; ClickHouse fallback if W0a gate fails twice) | live window only | Timescale rejected for D1 (tsl/ license wall) |
| D2 | NATS JetStream | live event fan-out | proton as layered streaming-MV candidate at W2 |
| D3 | PostgreSQL | registry/reference/rights/calendars/limits | + LLM `llm_tasks`/cockpit tables |
| D4 | PostgreSQL append-only role | fills/P&L/trials/audit ≥5 yr | E7 + G4 write here |
| D5 | filesystem + Postgres index | frozen artifacts, feature-hashed, signed | scoring outputs versioned here |
| D6 | DuckDB over D0/D1 | research scans | Mode R only |
| D7 | (upsert) ClickHouse candidate if research outgrows D6 | future | not planned now |

**Gathering:** master TODO 1,114 rows (18 volumes; S43 merge) drills wave-tagged W0/W1/
W2/W3; B1 backfill gate = all 3 domains loaded 6–10y + gaps table verified **before
any agent trains** (S42/§17.3). Cross-check rows each wave; no walled-source scraping
(DB-GATE per §17.2).

## §6 · The engine stack (who builds, who adopts)

| Role | Engine | Built by | Note |
|---|---|---|---|
| Ingest rail | R1 | ours (Rust) | validate·as-of·rights-tag·fan-out writes |
| Feature engine | E1 | **ours (Rust)**, tree-sitter-style determinism + ONNX where ML | pre-warm at boot; no per-tick store query |
| Signal | E2 | ours (Rust) | loads frozen D5 artifacts at boot |
| Gate | E4 | ours (research tooling + CPCV/DSR crates) | council-fixed stats |
| Governor | E5 | ours (Rust) | deterministic |
| Execution | E6 adapters | **Nautilus** runtime for cell hosts (fleet), adapters ours | Nautilus held the speed verdict (§13) |
| Ledger | E7 | ours | append-only D4 writer |
| LLM runtime | (seats) | Claude Code / opencode harnesses + brigade-loop/ruflo-swarm ports | never in hot path |

Rust↔Python boundary: kernel = Rust (rail/hot path); edge = Python (research, E4
tooling, LLM seats) — monorepo, hard boundary, contracts across (FULL-SYSTEM-PLAN §6).

## §7 · Workflows (the "how it all runs," pinned)

### 7-A One data point's life (session-locked)
venue → R1 (validate·as-of·rights) → D2 (µs–ms fan-out) + D1 (warm) + D0 (cold) +
D3 (ref) → E1 prewarmed shared memory → E2 (frozen artifact) → E5 → E6 → venue →
fills → E7 → D4 async → nightly D6 research → E4 ladder → owner → D5 signed →
Mode T boots it.

### 7-B Research → live ladder (the only promotion path)
COLLECT (R1-stamped) → LABEL (triple-barrier, net-of-costs, from signal log not
fills) → SEARCH in Mode R (GP/ML/meta-label, **10³-candidate budget per style**) →
GATE E4 (CPCV purge+embargo by day, DSR ≥ 0.95 vs decorrelated trial count, crisis
replay, net-of-cost) → PAPER (E6-PAPER) → SHADOW (3–6 mo) → owner approval
(≤5/week) → D5 signed write → Mode T loads at next boot → MONITOR (rolling
IC/PSI-drift/edge decay) → retrain loop or retire.

### 7-C Fix-a-bug loop (build/review/audit)
1. ticket from alert/chat (fleet-control seat repro'd via E7/D4 flight data) →
2. build seat: reproduce + fix + regression test (Rust: `cargo clippy -- -D warnings`
   + `cargo test`; Python: `uv run pytest`; TS seats: `tsc --noEmit` + test) →
3. review seat + cybersec seat (egress diff/marketing-drift/secret scan) →
4. owner merge → CI gates rerun → deploy window (no live-position freeze violation).
**Cooldown rule:** cells touched by a fix restart in shadow first if E4 still binding.

### 7-D Training / self-learning / cross-cell merge (offline only)
- Weekly: re-run §7-B labels → candidate artifacts; every attempt G4-logged.
- Drift-triggered: edge decay (rolling net-IC vs frozen) → retrain+re-gate, logged.
- **Cross-cell merge:** monthly research batch flags overlapping artifacts (same
  signal family across cells) → merge candidate must re-clear **full E4** on the
  union instrument list, then the old artifacts retire **per-cell**, owner-approved.
- Hard ceiling unchanged: no live rewrite of Mode T; no LLM in R1/R2; no style
  enable without E4; agents never edit risk limits (RL-gate R7 of FULL-SYSTEM-PLAN).

### 7-E Cockpit chat loop (owner ⇄ system)
report types: cell health · portfolio · kill-state per market · pending L0–L3
approvals · sprint status · LLM-layer quality (scores passed E2 gate, token burn) ·
budget burn · ticket burn-down · research batch digest. Every chat line labeled
ESTIMATE/MEASURED/SOURCE. Weekly digest auto-writes to the vault. Incidents page
(owner mobile kill is out-of-band and always works without the app).

## §8 · Comms & wiring (latency-engineering targets — ESTIMATE until measured at gates)

| Hop | Transport | Budget |
|---|---|---|
| venue feed → R1 → D2 fan-out to cells | NATS JetStream WS | ≤ ~2 ms internal P99 (MEASURE at W0b) |
| D1 warm read (boot/replay/dashboards) | QuestDB ILP | ms-class; not on tick path |
| LLM seat → task queue → seat | Postgres `llm_tasks` + NATS subject | seconds — intentionally async |
| cockpit chat → owner | gateway or thin WS (O16 decision) | human-scale |
| order path E1→E5→E6→wallet | in-process Rust + pre-serialized templates | <5 ms internal; venue RTT dominates (HFT honesty R8) |
| research scans | D6 DuckDB vectorized | seconds–minutes, off-peak |

**Container layer (owner ask 2, adopted):** Compose wraps Postgres/QuestDB/NATS/
cockpit/chat + build workers; per-service CPU pinning (council fix A2) · resource
volumes sized per store · restore drills reuse Compose snapshots · the 10×-restart
W0a gate is defined against the Compose stack; **NOT** in the tick path (rail stays
bare-metal).

## §9 · Memory — every kind, every agent (owner ask 2/4)

| Memory type | Non-LLM fleet | LLM seats | Store/writer |
|---|---|---|---|
| Working | cell process state (E1/E2/E5) | task-lease payload in `llm_tasks` | process / Postgres |
| Episodic | E7 ledger + G4 trial log | D4 audit of every run (token cost, result) | append-only |
| Semantic | R3 frozen artifacts + calendars/limits | D5 knowledge artifacts + Graphify graph.json + vault notes | signed/versioned |
| Procedural | code + runbooks (§6A) | skill files (§7 of LLM blueprint) | git + reviews |
| Owner rules | — | vault note (rules people + seat rules) | cockpit writes only |

**New layers (owner S44 ask, adopted in LLM-FLEET-BLUEPRINT §8.1, adopted again here):**
- **Obsidian layer** = declarative decisions/knowledge: `gfi/` IS the vault already;
  add note types (decision · incident · runbook · research-digest · training-record)
  + cross-links; cockpit weekly digest auto-writes; read-only from agents.
- **Graphify layer** = code+docs knowledge graph on-demand: adopt as **local tool**
  (tree-sitter AST, 0-token code mode, EXTRACTED/INFERRED edge audit) refreshed via
  `--update` in the build/review loop; optional vault-index expansion — **not** the
  paid Cloud. Token-savings claim = author-reported/unreprovable → **W0c fit-test**
  measures our own delta before any agent depends on it.
- Recall substrate candidates (brigade Tideline patterns vs ruflo `@claude-flow/memory`)
  decided by a W0c toy-shootout (measured recall/latency), then wired to R3/D5 tables.

## §10 · Quality machinery (the honest wall)

| Gate | Threshold | Layer |
|---|---|---|
| DSR at E4 | ≥ 0.95 vs decorrelated trial count (G4) | research |
| Pressed edge-lift (E2) | beat frozen baseline in replay repeatedly | scoring seats |
| Crisis replay | EOD index-option crisis series pass | E4 |
| Backtest discipline | CPCV purge + embargo, day-grouped folds | E4 |
| Self-learn cap | 10³ candidates/style; if it can't clear DSR, no trade | G4/C3 |
| Code | `cargo clippy -D warnings`/`cargo test` · `tsc --noEmit` · `uv run pytest` · review+cybersec seats · secret scan | all seats |
| LLM honesty | citations required (quant-mind contract); ESTIMATE/MEASURED labels in cockpit; anti-fabrication hard rules lifted from Vibe-Trading; **S44-gap-register G5** eval-replay harness with FunctionModel-style stubs | seats |
| Liquidity/cost | venue cost model in E5 governor + E7 reconciler | always |

## §11 · Build instructions — wave by wave, gate by gate

The 10-stage order (FULL-SYSTEM-PLAN §5) + S41/S42 amendments, now with **per-stage
validation pins** (a wave is DONE only when its pins are true commands with true outputs):

| Wave | Builds (from all 4 plans) | Validation pins |
|---|---|---|
| **W0a** | D0 lake · Compose stack (D3/D4/D5/D6 + NATS + QuestDB) · R1 rail skeleton · PIT replay harness · 5 D1 gates (burst/determinism/pre-warm/restore/10×-restart) | replay twice identical (bit-level) · harness outputs filed · result JSON in D3 |
| **B1 (S42)** | collecting engines for market/news/world · backfill 6–10y, all 3 domains, TODO-driven · in-DB gaps table · blocked-source handling | domain verified + gaps table non-empty-but-justified → B-GATE before any training |
| **W0b** | D4 ledger + append-only roles · D5 signed registry · E1/E2/E5/E7 skeletons · L0–L4 cascade + out-of-band kill · IPC fan-out benchmark | crash-restart cycle ×10 · fan-out P99 file · kill drill rehearsal log |
| **W0c (S44/45)** | LLM control plane: brigade-guard port + ruflo swarm runner · `llm_tasks` · L0–L3 bridge · Graphify fold-in + Obsidian note types · G1 vault · G2/G9 dashboards · fit-test O16 | toy build measures: cold start · 10×-restart of Compose · Graphify recall delta · substrate shootout |
| **W1** | D6 research cache · PySR/MAPIE arch integration · E4-SIM/BT/WF · E6-PAPER | one full style through §7-B to D5 (dry-run) |
| **W2** | seats live (news/data-QA/compliance) · cost model verified O8 ticking · proton JetStream-MV trial (gates: ingest parity ≥ JetStream benchmark) · G4/G6/G7 | first paper fills · reconcile E7 vs broker |
| **W3** | first cells live-candidate · cTrader adapter (NY4 lane planning) · shadow 3–6 mo | shadow METRICS, no live claims |
| **W4–W5** | tick+/depth feeds mode · Hyperliquid node option · crypto cells · training/tech-selflearn rolling | drift-trigger retrain demonstrated |
| **Stage 6+** | cockpit GA (OpenStock-pattern UX ideas; O16 resolved) | L3 flows + weekly digest flowing |
| **Stages 7–10** | Terminal/Portal/Media (public, rights-firewalled) · World Monitor LAST · HFT subsystem only if measured demand | rights firewall live · Row-1 replica never blocks hot path |

## §12 · Monitor/control plan (owner ask 4 — the one line per surface)

1. **Cells**: NATS subjects + D3 cell-registry; per-cell heartbeat; L0–L4 map in cockpit.
2. **Engines**: p95/p99 dashboards (Rust metrics; Grafana-lite on Postgres, G2).
3. **Data**: gap calendar (B1 gate tables), D1/D0 budget burn e.g., 16-TB tracker.
4. **LLM layer**: token burn per seat/day (G9), truth labels, eval-harness replays.
5. **Owner mobile**: out-of-band kill + status, independent of everything else.

## §13 · Honest risk register (standings, not new)

single-box fragility → offsite/backup story + compose snapshots; venue RTT domination
(HFT honesty R8: no shortcut); vendor license drift (re-verify cadence); India data
gaps pre-2022 = paid-vendor question (O12/O15); council single-source research claims
(meta-labeling etc. — C14: adopt cautiously, verify on our data); cockpit/LLM layer is
NEW code with new failure modes — treat at parity with trading code for review gates.

## §14 · Open decisions (O-registry status)

| ID | Item | Needed by |
|---|---|---|
| — | ~~O1~~ RESOLVED (S43) | — |
| O3 | F&O cell granularity (per underlying vs per contract) | Stage-5 kickoff |
| O4 | keep 7 off-index names or swap to true NIFTY 50 | B1 |
| O5 | first style per instrument (73→75 brain count note) | build |
| O6 | add FX scalping style? (30→31 styles) | build |
| O7 | hedge-executor shape (per-cell vs portfolio) | build |
| O8 | cTrader production limits (500/min is demo) | B1/W2 |
| O9 | Upstox algo-tagging 13th digit | W2 |
| O10 | order-balance requirement definition | B1 |
| O11 | Hyperliquid FIU-IND/FEMA/tax | W2 (before HL cells) |
| O12 | PIT as-reported fundamentals source | B1 |
| O13 | ToS/ban-avoidance checklist | B1 sourcing |
| O14 | model routing + privacy per LLM seat | W0c |
| O15 | NSE index membership / survivorship-free vendor | W2 |
| O16 | cockpit runtime: brigade gateway vs thin own | W0c |

## §15 · Sources

Primary: the four specialist docs (each with its own sources S1–S17) + planning log
S1–S44. New lines this session: consolidation synthesis over those docs; O-registry
restated from log; no new external claims. External numbers cited earlier
(71.5×, "80% cut", vendor benchmarks) remain labeled as before — **this doc adds no
new uncorrected claims**.

*Session 45 log entry records this deliverable; commit hash recorded in the log.*
