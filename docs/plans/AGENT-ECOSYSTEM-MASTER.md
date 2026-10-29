# Non-LLM Agent Ecosystem — Master Design (All 3 Classes)

> Master artifact v1 · 2026-10-26. Root-level finalized plan. Companion sources:
> `ROW3-SELF-LEARNING-AGENT-STACK.md` (population design),
> `AGENT-CLASS-DESIGN-TYPE1-2-3.md` (class skeletons), `AGENT-RL-FRAMEWORKS-LIFECYCLE.md`
> (training stack + gates), `DATA-LAYER-ULTRA-LOW-LATENCY.md` (data spine),
> `FULL-SYSTEM-PLAN.md` (three-row system). This doc is the COMPLETE picture to hand
> to implementation: structure, architecture, training, what matters (metrics bible),
> research-paper map, gates — for all three agent classes.

---

## 0 · Executive picture

**One shared skeleton. Three species. Twelve self-improving duties.** All agents of all
classes share the same anatomy (feature bus → policy → deterministic risk gateway →
unified order model); what separates each class is exactly its action space, its
reward, and the auxiliary math it must own. Nothing here contains an LLM — the hot
loop is compiled arithmetic; language models are permanently quarantined in
research/cockpit lanes (R3).

```
                    ┌─────────────────────────────────────────────┐
  engines' data  ⇒  │ SHARED ANATOMY (all agents, all classes)     │
  (PIT, Arrow)      │ feature bus(µs,mmap) → head(payload: typed)  │
                    │ → risk gateway(rules) → order model → venue  │
                    │ offline: replay-train → shadow → ORACLE      │
                    └─────────────────────────────────────────────┘
      TYPE 1 alpha cells          TYPE 2 market makers       TYPE 3 portfolio layer
      (direction edge)            (spread & inventory edge)  (allocation & risk edge)
      act per symbol/pair         act per venue book         act across cells
```

**Honest capability statement (owner's standing rule):** internal loop = µs-class
(measured, CI-benchmarked); venue hop 0.5–50 ms is external; this stack targets
intraday → multi-day horizon edges + 24/7-venue MM, NOT sub-ms colo HFT (R8, upgrade
path preserved).

---

## 1 · Shared anatomy (what every agent is)

### 1.1 The pipeline (identical for all classes)

| Stage | Piece | Class-independent spec |
|---|---|---|
| Ingest | typed events | engine envelope: `{event_id, schema_v, source_id, occurred_at, ingested_at, rights_class, lineage, hazard_flags}` |
| Feature | mmap Arrow columns | 0.1–2 µs read; versioned features (semver, lineage); training reads THE SAME bytes |
| Head | policy payload | `{action_typed, size, confidence:calibrated, ttl}` — the Head Contract (§4) |
| Gateway | deterministic rules | position caps · loss ladders · kill-switch · market-state gates; **never learned** |
| Encode | unified order model | one encoding for every venue adapter; sim↔live bit-identical |
| Observe | attribution logger | PnL decomposed per trade: why made/lost (features + regime tag + execution cost) |

### 1.2 The Head Contract (Jev-pattern, native at µs) — standardized across ALL classes

```
Type 1:  {target_position_delta | spread_delta | greek_targets, urgency, conf, ttl}
Type 2:  {bid_price, bid_size, ask_price, ask_size, skew, conf, ttl}
Type 3:  {target_weights[], order_intents, risk_budget}
```
- Confidence is **conformal-calibrated** on a frozen window: P(profitable | c) is
  measured, not hoped. Miscalibration ⇒ auto-demote to shadow.
- Confidence mechanically drives: position sizing · peer-trust weights · allocator
  belief-quality. Calibration quality propagates; nothing "feels" right, everything
  measures.
- TTL prevents stale decisions from firing late (time-decayed validity).

### 1.3 Lifecycle (identical gates for all classes; designed in AGENT-RL-F…)

```
train(PIT replay) → evaluate(walk-forward+CPCV, champion race) → certify(PSR/DSR)
→ shadow(2–4 wks live, zero risk) → approve(HUMAN, graduated capital) → deploy
→ monitor(attribution + drift + SLO) → retrain(nightly) ⇄ rollback(seconds, signed)
```
Two immovables: agents NEVER touch own risk limits (R7); deployments are
champion-only (one live model per unit, candidates live in shadow rings).

### 1.4 Class-appropriate champion controls (the anti-hype races)

| Class | Deterministic control every candidate must beat |
|---|---|
| Type 1 alpha | supervised GBM/linear forecast-and-trade on identical folds |
| Type 2 MM | static Avellaneda-Stoikov-class spread (no learned adjustments) |
| Type 3 portfolio | inverse-volatility static allocator |

---

## 2 · TYPE 1 — Alpha cells (quant · stat-arb · derivatives · event-driven)

### 2.1 The cell unit (decision-unit design)
One cell = one tradeable decision. Materially: a symbol (most cases) — **or a
PAIR/basket for stat-arb** (RELIANCE-INFY spread · BTC-ETH ratio): the trade IS the
relative value, so the agent's world-model is the spread, not its legs.

### 2.2 Variant design table

| Variant | Action space | Aux learned models | Reward |
|---|---|---|---|
| **Intraday / mid-freq** | target Δ position ±K lots, or cancel | — (policy is the product) | ΔPnL − fees − slippage − λ·turnover |
| **Stat-arb pair/basket** | joint leg Δ | cointegration-strength · spread drift (nightly re-est) | spread PnL + alignment penalty |
| **Futures** | target Δ | basis/rollover calendar · term-structure state | PnL − rollover cost penalty |
| **Options** | greek targets (Δ, Vega, θ-budget) | vol-surface learner; **Greeks engine stays deterministic** | PnL − γ-scalping friction |
| **Event-driven** | Δ + urgency | event-response curves per event-type (from News/World engines + event-study priors); corroboration-weighted signals | event-window PnL − false-positive cost |

### 2.3 Model architecture & training program
- **Family trunks, symbol heads** (refined 2026-10-26): each asset-class family gets its
  OWN trunk (equities-family · crypto-family · futures-family …) because microstructure
  genuinely differs across classes (sessions vs 24/7, auction structure). Within a
  family, one trunk serves all symbols: the trunk learns the family's MARKET PHYSICS
  (momentum/reversal/imbalance/liquidity response — shared mechanics), while each
  symbol's small head learns its PERSONALITY (volatility signature, session rhythm,
  event anatomy, liquidity depth, regime mix). Strategy differences emerge from
  head + per-cell risk parameters (evolved per cell by pycma) + regime heads +
  per-event-type response curves — never from one trunk imposing one strategy.
  Literature (QuantNet, QF 2022): family-agnostic features + market-specific heads
  beat BOTH extremes — fully-shared and fully-separate training.
- **Shared-trunk encoder** (learned from ALL symbols jointly — tensor data is the
  population's teacher) + small per-cell head. New symbol = instant competence via
  trunk, then specializes (learning-fast-by-architecture).
- **Algo ladder** per cell:
  1. supervised baseline (GBM/linear next-N-minute edge) — permanent champion;
  2. RL fine-tune (SAC for continuous sizing · PPO for act/cancel) — only where
     sequential execution matters;
  3. outer evolution (pycma) over hyperparameters, risk parameters, feature sets.
- **Regime dual-heads:** one head per regime bucket (calm · volatile · event-window);
  detector selects at inference; certification is per-head, never blended-only.
- **Nightly training:** replay simulates years of history; new-data incremental
  fine-tune + periodic full-window retrain; seed-invariance CI gate (same seed ⇒
  same weight hash).

### 2.4 What matters most (expert priority — Type 1)
1. **Expectancy AFTER costs** — a signal that earns 2 bp gross and pays 2.5 bp in
   spread+fees is noise: turnover drag is always in the reward.
2. Impact/slipper modeling BEFORE scaling: capacity curves, participation caps.
3. tails: Sharpe is silent about short-vol traps; CVaR enters the risk budget.
4. non-stationarity as first-class state (regime in the OBSERVATION space).
5. event-cell false-positive cost (acting on noise is costed, not free).

---

## 3 · TYPE 2 — Market-making agents (quoting · crypto MM · spread capture)

### 3.1 Species difference
The product is **spread + inventory control**, not direction. Latency-sensitivity is
the highest of all classes; adverse selection is the permanent enemy.

### 3.2 Architecture: analytic core + learned adjustments (never end-to-end RL)

```
fair value (learned: mid + short-horizon drift)
   → Avellaneda-Stoikov-class quote core (deterministic spread+skew math, provable)
   → 2 small learned nets: size adjustment · skew adjustment (within certified bounds)
   → quote risk grid (deterministic: min/max spread, max size, inventory cap)
   → dialer thread (core-pinned, per-event re-price, tens of µs)
```
Why hybrid: end-to-end RL quoting produces unpredictable tail behavior in inventory;
the proven pattern is a provable core with learned tuning inside bounds.

### 3.3 Reward & the inventory problem
`spread_capture − adverse_selection_costs − inventory_risk_penalty − fees(rebates)`
Inventory is first-class: dedicated inventory-forecast head; governor **auto-widens
quotes** as inventory nears cap (deterministic). Both sides quoted simultaneously;
action space includes one-sided pausing under uncertainty.

### 3.4 MM-specific survival gear
poison-tape detector (stale/crossed/flash ⇒ pull quotes instantly) · quote-storm
breaker (cancel/replace rate cap) · one-sided pausing · graduated venue caps ·
**front-run firewall: MM inventory must never front-run the portfolio's own parent
orders** (hard governor separation).

### 3.5 Strategy of entry
Crypto venues first (24/7, accessible APIs, ms-class loop adequate) — and MM's
realized fills calibrate the per-venue cost model that Type 1/3 consume. That
synergy is why MM ships before deep alpha scale.

---

## 4 · TYPE 3 — Portfolio & asset-management layer

### 4.1 Species difference
Money-management errors are the most expensive errors in the system ⇒ this layer is
**deterministic optimization cores with learned inputs**, never a big black box.

### 4.2 Architecture

```
learned inputs                            deterministic cores
belief signals (cells: signed,conf)  →   allocation engine (Black-Litterman-class)
regime states (detectors)            →   risk targeter (σ-target vol scaling,
covariance (shrunk, regime-cond)     →       drawdown de-risk ladder)
cost model (per venue, MM-calib)     →   rebalancer (λ·turnover cost-aware)
exit/kill signals                    →   governor overlay (caps, loss ladders)
```
- Covariance: **Ledoit-Wolf-class shrinkage, regime-conditioned, nightly** — raw
  sample covariance banned (tails are where it lies).
- Allocator is the ONLY component that moves capital ACROSS cells; hysteresis bands
  (no churn from micro-signals).
- Rebalancing: threshold-band triggers (drift beyond band → restore) + calendar
  floor; cost-model-verified; block-transfer logic.
- Risk targeting: σ walks toward target via scaling ladder; drawdown states
  auto-map to de-risk steps — **deterministic table, not a learned policy** (R7).

### 4.3 What matters most (expert priority — Type 3)
1. estimation risk > model choice: covariance regime-conditioning outranks fancy
   optimizers (a wrong Σ poisons any allocator).
2. turnover drag: every rebalance pays the bid-ask; cost model gates it.
3. cross-agent crowding: pooled signals become ONE correlated bet — the governor's
   systemic circuit breaker reads many-cells-flipping-same-way.
4. regime persistence: allocation changes only when regime state CONFIRMS
   (dwell time), not on detector flicker.

---

## 5 · THE METRICS BIBLE (what matters, from a top-firm quant's view)

Grouped into the 5 families our gates actually consume. Single page summary here;
each metric = what it honestly measures + how we compute + trap to avoid.

### 5.1 EDGE family (is there money in the idea?)

| Metric | Definition / formula | Honest role | Trap it prevents |
|---|---|---|---|
| **Expectancy (per trade)** | `E = p·W_avg − (1−p)·L_avg` (+costs!) | the ONLY first-class edge number: everything else is a view of it | raw-PnL euphoria on tiny samples |
| **Win rate** | fraction profitable | diagnostic only — meaningless without payoff | seducing owners into martingale traps |
| **Risk:Reward (R:R)** | avg win / avg loss (in R-multiples) | the other half of the pair | "high win rate" strats with 1:5 payoff dying on one loss |
| **Break-even win rate** | `p* = 1/(1 + R:R)` (before frictions) | sanity floor; add frictions on top | underestimating the friction bar |
| **Profit factor** | Σwins / Σlosses | scale-robust expectancy view (target >1.3 for MF cells) | PF computed on gross (ignores costs) |
| **Expectancy per R-multiple distribution** | histogram of trade outcomes in R | reveals skew/tails a mean hides | truncating losers from reports |

**Win-rate × payoff matrix (expectancy per 1 risked, before-fees):**

| WR ↓ \ R:R → | 1:1 | 2:1 | 3:1 | 1:2 | 1:3 |
|---|---|---|---|---|---|
| **90%** | +0.80 | +1.70 | +2.60 | +0.35 | +0.20 |
| **75%** | +0.50 | +1.25 | +2.00 | +0.25 | 0.00 |
| **60%** | +0.20 | +0.80 | +1.40 | +0.10 | −0.20 |
| **50%** | 0.00 | +0.50 | +1.00 | −0.25 | −0.50 |
| **40%** | −0.20 | +0.20 | +0.60 | −0.40 | −0.60 |
| **33%** | −0.34 | 0.00 | +0.32 | −0.51 | −0.56 |
| **25%** | −0.50 | −0.25 | 0.00 | −0.63 | −0.67 |

⇒ the expert read: a 33% WR strat with 3:1 R:R is a FINE business (E=+0.32); a 60%
WR strat with 1:3 R:R is a slow bleed (E=−0.20). The break-even frontier sits exactly
on p\*=1/(1+R:R) — that diagonal runs 1:1→50% · 2:1→33.3% · 3:1→25% · 1:2→66.7% ·
1:3→75%, and every friction you pay moves YOUR closer to being above it.
**Profitability lives in the PRODUCT of the two, never in either alone.** Our gates
judge expectancy-after-costs per regime, never headline WR.

### 5.2 RISK-ADJUSTED RETURN family (is it a good money machine?)

| Metric | Definition | Honest role | Trap |
|---|---|---|---|
| **Sharpe** | `mean(return)/std × √periods_per_yr` | cross-strat comparability of return-per-unit-vol | tail-blindness: short-vol strats game it |
| **Probabilistic Sharpe (PSR)** | P(true SR > SR*) adjusting for skew/kurt/n (Bailey–LdP) | is the Sharpe REAL given fat tails & few observations | claiming Sharpe from 60 trades |
| **Deflated Sharpe (DSR)** | PSR penalized by N TRIALS run (Bailey–LdP 2014) | our certification number: trials-logged, DSR-gated | p-hacking / trial-count amnesia |
| **Sortino** | mean/downside-deviation (target=0) | vol-down vs vol-up asymmetric quality | treating upside vol as risk |
| **Calmar / MAR** | CAGR / MaxDD | long-game survivability rating | free-lunch hidden in single cycle |
| **Max Drawdown + Time-Under-Water + Recovery Factor** | peak-trough depth/duration; net gain/MaxDD | governor language: DD states drive de-risk ladder | averaging away the worst state |
| **CVaR (Expected Shortfall)** | mean loss beyond VaR-tail | the tail-budget number the governor consumes | VaR alone ignoring loss SHAPE |
| **Ulcer Index** | RMS of drawdown series | smoothness-of-pain metric | ignoring "suffering time" |
| **Information Ratio** | active return / tracking error vs benchmark | portfolio-layer skill vs target | mixing benchmark classes |

### 5.3 CONFIDENCE & STATISTICAL HONESTY family (can we trust the numbers?)

| Metric | Definition | Honest role | Trap |
|---|---|---|---|
| **Calibrated confidence** | conformal coverage: P(outcome ∈ interval) = 1−α, measured OOF | the head contract's number; drives sizing/trust/weights | uncalibrated output passed as probability |
| **Reliability diagram / ECE** | predicted-probability vs empirical-frequency bins | calibration AUDIT; visually checked per regime | calibration drift after deploy |
| **Brier score** | mean((p − outcome)²) | single calibration number per head | only reporting Brier (no regime split) |
| **Accuracy / Precision / Recall / F1** | standard classification quartet | event-cell correctness diagnostics | accuracy alone in rare-event data (99% "no-event" accuracy = useless) |
| **PSR/DSR (also §5.2)** | see above | THE certification stats | reporting point-estimate only |
| **PBO (Probability-of-Backtest-Overfit)** | fraction of CSCV combos where OOS underperforms | overfitting early-warning at design time | ignoring it until the account does |
| **CPCV discipline** | combinatorial purged CV with purge+embargo | multi-path backtests replace single-path | one lucky path passed = "proven" |
| **Seed-invariance hash** | same seed ⇒ same weights | training determinism gate | silent non-determinism changing candidates |

### 5.4 EXECUTION & VOLUME family (can we actually get filled where we said?)

| Metric | Definition | Honest role | Trap |
|---|---|---|---|
| **Implementation Shortfall** | realized avg fill vs arrival price (Perold) | THE cost number of any live run | simulation costs flattered vs reality |
| **VWAP / TWAP slippage (bp)** | fill vs benchmark | participant perception of quality | comparing-ok vs different venue/session |
| **Participation rate** | our volume / venue volume | capacity governor input | exceeding it → impact explodes |
| **Market-impact curve** | slippage vs size (Almgren-Chriss class) | learned, per venue; capacity planning point | assuming LINEAR impact at big size |
| **Fill ratio / queue-loss** | fills ÷ quotes reachable | MM quality metric; queue-position reality | desktop backtests ignoring queue loss |
| **Adverse-selection score** | mid-move after our fills (signed) | top MM health sign: negative score = being picked | averaging away regimes |
| **Adv-based capacity** | alpha decay vs AUM-participation band | scales positions per cell | static fixed-size scaling |
| **Turnover drag** | λ·annual turnover × cost/quant | rewards decay exposure | reported gross only |
| **Funding/borrow/frictions** | perp funding, shorts borrow, FX carry | REAL cost lines in reward | venue-dependent fee tables unmaintained |

### 5.5 PORTFOLIO family (are the parts adding or canceling)

| Metric | Definition | Honest role | Trap |
|---|---|---|---|
| **Contribution-to-Risk** | ρᵢ·σᵢ / portfolio σ | who actually carries portfolio risk | weight ≠ risk |
| **Beta + factor exposures** | regression vs market/factors | hidden bet detection | assuming 0 because "multi-cell" |
| **Regime-conditioned correlation** | peer correlation per regime cluster | crowding detection | chilling sample-Σ based only on calm regimes |
| **Band-compliance** | % time weights in-band | allocator discipline | silent band expansion (leash creep) |
| **Cross-agent overlap** | position similarity across cells | systemic single-bet score | treating cells as independent merely |

### 5.6 TOP-10 "what actually matters" (expert priority — memorize order)

1. **Expectancy after costs** — the edge question, per regime.
2. **Drawdown states** govern capital — NOT returns; governor ladders act on DD.
3. **PSR/DSR discipline** — trials logged, deflated acceptance.
4. **Tails (CVaR)** in the risk budget — Sharpe alone is tail-blind.
5. **Execution honesty** — shadow-realized shortfall ≤ model claim; else freeze.
6. **Regime persistence** before reacting — dwell-time confirmation.
7. **Capacity curves** before scaling — participation ceilings per venue.
8. **Σ quality** (shrunk, regime-conditioned) before optimizer choice.
9. **PIT truth** — no look-ahead, survivorship-certified universe.
10. **Cross-agent crowding** — many cells same direction = ONE bigger bet.

### 5.7 Concrete gate defaults (v1, to tune in the first slice)

| Gate | Threshold (v1 default) |
|---|---|
| Type-1 cell certified | DSR ≥ 0.9 with logged trials-count; expectancy > controller on same folds |
| MM certified | realized adverse-selection ≤ +10% of model claim, beat static core, one-sided-pause ≤ 2 events/wk |
| Portfolio certified | rebalance IS within model ±20%; de-risk ladder fired correctly in replayed crashes |
| Calibration gate | ECE < 0.02 per regime bucket before deployment |
| Shadow→live | shadow drift (decision parity) ≥98%; realized cost ±20% of sim |
| Latency CI | P99 (< 100 µs internal) — benchmark hops each merge |

---

## 6 · RESEARCH-PAPER MAP (why each matters for us)

Verification note: classics below are decades-stable; the 2025-26 surveys verified live
2026-10-26. Sources mapped to the exact component they teach.

### 6.1 Market microstructure & market making
| Paper | What it gives us |
|---|---|
| **Avellaneda & Stoikov 2008** — "Optimal M in Limit Order Book Markets" | Type-2 quote-core math: spread+skew from inventory risk aversion — our deterministic MM base |
| **Guéant, Lehalle, Fernandez-Tapia 2013** — dealer's optimal quoting generalization | robustness layer: closed-form HJB solutions extend A-S to rougher regime assumptions |
| **Cartea, Jaimungal, Penalva** — Algorithmic & HFT book | inventory + adverse-selection dynamics menu of models |

### 6.2 Execution & market impact
| Paper | What it gives us |
|---|---|
| **Almgren & Chriss 2000** — Optimal Execution of Portfolio Transactions | impact model + TWAP/POV trade-off formulation (basis of our execution module + capacity curves) |
| **Perold 1988** — Implementation Shortfall | the cost benchmark family itself (arrival price IS metric). Foundation of §5.4 |
| **Bouchaud, Farmer, Lillo** — limit order book empirical laws | price-impact empirical regularities the impact model is calibrated against |

### 6.3 Statistical evaluation & backtest honesty
| Paper | What it gives us |
|---|---|
| **Bailey & López de Prado 2012** — The Sharpe Ratio Efficient Frontier / **PSR** | certification statistic #1 — is Sharpe real with fat tails & short sample |
| **Bailey & López de Prado 2014** — **Deflated Sharpe Ratio** | certification statistic #2 — multi-testing discipline; our cert gate number |
| **Bailey et al 2017** — PBO via CSCV (Probability of Backtest Overfit) | design-time overfitting early warning |
| **LdP 2018** — Advances in Financial ML (CPCV, purged k-fold, meta-labeling) | our validation machinery; meta-labeling idea = the sizing-on-confidence layer |

### 6.4 ML asset pricing & supervised edge
| Paper | What it gives us |
|---|---|
| **Gu, Kelly, Xiu 2020** — Empirical Asset Pricing via ML | supervised baselines benchmark standard; feature-importance housekeeping discipline |

### 6.5 RL in finance (current surveys, all live-verified 2026-10-26)
| Paper | What it gives us |
|---|---|
| **arXiv:2512.10913 (Dec 2025)** — RL in Financial Decision Making; 167 papers 2017-2025 | the definitive field map: market making / portfolio / trading RL — gives our course corrections |
| **Wang et al 2025 (ESWA)** — survey of RL for investment decisions/options hedging/MM | componentized view matches our class split |
| **Kolm & Ritter** — Modern Perspectives on RL in Finance | honest framing of where RL actually adds value vs supervised (feeds our anti-hype champion design) |

### 6.6 Hedging & derivatives RL
| Paper | What it gives us |
|---|---|
| **Buehler et al 2019** — Deep Hedging | options-cell hedging: RL as convex-optimization alternative under risk measure; taught our greeks-target action space |
| **BlackRock researchers 2025** — RL for dynamic hedging of index options | production evidence the path matures (live-verified) |

### 6.7 Safe/constrained & risk-sensitive RL
| Paper | What it gives us |
|---|---|
| **García & Fernández 2015** — Safe RL survey | taxonomy of safety shields — maps 1:1 to our governor concept |
| **Achiam et al 2017** — Constrained Policy Optimization (CPO) | the constrained-update inspiration between training eagle + gateway |
| **Tamar et al 2016** — CVaR RL optimization | direct CVaR-objective training = our tail-first precinct |

### 6.8 Portfolio construction & estimation
| Paper | What it gives us |
|---|---|
| **Markowitz 1952** | mean-variance base (know why it breaks with wrong Σ) |
| **Black & Litterman 1992** | belief-blend posterior precisely analogous to our allocator ingesting signed cell beliefs |
| **Ledoit & Wolf 2004** — covariance shrinkage | our Σ estimation default (shrunk, regime-conditioned) |
| **Kelly 1956** | sizing: full-Kelly banned (estimation noise) → fractional-Kelly ceiling in governor |

### 6.9 Non-stationarity, regime & conformal trust machinery
| Paper | What it gives us |
|---|---|
| **Hamilton 1989** — Markov-Switching models | HMM-class regime detector design |
| **Page 1954 /CP Hinkley 1971** — CUSUM | fast-trigger change-point detectors |
| **Lo 2004** — Adaptive Markets Hypothesis | intellectual foundation: edges decay, refill, regimes — informs nightly retrain cadence & evolution layer |
| **Vovk et al / Shafer-Vovk 2008; Angelopoulos & Bates 2021** — conformal prediction | calibrated-confidence machinery of the Head Contract |
| **JPM/Citi/LdP TCA lines (Kissell)]** | execution-quality measurement vocabulary behind §5.4 |

---

## 7 · Failure-mode defenses (complete, all classes)

| # | Failure mode | Defense | Enforcement |
|---|---|---|---|
| 1 | Non-stationarity | regime detectors (CUSUM/HMM) in obs space; rolling windows; DD-state de-risk | data layer + governor |
| 2 | Overfitting | CPCV+purge/embargo; DSR with trials-logged; parameter-budget caps | certification algorithm |
| 3 | Look-ahead | as_of()-only data channel; availability-time features; embargo | physically enforced at data layer |
| 4 | Survivorship | membership-at-date universe; certificate required, else run refuses | backtester hard gate |
| 5 | Data leakage | purge/embargo in time + cross-instrument; feature-version isolation | PIT rules + lineage manifest |
| 6 | Regime break (live) | governor: capital cut + correct-regime-head swap; dwell-time confirm | deterministic wiring |
| 7 | Reward hacking | independent audit-metric set (TCA, turnover-adj PnL, risk-adjusted path) read by committee; reward function versioned | committee, not agents |
| 8 | Training instability | seed-invariance CI; grad-clip + LR schedule + NaN-watch; min-stability epochs | train gate |
| 9 | MM adverse tail inventory | A-S core + certified-bound nets; auto-widen; one-sided pause | quote risk grid |
| 10 | Execution-cost drift | shadow-realized IS vs sim claim; ±20% freeze-and-recalibrate | monitor gate |
| 11 | Cross-agent crowding | overlap score + systemic circuit breaker | governor |
| 12 | Calibration rot | ECE per-regime recheck post-deploy | monitor gate |

---

## 8 · Build order & verification gates (to implementation)

```
M1 Type-1 slice (1 symbol, trunk+head, replay train, shadow, human gate 1)
M2 Type-3 lite (static allocator + governor + 1 risk target)
M3 Type-1 derivatives/stat-arb/event variants (3 engines wired)
M4 Type-2 MM (crypto venue first; fills calibrate cost model)
M5 execution module (size scales beyond starter bands)
every milestone ships with: latency CI, seed-invariance CI, champion race, attribution logging
```

## 9 · Honest limits
- venue hops bound alpha take-rate; our moats = µs-internal + PIT truth + 981 unique-
  data connectors + nightly continuous learning + fused 3-engine event context.
- population edge ≠ per-cell edge: cross-agent crowding must be governed.
- RL must beat the GBM control race to exist; supervised baseline keeps office honest.
- Meta-labeling & conformal confidence = the trust layer, not raw model outputs.

**Name note:** system name pending owner reveal; all class names herein are functional
placeholders.
