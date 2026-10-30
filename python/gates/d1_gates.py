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

Usage: python3 python/gates/d1_gates.py A|B|C|D|E|ALL
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
INGEST_BIN = REPO / "rust" / "target" / "release" / "d1_ingest"
REST = "http://127.0.0.1:9000/exec?query="


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
        capture_output=True, text=True, timeout=600,
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
            ["docker", "inspect", "gfti-questdb-1", "--format", "{{.State.Health.Status}}"],
            capture_output=True, text=True, timeout=30,
        )
        if r.stdout.strip() == "healthy":
            return
        time.sleep(1)
    raise RuntimeError("questdb never reached healthy")


def compose_restart():
    subprocess.run(["docker", "compose", "stop", "questdb"], capture_output=True,
                   cwd=str(COMPOSE), timeout=90)
    subprocess.run(["docker", "compose", "start", "questdb"], capture_output=True,
                   cwd=str(COMPOSE), timeout=90)
    compose_wait_healthy()


def gate_a():
    """Burst: 500k rows over one ILP socket; every row must land."""
    n = 500_000
    tbl = "gate_a_burst"
    ensure_fresh(tbl)
    meta, wall = ingest(n, tbl)
    got = stable_count(tbl)
    ok = got == n
    print("GATE-A-BURST " + json.dumps({
        "n_sent": n, "rows_server": got, "ok": ok,
        "ingest_wall_s": round(wall, 2),
        "client_ns_per_row": meta["ns_per_row"],
        "rows_per_sec_eq": round(n / wall) if wall > 0 else 0,
    }))
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
    print("GATE-B-DETERMINISM " + json.dumps({
        "rows": n, "rows_tab1": len(a), "rows_tab2": len(b),
        "datasets_identical": a == b, "ok": ok,
    }))
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
    print("GATE-C-PREWARM " + json.dumps({
        "cold_first_query_ms": round(cold_ms, 1),
        "warm_p50_ms": round(lats[len(lats) // 2], 1),
        "warm_p99_ms": round(warm_p99, 1),
        "cold_result_rows": cold[0][0] if cold else None,
        "ok": ok,
    }))
    return ok


def gate_d():
    """Restore drill: 100 rows → tar volume → +5 markers → restore → markers gone."""
    tbl = "gate_d_restore"
    ensure_fresh(tbl)
    ingest(100, tbl)
    before = stable_count(tbl)
    bak = Path("/tmp/opencode/qdb-gate-d.tgz")
    bak.parent.mkdir(exist_ok=True)
    with open(bak, "wb") as f:
        subprocess.run(
            ["docker", "exec", "gfti-questdb-1", "tar", "czf", "-", "-C", "/var/lib", "questdb"],
            stdout=f, timeout=300,
        )
    size = bak.stat().st_size
    ingest(5, tbl)  # post-backup markers must vanish on restore
    mid = stable_count(tbl)
    # restore: stop → wipe volume root (true restore, not merge) → extract
    # backup at volume root (tar members carry a questdb/ prefix → strip it) → start
    subprocess.run(["docker", "compose", "stop", "questdb"], capture_output=True,
                   cwd=str(COMPOSE), timeout=90)
    subprocess.run(
        ["docker", "run", "--rm", "-v", "gfti_qdbdata:/data", "alpine",
         "sh", "-c", "rm -rf /data/* && ls /data | wc -l"],
        capture_output=True, timeout=120,
    )
    with open(bak, "rb") as f:
        subprocess.run(
            ["docker", "run", "--rm", "-i", "-v", "gfti_qdbdata:/data", "alpine",
             "tar", "xzf", "-", "--strip-components=1", "-C", "/data"],
            stdin=f, capture_output=True, timeout=300,
        )
    subprocess.run(["docker", "compose", "start", "questdb"], capture_output=True,
                   cwd=str(COMPOSE), timeout=90)
    compose_wait_healthy()
    after = stable_count(tbl)
    ok = (before == after and mid == before + 5 and after == 100)
    print("GATE-D-RESTORE " + json.dumps({
        "rows_backup": before, "rows_after_marker_ingest": mid,
        "rows_after_restore": after, "backup_bytes": size,
        "markers_reverted": mid == before + 5 and after == before,
        "ok": ok,
    }))
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
    print("GATE-E-10XRESTART " + json.dumps({
        "restarts": 10, "invariant_rows": expected,
        "cycles_ms": per_cycle, "all_cycles_ok": all_ok,
        "head_rows": head, "ok": ok,
    }))
    return ok


GATES = {"A": gate_a, "B": gate_b, "C": gate_c, "D": gate_d, "E": gate_e}

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
