# Intraday Alpha Research & Training — 3-Symbol Design

Status: proposed implementation design; no profitability or tick-coverage claim.
Scope is exactly NIFTY, BTC, and GOLD until the owner expands it.

## Goal and non-goals

Build a source-honest, reproducible non-LLM research lane that can discover and
evaluate candidate alpha from the finest validated historical data available,
with multi-timeframe features, real-volume semantics, explicit gaps, realistic
costs, and the project's complete research metrics. Colab GPU is an optional
compute runner; it does not change data provenance, validation, or promotion
gates. This work does not claim profitability, enable live trading, or put an
LLM in the decision path.

## Source and granularity contract

Never call an OHLC candle a raw tick, fabricate volume, or turn an absent bar
into an observed zero-volume bar. Keep raw/source artifacts immutable and record
source, venue, resolution, coverage, volume meaning/unit/availability, schema
version, content hash, and license/provenance in the dataset manifest.

| Symbol | Current locked source and evidence | Research ceiling until approved source change |
|---|---|---|
| BTC | Hyperliquid BTC-USD perp; collector supports candle intervals down to 1m, with source volume and trade count. Existing registered artifact is 1d. It does not provide historical trade-by-trade ticks. | Collect 1m candles first; aggregate validated 5m/15m/1h/4h/1d frames. Label as candles, not ticks. |
| NIFTY | Official NSE F&O bhavcopy; daily contract-level OHLC/OI/volume/trades. | EOD research only. Intraday/tick training is blocked pending an approved source and rights review. |
| GOLD | HistData XAUUSD M1 fallback; source volume is identically zero/unavailable. cTrader is the locked preferred venue but capture is blocked on owner credentials. | M1 price research with volume-dependent features disabled and availability marked false. Real-volume/tick research waits for cTrader or another approved source. |

Higher timeframes may only be derived from validated lower-resolution bars using
documented UTC or venue-session boundaries. Never derive finer data from coarser
bars. Keep calendar/session gaps in the gap ledger and block a training claim
when the relevant source coverage is not verified.

## Data and feature path

1. Collect source-resolution artifacts to D0, deterministic and hash-verified.
2. Run integrity checks: order/duplicates, closed-bar cutoff, OHLC constraints,
   source-specific volume semantics, expected intervals, gap classifications,
   and venue/session calendars. Unresolved gaps remain open; no synthetic fill.
3. Register source, granularity, volume availability/units, feature schema
   version, hash, and coverage in D3. B-GATE must pass before training.
4. Build aligned multi-timeframe observations. Every feature at decision time t
   uses only data timestamped at or before t; higher-timeframe bars are usable
   only after their close. Persist feature definitions and hashes.
5. Include, where supported by the source: returns/momentum, SMA/EMA, RSI, MACD,
   ATR/true range, realized volatility, range/body features, volume and trade-
   count changes, volume z-scores, and VWAP/flow features only when real volume
   exists. Store feature availability masks; do not impute unavailable volume.
6. Research candidate alpha using the preregistered search budget; record every
   candidate/configuration and retrain/re-gate cycle before evaluation.

## Evaluation and certification

- Use point-in-time labels net of fees, spread, slippage, turnover, funding/borrow
  where applicable, and instrument-specific execution constraints.
- Use calendar-day-grouped CPCV with purge at least the maximum label horizon,
  embargo, and a final untouched holdout. Report folds and regimes separately.
- For each candidate and regime, report: net expectancy per trade and in R,
  win rate, average win/loss, R:R, friction-adjusted break-even win rate,
  profit factor, Sharpe, PSR/DSR, max drawdown, time under water, recovery,
  CVaR/Expected Shortfall, turnover, sample/trade counts, and gap coverage.
- DSR is a probability in [0,1]. Use the fleet blueprint's certification bar
  **DSR >= 0.95** against the **effective decorrelated** trial count; preregister
  and cap the search at 1,000 candidates per style, logging every trial. Do not
  certify with the older impossible `DSR >= 1` wording, a hard-coded trial
  count, or an uncalibrated approximation. A failure to pass means no promotion.
- No model or strategy is promoted by the trainer. Existing E4 gates and the
  owner-controlled promotion ladder remain authoritative. Report research
  performance as evidence, not a profitability guarantee.

## Colab runner

Colab is a disposable optional execution environment for compute-heavy training.
The reproducible input is a verified D0 artifact plus its manifest/hash; the
runner cannot collect, repair, or silently re-label data. It must run the same
feature, fold, cost, and metric definitions as the local baseline. Downloaded
artifacts are candidates only and must pass local hash, schema, replay, and E4
gates before registration/promotion. Always use `colab new` → `colab exec` →
`colab stop`; never use `colab run`. GPU absence falls back explicitly to CPU.

## Delivery sequence and acceptance criteria

1. Extend BTC collection to M1 and prove deterministic source-quality integrity;
   document that this is candle-level, not raw tick data.
2. Preserve HistData M1 as price-only and mark volume unavailable; do not imply
   its daily aggregate is adequate for intraday certification.
3. Keep NIFTY's current EOD data out of intraday certification until a separate
   owner-approved intraday source, rights, and data contract are verified.
4. Implement/test feature generation, multi-timeframe alignment, volume masks,
   gap-aware labels, metrics, and trial ledger on fixture data before a full run.
5. Replace/validate the approximate DSR implementation against a known
   reference and enforce the effective-trial count/cap.
6. Run Colab only after the corrected local pipeline and tests pass; verify GPU
   device, execute with the documented `-f` interface, retrieve artifacts, and
   run local E4 validation. No success claim without captured command output.

End-to-end success is source-specific: BTC may demonstrate M1 candle research;
GOLD may demonstrate M1 price-only research; NIFTY may demonstrate EOD research.
The system must not claim tick-level or real-volume results across all three
until the missing source capabilities are actually approved, collected, and
verified.
