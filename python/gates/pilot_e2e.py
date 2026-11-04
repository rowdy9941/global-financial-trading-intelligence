#!/usr/bin/env python3
"""G-PILOT-E2E — fixture end-to-end for the three frozen pilots (A0 §7 gate 7).

What this proves per pilot, on the LIVE binaries (not mocks):

    feed -> rail -> cell (E1..E6) -> E7 -> signed D4 fills -> ledger_recon

twice over the identical feed, with identical results both passes
(deterministic spine). A second, sequential pass guards against the classic
"works once because state leaked" failure.

Data honesty (A0 §4/§7): two arms, never mixed up.

  * **fixture arm** (default) runs on labelled synthetic feeds. It makes NO claim
    about venue data, registers nothing, and prints `data_kind:
    "fixture-synthetic"` / `real_registered_data: false`.
  * **real-data arm** (`--real P1`) feeds the REGISTERED `hyperliquid/BTC-USDC/1m`
    bars — real venue closes and real open times — and refuses to run unless the
    D3 row is `verified=true`. It prints `data_kind: "registered-1m"` and the
    dataset key, so the two arms can never be confused in a report.

P1 identity stays BTC-USDC-PERP; no feed here is ever relabelled as BTC-USD or
Binance BTCUSDT. P2/P3 have no real-data arm because neither has a verified D3 row
(P2 needs owner broker credentials, P3's feed identity is not frozen).

Usage:
  uv run --project python/train python3 python/gates/pilot_e2e.py [P1|P2|P3|ALL]
  uv run --project python/train python3 python/gates/pilot_e2e.py --real P1
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

import w0b_gates as w0b
from _env import scratch_dir

SCRATCH = scratch_dir()

PILOTS = {
    "P1": {
        "symbol": "BTCUSDC",
        "subject": "market.spot.BTCUSDC",
        "identity": "Hyperliquid BTC-USDC-PERP (pilot 1, quote USDC)",
    },
    "P2": {
        "symbol": "NIFTY",
        "subject": "market.spot.NIFTY",
        "identity": "NSE NIFTY F&O option chain (pilot 2, quote INR)",
    },
    "P3": {
        "symbol": "GOLD",
        "subject": "market.spot.GOLD",
        "identity": "Gold (pilot 3 — venue/feed still pending, identity not frozen)",
    },
}

TICKS = 220
DECIDE_EVERY = 25
REAL_TICKS = 240  # bounded, but long enough for the skeleton rule to alternate


def fixture_closes() -> list[float]:
    """Deterministic four-block ramp: the skeleton rule must alternate
    Buy/Sell on the rising/falling blocks, so the gate has a known-shape
    signal rather than a random walk whose intent count could be zero."""
    closes: list[float] = []
    px = 100.0
    for block in range(4):
        step = 0.5 if block % 2 == 0 else -0.5
        for _ in range(TICKS // 4):
            px = round(px + step, 2)
            closes.append(px)
    if not all(c > 0 for c in closes):
        raise RuntimeError("fixture produced a non-positive price")
    return closes


DATASETS = {"P1": "hyperliquid/BTC-USDC/1m"}


def load_registered_1m(pid: str, limit: int) -> tuple[list[float], dict]:
    """Real closes + open times from a REGISTERED dataset, gated on D3 `verified`.

    Returns evenly spaced (by row) closes from the tail of the series so a run
    stays bounded. Prices and timestamps are the venue's; nothing is rescaled,
    smoothed or synthesised — a real-data arm that fudges its input would prove
    nothing about the spine.
    """
    key = DATASETS[pid]
    row = psql(
        f"SELECT verified, rows FROM d3.datasets WHERE dataset_key = '{key}';"
    ).strip()
    if not row:
        raise RuntimeError(f"{key}: no D3 row — refusing a real-data arm")
    parts = [x.strip() for x in row.split("|")]
    verified = parts[0].lower() in ("t", "true")
    if not verified:
        raise RuntimeError(f"{key}: D3 row is not verified — refusing a real-data arm")

    import pyarrow.parquet as pq

    path = REPO / "d0" / "hyperliquid" / "BTC-USDC" / "1m.parquet"
    if not path.exists():
        raise RuntimeError(f"missing local dataset {path} (d0/ is not tracked in git)")
    table = pq.read_table(path, columns=["open_ms", "close"])
    opens = table.column("open_ms").to_pylist()
    closes = [float(x) for x in table.column("close").to_pylist()]
    if len(closes) < limit:
        raise RuntimeError(f"{path}: only {len(closes)} bars, need {limit}")
    opens, closes = opens[-limit:], closes[-limit:]
    if not all(c > 0 for c in closes):
        raise RuntimeError("registered series contains a non-positive close")
    return closes, {
        "opens_ms": opens,
        "dataset_key": key,
        "rows_in_dataset": len(table),
        "first_open_ms": opens[0],
        "last_open_ms": opens[-1],
        "d3_verified": True,
    }


def psql(sql: str) -> str:
    """Read path over the compose Postgres (same flags as w0b.psql_exec:
    -A -t unaligned/tuples-only so counts come back as one bare line)."""
    r = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "gfti_app",
            "-d",
            "gfti",
            "-A",
            "-t",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(w0b.COMPOSE),
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()}")
    return r.stdout


def _reap() -> None:
    # a leaked e7/cell/rail from a crashed run consumes this run's intents
    for stray in ("e7", "cell", "rail"):
        subprocess.run(
            ["pkill", "-f", f"target/release/{stray}"],
            capture_output=True,
            check=False,
        )


def run_pilot(pid: str, closes: list[float], real: dict | None = None) -> dict:
    p = PILOTS[pid]
    symbol, subject = p["symbol"], p["subject"]
    e, _secret = w0b.owner_env()
    e = dict(
        e,
        RAIL_NATS_URL="nats://127.0.0.1:4222",
        KILL_REFRESH_MS="200",
        CELL_TICKS=str(len(closes)),
        CELL_DECIDE_EVERY=str(DECIDE_EVERY),
        CELL_SUBJECT=subject,
    )

    w0b.run(w0b.BIN / "ledger_init", str(w0b.COMPOSE / "sql" / "001_d3_d4_init.sql"), env=e)
    _reap()
    w0b.psql_exec("TRUNCATE d4.fills; TRUNCATE d4.pnl_recon;")
    w0b.run(w0b.BIN / "ledger_kill", "resolve", "L4", f"{pid}-fixture-open", env=e)

    feed = SCRATCH / f"pilot_{pid}_feed.tsv"
    if real is not None:
        # real venue open times (1m spacing), so the rail sees the true sequence
        stamps = [ms * 1_000_000 for ms in real["opens_ms"]]
    else:
        base_ts = 1_760_000_000 * 1_000_000_000
        stamps = [base_ts + i * 86_400 * 1_000_000_000 for i in range(len(closes))]
    with open(feed, "w") as fh:
        fh.writelines(
            f"{symbol} {ts} {px} public\n" for ts, px in zip(stamps, closes, strict=True)
        )

    children: list[subprocess.Popen] = []

    def _spawn(cmd: list, **kw) -> subprocess.Popen:
        child = subprocess.Popen(cmd, **kw)
        children.append(child)
        return child

    def _cell_pass(log: Path) -> None:
        with open(log, "w") as fh:
            cell = _spawn(
                [str(w0b.BIN / "cell")],
                env=e,
                stdout=fh,
                stderr=subprocess.STDOUT,
                cwd=str(REPO),
            )
            time.sleep(1.2)
            with open(feed) as feed_handle:
                subprocess.run(
                    [str(w0b.BIN / "rail")],
                    stdin=feed_handle,
                    capture_output=True,
                    timeout=180,
                    cwd=str(REPO),
                    check=False,
                )
            cell.wait(timeout=90)

    def _summary(log: Path) -> dict:
        lines = [
            x
            for x in log.read_text().splitlines()
            if x.startswith("LEDGER-CELL-SUMMARY")
        ]
        return json.loads(lines[-1].split(" ", 1)[1]) if lines else {}

    try:
        # pass 1 — calibrate the exact intent count (deterministic decisions)
        _cell_pass(SCRATCH / f"pilot_{pid}_cell1.log")
        s1 = _summary(SCRATCH / f"pilot_{pid}_cell1.log")
        expected = int(s1.get("intents_published", 0) or 0)
        if expected < 1:
            raise RuntimeError(f"{pid}: fixture produced zero intents — gate cannot verify routing")

        w0b.psql_exec("TRUNCATE d4.fills; TRUNCATE d4.pnl_recon;")
        # A-1: E7 is resident; no "GO" handshake exists any more.
        with open(SCRATCH / f"pilot_{pid}_e7.log", "w") as e7log:
            p_e7 = _spawn(
                [str(w0b.BIN / "e7")],
                env=dict(e, E7_EXPECT=str(expected), E7_SUBJECT="order.spot.>"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=e7log,
                cwd=str(REPO),
            )
            time.sleep(1.2)  # E7 subscribed before any new intent is published
            _cell_pass(SCRATCH / f"pilot_{pid}_cell2.log")
            p_e7.wait(timeout=120)
        s2 = _summary(SCRATCH / f"pilot_{pid}_cell2.log")
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    e7_txt = (SCRATCH / f"pilot_{pid}_e7.log").read_text()
    done = [x for x in e7_txt.splitlines() if x.startswith("LEDGER-E7-DONE")]
    e7j = json.loads(done[-1].split(" ", 1)[1]) if done else {}

    fills_raw = psql(
        f"SELECT count(*) FROM d4.fills WHERE symbol = '{symbol}';"
    ).strip()
    fill_rows = [x.strip() for x in fills_raw.splitlines() if x.strip()]
    fills = int(fill_rows[-1]) if fill_rows else 0

    recon_out = w0b.run(w0b.BIN / "ledger_recon", env=e)
    rline = [x for x in recon_out.splitlines() if x.startswith("LEDGER-RECON ")]
    rj = json.loads(rline[-1].split(" ", 1)[1]) if rline else {}

    checks = {
        "decisions_gt_zero": int(s2.get("decisions", 0) or 0) > 0,
        "e12_within_budget": (s2.get("e12_p99", 10**12) or 10**12) < 1_000_000,
        "intents_gt_zero": expected > 0,
        "fills_equal_intents": fills == expected,
        "e7_saw_all": e7j.get("fills") == expected,
        "bad_sigs_zero": rj.get("bad_sigs") == 0,
        # determinism: identical feed, identical decision shape in both passes
        "deterministic_passes": (
            s1.get("decisions") == s2.get("decisions")
            and s1.get("allows") == s2.get("allows")
            and s1.get("intents_published") == s2.get("intents_published")
        ),
    }
    return {
        "pilot": pid,
        "symbol": symbol,
        "identity": p["identity"],
        "data_kind": "registered-1m" if real else "fixture-synthetic",
        "real_registered_data": real is not None,
        "dataset_key": real["dataset_key"] if real else None,
        "dataset_rows": real["rows_in_dataset"] if real else None,
        "feed_span_ms": (
            [real["first_open_ms"], real["last_open_ms"]] if real else None
        ),
        "ticks": len(closes),
        "decisions": s2.get("decisions"),
        "allows": s2.get("allows"),
        "intents": expected,
        "d4_fills": fills,
        "e7_fills": e7j.get("fills"),
        "bad_sigs": rj.get("bad_sigs"),
        "e12_p99_ns": s2.get("e12_p99"),
        "budget_ns": 1_000_000,
        "checks": checks,
        "ok": all(checks.values()),
    }


def main() -> None:
    argv = [a for a in sys.argv[1:] if a != "--real"]
    real_arm = "--real" in sys.argv[1:]
    which = argv[0].upper() if argv else "ALL"
    todo = list(PILOTS) if which == "ALL" else [which]
    if not all(pid in PILOTS for pid in todo):
        print(f"unknown pilot selector {which!r}; use P1|P2|P3|ALL")
        raise SystemExit(2)
    if real_arm:
        unsupported = [pid for pid in todo if pid not in DATASETS]
        if unsupported:
            print(
                f"no real-data arm for {unsupported}: P2 needs owner broker "
                "credentials, P3's feed identity is not frozen"
            )
            raise SystemExit(2)

    closes = fixture_closes()
    results = []
    for pid in todo:
        try:
            real = None
            if real_arm:
                closes, real = load_registered_1m(pid, REAL_TICKS)
            res = run_pilot(pid, closes, real=real)
        except Exception as exc:  # noqa: BLE001 — a gate reports, never crashes silently  # gate must report, never crash silently
            res = {"pilot": pid, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        results.append(res)
        print("PILOT-E2E " + json.dumps(res, sort_keys=True), flush=True)

    ok = all(r.get("ok") for r in results)
    print(
        "PILOT-E2E-SUITE "
        + json.dumps(
            {
                "requested": which,
                "pilots": [r.get("pilot") for r in results],
                "failed": [r.get("pilot") for r in results if not r.get("ok")],
                "data_kind": "registered-1m" if real_arm else "fixture-synthetic",
                "ok": ok,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
