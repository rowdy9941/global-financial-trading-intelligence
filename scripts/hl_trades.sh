#!/usr/bin/env bash
# P1 continuous TICK recorder — Hyperliquid BTC perp trades (quote USDC).
#
# WHY: per-trade prints exist ONLY as a live WebSocket feed. The REST API serves
# candles, and 1m candles are retained ~3 days (measured, docs/gates/P1-DATA.md),
# so tick history can never be backfilled — it can only be recorded going
# forward. Every hour not recorded is gone permanently.
#
# Single-instance contract: an flock guard means only ONE recorder runs at a
# time. This script therefore loops forever (55-minute sessions with a 5-second
# pause between them, so a session boundary never leaves a gap); cron's job is
# only to restart it if it ever dies. Overlapping cron ticks exit immediately.
#
#   */15 * * * * /home/madava/global-financial-trading-intelligence/scripts/hl_trades.sh \
#       >> ~/.cache/gfti/hl_trades.log 2>&1
#
# Output: append-only hourly gzipped JSONL segments under
# d0/hyperliquid/BTC-USDC/trades/<YYYY-MM-DDTHH>.jsonl.gz (raw, not rewritten —
# live capture is not reproducible, unlike the candle collectors).
set -euo pipefail

# cron/systemd run with a minimal PATH: resolve tools explicitly so the recorder
# cannot fail silently under a scheduler.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin"
for tool in uv flock; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "hl_trades: MISSING prerequisite: $tool (PATH=$PATH)" >&2
        exit 2
    }
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COIN="${COIN:-BTC}"
SYMBOL="${SYMBOL:-BTC-USDC}"
OUT_REL="d0/hyperliquid/${SYMBOL}/trades"
LOCK="${TMPDIR:-/tmp}/gfti_hl_trades.lock"
SESSION_S="${SESSION_S:-3300}"   # 55 min < the 60-min hour, so segments close cleanly
IDLE_S="${IDLE_S:-120}"          # a silent feed for 2 min is a failure, not a quiet market

exec 9>"$LOCK"
if ! flock -n 9; then
    echo "hl_trades: another recorder holds $LOCK — nothing to do"
    exit 0
fi

cd "$REPO/python/collect"
while :; do
    # a failed session must not stop the loop: log it and reconnect (the next
    # iteration re-establishes the socket with backoff inside the recorder)
    if uv run hl_trades_ws.py \
        --coin "$COIN" --symbol "$SYMBOL" \
        --out-dir "$REPO/$OUT_REL" \
        --duration "$SESSION_S" --idle-timeout "$IDLE_S" --flush-every 200; then
        echo "hl_trades: session ok $(date -u +%FT%TZ)"
    else
        echo "hl_trades: session FAILED rc=$? at $(date -u +%FT%TZ) — retrying in 30s" >&2
        sleep 30
    fi
    sleep 5
done
