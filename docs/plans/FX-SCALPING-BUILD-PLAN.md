# FX SCALPING BUILD PLAN — integrations, training data, profitability

**Session 25 · 2026-10-27 · status: owner-requested deep dive (supersedes the wide-range 30–150 pip plan for FX)**
**Scope:** the 7 FX pairs on the IC Markets trunk (EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF, USD/SGD) traded as **non-LLM scalping cells**. LLMs never in the hot path (R2/R3). Every number below is either *measured*, *web-verified with source*, or marked *unverified*.

---

## §1 · The cost math — what a scalper actually pays

Measured on the IC Markets MT Raw account (26-Oct): **spread 0.32 pip avg + commission $7/lot round-turn = 0.70 pip**.

| Path | Cost per round trip | Formula |
|---|---|---|
| **Taker** (market order both ways) | **1.02 pips** | 0.32 spread + 0.70 commission |
| **Maker** (limit both ways) | **0.70 pips** | commission only — you *earn* the spread if both fill |
| *Alt:* cTrader $3/$100k/side | 0.92 pips taker | 0.32 + 0.60 |

**Breakeven win rate** = `50% + friction ÷ (2 × target)`:

| Target | Taker (1.02) | Maker (0.70) | Verdict |
|---|---|---|---|
| 1 pip | **101%** | 85% | ❌ mathematically dead on taker |
| 2 pips | 75.5% | 67.5% | ❌ |
| 3 pips | **67.0%** | 61.7% | ❌ brutal |
| 5 pips | **60.2%** | 57.0% | ⚠️ hard |
| 10 pips | 55.1% | 53.5% | ⚠️ reachable |
| 20 pips | 52.6% | 51.8% | ✅ |

**Two conclusions from the table alone:**

1. **Maker on entry is mandatory.** Taker scalping under 5 pips is a losing game before skill enters. Standing rule for E6: *enter on limit orders (earn the spread); market orders are for exits only.*
2. **Real scalping band = 3–10 pips**, requiring **53–67% win rate**. Not 1–2 pips.

---

## §2 · Latency budget — measured tiers

| Tier | Latency | Slippage cost | Verdict |
|---|---|---|---|
| Ideal | **< 5 ms** | ~0.01 pip | NY4 VPS → IC = **< 1 ms** ✅ achievable |
| Excellent | < 30 ms | 0.01–0.1 pip | ✅ |
| Hard ceiling | **< 100 ms** | grows fast | ⚠️ at 100 ms a 5-pip scalp loses ~0.2 pip/trade ≈ 4% of profit, compounding over hundreds of trades/month |
| Home MT4 | 500–700 ms | — | ❌ enters *after* the move |
| News (NFP/CPI/FOMC) | **sub-10 ms required** | pips/second | ⚠️ only after NY4 is proven |

*Source: vpsforextrader.com "Scalping Execution Latency 2026: Every Component Measured" (2026-04-05).*

**Implication:** the **NY4 VPS is not optional for scalping — it is build step 1.** Without it, do not build this plan.

---

## §3 · Feature set — what the scalpers train on

### Group D+ · microstructure (the core, from the cTrader feed)

| Feature | Source | Why |
|---|---|---|
| **Microprice** (size-weighted bid/ask mid) | Level II | leads the mid — best short-horizon signal class |
| **Order flow imbalance (OFI)** | Level II | Cont-Kukanov-Stoikov (2014): explains a large share of next-tick price change |
| **Book imbalance** | Level II | EFMA 2025: predictive up to **30 s lag**; midpoint at 100 ms / 1 s / 2 s horizons |
| **Spread regime state** ⭐ | ticks | #1 cost feature — spread widens at news and session opens *exactly when scalpers trade* |
| **Tick count (hourly)** = volume proxy | ticks | ~90% corr with CME futures volume at hourly aggregation — **labelled proxy, never "real volume"** (R8: FX has no live volume tape) |
| Session clock (Sydney→Tokyo→London→NY) | computed | liquidity by hour |
| Realized vol / ATR regime | computed | skip when friction > edge |
| **IC liquidity-pool depth** | Level II | ⚠️ verify volume-bearing vs synthetic at onboarding — label "broker book" |

### Group A/B/C · macro filters (slower clock, from free PIT-safe sources)

| Feature | Source | Use in scalping |
|---|---|---|
| Calendar + surprise index | FXStreet API / FRED | **trade filter only** — no entries in the ±5 min window (spread blows out) |
| Rate differentials, real yields | FRED (**vintages** = lookahead killer) | session bias direction |
| COT net positioning | CFTC API (works without token, free) | daily regime — which side not to fade |

### ⚠️ Honest academic caveats

| Evidence | What it says |
|---|---|
| Kolm 2023, *Mathematical Finance* | models trained on **order flow** significantly outperform models trained directly on order books |
| NSE OIB study (ScienceDirect 2021) | order-imbalance predictability strong in the **first 5 minutes, dead by 30 minutes** |
| Imbalance effect is **stronger for larger ticks** | **FX majors have the smallest ticks of any asset class → the weakest raw imbalance signal** |

**Translation:** the edge on FX majors will be *smaller* than the same strategy on NSE stocks or futures. Expectation stays modest — **spread-regime + session clock + maker spread capture carry more weight than raw book imbalance.**

---

## §4 · Data integration — cTrader Open API (open decision O2 = RESOLVED here)

| Need | cTrader message | Notes |
|---|---|---|
| Live bid/ask ticks | `ProtoOASubscribeSpotsReq` → `ProtoOASpotEvent` | price = raw ÷ 100000, round to `digits` |
| **Level II depth** | `ProtoOASubscribeDepthQuotesReq` → `ProtoOADeathEvent`/`ProtoOADepthEvent` | fires on every book change — OFI/microprice source |
| **Historical ticks** | `ProtoOAGetTickDataReq` (BID/ASK type) | backtest fuel |
| Symbol master | `digits, pipPosition, minVolume, maxVolume, stepVolume, lotSize, swapLong, swapShort` | swap pulled from API, never hardcoded |
| Connection | TCP **or** WebSocket, Protobuf/JSON, `live.ctraderapi.com:5035`, AWS Global Accelerator | Linux-native ✅ |
| SDK | `pip install ctrader-open-api` (Python/Twisted), Go, TypeScript | |
| **Rate limits** | **5 req/s historical · 50 req/s live** | tick-history pull = days, not hours |
| FIX 4.4 | no minimum deposit or volume | |

*Why cTrader and not MT5: IC Markets — "We do not offer a FIX API connection into MetaTrader 4 or 5. You can, however, connect to our cTrader platform via FIX API." MT5 automation is Windows-only → risk R1 avoided.*

---

## §5 · Backtest engine — tick-level or it's worthless

Candle backtests **cannot** test scalping. Required additions to E4:

| Requirement | Why |
|---|---|
| Tick-by-tick replay | intrabar sequencing decides 3-pip wins |
| **Queue position modeling** | maker fills: you join the back of the queue — fill probability ≠ 1 |
| Partial fills | your size vs displayed depth |
| **Spread as a time series** | 0.32 avg hides 2–5 pip news spikes |
| Latency injection | run the sim at 1 / 10 / 100 ms — watch the strategy die at 100 |
| Slippage from **own E7 fills** | live-measured, not assumed |
| CPCV + DSR gates | existing E4 standard — no random splits on autocorrelated ticks |

---

## §6 · Training pipeline

| Step | What | Gate |
|---|---|---|
| 1 | Pull tick + Level II history (5 req/s — **start first**, it is the long pole) | coverage log per pair |
| 2 | Assemble PIT panel: microstructure + spread regime + session clock + macro filters | 0 lookahead rows |
| 3 | **Label** = forward 3–10 pip move, **net of 1.02 taker / 0.70 maker + measured slippage** | labels net, never gross |
| 4 | Train E3 (GBM / small NN → ONNX; SB3 baseline) | seed-invariance: same seed ⇒ same weight hash, twice |
| 5 | CPCV + DSR + crisis replays (news-spike kits) | DSR ≥ 1 |
| 6 | **Spread-regime audit** — performance *by spread bucket* | positive in tight bucket only; wide bucket flat = not traded |
| 7 | Latency sweep: same model at 1 / 10 / 100 ms | edge survives at real venue latency |
| 8 | Shadow 3–6 months, zero orders | **net expectancy > 0 after real friction** |
| 9 | Live 1 pair (EUR/USD), maker-entry rule enforced in E6 | 5 sessions ledger-matched |
| 10 | Expand → 7 pairs | governor caps hold |

**Profitability bar (honest, stated once):**
> **Win rate > 55% (target 60%) at 3–10 pip targets · profit factor > 1.2 (target 1.5) · max drawdown < 20% (target 10%) · DSR ≥ 1 · order latency < 100 ms (target < 50 ms) · net expectancy > 0 after 1.02-pip friction + measured slippage — over ≥ 3 months out-of-sample shadow.** Anything short of that does not ship. Profit is never promised; only this bar is measurable.

---

## §7 · Build order

| # | Step | Owner input | Gate |
|---|---|---|---|
| 1 | **NY4 VPS** for the FX hub | buy (~$100–300/mo) | ping to IC < 1 ms |
| 2 | **cTrader account + API keys** (O2 resolved) | your login | read-only quote stream works |
| 3 | Start **historical tick + depth pull** immediately | none | 5 req/s rate limit = days of pull |
| 4 | Symbol master: resolve Brent symbol (`XBRUSD`/`XBRUSH`/`BRENT`), **confirm DOM volume-bearing vs synthetic** | none | exactly 1 symbol each; DOM labelled |
| 5 | Feature engine E1: microstructure group wired | none | offline/online bit-exact in CI |
| 6 | Tick backtest engine in E4 (queue / partial fills / spread series / latency injection) | none | matches cTrader fills on a sample window |
| 7 | Train first cell — EUR/USD scalp | none | CPCV + DSR + spread-bucket audit pass |
| 8 | Shadow ≥ 3 months | none | **net expectancy > 0 after friction** |
| 9 | Live EUR/USD only, maker-entry rule | sign-off | 5 sessions ledger-matched |
| 10 | Expand → 7 FX pairs | sign-off per tranche | E5 caps hold |

---

## §8 · Risk register

| ID | Risk | Sev | Mitigation |
|---|---|---|---|
| **S1** | 1.02-pip taker friction is dead under 5-pip targets | **High** | **maker-entry rule enforced in E6** (0.70 path) |
| **S2** | FX majors = smallest ticks → weakest imbalance signal | **High** | weight spread-regime + session clock; modest expectations |
| **S3** | Spread widens at news/session opens exactly when scalping | **High** | spread-regime = hard gate, no entry in widen state |
| **S4** | Latency > 100 ms kills the edge | **High** | NY4 mandatory (step 1); latency sweep in training |
| **S5** | Queue position — maker orders may not fill | **Med** | fill-probability model in backtest; capped chase rules |
| **S6** | Adverse selection — IC is the CFD issuer, sees your flow | **Med** | no one-way quoting, modest size, E7 slippage feedback loop |
| **S7** | Tick-history pull slow (5 req/s) | **Med** | start at build step 3 — long pole of the plan |
| **S8** | Lookahead from revised news prints | **Med** | FRED vintages + as-of stamps enforced in CI |
| **S9** | No real FX volume tape (R8) | **Med** | hourly tick-count proxy + optional CME cross-check, labelled proxy |

---

## §9 · Could not verify (needs the live account)

| Item | Status |
|---|---|
| IC DOM carries real volumes or is synthetic | onboarding step 4 |
| Brent symbol — `XBRUSD` / `XBRUSH` / `BRENT` | same |
| Full swap schedule per pair (XAUUSD long −$53.31/lot/night measured; EUR/USD unmeasured) | pull from cTrader symbol master |
| Historical depth availability via API (docs only show *live* depth subscribe) | test at step 3 — if no history, record live going forward |
| CME FX futures live volume | `cme.com` HTTP 403 IP-blocked → paid feed; optional cross-check only |

---

## §10 · Provenance — measured vs verified vs unverified

| Claim | Class |
|---|---|
| Spread 0.32 pip (EUR/USD, 26-Oct), commission $7/lot RT = 0.70 pip, round trip 1.02 pips | **Measured** |
| Breakeven win-rate table (§1) | **Derived** from measured friction |
| Latency tiers and slippage ranges (§2) | **Web-verified** — vpsforextrader.com 2026-04-05 |
| OFI / book-imbalance predictive evidence, Kolm 2023, tick-size caveat (§3) | **Web-verified** — Cont-Kukanov-Stoikov 2014, EFMA 2025, NSE OIB 2021, Kolm *Mathematical Finance* 2023 |
| cTrader messages, rate limits, endpoint, SDK (§4) | **Web-verified** — help.ctrader.com Open API docs + IC Markets support statement |
| Tick-count ≈ 90% corr with CME at hourly | **Web-verified** (Marney/Dxa) — correlation must be **re-measured on our 7 pairs** |
| Win-rate / PF / DD / latency targets (§6) | **Targets**, not results |
| Actual profitability | **Unverified — cannot be promised.** Only the measurable bar ships |

---

*Artifact of Session 25. O2 (connectivity) resolved here = cTrader Open API. Supersedes the wide-range 30–150 pip FX plan for the FX trunk; the wide-range styles (session/carry/news-fade) remain valid as future second styles per O5.*
