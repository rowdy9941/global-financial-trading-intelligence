# P1 data — Hyperliquid BTC perp, BTC-USDC identity (measured 2026-11-03)

Pilot 1 (A0 §4) is the **Hyperliquid BTC perpetual, venue quote USDC**. This note
records what the public API can and cannot supply, what was actually captured and
registered, and what is still missing. No claim here extends past the evidence.

## 1 · API retention (measured, not assumed)

`POST https://api.hyperliquid.xyz/info {"type":"candleSnapshot"}` — public,
key-free. Probed with 1-hour windows at increasing age:

| Interval | Served at | Empty at | Verdict |
|---|---|---|---|
| `1m` | 3 days | 4 days | **~3-day retention** |
| `5m` | 7 days | 30 days | ~1–4 weeks |
| `15m` | 30 days | 90 days | months |
| `1h` | 90 days | ~1 year | months |
| `1d` | perp inception 2023-11-09 | — | full history |

**Consequence:** tick/1m history beyond ~3 days cannot be backfilled from the
public API. A PIT intraday series must be *captured going forward*
(`scripts/hl_capture.sh`). The legacy `hyperliquid/BTC-USD/1d` daily row is
unaffected and remains a separate identity.

## 2 · Capture + registration (real run)

`python/collect/hyperliquid_daily.py --coin BTC --interval 1m --symbol BTC-USDC`

| Field | Value |
|---|---|
| dataset_key | `hyperliquid/BTC-USDC/1m` |
| window | 2026-10-31 → 2026-11-03 (3 days, aligned) |
| bars | 4,320 requested / 4,320 returned — **0 missing slots** |
| integrity | PASS (closed bars only, monotonic, OHLC bounds, venue-onset filtered) |
| sha256 | `f300a77ac4a38ffbab3c845bf987c57e21ed2e55884c2a9a573b52913701e62c` |
| D0 path | `d0/hyperliquid/BTC-USDC/1m.parquet` (local, not tracked in git) |
| D3 volume metadata | `volume_available=true`, `volume_kind=base_asset_volume`, `volume_unit=BTC` |
| D3 `verified` | **true** — set by the B-GATE rule (no open unjustified gaps), not by assertion |

Determinism: a second, independent capture of the **same fixed window** produced a
byte-identical parquet (`sha256` equal). Capture is reproducible.

Registry honesty: the pre-first_day gap row is `justified` with the *measured*
reason (API retention), not the generic "deeper history in another row" text the
registrar defaults to for daily spot captures. `register_b1.py` gained
`--pre-inception-note` for exactly this; a misleading justification is worse than
no row.

## 3 · Derived daily projection (how accumulation reaches the training lane)

The WF lane is day-bar based and **refuses intraday datasets**
(`walk_forward.require_daily_lane`, added after this dataset produced all-zero
Sharpes with `dsr=NaN` — the bug was the lane, not the data). So the 1m capture is
projected to 1d by `python/collect/resample_1m_to_1d.py`, which never manufactures a
day bar:

- a day is kept only if **complete** — first slot is the UTC midnight open, every
  expected slot present, no interior hole;
- a partial **edge** day (capture window cuts it) is **dropped**: its last 1m close
  is not the day's close;
- a partial **interior** day is a real hole and is **refused** unless
  `--allow-partial`, in which case it is dropped and listed in the summary.

Run (also done automatically at the end of `scripts/hl_capture.sh`):

```
uv run resample_1m_to_1d.py --d0-path ../../d0/hyperliquid/BTC-USDC/1m.parquet \
    --dataset-key hyperliquid/BTC-USDC/1m \
    --out ../../d0/hyperliquid/BTC-USDC/1d.parquet --summary /tmp/hl_1d.json
```

Result on the 4,320-bar capture: **2 complete days kept** (2026-11-01, 11-02),
**2 edge days dropped** (10-31 with 1,035/1,440 bars, 11-03 with 405/1,440).
Registered as `hyperliquid/BTC-USDC/1d` with the lineage in `source`:
`derived:resample-1m-to-1d:hyperliquid/BTC-USDC/1m:sha-<digest>`.

### Cross-check against the venue's own daily bar

The derived 1d series and an independently captured `hyperliquid/BTC-USD/1d`
(the API's own daily bars) were compared on every overlapping day:

```
2026-11-01  open/high/low/close/volume/n_trades identical  (close 78326.0)
2026-11-02  open/high/low/close/volume/n_trades identical  (close 76805.0)
days_compared 2  all_identical True
```

Two independent paths agree to the last decimal, which is the strongest integrity
signal available without venue files.

## 4 · Known limitations (explicit)

1. **D3 gap calendar is day-granular.** A missing 1-minute bar inside a captured
   day is *not* representable as a gap row; only whole-day holes are. Current
   capture has zero missing slots, so nothing is concealed — but future intraday
   holes must be caught by the collector's own `missing_bar_slots` check, not the
   registry.
2. **Not enough bars to train yet.** The derived 1d projection currently has
   **2 bars**; the lane refuses with `data too short for even one fold: 2 bars
   at folds=6 needs at least 300 bars`, so no artifact can be produced until
   accumulation reaches ~300 days. The DSR/WF gate stays the promotion
   authority (0.4894 on the legacy daily row, below the 0.95 gate).
3. **No historical trades.** True tick (per-trade) data from HL is only
   available as a live WebSocket capture from now on; it cannot be reconstructed.

## 5 · Next steps

- let `scripts/hl_capture.sh` keep running (cron, every 15 min) so the 1m series
  and its derived 1d projection grow toward the 300-bar WF minimum;
- add a WS `trades` recorder for genuine tick data going forward;
- only then extend the WF/eval gate to 1m bars on accumulated real data.
