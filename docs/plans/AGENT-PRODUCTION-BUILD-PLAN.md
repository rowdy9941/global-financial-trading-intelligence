# AGENT PRODUCTION BUILD PLAN — Non-LLM Agent Ecosystem (full in-depth report)

> Final deliverable · 2026-10-26 · assembled from ALL planning sessions (S2–S22) +
> fresh point-by-point web verification (sources in §19). System name: pending owner
> reveal. This is the production-ready, instruction-based build plan for the complete
> agent layer: one deep agent design, all ~30 styles, communication, population sizing,
> memory, training, limits, and the decade-durability architecture.

## 0 · How to read this report (evidence discipline)

- **Every number is sourced or marked as our own budget/target.** Sourced = §19
  evidence appendix with live-verified citations (2026-10-26). "Our budget" = a design
  target we set, not a measured claim.
- **Honest language:** where the research says something is NOT possible (e.g. precise
  regime-duration prediction), this report says so instead of selling it.
- **Plain English first, detail underneath.** Tables carry the density.

---

## 1 · Executive summary — what we are building

**One line:** thousands of tiny, fast, non-LLM specialist agents — one per symbol, one
style each — thinking in microseconds inside a deterministic safety cage, trained
nightly, coordinated by a message bus, governed by a risk governor that has zero
machine learning in it, and all of it wired into seven engines and three rails.

| Layer | What it is | Key rule |
|---|---|---|
| **Cells** (the agents) | one symbol × one style, 5–50M-param models, Rust hot path | never touch own limits (R7) |
| **Family trunks** (6) | equities · futures · options · crypto · FX · predictions | cross-family transfer forbidden |
| **Engines (E1–E7)** | feature · runtime · training · arena · governor · execution · ledger | styles are tenants of engines |
| **Rails (R1–R3)** | signal bus (NATS) · memory (LanceDB) · model registry | audited, need-to-know |
| **Safety** | deterministic governor, certification gates, crisis replays, human ratification | LLMs never in hot/risk path (R3) |

<svg viewBox="0 0 760 330" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI,Arial" font-size="11">
  <rect width="760" height="330" fill="#fbfdff"/>
  <rect x="130" y="8" width="500" height="30" rx="6" fill="#eef4fb" stroke="#2b6cb0"/>
  <text x="380" y="27" text-anchor="middle" fill="#0d1b2a" font-weight="bold">COCKPIT (LLM: explains &amp; reports — never decides, R2/R3)</text>
  <line x1="380" y1="38" x2="380" y2="52" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#ar)"/>
  <rect x="60" y="54" width="640" height="44" rx="6" fill="#fff3f3" stroke="#c53030" stroke-width="2"/>
  <text x="380" y="72" text-anchor="middle" fill="#c53030" font-weight="bold" font-size="12">GOVERNOR + ALLOCATOR (E5 — DETERMINISTIC, ZERO ML)</text>
  <text x="380" y="90" text-anchor="middle" fill="#0d1b2a">caps · drawdown ladders · capital bands · netting · circuit breakers · kill switch</text>
  <rect x="60" y="120" width="640" height="26" rx="6" fill="#f3ecff" stroke="#6b46c1"/>
  <text x="380" y="137" text-anchor="middle" fill="#4c1d95" font-weight="bold">R1 SIGNAL BUS (NATS) — referee: schema ✓ · rate ≤2/min · TTL · trust Δcap · self-deal quarantine</text>
  <text x="55" y="137" text-anchor="middle" fill="#6b46c1" font-size="16">▲</text>
  <text x="715" y="137" text-anchor="middle" fill="#6b46c1" font-size="16">▲</text>
  <rect x="60" y="162" width="140" height="72" rx="6" fill="#eef4fb" stroke="#2b6cb0"/>
  <text x="130" y="184" text-anchor="middle" fill="#2b6cb0" font-weight="bold">R2 MEMORY</text>
  <text x="130" y="202" text-anchor="middle" fill="#0d1b2a">LanceDB episodic</text>
  <text x="130" y="218" text-anchor="middle" fill="#0d1b2a">≤50 µs recall</text>
  <rect x="216" y="162" width="340" height="72" rx="6" fill="#eaf7ef" stroke="#2f855a" stroke-width="2"/>
  <text x="386" y="186" text-anchor="middle" fill="#22543d" font-weight="bold" font-size="12">CELLS — 3,000–6,000 agents (one symbol × one style)</text>
  <text x="386" y="206" text-anchor="middle" fill="#0d1b2a">built from 6 family trunks × 30 archetype spec-sheets</text>
  <text x="386" y="224" text-anchor="middle" fill="#0d1b2a">5–50M-param non-LLM models · Rust hot path · µs decisions</text>
  <rect x="572" y="162" width="128" height="72" rx="6" fill="#eef4fb" stroke="#2b6cb0"/>
  <text x="636" y="184" text-anchor="middle" fill="#2b6cb0" font-weight="bold">R3 REGISTRY</text>
  <text x="636" y="202" text-anchor="middle" fill="#0d1b2a">signed weights</text>
  <text x="636" y="218" text-anchor="middle" fill="#0d1b2a">lineage · rollback</text>
  <rect x="60" y="254" width="640" height="40" rx="6" fill="#fff8e6" stroke="#b7791f" stroke-width="2"/>
  <text x="380" y="272" text-anchor="middle" fill="#744210" font-weight="bold">DATA SPINE (R1 rule) — Arrow/Parquet one-copy archive · as-of truth (no look-ahead)</text>
  <text x="380" y="288" text-anchor="middle" fill="#0d1b2a">E1 feature engine → E2 runtime → E4 arena → E3 training → E6 execution → E7 ledger</text>
  <text x="380" y="322" text-anchor="middle" fill="#5a6a7a" font-size="10">Fig 1 — the agent ecosystem: cells think locally in µs, safety lives in the deterministic cage above them</text>
  <defs><marker id="ar" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#0d1b2a"/></marker></defs>
</svg>

**The four answers people ask first:**

1. **Can it find alpha by itself?** Yes — bounded self-discovery inside its spec-sheet
   IS the agent's job (Sessions 20–21). It can also invent new strategy *combinations*
   (Invention Lane) — but never new rules.
2. **Is one generalist agent per symbol better?** No — verified against research
   (negative transfer) and industry (the pod model): specialists win (Session 22).
3. **Will it make profit?** Not promised. Most candidates fail their gates — that is
   why the gates exist. The plan optimizes for *surviving* reality, not backtest curves.
4. **How long does it last?** Designed for a decade: event-sourced everything, versioned
   contracts, rolling trunk refresh, swappable edges (§16).

---

## 2 · The complete agent — anatomy, deep dive

### 2.1 The skeleton (identical for every agent of every class)

```
                    ┌────────── OFFLINE (minutes–hours, Python/JAX, never live) ──────────┐
                    │  data → features → train (SB3/PyTorch/JAX) → eval-CI → arena race   │
                    │  → crisis replays → DSR gate → shadow → HUMAN gate → registry       │
                    └───────────────────────────────┬─────────────────────────────────────┘
                                                    │ signed weights (ONNX) + version
┌────────────────────────── HOT PATH (Rust, inference-only, µs) ──────────────────────────┐
│ Arrow mmap bus → obs builder (as-of fields) → policy core (trunk+head, ONNX/tract)      │
│ → conformal calibrator → DETERMINISTIC risk gateway (caps/ladders/bans — no ML)        │
│ → Head Contract {action_typed, size, confidence, ttl} → unified order model            │
│ → venue adapter → exchange                                        every hop CI-timed │
└────────────────────────────────────────────────────────────────────────────────────────┘
        ↑ ring buffer with skip-tags (C4) · every decision logged to the ledger (D3)
```

<svg viewBox="0 0 760 210" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI,Arial" font-size="10.5">
  <defs><marker id="a2" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#0d1b2a"/></marker>
  <marker id="a3" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#2f855a"/></marker></defs>
  <rect width="760" height="210" fill="#fbfdff"/>
  <rect x="10" y="46" width="88" height="52" rx="5" fill="#eef4fb" stroke="#2b6cb0"/>
  <text x="54" y="66" text-anchor="middle" font-weight="bold" fill="#2b6cb0">Arrow bus</text>
  <text x="54" y="82" text-anchor="middle">mmap obs</text>
  <text x="54" y="94" text-anchor="middle" font-size="9">&lt;10 µs</text>
  <line x1="98" y1="72" x2="116" y2="72" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#a2)"/>
  <rect x="118" y="46" width="96" height="52" rx="5" fill="#eef4fb" stroke="#2b6cb0"/>
  <text x="166" y="66" text-anchor="middle" font-weight="bold" fill="#2b6cb0">Obs builder</text>
  <text x="166" y="82" text-anchor="middle">as-of fields</text>
  <text x="166" y="94" text-anchor="middle" font-size="9">A1 schema v</text>
  <line x1="214" y1="72" x2="232" y2="72" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#a2)"/>
  <rect x="234" y="46" width="104" height="52" rx="5" fill="#eaf7ef" stroke="#2f855a"/>
  <text x="286" y="66" text-anchor="middle" font-weight="bold" fill="#22543d">Policy core</text>
  <text x="286" y="82" text-anchor="middle">trunk+head ONNX</text>
  <text x="286" y="94" text-anchor="middle" font-size="9">&lt;100 µs</text>
  <line x1="338" y1="72" x2="356" y2="72" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#a2)"/>
  <rect x="358" y="46" width="92" height="52" rx="5" fill="#eef4fb" stroke="#2b6cb0"/>
  <text x="404" y="66" text-anchor="middle" font-weight="bold" fill="#2b6cb0">Conformal</text>
  <text x="404" y="82" text-anchor="middle">calibrated conf</text>
  <text x="404" y="94" text-anchor="middle" font-size="9">ECE&lt;0.02</text>
  <line x1="450" y1="72" x2="468" y2="72" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#a2)"/>
  <rect x="470" y="40" width="118" height="64" rx="5" fill="#fff3f3" stroke="#c53030" stroke-width="2.5"/>
  <text x="529" y="60" text-anchor="middle" font-weight="bold" fill="#c53030">RISK GATEWAY</text>
  <text x="529" y="76" text-anchor="middle" font-size="9.5">deterministic rule table</text>
  <text x="529" y="90" text-anchor="middle" font-size="9.5">caps · bans · ladders · ZERO ML</text>
  <line x1="588" y1="72" x2="606" y2="72" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#a2)"/>
  <rect x="608" y="46" width="70" height="52" rx="5" fill="#eef4fb" stroke="#2b6cb0"/>
  <text x="643" y="64" text-anchor="middle" font-weight="bold" fill="#2b6cb0">Head</text>
  <text x="643" y="76" text-anchor="middle" font-size="9">Contract</text>
  <text x="643" y="88" text-anchor="middle" font-size="8.5">{act,size,conf,ttl}</text>
  <line x1="678" y1="72" x2="696" y2="72" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#a2)"/>
  <rect x="698" y="46" width="56" height="52" rx="5" fill="#fff8e6" stroke="#b7791f"/>
  <text x="726" y="66" text-anchor="middle" font-weight="bold" fill="#744210">Order</text>
  <text x="726" y="80" text-anchor="middle" font-size="9">model→venue</text>
  <rect x="234" y="128" width="380" height="44" rx="5" fill="#f3ecff" stroke="#6b46c1" stroke-dasharray="4 3"/>
  <text x="424" y="146" text-anchor="middle" fill="#4c1d95" font-weight="bold">OFFLINE LANE (never in the loop): E3 train → E4 gates → registry → signed ONNX</text>
  <text x="424" y="163" text-anchor="middle" fill="#0d1b2a">nightly weights swap only at session-open (D1) · 5-min no-new-positions cooldown</text>
  <line x1="424" y1="128" x2="424" y2="100" stroke="#6b46c1" stroke-width="1.5" stroke-dasharray="4 3" marker-end="url(#a3)"/>
  <text x="380" y="198" text-anchor="middle" fill="#5a6a7a" font-size="10">Fig 2 — one decision, start to finish: proposal → calibration → deterministic gate → typed contract. No language anywhere.</text>
</svg>

**What differs between agents is exactly three things:** action space, reward formula,
auxiliary learned models. Everything else is shared code — **no new codebase per style.**

### 2.2 Structure — the three-layer identity

| Layer | Learns | Lifetime | Example |
|---|---|---|---|
| **Family trunk** | market physics of ONE family (order-flow shapes, session rhythm, vol structure) | refreshed yearly | the futures trunk |
| **Archetype spec-sheet** | the payoff problem (frozen design: obs blocks, action, reward, hazards, champion) | changed only by owner ratification | theta-harvest |
| **Cell instance** | that instrument's personality (when IT gaps, fills, fades) | retrained nightly | NIFTY-weekly-theta cell |

### 2.3 How it *thinks* (one decision, start to finish)

1. **Sees** a typed observation (A1): market block (~60 scalars) + position block +
   signal block (scoped peer signals) + regime block + hazard block. Every field
   carries an availability timestamp — future data is physically impossible to read.
2. **Measures** its trunk embedding of the current situation and (≤50 µs) recalls the
   top-8 similar past episodes from its own LanceDB — *measured outcomes only, no
   narratives* (anti-hallucination by construction).
3. **Decides** with its policy core: a fast neural map obs→action proposal. Output is
   a *proposal*, never an order.
4. **Calibrates** the confidence: conformal wrapper turns raw scores into honest
   P(true) intervals (ECE < 0.02 gate per regime).
5. **Gates** through the deterministic gateway: position caps, drawdown ladder,
   eligibility checks (C1), staleness checks (C4), ban lists, circuit proximity.
   Zero ML — a fixed rule table.
6. **Emits** the Head Contract and the order model splits it into venue orders (TWAP/
   VWAP/POV when size demands).
7. **Records** everything: fills, skips, counterfactual leg P&L → per-cell ledger (D3).

There is **no language, no chat, no free text** anywhere in this loop. That is the
whole point (§7).

### 2.4 The framework stack (what runs where)

| Tier | Tool | Runs | Rule |
|---|---|---|---|
| Hot path | Rust + ONNX Runtime / tract | live, µs | inference only, no Python, no GC pauses |
| Training | Stable-Baselines3 (reliability harness), PyTorch, pycma (evolution) | nightly GPU | training-time only — deployed artifact is a compiled model file |
| Research/scale tier | JAX (GPU rollouts, JaxMARL-class) | optional P4 | adopt when population > hundreds of cells |
| Eval | replay arena + DSR/PSR + crisis kits | CI on every merge | seed-invariance, latency CI, adversarial audit |
| Orchestration | NATS JetStream · PostgreSQL · LanceDB · Arrow/Parquet | always | rails, not brains |

### 2.5 Infrastructure — the seven engines (Session 18)

| Engine | Job | Language |
|---|---|---|
| E1 Feature | computes obs blocks on the hot path | Rust |
| E2 Runtime | loads/runs models, conformal calibration | Rust + ONNX |
| E3 Training | nightly trains/evolves candidates | Python + GPU |
| E4 Arena | replay evaluation, champion races, crisis kits | Python (deterministic) |
| **E5 Governor** | caps, ladders, kill switches, allocator | **Rust, ZERO ML by design** |
| E6 Execution | TWAP/VWAP/POV + venue adapters | Rust |
| E7 Ledger | per-cell P&L, reconciliation, tax lane | Rust + Postgres |
| R1/R2/R3 | signal bus / memory store / model registry | NATS / LanceDB / registry |

### 2.6 How it is built (birth → life → death)

```
symbology master picks symbol → data backfill (as-of, PIT-certified)
→ cell BIRTH (A6: ≥20 sessions tape or seeded head from nearest family sibling)
→ reduced capital 0.25× for 30 sessions
→ nightly: E3 trains → E4 races vs champion → crisis replays → DSR gate
→ SHADOW (live decisions, no money) until decision-parity ≥98%
→ HUMAN approval gate → small band capital → monitored → champion races continue
→ underperformer: retirement proposal (B1) → freeze → archive → capital recycled
```

Every step is logged; every gate is a CI job; nothing enters live money without a
human signature (R7).

<svg viewBox="0 0 760 300" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI,Arial" font-size="10.5">
  <defs><marker id="a4" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#0d1b2a"/></marker>
  <marker id="a5" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#c53030"/></marker></defs>
  <rect width="760" height="300" fill="#fbfdff"/>
  <rect x="40" y="14" width="150" height="34" rx="5" fill="#eef4fb" stroke="#2b6cb0"/><text x="115" y="35" text-anchor="middle" font-weight="bold">symbology + backfill</text>
  <line x1="115" y1="48" x2="115" y2="60" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#a4)"/>
  <rect x="40" y="62" width="150" height="34" rx="5" fill="#eaf7ef" stroke="#2f855a"/><text x="115" y="78" text-anchor="middle" font-weight="bold" fill="#22543d">BIRTH (A6)</text><text x="115" y="91" text-anchor="middle" font-size="9">≥20 sessions · 0.25× capital</text>
  <line x1="115" y1="96" x2="115" y2="108" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#a4)"/>
  <rect x="40" y="110" width="150" height="34" rx="5" fill="#eef4fb" stroke="#2b6cb0"/><text x="115" y="131" text-anchor="middle" font-weight="bold">E3 nightly train</text>
  <line x1="115" y1="144" x2="115" y2="156" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#a4)"/>
  <polygon points="115,158 205,186 115,214 25,186" fill="#fff8e6" stroke="#b7791f"/>
  <text x="115" y="183" text-anchor="middle" font-weight="bold" fill="#744210">E4 arena race</text>
  <text x="115" y="196" text-anchor="middle" font-size="9">+ DSR ≥0.9 + crisis kits</text>
  <line x1="205" y1="186" x2="238" y2="186" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#a4)"/>
  <rect x="240" y="169" width="130" height="34" rx="5" fill="#eaf7ef" stroke="#2f855a"/><text x="305" y="185" text-anchor="middle" font-weight="bold" fill="#22543d">SHADOW</text><text x="305" y="198" text-anchor="middle" font-size="9">parity ≥98%, no money</text>
  <line x1="370" y1="186" x2="403" y2="186" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#a4)"/>
  <rect x="405" y="169" width="150" height="34" rx="5" fill="#fff3f3" stroke="#c53030" stroke-width="2"/><text x="480" y="190" text-anchor="middle" font-weight="bold" fill="#c53030">HUMAN GATE (R7)</text>
  <line x1="555" y1="186" x2="588" y2="186" stroke="#0d1b2a" stroke-width="1.5" marker-end="url(#a4)"/>
  <rect x="590" y="169" width="130" height="34" rx="5" fill="#eaf7ef" stroke="#2f855a" stroke-width="2"/><text x="655" y="185" text-anchor="middle" font-weight="bold" fill="#22543d">LIVE (band $)</text><text x="655" y="198" text-anchor="middle" font-size="9">monitor · attribute</text>
  <path d="M655 203 L655 244 L115 244 L115 216" fill="none" stroke="#2b6cb0" stroke-width="1.5" stroke-dasharray="4 3" marker-end="url(#a4)"/>
  <text x="385" y="238" text-anchor="middle" fill="#2b6cb0" font-weight="bold">nightly loop: retrain ⇄ champion race ⇄ rollback (bit-exact)</text>
  <path d="M205 200 L238 244 L115 244" fill="none" stroke="#c53030" stroke-width="1.5" stroke-dasharray="3 3"/>
  <text x="176" y="262" text-anchor="middle" fill="#c53030" font-size="9.5">fail → candidate rejected, incumbent stays</text>
  <rect x="405" y="252" width="315" height="34" rx="5" fill="#f3ecff" stroke="#6b46c1"/>
  <text x="562" y="273" text-anchor="middle" fill="#4c1d95" font-weight="bold">monthly: retirement proposal (B1) → freeze → archive → capital recycled</text>
  <text x="380" y="296" text-anchor="middle" fill="#5a6a7a" font-size="10">Fig 3 — the lifecycle: gates are mechanical, the only human step is the promotion signature</text>
</svg>

---

## 3 · One symbol, massive data, massive training — how far does it go?

### 3.1 What "massive data" means per cell (and what the universe actually is)

Verified universe sizes (2026-10-26, §19):

| Universe | Count | Source |
|---|---|---|
| Listed companies worldwide | **59,581** | WFE Dashboard Oct 2026 |
| Global equity symbols (incl. ETFs/funds) | ~340k+ | OneTick coverage (240k equities, 100k ETFs) |
| Global futures contracts | ~190k (140k CME alone) | OneTick / TrendSpider |
| Listed options (US/global) | **4.3M–6M active** | OneTick / LSEG |
| Crypto markets | **30,537** across 8,468 coins (3,519 perps) | WorldCoinIndex Oct 2026 |
| Crypto spot/futures/options markets | 21k / 10k / 14k | CoinMetrics coverage |
| FX liquid pairs | ~40–70 | Stooq major+minor |

**But we do not need all of them.** The liquid-first roster (our actual target):
~2,000–5,000 equities · ~300 futures · top-underlying options chains · ~200 crypto
pairs · ~40 FX · ~100–500 prediction markets ⇒ **first-decade population ≈ 3,000–6,000
cells**, full-world coverage is a scaling decision, not a day-one requirement (§13).

### 3.2 Per-symbol data depth — how deep one agent can pull

One cell can legitimately consume: full tick/LOB history · trades · quotes · funding &
open interest (crypto) · earnings/event calendars · sector & index co-moves ·
cross-venue prints · news-tension features (as a *feature*, not a decision-maker) ·
corporate-action-adjusted fundamentals · option surface slices (options cells) ·
liquidity/holiday calendars. All flowing through the **one Arrow spine** — no copies,
no re-serialization (DATA-LAYER doc).

### 3.3 The honest limit: more data ≠ more edge

Three verified truths keep us honest:
1. **Information density decays with horizon** — tick data helps an MFT cell most;
   daily fundamentals help a carry cell most. Dumping HFT data on a theta cell adds
   noise, not signal. That is why the spec-sheet picks the obs blocks.
2. **Regime-duration prediction is NOT solved** — a 2026 study (§19) showed survival
   models trying to predict "how long does this regime last" fail structurally
   (concordance 0.20–0.39 ≈ random). We therefore predict regime *state* + dwell
   confidence, never precise duration (§11).
3. **Training scales with compute, not wishes** — FinRL-Podracer trained a
   NASDAQ-100 agent over 10 years of minute data in **10 minutes on 80×A100** (§19);
   our nightly window on 1–2 GPUs with 5–50M-param models is minutes-to-hours per
   cell-batch — plenty, because our cells are small and data is pre-chunked.

### 3.4 First training (cold start) and nightly retraining

| Phase | What happens | Speed evidence |
|---|---|---|
| **First train** | seeded head (A6) + 20 sessions minimum tape → supervised pretrain on replayed outcomes → offline RL fine-tune → arena race | hours on one GPU (small models) |
| **Nightly retrain** | sampling ladder: 30d 50% · 90d 30% · 12mo 15% · whole-history 5% (A3) → candidate → gates | minutes–hours; end-to-end retrain orchestration saves 4–6h cycles in production MLOps (§19) |
| **Intraday adaptation** | NOT weight changes — config/hazard level changes only (D1: weights = session boundary, always) | sub-ms per-sample online update exists in literature; we deliberately don't use it live |
| **Evolution** | pycma mutates feature/param variants nightly; Invention Lane composes strategies (§4) | generational evolution every ~800s shown feasible (FinRL-Podracer) |

---

## 4 · The self-improvement machine (how it updates, upgrades, versions, and stays sane)

### 4.1 The four loops (different speeds, different permissions)

| Loop | Cadence | What changes | Permission |
|---|---|---|---|
| **Fast loop** | per event | nothing — inference only | automatic |
| **Learning loop** | nightly | weights, feature weights, calibrators | automatic behind gates |
| **Evolution loop** | nightly/weekly | parameter variants, feature sets, Invention-Lane compositions | automatic into arena; promotion needs gates + human |
| **Structural loop** | monthly/quarterly | spec-sheet proposals, new archetypes, trunk refresh | **owner ratification only** |

### 4.2 Versions — everything is versioned (the answer to "can it has version?")

`obs-schema v` · `feature-registry v` · `reward-spec v` · `weights v` (content-hashed,
signed) · `config v` · `lineage manifest` (training vintage, data window, trials count)
· `spec-sheet v`. Session-open is the single atomic swap point (D1); any live change
forces a 5-minute no-new-positions cooldown; rollback = flip pointer to the previous
bit-exact champion. Version numbers ride *inside* the observation so a head can never
consume features from a mismatched schema.

### 4.3 Why it does not hallucinate (the honest engineering answer)

"Better understanding without hallucination" is not a wish — it is a consequence of
design choices, each verified against how LLMs fail (§19):

| LLM failure | Our countermeasure |
|---|---|
| free-text reasoning invents facts | **no language in the loop at all** — tensors in, typed contract out |
| numeric hallucination (a wrong delta/risk figure) | every number comes from deterministic math libraries (Black-Scholes, Kalman, PCA) — the model only *chooses among measured options* |
| memory contamination (narrative drift) | episodic store holds **measured trade outcomes only** — no summaries, no prose |
| non-reproducible outputs | content-hashed weights + seed-invariance CI: same seed ⇒ same weights, bit-exact |
| uncalibrated confidence | conformal calibration gate (ECE < 0.02 per regime) — a number that claims 80% must be right ~80% |

### 4.4 "Consciousness on the data" — the honest reframing

There is no sentience, and we do not need it. What the owner is describing has three
real engineering names: **state estimation** (the regime block + hazard block = a
continuously updated picture of *what kind of market this is right now*), **attention**
(obs-scoping: the spec-sheet decides what matters; the governor strips what the cell
may not see), and **calibrated self-knowledge** (the agent's confidence is measured,
not felt — and a miscalibrated agent fails its gate). A decade of "understanding"
without one gram of consciousness — and every decision remains auditable, which a
consciousness could never promise.

---

## 5 · The rules we give the agent (the complete rulebook)

### 5.1 The eight non-negotiables (R1–R8)

| # | Rule | What it forbids |
|---|---|---|
| R1 | One shared data spine, as-of truth only | look-ahead, duplicate stores |
| R2 | LLMs live ONLY in cockpit/research | any LLM in hot/risk path |
| R3 | Deterministic governor owns risk | learned risk logic, agent-touched limits |
| R4 | Build order follows dependency truth | parallel fantasy work |
| R5 | Certification before capital | unproven candidates trading money |
| R6 | Everything measured, everything logged | silent failure, unauditable decisions |
| R7 | **Agents never touch their own limits** | self-modifying risk, reward hacking of constraints |
| R8 | Human ratification for structural change | autonomous spec-sheet/species evolution |

### 5.2 Per-agent standing constraints (the cage around every cell)

- **Action contract** `{action_typed, size, confidence, ttl}` — nothing else can be
  emitted; ttl expiry = proposal dies, never executes stale.
- **Capital band** set by allocator; hard caps (max position, max loss/day, max
  participation, drawdown ladder) enforced by E5 — *before* the order exists.
- **Eligibility gates** (C1): ban lists, circuit proximity, margin class, calendar —
  governor blocks ineligible instruments with zero discretion.
- **Staleness gates** (C4): drop-tags travel with data; staleness ⇒ cancel quotes,
  reduce-only; sustained drops ⇒ governor pauses the cell.
- **Hazard gates** per class: expiry rules (C3), spoofing score (C2), poison-tape
  detector (MM), one-sided pausing.
- **Bus etiquette** (B3): ≤2 signals/min/type, TTL freshness, bounded trust deltas,
  self-dealing quarantine.
- **Isolation** (B4): a cell NEVER sees other cells' positions/capital/orders — scoped
  signals only, stripped at the gateway before the cell reads anything.

### 5.3 What the agent is *allowed* to change by itself

| Thing | Self-change? |
|---|---|
| weights / feature weights / calibrators (nightly, behind gates) | ✅ automatic |
| parameter variants, feature sets (evolution loop) | ✅ into arena only |
| strategy compositions (Invention Lane) | ✅ propose → same gates → **human ratifies** |
| reward formula, risk limits, gateway logic, action contract | ❌ never (R7/R8) |

---

## 6 · What goes wrong — the complete failure catalog (and the recipe that succeeds)

### 6.1 The 12 engineered defenses (from the master design)

| # | Failure | Defense |
|---|---|---|
| 1 | non-stationarity | regime detectors in obs, rolling windows, DD de-risk ladder |
| 2 | overfitting | CPCV + purge/embargo, DSR with trials-logged, parameter budgets |
| 3 | look-ahead | as-of-only channel, physically enforced at data layer |
| 4 | survivorship | membership-at-date universe, certified else refuses to run |
| 5 | data leakage | time+cross-instrument purge, feature-version isolation |
| 6 | regime break live | governor capital cut + correct-head swap, dwell confirm |
| 7 | reward hacking | independent audit metrics read by committee, versioned reward |
| 8 | training instability | seed-invariance CI, grad-clip, NaN-watch, min-stability epochs |
| 9 | MM tail inventory | A-S core + bounded nets, auto-widen, one-sided pause |
| 10 | execution-cost drift | shadow IS vs sim ±20% ⇒ freeze & recalibrate |
| 11 | cross-agent crowding | overlap score + systemic circuit breaker |
| 12 | calibration rot | per-regime ECE recheck post-deploy |

### 6.2 "Training itself may go wrong and cause losses" — yes, and here is exactly how

| Training failure | How it shows up | What stops it |
|---|---|---|
| trained on a lucky window | great replay, awful live | DSR trials-deflation, crisis kits, vintage-matched arena (A5) |
| learned the wrong regime | confident in the wrong market | regime-stratified eval buckets; preservation gate (A3) — candidate may not degrade older-regime buckets |
| catastrophic forgetting | forgets 2020 skills after 2026 data | sampling ladder + frozen regime heads + ancient-window CI probe |
| reward hacking | maximizes metric, destroys money | independent audit metric set (TCA, turnover-adj PnL), committee-read |
| non-deterministic training | candidate unreproducible | seed-invariance gate — same seed ⇒ same weights or the run is invalid |
| poisoned data | garbage features | PIT certification, poison-tape detector, backfill QA gates |
| cost model wrong | sim says profit, live bleeds | MM fills calibrate cost model; ±20% shadow tolerance → freeze |

**The "successful model" recipe (what a certified agent actually is):**
expectancy-after-costs positive per regime bucket · DSR ≥ 0.9 with logged trials ·
beats its deterministic champion on matched folds · survives the 5-crisis replay
suite (blind windows) · ECE < 0.02 per regime · shadow decision-parity ≥ 98% ·
realized cost within ±20% of sim · latency P99 inside budget · human signature.
**That list IS the definition of a good agent.** A model trained without it is not a
failed model — it is an *untested* one, and untested models never touch money.

### 6.3 The honest risk statement

Most candidates will fail these gates. Some cells will be retired. No architecture
guarantees profit — what this architecture guarantees is that losses stay bounded,
explainable, and survivable, and that the system *keeps the winners* (Lo's Adaptive
Markets logic: edges decay, the population refills).

---

## 7 · Why non-LLM beats LLM for this job (verified, 2026 evidence)

| Dimension | LLM agent | Our non-LLM agent | Evidence (§19) |
|---|---|---|---|
| **Decision cycle** | 15–40 seconds | µs–ms internal; MFT decisions at signal cadence | MethodAlgo 2026: 5–6 orders of magnitude gap; <5% of short-term signal value remains after 20s |
| **Cost per decision** | ~$0.12 inference | ~$0 (local compiled model) | MethodAlgo 2026 |
| **Numeric honesty** | "hallucinates pretty egregiously… extreme errors in quantitative settings" | deterministic math libs; model only selects measured options | DL News 2025 (Allora) |
| **Reproducibility** | non-deterministic outputs — MiFID II / SEC 15c3-5 incompatible | bit-exact, seed-invariant, signed weights | MethodAlgo 2026 compliance thesis |
| **Self-revision safety** | deployed LLM agent: operator had to remove 11 of 19 self-added rules, median 8-day lag | self-revision limited to weights behind gates; rules are constitution | AllPath 2026 production incident taxonomy |
| **Extreme markets** | API rate-limits/outage risk exactly when it matters | local, no external dependency | MethodAlgo 2026 |
| **Best role** | research proposer, explainability cockpit, attribution narration | **trading principal** | our R2/R3 rule — matches the literature's own recommendation |

**Where the LLM stays:** research-lane strategy proposals (Invention Lane), morning-report
narration, owner cockpit Q&A — read-only, never deciding, never in the risk path.

---

## 8 · Latency, throughput, accuracy — the numbers (sourced vs ours)

### 8.1 What the world actually achieves (verified)

| System | Tick-to-trade | Notes |
|---|---|---|
| NanoARB (Rust, CME HFT) | **median 780 ns · p99 1.2 µs** | LOB 45ns + features 120ns + inference 580ns, ONNX, AMD EPYC (§19) |
| RustAlgorithmTrading | book updates <5 µs p99 · 100k+ msgs/s | Rust, Ryzen 9 |
| QuantMemo worked example | 10 µs full budget (market making) | parse 1.5 + features 3 + model 3.5 + risk 1 + send 1 |
| LLM trading agent | 15–40 **seconds** | the contrast that justifies our whole architecture |

<svg viewBox="0 0 760 235" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI,Arial" font-size="10.5">
  <rect width="760" height="235" fill="#fbfdff"/>
  <text x="20" y="20" fill="#0d1b2a" font-weight="bold" font-size="11.5">Latency comparison (log scale — every step = 10×)</text>
  <line x1="150" y1="188" x2="740" y2="188" stroke="#0d1b2a" stroke-width="1.2"/>
  <g fill="#5a6a7a" font-size="9">
    <line x1="150" y1="188" x2="150" y2="193" stroke="#5a6a7a"/><text x="150" y="205" text-anchor="middle">1 µs</text>
    <line x1="297" y1="188" x2="297" y2="193" stroke="#5a6a7a"/><text x="297" y="205" text-anchor="middle">1 ms</text>
    <line x1="443" y1="188" x2="443" y2="193" stroke="#5a6a7a"/><text x="443" y="205" text-anchor="middle">1 s</text>
    <line x1="590" y1="188" x2="590" y2="193" stroke="#5a6a7a"/><text x="590" y="205" text-anchor="middle">100 s</text>
  </g>
  <rect x="150" y="34" width="43" height="22" fill="#2f855a"/><text x="199" y="49" fill="#0d1b2a">NanoARB full tick-to-trade — 780 ns (verified)</text>
  <rect x="150" y="64" width="74" height="22" fill="#2b6cb0"/><text x="230" y="79" fill="#0d1b2a">Rust book update — &lt;5 µs p99 (verified)</text>
  <rect x="150" y="94" width="88" height="22" fill="#2b6cb0"/><text x="244" y="109" fill="#0d1b2a">QuantMemo full MM budget — 10 µs (verified)</text>
  <rect x="150" y="124" width="146" height="22" fill="#b7791f"/><text x="302" y="139" fill="#0d1b2a">OUR internal budget — &lt;100 µs p99 (target, CI-gated)</text>
  <rect x="150" y="154" width="565" height="22" fill="#c53030"/><text x="470" y="169" fill="#fff" text-anchor="middle" font-weight="bold">LLM trading agent — 15–40 SECONDS (verified): signal is dead on arrival</text>
  <text x="380" y="228" text-anchor="middle" fill="#5a6a7a" font-size="10">Fig 4 — why the trading principal is non-LLM: 5–6 orders of magnitude separate the two worlds</text>
</svg>

### 8.2 Our budgets (targets we set — to be CI-enforced, not yet measured)

| Hop | Our budget (p99) |
|---|---|
| Arrow mmap obs read | < 10 µs |
| policy core inference (5–50M params, quantized) | < 100 µs |
| conformal + deterministic gateway | < 20 µs |
| order model + venue adapter | < 50 µs |
| **internal total** | **< 100 µs** (already written into the master doc's latency-CI gate) |
| venue hop (network + exchange) | outside our control — measured per venue, priced into cost model |

**Decisions per second, operations per second (honest math):**
- Hot loop processes **every book update** — thousands of events/s per liquid cell are
  trivial at µs each (100k+ msg/s verified in comparable Rust systems).
- An MFT cell *acts* orders far less often: signals arrive seconds-to-minutes apart;
  order rate is bounded by venue rate limits anyway. Speed is for *seeing and gating*,
  not for spamming orders.
- **Accuracy we can promise:** calibration accuracy (a stated 80% must be ~80%) —
  NOT price-prediction accuracy. Anyone promising prediction accuracy is selling
  something; our edge metric is expectancy-after-costs per regime (Metrics Bible §5).

---

## 9 · How the agents communicate (and is it better or messy?)

### 9.1 The design: a refereed signal bus, not an agent free-for-all

```
cell A ──signal{type,value,conf,ttl}──▶ ┌──────────── R1 BUS GATEWAY ────────────┐
cell B ──signal{...}───────────────────▶ │ schema ✓ · rate ≤2/min · TTL fresh     │
cell C ──signal{...}───────────────────▶ │ dedup · trust Δcap/day · self-deal chk │
                                         └───────────────┬───────────────────────┘
                    scoped, need-to-know filtered signals ▼
        cells (obs.signal_block) · allocator (aggregate) · governor (overlay)
   ◀── NO positions · NO capital · NO orders ever cross this bus (B4) ──▶
```

**What is shared (and why):** signed belief signals — {type, value, calibrated
confidence, ttl, trust weight} — e.g. "event-cell: earnings surprise incoming, conf
0.71, ttl 40 min". Enough for a peer to *condition* its own view; never enough to
copy another's book.

**What is never shared:** positions, capital, orders, limits. Only the governor sees
aggregate exposure. The auditor gets a signed, logged exception view.

### 9.2 Is communication a net positive? (the verdict)

| | Without bus (isolated) | With bus (our design) |
|---|---|---|
| cross-style coordination (MM hedged by directional view) | impossible | **at the coordinator layer** — visible, auditable |
| contagion / spam / collusion risk | n/a | bounded by referee: rate caps, TTL, trust deltas, self-dealing quarantine |
| attribution | clean | stays clean — signals carry source + trust, never merge into one skull |
| failure mode if a cell goes bad | contained | contained — quarantine freezes its trust, incident logged |

**Verdict: better, not messy — because the bus carries *claims with receipts*, not
conversations.** The failure mode of agent-to-agent chatter (emergent nonsense,
collusion, feedback loops) is structurally excluded: datagram signals, audited,
rate-limited, need-to-know scoped. Evidence for the messaging substrate: NATS handles
**millions of messages/second at sub-millisecond latency**, single binary, JetStream
adds durable streams + replay + at-least-once (§19) — i.e. the bus is a solved
problem at our scale.

---

## 10 · How fast does it learn, adopt, and let go? (adaptation speed)

The agent has **three speeds of change**, each with a verified detector behind it:

| Loop | Trigger | Mechanism | Speed (design) |
|---|---|---|---|
| **Detect "this is going bad"** | continuous | drift detectors on the cell's own outcome stream: **CUSUM / Page-Hinkley** (fast triggers on small persistent shifts), **ADWIN** (adaptive window compares old vs new averages), DDM/EDDM (error-rate monitors) — the standard, peer-reviewed family, already in our research map (Page 1954) | detector fires within tens–hundreds of observations after a real shift; governor acts on confirmed breach, not flicker |
| **Leave the bad thing** | detector + gates | capital ladder steps down (deterministic), hazard level rises, candidate search widens; persistently negative cells → retirement proposal (B1) | same session for capital cuts; days–months for full retirement (with human confirm) |
| **Adopt the good thing** | nightly arena | new candidate must beat champion on matched windows → shadow → human gate | best case: ~2 days (nightly train → next-session shadow → approval); never instant, on purpose |
| **Learn a whole new symbol** | birth | seeded head + 20 sessions tape + reduced capital 30 sessions | weeks to full band |

**The honest asymmetry: losing is fast, winning is slow.** Cutting risk is a
deterministic table lookup (no ML, no approval for *tightening* — D1 monotonic rule:
safety may tighten immediately, loosening waits for session boundary). Gaining trust
requires passing every gate. That asymmetry is the entire survival strategy.

Evidence note: concept-drift detection is a mature field (CUSUM 1954, ADWIN, PH,
KSWIN, BOCD all current in 2026 libraries — §19); the parameter trade-off (false
alarm vs missed detection) is real and is tuned per cell class in E4, not hand-waved.

---

## 11 · Regimes: how it knows *what market this is* — and the honest limit on *how long*

### 11.1 Detection (what we CAN do well)

The regime block in every obs (A1) is fed by a detector stack:

1. **HMM / Markov-switching detectors** (Hamilton 1989) — discrete states (calm /
   trend / stress / transition) with posterior probabilities, per family.
2. **CUSUM-class fast triggers** — quick flag on mean/variance shifts (the "something
   changed" alarm).
3. **Structural features** — realized-vol level, correlation breakdown, liquidity
   depth, cross-asset moves, session phase → a feature vector the detector consumes.
4. **Dwell-time discipline** — the governor only *acts* on a regime state after
   dwell confirmation (master doc §4.3: allocation changes only when regime state
   confirms, not on detector flicker).

### 11.2 Duration — the honest answer to "will this regime run hours, days, months?"

**We can estimate persistence, not predict duration.** Verified evidence (§19): a
2026 study tried exactly this — Weibull survival models on 54 bearish regimes across
8 asset classes — and failed structurally: concordance 0.20–0.39 (worse than random),
flat with more training data, and predictions collapsing to boundary values. Their
conclusion: HMM covariates carry **no duration-predictive information** under those
models.

What we do instead (and what the industry actually does):
- **Stay-probability + hazard shaping**, not point forecasts: "state X has dwell
  12 days median historically, current posterior stay = 0.83" → a *confidence-weighted
  persistence prior*, consumed by the allocator's dwell gate.
- **Scenario fan, not a date**: crisis kits + Monte-Carlo-class replays produce a
  distribution of continuations; capital is sized against the *bad* tail of that fan.
- **Cheap insurance**: the drawdown ladder de-risks automatically regardless of what
  the regime predictor believes. The regime block informs; the ladder decides.

### 11.3 Dynamic response — "change everything based on the regime instantly"

Instant *trading* response = already in obs (every policy sees regime state every
event; hazard gates fire immediately). Instant *structural* response = deliberately
NO: strategy swaps, spec-sheet changes, and weight swaps happen at session boundaries
(D1). The regime block can flip a cell to reduce-only in milliseconds; it cannot flip
the cell's brain mid-session. Regime-chasing mid-session is how systems whipsaw
themselves to death — dwell confirmation exists precisely to prevent it.

---

## 12 · The 30 styles — full deep-dive (owner-locked rosters, Session 17)

**Reading the table:** every row is one archetype = one spec-sheet instantiated on the
shared skeleton. Columns: *Payoff physics* (what makes it money) · *Signature obs*
(what it watches extra) · *Reward core* (what it optimizes) · *Champion* (what it must
beat — deterministic) · *Hazards* (its special gates).

### 12.1 EQUITIES (5) — trunk physics: auction dynamics, liquidity cycles, earnings calendar

| Style | Payoff physics | Signature obs | Reward core | Champion to beat | Key hazards |
|---|---|---|---|---|---|
| **intraday-MF** | short-horizon momentum/mean-revert on liquid large-caps, many small wins | multi-horizon returns, spread/imbalance, session phase | expectancy after costs (cost coefficients real) | linear/GBM signal + fixed filter | cost drag, regime flicker, news spikes |
| **stat-arb PAIR** | cointegrated pair spread reversion, market-neutral | Kalman spread state, hedge ratio, residual stats | spread-PnL (leg ledger A4) | static hedge-ratio OLS | spread break, leg gap, borrow |
| **earnings/event** | pre/post-event drift & surprise response | event calendar, surprise history, IV context, news-tension | event-window expectancy | calendar-anomaly baseline | event misspec, gap risk, IV crush |
| **auction (open/close)** | open/close imbalance continuation, auction theory | opening auction book, close-auction volume profile | session-window expectancy | time-of-day fixed rule | auction manipulation, one-shot windows |
| **equity MM** (where loop permits) | spread capture + queue position | depth×levels, queue, toxicity (VPIN-class) | spread − adverse − inventory − fees | A-S static core | adverse selection, poison tape, quote storms |

### 12.2 FUTURES (4) — trunk physics: term structure, basis, roll, margin cycles

| Style | Payoff physics | Signature obs | Reward core | Champion to beat | Key hazards |
|---|---|---|---|---|---|
| **delta-direction** | trend/conviction on index & commodity futures | multi-horizon momentum, positioning (COT-class), vol regime | directional expectancy after costs | Donchian/MA-class system | whipsaw, gap risk, roll days |
| **basis/calendar-spread** | relative value between delivery months | term-structure slopes, spread z-scores, inventory | spread expectancy | static z-score threshold | delivery mechanics, liquidity per leg |
| **term-structure carry** | roll-yield capture (contango short / backwardation long) | roll yield, curve slope, seasonality | carry after roll costs | constant-carry rule | curve inversion, regime break |
| **futures MM** (tick-aware) | spread capture on liquid contracts | tick book, queue, imbalance | spread − adverse − inventory | A-S static core | tick whipsaw, news halts |

### 12.3 OPTIONS (8) — trunk physics: vol surface, greeks, expiry mechanics (largest roster)

| Style | Payoff physics | Signature obs | Reward core | Champion to beat | Key hazards |
|---|---|---|---|---|---|
| **delta-direction via options** | leveraged directional via premiums | surface deltas, IV vs RV, greeks | direction after premium decay | futures-direction equivalent | IV crush, theta bleed, gap |
| **vol-alpha (vega)** | trading implied vs realized vol, term-structure of vol | IV surfaces, RV forecasts, skew | vol spread expectancy | straddle-straddle static rule | vol regime jumps, gap IV |
| **theta-harvest** | premium decay capture (short premium, defined risk) | term IV rich/poor, event proximity | decay after tail costs | short-strangle baseline | fat tails, gap events (D2 kits mandatory) |
| **gamma-scalper** | long gamma day-scalp vs decay | gamma/theta ratio, intraday RV | scalp PnL − decay | no-gamma control | decay drag in calm markets |
| **basis/calendar (options)** | calendar/diagonal spread value | term-IV shape, forward vol | spread expectancy | static calendar rule | pin, early assignment |
| **expiry/pin specialist** | pin-risk capture near expiry | OI walls, dealer gamma, pin score | expiry-window expectancy | no-expiry baseline | pin breaks, settlement rules (C3) |
| **hedge-executor** | cheapest hedge for the book (cost minimization) | greek exposures, hedge ratios, borrow | hedge cost vs slippage (NOT alpha) | static rebalance rule | hedge slippage, leg selection |
| **options-MM** (vol-aware quoting) | two-sided vol quoting, inventory control | surface microstructure, greeks P&L | spread − adverse − vol-inventory | A-S on surface | surface jumps, adverse selection |

### 12.4 CRYPTO (6) — trunk physics: 24/7 sessions, funding, liquidations, fragmented venues

| Style | Payoff physics | Signature obs | Reward core | Champion to beat | Key hazards |
|---|---|---|---|---|---|
| **perp funding-rate** | funding capture on perp basis | funding rate, basis, OI | funding + basis after fees | static funding rule | funding flips, venue risk |
| **liquidation-cascade hunter** | fade/ride forced-flow cascades | liquidation heatmap, OI drops, leverage clusters | cascade-window expectancy | level-breakout baseline | fakeouts, exchange outages |
| **weekend-session quirks** | weekend liquidity/behavior patterns | session-phase (crypto), weekend flags | weekend expectancy | weekday-only baseline | thin books, gap risk |
| **stat-arb PAIR (crypto)** | cross-pair cointegration | pair spreads, venue basis | spread expectancy | OLS hedge | de-peg events, 24/7 drift |
| **cross-venue MM** | venue price dispersion, transfer flow | multi-venue books, transfer alerts | spread − adverse − transfer cost | single-venue A-S | venue outage, deposit delays |
| **perp MM** | perp spread capture | perp book, funding clock, OI | spread − adverse − inventory | A-S static core | funding-time adverse, halts |

### 12.5 FX (3) — trunk physics: session overlaps, macro calendars, carry

| Style | Payoff physics | Signature obs | Reward core | Champion to beat | Key hazards |
|---|---|---|---|---|---|
| **session-pattern** | London/NY/Asia session behaviors | session phase, overlap liquidity, fixings | session expectancy | time-of-day rule | session regime shifts |
| **carry** | interest-rate differential | rate differentials, vol regime, risk-on/off | carry after vol drag | static carry basket | carry unwind (crisis kits) |
| **news-fade** | overreaction fade after macro prints | calendar, surprise index, post-news path | fade expectancy | post-news drift baseline | twin-fire slippage, gap |

### 12.6 PREDICTIONS (4) — trunk physics: resolution risk, 0/1 payoffs, no continuous book (own trunk, day one)

| Style | Payoff physics | Signature obs | Reward core | Champion to beat | Key hazards |
|---|---|---|---|---|---|
| **probability-alpha** | mispriced event probabilities vs model | market prices, base rates, resolution data | Brier-improvement → PnL | base-rate estimator | resolution risk, thin books |
| **event-odds arb** | same event, price discrepancy across markets | cross-market quotes, correlation of events | arbitrage spread after fees | naive cross-quote rule | one-leg unfillable, resolution mismatch |
| **resolution-discount** | trading resolution certainty drift | resolution timeline, rule text, volume | expectancy on certainty convergence | calendar baseline | rule ambiguity, delisting |
| **probability-MM** | two-sided quoting on event odds | book depth, resolution clock | spread − adverse − resolution risk | A-S adapted to 0/1 payoff | resolution jumps, adverse selection |

**Cross-cutting standing elements:** Type-3 portfolio layer (allocator + governor over
ALL families), execution module (TWAP/VWAP/POV), event triggers (cross-family
OBSERVATION triggers, not a fifth family).

**Adoption waves (anti-explosion rule):** W1 equities core → W2 event + first
derivative trio → W3 MM + options depth → W4 crypto rosters → W5 FX + predictions.
An archetype ships ONLY when its family trunk exists ∧ deterministic control is
implementable ∧ ops can absorb its shadow slot.

---

## 13 · How many agents do we actually need? (population sizing, with math)

### 13.1 The world universe vs our roster (honest funnel)

| Tier | Definition | Count (evidence §19) |
|---|---|---|
| **Full world universe** | every listed equity (WFE) + futures + options + crypto markets + FX + prediction markets | 59.5k companies · ~190k futures contracts · 4.3–6M options · 30.5k crypto markets · ~60 FX |
| **Tradable-for-us universe** | liquid enough for our cost model, in markets we choose to connect | see below |
| **Our first-decade roster** | liquid-first, per family, gated by venue decisions | **≈ 3,000–6,000 cells** |
| **Day-one slice (W1)** | 1–2 symbols × equities styles + pilot portfolio layer | 2–10 cells |

**Liquid-first roster math (our estimate, clearly labeled):**

| Family | Selection rule | Cells |
|---|---|---|
| Equities | top ~2,000–5,000 by ADV in chosen markets | 2,000–5,000 |
| Futures | top ~200–300 contracts by open interest | 200–300 |
| Options | top ~30–50 underlyings × chains treated as one cell each | 30–50 |
| Crypto | top ~150–250 pairs/perps | 150–250 |
| FX | liquid majors+minors | ~40 |
| Predictions | active liquid markets on chosen venues | 100–500 |
| **Total** | | **≈ 3,000–6,000 cells** |

<svg viewBox="0 0 760 240" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI,Arial" font-size="10.5">
  <rect width="760" height="240" fill="#fbfdff"/>
  <polygon points="40,30 720,30 660,68 100,68" fill="#eef4fb" stroke="#2b6cb0"/>
  <text x="380" y="54" text-anchor="middle" fill="#0d1b2a" font-weight="bold">FULL WORLD UNIVERSE — 59.5k listings · ~190k futures · 4.3–6M options · 30.5k crypto markets</text>
  <polygon points="100,74 660,74 610,112 150,112" fill="#fff8e6" stroke="#b7791f"/>
  <text x="380" y="98" text-anchor="middle" fill="#744210" font-weight="bold">TRADABLE FOR US — liquid enough for our cost model, in chosen venues only</text>
  <polygon points="150,118 610,118 560,156 200,156" fill="#eaf7ef" stroke="#2f855a"/>
  <text x="380" y="142" text-anchor="middle" fill="#22543d" font-weight="bold">FIRST-DECADE ROSTER — ≈ 3,000–6,000 cells (6 trunks × applicable archetypes)</text>
  <polygon points="200,162 560,162 470,200 290,200" fill="#f3ecff" stroke="#6b46c1" stroke-width="2"/>
  <text x="380" y="186" text-anchor="middle" fill="#4c1d95" font-weight="bold">DAY-ONE SLICE (W1) — 2–10 cells</text>
  <text x="380" y="228" text-anchor="middle" fill="#5a6a7a" font-size="10">Fig 5 — the population funnel: we start tiny, grow through wave gates, never "deploy the world" at once</text>
</svg>

**Key insight — cells ≠ styles × symbols fully crossed.** Not every style applies to
every symbol (the roster matrix in §12 defines which cells exist). A cell is born only
where liquidity, data, and a deterministic champion exist.

### 13.2 What one cell costs (scale economics, P4 numbers to confirm)

- Model: 5–50M params → ~20–200 MB with versions in registry (est.).
- Episodic memory: ≤100k episodes × ~1 KB → ≤100 MB/cell (A2 ceiling).
- Training: batched — dozens of cells per GPU-night; small models, minutes each.
- Hot path: µs inference, near-zero marginal cost.
⇒ **Thousands of cells are operationally affordable on a small GPU fleet + 1–2 boxes**
— this is the whole reason for tiny shared-skeleton models instead of giant ones.

---

## 14 · Orchestration — how the groups are run over markets

### 14.1 The orchestration stack (exactly four levels, not one per market)

```
L4  OWNER / COCKPIT          — human gates, morning report, kill switch (LLM explains, never decides)
L3  GOVERNOR + ALLOCATOR     — ONE per system (E5): caps, ladders, bands, netting, circuit breakers
L2  FAMILY TRUNKS (6)        — trunk refresh, shared feature/encoder training, champion per class
L1  CELL OPERATORS           — per-market runtime groups: feed adapters, session managers,
                               batch schedulers (one operator covers a market's cells)
L0  CELLS (3,000–6,000)      — the agents themselves
```

<svg viewBox="0 0 760 285" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI,Arial" font-size="10.5">
  <rect width="760" height="285" fill="#fbfdff"/>
  <polygon points="290,20 470,20 450,58 310,58" fill="#f3ecff" stroke="#6b46c1" stroke-width="2"/>
  <text x="380" y="44" text-anchor="middle" fill="#4c1d95" font-weight="bold">L4 · OWNER / COCKPIT — human gates, kill switch</text>
  <polygon points="240,66 520,66 495,104 265,104" fill="#fff3f3" stroke="#c53030" stroke-width="2"/>
  <text x="380" y="84" text-anchor="middle" fill="#c53030" font-weight="bold">L3 · GOVERNOR + ALLOCATOR — exactly ONE for the whole system</text>
  <text x="380" y="98" text-anchor="middle" fill="#0d1b2a" font-size="9.5">caps · ladders · netting · circuit breaker — two governors = two truths (never)</text>
  <polygon points="190,112 570,112 540,150 220,150" fill="#eef4fb" stroke="#2b6cb0"/>
  <text x="380" y="130" text-anchor="middle" fill="#2b6cb0" font-weight="bold">L2 · 6 FAMILY TRUNKS — trunk refresh · shared encoder · class champions</text>
  <text x="380" y="144" text-anchor="middle" fill="#0d1b2a" font-size="9.5">equities · futures · options · crypto · FX · predictions (cross-family transfer forbidden)</text>
  <polygon points="140,158 620,158 585,196 175,196" fill="#fff8e6" stroke="#b7791f"/>
  <text x="380" y="176" text-anchor="middle" fill="#744210" font-weight="bold">L1 · MARKET RUNTIME GROUPS (~4–8) — feeds · sessions · batch schedulers</text>
  <text x="380" y="190" text-anchor="middle" fill="#0d1b2a" font-size="9.5">India-equities · crypto-venue · FX · predictions … one per venue/session regime</text>
  <rect x="90" y="204" width="580" height="44" rx="6" fill="#eaf7ef" stroke="#2f855a" stroke-width="2"/>
  <text x="380" y="224" text-anchor="middle" fill="#22543d" font-weight="bold">L0 · CELLS — 3,000–6,000 agents: decide locally in µs, need-to-know isolation</text>
  <text x="380" y="240" text-anchor="middle" fill="#0d1b2a" font-size="9.5">more markets ⇒ more L1 groups, NEVER more governors</text>
  <text x="380" y="274" text-anchor="middle" fill="#5a6a7a" font-size="10">Fig 6 — orchestration: four levels, one governor, deterministic scheduling (no "herding minds" — there is no language)</text>
</svg>

**How many orchestrations?** Not one per market — **one governor (L3) for the whole
system**, six trunk managers (L2), and a handful of *market runtime groups* (L1: e.g.
India-equities runtime, crypto-venue runtime, FX runtime, predictions runtime — one
per venue/session regime, ~4–8 groups initially). More markets = more L1 groups, NOT
more governors. The governor must stay singular — two governors = two truths about
risk (that is exactly how pod shops keep a *central* risk desk).

**Does orchestration make the agents better?** The cells don't need orchestration to
think — they decide locally in µs. Orchestration adds what local thinking cannot:
capital allocation across cells, netting of conflicting exposures, contagion control,
signal routing, and fleet-wide training scheduling. Since our agents are non-LLM
tensors (not chatty personas), orchestration is *scheduling + arbitration* — deterministic,
cheap, and auditable — not "herding minds". That is why it stays clean at thousands
of cells: **messiness in agent orchestration comes from language; we have none.**

### 14.2 Fleet operations rhythm

| Cadence | Job |
|---|---|
| per event | cells decide; governor gates; ledger records |
| per session open | atomic swap point (weights+features+config), eligibility recompute |
| nightly | E3 trains batch → E4 races → registry updates → morning report |
| weekly | evolution loop + Invention Lane sweep; diversity/monoculture check (B2) |
| monthly | retirement proposals (B1), covariance refresh, crisis-suite audit |
| yearly | trunk refresh, spec-sheet reviews, suite expansion (D2), tech-refresh review (§16) |

---

## 15 · The memory layer for thousands of agents (long-term → short-term, organized)

### 15.1 The four memory tiers (per cell + shared)

| Tier | What it holds | Where | Retention | Recall speed |
|---|---|---|---|---|
| **T0 working state** | current obs, recurrent slot, position state | in-process RAM (mmap) | session-lived | ~µs (same process) |
| **T1 episodic** | measured trade situations → outcomes, regime-tagged | per-cell embedded LanceDB | age-decayed + regime-stratified, ≤100k episodes/cell | top-8 ≤50 µs budget (A2) |
| **T2 training corpus** | full tick/bar history, as-of, PIT-certified | Arrow/Parquet archive (L0, immutable) | **forever** (hash-chained) | columnar scan, offline only |
| **T3 fleet knowledge** | weights, lineage, champions, spec-sheets, feature registry | model registry + Postgres | forever, versioned | ms |

<svg viewBox="0 0 760 230" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI,Arial" font-size="10.5">
  <rect width="760" height="230" fill="#fbfdff"/>
  <text x="20" y="20" fill="#0d1b2a" font-weight="bold" font-size="11.5">Memory timeline — how long one cell remembers, and how fast it recalls</text>
  <line x1="40" y1="180" x2="720" y2="180" stroke="#0d1b2a" stroke-width="1.2"/>
  <g fill="#5a6a7a" font-size="9">
    <line x1="40" y1="180" x2="40" y2="185" stroke="#5a6a7a"/><text x="40" y="198" text-anchor="middle">µs</text>
    <line x1="200" y1="180" x2="200" y2="185" stroke="#5a6a7a"/><text x="200" y="198" text-anchor="middle">session</text>
    <line x1="380" y1="180" x2="380" y2="185" stroke="#5a6a7a"/><text x="380" y="198" text-anchor="middle">30–90 days</text>
    <line x1="540" y1="180" x2="540" y2="185" stroke="#5a6a7a"/><text x="540" y="198" text-anchor="middle">12 months+</text>
    <line x1="720" y1="180" x2="720" y2="185" stroke="#5a6a7a"/><text x="720" y="198" text-anchor="middle">forever</text>
  </g>
  <rect x="40" y="40" width="160" height="26" fill="#eaf7ef" stroke="#2f855a"/>
  <text x="120" y="57" text-anchor="middle" fill="#22543d" font-weight="bold">T0 working state — µs</text>
  <rect x="40" y="72" width="340" height="26" fill="#eef4fb" stroke="#2b6cb0"/>
  <text x="210" y="89" text-anchor="middle" fill="#2b6cb0" font-weight="bold">T1 episodic (LanceDB) — recall ≤50 µs · ≤100k episodes · age-decayed</text>
  <rect x="40" y="104" width="680" height="26" fill="#fff8e6" stroke="#b7791f"/>
  <text x="380" y="121" text-anchor="middle" fill="#744210" font-weight="bold">T2 training corpus (Arrow/Parquet, immutable, hash-chained) — FOREVER, as-of truth</text>
  <rect x="40" y="136" width="680" height="26" fill="#f3ecff" stroke="#6b46c1"/>
  <text x="380" y="153" text-anchor="middle" fill="#4c1d95" font-weight="bold">T3 fleet knowledge (registry) — weights · lineage · champions · spec-sheets — FOREVER, versioned</text>
  <text x="380" y="220" text-anchor="middle" fill="#5a6a7a" font-size="10">Fig 7 — four tiers: recent life is cheap and fast, history is immutable, knowledge is versioned · writes are write-behind (hot loop never waits)</text>
</svg>

### 15.2 How memories are organized and how long they last

- **Short-term (seconds–days):** T0 + the sampling ladder's newest-30-days bucket —
  the cell is *most tuned* to the last month (50% of nightly training weight).
- **Medium-term (weeks–months):** 90-day (30%) and 12-month (15%) buckets + episodic
  top-8 recall of similar past situations.
- **Long-term (years–forever):** whole-history 5% reserve, regime-stratified archive
  (T2), ancient-window CI probe (a frozen set of old windows re-run on every candidate
  so the cell can never silently forget how 2020 worked), and the archive itself.
- **Write path:** async write-behind — the hot loop NEVER waits on memory writes;
  every skip is tagged, every outcome lands in the ledger then the episodic store.
- **Why no hallucination in memory:** entries are structured measurement records
  {situation-embedding, outcome, regime, event-tag} — there is nothing to "imagine";
  retrieval is nearest-neighbor over numbers.
- **Fleet math:** 5,000 cells × 100 MB episodic ≈ 500 GB (one disk) · T2 archive
  scales with tick volume (TB-class, commodity storage) · T3 registry GB-class.
  Nothing here needs exotic infrastructure.

---

## 16 · The decade-durability architecture (run a decade without rewriting)

**Honest premise:** no architecture survives a decade unchanged — *what* survives is
a stable core with swappable edges. The design below is the pattern that has actually
lasted in production systems (event sourcing + versioned contracts + rolling refresh),
verified against schema-evolution research (§19: event-sourced systems evolve via
versioned events; the known challenges are evolution discipline and projection
rebuilds — both engineered for here).

### 16.1 What NEVER changes (the decade-stable core)

| Stable element | Why it lasts |
|---|---|
| Arrow/Parquet data spine + as-of truth (R1) | format is an open standard; data outlives any code |
| Event-sourced everything (decisions, fills, config changes as append-only events) | history is reconstructable forever; new views = new projections, no migration of the past |
| Head Contract shape `{action_typed, size, conf, ttl}` | typed payloads absorb new action kinds without breaking consumers |
| Deterministic governor concept (R3/R7) | it is a rule table — rules are edited by humans, versioned, never learned |
| Spec-sheet identity (family × archetype × cell) | new styles = new spec-sheets, zero new codebases |
| Certification gates (DSR, crisis kits, shadow, human gate) | they are process, not code fashion |

### 16.2 What rolls (the refresh lanes)

| Lane | Cadence | Mechanism |
|---|---|---|
| Weights | nightly | registry pointer flip, bit-exact rollback |
| Trunk encoder | yearly (or drift-triggered) | full-window retrain; heads adapt via ladder |
| Obs/feature schema | quarterly-ish | versioned contracts + compatibility check at session swap (D1) |
| Venue adapters | when venues change | adapter interface frozen; implementations replaced |
| Training framework tier | as needed (SB3 → JAX tier at scale) | training-time only — **deployed artifact format (ONNX) never changes** |
| Crisis suite | yearly expansion | new kits added, old never removed |
| Hardware/infra | as needed | hot path is plain Rust — no vendor lock in the loop |

### 16.3 The anti-rot mechanisms

- **Doc-drift checks** (standing policy): planning docs → engineering docs with review
  cadence; the remainder map (P1–P4) is the living gap list.
- **Deprecation discipline:** every versioned contract carries a migration window; the
  atomic session swap means at most ONE version skew exists at any moment.
- **The ONNX firewall:** Python/framework churn can never touch the live loop — only
  compiled model files cross the boundary. Frameworks rise and fall; the artifact
  format is the wall.
- **Blind crisis replay + seed-invariance + latency CI run on every merge** — a decade
  of merges cannot silently degrade the system because the gates are mechanical.

**Claim we will make (after M1 measurements, not before):** internal p99 < 100 µs,
reproducible training, 24/7 operation with session-boundary swaps. Until measured,
these are budgets — the latency-CI gate exists precisely so the claim becomes a fact
or gets corrected.

---

## 17 · Advanced technology to adopt (the "even better" menu — each evidence-checked)

| Technology | What it gives us | Status |
|---|---|---|
| **Small sequence models for LOB** (Mamba/SSM-class) | NanoARB runs a ~500K-param Mamba LOB model at <800 ns — proof that tiny sequence trunks are production-viable | candidate trunk architecture for E4 shootout (GBM vs temporal-conv vs transformer vs SSM) |
| **GPU-accelerated rollouts** (JAX-LOB / JaxMARL-class) | up to **240× faster training** with 4096 parallel envs on one GPU — makes crisis-replay + evolution sweeps cheap at fleet scale | P4 adoption when population > hundreds of cells |
| **Generational evolution ensembles** (FinRL-Podracer) | populations of agents evolved in generations, 3–7× faster than RLlib, proven on NASDAQ-100 | maps directly to our pycma evolution loop |
| **Inference-time policy conditioning** (FinPILOT-class, arXiv 2605.12653) | a forecaster's trajectory adjusts the policy AT DECISION TIME without retraining — compatible with any pre-trained agent | research-lane experiment (P4), never auto-deployed |
| **World-model trial** | learned environment model for planning/eval rollouts | already on the P4 research agenda; gated like any candidate |
| **Meta-labeling** (LdP) | second model sizes positions on primary-signal confidence — turns our conformal confidence into sizing directly | fold into reward/feature spec (S15) |
| **Foundation forecasting models** (TimesFM-class) | generic forecaster as a *feature candidate* to test, never a decision-maker | research lane only (Session 20 verdict) |
| **Digital-twin LOB simulator** (JAX-LOB-class) | high-fidelity replay for E4 + sim-fidelity calibration loop (P3-13) | build in Stage sim-fidelity lane |
| **Conformal prediction** (already core) | distribution-free calibrated confidence — the Head Contract's number | adopted, non-negotiable |
| **Drift-detector family** (CUSUM/PH/ADWIN — already core) | the "learn fast, leave fast" machinery of §10 | adopted, per-cell tuning in E4 |

**Adoption rule:** every item on this menu enters through the same gates as any
candidate — no technology deploys because it is new; it departs when it beats the
incumbent on matched windows.

---

## 18 · Build instructions — the concrete order (dependency-true, with done-when gates)

> Maps to FULL-SYSTEM-PLAN's 10 stages and the P1–P4 remainder map. Each step has a
> **done-when** gate; nothing starts before its predecessor's gate passes.

### Phase A — foundation (unblocks everything)

| # | Build this | Done when |
|---|---|---|
| A1 | **Stage-1 foundation spec freeze** (P1-1): event contract final fields, source-registry schema, storage-edge schemas | spec reviewed + committed; every later doc references it |
| A2 | **Data spine**: Arrow/Parquet archive + as-of() API + hash-chaining | backfill of first symbol passes PIT certification tests |
| A3 | **Connector framework** (P1-2): typed template, checkpoint table, QA gates | one live connector produces certified data end-to-end |
| A4 | **Feature spec + Reward spec** (P1-3,4): actual feature list per class, frozen reward formulas with real cost coefficients | both docs frozen; obs blocks implementable from them |

### Phase B — the agent machine (one cell, end to end)

| # | Build this | Done when |
|---|---|---|
| B1 | **E1 feature engine** (Rust, hot path) + **obs schema A1** with as-of fields | feature outputs match offline reference bit-exact in CI |
| B2 | **E2 runtime** (ONNX load, conformal calibrator, gateway scaffold) + **latency CI** (p99 budget per merge) | latency CI green on target hardware |
| B3 | **E3 training** (SB3 harness, seed-invariance CI, sampling ladder) | same seed ⇒ same weights, twice, in CI |
| B4 | **E4 arena**: champion race, CPCV+DSR gates, crisis-kit replays (start with 2 kits) | a synthetic candidate can be promoted/rejected by gates alone |
| B5 | **E5 governor**: deterministic rule table, drawdown ladder, kill switch (zero ML) | replayed crash suite: ladder fires correctly, 100% deterministic |
| B6 | **First cell (M1)**: 1 symbol × 1 style — replay train → shadow → human gate 1 | shadow decision-parity ≥98%; owner signs gate |

### Phase C — the loop closes (money-path + coordination)

| # | Build this | Done when |
|---|---|---|
| C1 | **First market decision** (P1-5): India vs crypto vs FX → venue adapter + cost model + tax lane | live tiny-order in chosen venue, full ledger attribution |
| C2 | **E6 execution** (TWAP/VWAP/POV) + **E7 ledger** + morning report | IS within model ±20% on live shadow |
| C3 | **Rails**: R1 signal bus with referee (B3/B4), R3 model registry, R2 LanceDB episodic | bus abuse tests pass (rate, TTL, self-deal quarantine) |
| C4 | **M2 portfolio lite**: static allocator + governor overlay + risk targeting | de-risk ladder correct in replayed crashes |
| C5 | **Population growth W1→W5** per taxonomy waves | each new archetype passes wave rule (trunk ∧ control ∧ ops) |

### Phase D — hardening & scale (P3/P4)

Sim-fidelity calibration loop · eval-CI harness golden datasets · full crisis suite
(5 kits) · adversarial audit (C2) · capital-allocation scheme + governor table v2 ·
monitoring/SLO/pager runbook · incident runbooks · explainability cockpit (LLM lane)
· scale economics review → JAX tier decision.

**Day-one practical commands (scaffolding, when Stage 1 starts):**
```bash
cargo new --lib hot-path          # E1/E2/E5/E6/E7 in one workspace, Rust 2024
uv init --python 3.12 train-lane  # E3/E4 Python lane (uv + ruff + pytest)
# gates: cargo clippy -D warnings · cargo test · uvx ruff check . · uv run pytest
# CI per merge: latency budget test · seed-invariance · DSR gate · secrets scan
```

---

## 19 · Evidence appendix (every external claim → source, all live-verified 2026-10-26)

**Universe sizing**
1. WFE Dashboard Oct 2026 — 59,581 listed companies (domestic+foreign): focus.world-exchanges.org/issue/october-2026/dashboard
2. OneTick coverage — 240k equities, 100k ETFs, 190k futures, 4.3M options: onetick.com/market-data-coverage
3. TrendSpider — 61k assets, 28k crypto pairs, 140k CME futures contracts: help.trendspider.com/kb/data-feeds/market-data-sources
4. WorldCoinIndex (2026-10-26) — 30,537 markets, 8,468 coins, 3,519 perpetuals: worldcoinindex.com/exchange
5. CoinMetrics — 21k+ spot, 10k+ futures, 14k+ options markets: gitbook-docs.coinmetrics.io/market-data/market-data-overview
6. LSEG — 6M+ active options, 160+ futures feeds: lseg.com/en/data-catalogue/derivatives

**Latency / throughput**
7. NanoARB — tick-to-trade median 780 ns, p99 1.2 µs (LOB 45ns, features 120ns, inference 580ns, ONNX, AMD EPYC): mintlify docs dhir1007/nanoARB
8. RustAlgorithmTrading — 100k+ msgs/s, book updates <5 µs p99: github.com/SamoraDC/RustAlgorithmTrading
9. Quant Memo — 10 µs tick-to-trade budget worked example + P99 methodology: quantmemo.com/concepts/inference-latency-budgets
10. Inference-latency-budget skill — P99 measurement discipline (warm-up, GC, quantization caveats): skills.himanshujangir.com

**LLM limits (the non-LLM case)**
11. MethodAlgo 2026 — LLM decision cycle 15–40 s vs <100 µs HFT; ~$0.12/decision; <5% signal value at 20 s; MiFID II/SEC reproducibility conflict: methodalgo.com/press/research/latency-of-logic
12. DL News 2025-10-05 — "LLMs hallucinate pretty egregiously… extreme errors" in quant settings (Allora): dlnews.com/articles/defi/ai-agents-are-terrible-at-trading-crypto-but-that-could-change/
13. AllPath (arXiv 2610.26662, 2026) — deployed LLM trading agent: operator removed 11 of 19 self-added rules (median 8-day lag); deterministic code gates required
14. Agentic Trading survey (arXiv 2605.19337, 2026) — memory hallucination = critical vulnerability; LLM should be read-only
15. Execution-realism scoping review (arXiv 2606.08285, 2026) — LLM trading evaluation practice immature; costs/survivorship/look-ahead routinely omitted
16. Qlumina 2026 — probabilistic text vs deterministic capital: non-deterministic outputs incompatible with reproducible execution

**Training speed**
17. FinRL-Podracer (arXiv 2111.05188) — agent trained in 10 min on 80×A100 (NASDAQ-100, 10y minute data); 3–7× vs RLlib; generational evolution every 800 s
18. JaxMARL-HFT (arXiv 2511.02136) — GPU rollouts, 4096 parallel envs, up to 240× training speedup
19. Inferensys MLOps — sub-ms per-sample online updates / 1–5 s retrain cycles; EOD orchestration cuts 4–6 h retraining windows: inferensys.com
20. dTrader — nightly fine-tune on 30 days of trades + 60 s PPO rollout horizon: theclevermachine.com

**Regime / drift**
21. Regime Survival Forecasting (TQFB 2026) — Weibull AFT fails: concordance 0.20–0.39, flat vs data volume, boundary-collapsed predictions: tqfb.reapress.com/journal/article/view/85
22. CUSUM/Page-Hinkley/ADWIN/DDM/KSWIN/BOCD — current standard drift-detector family: CRAN datadriftR (2026), frouros/river docs; Page 1954; Biometrika 2024 CUSUM 70-year review
23. Hamilton 1989 (Markov-switching) · Lo 2004 (Adaptive Markets) · Bailey–LdP (PSR/DSR/PBO/CPCV) · conformal (Vovk/Angelopoulos-Bates) — the classics already mapped in AGENT-ECOSYSTEM-MASTER §6

**Bus / memory / durability**
24. NATS — millions msgs/s, sub-millisecond latency, single binary; JetStream streams/consumers/replay/at-least-once: docs.nats.io, nats.io/about
25. ACM TOIS 2025 — memory-mechanism survey for agents (structured review): dl.acm.org/doi/full/10.1145/3748302
26. Event-sourced systems schema evolution (arXiv 2104.01146) — industry evidence: evolution + projection rebuild are THE challenges → our versioned-contract discipline

**Specialist-vs-generalist (Session 22)**
27. ScaleZero (ICLR 2026) — multi-task negative interference; generalist ≈ or < 26 single-task specialists
28. Pod-model industry structure — Millennium/Citadel/Point72 central-risk-desk architecture (TD/CAIS/Long Angle 2025–26)
29. Quant taxonomy — quant = method umbrella incl. market making, stat-arb, vol (Quantt 2026, The Wick 2026, Springer 2025)

**Research-lane candidates**
30. FinPILOT (arXiv 2605.12653) — inference-time MPC-style policy conditioning on forecasts

---

## 20 · Open forks (owner decisions still pending)

1. **Demo milestone** — what counts as the first demo.
2. **First market** — India vs crypto vs FX (decides venue adapter, cost model, tax lane).
3. **Engine shootout** — GBM vs temporal-conv vs small transformer vs SSM trunk.
4. **Final system name** — pending owner reveal.
5. **Timeframe sub-type field** — proposed config field, owner verdict pending.
6. **Colab-as-E3-lane** — T4 verified here; decision pending (training lane only).
7. **Long-horizon allocator cell** — P4 capital-allocation session.
8. **Master-doc sync** — AGENT-ECOSYSTEM-MASTER needs family×archetype update + re-render.

---

*End of report. Companion docs: FULL-SYSTEM-PLAN · AGENT-ECOSYSTEM-MASTER ·
DATA-LAYER-ULTRA-LOW-LATENCY · ROW3-SELF-LEARNING-AGENT-STACK ·
AGENT-RL-FRAMEWORKS-LIFECYCLE · AGENT-CLASS-DESIGN-TYPE1-2-3 ·
AGENT-TAXONOMY-FAMILIES · AGENT-17-TOPICS-DESIGN · PLANNING-CONVERSATION-LOG (S2–S23).*
