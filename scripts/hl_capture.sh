#!/usr/bin/env bash
# P1 continuous capture — Hyperliquid BTC perp (venue quote USDC) 1-minute bars.
#
# WHY THIS EXISTS: the public candleSnapshot API serves only ~3 days of 1m
# history (measured 2026-11-03: 3d served, 4d empty). Historical 1m backfill
# is therefore impossible; the only way to accumulate a PIT intraday series is
# to capture on a schedule. Run this from cron/systemd-timer, e.g.
#
#   */15 * * * * /home/madava/global-financial-trading-intelligence/scripts/hl_capture.sh \
#       >> ~/.cache/gfti/hl_capture.log 2>&1
#
# Each run re-captures the trailing window (default 3 days), rewrites the D0
# artifact and UPSERTs the D3 registry row (the registry is a mutable manifest;
# D4 remains the append-only ledger). Capture is deterministic: identical
# windows produce byte-identical parquet (proven by sha256 equality).
#
# Identity: BTC-USDC — the venue-native quote of the Hyperliquid perp. This is
# NOT a relabel of the legacy `hyperliquid/BTC-USD/1d` row (A0 §4).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DAYS="${1:-3}"
COIN="BTC"
SYMBOL="BTC-USDC"
INTERVAL="1m"
OUT_REL="d0/hyperliquid/BTC-USDC/1m.parquet"
SUMMARY="${TMPDIR:-/tmp}/gfti_hl_btc_usdc_${INTERVAL}.json"

read -r START_MS END_MS < <(python3 - "${DAYS}" <<'PY'
import sys, time
step = 60_000  # 1m
end = (int(time.time() * 1000) // step) * step
start = end - int(sys.argv[1]) * 86_400_000 // step * step
print(start, end)
PY
)

cd "$REPO/python/collect"
uv run hyperliquid_daily.py \
    --coin "$COIN" --interval "$INTERVAL" --symbol "$SYMBOL" \
    --start-ms "$START_MS" --end-ms "$END_MS" \
    --out "$REPO/$OUT_REL" --summary "$SUMMARY" >/dev/null

uv run register_b1.py \
    --summary "$SUMMARY" --d0-path "$OUT_REL" \
    --data-class hyperliquid \
    --volume-available true --volume-kind base_asset_volume --volume-unit BTC \
    --schema-version 1 \
    --pre-inception-note "public candleSnapshot serves only the most recent ~3 days at 1m (measured 2026-11-03: 3d served, 4d empty), so nothing earlier is retrievable and no deeper intraday history is claimed by any row"

echo "hl_capture: OK window=${START_MS}..${END_MS} artifact=${OUT_REL}"
