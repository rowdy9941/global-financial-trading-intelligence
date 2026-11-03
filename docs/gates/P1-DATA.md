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

## 3 · Known limitations (explicit)

1. **D3 gap calendar is day-granular.** A missing 1-minute bar inside a captured
   day is *not* representable as a gap row; only whole-day holes are. Current
   capture has zero missing slots, so nothing is concealed — but future intraday
   holes must be caught by the collector's own `missing_bar_slots` check, not the
   registry.
2. **3 days is not enough for training.** Training on P1 intraday requires the
   capture loop to accumulate weeks; the DSR/WF gate remains the promotion
   authority (currently 0.4894 on the daily row, below 0.95).
3. **No historical trades.** True tick (per-trade) data from HL is only
   available as a live WebSocket capture from now on; it cannot be reconstructed.

## 4 · Next steps

- schedule `scripts/hl_capture.sh` (cron/systemd timer) so the intraday series
  grows;
- add a WS `trades` recorder for genuine tick data going forward;
- only then extend the WF/eval gate to 1m bars on accumulated real data.
