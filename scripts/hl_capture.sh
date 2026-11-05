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
# Each run re-captures the trailing window (default 3 days) and MERGES it into
# the local archive (`--merge`), then UPSERTs the D3 registry row (the registry
# is a mutable manifest; D4 remains the append-only ledger). Capture is
# deterministic: identical windows produce byte-identical parquet.
#
# Why --merge is not optional: the public API retains only ~3 days of 1m bars, so
# a run that just REWRITES the trailing window can never grow the archive — it
# stays at 3 days forever (measured 2026-11-04: 4,320 rows on every run, which
# would have made the 300-bar training target unreachable no matter how long the
# cron ran). A single-instance lock prevents two runs from rewriting one artifact.
#
# Identity: BTC-USDC — the venue-native quote of the Hyperliquid perp. This is
# NOT a relabel of the legacy `hyperliquid/BTC-USD/1d` row (A0 §4).
set -euo pipefail

# cron/systemd run with a minimal PATH: resolve the real tool locations
# explicitly so the capture cannot fail silently under a scheduler.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin"
for tool in uv docker python3 flock; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "hl_capture: MISSING prerequisite: $tool (PATH=$PATH)" >&2
        exit 2
    }
done

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

# One capture at a time: two runs writing the same parquet is a corruption
# hazard, and at boot cron can fire while an earlier run is still alive.
LOCK="${TMPDIR:-/tmp}/gfti_hl_capture.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "hl_capture: another capture holds $LOCK — nothing to do"
    exit 0
fi

cd "$REPO/python/collect"
uv run hyperliquid_daily.py \
    --coin "$COIN" --interval "$INTERVAL" --symbol "$SYMBOL" \
    --start-ms "$START_MS" --end-ms "$END_MS" \
    --out "$REPO/$OUT_REL" --summary "$SUMMARY" --merge >/dev/null

uv run register_b1.py \
    --summary "$SUMMARY" --d0-path "$OUT_REL" \
    --data-class hyperliquid \
    --volume-available true --volume-kind base_asset_volume --volume-unit BTC \
    --schema-version 1 \
    --legacy-writer-reason "hyperliquid candleSnapshot 1m bars carry OHLCV; the rail tick contract is single-price, so this capture still writes D0 until A-7b (rail bar schema) lands" \
    --pre-inception-note "public candleSnapshot serves only the most recent ~3 days at 1m (measured 2026-11-03: 3d served, 4d empty), so nothing earlier is retrievable and no deeper intraday history is claimed by any row"

# Derive the DAILY projection of the accumulated capture. The training lane is
# day-bar based and refuses intraday keys, so this is how P1 intraday
# accumulation reaches it — with complete days only (partial edge days are
# dropped by the resampler, never manufactured into a day bar).
#
# The projection is registered under a `-derived` key, NOT `hyperliquid/BTC-USDC/1d`:
# that key belongs to the venue-native full-history series (1,347 bars,
# 2023-02-26 onward, captured by hand and verified field-for-field against the
# legacy BTC-USD daily row). Before 2026-11-04 this line registered the 2-row
# projection under the plain key, so every cron tick would have silently
# overwritten the canonical row's identity.
DAILY_REL="d0/hyperliquid/BTC-USDC/1d-derived.parquet"
DAILY_SUMMARY="${TMPDIR:-/tmp}/gfti_hl_btc_usdc_1d.json"
uv run resample_1m_to_1d.py \
    --d0-path "$REPO/$OUT_REL" --dataset-key "hyperliquid/${SYMBOL}/${INTERVAL}" \
    --symbol "$SYMBOL" \
    --out "$REPO/$DAILY_REL" --summary "$DAILY_SUMMARY" >/dev/null

uv run register_b1.py \
    --summary "$DAILY_SUMMARY" --d0-path "$DAILY_REL" \
    --data-class hyperliquid --key-suffix=-derived \
    --legacy-writer-reason "derived 1d projection of a legacy 1m bar capture; OHLCV is not representable in the rail tick contract until A-7b lands" \
    --pre-inception-note "derived 1d projection of the PIT 1m capture hyperliquid/BTC-USDC/1m — history begins when that capture began (public API retains only ~3 days of 1m), so nothing earlier exists in this lineage" \
    >/dev/null

echo "hl_capture: OK window=${START_MS}..${END_MS} artifact=${OUT_REL} daily=${DAILY_REL}"
