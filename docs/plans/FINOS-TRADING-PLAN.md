# FINOS-TRADING-PLAN — the financial trading plan of the whole system (Session 47, 2026-10-29)

> The **visual edition** of the full trading plan, in the owner-specified diagram style
> (colored-band data pipeline · layered runtime · latency-banded execution path ·
> streaming-platform planes). Same locked content as `FULL-SYSTEM-BLUEPRINT.md` (S46),
> re-planes it into 10 architecture pages. **The .html/.pdf carry the diagrams; this .md
> is the text source of the same content.** Labels: ESTIMATE = to measure at its gate ·
> MEASURED = on record · everything else = a locked decision.

Read order: with the master plan (S45) for navigation and the blueprint (S46) for depth;
this document is the owner-facing picture plane.

## Page 1 · Master map (cover)

- Row 1 public content (Terminal/Portal/Media, LAST, rights-firewalled) ← reads engines
- Row 2 shared intelligence foundation (collect once/reuse many)
- Row 3 private: **FinOS (this plan) + Cockpit (12 LLM seats) + World Monitor (LAST)**
- Stats: 75 research brains (owner "73" = old symbols×1-style count, S28) · 480–545
  cell ceiling, ≤75 active · 12 seats · <5 ms internal order budget (R8)
- Invariants: R1–R8, R9–R14, guards G1–G6, kill cascade L0–L4 + out-of-band kill

## Page 2 · Data pipeline (in motion → at rest → insight → action)

| Band | Content |
|---|---|
| in motion | venue feeds (Upstox WS+SSE · cTrader Open API · Hyperliquid) · news/world collectors (B1, T3 pin-to-pin only) · filings/reference — R1 rail validate·as-of·rights-tag |
| the bus | **NATS JetStream (D2)** — market.* signal.* order.* risk.* ledger.* seat.* · queue-groups = LB · durable = replay · partitioned per-key ordering |
| at rest | D0 Parquet lake (NVMe + offsite, 16-TB accepted) · D1 **QuestDB** warm (§19 final) · D3 Postgres reference (+llm_tasks) · D4 Postgres **append-only** ledger (≥5 yr R13) |
| at insight | D6 DuckDB scans (one process) · E4 gate stack (SIM 10⁴ worlds · BT · WF CPCV · DSR ≥ 0.95 · crisis replay) · scoring/research seats (citations required, 10³/style cap) |
| at action | E5 governor (zero ML) · E6 execution (PAPER/SHADOW/LIVE) · E7 ledger → D4 · cockpit approvals |

Store rules: hot path never queries a store per tick · cold in D0, warm in D1, hot in
RAM, truth in D3/D4/D5 · owner gate ≤5 approvals/week (C8) before any D5 write.

## Page 3 · Three runtime layers

| Layer | Members | Boundary |
|---|---|---|
| Python (edge) | research/ (PySR·MAPIE·arch·sklearn·PyTorch·SB3) · gates/ (E4: CPCV·DSR·crisis·E4-SIM NumPy/Numba) · seats/ · tools/ | nightly/offline |
| **Rust (kernel, hot)** | rail/ R1 · featurize/ E1 (pre-warmed) · evaluate/ E2 · govern/ E5 · exec/ E6 · ledger/ E7 | no framework in hot path |
| LLM seats (R3) | 12 seats + brigade loop-guards + ruflo DAG ports | approvals/PRs only |

Contracts across = the five interfaces (storage · research · artifact · execution ·
audit, R6 versioned). Connectors in: per-venue feeds; orders out: E6 adapters only
(idempotent IDs · reconcile-before-orders). Adopt-vs-build table = blueprint §3.3.

## Page 4 · Execution path with latency bands (ESTIMATE → measured at gates)

- Market data in (~ms network): Upstox · cTrader · Hyperliquid · news rail (async)
- **HOT path** trading-kernel: E5 risk first (pre-trade size/limits/spread/news-ladder)
  · E1 pre-warmed · E2 frozen-artifact eval · daily circuit kill — budget <1 ms decision,
  ≤~2 ms R1→cell fan-out P99 (W0b)
- **WARM path** OMS-class: E6 order state machine · lifecycle · replays · position
  reconciler · D4 ledger — E1→E5→E6→wire **<5 ms internal (R8)**; venue RTT dominates
- Venues + labels: Upstox ~45 ms (vendor-claim) · cTrader GA-routed, NY4 lane W3,
  prod limits O8 · Hyperliquid ~600–700 ms public API (MEASURED public figure), node
  option W4–W5
- Settlement of truth: D4 append-only + D1 warm + COLD overnight (G4, E3 Colab-T4 lane,
  D5 feature store/model registry)

## Page 5 · Streaming platform (five transports, nothing else)

sources → B1 producers (1,114-row TODO catalog) → NATS JetStream core: bus · event
store/log (replayable) · schema registry = the five contracts · security layer =
rights firewall + append-only roles · observability = flight-recorder + G2/G9 →
consumers (Mode-T cells · seats · E7/dashboards) → destinations (D0/D1 · cockpit ·
owner mobile out-of-band kill · weekly vault digest). Cockpit L0–L3 = the API-gateway/
auth analog. Proton JetStream-MV = W2 trial on top, NOT a D1 change.

## Page 6 · Engine stack + candidate ladder

E1 featurize · E2 evaluate · E5 govern (zero ML) | E3 train (offline, Colab T4) ·
E4 THE gate · E6 execute (only engine that ever sends an order) · E7 ledger.
Ladder: E4-SIM → E4-BT vectorized → E4-BT event (fill model per hftbacktest checklist)
→ E4-WF (CPCV purge+embargo, day-grouped folds, untouched holdout, DSR ≥ 0.95, crisis
replay) → PAPER (days) → SHADOW (3–6 mo, zero order authority) → owner gate (≤5/wk) →
D5 signed → LIVE → E7 decay watch → monitor/retrain/retire (per-cell, owner-approved,
G4 lineage). Self-upgrade ceiling = a NEW frozen artifact through this ladder, never a
live rewrite (G1).

## Page 7 · Workforces + swarms

Fleet hands: stocks ≤59 (options buying only, same-day) · FX Team 1 (11) · FX Team 2
(2, separate account, scalping push-mode) · crypto (3) — exits per market, kills.
Cockpit seats ×12 (build/review/cybersec/data-QA/news/research/scoring/ops/fleet-ctl/
training/compliance/cockpit). Ten operating loops bind them; nothing a seat says
becomes live without a gate. Swarms: fleet swarm = E4-SIM 10⁴ worlds · LLM crew swarm =
ruflo-port DAG with crew.yaml (roster · depends_on · tool whitelist · budget cap ·
approval lid · CI lint) · cross-cell swarms for merge testing.

## Page 8 · Memory + control plane

Memory matrix (both kinds): fleet working/episodic(E7+G4)/semantic(R3)/procedural(code/
runbooks) ⇄ seats working(llm_tasks lease)/episodic(D4 audit)/semantic(D5+Graphify+
vault)/procedural(skill files)/owner-rules(vault, cockpit-seat-writes-only). Write
authority DB-enforced. Retention: D4 ≥5 yr · G4 forever · llm_tasks trimmed after
digest · Obsidian note types (decision · incident · runbook · digest · training-record)
· W0c substrate shootout. Kill cascade L0 cell→L4 global + out-of-band watchdog +
venue-side max-loss + one-action mobile kill; chaos drills monthly; cockpit monitor
surfaces G2 (7 rows). Owner tiers L0–L3.

## Page 9 · Build order

W0a → B1 (data-first, B-GATE before training) → W0b → W0c → W1 → W2 → W3 → W4–W5 →
Stage 6 (cockpit GA, O16) → Stages 7–10 (Row-1 engines/Media/World Monitor LAST) —
each with exit pins as real command output (table identical to blueprint §13). Week-1
instructions: skeleton, Compose healthchecks, R1 minimal loop, replay CLI, G1 bench
stub, docs import, vault note types, Graphify baseline, W0a gate doc opened.

## Page 10 · Honesty register

Vendor/author claims stay labeled (Upstox 45 ms vendor-claim; "80% cut"/"71.5×"
unreproducible → replaced by our own W0c fit-test; Graphify Cloud upsell rejected).
Measured-at-gate list: W0a 5 gates · W0b fan-out+restart · W0c deltas+shootout · W1
DSR+crisis+cap · W2 parity. Rejects on record: AI-Trader (no LICENSE) · nixtla SDK
(cloud-locked) · laya-mlx runtime (darwin/arm64) · OpenStock code (AGPL, ideas only) ·
TimescaleDB for D1 (tsl/ wall) → QuestDB final. Open decisions O3–O16 carried to waves
(O1 resolved S43).

**Style note:** visual layout inspired by the owner's reference architecture images;
all engineering choices are our locked decisions — no content copied from references.

*Session 47 log entry recorded; .html/.pdf rendered from this content plane.*
