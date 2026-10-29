# OPTIONS-BUYING SCOPE (D4) + PER-SYMBOL MULTI-STYLE CROSSCHECK — real-evidence review

**Session 27 · 2026-10-27 · owner scope change + evidence crosscheck (web-researched)**
**Owner decision:** no cash equities, no futures on stocks — **options only, BUYING only**,
on NIFTY, SENSEX and the stock roster. Each symbol runs the compatible styles via its own
cells; online mode picks what works now and exits immediately on adverse news.
**Covers:** D4 roster re-baseline · 4-of-8 style physics · per-symbol architecture check ·
NSE/SEBI rulebook constraints · **the "does it work / how profitable" evidence search** ·
regime router · exit ladder 1–6 · cell-count math. Every claim classified:
measured / web-verified / unverified.

---

## §1 · What the owner decided (D4) and what it changes

| Owner said | Effect |
|---|---|
| "not using equities & futures on stocks" | **D1 re-opened** — 57 NSE cash-equity cells and stock-futures cells drop |
| "only options, buying only" | stock cells become **options-buying cells** on optionable names |
| NIFTY + SENSEX stay | index options (NFO / BFO) — compatible with buying (Session 23, D3) |
| "each symbol has its own agent with all these styles" | per-symbol × **4 compatible styles** = specialist cells (Session 22 preserved) |
| "online figures out which works now and does those" | deterministic **regime router** (§6) |
| "mid-trade news → immediately close" | **exit ladder 1–6** (§7) |
| FX / commodities / crypto | unchanged — 14 cells (7 FX + 4 commodity + 3 crypto) |

---

## §2 · Buy-only vs the 8 options styles — only 4 fit (option physics)

**The core physics: buying options PAYS theta every day. You cannot harvest what you are paying.**

| # | Style | Fits buy-only? | Why |
|---|---|---|---|
| 1 | **delta-direction** (long calls/puts) | ✅ **YES** | pure directional buying |
| 2 | **vol-alpha (vega)** | ✅ **YES** | buy cheap vol (long vega) — buy-only version works |
| 3 | **gamma-scalper** | ✅ **YES** | buy straddle → scalp delta — long gamma is buying |
| 4 | **hedge-executor** | ✅ **YES** | buying protective puts = exactly this |
| 5 | theta-harvest | ❌ NO | it means **selling** premium — a long book *pays* theta |
| 6 | basis/calendar | ❌ NO | a calendar = long far + **short near** — contains a short leg |
| 7 | expiry/pin | ❌ NO | pin-harvest = selling into expiry (theta capture) |
| 8 | options-MM | ❌ NO | quoting = selling + already deferred (Session 25: MM impossible on our venues) |

**Options roster after D4: 4 of 8 — delta-direction · vol-alpha · gamma-scalper · hedge-executor.**
The other 4 are excluded by the owner's own buy-only rule (option physics, not architecture).

**Honest economics of buy-only:** every day held, theta bleeds the premium. Long-only wins need
direction or vol move > decay before expiry. Loss is capped at premium paid — the governor's
worst case is **known at entry** — but the win-rate bar is higher than selling. Capped-loss /
positive-omega is a legitimate choice; it is what fits the risk rule.

---

## §3 · Per-symbol architecture crosscheck — "one agent per symbol with all these styles"

**Correction first (Session 22 evidence):** not one agent holding all styles simultaneously —
**one specialist cell per (symbol × style)**, because ScaleZero-style negative interference
kills multi-style simultaneous weights. What the owner describes = 4 cells per symbol, with
the regime router enabling/disabling them.

```
SYMBOL (e.g. NIFTY)
 ├─ cell NIFTY-delta-direction   ─┐
 ├─ cell NIFTY-vol-alpha          ├─ all offline-researched (Mode R, 5 methods)
 ├─ cell NIFTY-gamma-scalper      │   promoted through E4 → frozen artifacts (Mode T)
 └─ cell NIFTY-hedge-executor    ─┘
        ▲
        └── REGIME ROUTER (deterministic rules, default = all off) enables 0 or 1 at a time
```

| Question | Verdict | Evidence class |
|---|---|---|
| One specialist cell per symbol × style? | ✅ yes — Session 22 evidence (negative interference) | measured (internal) |
| All 4 styles researched offline per symbol? | ✅ yes — Mode R cell, 5 methods, frozen artifacts | design (Session 26) |
| Online picks the working style? | ✅ deterministic regime router, default off | design (§6) |
| Does multi-style breadth help? | ✅ **web-verified** — CTA multi-strategy literature, §5.5 | web-verified |
| Will it be profitable? | ⚠️ **unknown until backtest + shadow** — see §8 | unproven |

---

## §4 · The instruments — what NSE/BSE actually allow (verified 2026-08)

| Fact | Source | Consequence for us |
|---|---|---|
| F&O on **6 indices + more than 200 securities**, only those meeting SEBI eligibility | NSE (updated 11-Aug-2026) | **not all 57 names have options** — screen at onboarding |
| **One weekly index expiry per exchange** (NIFTY only on NSE; SENSEX on BSE) | SEBI reform | fewer short-dated contracts; stock options = monthly |
| **Option premiums paid fully upfront** | SEBI | buy-only = the cheaper side (sellers post margin) |
| Lot sizes sized to **₹15–20 lakh contract value** (NIFTY = 65 from Jan 2026) | NSE FAOP70616 | position-sizing math changes; capital per trade is large |
| **Ban period**: MWPL > 95% → reduce-only, no new positions | SEBI May-2025 circular | **hard entry gate: check ban status before every entry** |
| Intraday position monitoring ≥ 4 snapshots/day | SEBI Apr-2025 | E5 checks limits intraday, not just EOD |

**Onboarding screen required:** intersect the 57 names with the option master + **liquidity
filter** (stock options in India are far thinner than index options — wide spreads, sometimes
no bid). Honest expectation: **tradeable set < 57**. Also new: **O3 gets sharper** — a NIFTY
cell is one underlying, but its tradable instruments are thousands of strike×expiry contracts;
the router picks the strike, not the cell.

---

## §5 · THE EVIDENCE CROSSCHECK — "does it work, how profitable, real web evidence"

### 5.1 The retail base rate (SEBI's own study — the world we are NOT joining)

| Finding | Source |
|---|---|
| **93% of 1.13 crore individual traders lost ₹1.81 lakh crore** in F&O over FY22–FY24 (avg ~₹2 lakh/person) | SEBI/PwC study, Sep-2024 |
| **91.1% lost in FY24 alone** (~73 lakh traders, avg net loss ₹1.20 lakh incl. costs) | same |
| **>80% of options losses** came from 17% "high-value" traders; **93.5% of them lost** | same |
| Only ~1% of traders earned >₹1 lakh | SEBI PR 22/2024 |

**Read correctly:** this is the base rate for *unsystematic* retail — discretionary entries,
no pre-defined exits, leverage, no cost accounting. It is the population our deterministic
governor, exit ladder, and E4 gates exist to avoid. It is **not** evidence that systematic
buy-only cannot work — and it is **not** evidence that it can.

### 5.2 Strategy-level evidence on NIFTY options (both sides, net of costs)

| Study | What it tested | Result |
|---|---|---|
| **SSRN Pillai 2026** — VRP on Nifty 50, realistic frictions | **4 short-vol strategies, 119 expiries, 2015–2025** | ❌ **ALL negative net of costs** — straddles ≈ −44 to −45%/yr; put-write −0.9%/yr **with a 91.6% win rate** |
| Nifty short-straddle retail backtest | systematic short straddle | ⚠️ 68% win, **+14.8% pre-tax** — costs and tail risk decide the sign |
| IRE decade study (2015–2025) | weekly index behaviour, Fri→Thu | avg weekly move only **+0.12%** (no free directional edge); moves >±300 pts occur **28.7%** of weeks (risk is fat) |
| Angled short straddle (Indian market, 2021) | selling variant vs plain short straddle | +18 ppt success, +57% profitability — **still a selling strategy** |

**The brutal honest reading:** after costs, **the documented NIFTY strategies lose on BOTH
sides** (Pillai: sellers all negative; SEBI: buyers 91% negative; retail short-straddle
backtests flip sign once frictions are honest). **There is no free side. Edge must come from
selection, timing, and exits — not from which side of the trade you take.**

### 5.3 Evidence on the BUY side specifically (what we chose)

| Evidence | Result | Class |
|---|---|---|
| IV-crush studies on long straddles | buying vol into events, holding through the print → **systematically pays for the implied>realised gap** | web-verified |
| LEAP backtest (Ryan Tolone, 2024) | naive long-dated deep-ITM calls **can underperform simply holding stock**; "wins concentrated in deep-OTM cells — exactly the cells with worst path-dependence" | web-verified |
| Deep-OTM monthly put buying, BTC/ETH 2018–2026 | tail insurance: **BTC +97.6% but ETH −73.1%**, very low win rates, top-5 trades >10× net profit → "not a stable source of profit" | web-verified |
| Monash VRP study (S&P 500) | systematic **long**-option books exist but the VRP literature's harvest sits with **sellers**; top systematic strategies are short-side | web-verified |
| Long-call retail backtest sites (QQQ/SPY, 85%/71% win) | ⚠️ **not credible** — no honest cost model, CAGR figures in the hundreds of % — discard | unverified |
| Indian equities long-only momentum, net of costs (QuantifiedTrader) | systematic long-only equity momentum survives costs in India — but that is **stock**, not options | web-verified |

**Buy-only verdict:** every credible independent study of *mindless* long-option buying loses
(theta + IV crush). The buy side only works with **(a) cheap vol entry, (b) catalyst/timing,
(c) strict exit before decay outruns edge** — exactly what the 4 buy-compatible styles and
exit ladder are for. That is a design hypothesis until Mode R proves it on our data.

### 5.4 What the owner's system adds that the losing population does not have

| Documented failure (evidence above) | Our counter-measure | Class |
|---|---|---|
| Discretionary entries, no rules | deterministic cells, frozen artifacts, E5 governor | design |
| No exit discipline (hold losers) | exit ladder 1–6, pre-flatten before prints | design |
| Unknown worst case | **loss capped at premium — known at entry** | physics of buy-only |
| Over-trading thin contracts | liquidity + optionability screen at onboarding | design |
| Ban-period / limit violations | ban check in entry gate, intraday MWPL monitoring (E5) | design |
| Strategy chosen by mood | regime router, default = all off | design |
| Unaccounted costs (STT, slippage, spread) | E7 cost model in the **label** before any promotion | design |

### 5.5 Does the multi-style, multi-symbol architecture itself have evidence? — YES

| Study | Finding |
|---|---|
| **CME/Efficient Capital CTA diversification (2014)** | diversification **continuously adds value**; optimal = 2–5 managers per style; complementary-style portfolios beat concentrated ones |
| **Campbell White Paper (Feb 2015)** | clear diversification benefit in strategy portfolios; cross-strategy correlation **weakens when momentum opportunities exist** |
| **CFA Digest (2018) — CTAs: superior performance or diversification only** | median pairwise CTA correlation **0.08**; adding managed futures raised return 8.76% → up to **11%** while cutting vol 9.63% → **8.66%** |
| Campbell multi-strategy flagship | **Sharpe > 1** partly from netting/diversification across 4 strategies; 144 models with a diversification criterion for admission |
| Grinold–Kahn breadth law (Session 26) | **IR ≈ IC × TC × √BR** — 59 symbols × 4 styles raises independent-bet breadth; same IC → materially higher IR |

**Architecture verdict: the multi-cell, multi-style breadth design is the part with the
strongest external evidence. The buy-only options edge itself is the unproven part.**

### 5.6 Honest bottom line on "how profitable"

| Question | Answer |
|---|---|
| Does the architecture work? | ✅ **supported** — specialist cells (measured) + multi-strategy diversification (web-verified, §5.5) |
| Do long options make money by default? | ❌ **NO** — SEBI 91–93% lose; IV crush; Pillai shows even the documented NIFTY strategies lose net of costs |
| Is there a credible profit number today? | ❌ **No — anyone quoting one pre-backtest is guessing.** |
| What is honestly known? | worst case = premium paid (capped) · base rate = ~91–93% of traders lose (they are unsystematic) · breadth/diversification raises IR · our gates only promote what survives CPCV + DSR ≥ 1 + shadow |
| What would make it profitable? | Mode R must find, **on our own data**, entries with expected value > (premium decay + STT + spread + slippage) — then E4 promotion, then shadow proves it live |
| Failure condition (defined up front) | if the research cell cannot clear DSR ≥ 1 on buy-only option strategies after a full search budget → **the scope does not trade**; that is the gate working, not a bug |

---

## §6 · Online mode — the deterministic regime router ("figures out what works now")

| Regime state (computed, rules-only) | Enabled style |
|---|---|
| trend + normal vol | **delta-direction** |
| vol percentile low + event within N days | **vol-alpha** (buy before catalyst) |
| high realized vol, intraday | **gamma-scalper** |
| portfolio DD rising / event risk | **hedge-executor** (buy protection) |
| ambiguous / news window / ban period / no liquidity | **ALL OFF — no entries** |

| # | Rule |
|---|---|
| R1 | regime→style map is itself proven offline in Mode R (DSR-gated like any candidate) |
| R2 | router = **rules, zero ML** — deterministic governor doctrine (E5) |
| R3 | cells stay one-style specialists — router only enables/disables, never re-weights (Session 22) |
| R4 | **default = everything off** — not trading is a valid position |

Honest risk: style-timing rules overfit easily → R1 exists for that reason.

---

## §7 · Exit ladder 1–6 ("mid-trade news → immediately close")

| # | Exit | Fires when |
|---|---|---|
| 1 | **Pre-flatten / never-enter** | print due within ±5 min → no entries; existing positions closed **before** the window |
| 2 | **Hard stop-loss** | premium −30–50% (config per style) → close immediately |
| 3 | **Time-stop** | DTE < X, or decay faster than edge → close (theta works against you daily) |
| 4 | **News-flatten (mid-trade)** | adverse event state fires → close now — owner's rule, event trigger in E5 |
| 5 | **Liquidity gate on exit** | exit only when a real bid exists; if none → degrade to underlying hedge (closing, not opening) |
| 6 | **Kill-switch** | E5 ladder breach → all cells off |

Caveat: closing *during* the news moment pays the widest spread of the day — rule 1 (flatten
**before** the print) matters more than rule 4. With buy-only, "close immediately" saves the
**remaining premium**; max loss was always capped at what was paid.

---

## §8 · Cell-count math after D4 (honest ranges)

| Group | Formula | Count |
|---|---|---|
| Index options | 2 (NIFTY, SENSEX) × 4 styles | **8** |
| Stock options | **N × 4**, N = optionable ∩ liquid subset of the 57 | **8 ≤ N ≤ 57 → 32…228** (N known only after onboarding screen) |
| FX + commodities + crypto (unchanged) | 7 + 4 + 3 | **14** |
| **Total** | 22 + N×4 | **54 … 250** |

⚠️ **O7 surfaced:** hedge-executor (buying protection on portfolio drawdown) is arguably
**one portfolio-level cell**, not 57 per-symbol copies. If so: (2+N)×3 + 1 + 14.
Flagged for owner — affects every count above.

---

## §9 · Provenance

| Claim | Class |
|---|---|
| SEBI 93% / 91.1% / ₹1.81 lakh cr / 17% high-value losses | **Web-verified** — SEBI-PwC Sep-2024, PR 22/2024 |
| Pillai 2026: 4 short-vol strategies all negative net of costs, 119 expiries | **Web-verified** — SSRN |
| LEAP underperformance, deep-OTM tail-insurance instability, IV-crush losses | **Web-verified** — Tolone 2024, HTX 2026, IV-crush literature |
| Long-call backtest sites (85% win, CAGR 300%+) | **Unverified — discarded** (no credible cost model) |
| CTA/multi-strategy diversification (corr 0.08, 8.76→11%, Sharpe>1) | **Web-verified** — CME 2014, Campbell 2015, CFA Digest 2018 |
| NSE 6 indices + >200 securities; single weekly expiry; upfront premium; MWPL ban; NIFTY lot 65 | **Web-verified** — NSE, SEBI circulars (Oct 2024–May 2025) |
| 4-of-8 style compatibility | **Derived from option physics** — not an empirical study |
| Per-symbol specialist cells (vs one generalist) | **Measured (internal)** — Session 22 ScaleZero evidence |
| Regime router, exit ladder, gates | **Design requirements** — not yet measured |
| Profitability of this scope | **UNPROVEN until Mode R backtest + E4 + shadow** — §5.6 failure condition defined |

---

## §10 · Open decisions this session creates

- **D4** — cash equities dropped → options-buying roster (this artifact is the working spec).
- **O6** — add "scalping" to the FX roster (30 → 31 locked styles)?
- **O7** — hedge-executor: per-symbol cell × N, or **one portfolio-level cell**?
- Carry-forward: O1 (predictions 0 cells), O3 (index F&O granularity — sharpened by §4),
  O4 (7 off-index names), O5 (styles per instrument — D4 now implies **4 per optionable symbol**).
- **Onboarding screen** — intersect 57 names with option master + liquidity filter → sets N.

*Artifact of Session 27. Companion to QUANT-TRADING-ALPHA-EXPLAINED.md (Session 26),
FX-SCALPING-BUILD-PLAN.md (Session 25), FIRST-BATCH-NON-LLM-AGENTS.md (needs D4 rewrite).*
