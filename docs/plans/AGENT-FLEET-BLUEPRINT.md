# AGENT FLEET BLUEPRINT — the complete finalist plan (non-LLM trading fleet)

**Session 28 · 2026-10-27 · final blueprint for owner review**
**Scope:** every decision locked through Sessions 22–28 — 75 symbol agents, per-market
styles, architecture, end-to-end workflow, rules & regulations, training / self-improvement,
data requirements, latency + operations-per-second budget, build waves with time estimates.
**Status:** draft v1 → **council-validated** (§14) → final. Every claim classified:
measured / web-verified / design / unverified.
**Diagrams:** `diagrams/d1-architecture.png` … `d6-e2e.png` (6 figures, embedded below).

---

## §1 · The fleet at a glance

| Unit | Count | What it is |
|---|---|---|
| **Agents (brains)** | **75** | one research agent + one regime router per symbol (Mode R, offline) |
| Cells (hands) — ceiling | ~460–520 | one executor per (symbol × style), built only if E4 promotes it |
| Active at once | **≤ 75** | router enables **0 or 1** style per symbol; default = all off |
| Day-1 live cells | 2–4 | wave 1 = index options first, then liquid stock options |

| Market | Symbols | Count | Styles/symbol | Cell ceiling |
|---|---|---|---|---|
| **Stocks** (Upstox NSE/BSE, options BUYING only, same-day) | NIFTY + SENSEX + optionable weight-stocks (scan S3) | **≤ 59** | 8–9 (S1: MM conditional) | ~440–500 |
| **FX Team 1** (cTrader ECN) | 7 pairs + Brent/WTI/XAU/XAG | **11** | 3 pairs (session·carry·news) / 2 commodities | **29** |
| **FX Team 2** (cTrader, **separate account**, pure scalping) | XAU/USD + WTI | **2** | 1 (scalping only, push-mode) | **2** |
| **Crypto** (Hyperliquid perps) | BTC, ETH, HYPE | **3** | 4 (funding·liquidation·weekend·PAIR) | **10–12** |
| **Total** | | **75** | | **≈ 480–545** (recomputed Session 33: 481–543) |

**Unit clarification (owner Q, Session 28):** 73 (old) was symbols × 1 style. The
multiplication was always in the plan (O5: "2 styles = 146"). Brains = 75; hands = many;
**only one hand works per symbol at a time (G5)**; most cells are never built (only
E4-promoted styles exist).

---

## §2 · Symbols and styles — the locked rosters

### 2.1 Stocks (≤59 symbols, options BUYING only)

- **Scan rule (S3):** universe = NIFTY 50 weight ∪ SENSEX 30 weight ∪ option universe →
  one list **≤ 59**. This scan IS the D4 screen; non-optionable names drop (may reduce the 57).
- **Instruments:** options only, **buy side only** (sellers post full premium + margin —
  the owner's verified reason). **Fully same-day: buy and sell same day / minutes–hours,
  never hold (S2)** → theta negligible; **spread cost is the battle**.
- **Index workhorse:** NIFTY (NFO) + SENSEX (BFO) weeklies carry the intraday load;
  stock options (monthly, thinner) serve event/swing cells.
- **Styles (8–9):** intraday-MF · stat-arb PAIR · earnings/event · auction ·
  **equity MM (S1: adapts by style condition — conditional/gated, not deleted)** ·
  + option styles delta-direction · vol-alpha · gamma-scalper · hedge-executor.
  **Council fix Q10: hedge-executor is a portfolio overlay (cost), not an alpha style** —
  long protective puts have negative standalone expectancy by construction, so it is
  **governed by E5 (hedge effectiveness), excluded from E4 alpha promotion.**
- **Same-day research power (council fix Q12):** day-1 statistical claims restricted to
  NIFTY/SENSEX weeklies (only adequately sampled instrument); stock-option cells pool
  symbols hierarchically with a shared cost/entry model.
- **Hierarchy:** 57 weight-stock agents → **NIFTY agent (NFO)** / **SENSEX agent (BFO)**;
  compressed "important-only" comms both ways (S4). Index agents never edit cell artifacts (G1).

### 2.2 FX Team 1 — 11 symbols, one agent per symbol, offline + online

| Group | Symbols | Styles | Cells |
|---|---|---|---|
| 7 pairs | EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF, USD/SGD | session-pattern · carry · news-fade | 21 |
| 4 commodities | Brent, WTI, XAU/USD, XAG/USD | session-pattern · news-fade (no carry) | 8 |

Per symbol: 1 research agent (Mode R: hypothesis · GP · ML · alt-data · microstructure)
→ E4 gate → specialist cells (Mode T). Router: session→session-pattern, rate-diff→carry,
calendar→news rules, ambiguous→**all off**.

### 2.3 FX Team 2 — gold + WTI only, separate account, pure scalping

| Item | Design |
|---|---|
| Symbols | **XAU/USD + WTI only** (2 cells), **separate account** — scalping risk isolated |
| Style | **1 scalping style only** — one agent, one symbol, one style, push-mode |
| Two-mode | offline learns *this symbol's* microstructure; online executes every tick |
| Machinery | Session 25 plan: NY4 VPS, **maker-entry mandatory**, cost floor per tick |
| Exits | hard tick stop · spread-widening gate · EIA/CPI print pre-flatten (oil) · kill-switch |

**Push honesty:** Mode R pushes the *search* hard; **E7 proves net-of-cost per attempt →
E5 allows size only while net edge is positive. The machine pushes; the governor decides how far.**

### 2.4 Crypto — BTC, ETH, HYPE (Hyperliquid perps)

- **Tradeable styles: 4 of 6** — funding-rate · liquidation-cascade · weekend-quirks ·
  stat-arb PAIR. ❌ cross-venue MM + perp MM = **deferred** (Hyperliquid rebate tier unreachable).
- Cells: 3 symbols × 3 styles = 9 + 1–3 PAIR = **10–12**.
- Exits: cascade-flatten · weekend-gap guard · funding-window (00/08/16 UTC) = the "print" ·
  kill-switch. 24/7 market, no theta.

---

## §3 · Architecture — rails, engines, orchestration

![D1 — end-to-end architecture](diagrams/d1-architecture.png)

**Figure D1 — the machine.** Data → R1 rail → E1 features → Mode R research → E4 gate →
R3 registry → Mode T (router → E2 signal → E5 governor → E6 execution) → venues → E7 ledger
→ decay monitor → back to research.

| Layer | Name | Job | Class |
|---|---|---|---|
| **Rails** | R1 DATA | point-in-time, as-of stamped bars/ticks/Greeks/alt-data — **no future leakage** | design |
| | R2 SIGNAL | frozen formulas/ONNX only in the hot path | design |
| | R3 LEARNING | offline only — LLM allowed **here only**, never in R1/R2 (R3 non-negotiable) | design |
| **Engines** | E1 features | bars · ticks · Greeks · indicators · microstructure · pattern flags | design |
| | E2 signal | evaluates frozen artifacts each tick | design |
| | E4 gate | **external, uneditable** — CPCV + **DSR ≥ 0.95** (probability metric, council fix C1) + crisis replay + net-of-cost → paper → shadow 3–6 mo; **owner approves each promotion, ≤5 decisions/week** | design |
| | E5 governor | deterministic: stops · size caps · news ladder · kill-switch · ban checks | design |
| | E6 execution | TWAP/VWAP/limits · venue adapters (Upstox / cTrader / Hyperliquid) | design |
| | E7 ledger | fills · slippage · P&L · **costs in the label before promotion** | design |
| **Orchestration** | L0–L4 | cell → symbol agent → team → market → global governor (kill-switch cascades down) | design |

### 3.1 One cell, two modes (Session 26)

![D2 — Mode R / Mode T](diagrams/d2-twomode.png)

| | Mode R (research, offline) | Mode T (trading, online) |
|---|---|---|
| Runs | hours–days, per symbol | us–ms, per tick |
| May | search 10⁴–10⁶ candidates, **may fail** (failure = data) | execute promoted artifacts only |
| Must not | hold order code path (**G3**) | contain search code (**G2**) or edit itself (**G1**) |
| LLM | optional (R3 only) | **never** |
| Isolation | separate process (**G6**) | separate process (**G6**) |

**Guards G1–G6:** G1 cells can't edit their own artifacts · G2 no search code in Mode T ·
G3 research has no order path · G4 every trial logged (DSR denominator) · G5 one style
active per symbol · G6 Mode R/T separate processes.

---

## §4 · The stocks system (≤59 agents as ONE system)

![D3 — stocks hierarchy](diagrams/d3-stocks.png)

**Owner's S1–S4 answers (locked):**

| # | Decision | Effect |
|---|---|---|
| **S1** | equity MM **adapts by style condition** | MM stays, gated — up to 9 styles/symbol |
| **S2** | fully same-day buying+selling, **never hold** | all stock styles intraday; theta ≈ 0; spread cost = the battle |
| **S3** | scan NIFTY wgt ∪ SENSEX wgt ∪ option universe → **one list ≤ 59** | this scan IS the D4 screen; may drop some of 57 |
| **S4** | upward comms = **volume shock + shock news + sudden event** on the weight (deterministic thresholds) | weights move the index — rules-only, R2-safe |

**Comms protocol (important-only, both directions):**

- **Stock → index:** sends only on threshold crossing (regime flip / style enable / kill participation) — triggered by new volume flow, shock news, sudden events on that weight.
- **Index → stock:** only `BLOCK` new entries · `PRIORITY` hedge · `KILL` participation.
- **Index agents NEVER edit** stock cells' frozen artifacts (G1 intact).
- NIFTY agent (NFO) has the aggregated bottom-up view + more control; same for SENSEX (BFO).

---

## §5 · How each market's agent works

### 5.1 FX Team 1 (11 symbols — steady rotation)

| Phase | What happens |
|---|---|
| Offline (Mode R) | per symbol: hypothesis → GP/ML search → alt-data (COT, rate diffs) → microstructure; 5 methods; every trial logged |
| Gate | E4: CPCV + DSR ≥ 0.95 + net-of-cost → frozen artifact in R3 |
| Online (Mode T) | router: session state → session-pattern · rate differential → carry · calendar → news-flade rules · **ambiguous = all off** |
| Risk | E5: spread check vs cost floor, session-spread widening, news ladder, kill-switch |
| Venue | cTrader Open API — **50 non-hist req/s**, heartbeat ≤10s, proxy routing (AWS Global Accelerator), NY4 |

### 5.2 FX Team 2 (gold + WTI — pure scalping, push mode)

| Phase | What happens |
|---|---|
| Offline (Mode R) | learns this symbol's microstructure: tick patterns, spread rhythm, liquidity windows, session liquidity gaps |
| Gate | E4 — plus **E7 net-per-attempt cost proof** required (spread + commission per scalp) |
| Online (Mode T) | executes scalps every tick; maker-entry mandatory; hard tick stop; spread-widening gate; EIA/CPI pre-flatten (oil) |
| Push rule | R searches hard → E7 proves net edge → **E5 sets size** while net edge positive |
| Account | **separate account** — scalping risk isolated from Team 1 |

Costs (web-verified, IC Markets Raw): EUR/USD avg 0.1 pip + $7/lot RT; XAU avg 0.07 + $7/lot RT;
NY4 servers; scalping allowed. Gold + WTI trade in **both teams, separate money**.

### 5.3 Crypto (BTC, ETH, HYPE)

| Phase | What happens |
|---|---|
| Offline (Mode R) | funding regimes that mean-revert · cascade precursors (OI + leverage build-up) · weekend dislocation · pair cointegration |
| Online (Mode T) | features = funding rate, OI, liquidation feed, book depth — **in frozen formulas, never "thinking"** |
| Exits | cascade-flatten · weekend-gap guard · funding window (00/08/16 UTC) = the print · kill-switch |
| Venue | Hyperliquid: REST weight **1200/min**, ~100 actions/10s practical, public-API order path measured ~600–700ms end-to-end (unverified: in-house node would cut this) |

---

## §6 · End-to-end trade lifecycle

![D6 — end-to-end lifecycle](diagrams/d6-e2e.png)

```
1 TICK/NEWS/STATE  →  2 E1 FEATURES (<1ms)  →  3 E2 SIGNAL (frozen eval, router picks style)
→  4 E5 GOVERNOR pre-trade (ban? spread? news? size cap? DD ladder?)
→  5 E6 EXECUTE (venue adapter, order + ack)  →  6 E7 POST-TRADE (fill vs expected, slippage + cost log)
→  7 FEEDBACK to research record (nightly)  ⇄ cost model refit → back to 2
```

**Exit ladder 1–6 (all markets):** 1 pre-flatten before prints ±5min · 2 hard stop
(−30–50% premium / tick stop) · 3 time-stop · 4 news-flatten (mid-trade, owner's rule) ·
5 liquidity gate on exit (no bid → underlying hedge, close-only) · 6 kill-switch (E5 ladder).

---

## §6A · Operations — runbook, kill matrix, recovery (council fixes O1–O6)

> The council's verdict: *"the document designs the machine but not the man."* This section
> closes that gap before delivery.

### A.1 Kill matrix (trigger → scope → action → re-arm)

| Trigger | Scope | Position action | Re-arm |
|---|---|---|---|
| E5 DD ladder breach | all cells in scope (symbol → team → market → global L0–L4) | **flatten** + cancel resting orders | owner confirmation, cooldown 1 session |
| Venue feed stale / WS drop | venue's cells | **forced-flat + pause** (no blind trading) | feed healthy + reconcile |
| Half-sent order / no ack | that cell | hold, reconcile, idempotent client order ID prevents double fill | ledger matches venue |
| Owner kill (mobile, out-of-band) | global | flatten all, both FX accounts | manual re-arm |
| Kill confirmation | — | **no confirmation delay** — kill is instant, re-arm is gated | — |

**Out-of-band backstop (council fix A13):** venue-side max-loss order + independent
watchdog process that can flatten **without the app** — the L0–L4 in-process cascade must
not be the only kill path.

### A.2 Startup invariant

`reconcile ledger vs venue positions → then, and only then, accept orders`. Idempotent
client order IDs on all 3 adapters (council fix O3).

### A.3 Push-mode human guardrail (council fix O5)

| Rule | Why |
|---|---|
| **No intraday size-up path** — size only via E5, never by hand | governor dies the first manual override |
| Manual override = cooldown + logged as a counter (feeds E4 review) | overrides become visible data |
| **Manual trades flagged and excluded from E7 labels** (or separate sub-account) | owner's own orders would pollute the net-per-attempt proof |
| **Attention budget:** threshold alerts + delayed/aggregated P&L view — no live tape staring | Barber & Odean / disposition-effect evidence: tape-watching drives myopic loss aversion |

### A.4 Daily runbook (minimum)

| When | Check |
|---|---|
| Pre-open | WS health ×3 venues · ban status · news calendar · position-vs-ledger break |
| In-session | exception-based supervision only — page alerts (tiered: page / same-day / weekly digest; thresholds tuned against replay before go-live, council fix O9) |
| Post-close | E7 cost reconciliation · E4 promotion queue (**owner-approved, ≤5 decisions/week**, council fix O8) · research batch status |
| Weekly | drift review · alert-threshold review · venue API/circular drift check · **backup restore-test (E7/G4 append-only + R3 registry)** |
| Monthly | **kill-drill: feed-stale + venue-down + kill-switch rehearsal** (Session 33 P3) |
| Always | process supervisor auto-restart · **off-VPS heartbeat** · one-action mobile kill (council fix O10) |

---

## §7 · Rules and regulations we keep

### 7.1 Non-negotiables R1–R8 (internal)

| # | Rule |
|---|---|
| R1 | no Windows-only runtime in the hot path |
| R2 | deterministic governor — no ML in risk decisions |
| R3 | **LLMs never in hot/risk paths** (R1/R2 rails) — R3 research only |
| R4 | risk first — capped worst case beats extra opportunity |
| R5 | every claim measured vs target vs web-verified vs unverified |
| R6 | no hand-editing generated artifacts / no editing vendored code |
| R7 | one style active per symbol (G5), default = all off |
| R8 | gates external and uneditable (G1–G6) |

### 7.2 Venue regulations (web-verified)

| Rule | Source | Where it binds |
|---|---|---|
| **Algo order tagging** — first 12 digits `444444444444` + 13th digit **"0"/"2"/"4"**; all API orders = algo orders; tagging applied broker-side at OMS — confirm Upstox exposes/sets it in W0 | NSE/INVG/69255 + corrigendum 69289, NSE FAQ Nov-2025 | E6 adapter tags every order; W0 integration test |
| **Rate limits** — regular algo **10 rps**, SEBI-registered **50 rps**, 500/min, 2000/30min (**Upstox docs**; NSE May-2025 INVG/67858 = implementation standards, separate doc — council fix V10) | Upstox developer docs | E6 central scheduler caps throughput |
| **Retail algo framework IN FORCE since 1-Apr-2026** (Sep-2025 extension) — not a future deadline (council fixes V1/O11) | SEBI Feb-2025 + extension | **W0 blocker — go-live without it = broker rejects orders** |
| **API access gates:** static-IP whitelisting · OAuth-only + 2FA · broker-issued API key · **no market/IOC orders for algo orders** · **≥5-year audit trail** with actual-user ID (council fix V2) | NSE/INVG/67858 §I | E6 adapter + W0 integration test |
| **TOPS 10 OPS = algo-registration trigger** (above → register for unique algo ID; below → generic) — **distinct from** Upstox's 10/50 rps API throttle (council fix V5) | NSE implementation standards | E6: throttle ≤10 OPS unless registered |
| **<45 ms order execution** — *vendor performance claim (status.upstox.com), NOT a regulation* (council fix V6) | Upstox vendor docs | design target only, §10 |
| **MWPL ban** >95% → reduce-only — **stock derivatives only** (index options not MWPL-banned); long-standing **exchange** framework (council fix V9) | exchange F&O ban circular | E5 hard entry gate (stock options) |
| Intraday monitoring ≥4 snapshots/day — **exchange-side duty**; E5 mirrors as **voluntary control** (council fix V8) | SEBI/HO/MRD/TPD-1/P/CIR/2025/41 | E5 intraday checks |
| **Option premium paid fully upfront** (buy-only = cheap side) | SEBI Nov-2024 | sizing math |
| NIFTY lot = 65 (₹15–20L contract value, Jan-2026) | NSE FAOP70616 | position sizing |
| One weekly index expiry/exchange (NIFTY NSE, SENSEX BSE) | SEBI reform | expiry calendar |
| **FEMA gate (CRITICAL — council fix V3):** RBI bars residents remitting margin "for trading in foreign exchange abroad" — **applies to BOTH FX teams**; FX only on SEBI-recognized exchanges / RBI-authorized ETPs. **No India-sourced margin to cTrader until written FEMA/advisor opinion on residence + funding route** | RBI FAQ Q6, Apr-2024 circular | **hard pre-W3 gate** |
| **Crypto gate (council fix V4):** Hyperliquid not FIU-IND-registered (status may change); 30% VDA tax + 1% TDS; perp P&L tax position unsettled → FEMA funding review + FIU-IND status check + tax position before any deposit | FIU-IND, 115BBH/194S | **hard pre-W5 gate** |
| cTrader: 50 non-hist req/s, heartbeat ≤10s; **500 new-orders/min = demo ceiling, production cap to verify** | cTrader Open API docs | Team 1/2 scheduler |
| Hyperliquid: 1200 REST weight/min, 1 req per 1 USDC traded, 1000 open orders default | Hyperliquid docs | crypto scheduler |

### 7.3 Regulatory non-negotiables R9–R14 (added by council fix V14)

| # | Rule |
|---|---|
| R9 | no order without valid exchange algo ID/tag |
| R10 | throttle ≤ TOPS (10 OPS) unless registered algo |
| R11 | no market/IOC orders from algo paths |
| R12 | static-IP-only egress for Upstox |
| R13 | audit trail retained ≥ 5 years |
| R14 | **no India-sourced funding to venues not permitted under FEMA** |

---

## §8 · Training, self-improvement, self-training (how far it can research itself)

![D5 — self-improvement loop](diagrams/d5-selfimprove.png)

**The loop (6 steps, all offline except the live-outcomes feed):**

| # | Step | Detail |
|---|---|---|
| 1 | COLLECT | market ticks · fills · alt-data — all as-of stamped (R1) |
| 2 | LABEL | triple-barrier / forward returns **net of costs** (E7 cost model in the label) |
| 3 | SEARCH (Mode R) | GP formula search · ML discovery · **meta-labeling** — the research record guides the next batch |
| 4 | GATE (E4) | CPCV (purge = max label horizon + embargo, folds grouped by calendar day across symbols, untouched final holdout) · **DSR ≥ 0.95 vs effective decorrelated trial count** (G4) · crisis replay (EOD index-option history) |
| 5 | SERVE | frozen artifact → Mode T (registry R3) |
| 6 | MONITOR | rolling IC · PSI drift · live P&L → **retrain: scheduled + drift-triggered** |

**Self-improvement evidence (web-verified):**

- **Meta-labeling (López de Prado):** secondary model on primary signals lifts **PF in 38/42 instruments, DSR in 39/42** (crypto + US equities + FX, daily resolution — **single-source, unreplicated; extrapolation to NSE intraday options unproven**, council fix C14) — the meta-label decides *whether to act* and *bet size*; labels come from the **signal log** (all primary signals, taken or not), not the fill ledger.
- **Automated discovery:** AQuA recursive self-improvement (research-record loop) — Sharpe ~+2.0 walk-forward; QuantEvolve evolutionary multi-agent strategy creation outperforms baselines. **Both single-team arXiv results, no independent replication (council fix C14).**
- **Retraining cadence:** scheduled (daily drift checks / weekly review) + drift-triggered — trigger on **edge degradation (rolling net-of-cost IC vs frozen baseline)**, not raw feature PSI (IV is non-stationary by design → retraining storms, council fix C9); **every retrain+re-gate cycle logged as a trial in G4**.
- **Honesty backbone:** every Mode R trial is logged (G4) → DSR denominator with a **pre-registered search budget capped at 10³ candidates per style** (council fix C3: raw 10⁶ search would make the deflation bar unreachable); failure condition defined up front — *if research can't clear DSR ≥ 0.95 after the capped budget, the scope does not trade.*

**What it may NOT do (hard ceiling):** self-edit Mode T artifacts (G1), place orders from
research (G3), put an LLM in R1/R2 (R3), or enable a style without E4 (R7). "Self-upgrading"
= new frozen artifacts through the gate — **never live code rewrite.**

### 8.5 Technique-coverage matrix (owner Q, Session 29 — "can the agents do all of this by themselves?")

**Short answer: yes for ~90% of the quant-technique list — already built in. A few are
excluded by the owner's own rules. Caveat: agents run these as *code*, not "understanding."**

| Technique | Where it lives | How the agent uses it |
|---|---|---|
| Monte Carlo simulation | E5 governor + Mode R | worst-case paths before every size; research stress-tests; **fan = swarm harness §8.7** |
| Walk-forward optimization | E4 gate (CPCV + walk-forward) | automatic in every promotion |
| Sharpe · Sortino · Kelly · VaR · efficient frontier · CAPM-beta · CLT · Bayes | E4 metrics + E5 sizing | every candidate and live position |
| Bootstrapping · hypothesis testing · OOS validation | Mode R method 1 + E4 | every claim gets a resampled null distribution |
| Volatility clustering · GARCH | Mode R + E1 | candidate models for vol-alpha / gamma cells |
| Regime detection | **regime router** (rules-only, default off) | computed each tick |
| Stat-arb · cointegration · OLS · PCA/SVD · Cholesky · Kalman | Mode R method 2 + E1 | exactly the stat-arb PAIR stack |
| **Multiple testing** | **G4 trial logging + DSR** (honesty backbone) | every trial counted; gate punishes fishing |
| Multi-timeframe analysis | E1 features | tick → daily bars, as-of stamped |
| Strategy routing | regime router (Session 26) | enables 0–1 styles/symbol |
| Adaptive risk | E5 governor | size scales with DD ladder + cost floor |
| Execution monitoring · trade analytics | E7 ledger | fill vs expected, slippage, per-style cost curves |
| Portfolio intelligence | L2–L4 + E5 | correlation, net exposure, cross-team limits |
| Black-Scholes · Greeks · binomial tree · finite diff · delta-gamma Taylor · convexity | E1/E5 option math (+ Upstox live Greeks) | verify broker Greeks, recompute deltas for sizing |
| 0DTE · gamma exposure · gamma levels · open interest | option features (OI in §9 data) | features for vol-alpha + gamma-scalper |
| Intraday seasonality · factor neutrality · regime-dependent momentum | Mode R candidates + E1 | researched per symbol, promoted or rejected |
| Time & sales · footprint · delta · VWAP · orderflow · volume/market profile · absorption · imbalances · auction market theory | **E1 microstructure features** (Team 2 scalping's food) | needs **tick + depth feeds** — §9 gap, procure W0/W4 |
| Heatmap | monitoring dashboards | trivial |

**Excluded by the owner's own rules:**

| Technique | Why |
|---|---|
| Market making | crypto MM deferred (Hyperliquid rebate tier) · equity MM conditional (S1) · options-MM impossible on our venues |
| 0DTE **selling** / short-gamma harvesting | buy-only rule (D4) — 0DTE exists only as **long-gamma buying** |
| Any of it as ML in the hot/risk path | R2/R3 non-negotiable — ML runs offline in Mode R only |

**The honest caveat:**

| "By themselves" means | |
|---|---|
| Offline (Mode R) | ✅ fully automatic — unattended, 10³ candidates/style cap, logs every trial, may fail |
| Online (Mode T) | ❌ frozen code only — no learning, no improvisation |
| Promotion | owner-approved, ≤5 decisions/week (council fix O8) |
| **Capability ≠ profit** | the agent can *run* any of it — surviving DSR ≥ 0.95 is the gate's job, not the library's |
| Data dependency | footprint/absorption/time&sales need tick+depth data (§9 gap); historical chains needed for Greeks research |

### 8.6 Memory architecture — episodic · semantic · procedural · working (owner Q, Session 30)

**All four memory types already exist in the design — under different names. The honest
twist: for non-LLM code agents memory is EASIER and more reliable than for LLM agents,
because every "memory" is a file/database, not a model guessing what it remembers.**

| Memory type | Human/LLM meaning | **Fleet equivalent** | In design? |
|---|---|---|---|
| **Working** | scratchpad, current thoughts | live process state: current tick, computed features, open positions, regime state, DD state — inside each Mode T cell + L0–L4 | ✅ E1/E2/E5 |
| **Episodic** | "what happened to me before" | **E7 ledger** (signal → fill → slippage → P&L, every trade) + **G4 trial log** (every research attempt, pass or fail) + shadow history + decay events | ✅ E7/G4 |
| **Semantic** | facts & knowledge about the world | **R3 registry**: frozen artifacts · cost model · session/expiry calendars · venue rate limits · ban status · **regime→style map** | ✅ R3 |
| **Procedural** | skills, "how to do things" | **the code itself**: frozen formulas, TWAP/VWAP procedures, exit ladder 1–6, governor rules, §6A runbook | ✅ code + §6A |

**Why this memory beats LLM memory:**

| LLM memory problem | Fleet answer |
|---|---|
| Hallucinated memory ("I remember X" that never happened) | **impossible** — episodic = append-only ledger; not in the log = didn't happen |
| Silent overwrite/decay | frozen + versioned + feature-hashed artifacts (council fix A4); rollback tested |
| Can't audit a decision | full replay: same as-of data + same artifact = **same decision, bit-for-bit** |
| Forgets failures, retries them | **G4 counts every failed trial** — the research agent cannot forget what was already tried |

#### Memory write-authority policy (the gap this session fills — enforced, not prose)

| Memory | Who may WRITE | Who may READ | Retention |
|---|---|---|---|
| **Working** | only that cell (its own state) | governor L0–L4 | process lifetime |
| **Episodic** (ledger, trial log) | **append-only** — E7/G4 only, **nobody edits, ever** | everyone (research feeds on it) | **≥5 years** (R13 audit trail) |
| **Semantic** (registry) | **only via E4 promotion** — never the cell itself (G1) | everyone | versioned forever (rollback needs history) |
| **Procedural** (code/runbook) | **humans via git + versioned releases — never live-edit by any agent** | everyone | git history |

**Enforcement (W0b):** episodic stores are append-only filesystems/DB roles (no UPDATE/DELETE
grants); semantic writes require the E4 signature; procedural = git only, Mode T mounts
read-only (council fix A8).

**Honest exclusion:** vector-store / RAG "agent memory" (the LLM kind) is **not needed in
Mode T at all**. It may appear *optionally* in Mode R (R3 rail allows LLM there) — e.g., the
research agent recalling past hypothesis notes — but it can only **suggest** candidates;
every promotion still passes the deterministic E4 gate.

### 8.7 Swarm-simulation harness — stolen pattern, deterministic engine (owner decision, Session 32)

**The steal (owner: "steal that feature and adopt that too"):** MiroFish's orchestration
*shape* — seed one scenario → fork thousands of parallel worlds with per-world state →
aggregate every world into one report. **The engine is NOT stolen and NOT LLM:** each world
is one deterministic math cell (frozen rule + fixed parameter vector + seeded RNG). MiroFish
itself was rejected as a dependency (AGPL + LLM swarm = slow, paid, non-replayable); only
the pattern is adopted.

| MiroFish piece | Our deterministic counterpart |
|---|---|
| seed info | one hypothesis + scenario manifest (data slice, date range, cost assumptions) |
| thousands of LLM agents | **N parallel math cells** — same rule, different parameter vector / regime slice (grid or Sobol-sampled) |
| per-agent memory | each world's carried state (position, cash, style flags) |
| digital worlds | one seeded-RNG market path or one walk-forward window each |
| prediction report | **aggregate distribution**: fan of equity curves, percentile bands, worst-case path, survival fraction |

**Why this is fast:** vectorized NumPy/Numba pass — 10⁴ worlds × 10 yrs daily ≈ seconds on
one CPU, free, **bit-exact replay per world** (seed + parameter hash = world fingerprint,
ties to the §8.6 replay-gate idea). An LLM swarm would be ~10⁴ inference calls per tick —
hours, metered cost, and no two runs identical.

**What it computes (Mode R only):**

| Use | Question it answers |
|---|---|
| Parameter-ensemble robustness | does the edge survive across the whole neighborhood, or only the one tuned point? (anti-overfit view of E4) |
| Monte Carlo scenario paths | E5 worst-case inputs — the distribution, not the single backtest |
| Regime/capacity stress | does it survive every regime slice and doubled size? |
| O1 predictions exploration | fork plausible futures → count outcomes — **suggest-only**, never a Mode T input |

**Hard rules (unchanged):** worlds live in Mode R (R3) · aggregate output feeds E4 as
*evidence*, never as a promotion signal by itself · every world run logged in G4 (counts
against the 10³/style search budget when it explores parameters) · **no LLM inside any
world** (R2/R3) · determinism is the point — same seed + same artifact = same fan, always.

---

## §9 · Data requirements (what these agents need to train)

| Data class | Sources | Used by |
|---|---|---|
| 1-min+ candles, 6 months history max (Upstox limit) | Upstox candles API | all stock cells (longer history via EOD bhavcopy archive) |
| Ticks / order book | venue WS (Upstox WS v3: 50 instr/conn; cTrader WS; Hyperliquid book) | scalping, MM, microstructure |
| Option chain + Greeks (50 instruments/req) | Upstox option Greeks API (iv, delta, gamma, theta, vega, oi) | all 4 option styles |
| F&O open interest, ban status, expiry calendar | NSE circulars + Upstox | E5 entry gates |
| Macroeconomic calendar, rate decisions | FRED, central-bank calendars | carry, news-fade, vol-alpha |
| COT / positioning, news wire | alt-data feeds | Team 1 research, shock-news (S4) |
| Funding rate, OI, liquidation feed | Hyperliquid info APIs | crypto cells |
| Own fills / slippage (E7) | internal ledger | cost model, meta-labeling labels |

### 9.1 Data gaps the council flagged (must close before research)

| Gap | Impact | Fix before |
|---|---|---|
| **No historical bid/ask for NFO/BFO** (Upstox candles = OHLC only) | E7 would put an *assumed* spread in the label → "net of spread" is circular | W1 — procure historical option tick/chain vendor, or reconstruct EOD spreads from bhavcopy and state the limitation |
| **No historical intraday option chains/Greeks** (Upstox Greeks = live only) | vol-alpha + gamma-scalper **unresearchable at operating resolution** | W1 — same vendor; else demote those 2 styles to EOD-resolution research |
| **1-min candles only 6 months back** (Upstox) | crisis replay can't run on intraday option history | W0 — build EOD bhavcopy archive; scope crisis replay to EOD index-option history |
| Stock options monthly + thin → low trade counts | underpowered per-symbol stats (council fix C12) | pool symbols hierarchically; day-1 statistical claims restricted to NIFTY/SENSEX weeklies |

---

## §10 · Latency and operations-per-second budget

| Path | Budget | Evidence class |
|---|---|---|
| Tick → E1 features | **< 1 ms P99** (in-process; **IPC fan-out cost measured in W0** — shared feature server feeds all Mode T cells, council fix A1) | design target — unmeasured |
| E2 signal eval (frozen formula/ONNX) | **< 1 ms P99** | design target |
| E5 governor checks | **< 0.5 ms** (deterministic, in-memory) | design target |
| **Hot-path total (cell → order)** | **< 5 ms P99 internal** + venue network RTT | design target |
| Upstox order exec | **< 45 ms** — *vendor performance claim, not a regulation* (council fix V6) | web-verified vendor docs |
| Hyperliquid public API order round-trip | ~600–700 ms → crypto cells scoped **"sub-second", not "every tick"**; in-house node = optional W5 milestone | web-verified |
| cTrader order path | proxy-routed AWS Global Accelerator; **500 orders/min = demo ceiling — production cap to verify** (council fix V11) | web-verified (demo) |
| **Ops/second — Upstox orders** | 10/s regular · 50/s SEBI-registered — **central E6 scheduler, token bucket, per-cell fair-share, reserved preemption lane for exits/kill-switch** (council fix A3) | web-verified (Upstox docs) |
| **Ops/second — Upstox market data** | 50 req/s standard; WS 50 instr/conn → 59 symbols ≥ 2 connections + failover; staleness detector → **forced-flat on stale feed** | web-verified |
| **Ops/second — Hyperliquid** | 1200 REST weight/min; ~100 actions/10s practical; book WS 100–200ms | web-verified |
| **Ops/second — cTrader** | 50 non-hist req/s | web-verified |
| Compute | **75 Mode T + up to 75 Mode R processes** — Mode R gets CPU affinity/cgroup isolation so GP searches can't steal hot-path cycles; throughput table (tick rate × cells + research cores) built in W0 (council fix A2) | design — unmeasured |

**Quality/optimization levers:** one process per Mode T cell (G6, crash isolation) ·
frozen artifacts = no runtime compilation · **artifact = weights + feature-hash + schema-version;
E1 refuses mismatched artifacts; atomic write + tested rollback in W0** (council fix A4) ·
as-of join once, reuse · co-location: NY4 (IC Markets), Upstox colo, Hyperliquid node later ·
shadow-first promotion · **startup invariant: reconcile ledger vs venue positions before any
order; idempotent client order IDs on all 3 adapters** (council fix O3).

---

## §11 · Build plan — waves and time estimates

| Wave | What | Est. | Gate to exit |
|---|---|---|---|
| **W0a — data foundation** | R1 data rail · point-in-time replay engine (leakage tests) · EOD bhavcopy archive · **historical option spread/chain vendor decision** | **4–5 weeks** | data replay passes (no leakage) |
| **W0b — engines + gates** | E1/E7 · registry R3 (**versioning + feature-hash + rollback**) · E5 skeleton · **E4 harness (CPCV/DSR/crisis-replay infra)** · E2 router · L0–L4 kill cascade · G5 arbiter · central E6 scheduler (token bucket + preemption lane) · SEBI order-tagging adapter + static-IP/OAuth integration test · **IPC/CPU throughput measurement vs <1ms budget** · signed artifacts + read-only Mode T mounts · **memory enforcement: append-only DB roles for episodic stores (no UPDATE/DELETE grants) + E4-signed semantic writes** (§8.6) | **4–5 weeks** | governor unit tests, rollback tested, IPC overhead measured |
| **W1 — index options (pilot)** | NIFTY + SENSEX: 4 buy-styles researched (Mode R) → E4 → 2–4 cells shadow · **swarm-simulation harness (§8.7: seed → 10⁴ deterministic worlds → aggregate, vectorized)** | **10–14 weeks** (council fix Q13) | DSR ≥ 0.95 on ≥1 style + 3-mo shadow start |
| **W2 — stock options wave** | optionability/liquidity screen (S3) → top-liquid names → hierarchy comms (S4) | **8–10 weeks** (overlaps W1 shadow) | shadow PF > 1 net of costs, hierarchy tests pass |
| **W3 — FX Team 1** | 11 symbols × styles, cTrader adapter, NY4 VPS | **6–8 weeks** (overlaps W2) | cost floor per style proven in E7 + shadow start |
| **W4 — FX Team 2 scalping** | gold + WTI scalping cells, separate account | **4–6 weeks research + 2–3 mo attempt accumulation** (500 attempts don't fit research window, council fix Q13) | net-per-attempt positive over 500+ attempts |
| **W5 — crypto** | BTC/ETH/HYPE 4 styles + PAIR (+ optional in-house node milestone) | **6–8 weeks** | funding/cascade cells clear E4 |
| **W6 — self-improvement loop** | meta-labeling, drift triggers, research record → auto-batches — **moved AFTER W2** (needs ≥6 months of logged signals, council fix Q8) | **4–6 weeks** | drift detector catches injected decay |

**Mode R toolkit (Session 31 survey, W1 inputs — detail in
`Research/notes/SESSION-31-TOOL-EVALUATION.md`):** adopt **PySR** (formula search → E2) ·
**MAPIE** (conformal intervals → E4/E5) · **arch** MCS/SPA (G4 multiple-testing toolkit) ·
**hftbacktest** fill-model checklist (feed latency + order latency + queue position) as the
E4 simulation-honesty reference · **vectorbt blocked pending LICENSE read** (Fair-Code
commercial restriction).

**Honest calendar (council fixes Q7/A6):** shadow windows run **in parallel** with later
waves. **"Built" ≈ 7–9 months; "fully live" ≈ 13–15 months** (W1 shadow alone is 3–6 months
before its cells go live). Add **30–50% schedule slack** for ops load — from month ~2 the
owner is builder + researcher + on-call (council fix O7).

Day-1 = wave 1 = **2–4 live cells**, not 500. Every wave is reversible: artifacts demote
via decay monitor **as a state machine: flatten-and-confirm → disable → hand off** — never
instant-disable with an open position (council fix A9).

---

## §12 · Provenance — what is measured vs claimed

Evidence sub-grades added per council fix Q15: **P** = peer-reviewed/regulatory primary ·
**R** = preprint · **V** = vendor docs/white paper · **X** = single-source, unreplicated ·
**E** = extrapolated (proven elsewhere, unproven here).

| Claim | Class |
|---|---|
| 75 agents / ≤59 stocks / 11+2 FX / 3 crypto rosters | **Owner-locked** (Sessions 27–28) |
| Specialist cell per style vs one generalist | **Measured (internal)** — Session 22 ScaleZero (attach artifact in W0) |
| SEBI 93%/91.1% losers | **Web-verified, P** — SEBI/PwC primary regulatory study |
| Pillai both-sides-negative | **Web-verified, R** — SSRN preprint |
| CTA diversification pro | **Web-verified, V** — industry white papers (vendor-adjacent) |
| Meta-labeling 38/42; AQuA Sharpe ~+2.0 | **X — single-source, unreplicated**; daily US/crypto/FX → NSE intraday options extrapolation **unproven (E)** (council fix Q14) |
| Venue rate limits, lot sizes, algo tagging | **Web-verified, P/V** — SEBI/NSE circulars (P) vs vendor docs (V) |
| <45ms Upstox | **V** — vendor perf claim, not regulation |
| <1ms/<5ms hot-path budgets | **Design targets — unmeasured until W0** |
| Effective independent bets per optionable symbol | **≈1–1.5, not 4** — long-delta/gamma/vega correlate (council fix Q11); breadth/IR claims re-derived on this number |
| Buy-only options profitability | **UNPROVEN until Mode R + E4 + shadow** — failure condition: DSR ≥ 0.95 after capped 10³ budget |
| In-house Hyperliquid node latency | **Unverified** |
| Program capital, opex ceiling, tax treatment | **OPEN — owner-set at W0 (§15)** |

---

## §13 · Open items carried forward

*(Pre-council open-item series — distinct from the council ops O1–O12 in §14.)*

O1 (predictions trunk 0 cells) · O3 (index F&O granularity) · O4 (7 off-index names) ·
O5 (styles per instrument — D4 implies 4–9 per optionable symbol) · O7 (hedge-executor
per-symbol ×N vs one portfolio cell) · **D4** working spec = OPTIONS-BUYING-SCOPE.md ·
onboarding optionability screen sets final N.

---

## §14 · Validation council (owner requirement — 4 domain agents, adversarial review)

**Run 2026-10-27.** Four independent reviewers argued against the draft: quant research
methods · systems/latency · regulatory compliance · ops/human factors. **60 findings total,
18 CRITICAL.** All CRITICAL findings integrated into the sections above (fix IDs in
parentheses); summary:

| Lens | Top CRITICAL findings | Status |
|---|---|---|
| **Quant methods** (Q1–Q15) | DSR ≥ 1 impossible as defined (probability) → **fixed to ≥ 0.95** (Q1) · trial denominator unreliable → pre-registered search space + effective count (Q2) · 10⁶ search makes gate unreachable → **cap 10³/style** (Q3) · CPCV unspecified → purge/embargo/grouped folds specified (Q4) · crisis replay infeasible on 6-mo data → scoped to EOD archive (Q5) · **no historical spreads/chains = 2 of 4 styles unresearchable** → vendor added to W0a (Q6) · shadow timeline excluded from 7–9 mo → **split built/live** (Q7) | ✅ integrated |
| **Systems/latency** (A1–A15) | process-per-cell vs <1ms IPC gap → shared feature server + W0 measurement (A1) · no CPU math → cgroup isolation + throughput table (A2) · E6 throttling stub → central token bucket + kill-switch preemption lane (A3) · R3 no versioning → feature-hash + rollback (A4) · E4/router/kill-cascade unscheduled → W0b (A5) · shadow calendar (A6) | ✅ integrated |
| **Compliance** (V1–V14) | Apr-2026 already in force → W0 blocker (V1) · missing static-IP/OAuth/audit-trail gates → added (V2) · **FEMA bars India-sourced margin to offshore FX — both teams** → pre-W3 gate (V3) · Hyperliquid FIU-IND/tax → pre-W5 gate (V4) · 45ms = marketing not regulation (V6) · **R9–R14 regulatory non-negotiables added** (V14) | ✅ integrated |
| **Ops/human factors** (O1–O12) | no operations section → **§6A runbook/kill matrix/startup invariant added** (O1) · kill semantics undefined → kill matrix (O2) · restart/reconcile missing → startup invariant (O3) · shadow contradiction (O4) · **push-mode human guardrail: no manual size-up, manual trades excluded from E7** (O5) · attention budget (O6) · E4 queue owner-capped ≤5/week (O8) | ✅ integrated |

**Open council items (not fixable in-doc, tracked for owner):** verify cTrader
**production** order limits (V11) · confirm Upstox broker-side algo tagging (V7) · owner to
define "order-balance requirement" mentioned in an earlier session (V12, could not identify
a regulation by that name) · Hyperliquid FIU-IND status is as-of Oct-2026 and may change (V4).

**Council verdict:** architecture and honesty framing sound; delivery was blocked on
gate math (Q1–Q6), run-ability (A1–A6), legal exposure (V3–V4), and operator safety
(O1–O5) — **all now addressed above.**

---

## §15 · Business layer — capital, costs, tax, stop-rules (full-plan crosscheck, Session 33)

> Owner asked: "is planning COMPLETE or did we miss anything?" Full re-read of §1–§14
> verdict: **the machine is planned; the BUSINESS around it was not.** §15 closes that.
> One stale number also fixed (§1 cell ceiling recomputed 481–543).

### 15.1 Capital & account funding (owner sets amounts at W0 — rules are locked now)

| Account | Funds | Currency | Funding rule |
|---|---|---|---|
| A1 Upstox | stocks options (≤59) | INR | option premium paid upfront (buy-only = cheap side); tranche per wave |
| A2 cTrader | FX Team 1 (11 symbols) | USD | **no India-sourced margin until FEMA written opinion (R14, pre-W3)** |
| A3 cTrader (separate) | FX Team 2 scalping (gold+WTI) | USD | same FEMA gate; **isolated — scalping losses cannot eat A2** |
| A4 Hyperliquid | crypto (BTC/ETH/HYPE) | USDC | **pre-W5 gate**: FIU-IND status + 30% VDA + 1% TDS position first |

**Rules:** no commingling between accounts · **capital expands only into a wave whose
gate already passed** (never pre-fund an unproven wave) · per-wave tranche size =
owner decision at that wave's start, recorded in the E7 trial log.

### 15.2 Opex — the program costs money even with zero positions

| Line | Class | When |
|---|---|---|
| Historical option chain/spread vendor (§9.1 gap) | one-time setup + subscription | W0a |
| NY4 VPS (FX), monitoring/backup storage, alerting | monthly | W0b |
| Alt-data (COT, news wire for S4) | monthly | W1–W3 |
| LLM API for Mode R (optional, R3 only) | metered — **optional, may be zero** | W1+ |
| FEMA/CA advisory (one-time opinions) | one-time | pre-W3 / pre-W5 |

**Rule: E7 cost model must include opex** — a style is only "profitable" net of the
infrastructure that carries it. Opex ceiling = owner-set monthly number; exceeding it
pauses research, never trading risk controls.

### 15.3 Tax & accounting (India — design obligations, execution = owner's CA)

| Item | Regime (web-verified §7.2 where applicable) | Fleet obligation |
|---|---|---|
| F&O (stocks/index options) | business income (non-speculative), audited books likely | E7 must export **financial-year P&L + trade archive** in CA format |
| Crypto | 30% VDA on gains + 1% TDS (115BBH/194S) | same E7 export; tax position is part of pre-W5 gate |
| Offshore FX gains | FEMA route + tax on remitted gains — **unsettled until advisor opinion** | R14 gate; E7 keeps native-currency records ≥5 yr (R13) |
| Audit trail | ≥5 years (R13, NSE requirement) | already enforced (§8.6 append-only) |

**New W0b build item: E7 fiscal-year export** (P&L per account + per style + trade list +
deposit/withdrawal log) — the ledger already stores it; the exporter is the missing part.

### 15.4 Program-level stop rules (new — per-style failure existed, program failure didn't)

| Level | Failure definition | Action |
|---|---|---|
| Style | DSR < 0.95 after capped 10³ search | scope does not trade (§8) — exists |
| Wave | gate not passed in its window | wave stalls; later waves hold (§11) — exists |
| **Program** | **by end of W1 shadow (≈month 7–9): zero styles cleared E4 on ANY market** | owner reassesses program (pivot scope/venue/budget) — **new** |
| **Program** | **cumulative realized drawdown > owner-set % of total capital (default suggestion 20%)** | global flatten + hard stop until owner re-authorizes — **new** |

### 15.5 Multi-currency reporting (silent-pollution guard)

E7 reports **per-venue native currency** (INR stocks · USD FX · USDC crypto) + INR
equivalent at a daily reference rate as a **separate conversion line**. Style statistics
are computed in native currency only — otherwise INR/USD drift would contaminate cost
curves and the decay monitor would fire on FX noise, not edge loss.

### 15.6 Pre-go-live verify list (owner/ops items — nothing here is blocked design)

| # | Item | Gate |
|---|---|---|
| P1 | NSE **client position limits** in index options (per-client size ceilings) | before first live cell |
| P2 | Upstox broker-side algo tag confirmation (council V7 = O9) | W0 |
| P3 | monthly kill-drill (feed-stale · venue-down · kill-switch) — **added to §6A runbook** | from W0b |
| P4 | **secrets management**: venue API keys in OS keychain/vault, never config files; rotation schedule | W0b |
| P5 | cTrader production order limits (council V11 = O8) | pre-W3 |
| P6 | FEMA written opinion (R14) | pre-W3 |
| P7 | FIU-IND status recheck (V4) | pre-W5 |
| P8 | historical option chain/spread vendor pick (Q6) | W0a |

### 15.7 Completeness scorecard — the honest final answer

| Layer | Status | Where |
|---|---|---|
| Symbols/styles/architecture/gates/ops/regulatory | **complete, council-validated** | §1–§14 |
| Business layer (capital rules, opex, tax, stop-rules, multi-currency) | **complete as of Session 33** | §15 |
| Remaining unknowns | **W0 measurement items, not design gaps**: IPC latency · opex/capital amounts · vendor pick · broker confirmations | §10, §15.6 |

**Verdict: planning is DONE.** What remains is decided by *doing* (W0), not by more
planning. The first build action = W0a data foundation.
