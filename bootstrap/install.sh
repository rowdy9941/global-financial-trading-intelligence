#!/usr/bin/env bash
# A0 baseline doc cites this as the tier-A/B/C installer (§2, §7 G-INSTALL).
# Refuses unsupported configs loudly. Idempotent: safe to re-run.
#
# Usage: bootstrap/install.sh [--verify] [--tier=A|B|C]
#   --verify    run the W1 + pilot gate suites too (slow, needs a live stack)
#   --tier=X    assert the host tier (A0 §2) instead of merely detecting it;
#               a mismatch is refused rather than silently ignored
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$REPO/compose/docker-compose.yml"
VERIFY=false
WANT_TIER=""

while [ $# -gt 0 ]; do
    case "$1" in
        --verify)  VERIFY=true ;;
        --tier=*)  WANT_TIER="${1#--tier=}" ;;
        --tier)    echo "install.sh: use --tier=A|B|C"; exit 2 ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *)         echo "install.sh: unknown argument '$1' (see --help)"; exit 2 ;;
    esac
    shift
done

# A0 §2 tiers. Detection is a fact about this host; --tier is an assertion about it.
detect_tier() {
    case "$(uname -s)" in
        Linux)
            if grep -qi microsoft /proc/version 2>/dev/null; then echo C; else echo A; fi ;;
        Darwin) echo B ;;
        MINGW*|MSYS*|CYGWIN*) echo C ;;
        *) echo "?" ;;
    esac
}

TIER="$(detect_tier)"
if [ "$TIER" = "?" ]; then
    echo "install.sh: unsupported host '$(uname -s)' — A0 §2 covers Linux (A), macOS (B), Windows via WSL2 (C)."
    exit 2
fi
if [ -n "$WANT_TIER" ] && [ "$WANT_TIER" != "$TIER" ]; then
    echo "install.sh: --tier=$WANT_TIER asserted, but this host detects as tier $TIER — refusing."
    exit 2
fi

need() {
    command -v "$1" >/dev/null 2>&1 \
        && echo "  ok   $1 $("$1" --version 2>/dev/null | head -1)" \
        || { echo "MISSING prerequisite: $1 — install it, then re-run."; exit 2; }
}

echo "[1/6] prerequisite probe (tier $TIER)"
need docker
need cargo
need uv

echo "[2/6] services (postgres/questdb/nats)"
[ -f "$REPO/compose/.env" ] \
    || { echo "install.sh: compose/.env missing — cp compose/.env.example compose/.env"; exit 2; }
docker compose -f "$COMPOSE_FILE" up -d --wait

echo "[3/6] python lanes (uv sync)"
( cd "$REPO/python/train" && uv sync )

echo "[4/6] rust workspace build"
( cd "$REPO/rust" && cargo build --release )

echo "[5/6] migrations (001-006, idempotent)"
for f in 001_d3_d4_init 002_w0b_approval_flow 003_b1_data_plane \
         004_w0c_llm_tasks 005_w1_artifacts 006_d3_intraday_metadata; do
    cat "$REPO/compose/sql/$f.sql" | \
        docker compose -f "$COMPOSE_FILE" exec -T postgres \
        psql -U gfti_app -d gfti -v ON_ERROR_STOP=1 >/dev/null
done

echo "[6/6] gfti entry point"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
mkdir -p "$BIN"
chmod +x "$REPO/bootstrap/gfti" 2>/dev/null || true
# A symlink keeps `gfti` pointed at this checkout, so a git pull updates the CLI.
if ln -sf "$REPO/bootstrap/gfti" "$BIN/gfti" 2>/dev/null; then
    case ":$PATH:" in
        *":$BIN:"*) echo "  gfti -> $BIN/gfti" ;;
        *) echo "  gfti -> $BIN/gfti  (not on PATH: export PATH=\"$BIN:\$PATH\")" ;;
    esac
else
    echo "  symlink unavailable here; call $REPO/bootstrap/gfti directly"
fi

if $VERIFY; then
    echo "[--verify] W1 gates (WF + ARTIFACT + REPLAY)"
    ( cd "$REPO/python/train" && uv run python3 "$REPO/python/gates/w1_gates.py" ALL ) \
        || { echo "INSTALL VERIFY FAILED — see gate output above"; exit 1; }
    echo "[--verify] G-PILOT-E2E (P1/P2/P3 fixture spine)"
    ( cd "$REPO/python/train" && uv run python3 "$REPO/python/gates/pilot_e2e.py" ALL ) \
        || { echo "INSTALL VERIFY FAILED (pilot E2E) — see gate output above"; exit 1; }
    # Real-data arm: only where a pilot has BOTH a verified D3 row and the local
    # archive (d0/ is not tracked in git, so a fresh checkout legitimately skips
    # this). It is a separate claim from the fixture arm and is labelled as such.
    if [ -f "$REPO/d0/hyperliquid/BTC-USDC/1m.parquet" ]; then
        echo "[--verify] G-PILOT-E2E real-data arm (P1, registered 1m bars)"
        ( cd "$REPO/python/train" && uv run python3 "$REPO/python/gates/pilot_e2e.py" --real P1 ) \
            || { echo "INSTALL VERIFY FAILED (P1 real-data arm) — see gate output above"; exit 1; }
    else
        echo "[--verify] G-PILOT-E2E real-data arm SKIPPED (no d0/hyperliquid/BTC-USDC/1m.parquet)"
    fi
fi

echo "install: OK ($REPO)"
