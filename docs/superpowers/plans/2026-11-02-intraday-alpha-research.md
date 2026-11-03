# Intraday Alpha Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a source-honest NIFTY/BTC/GOLD non-LLM research pipeline with finest-available data, multi-timeframe features, trial-accounted alpha search, full risk metrics, and optional Colab GPU execution.

**Architecture:** Collectors write immutable source-resolution D0 Parquet and explicit sidecar provenance; D3 registration records granularity, volume semantics and content hashes. A deterministic feature builder aligns higher timeframes without lookahead, and an E4 research evaluator runs preregistered strategies/models with purged validation, realistic costs, a complete trial ledger and risk metrics. Colab is only an optional compute runner; artifacts return to local verification and existing promotion gates.

**Tech Stack:** Python 3.11+, `uv`, pytest, Ruff, PyArrow/Parquet, DuckDB, NumPy/SciPy; PyTorch available in Colab. Existing PostgreSQL/D3 and E4 gate infrastructure remains authoritative.

**Spec:** `docs/superpowers/specs/2026-11-02-intraday-alpha-research-design.md`

## Global Constraints

- Scope is exactly NIFTY, BTC, GOLD until the owner expands it.
- BTC's current source is Hyperliquid BTC-USD perp candles; M1 is not raw trade ticks.
- NIFTY NSE bhavcopy is EOD only; do not enable intraday training without an owner-approved, rights-reviewed source.
- HistData GOLD is M1 price data with unavailable/zero source volume; never use it as real volume.
- No synthetic bars, fabricated volume, implicit gap filling, lookahead, or LLM in the decision path.
- All features and labels are point-in-time; include applicable fees, spread, slippage, turnover and funding/borrow costs.
- CPCV is calendar-day grouped; purge ≥ maximum label horizon, use embargo and an untouched final holdout.
- Log every candidate and every retrain/re-gate trial; cap at 1,000 preregistered candidates per style; certification requires DSR ≥0.95 against effective decorrelated trials.
- Report expectancy, WR, R:R, friction-adjusted break-even WR, profit factor, Sharpe, PSR/DSR, max drawdown, time underwater, recovery, CVaR/ES, turnover, sample size, regimes and gap coverage.
- Training outputs candidate artifacts only. E4 and owner-controlled promotion remain mandatory.
- Colab lifecycle is `new` → `exec` → `stop`; never use `colab run`; GPU absence is reported and may fall back to CPU.

---

## File map

| File | Responsibility |
|---|---|
| `python/collect/hyperliquid_daily.py` | Existing interval-capable BTC candle collector; add bounded-window CLI and M1 capture validation. |
| `python/collect/histdata_daily.py` | Existing HistData parser; retain raw M1 records as D0 while keeping daily aggregation explicitly separate. |
| `python/collect/test_hyperliquid_daily.py` | BTC interval/pagination/integrity regression tests. |
| `python/collect/test_histdata_daily.py` | HistData raw-minute, zero-volume semantics, gap and aggregation tests. |
| `compose/sql/006_d3_intraday_metadata.sql` | Add explicit volume semantics and schema version to D3 dataset metadata. |
| `python/collect/register_b1.py` | Populate/validate the new metadata fields for registrations. |
| `python/train/features.py` | Source-aware resampling, multi-timeframe alignment, indicator computation and availability masks. |
| `python/train/test_features.py` | Feature causality, resampling, missing-data and volume-semantics tests. |
| `python/train/alpha_search.py` | Preregistered strategy/model candidate generation and append-only trial records. |
| `python/train/metrics.py` | Trade/risk metrics and reference-checked PSR/DSR. |
| `python/train/validation.py` | Calendar-day CPCV splits with purge, embargo and final holdout. |
| `python/train/test_research.py` | Leakage, costs, gaps, metrics, trial count and split tests. |
| `python/train/colab_runner.py` | Env-configured Colab entrypoint using the same local features/evaluation definitions. |
| `python/train/test_colab_runner.py` | Runner configuration validation and artifact manifest checks without Colab dependency. |
| `docs/gates/W1.md` | Final captured gate evidence; do not write success claims before actual runs. |

## Task 1: Prove and collect source-resolution intraday data

**Files:**
- Modify: `python/collect/hyperliquid_daily.py`
- Modify: `python/collect/test_hyperliquid_daily.py`
- Modify: `python/collect/histdata_daily.py`
- Modify: `python/collect/test_histdata_daily.py`

**Interfaces:**
- Preserve `fetch_all(coin: str, interval: str, until_ms: int, _post=None, max_windows: int = 64) -> list[dict]` compatibility while adding bounded start/end collection.
- Preserve existing normalized candle columns; append no fabricated values.
- Add a raw HistData M1 Parquet writer with `ts_ms, open, high, low, close, volume, volume_available`; `volume_available` is false for HistData rows.

- [ ] **Step 1: Add failing BTC M1 tests** for interval-aligned closed bars, bounded start/end, backward pagination, overlap rejection and actual volume/trade-count preservation.
- [ ] **Step 2: Run only targeted tests** with `cd python/collect && uv run pytest test_hyperliquid_daily.py -q`; confirm new cases fail before implementation.
- [ ] **Step 3: Add bounded interval capture** using existing `VALID_INTERVALS`, `fetch_window`, retry and Decimal normalization. Set pagination bounds from a user-visible CLI option; fail rather than silently truncate when history exceeds the configured limit.
- [ ] **Step 4: Add failing HistData M1 tests** asserting each source CSV minute is retained, timestamp order is stable, raw zero volume remains literal but `volume_available` is false, and aggregation gaps are not fabricated.
- [ ] **Step 5: Implement raw M1 output** from the existing validated zip/member parser; retain current daily output as a distinct derived dataset with provenance.
- [ ] **Step 6: Run both collector test files** and `uvx ruff check` on the two collectors and tests; inspect a bounded live BTC interval probe before scheduling a full M1 pull.
- [ ] **Step 7: Record source coverage honestly.** If the API does not expose the intended historical range, register the actual range and leave the missing coverage as an open gap.

## Task 2: Make D3 volume and schema provenance explicit

**Files:**
- Create: `compose/sql/006_d3_intraday_metadata.sql`
- Modify: `python/collect/register_b1.py`
- Create: `python/collect/test_register_b1_metadata.py`

**Interfaces:**
- New nullable dataset fields: `volume_kind TEXT`, `volume_unit TEXT`, `volume_available BOOLEAN NOT NULL DEFAULT FALSE`, `schema_version TEXT NOT NULL DEFAULT '1'`.
- Registration accepts explicit metadata and rejects `volume_available=true` if `volume_kind` or `volume_unit` is empty.

- [ ] **Step 1: Write failing registration tests** for valid trade-volume, HistData unavailable volume, and invalid “available with no kind/unit” metadata.
- [ ] **Step 2: Run targeted registration tests** and confirm failure before code changes.
- [ ] **Step 3: Add the additive SQL migration** with safe defaults for existing daily rows; do not update historical provenance to claim unavailable fields existed.
- [ ] **Step 4: Extend registry CLI** with explicit volume metadata and schema version; require metadata for newly registered intraday datasets.
- [ ] **Step 5: Run registration tests** and apply the migration against the local compose DB in a reversible, non-destructive verification flow.

## Task 3: Build causal multi-timeframe features

**Files:**
- Create: `python/train/features.py`
- Create: `python/train/test_features.py`
- Modify: `python/train/pyproject.toml` only if a required dependency is absent.

**Interfaces:**
- `resample_bars(rows, interval, *, source_interval, volume_available, session_timezone='UTC') -> list[dict]` derives coarser bars only from finer closed bars and preserves missing windows as gaps; resolution and volume semantics come from D3 metadata, never timestamp inference.
- `build_features(rows, *, source_interval, volume_available, frames=('5m','15m','1h','4h','1d')) -> FeatureSet`; `FeatureSet` contains sorted timestamps, numeric matrix, feature names, per-feature availability mask, source hash and feature schema hash.
- Compute returns/momentum, SMA/EMA, RSI, MACD, ATR, realized volatility, range/body and (only when available) volume/trade-count changes, z-scores and VWAP/flow features.

- [ ] **Step 1: Add tests** for exact UTC bucket boundaries, source-volume summation, missing-minute gap preservation, no lookahead across unfinished higher-timeframe bars, and volume feature masking for HistData.
- [ ] **Step 2: Run `cd python/train && uv run pytest test_features.py -q`** and confirm tests fail before implementation.
- [ ] **Step 3: Implement resampling** with explicit interval validation and no finer-than-source conversion.
- [ ] **Step 4: Implement indicators** with documented warm-up NaNs/availability masks; prohibit filling a warm-up or gap with future values.
- [ ] **Step 5: Run feature tests and Ruff**; verify feature hashes are deterministic across two identical runs.

## Task 4: Add trial-accounted alpha evaluation and risk metrics

**Files:**
- Create: `python/train/validation.py`
- Create: `python/train/metrics.py`
- Create: `python/train/alpha_search.py`
- Create: `python/train/test_research.py`
- Modify: `python/train/walk_forward.py` only to call the shared implementations or remove it from the active certification path if its daily-only assumptions conflict.

**Interfaces:**
- `calendar_cpcv_splits(timestamps, *, n_splits, purge_bars, embargo_bars, holdout_start) -> list[Fold]` returns disjoint train/test indices and a final untouched holdout.
- `evaluate_trades(trades, equity_curve, periods_per_year, trials) -> dict` returns net expectancy/trade and R, WR, avg win/loss, R:R, friction-adjusted break-even WR, PF, Sharpe, PSR/DSR, max drawdown, time underwater, recovery, CVaR/ES, turnover, sample count and trial count.
- `run_search(dataset, features, *, style, candidates, budget=1000) -> ResearchResult` persists every candidate, including failed/invalid candidates and retrain/re-gate attempts, with dataset and feature hashes.
- Preregister candidate families and search budget before opening the untouched holdout; candidate budget is at most 1,000 per style.

- [ ] **Step 1: Write failing split tests** for calendar grouping, purge ≥ label horizon, embargo, and untouched holdout exclusion.
- [ ] **Step 2: Write failing metric tests** with synthetic trades where exact expectancy, R:R, WR, PF, Sharpe, drawdown and costs are known; add published/reference DSR test vectors and degenerate-input cases.
- [ ] **Step 3: Write failing trial-ledger tests** proving every attempted candidate gets an immutable row and search stops at 1,000 candidates per style.
- [ ] **Step 4: Run `cd python/train && uv run pytest test_research.py -q`**; confirm the new tests fail before implementation.
- [ ] **Step 5: Implement purged validation and net-of-cost trade accounting**; reject missing label timestamps or unverified dataset/gap state before candidate evaluation.
- [ ] **Step 6: Implement metrics** and validate DSR against a reference implementation/test vectors. DSR must use logged effective decorrelated trial count; no hard-coded grid size or certification from an approximation.
- [ ] **Step 7: Implement a preregistered candidate set** covering trend/momentum, mean reversion and breakout using the causal feature set; keep the final holdout unread until search and selection are frozen.
- [ ] **Step 8: Run targeted tests, Ruff, and existing train tests.** The expected honest result may be “no candidate passes DSR ≥0.95”; that is a valid research outcome, not a pipeline failure.

## Task 5: Implement optional Colab GPU execution

**Files:**
- Create: `python/train/colab_runner.py`
- Create: `python/train/test_colab_runner.py`
- Modify: `docs/superpowers/specs/2026-11-02-intraday-alpha-research-design.md` only if verified CLI behavior requires a correction.

**Interfaces:**
- Runner reads `GFTI_DATA_PATH`, `GFTI_DATASET_KEY`, `GFTI_SOURCE_SHA256`, `GFTI_OUTPUT_DIR`, and `GFTI_STYLE` from environment; no CLI positional arguments are required because `colab exec` accepts a source file, not trailing script arguments.
- Output is candidate model state plus a JSON manifest containing source/feature hashes, device, trial count, folds, metrics and dependency versions.

- [ ] **Step 1: Write configuration tests** for missing inputs, hash mismatch, CPU/GPU detection and output manifest validation.
- [ ] **Step 2: Implement the same feature, fold, cost and metric interfaces** from Tasks 3–4; do not duplicate metric implementations in the runner.
- [ ] **Step 3: Run local runner tests** without requiring a GPU or Colab credentials.
- [ ] **Step 4: Start a named Colab session only after tests pass**, install only missing declared dependencies, upload a verified D0 input, and invoke `colab exec -s <name> -f python/train/colab_runner.py --env ...` using the actual CLI help syntax.
- [ ] **Step 5: Download candidate artifacts and verify hashes/manifests locally**; use `colab stop -s <name>` even after failure. Do not promote or register from Colab.

## Task 6: Run three-symbol proof and publish actual gate evidence

**Files:**
- Modify: `python/gates/w1_gates.py`
- Create or update: `docs/gates/W1.md`

- [ ] **Step 1: Repair W1 gate debris** before invoking it: remove unreachable branches, fix artifact SQL/reverification, and make replay dataset/granularity explicit.
- [ ] **Step 2: Add negative training-lock tests** proving unverified datasets and open unjustified gaps refuse before reading labels or fitting a model.
- [ ] **Step 3: Run data integrity and registration checks** for all three symbols; BTC may enter M1 training only with verified coverage, GOLD remains price-only, and NIFTY stays EOD-only unless an approved intraday source exists.
- [ ] **Step 4: Run candidate research per eligible symbol/resolution** through CPCV and the untouched holdout; report every required metric and the effective trial count, without suppressing failed regimes or gaps.
- [ ] **Step 5: Run the existing E4/artifact/replay gates** only for outputs whose B-GATE passed; no candidate failing DSR ≥0.95 is promoted.
- [ ] **Step 6: Write `docs/gates/W1.md` from captured command output** with source coverage, granularity, volume semantics, gaps, metrics, holdout status and failed/unrun items.
- [ ] **Step 7: Run project verification:** collector tests, train tests, gate-targeted tests, `uvx ruff check` on modified Python files, migration checks, and the relevant Rust engine tests only if the replay interface changed.
- [ ] **Step 8: Check `git status --short`, `git diff --stat`, and the diff for secrets or edits outside the project.** Report the exact commands and actual results.

## Self-review

- Spec coverage: collector/source granularity (Task 1); data provenance and volume semantics (Task 2); causal multi-timeframe features (Task 3); alpha search, CPCV, all requested metrics, DSR and trial cap (Task 4); optional GPU runner (Task 5); three-symbol proof and gate evidence (Task 6).
- Source limitations are explicit: no raw BTC trades from the locked candle API, no current NIFTY intraday source, and HistData volume unavailable. These remain data gates, not hidden implementation assumptions.
- DSR acceptance follows the council-validated fleet blueprint (≥0.95 against effective decorrelated trials); older `DSR >= 1` language is not used because DSR is a probability.
- Every step names a concrete change or verification command; no undefined work is deferred to Task 1.
