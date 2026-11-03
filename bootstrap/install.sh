#!/usr/bin/env bash
# A0 baseline doc cites this as the tier-A/B/C installer (§2, §7 G-INSTALL).
# Refuses unsupported configs loudly. Idempotent: safe to re-run.
# Usage: bootstrap/install.sh [--verify]   (--verify = run the W1 gate suite too)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERIFY=false
[ "${1:-}" = "--verify" ] && VERIFY=true

need() {
    command -v "$1" >/dev/null 2>&1 \
        && echo "  ok   $1 $("$1" --version 2>/dev/null | head -1)n" \
        || { echo "MISSING prerequisite: $1 — install it, then re-run."; exit 2; }
}

echo "[1/5] prerequisite probe (tier check)"
need docker
need cargo
need uv

echo "[2/5] services (postgres/questdb/nats)"
[ -f "$REPO/.env" ] || { echo "NOTE compose/.env missing — copy .env.example in compose/"; }
docker compose -f "$REPO/compose/docker-compose.yml" up -d

echo "[3/5] python lanes (uv sync)"
( cd "$REPO/python/train" && uv sync )

echo "[4/5] rust workspace build"
( cd "$REPO/rust" && cargo build --release )

echo "[5/5] migrations (001-006, idempotent)"
for f in 001_d3_d4_init 002_w0b_approval_flow 003_b1_data_plane \
         004_w0c_llm_tasks 005_w1_artifacts 006_d3_intraday_metadata; do
    cat "$REPO/compose/sql/$f.sql" | \
        docker compose -f "$REPO/compose/docker-compose.yml" exec -T postgres \
        psql -U gfti_app -d gfti -v ON_ERROR_STOP=1 >/dev/null
done

if $VERIFY; then
    echo "[--verify] W1 gates (WF + ARTIFACT + REPLAY)"
    ( cd "$REPO/python/train" && uv run python3 "$REPO/python/gates/w1_gates.py" ALL ) \
        || { echo "INSTALL VERIFY FAILED — see gate output above"; exit 1; }
    echo "[--verify] G-PILOT-E2E (P1/P2/P3 fixture spine)"
    ( cd "$REPO/python/train" && uv run python3 "$REPO/python/gates/pilot_e2e.py" ALL ) \
        || { echo "INSTALL VERIFY FAILED (pilot E2E) — see gate output above"; exit 1; }
fi

echo "install: OK ($REPO)"
