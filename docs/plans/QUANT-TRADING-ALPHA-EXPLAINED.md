# QUANT TRADING & ALPHA EXPLAINED — plus the one-cell offline+online research design

**Session 26 · 2026-10-27 · owner-requested deep explanation (web + paper researched)**
**Covers:** what quant trading is · how the math works · how alpha is found · what finding it
needs · the per-symbol research cell (offline) and how online cells consume proven artifacts ·
one-cell two-mode design · candlestick "sighting" · Kronos foundation-model evaluation.
LLMs never in hot path (R2/R3). Every claim is measured, web-verified with source, or marked unverified.

---

## §1 · What quant trading IS

| | Discretionary trader | Quant trader |
|---|---|---|
| Decision from | experience, gut, charts | **a mathematical rule computed from data** |
| Can explain the trade? | "I feel the trend" | "signal X > threshold, because historically this predicted Y with IC 0.04" |
| Speed | minutes–hours of thinking | microseconds–seconds of code |
| Consistency | changes mood to mood | identical in every condition |
| Scaling | 1 human = 1 chart | 1 system = 73 symbols |

**One sentence:** quant trading = *turning a statistical edge into a repeatable rule, then letting computers execute it without emotion.*

---

## §2 · How it works — the pipeline

```
DATA → SIGNAL (alpha) → PORTFOLIO → EXECUTION → RISK → LEDGER
         ▲                                     │
         └────────── feedback (what actually happened) ──┘
```

| Stage | What happens | Our engine |
|---|---|---|
| 1. Data | pull prices, news, fundamentals, book — stamped "as-of" time | E1 + data layer |
| 2. **Signal (alpha)** | compute a score predicting future return | E1 features → E3 model |
| 3. Portfolio | convert score → position size | allocator |
| 4. Execution | place orders with minimal cost (TWAP/VWAP/limits) | E6 |
| 5. Risk | cap loss, kill switch, drawdown ladder | E5 (deterministic) |
| 6. Ledger | record everything, reconcile nightly | E7 |
| 7. Feedback | measured P&L → retrain or retire the signal | E3/E4 |

---

## §3 · The math

### Alpha (Jensen) — what "alpha" literally means

```
Your return = β × market return  +  α
              (riding the market)     (your actual edge)
α = R_portfolio − [R_f + β(R_market − R_f)]
```

If β explains all the profit you just held an index and paid fees. **Alpha = what remains after removing everything explainable.**

### The Fundamental Law — Grinold & Kahn (1989/2000)

```
IR  ≈  IC  ×  TC  ×  √BR
```

| Term | Meaning | Real-world value |
|---|---|---|
| **IR** | information ratio = active return per unit risk | > 1 good, > 1.5 excellent |
| **IC** | forecast-vs-outcome correlation | **0.02–0.07 real markets; > 0.10 suspicious = usually overfitting** |
| **TC** | transfer coefficient — signal→trade quality after costs | 0.7–0.9 realistic |
| **BR** | breadth = independent bets per year | the multiplier |

**Worked numbers:**

| Scenario | IC | BR | TC | **IR** | Verdict |
|---|---|---|---|---|---|
| One symbol, 1 bet/month | 0.05 | 12 | 0.9 | **0.16** | ❌ useless |
| One symbol, 1 bet/day | 0.05 | 250 | 0.9 | **0.71** | ⚠️ okay |
| One symbol, ~10 independent bets/day (scalping) | 0.03 | 2,500 | 0.7 | **≈ 1.05** | ✅ good |
| 600 stocks monthly (institutional) | 0.05 | 72,000 | 0.8 | ~10 theoretical | the fund game |

**Three proofs from this law:** (1) small IC is normal — 0.05 ≈ 52.5% pair-ranking, edge comes from repetition; (2) one symbol works *only* at high bet frequency; (3) bets must be **independent** — correlated bets collapse √BR.

### Honesty formula — Deflated Sharpe Ratio (Bailey & López de Prado 2014)

```
Test 1,000 variants → the BEST looks great by luck.
DSR = probability your Sharpe is real after counting every trial run.
```
**Our gate DSR ≥ 1.** The research cell must log `trials_run`; DSR is computed against total search count. *This is the single most important line of code in the research agent.*

### Decay formula — why speed matters

| Finding | Source |
|---|---|
| Published anomalies lose **~50%** of returns post-publication | McLean & Pontiff 2016 |
| After costs, decay reaches **~93%** for the typical anomaly | Cambridge JFQ 2023 |
| Decay worsens **~5 ppt per year** | *Quantitative Finance* 2022 |
| Only **7.58%** of stock alpha estimates are statistically significant (t>2) | BFI Chicago WP 2024 |

Private/unpublished alpha decays slower — **finding your own beats copying papers.**

---

## §4 · How alpha is found — 5 methods

| # | Method | How | Non-LLM? | Evidence |
|---|---|---|---|---|
| 1 | **Hypothesis-first** | economic logic → test | ✅ | the classical way |
| 2 | **Formula search (GP / symbolic regression)** | machines compose/mutate formulas 100,000×, score by IC | ✅ | AlphaForge (AAAI 2025), RiskMiner (MCTS), warm-start GP (arXiv 2412.00896), gplearn |
| 3 | **ML pattern discovery** | GBM/NN learn mapping | ✅ | our E3 |
| 4 | **Alternative data** | satellite, card, news NLP | ✅ (NLP separate) | AIMA: leaders' top choice |
| 5 | **Microstructure** | order book (OFI, microprice) | ✅ | FX scalping plan §3 |

**Methods 1+2+3 are what the non-LLM research agent runs. GP = "build your own alpha formulas automatically" — worked since the 1990s, no LLM needed.**

---

## §5 · What you NEED to find alpha — the six

| # | Requirement | Do we have it? |
|---|---|---|
| 1 | Point-in-time data (no leakage) | ✅ PIT assembler |
| 2 | Compute (search 10⁴–10⁶ formulas) | ✅ E3 + Colab GPU |
| 3 | Statistical discipline (DSR, CPCV, trial logging) | ✅ E4 gates |
| 4 | Breadth (√BR) | ✅ 73 cells = portfolio BR |
| 5 | Cost model in the label | ✅ E7 feedback |
| 6 | Speed + privacy (decay clock) | ⚠️ the real bottleneck |

---

## §6 · The per-symbol RESEARCH CELL (offline) — spec

**The owner's ask:** give the offline agent hypothesis-first + GP/symbolic search + ML discovery + alternative data + microstructure + fundamentals/technicals/indicators/strategies/candlesticks — and have online agents use what proves out.

| Owner's ingredient | Research-cell implementation | Status |
|---|---|---|
| Hypothesis-first | economic-logic templates → candidate statements, each with a falsification test | to build |
| Formula search (GP/symbolic) | expression-tree search over operators (rank, corr, decay, rolling, OHLC math), fitness = IC, **every trial logged** | to build (proven method) |
| ML pattern discovery | E3 GBM/small-NN training on candidate features | ✅ engine exists |
| Alternative data | FRED vintages, BLS, CFTC COT, FXStreet, news as-of | ✅ sources named (FX plan §3) |
| Microstructure | OFI, microprice, book imbalance, spread regime | ✅ spec'd (FX plan §3) |
| Fundamentals/technicals/indicators | standard E1 feature library (RSI, ATR, MA, momentum, carry, …) | ✅ standard |
| Candlestick patterns | pattern detectors as **binary features** (body/range, engulfing flags, trend structure) | features ✅ — evidence mixed, never hard signals |
| **Outputs** | candidates + evidence pack (trials_run, IC, DSR, crisis replay, net-of-cost) | gate → E4 |

---

## §7 · How ONLINE agents use what offline proved — the handoff

The offline agent does not "teach" the online agent. It produces **frozen artifacts**; the online agent executes them without thinking:

```
OFFLINE RESEARCH CELL                  E4 ARENA (external gate)        ONLINE TRADING CELL
search 10,000s of candidates ──────▶ CPCV + DSR≥1 + crisis ──────▶ load frozen artifact
log EVERY trial (DSR)                replay · shadow 3–6 mo            compute each tick
                                     (cell CANNOT edit this)           NEVER searches
```

| What research proves | Artifact | How the online agent uses it |
|---|---|---|
| a formula works | **expression + params** in R3 registry | E1 evaluates every bar/tick |
| an ML model works | **ONNX file** in R3 | E3 deterministic inference |
| a threshold works | **JSON config** (e.g., "no entry when spread > 1.5 pip") | E1/E5 enforce each cycle |
| a feature set works | **feature spec** (columns + as-of rules) | E1 builds exactly those |
| a style works for the symbol | **spec-sheet update** (one style) | trading cell runs that style |
| edge decaying | **decay monitor** (rolling IC watch) | E7 → E4 demotes to research |

**Core rule:** *online = execute frozen artifacts only. Research = create candidates only. Neither crosses without E4.*

---

## §8 · ONE cell, offline + online, one symbol — ONE CELL · TWO MODES · EXTERNAL GATE

Session 22 rejected *one agent trading all styles simultaneously*. This design is different: **sequential modes on one symbol**, gate outside the cell.

| | MODE R (research, offline) | MODE T (trading, online) |
|---|---|---|
| Runs | batch, hours/days | continuous, µs–ms |
| May fail | **yes** — failure is data | **no** — governed |
| Searches? | ✅ all 6 ingredient groups | ❌ registry reads only |
| Places orders? | ❌ **no E6 access** | ✅ promoted artifacts only |
| Edits itself? | candidates only | **never** |
| LLM allowed? | optional (R3 permits research) | **never** (R2) |
| Output | candidates + trial log | P&L + ledger |

**State machine:**

```
MODE R ──candidates──▶ E4 GATE ──promote──▶ R3 REGISTRY ──▶ MODE T
  ▲                                                          │
  └────────── E7 + decay monitor (rolling IC) ──demote───────┘
```

**Guard rules:**

| # | Rule | Defeats |
|---|---|---|
| G1 | **E4/E5 are separate components the cell cannot write** | self-grading |
| G2 | **Mode T has no search code path** (not loaded) | hot-path contamination |
| G3 | **Mode R has no order code path** | research placing trades |
| G4 | **Every trial logged → DSR against total search count** | overfitting |
| G5 | **One style active in Mode T at a time** (Session 22 preserved) | negative transfer |
| G6 | **Mode R and Mode T run as separate processes** sharing the registry | research crash must not stop live trading |

**Performance (honest):** research quality = identical to separate agents (same search, 73 cells parallel); trading quality = identical **if G1–G5 hold**; main risk = gate integrity; no conflict with Session 22 (that evidence bans multi-style *simultaneous* trading weights, not research-then-trade sequence).

---

## §9 · Can the agent "see" candlesticks like us?

| Question | Answer |
|---|---|
| Eyes? | **No** — it receives OHLCV as numbers |
| Worse than seeing? | **No — better.** The chart is a visual compression of those numbers; the agent gets the raw thing humans interpret |
| Chart images (vision model)? | possible, no proven edge over numeric, slower, harder to audit → **not recommended** |
| "Reading candles" for it | pattern features: body/range ratio, higher-high structure, engulfing flags, regime |
| **Kronos?** | **tokenizes OHLCV into discrete tokens and reads them like a sentence** — machine-form "seeing candles" from numbers, not images |

**Candlestick-pattern evidence (mixed — features, not signals):**

| Study | Market | Result |
|---|---|---|
| Marshall & Young 1998 | S&P 500 | ✅ 36σ, ~1%/2-day |
| Chen/Bao/Zhou 2016 | China | ✅ 5/8 patterns significant (mid/large caps) |
| Lu 2012 | Taiwan electronics | ✅ 3 bullish patterns profitable |
| Tharavanij 2017 | Thailand | ❌ most patterns not ≠ 0 |
| Brazil 2013 | Ibovespa | ❌ few confirmed; one significant *opposite* |
| Vietnam 2018 | Vietnam | ❌ none of 10 significant |
| Engulfing 2021 | S&P 500 | ⚠️ some outperformance |

**Verdict: legitimate features, unreliable standalone signals — market-dependent. Feature bank yes; hard-code "engulfing = buy" no.**

---

## §10 · Kronos (shiyu-coder/Kronos) — verified facts vs independent evidence

### What it is (verified)

| Fact | Value |
|---|---|
| Repo | `shiyu-coder/Kronos` — **29,561 stars**, **MIT** |
| What | decoder-only foundation model for **K-line (candlestick) sequences** |
| Training | **12 billion K-line records, 45 global exchanges** |
| Mechanism | tokenizer: OHLCV → hierarchical discrete tokens → autoregressive prediction |
| Paper | arXiv 2508.02739 · **accepted AAAI 2026** |
| Claim | **RankIC +93%** vs leading TSFM, +87% vs best non-pretrained; vol MAE −9%; generative fidelity +22% |
| Sizes | Kronos-**mini = 4M params** (demo: 360-bar context, 30 MC paths → probability distribution of future candles) |
| Extras | fine-tuning scripts (2025-08) |

### Independent evaluation (the part the abstract doesn't show)

| Independent study | Protocol | Result |
|---|---|---|
| `kronos-market-eval` (2026-08-04) | walk-forward, BTC + SPY, vs random walk | ❌ **"Kronos does not beat 'the price stays where it is'"** |
| *Finance Research Letters* 2026 | **4,998 A-shares, lagged-only OOS, 2023–2025** | ❌ zero-shot MAE **2.4–2.6× worse than HAR-RV**, **last place every year**; fine-tuning **collapses encoder**; vs GARCH hybrid d=0.06 (p=0.42) = no difference |
| Diagnosis | — | BSQ quantization + price-derived volatility explain failure |

### Verdict

| Question | Answer |
|---|---|
| Add it? | ✅ **as a Mode-R CANDIDATE, not an oracle** |
| Role | **feature generator**: forecast distribution → features (`P(up)`, expected range, forecast vol, 30-path spread) into E1/E3 |
| LLM ban? | **Not an LLM** — 4M-param TSFM = E3-class model. Allowed offline; online only as frozen artifact |
| Gates | CPCV + DSR + shadow + decay monitor — **default expectation = fails** (two independent negative results) |
| Fine-tune on 73 symbols? | MIT allows; encoder-collapse evidence weak → zero-shot baseline first |
| Hot path (scalping)? | ⚠️ latency unverified — autoregressive + 30 MC paths likely too slow for ms-class; **fine for H1/D1 swing cells** |
| One-liner | *Promising candle-language reader; published wins not yet reproduced by outsiders — feature, not forecast god.* |

---

## §11 · Bottom line

| Question | Answer |
|---|---|
| What is quant trading? | Statistical edge → repeatable rule → computer executes without emotion |
| How does the math work? | **α = return unexplained by beta · IR = IC × TC × √BR · DSR = honesty · decay ≈ 50%+** |
| How is alpha found? | hypothesis · **GP formula search (non-LLM, proven)** · ML · alt data · microstructure |
| What's needed? | PIT data + compute + DSR discipline + breadth + net-cost labels + speed |
| One non-LLM research agent per symbol? | ✅ build it — formalized Invention Lane |
| Same agent also trading everything? | ❌ self-grading, negative transfer, hot-path rules → **one cell, two modes, external gate (G1–G6)** |
| How do online cells use proven work? | **frozen artifacts** via R3 — execute, never search |
| Can it see candles? | better than us — raw OHLCV; patterns = features (evidence mixed) |
| Add Kronos? | ✅ Mode-R candidate feature generator — but independent evals say it doesn't beat naive baselines → must clear our gates assuming failure |

---

## §12 · Provenance

| Claim | Class |
|---|---|
| IR = IC × TC × √BR; IC norms 0.02–0.07 | **Web-verified** — Grinold-Kahn, CFI, QuanterLab, riskhub |
| DSR / backtest overfitting | **Web-verified** — Bailey & López de Prado 2014, AMS 2014 |
| Alpha decay 50% / 93% / +5ppt-per-year / 7.58% significant | **Web-verified** — McLean-Pontiff 2016, Cambridge JFQ 2023, Quant Finance 2022, BFI Chicago 2024 |
| GP/symbolic alpha mining works | **Web-verified** — AlphaForge AAAI 2025, RiskMiner, arXiv 2412.00896 |
| Kronos facts (MIT, 12B records, AAAI 2026, +93% RankIC) | **Web-verified** — GitHub + arXiv 2508.02739 |
| Kronos independent failures | **Web-verified** — kronos-market-eval 2026-08-04, Finance Research Letters 2026 |
| Candlestick pattern results | **Web-verified** — 7 studies in §9, mixed |
| One-cell-two-mode design | **Design proposal** — guard rules G1–G6 are requirements, not measurements |
| Research-cell performance | **Unproven until built** — identical-to-split is conditional on gate integrity |

*Artifact of Session 26. Companion to FX-SCALPING-BUILD-PLAN.md (Session 25) and the 73-cell first batch (Session 24).*
