#!/bin/sh
# A0 §2 tier-A/B/C installer. POSIX sh on purpose: an installer is the one program
# that has to run on a host where nothing is installed yet, and that host may not
# have bash (dash on minimal Debian, ash on Alpine, /bin/sh on macOS). It refuses
# unsupported configurations loudly. Idempotent: safe to re-run.
#
# Usage: bootstrap/install.sh [--verify] [--tier=A|B|C]
#   --verify    run the W1 + pilot gate suites too (slow, needs a live stack)
#   --tier=X    assert the host tier (A0 §2) instead of merely detecting it;
#               a mismatch is refused rather than silently ignored
#
# Windows: tier C means WSL2. Run this inside the WSL2 distro; native Windows is
# refused (use bootstrap/gfti.ps1 for the CLI, and docs/install-tiers.md for why).
set -eu

# Resolve the checkout through any symlink chain. `$0` is not enough: install.sh is
# reachable through `bootstrap/gfti install` and through a symlink in ~/.local/bin.
self=$0
while [ -L "$self" ]; do
    target=$(readlink "$self")
    case $target in
        /*) self=$target ;;
        *)  self=$(dirname "$self")/$target ;;
    esac
done
REPO=$(CDPATH= cd "$(dirname "$self")/.." && pwd)
CLI="$REPO/python/gfti/cli.py"
COMPOSE_FILE="$REPO/compose/docker-compose.yml"
VERIFY=false
WANT_TIER=""

while [ $# -gt 0 ]; do
    case "$1" in
        --verify)  VERIFY=true ;;
        --tier=*)  WANT_TIER="${1#--tier=}" ;;
        --tier)    echo "install.sh: use --tier=A|B|C"; exit 2 ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *)         echo "install.sh: unknown argument '$1' (see --help)"; exit 2 ;;
    esac
    shift
done

# ------------------------------------------------------------------ prereqs
# python is discovered before anything else because the tier is a fact owned by
# `cli.detect_tier()`, not by a second copy of the OS table in this file. Two
# definitions of the tier would be the same defect as two config files.
PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then PY=$cand; break; fi
done
if [ -z "$PY" ]; then
    echo "MISSING prerequisite: python 3.11+ — install it, then re-run."
    exit 2
fi
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "MISSING prerequisite: python 3.11+ (found $("$PY" -V 2>&1)) — needed before anything else."
    exit 2
fi

echo "[1/7] host and prerequisites"
printf '  ok   %-8s %s\n' python "$("$PY" -V 2>&1)"

TIER_JSON=$("$PY" "$CLI" tier --json) || {
    why=$(printf '%s' "$TIER_JSON" | sed -n 's/.*"detail"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/p')
    echo "install.sh: unsupported host — $why"
    echo "install.sh: A0 §2 covers Linux (A), macOS (B), Windows via WSL2 (C). Nothing was installed."
    exit 2
}
TIER=$(printf '%s' "$TIER_JSON" | sed -n 's/.*"tier"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
TIER_DETAIL=$(printf '%s' "$TIER_JSON" | sed -n 's/.*"detail"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/p')
printf '  ok   %-8s tier %s — %s\n' tier "$TIER" "$TIER_DETAIL"

if [ -n "$WANT_TIER" ] && [ "$WANT_TIER" != "$TIER" ]; then
    echo "install.sh: --tier=$WANT_TIER asserted, but this host detects as tier $TIER — refusing."
    exit 2
fi

# Collect every missing prerequisite instead of dying on the first: a host that
# lacks docker and cargo should get one message listing both, not two runs.
MISSING=""
need() { # $1 = command · $2 = what to install
    if command -v "$1" >/dev/null 2>&1; then
        printf '  ok   %-8s %s\n' "$1" "$("$1" --version 2>/dev/null | head -1)"
    else
        printf '  MISS %-8s %s\n' "$1" "$2"
        MISSING="$MISSING $1"
    fi
}
need docker "Docker Engine 24+ with the compose v2 plugin (dev on every tier)"
need cargo  "Rust toolchain 1.80+ (rail / cell build)"
need uv     "uv 0.4+ (python lanes: uv sync)"
if command -v docker >/dev/null 2>&1; then
    if docker compose version >/dev/null 2>&1; then
        printf '  ok   %-8s %s\n' compose "$(docker compose version --short 2>/dev/null)"
    else
        printf '  MISS %-8s %s\n' compose "the docker compose v2 plugin (docker-compose-plugin)"
        MISSING="$MISSING compose"
    fi
fi
if [ -n "$MISSING" ]; then
    echo "install.sh: tier $TIER is not installable — missing:$MISSING"
    echo "install.sh: install the ones marked MISS above, then re-run. Nothing was changed."
    exit 2
fi

echo "[2/7] configuration (configs/gfti.toml → environment)"
# A2 §4 A-4: one file owns every non-secret value, and the installer must hand
# compose the SAME environment `gfti up` does. Evaluating `config --sh` is how
# that stays true without a second copy of any default.
#   GFTI_CONFIG  override the config path (gates use it to pivot one value)
if ! CONFIG_SH=$("$PY" "$CLI" config --sh); then
    echo "install.sh: configs/gfti.toml is invalid — run 'gfti doctor' for the reason"
    exit 2
fi
eval "$CONFIG_SH"
export POSTGRES_PORT POSTGRES_DB POSTGRES_USER NATS_URL CELL_SUBJECT E7_SUBJECT

echo "[3/7] services (postgres/questdb/nats)"
[ -f "$REPO/compose/.env" ] \
    || { echo "install.sh: compose/.env missing — cp compose/.env.example compose/.env"; exit 2; }
docker compose -f "$COMPOSE_FILE" up -d --wait

echo "[4/7] python lanes (uv sync)"
( cd "$REPO/python/train" && uv sync )

echo "[5/7] rust workspace build"
( cd "$REPO/rust" && cargo build --release )

echo "[6/7] migrations (001-006, idempotent)"
# A-4: the psql coordinates come from the config rendered above, not from a copy.
for f in 001_d3_d4_init 002_w0b_approval_flow 003_b1_data_plane \
         004_w0c_llm_tasks 005_w1_artifacts 006_d3_intraday_metadata; do
    cat "$REPO/compose/sql/$f.sql" | \
        docker compose -f "$COMPOSE_FILE" exec -T postgres \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 >/dev/null
done

echo "[7/7] gfti entry point"
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
