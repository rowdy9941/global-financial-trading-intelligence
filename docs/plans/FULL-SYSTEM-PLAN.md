# FULL SYSTEM PLAN — Global Intelligence Ecosystem (working title pending)

> Status: master plan v1 — assembled 2026-10-25 from the full vision discussion
> (source blueprint: `~/Documents/AlphaLens MANI Ecosystem.md` + clarified refinements:
> World Monitor built last in the entire project · Media hub built last in Row 1 ·
> Media consumes ONLY Terminal+Portal outputs, never the engines directly).
> System is built ENTIRELY FROM SCRATCH — no merging of existing projects; prior research
> corpus only informs the design. Final system name: to be revealed by owner.

## 1. The idea in one line

Build a massive interconnected ecosystem — not one product — where every part is fed
by one shared intelligence foundation and in turn supports the others:
one data layer → three parallel engines → two private power stacks → two public money
machines → a content engine that multiplies everything into reach.

**Guiding principle:** One shared intelligence foundation. Three headless engines.
Multiple connected hubs. Two distinct product environments (public + private), with a
dedicated low-latency trading path where justified. Collect once, organize properly,
reuse many times, control access separately.

## 2. Three-row architecture (final composition)

```
ROW 1 — PUBLIC PRODUCTS & MEDIA (approved data only)
   Terminal (Bloomberg-class)   ·   News portal (every niche & domain)
   → Media/content/entertainment hub  [LAST in Row 1 — never touches engines;
                                       consumes Terminal+Portal outputs only]

ROW 2 — THREE HEADLESS INTELLIGENCE ENGINES (the foundation)
   Financial Engine · News Engine · World Engine
   + shared machinery: source registry, schemas+provenance, quality scoring,
     storage lanes, event distribution, APIs, access control/data-rights,
     monitoring · AI-assisted organization (entity resolution, gap
     detection, anomaly detection, source discovery)

ROW 3 — PRIVATE OPERATING LAYER (uses ALL 3 engines, two separate stacks)
   FinOS: quant research → backtest (PIT replay) → risk gateway →
          controlled live execution → portfolio management [first]
   Cockpit/coordination layer: planning, agents, missions, memory,
          approvals, governance, software & automation [not in fast paths]
   World Monitor: fuses ALL 3 engines + personal AI — surveillance &
          investigation surface        [LAST IN THE ENTIRE PROJECT]
```

### Row 1 detail
1. **Terminal** — global markets, instrument scanners, fundamentals, technicals,
   quality scores, historical charts, research, subscription tools, public APIs.
   Consumes all 3 engines directly. Acts as the approved gateway (rights firewall).
2. **News portal** — worldwide news: original summaries, timelines, source refs,
   credibility assessments, related events — across every niche & domain, not
   only finance. Consumes all 3 engines directly.
3. **Media hub (LAST)** — YouTube/Instagram/social/websites/entertainment.
   License-filtered content lives here because the Terminal+Portal boundary already
   enforced data rights; the media layer can never leak restricted data upstream
   by accident. Decoupled from engine uptime (buffer + cache effect).

### Row 3 detail
1. **FinOS (trading & management stack)** — consumes all three engines FOR TRADING
   PURPOSES ONLY: Financial Engine (market/execution path), News Engine (event and
   fundamental catalysts), World Engine (macro/geo signals). Owns quant research,
   backtesting, paper trading, live execution, portfolio + risk management.
2. **Cockpit layer** — unified control environment: missions, projects, agents,
   approvals, knowledge, activity, results, logs. Coordinates the other hubs;
   does not duplicate domain functionality. Never in the market-data or trading
   critical path.
3. **World Monitor ( surveillance & personal intelligence )** — combines
   authorized world data, news, geospatial observations, public event feeds,
   market context per-per-entity/organization/time; dashboard + investigation
   workflows + personal AI on top. Built LAST in the whole project because it is
   a join/association product: it needs every engine mature, entity resolution
   working, and events cross-linked before fusion adds value.

## 3. Non-negotiable architecture rules

| # | Rule |
|---|---|
| R1 | **Collect once, reuse many, control access separately** — every consumer subscribes to validated engine output; no hub ingests raw upstream directly. |
| R2 | **Rights firewall** — Row 1 products are the ONLY gateway from engine data to the public. License classes (`free / internal-only / no-local-store / never-commercial`) are enforced at the boundary, not by convention. |
| R3 | **Intelligence layer is never in fast paths** — the cockpit/planning layer decides what to research and manages workflows; time-sensitive market flow runs deterministic private lanes. |
| R4 | **PIT discipline & survivorship gating** — all factor/backtest data must be as-of-filing/vintage; overwrite-in-place vendors are mechanically fenced; survivorship-correct universes required before any backtest claim. |
| R5 | **Provenance everywhere** — every stored record carries source-URL, fetch time, parser version, rights class (Two-Clicks lineage at schema level). |
| R6 | **Versioned schemas + event contracts** — consumers depend on contracts, not on implementations. |
| R7 | **RL agent deployment gates** — training → evaluation → paper trading → approval → controlled deploy. Agents never modify their own risk limits. |
| R8 | **HFT honesty** — dedicated low-latency subsystem inside FinOS, engineered and benchmarked separately (P50/P95/P99, gaps, recovery); retail-grade feeds are research-grade, not HFT-grade. |

## 4. Communication contracts (the four methods)

| Method | Use | Boundary controls |
|---|---|---|
| Request/response API | point queries ("latest price X", "news between t1..t2") | auth, quotas, rights |
| Streaming subscription | continuous ("send every relevant market update") | per-conn budgets, latency class |
| Event notification | "new filing arrived", "source stopped delivering" | queue perms, retention |
| Bulk transfer | 10-year historical loads for research jobs | throughput caps, never blocks live paths |

Latency engineering targets (benchmark, not claims): in-process Rust µs–sub-ms;
optimized local service sub-ms; local event bus low single-digit ms; indexed
historical query ms–100s ms; AI/verification async (seconds+); bulk = throughput-first.

## 5. Build order (dependency-true, 10 stages)

| Stage | Deliverable | Feeds |
|---|---|---|
| 1 | **Foundation skeleton** — source registry (seeded from the 1,000-dataset research catalogue), schema+provenance contracts, storage lanes (lake/archive + time-series + event log + reference store), event bus, APIs, auth/quotas/data-rights gateway, monitoring | everything |
| 2 | **Financial Engine v0** — collection, archives, realtime ingestion, quality scoring, replay service, query APIs (first payloads: verified free benchmark bundle → India EOD/F&O rail → crypto tails) | FinOS early; Terminal later |
| 3 | **News Engine v0 + World Engine v0** — same skeleton, document/event-shaped payloads; timelines, evidence/credibility scoring; geo/event collection | Row 1, World Monitor, FinOS signals |
| 4 | **AI-assisted organization** — entity resolution, gap detection, anomaly detection, source discovery; the layer where moat-class datasets (registries/legal/IP/clínical/procurement) pay off | all engines; World Monitor prerequisite |
| 5 | **FinOS v0→v1** — research env → backtester on replayed PIT data → paper trading → risk gateway → controlled execution | private revenue + trading capability |
| 6 | **Cockpit layer** — mission/agent/approval/memory machinery over both stacks | operations scale |
| 7 | **Terminal + News portal launch** — public products on approved views; subscriptions, public APIs, editorial workflows | public revenue; fistful for Media |
| 8 | **World Monitor** — LAST in project: fuse all 3 engines per entity/event + personal AI investigation surface | owner's command room |
| 9 | **Media/entertainment hub** — LAST in Row 1: multi-platform content from Terminal+Portal outputs only | reach & brand |
| 10 | **Scale & specialize** — throughput, historical coverage, cross-engine intelligence, RL expansion, HFT path against measured requirements | matures everything |

Sequencing style: vertical slices (one dataset family fully through the stack) rather
than horizontal layer-by-layer across all payloads. World Monitor (8) must follow
FinOS (5); Media (9) must follow Terminal+Portal (7).

## 6. Foundation design decisions (to finalize at Stage 1 kickoff)

- **Workspace shape:** monorepo; Rust core (ingestion, event bus, engine hot paths)
  + Python edge (quant research, analytics). Hard language boundary.
- **Storage lanes:** object-backed parquet lake (archives/L0) · time-series engine
  for tick/intraday (evaluate QuestDB / TimescaleDB / ClickHouse before committing) ·
  event log (NATS or Redis Streams to start; Kafka only at scale) ·
  relational store for registry/reference data · per-workload sizing rule.
- **Source registry seed:** repot the 1,000-row dataset catalogue (12 domain volumes,
  license/verification/latency/history fields) into the registry schema — research
  corpus informs it; the system itself is virgin code.
- **Rights enforcement point:** catalog license class → enforced at ingest + at every
  API/stream boundary (R2).

## 7. Honest risks & standing constraints

1. Blocked-fetch frontier stays honest (anti-bot walls, 403/429s, JS-only portals):
   rows keep `verify`/`quote` actions; the system never silently skips them.
2. Entity resolution is research-grade work — own stage, not a weekend.
3. Single-box fragility: L0 needs a backup story before the 100s-of-GB backfills land.
4. Licensing drift: re-verification cadence on paid/walled rows.
5. Latency claims = engineering targets to benchmark; source delay ≠ our delay.
6. Strategy quality, liquidity, and cost decide trading outcomes; fast ≠ profitable.

## 8. Naming

Final ecosystem name: pending owner reveal. Working names used above (Foundation,
Financial Engine, News Engine, World Engine, FinOS, World Monitor, Cockpit, Terminal,
News portal, Media hub) are placeholders describing responsibilities, to be renamed
uniformly once the system name is announced.
