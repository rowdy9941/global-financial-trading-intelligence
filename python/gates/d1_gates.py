#!/usr/bin/env python3
"""W0a pin 2 — the five D1 (QuestDB) store gates (FULL-SYSTEM-BLUEPRINT §13).

Ingest = production `rail::ilp` path via d1_ingest (exact-byte unit tested).
Checks = QuestDB REST (/exec). Each gate prints one verbatim JSON line; the
gate doc pastes them unchanged.

  A burst        — 500k-row single-socket ingest, all rows counted server-side
  B determinism  — same feed twice → byte-comparable dataset equality
  C pre-warm     — after restart: first-query (cold) ms vs warm p50/p99
  D restore      — snapshot volume, append markers, restore, verify reversal
  E 10x restart  — restart x10, after each: healthy + invariant row count
  F live (A-9)   — the declared `ticks` table, written by the shipped rail

Usage: python3 python/gates/d1_gates.py A|B|C|D|E|F|ALL
"""

import calendar
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
COMPOSE = REPO / "compose"
from _env import config_env, release_dir, scratch_dir

INGEST_BIN = release_dir(REPO) / "d1_ingest"
SCRATCH = scratch_dir()
CFG_ENV = config_env(REPO)
REST = f"http://127.0.0.1:{CFG_ENV.get('QUESTDB_HTTP_PORT', '9000')}/exec?query="


def q(sql):
    """Run a query against the REST endpoint; return full JSON."""
    url = REST + urllib.parse.quote_plus(sql)
    last = None
    for _ in range(60):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return json.load(r)
        except Exception as e:  # server restarting / table committing
            last = e
            time.sleep(0.5)
    raise RuntimeError(f"REST query failed after retries: {sql}: {last}")


def qd(sql):
    return q(sql).get("dataset", [])


def stable_count(table):
    """Row count until it stops changing (ILP commit is async)."""
    prev, stable = -1, 0
    while stable < 3:
        c = qd(f"select count() from {table}")
        cur = c[0][0] if c else 0
        if cur == prev:
            stable += 1
        else:
            stable, prev = 0, cur
        time.sleep(0.5)
    return prev


def ingest(n, table):
    t0 = time.time()
    out = subprocess.run(
        [str(INGEST_BIN), str(n), table],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if out.returncode != 0:
        raise RuntimeError(f"d1_ingest failed: {out.stderr.strip()}")
    return json.loads(out.stdout.strip().splitlines()[-1]), time.time() - t0


def ensure_fresh(table):
    q(f"DROP TABLE IF EXISTS {table}")
    time.sleep(0.5)


def compose_wait_healthy():
    for _ in range(60):
        r = subprocess.run(
            [
                "docker",
                "inspect",
                "gfti-questdb-1",
                "--format",
                "{{.State.Health.Status}}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.stdout.strip() == "healthy":
            return
        time.sleep(1)
    raise RuntimeError("questdb never reached healthy")


def compose_restart():
    """One `restart`, not stop-then-start.

    A-4: compose interpolates `${VAR:?}`, so the call must carry the file env.
    Also: `stop` followed immediately by `start` is racy — measured 2026-11-05 the
    `start` silently failed (check=False) and every later query hit "connection
    refused" with no hint as to why. `restart` is a single primitive and its exit
    status is now checked.
    """
    subprocess.run(
        ["docker", "compose", "restart", "questdb"],
        capture_output=True,
        cwd=str(COMPOSE),
        timeout=180,
        env=CFG_ENV,
        check=True,
    )
    compose_wait_healthy()


def gate_a():
    """Burst: 500k rows over one ILP socket; every row must land."""
    n = 500_000
    tbl = "gate_a_burst"
    ensure_fresh(tbl)
    meta, wall = ingest(n, tbl)
    got = stable_count(tbl)
    ok = got == n
    print(
        "GATE-A-BURST "
        + json.dumps(
            {
                "n_sent": n,
                "rows_server": got,
                "ok": ok,
                "ingest_wall_s": round(wall, 2),
                "client_ns_per_row": meta["ns_per_row"],
                "rows_per_sec_eq": round(n / wall) if wall > 0 else 0,
            }
        )
    )
    return ok


def gate_b():
    """Determinism: identical feeds → identical server datasets."""
    n = 10_000
    tables = ("gate_b_det1", "gate_b_det2")
    for tbl in tables:
        ensure_fresh(tbl)
        ingest(n, tbl)
        stable_count(tbl)
    cols = "ts_nanos, arrival_nanos, price, symbol"
    a = qd(f"select {cols} from {tables[0]} order by ts_nanos")
    b = qd(f"select {cols} from {tables[1]} order by ts_nanos")
    ok = a == b and len(a) == n
    print(
        "GATE-B-DETERMINISM "
        + json.dumps(
            {
                "rows": n,
                "rows_tab1": len(a),
                "rows_tab2": len(b),
                "datasets_identical": a == b,
                "ok": ok,
            }
        )
    )
    return ok


def gate_c():
    """Pre-warm latency: cold first query after restart vs warm queries."""
    compose_restart()
    compose_wait_healthy()
    tbl = "gate_a_burst"
    t0 = time.perf_counter()
    cold = qd(f"select count(), min(price), max(price) from {tbl}")
    cold_ms = (time.perf_counter() - t0) * 1000
    lats = []
    for _ in range(50):
        t1 = time.perf_counter()
        qd(f"select count(), min(price), max(price) from {tbl}")
        lats.append((time.perf_counter() - t1) * 1000)
    lats.sort()
    warm_p99 = lats[int(0.99 * len(lats))]
    ok = cold_ms < 3000 and warm_p99 < 500
    print(
        "GATE-C-PREWARM "
        + json.dumps(
            {
                "cold_first_query_ms": round(cold_ms, 1),
                "warm_p50_ms": round(lats[len(lats) // 2], 1),
                "warm_p99_ms": round(warm_p99, 1),
                "cold_result_rows": cold[0][0] if cold else None,
                "ok": ok,
            }
        )
    )
    return ok


def _qdb(verb: str) -> None:
    """Stop/start the store with the rendered compose env (A-4) and CHECK the result.

    An un-enved call fails interpolation silently; and a failed drill must never
    leave the store down — measured 2026-11-05, one gate failure turned into
    "connection refused" for every gate that ran after it.
    """
    subprocess.run(
        ["docker", "compose", verb, "questdb"],
        capture_output=True,
        cwd=str(COMPOSE),
        timeout=120,
        env=CFG_ENV,
        check=True,
    )
    if verb == "start":
        compose_wait_healthy()


def _qdb_container(argv: list[str], stdin=None, stdout=None) -> None:
    """Run a command against the store's volume from a throwaway container.

    The volume root IS the server's data directory (it mounts at /var/lib/questdb),
    so archive members are relative to `/data`. A stopped container cannot be
    `docker exec`'d, and archiving a live one yields a torn copy.
    """
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            *(["-i"] if stdin is not None else []),
            "-v",
            "gfti_qdbdata:/data",
            "alpine",
            *argv,
        ],
        stdin=stdin,
        stdout=stdout,
        capture_output=True if stdout is None else False,
        timeout=300,
        check=True,
    )


def gate_d():
    """Restore drill: 100 rows → consistent backup → +5 markers → restore → 100."""
    tbl = "gate_d_restore"
    ensure_fresh(tbl)
    ingest(100, tbl)
    before = stable_count(tbl)
    bak = SCRATCH / "qdb-gate-d.tgz"
    bak.parent.mkdir(exist_ok=True)

    _qdb("stop")
    try:
        with open(bak, "wb") as f:
            _qdb_container(["tar", "czf", "-", "-C", "/data", "."], stdout=f)
    finally:
        _qdb("start")
    size = bak.stat().st_size

    ingest(5, tbl)  # post-backup markers must vanish on restore
    mid = stable_count(tbl)

    _qdb("stop")
    try:
        _qdb_container(["sh", "-c", "rm -rf /data/* /data/.[!.]* 2>/dev/null; true"])
        with open(bak, "rb") as f:
            _qdb_container(["tar", "xzf", "-", "-C", "/data"], stdin=f)
    finally:
        _qdb("start")
    after = stable_count(tbl)
    ok = before == after and mid == before + 5 and after == 100
    print(
        "GATE-D-RESTORE "
        + json.dumps(
            {
                "rows_backup": before,
                "rows_after_marker_ingest": mid,
                "rows_after_restore": after,
                "backup_bytes": size,
                "markers_reverted": mid == before + 5 and after == before,
                "ok": ok,
            }
        )
    )
    return ok


def gate_e():
    """10x restart: health + row-count invariant every cycle."""
    tbl = "gate_e_persistent"
    ensure_fresh(tbl)
    ingest(200, tbl)
    expected = stable_count(tbl)
    per_cycle = []
    all_ok = True
    for _ in range(10):
        t0 = time.time()
        compose_restart()
        c = stable_count(tbl)
        cycle_ok = c == expected
        all_ok &= cycle_ok
        per_cycle.append(round((time.time() - t0) * 1000))
    head = qd(f"select ts_nanos, price from {tbl} order by ts_nanos limit 5")
    ok = all_ok and len(head) == 5 and expected == 200
    print(
        "GATE-E-10XRESTART "
        + json.dumps(
            {
                "restarts": 10,
                "invariant_rows": expected,
                "cycles_ms": per_cycle,
                "all_cycles_ok": all_ok,
                "head_rows": head,
                "ok": ok,
            }
        )
    )
    return ok


def gate_f():
    """A-9 LIVE: the *declared* store, written by the *production* rail.

    Cases A–E prove QuestDB can be written and survive; they say nothing about
    whether the live table exists, has the shape the repo declares, or receives a
    real tick. This case drives the shipped binary with the shipped sink:

    1. `d1.apply` twice — the declaration converges, and the second run must not
       recreate anything (a re-run that "creates" again means the check is fake).
    2. `rail` with `RAIL_D1_URL` set, a fresh row block → its own receipt says how
       many rows it stored, and the server must agree (count delta, not a total:
       the table is shared with every earlier run).
    3. the designated timestamp `ts` must be the row's own time. This is the pin
       that catches the framing bug A-9 found: the ILP wire unit is nanoseconds, so
       a micros frame stores every row 56 years in the past while `ts_nanos` looks
       perfectly correct.
    """
    sys.path.insert(0, str(REPO / "python" / "gfti"))
    import d1
    import gfti_config

    cfg = gfti_config.load()
    first = d1.apply(cfg)
    second = d1.apply(cfg)
    convergent = second["ok"] and not second["created"] and "ticks" in second["present"]

    sym = "GATE_LIVE"
    rows = 300
    base = time.time_ns()
    feed = SCRATCH / "d1_live_feed.tsv"
    feed.write_text(
        "".join(f"{sym} {base + i * 1_000_000} {100.0 + i / 10} public\n" for i in range(rows))
    )
    before = stable_count_where(sym)

    ilp = CFG_ENV.get("QUESTDB_ILP_PORT", "9009")
    env = dict(
        CFG_ENV,
        RAIL_D1_URL=f"127.0.0.1:{ilp}",
        RAIL_NATS_URL=CFG_ENV.get("NATS_URL_HOST", "nats://127.0.0.1:4222"),
        RAIL_TICK_MS="100",
        MARKET_PREFIX="market.bench.d1live",
    )
    r = subprocess.run(
        [str(release_dir(REPO) / "rail"), "--ingest-file", str(feed)],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        check=False,
    )
    receipt = {}
    for line in r.stdout.splitlines():
        if line.startswith("RAIL-INGEST "):
            receipt = json.loads(line[len("RAIL-INGEST ") :])
    stored = (receipt.get("d1") or {}).get("rows")
    failed = (receipt.get("d1") or {}).get("failed")

    after = stable_count_where(sym)
    delta = after - before
    # one query so the min row is the same row on both sides of the comparison
    got = qd(
        "select min(ts_nanos), min(ts) from ticks "
        f"where symbol='{sym}' and ts_nanos >= {base}"
    )
    ts_ok = False
    skew_ms = None
    if got and got[0][0] is not None:
        want_us = got[0][0] / 1000.0
        wire_us = _epoch_us(got[0][1])
        skew_ms = None if wire_us is None else round((wire_us - want_us) / 1000.0, 3)
        ts_ok = skew_ms is not None and abs(skew_ms) <= 1.0

    status = d1.status(cfg)
    live = status.get("tables", {}).get("ticks", {})
    ok = (
        convergent
        and r.returncode == 0
        and receipt.get("accepted") == rows
        and stored == rows
        and not failed
        and delta == rows
        and ts_ok
        and status.get("ok") is True
        and live.get("designated") == "ts"
        and live.get("rows", 0) >= rows
    )
    print(
        "GATE-F-LIVE "
        + json.dumps(
            {
                "declared_apply_created": first["created"],
                "reapply_created": second["created"],
                "convergent": convergent,
                "rail_rc": r.returncode,
                "accepted": receipt.get("accepted"),
                "d1_rows_reported": stored,
                "d1_rows_failed": failed,
                "rows_delta_server": delta,
                "designated_ts_skew_ms": skew_ms,
                "status_rows": live.get("rows"),
                "declared_designated": live.get("designated"),
                "problems": status.get("problems"),
                "ok": ok,
            }
        )
    )
    return ok


def _epoch_us(text):
    """`2026-11-06T12:00:00.123456Z` → epoch microseconds, or None.

    `strptime(...%f)` parses the fraction into a `struct_time` field, but
    `calendar.timegm` only reads whole seconds — a silent 0.4 s error that a
    1 ms tolerance does not forgive (caught by this gate on its first run).
    """
    try:
        stamp, frac = str(text).rstrip("Z").split(".")
    except (AttributeError, ValueError):
        return None
    try:
        t = time.strptime(stamp, "%Y-%m-%dT%H:%M:%S")
        frac_us = int((frac + "000000")[:6])
    except ValueError:
        return None
    return calendar.timegm(t) * 1_000_000 + frac_us


def stable_count_where(symbol):
    """Row count for one symbol until it stops changing (WAL apply is async)."""
    prev, stable = -1, 0
    while stable < 3:
        c = qd(f"select count() from ticks where symbol='{symbol}'")
        cur = c[0][0] if c else 0
        if cur == prev:
            stable += 1
        else:
            stable, prev = 0, cur
        time.sleep(0.5)
    return prev


GATES = {"A": gate_a, "B": gate_b, "C": gate_c, "D": gate_d, "E": gate_e, "F": gate_f}
if __name__ == "__main__":
    which = sys.argv[1].upper() if len(sys.argv) > 1 else "ALL"
    todo = GATES if which == "ALL" else {which: GATES[which]}
    results = {}
    for name, fn in todo.items():
        try:
            results[name] = fn()
        except Exception as e:
            print(f"GATE-{name}-FAIL {e}")
            results[name] = False
    print("SUMMARY " + json.dumps(results))
    sys.exit(0 if all(results.values()) else 1)
