#!/usr/bin/env python3
"""A-7 gate — the rail is the only writer (invariant R1, defect I1).

Three things must be true, and each is a case because they fail independently:

  INGEST   a capture file goes through `rail --ingest-file` and the Parquet that
           lands is byte-identical to a second run, with the rail's own accepted
           count equal to the rows D0 read back. A reject anywhere in the batch
           must be reported, not swallowed.
  REFUSE   `register_b1.py` refuses a summary that carries neither a rail receipt
           nor a declared reason. This is the case that stops a collector from
           registering its own product silently — it is a *negative* test, so a
           regression that re-opens the old path shows up as a red REFUSE.
  DECLARED a declared legacy write is recorded append-only with its reason, and
           `d3.v_r1_violations` (the view `gfti doctor` reads) stays empty. A
           direct write with no declaration must make that view non-empty — proven
           inside a rolled-back transaction, so the gate never pollutes the ledger.

Runs against the live stack (the rail publishes with ack, and the views live in
Postgres). No synthetic green: every assertion is a count read back from the
writer or the database.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COLLECT = REPO / "python" / "collect"
RUST_BIN = REPO / "rust" / "target" / "release"
sys.path.insert(0, str(REPO / "python" / "gfti"))
sys.path.insert(0, str(COLLECT))

import gfti_config

CFG = gfti_config.load()
ENV = gfti_config.process_env(CFG)
RESULTS: dict[str, dict] = {}


def ok(case: str, detail: str, **extra) -> None:
    RESULTS[case] = {"ok": True, "detail": detail, **extra}


def bad(case: str, detail: str, **extra) -> None:
    RESULTS[case] = {"ok": False, "detail": detail, **extra}


def psql(sql: str) -> tuple[int, str]:
    r = subprocess.run(
        [*gfti_config.psql_argv(CFG), "-A", "-t", "-F", "|", "-c", sql],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO / "compose"),
        env=ENV,
        check=False,
    )
    return r.returncode, (r.stdout or "").strip() + (r.stderr or "").strip()


def run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=900, check=False, **kw
    )


def case_ingest(tmp: Path) -> None:
    """row → rail → D0, twice, and the two files must be byte-identical."""
    rows = [f"GATE_TICK {1_780_000_000_000_000_000 + i * 1_000_000} {100.0 + i} public"
            for i in range(200)]
    rows.append("GATE_TICK not-a-timestamp 1.0 public")  # must be rejected, loudly
    src = tmp / "rows.txt"
    src.write_text("\n".join(rows) + "\n")

    outs, receipts = [], []
    for i in (1, 2):
        pq = tmp / f"gate_r1_{i}.parquet"
        # Faithful to the shipped path: rail → d0_dump, exactly as rail_ingest runs it.
        rail = run(
            [str(RUST_BIN / "rail"), "--ingest-file", str(src)],
            env={**os.environ, "MARKET_PREFIX": "market.bench.r1gate"},
        )
        if rail.returncode != 0:
            bad("INGEST", f"rail rc={rail.returncode}: {(rail.stderr or rail.stdout)[-300:]}")
            return
        staged = tmp / f"rail_{i}.out.txt"
        staged.write_text(rail.stdout)
        d0 = run(
            [str(RUST_BIN / "d0_dump"), "--stdin", str(pq)],
            stdin=staged.open("rb"),
        )
        line = next((ln for ln in d0.stdout.splitlines() if ln.startswith("D0 ")), "")
        rec = next((ln for ln in rail.stdout.splitlines() if ln.startswith("RAIL-INGEST ")), "")
        if not line or not rec:
            bad("INGEST", f"missing verdict lines (d0={d0.stdout[:120]!r} rail={rail.stdout[-120:]!r})")
            return
        outs.append(pq)
        receipts.append((json.loads(line[3:]), json.loads(rec[len("RAIL-INGEST ") :])))

    d0a, ra = receipts[0]
    d0b, _ = receipts[1]
    if ra["accepted"] != 200 or ra["rejected"] != 1:
        bad("INGEST", f"rail accounted {ra['accepted']}/{ra['rejected']}, want 200/1")
        return
    if d0a["rows_written"] != ra["accepted"] or d0a["rejected"] != 1:
        bad("INGEST", f"d0 wrote {d0a['rows_written']} rejected {d0a['rejected']}, rail said {ra}")
        return
    if d0a["rows_written"] != d0b["rows_written"]:
        bad("INGEST", "two runs wrote different row counts")
        return
    # Byte-identity is NOT the right assertion here: `arrival_nanos` is the rail's
    # real boundary clock, and two runs happen at different times. What must be
    # identical is the accepted payload; the arrival stamp is supposed to differ.
    payloads = [_payload(p) for p in outs]
    if payloads[0] != payloads[1]:
        bad("INGEST", "two runs of the same rows produced different (symbol, ts, price) payloads")
        return
    if payloads[0][0][1] != 1_780_000_000_000_000_000:
        bad("INGEST", f"first accepted ts is {payloads[0][0][1]}, not the submitted one")
        return
    ok(
        "INGEST",
        f"rail accepted {ra['accepted']} rejected {ra['rejected']}; "
        f"D0 wrote {d0a['rows_written']} rows, payload identical across two runs "
        "(arrival stamp differs by design)",
    )


def _payload(parquet: Path) -> list[tuple]:
    import pyarrow.parquet as pq

    cols = pq.read_table(parquet).to_pylist()
    return [(r["symbol"], r["ts_nanos"], r["price"]) for r in cols]


def case_refuse(tmp: Path) -> None:
    """A summary with no rail block and no reason must be refused, with a reason."""
    summary = {
        "symbol": "GATE-R1",
        "interval": "tick",
        "source": "gate-synthetic",
        "license": "gate",
        "rows": 200,
        "first_day": "2026-11-03",
        "last_day": "2026-11-03",
        "holes": [],
        "artifact": {"path": "d0/gate/r1.parquet", "sha256": "0" * 64, "rows": 200},
    }
    path = tmp / "legacy_summary.json"
    path.write_text(json.dumps(summary))
    r = run(
        ["uv", "run", "register_b1.py", "--summary", str(path),
         "--d0-path", "d0/gate/r1.parquet"],
        cwd=str(COLLECT),
    )
    text = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        bad("REFUSE", "register_b1 accepted a summary with no rail receipt")
        return
    if "rail" not in text:
        bad("REFUSE", f"refused, but the message does not name the rail: {text[-200:]!r}")
        return
    # The declared path must still work (a refusal with no exit is a dead end).
    # volume metadata is required for intraday keys, so supply it; the run must
    # then get as far as the artifact check, which is the next gate, not this one.
    r2 = run(
        ["uv", "run", "register_b1.py", "--summary", str(path),
         "--d0-path", "d0/gate/r1.parquet",
         "--volume-available", "false", "--volume-kind", "none", "--volume-unit", "none",
         "--legacy-writer-reason",
         "gate: declared legacy write exists only to prove the flag is not a dead end"],
        cwd=str(COLLECT),
    )
    text2 = r2.stdout + r2.stderr
    if "did not go through the rail" in text2 or "cannot be" in text2 and "proven" in text2:
        bad("REFUSE", "the declared flag was ignored — the same refusal fired")
        return
    if "d0 path not found" not in text2 and "sha mismatch" not in text2:
        bad("REFUSE", f"declared path failed before reaching the artifact check: {text2.strip()[-220:]!r}")
        return
    ok(
        "REFUSE",
        "refused without a receipt (rc!=0, message names the rail); with a declared "
        "reason it proceeds to the artifact check",
    )


def case_declared() -> None:
    """Two halves: the live view is clean, and an undeclared write makes it dirty."""
    rc, out = psql(
        "SELECT count(*) FROM d3.v_r1_violations;"
        "SELECT count(*) FROM d3.legacy_writes;"
        "SELECT count(*) FROM d3.datasets WHERE writer = 'rail';"
    )
    if rc != 0:
        bad("DECLARED", f"psql failed (migrations applied?): {out[-200:]}")
        return
    parts = [p for p in out.splitlines() if p.strip()]
    if len(parts) < 3:
        bad("DECLARED", f"unexpected psql output: {out!r}")
        return
    violations, declared, rail_rows = (int(p.split("|")[0]) for p in parts[-3:])
    if violations:
        bad("DECLARED", f"{violations} undeclared direct write(s) in d3.v_r1_violations")
        return
    if rail_rows < 1 or declared < 1:
        bad(
            "DECLARED",
            f"no evidence to inspect: rail_rows={rail_rows} declared={declared}",
        )
        return
    # Negative half, rolled back: an undeclared legacy write after the cutoff MUST
    # appear in the view. Without this, "view is empty" could mean "view is broken".
    rc2, out2 = psql(
        "BEGIN;"
        "INSERT INTO d3.datasets (dataset_key, source, license_class, granularity,"
        " expected_from, expected_to, d0_path, rows, sha256, writer)"
        " VALUES ('gate/undeclared/1d','gate','gate','1d', now(), now(),"
        " 'd0/gate/undeclared.parquet', 1, repeat('0',64), 'legacy-collector');"
        "SELECT count(*) FROM d3.v_r1_violations WHERE dataset_key='gate/undeclared/1d';"
        "ROLLBACK;"
    )
    lines2 = [ln.strip() for ln in out2.splitlines() if ln.strip()]
    if rc2 != 0 or "1" not in lines2:
        bad("DECLARED", f"the violation view did not flag an undeclared write: {out2[-200:]!r}")
        return
    ok(
        "DECLARED",
        f"view empty ({rail_rows} rail-written, {declared} declared legacy); "
        "an undeclared write inside a rolled-back txn was flagged",
    )


def case_doctor() -> None:
    """`gfti doctor` is how an operator sees this before a gate does."""
    r = run([sys.executable, str(REPO / "python" / "gfti" / "cli.py"), "doctor", "--json"])
    if r.returncode not in (0, 1):
        bad("DOCTOR", f"doctor rc={r.returncode}: {(r.stderr or r.stdout)[-200:]}")
        return
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        bad("DOCTOR", f"doctor --json is not JSON: {r.stdout[:160]!r}")
        return
    check = next((c for c in doc.get("checks", []) if c["name"] == "rail_writer"), None)
    if check is None:
        bad("DOCTOR", "doctor has no rail_writer check")
        return
    if check["status"] != "ok":
        bad("DOCTOR", f"rail_writer is {check['status']}: {check['detail']}")
        return
    ok("DOCTOR", f"doctor rail_writer ok — {check['detail']}")


def main() -> int:
    if not (RUST_BIN / "rail").is_file():
        print("R1-GATE-FAIL rail binary missing — run cargo build --release", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="r1gate_") as td:
        tmp = Path(td)
        for name, fn in (
            ("INGEST", lambda: case_ingest(tmp)),
            ("REFUSE", lambda: case_refuse(tmp)),
            ("DECLARED", case_declared),
            ("DOCTOR", case_doctor),
        ):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 — a crashed case is a failed case
                bad(name, f"{type(e).__name__}: {e}")
            got = RESULTS.get(name)
            if got is None:
                bad(name, "case produced no verdict")
            v = RESULTS[name]
            mark = "ok  " if v["ok"] else "FAIL"
            print(f"R1-{name} {mark} {v['detail']}")
    _emit()
    return 0 if all(v["ok"] for v in RESULTS.values()) else 1


def _emit() -> None:
    # `SUMMARY {…}` is the aggregator's pass marker (status.gate_runner reads it), and
    # `ok` must be present or a crashed case would read as green.
    payload = {k: v["ok"] for k, v in sorted(RESULTS.items())}
    payload["ok"] = all(payload.values())
    print("SUMMARY " + json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    sys.exit(main())
