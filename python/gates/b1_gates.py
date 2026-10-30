#!/usr/bin/env python3
"""B1 gates (FULL-SYSTEM-BLUEPRINT §14): capture → verify → registry → the
B-GATE (rows verified · gaps open-but-justified · NO TRAINING before).

All evidence = verbatim command output; the gate doc pastes it unchanged.

  CAPTURE   real collector runs ×2 (BTCUSDT 1d, key-free public source),
            integrity contract (monotonic/no-future/OHLC-sane/fully-tiled),
            determinism (all-but-live-day byte-identical), registry row,
            verified=TRUE through the B-GATE, training-lock negative test.

Usage: python3 python/gates/b1_gates.py [CAPTURE|ALL]
"""
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent
COMPOSE = REPO / "compose"
COLLECT = REPO / "python" / "collect"
SQL003 = COMPOSE / "sql" / "003_b1_data_plane.sql"

# short alias to reuse the w0b harness plumbing where it fits
sys.path.insert(0, str(HERE))
import w0b_gates as w0b  # noqa: E402 — same box, shared compose/_pg_url helpers


def psql(sql: str):
    ok, out = w0b.psql_exec(sql)
    if not ok:
        raise RuntimeError(f"psql failed: {out}")
    return out


def run_collector(out_parquet: Path, summary: Path) -> dict:
    r = subprocess.run(
        ["uv", "run", "binance_daily.py", "--symbol", "BTCUSDT",
         "--interval", "1d", "--out", str(out_parquet),
         "--summary", str(summary)],
        capture_output=True, text=True, timeout=600, cwd=str(COLLECT),
    )
    if r.returncode != 0:
        raise RuntimeError(f"collector rc={r.returncode}: {r.stderr[-400:]}")
    return json.loads(Path(summary).read_text())


def parquet_compare(a: Path, b: Path) -> dict:
    """All-but-last-row identity between two parquet artifacts (pyarrow via uv)."""
    r = subprocess.run(
        ["uv", "run", "python", "-c", f"""
import pyarrow.parquet as pq
a = pq.read_table({str(a)!r}).to_pylist()
b = pq.read_table({str(b)!r}).to_pylist()
same_hist = a[:-1] == b[:-1]
last_changed = sorted(k for k in a[-1] if a[-1][k] != b[-1][k]) if len(a) == len(b) else ["LENGTH-DIFF"]
import json; print(json.dumps({{"rows_a": len(a), "rows_b": len(b),
    "history_identical": same_hist, "last_row_live_fields": last_changed}}))
"""],
        capture_output=True, text=True, timeout=180, cwd=str(COLLECT),
    )
    if r.returncode != 0:
        raise RuntimeError(f"compare rc={r.returncode}: {r.stderr[-300:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def gate_capture():
    """B1 opener: BTCUSDT 1d — real 9y fill, verified + registered + locked."""
    psql(open(SQL003).read())
    t0 = time.time()
    s1 = run_collector(Path("/tmp/opencode/b1_cap_1.parquet"),
                       Path("/tmp/opencode/b1_cap_1.json"))
    s2 = run_collector(Path("/tmp/opencode/b1_cap_2.parquet"),
                       Path("/tmp/opencode/b1_cap_2.json"))
    cmp = parquet_compare(Path("/tmp/opencode/b1_cap_1.parquet"),
                          Path("/tmp/opencode/b1_cap_2.parquet"))
    integ = (s1["integrity"] == "PASS" and s2["integrity"] == "PASS"
             and s1["rows"] >= 3000 and s2["rows"] >= 3000
             and s1["first_day"] <= "2017-08-18"          # BTCUSDT listing date
             and s1["rows"] == s1["calendar_days"]        # fully tiled 1d grid
             and s2["rows"] == s2["calendar_days"])
    det = cmp["history_identical"] is True
    # register run 1 (the artifact committed to D0)
    art = REPO / "data" / "d0" / "binance" / "BTCUSDT" / "1d.parquet"
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_bytes(Path("/tmp/opencode/b1_cap_1.parquet").read_bytes())
    r = subprocess.run(
        ["uv", "run", "register_b1.py", "--summary", "/tmp/opencode/b1_cap_1.json",
         "--d0-path", "data/d0/binance/BTCUSDT/1d.parquet"],
        capture_output=True, text=True, timeout=120, cwd=str(COLLECT),
    )
    reg_ok = r.returncode == 0 and "LEDGER-B1-REGISTER" in r.stdout
    # readback: registry + B-GATE states
    row = psql("SELECT rows, sha256, verified FROM d3.datasets "
               "WHERE dataset_key='binance/BTCUSDT/1d';")
    open_gaps = psql("SELECT count(*) = 0 AS no_open_gaps FROM d3.v_open_gaps "
                     "WHERE dataset_key='binance/BTCUSDT/1d';")
    ready = psql("SELECT count(*) = 1 AS training_ready FROM d3.v_training_ready "
                 "WHERE dataset_key='binance/BTCUSDT/1d';")
    sha_line_ok = s1["artifact"]["sha256"] in row
    reg_ok &= (sha_line_ok and "t" in row and "t" in open_gaps and "t" in ready
               and f"{s1['rows']}" in row)

    # TRAINING-LOCK negative: an open UNJUSTIFIED gap must block training even
    # if someone flips verified=TRUE by hand — v_training_ready enforces it.
    psql("INSERT INTO d3.datasets (dataset_key, source, license_class, granularity,"
         " expected_from, expected_to, d0_path, rows, sha256, verified) VALUES"
         " ('b1-test/unjustified/1d','test','test','1d',now()-interval '1 day',now(),"
         " 'test', 1, 'deadbeef', TRUE);")
    psql("INSERT INTO d3.gaps (dataset_key, window_from, window_to, kind, status)"
         " VALUES ('b1-test/unjustified/1d', now()-interval '1 day', now(),"
         " 'missing', 'open');")
    blocked = psql("SELECT count(*) n FROM d3.v_training_ready "
                   "WHERE dataset_key='b1-test/unjustified/1d';")
    # justified gap → unlock
    psql("UPDATE d3.gaps SET status='justified', justification='gate drill: real "
         "outage justification placeholder' WHERE dataset_key='b1-test/unjustified/1d';")
    unblocked = psql("SELECT count(*) n FROM d3.v_training_ready "
                     "WHERE dataset_key='b1-test/unjustified/1d';")
    lock_ok = ("0" in blocked) and ("1" in unblocked)
    # cleanup the drill rows (D3 registry is mutable by design)
    psql("DELETE FROM d3.gaps WHERE dataset_key='b1-test/unjustified/1d'; "
         "DELETE FROM d3.datasets WHERE dataset_key='b1-test/unjustified/1d';")

    ok = bool(integ and det and reg_ok and lock_ok)
    print("B1-GATE-CAPTURE " + json.dumps({
        "run1": {"rows": s1["rows"], "first_day": s1["first_day"],
                 "last_day": s1["last_day"], "tiled": s1["rows"] == s1["calendar_days"],
                 "sha256": s1["artifact"]["sha256"][:16], "integrity": s1["integrity"]},
        "run2": {"rows": s2["rows"], "sha256": s2["artifact"]["sha256"][:16],
                 "integrity": s2["integrity"]},
        "determinism": cmp, "registry": {"readback": row.replace("\n", " ")[:160],
                                          "sha_matches": sha_line_ok,
                                          "no_open_gaps": "t" in open_gaps,
                                          "training_ready": "t" in ready},
        "training_lock": {"unjustified_blocked": "0" in blocked,
                          "justified_unlocks": "1" in unblocked},
        "wall_seconds": round(time.time() - t0, 1), "ok": ok,
    }))
    return ok


GATES = {"CAPTURE": gate_capture}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "CAPTURE"
    todo = {k: v for k, v in GATES.items() if k == name or name == "ALL"}
    results = {}
    for case, fn in todo.items():
        try:
            results[case] = fn()
        except Exception as e:
            print(f"B1-GATE-{case}-FAIL {e}")
            results[case] = False
    print("SUMMARY " + json.dumps(results))
    try:
        stamp = json.dumps({k: bool(v) for k, v in results.items()})
        w0b.psql_exec(
            "INSERT INTO d3.gate_b1 (gate, verdict, payload) VALUES "
            f"('b1_{name.lower()}_{time.strftime('%Y%m%dT%H%M%S')}', "
            f"'{'pass' if all(results.values()) else 'fail'}',"
            f" $BODY${stamp}$BODY$) ON CONFLICT (gate) DO NOTHING;"
        )
    except Exception as e:  # landing failure must not fake a green gate
        print(f"B1-LAND-FAIL d3.gate_b1: {e}")
    sys.exit(0 if all(results.values()) else 1)
