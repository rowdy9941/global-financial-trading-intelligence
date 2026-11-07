#!/usr/bin/env python3
"""A-9 item 32 — D6 is a *product*, and these are the properties that make it one.

`gfti research build` is the only writer, it is atomic, it refuses to race itself,
and the file it produces is readable read-only by everything else. Those are four
claims about a file on disk, so each is a case here:

  BUILD     two builds of the same lake produce the same tables and row counts; the
            names are derived from paths (including a directory with `-` and `.` in
            it) and every row in the lineage table matches its source's sha256.
  LOCK      a held lock refuses the second build and names the holder; a *stale*
            lock is taken over loudly instead of wedging the build forever.
  READONLY  the finished file reads under `read_only=True`, its row counts agree
            with the lineage table, a write is refused by DuckDB itself, and no
            temporary file survives the rename.

The gate is stdlib-only on purpose (like every other gate): the two things that
need DuckDB/pyarrow run through `uv run --project python/train`.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
from _env import scratch_dir

SCRATCH = scratch_dir()
TRAIN = REPO / "python" / "train"
BUILDER = TRAIN / "d6_build.py"

# A fixture lake, not the real one: D6's contract is about paths, hashes and
# atomicity, and rebuilding 3.5M real rows inside a gate would cost minutes of CPU
# that FANOUT's p99 pin does not forgive.
MAKE_FIXTURE = r'''
import json, sys
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

root = Path(sys.argv[1])
rows = {
    "hyperliquid/BTC-USDC/1d.parquet": [("close", [100.0, 101.0, 102.5])],
    "nse_fo/NIFTY/1m.parquet": [("close", [1.0, 2.0])],
    "weird-symbol.with.dots/1d.parquet": [("close", [7.0])],
}
made = {}
for rel, cols in rows.items():
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({
        "ts_ns": pa.array([1_780_000_000_000_000_000 + i for i in range(len(cols[0][1]))], pa.int64()),
        cols[0][0]: pa.array(cols[0][1], pa.float64()),
    })
    pq.write_table(table, p)
    made[rel] = len(cols[0][1])
print(json.dumps(made))
'''

PROBE = r'''
import json, sys
import duckdb

path = sys.argv[1]
con = duckdb.connect(path, read_only=True)
tables = [r[0] for r in con.execute(
    "select table_name from information_schema.tables where table_schema='main' "
    "and table_name <> 'd6_datasets' order by 1").fetchall()]
counts = {t: con.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
meta = con.execute(
    "select dataset, table_name, rows, sha256 from d6_datasets order by dataset").fetchall()
write_error = None
try:
    con.execute("create table should_not_exist (x int)")
except Exception as e:
    write_error = type(e).__name__ + ": " + str(e).splitlines()[0][:120]
print(json.dumps({
    "tables": tables, "counts": counts,
    "meta": [{"dataset": m[0], "table": m[1], "rows": m[2], "sha256": m[3]} for m in meta],
    "write_error": write_error,
}))
'''


def uv(py_args, timeout=600):
    return subprocess.run(
        ["uv", "run", "--project", str(TRAIN), "python3", *py_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(TRAIN),
        check=False,
    )


def make_fixture(root: Path) -> dict:
    script = SCRATCH / "d6_make_fixture.py"
    script.write_text(MAKE_FIXTURE)
    root.mkdir(parents=True, exist_ok=True)
    r = uv([str(script), str(root)])
    if r.returncode != 0:
        raise RuntimeError(f"fixture failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def build(d0: Path, out: Path):
    r = uv([str(BUILDER), "--d0", str(d0), "--out", str(out)])
    line = next(
        (x for x in r.stdout.splitlines() if x.startswith("D6-BUILD ")), ""
    )
    payload = json.loads(line[len("D6-BUILD ") :]) if line else {}
    return r, payload


def probe(path: Path) -> dict:
    script = SCRATCH / "d6_probe.py"
    script.write_text(PROBE)
    r = uv([str(script), str(path)])
    if r.returncode != 0:
        raise RuntimeError(f"probe failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gate_build() -> bool:
    d0 = SCRATCH / "d6_lake"
    if d0.exists():
        for p in sorted(d0.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    made = make_fixture(d0)
    out = SCRATCH / "d6_build.duckdb"

    r1, b1 = build(d0, out)
    first = probe(out) if r1.returncode == 0 else {}
    size1 = out.stat().st_size if out.exists() else 0
    r2, b2 = build(d0, out)
    second = probe(out) if r2.returncode == 0 else {}

    expected = {
        "d0_hyperliquid_btc_usdc_1d": 3,
        "d0_nse_fo_nifty_1m": 2,
        "d0_weird_symbol_with_dots_1d": 1,
    }
    names_ok = first.get("tables") == sorted(expected)
    rows_ok = first.get("counts") == expected
    lineage_ok = all(
        m["rows"] == expected.get(m["table"])
        and m["sha256"] == sha(d0 / m["dataset"])
        for m in first.get("meta", [])
    ) and len(first.get("meta", [])) == len(expected)
    deterministic = (
        second.get("counts") == first.get("counts")
        and second.get("tables") == first.get("tables")
        and second.get("meta") == first.get("meta")
        and b2.get("previous_bytes") == size1
    )
    no_tmp = not list(SCRATCH.glob(".d6_build.duckdb.*.tmp"))
    ok = (
        r1.returncode == 0
        and r2.returncode == 0
        and b1.get("datasets") == len(expected)
        and b1.get("rows") == sum(made.values())
        and names_ok
        and rows_ok
        and lineage_ok
        and deterministic
        and no_tmp
    )
    print(
        "D6-BUILD "
        + json.dumps(
            {
                "datasets": b1.get("datasets"),
                "rows": b1.get("rows"),
                "tables": first.get("tables"),
                "counts": first.get("counts"),
                "lineage_sha_verified": lineage_ok,
                "second_build_identical": deterministic,
                "bytes": size1,
                "previous_bytes_on_rebuild": b2.get("previous_bytes"),
                "temp_files_left": len(list(SCRATCH.glob(".d6_build.duckdb.*.tmp"))),
                "ok": ok,
            }
        )
    )
    return ok


def gate_lock() -> bool:
    d0 = SCRATCH / "d6_lake"
    out = SCRATCH / "d6_lock.duckdb"
    lock = out.with_name(out.name + ".lock")

    # 1. a live holder (this process) must refuse the build and name it
    lock.write_text(f"pid={os.getpid()} at={int(time.time())}")
    held_run, held_payload = build(d0, out)
    refused = (
        held_run.returncode != 0
        and not held_payload
        and "another build holds" in (held_run.stderr + held_run.stdout)
    )
    named = str(os.getpid()) in (held_run.stderr + held_run.stdout)
    lock.unlink()

    # 2. a dead holder must be taken over, loudly, and the build completes
    lock.write_text(f"pid=999999 at={int(time.time())}")
    stale_run, stale_payload = build(d0, out)
    took_over = (
        stale_run.returncode == 0
        and stale_payload.get("ok") is True
        and "stale lock" in (stale_run.stderr + stale_run.stdout)
    )
    cleaned = not lock.exists()
    ok = refused and named and took_over and cleaned
    print(
        "D6-LOCK "
        + json.dumps(
            {
                "live_lock_refused": refused,
                "holder_pid_named": named,
                "stale_lock_taken_over": took_over,
                "lock_cleaned": cleaned,
                "ok": ok,
            }
        )
    )
    return ok


def gate_readonly() -> bool:
    out = SCRATCH / "d6_build.duckdb"
    if not out.exists():
        print("D6-READONLY false no build artifact to read")
        return False
    doc = probe(out)
    counts = doc.get("counts") or {}
    meta_rows = sum(m["rows"] for m in doc.get("meta") or [])
    agrees = sum(counts.values()) == meta_rows
    refused = bool(doc.get("write_error"))
    ok = agrees and refused and bool(counts)
    print(
        "D6-READONLY "
        + json.dumps(
            {
                "tables": len(counts),
                "rows": sum(counts.values()),
                "meta_rows": meta_rows,
                "counts_agree_with_lineage": agrees,
                "write_refused_by_engine": refused,
                "write_error": doc.get("write_error"),
                "ok": ok,
            }
        )
    )
    return ok


GATES = {"BUILD": gate_build, "LOCK": gate_lock, "READONLY": gate_readonly}

if __name__ == "__main__":
    which = sys.argv[1].upper() if len(sys.argv) > 1 else "ALL"
    todo = GATES if which == "ALL" else {which: GATES[which]}
    results = {}
    for name, fn in todo.items():
        try:
            results[name] = fn()
        except Exception as e:  # noqa: BLE001 — one case must not sink the suite
            print(f"GATE-{name}-FAIL {e}")
            results[name] = False
    print("SUMMARY " + json.dumps(results))
    sys.exit(0 if all(results.values()) else 1)
