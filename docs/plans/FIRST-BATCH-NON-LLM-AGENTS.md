# First Batch — 73 Non-LLM Agent Cells

**Day-One Roster and Build Plan · Wave-1 Re-Baseline**

Owner-finalized 2026-10-27. Supersedes the `2–10 cells` day-one sizing in
`AGENT-PRODUCTION-BUILD-PLAN.md` §13.

---

## §0 · Purpose and scope

| Item | Value |
|---|---|
| Cells defined here | **73** (instrument-level) |
| Venues | **3** — Upstox (NSE/BSE), IC Markets (FX + commodity CFDs), Hyperliquid (on-chain perps) |
| LLM involvement in these cells | **ZERO** — every cell is deterministic or trained-ML, never an LLM |
| Symbol validation status | **Verified live** against NSE bhavcopy 26-Oct-2026 (3,526 symbols) and three official NSE index files |
| What this document is | The day-one cell roster + ordered build plan + gates |
| What it is not | Not the style roster (30 styles live in `AGENT-TAXONOMY-FAMILIES.md`), not the engine spec |

This document replaces the assumption "wave 1 = 2–10 cells". The owner re-baselined wave 1
to the full 73-cell first batch (decision D1 below). That decision is recorded, not implied.

---

## §1 · Owner decisions locked this session

| # | Decision | Locked value | Consequence |
|---|---|---|---|
| **D1** | Wave re-baseline | **73 cells IS the new W1** | Wave table in `AGENT-TAXONOMY-FAMILIES.md` §5 and gate C5 in the production report must be rewritten |
| **D2** | Commodities trunk | **Hosted on the FX trunk — flagged exception** | The cross-family ban (§3 of taxonomy) gets one named carve-out for Brent, WTI, XAU, XAG |
| **D3** | Index cells | **Trade via index F&O** — NIFTY through `NFO`, SENSEX through `BFO` | Pulls futures + options families into day one; needs expiry calendars and lot sizes |

D1 changes a documented rule. It does **not** change the underlying engineering constraint:
a 73-cell cold start has no precedent in these docs. §9 answers that with tranches inside W1
rather than by reviving the old 2–10 limit.

---

## §2 · Cell census — 73

| # | Trunk | Venue | Cells | Composition |
|---|---|---|---|---|
| 1 | **Equities** (cash) | Upstox `NSE_EQ` | **57** | 50 NIFTY 50 members + 7 off-index large caps |
| 2 | **Futures + Options** (index) | Upstox `NSE_FO` / `BSE_FO` | **2** | NIFTY (NFO), SENSEX (BFO) — underlying-level cells |
| 3 | **FX** | IC Markets ECN | **7** | EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF, USD/SGD |
| 4 | **FX trunk — commodity exception (D2)** | IC Markets ECN | **4** | Brent, WTI, XAU/USD, XAG/USD |
| 5 | **Crypto** (perps) | Hyperliquid L1 | **3** | BTC, ETH, HYPE |
| 6 | **Predictions** | *none* | **0** | No venue connected — see §11 open decision O1 |
| | **Total** | | **73** | |

Count check: `57 + 2 + 7 + 4 + 3 + 0 = 73` ✓

**Trunks touched on day one: 5 of 6.** Predictions is the only absent trunk, while
`AGENT-TAXONOMY-FAMILIES.md` §3 states predictions is an "own trunk from day one".
That contradiction is carried openly to §11 as open decision **O1**.

---

## §3 · Cell definition — instrument cell vs agent instance

The plan defines a cell as **one symbol × one style** (`AGENT-PRODUCTION-BUILD-PLAN.md` §13).
This document counts **instrument cells** (73). The two numbers are different and must not be
conflated:

| Quantity | Formula | Day-one value |
|---|---|---|
| Instrument cells | fixed roster | **73** |
| Agent instances | instruments × styles per instrument | **73 at 1 style each** |
| Agent instances if 2 styles each | 73 × 2 | 146 |
| First-decade target (report §13) | liquid-first roster | 3,000–6,000 |

**Day-one rule adopted here: 1 style per instrument.** Styles multiply later through the
normal wave gates. Any document claiming "73 agents" means 73 instances.

---

## §4 · Symbol master — 57 NSE equities (corrected)

Verified against: `ind_nifty50list.csv` (50 rows), `ind_nifty100list.csv` (100 rows),
`ind_nifty500list.csv`, and `sec_bhavdata_full_26102026.csv` (3,526 symbols).

| # | Symbol | NIFTY 50 | Index tier | Note |
|---|---|---|---|---|
| 1 | ADANIENT | ✓ | NIFTY 50 | |
| 2 | ADANIPORTS | ✓ | NIFTY 50 | |
| 3 | APOLLOHOSP | ✓ | NIFTY 50 | |
| 4 | ASIANPAINT | ✓ | NIFTY 50 | |
| 5 | AXISBANK | ✓ | NIFTY 50 | |
| 6 | **BAJAJ-AUTO** | ✓ | NIFTY 50 | **corrected** — was written `BAJAJAUTO` |
| 7 | BAJAJFINSV | ✓ | NIFTY 50 | |
| 8 | BAJFINANCE | ✓ | NIFTY 50 | |
| 9 | BEL | ✓ | NIFTY 50 | |
| 10 | BPCL | — | NIFTY Next 50 | off-index (kept by owner) |
| 11 | BHARTIARTL | ✓ | NIFTY 50 | |
| 12 | BRITANNIA | — | NIFTY Next 50 | off-index (kept by owner) |
| 13 | **BSE** | ✓ | NIFTY 50 | **corrected** — was written `BSELTD` |
| 14 | CIPLA | ✓ | NIFTY 50 | |
| 15 | COALINDIA | ✓ | NIFTY 50 | |
| 16 | DIVISLAB | — | NIFTY Next 50 | off-index (kept by owner) |
| 17 | DRREDDY | ✓ | NIFTY 50 | |
| 18 | EICHERMOT | ✓ | NIFTY 50 | |
| 19 | ETERNAL | ✓ | NIFTY 50 | ex-Zomato |
| 20 | GRASIM | ✓ | NIFTY 50 | |
| 21 | HCLTECH | ✓ | NIFTY 50 | |
| 22 | HDFCBANK | ✓ | NIFTY 50 | |
| 23 | HDFCLIFE | ✓ | NIFTY 50 | |
| 24 | HEROMOTOCO | — | NIFTY 500 only | off-index (kept by owner) |
| 25 | HINDALCO | ✓ | NIFTY 50 | |
| 26 | HINDUNILVR | ✓ | NIFTY 50 | |
| 27 | ICICIBANK | ✓ | NIFTY 50 | |
| 28 | INDIGO | ✓ | NIFTY 50 | |
| 29 | INDUSINDBK | — | NIFTY 500 only | off-index (kept by owner) |
| 30 | INFY | ✓ | NIFTY 50 | |
| 31 | ITC | ✓ | NIFTY 50 | |
| 32 | JIOFIN | ✓ | NIFTY 50 | |
| 33 | JSWSTEEL | ✓ | NIFTY 50 | |
| 34 | KOTAKBANK | ✓ | NIFTY 50 | |
| 35 | LT | ✓ | NIFTY 50 | |
| 36 | M&M | ✓ | NIFTY 50 | `&` needs URL/JSON escaping |
| 37 | MARUTI | ✓ | NIFTY 50 | |
| 38 | MAXHEALTH | ✓ | NIFTY 50 | |
| 39 | NESTLEIND | ✓ | NIFTY 50 | |
| 40 | NTPC | ✓ | NIFTY 50 | |
| 41 | ONGC | ✓ | NIFTY 50 | |
| 42 | POWERGRID | ✓ | NIFTY 50 | |
| 43 | RELIANCE | ✓ | NIFTY 50 | |
| 44 | SBILIFE | ✓ | NIFTY 50 | |
| 45 | SBIN | ✓ | NIFTY 50 | |
| 46 | SHRIRAMFIN | ✓ | NIFTY 50 | |
| 47 | SUNPHARMA | ✓ | NIFTY 50 | |
| 48 | TATACONSUM | ✓ | NIFTY 50 | |
| 49 | TATASTEEL | ✓ | NIFTY 50 | |
| 50 | TCS | ✓ | NIFTY 50 | |
| 51 | TECHM | ✓ | NIFTY 50 | |
| 52 | TITAN | ✓ | NIFTY 50 | |
| 53 | **TMPV** | ✓ | NIFTY 50 | **corrected** — was written `TATAMOTORS` (PV demerger) |
| 54 | TRENT | ✓ | NIFTY 50 | |
| 55 | ULTRACEMCO | ✓ | NIFTY 50 | |
| 56 | UPL | — | NIFTY 500 only | off-index (kept by owner) |
| 57 | WIPRO | — | NIFTY Next 50 | off-index (kept by owner) |

**Coverage result:** after the three corrections, the roster contains **100% of the live
NIFTY 50** (50 of 50). The 7 off-index names are valid, tradable, and deliberately retained —
so the roster must be labelled **"NIFTY 50 + 7 large caps"**, never "NIFTY 50".

---

## §5 · The three symbol corrections

Each would have produced a rejected order or a wrong-instrument fill.

| Written by owner | Live NSE symbol | Proof — 26-Oct-2026 bhavcopy | Cause |
|---|---|---|---|
| `BAJAJAUTO` | **`BAJAJ-AUTO`** | `BAJAJAUTO` = 0 hits · `BAJAJ-AUTO` = 1 hit | missing hyphen |
| `BSELTD` | **`BSE`** | `BSELTD` = 0 hits · `BSE` = 1 hit | vendor suffix |
| `TATAMOTORS` | **`TMPV`** | `TATAMOTORS` = 0 hits · `TMPV` = 1 hit | Tata Motors PV demerger |

`M&M` and `ETERNAL` were checked separately and are **correct as written** (1 hit each).

---

## §6 · Venue matrix

| | **Upstox** | **IC Markets** | **Hyperliquid** |
|---|---|---|---|
| **Segments** | `NSE_EQ`, `NSE_INDEX`, `NSE_FO`, `BSE_EQ`, `BSE_INDEX`, `BSE_FO`, `CDS`, `BCD_FO`, `MCX_FO`, `NSE_COM`, `NCD_FO` | MT4 / MT5 / cTrader / TradingView | on-chain L1, perps only |
| **Cells used** | 59 | 11 | 3 |
| **Instrument key format** | `NSE_EQ\|<ISIN>`, `NSE_INDEX\|Nifty 50`, `BSE_INDEX\|SENSEX` | broker symbol (`XAUUSD`, `XTIUSD`, …) | bare name (`BTC`, `ETH`, `HYPE`) |
| **Counterparty** | Regulated exchange (NSE / BSE) | **CFD issuer — own book** | On-chain, no broker |
| **Cost** | brokerage + STT + exchange charges + stamp | Raw Spread: **$7/lot round-turn** (MT) or **$3 per $100k/side** (cTrader) | venue fee + funding |
| **Measured spread** | n/a | EUR/USD raw **0.32 pip** avg 24h; XAUUSD **$0.082** | n/a |
| **Hours** | NSE 09:15–15:30 IST | ~24/5, but **gold 01:02–23:57 server time** | 24/7 |
| **Overnight cost** | NSE F&O carry | XAUUSD long **−$53.31/lot/night**; Brent **+15.74 / −26.24** | funding, floor **0.00125%/8h** |

**Counterparty asymmetry is material.** IC Markets states it *"is the issuer of the products it
provides"* and holds a securities-dealer licence permitting it to make a market. FX loss is
the broker's book. Upstox risk clears through an exchange; Hyperliquid clears on-chain.
The governor (E5) must therefore carry a **venue risk class**, not a flat exposure number.

**Symbol ambiguity to resolve before any oil order:** IC's commodity spec sheet lists Brent as
`XBRUSH` and WTI as `XTIUSD`; their commodities page lists `XBRUSD`; a third-party live feed
lists `BRENT` / `WTI`. IC's own disclaimer says the authoritative list is what appears *in the
account*. Resolve from the live symbol master at onboarding — never hardcode.

---

## §7 · The non-LLM guarantee

This batch is the "non-LLM agents" batch. The constraint is structural, not stylistic:

| Layer | Allowed | Forbidden |
|---|---|---|
| Cell decision (hot path) | deterministic rules, trained ML exported to ONNX | **any LLM call** |
| Risk / governor (E5) | deterministic arithmetic only | **any LLM call** |
| Execution (E6) | deterministic order logic | **any LLM call** |
| Research / cockpit | LLM assistants permitted | must never write into the hot path |

Rationale, measured rather than asserted: LLM decision cycles run 15–40 s at ~$0.12 per
decision with <5% signal value at a 20 s horizon (MethodAlgo 2026, cited in report §19).
Every cell above must decide in µs–ms. An LLM in that loop is not a slow agent, it is a
broken one.

---

## §8 · Build plan — ordered, gated

Steps are dependency-ordered. A step does not start until the one it depends on has passed
its gate.

| # | Step | Deliverable | Done-when |
|---|---|---|---|
| 1 | Fix symbol master | corrected 57-symbol roster | 3 renames applied; validation gate passes |
| 2 | Resolve commodity exception (D2) | taxonomy carve-out clause | Brent/WTI/XAU/XAG named as FX-trunk tenants |
| 3 | Resolve index F&O typing (D3) | NIFTY→NFO, SENSEX→BFO cell spec | lot size, expiry calendar, contract-selection rule written |
| 4 | Write `VENUE-CONNECTIVITY.md` | 3-venue matrix (closes remainder-map item #6) | all cells mapped to a segment + instrument key |
| 5 | Symbol-master service | loader + reject-unknown gate | 0 unmatched symbols against live Upstox master |
| 6 | **IC Markets connectivity decision** | cTrader Open API **or** MT5 bridge | one path chosen and proven with a read-only quote |
| 7 | Hyperliquid adapter | WS feed + `meta` universe sync | `BTC`,`ETH`,`HYPE` resolve; `BTC/USDC` rejected |
| 8 | Upstox adapter | REST + WS, equity + F&O | quote + depth for 1 equity and 1 index option |
| 9 | IC adapter | quote + depth for 1 FX pair + 1 commodity | `XBRUSD`/`XBRUSH`/`BRENT` resolved to exactly one |
| 10 | Data plane wiring | NATS bus + hot store ingestion | 3 venues streaming, clock skew recorded |
| 11 | Cell bootstrap | 73 cells instantiated, 1 style each | governance skeleton + risk caps applied |
| 12 | Reconciliation + governor | nightly broker-statement recon, venue risk classes | mismatch ⇒ trading pauses (per `AGENT-17-TOPICS-DESIGN.md` §C1) |

Steps 6–9 are read-only. **No order is placed until step 12 passes.**

---

## §9 · Tranches inside W1

The owner re-baselined the wave; the physics of a cold start remain. 73 cells sharing one
governor, three venues, three counterparty classes and three clocks is not a single
deployable unit. W1 therefore ships in three tranches — this is execution sequencing inside
W1, not a return to the old 2–10 limit.

| Tranche | Contents | Cells | Gate to pass before the next tranche |
|---|---|---|---|
| **T1** | Venue connectivity (steps 4–10) + 10 equity cells, 1 style | 10 | 5 consecutive sessions, ledger matches broker, zero stale feeds |
| **T2** | Remaining 47 equity cells + 2 index F&O cells | 49 | governor exposure caps hold under all 3 counterparty classes |
| **T3** | 7 FX + 4 commodity + 3 crypto cells | 14 | swap/funding costs modelled in the backtest and matched live |

Cumulative: `10 → 59 → 73`. Each gate is measured against the ledger, not against intent.

---

## §10 · Risk register

| ID | Risk | Severity | Evidence | Mitigation |
|---|---|---|---|---|
| **R1** | MT5 automation is Windows-only; the FX adapter may be unbuildable as assumed | **High** | known limitation | Prefer **cTrader Open API** (WebSocket/FIX); decide at step 6 |
| **R2** | Counterparty asymmetry — IC is a CFD issuer, not an exchange | **High** | IC help-centre wording | Venue risk class in E5; per-class exposure caps |
| **R3** | 73-cell cold start exceeds any documented precedent | **High** | report §13 | T1→T2→T3 tranche gates (§9) |
| **R4** | Three broken symbols → rejected or misrouted orders | **High** | bhavcopy 0-hit proof | Validation gate at step 5 |
| **R5** | Brent symbol ambiguous across three sources | **Med** | `XBRUSH` / `XBRUSD` / `BRENT` | Resolve from live account master (step 9) |
| **R6** | IC hours ≠ 24/5 and swaps are large | **Med** | XAUUSD −$53.31/lot/night | Swap schedule inside the style cost model; no overnight carry for short-horizon styles |
| **R7** | Hyperliquid funding pinned at floor — carry ≈ 0 today | **Med** | live: BTC 0.00125%/8h, ETH 0.0000517%/8h | Measure regime before sizing the funding cell |
| **R8** | FX has no live volume tape | **Med** | BIS survey only | Name the tick-activity proxy in the E1 feature spec, or buy a feed |
| **R9** | Market-making archetypes cannot run on these venues | **Med** | external hop 0.5–50 ms (`DATA-LAYER` §61–64) | MM styles stay deferred |
| **R10** | Jurisdiction: IC and Hyperliquid are not US-available; Upstox is India-resident | **Med** | platform policy | Document as single-owner assumption |
| **R11** | NIFTY 50 reconstitutes semi-annually — roster silently drifts | **Low** | today's 7-name diff | Weekly membership refresh + explicit allowlist |
| **R12** | Upstox market-data entitlement / rate limits unverified | **Low** | not tested | Test at step 8 |
| **R13** | `M&M` breaks URL/JSON handling | **Low** | symbol confirmed live | Escape at the adapter boundary; add a test fixture |

---

## §11 · Open decisions

| ID | Question | Why it blocks |
|---|---|---|
| **O1** | Predictions trunk has **0 cells**, yet taxonomy §3 says "own trunk from day one". Add Polymarket/Kalshi cells, or formally defer? | Only remaining wave-rule contradiction after D1 |
| **O2** | cTrader Open API vs MT5+Wine for IC Markets | Decides the whole FX adapter shape (step 6) |
| **O3** | Index F&O contract selection — one underlying cell per index, or per-expiry/per-strike cells? | A "NIFTY cell" could be thousands of instruments; measured 40,212 F&O contracts in one session |
| **O4** | Keep the 7 off-index names or replace with true NIFTY 50 members? | Roster label and index-drift monitoring depend on it |
| **O5** | First style activated per instrument (currently assumed: 1 style each) | Determines whether day one is 73 or 146 instances |

---

## §12 · Verification checklist

| Gate | Check | Pass criteria |
|---|---|---|
| V1 | Symbol master join vs live Upstox master | 0 unmatched; `M&M` resolves |
| V2 | Index membership refresh (weekly) | roster ⊇ NIFTY 50, else gaps are in the allowlist |
| V3 | Hyperliquid universe sync | `BTC`,`ETH`,`HYPE` present; `BTC/USDC` rejected |
| V4 | IC symbol resolution | Brent resolves to exactly one live symbol |
| V5 | Adapter heartbeats (3 venues) | all return; clock skew logged; 0 auth errors |
| V6 | Wave gate | 73 cells documented as W1 per D1, with tranche gates |
| V7 | Cost baseline | 1 round-turn per venue vs published: EUR/USD 0.32 pip, $7/lot RT |
| V8 | Reconciliation | nightly statement match; mismatch pauses trading |
| V9 | Non-LLM audit | 0 LLM calls reachable from cell/governor/execution code paths |

---

## §13 · Provenance — what was measured, not assumed

All figures in this document were pulled live on 2026-10-26/27 unless marked otherwise.

| Claim | Source |
|---|---|
| 57 symbols resolve on NSE | `archives.nseindia.com/products/content/sec_bhavdata_full_26102026.csv` (3,526 symbols) |
| 50 live NIFTY 50 members | `archives.nseindia.com/content/indices/ind_nifty50list.csv` (50 rows) |
| 4 names sit in Next 50 | `ind_nifty100list.csv` (100 rows) minus NIFTY 50 |
| 3 names sit in NIFTY 500 only | `ind_nifty500list.csv` |
| Upstox segment codes | Upstox Developer API instrument documentation |
| `NSE_INDEX\|Nifty 50`, `BSE_INDEX\|SENSEX` are quote instruments | Upstox instrument master |
| IC Raw Spread cost | IC Markets trading-costs pages |
| IC spreads (EUR/USD 0.32 pip; XAUUSD $0.082) | live IC spread feed, 26-Oct-2026 |
| IC swap rates | same live feed, 26-Oct-2026 |
| Hyperliquid perp names + funding | `POST api.hyperliquid.xyz/info {"type":"meta"}` / `metaAndAssetCtxs` |
| NIFTY lot size 65 | F&O bhavcopy field `NewBrdLotQty` |
| 40,212 F&O contracts in one session | F&O bhavcopy 23-Oct-2026 |
| LLM decision economics | report §19, MethodAlgo 2026 |

**Not verified:** SENSEX lot size and BSE expiry calendar (blocked: `bseindia.com` returns
HTTP 403 to automated requests; `api.bseindia.com` returns *Access Denied*). Must be read
from the Upstox instrument master at step 3.

---

*Companion to `AGENT-PRODUCTION-BUILD-PLAN.md` (engines, orchestration, population) and
`AGENT-TAXONOMY-FAMILIES.md` (6 trunks, 30 styles, waves). Session 24 in
`planing conva/PLANNING-CONVERSATION-LOG.md` records the decisions D1–D3.*
