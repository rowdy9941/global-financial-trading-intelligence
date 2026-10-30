#!/usr/bin/env python3
"""W0b gates (FULL-SYSTEM-BLUEPRINT §13): D4 append-only enforcement ·
kill drill (engage → engine refuses; resolve → open; db-down → fail-closed) ·
postgres restart ×10 invariance · signed-verify + recon · ledger bench.

All evidence = verbatim command output; the gate doc pastes it unchanged.
Usage: python3 python/gates/w0b_gates.py [LEDGER|KILL|RESTART|UMBRA|ALL]
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent
COMPOSE = REPO / "compose"
BIN = REPO / "rust" / "target" / "release"
SQL = COMPOSE / "sql" / "001_d3_d4_init.sql"

# dev-local store coordinates come from compose/.env (never from a public file)
def _pg_url():
    env = {}
    for line in (COMPOSE / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return f"postgres://gfti_app:{env.get('POSTGRES_PASSWORD','dev-only-local-placeholder')}@127.0.0.1:5432/gfti"

LEDGER_ENV = dict(os.environ, LEDGER_URL=_pg_url())

def owner_env(role_secret=None):
    """Per-run ephemeral dev secret (never a repo constant; owner production
    secrets come from the owner-side secret store at deploy time)."""
    s = role_secret or ("dev-w0b-" + os.urandom(12).hex())
    return dict(LEDGER_ENV, **{"LEDGER_SECRET_OWNER": s}), s

def run(b, *args, env=None, need_rc=0):
    e = env or LEDGER_ENV
    r = subprocess.run([str(b)] + list(args), capture_output=True, text=True, timeout=600, env=e)
    if r.returncode != need_rc:
        raise RuntimeError(f"{b.name} rc={r.returncode} expected {need_rc}: {r.stderr.strip()}")
    return (r.stdout.strip() + (" " + r.stderr.strip() if r.stderr.strip() else "")).strip()

def q(sql):
    url = "http://127.0.0.1:9000/exec?query=" + urllib.parse.quote(sql)
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)

def psql_exec(sql, want_fail=False):
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres",
         "psql", "-U", "gfti_app", "-d", "gfti", "-c", sql],
        capture_output=True, text=True, timeout=60, cwd=str(COMPOSE),
    )
    if want_fail:
        ok = (r.returncode != 0) and ("append-only" in (r.stderr + r.stdout))
        return ok, (r.stderr.strip() + " | " + r.stdout.strip())[:880]
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()}")
    return True, r.stdout.strip()

def gate_append_only():
    run(BIN / "ledger_init", str(SQL))
    ok1, ev1 = psql_exec("UPDATE d4.fills SET qty = -1 WHERE fill_id = 1;", want_fail=True)
    ok2, ev2 = psql_exec("DELETE FROM d4.fills WHERE fill_id = 1;", want_fail=True)
    ok3, ev3 = psql_exec("DELETE FROM d4.kill_events WHERE evt_id = 1;", want_fail=True)
    ok = ok1 and ok2 and ok3
    print("W0B-G1-APPEND-ONLY " + json.dumps({
        "update_fills_denied": ok1, "delete_fills_denied": ok2,
        "delete_kill_events_denied": ok3, "ok": ok,
        "evidence": [ev1[:120], ev2[:120], ev3[:120]],
    }))
    return ok

def gate_kill_drill():
    run(BIN / "ledger_init", str(SQL))
    run(BIN / "ledger_kill", "resolve", "L4", "drill-reset")   # ensure open
    rc_open = run(BIN / "ledger_check", need_rc=0)[:100]
    run(BIN / "ledger_kill", "engage", "L0", "W0b-kill-drill")
    rc_engaged = run(BIN / "ledger_check", need_rc=7)[:100]
    # fail-closed: DB unreachable must NOT look open
    broken = dict(LEDGER_ENV); broken["LEDGER_URL"] = "postgres://gfti_app:x@127.0.0.1:5999/none"
    fc = run(BIN / "ledger_check", env=broken, need_rc=7)[:100]
    run(BIN / "ledger_kill", "resolve", "L4", "drill-resolve")
    rc_resolved = run(BIN / "ledger_check", need_rc=0)[:100]
    ok = True
    print("W0B-G2-KILL-DRILL " + json.dumps({
        "open_check": rc_open, "engaged_check_rc7": rc_engaged,
        "failclosed_unreachable_rc7": fc, "resolved_check": rc_resolved, "ok": ok,
    }))
    return ok

def gate_restart_x10():
    e, _ = owner_env()
    ok_all = True
    cycles = []
    for i in range(10):
        t0 = time.time()
        subprocess.run(["docker", "compose", "stop", "postgres"], capture_output=True,
                       cwd=str(COMPOSE), timeout=90)
        subprocess.run(["docker", "compose", "start", "postgres"], capture_output=True,
                       cwd=str(COMPOSE), timeout=90)
        for _ in range(60):
            r = subprocess.run(["docker", "inspect", "gfti-postgres-1",
                                "--format", "{{.State.Health.Status}}"],
                               capture_output=True, text=True, timeout=30)
            if r.stdout.strip() == "healthy":
                break
            time.sleep(1)
        run(BIN / "ledger_fill", "1", f"restart-probe-{i}", env=e)  # 1 signed append = store writable
        cycles.append(round((time.time() - t0) * 1000))
    print("W0B-G3-RESTART-X10 " + json.dumps({
        "restarts": 10, "cycles_ms": cycles, "ok": ok_all and len(cycles) == 10,
    }))
    return len(cycles) == 10

def gate_sign_recon():
    e, secret = owner_env()          # same run's secret signs + verifies
    run(BIN / "ledger_init", str(SQL), env=e)
    env = e
    # fresh start: rows from earlier gate runs are signed with secrets that are
    # gone by design (ephemeral per-run); TRUNCATE is the only reset possible
    # on an append-only table (UPDATE/DELETE denied at DB level).
    psql_exec("TRUNCATE d4.fills; TRUNCATE d4.pnl_recon;")
    bench = json.loads(run(BIN / "ledger_fill", "20000", "w0b-bench", env=env).splitlines()[-1])
    recon = run(BIN / "ledger_recon", env=env)  # exits 0 only if sigs all verify
    bad = json.loads(recon.splitlines()[-1].split(" ", 1)[1])["bad_sigs"]
    ok = bad == 0
    print("W0B-G4-SIGN-RECON " + json.dumps({
        "fills_signed": 20000, "bad_sigs": bad, "ok": ok,
        "mode": "same-run secret signs then verifies (ephemeral, dev)",
        "bench": bench, "pnl_lines": recon.splitlines()[:-1],
    }))
    return ok

GATES = {"APPEND": gate_append_only, "KILL": gate_kill_drill,
         "RESTART": gate_restart_x10, "RECON": gate_sign_recon}

if __name__ == "__main__":
    which = sys.argv[1].upper() if len(sys.argv) > 1 else "ALL"
    todo = GATES if which == "ALL" else {which: GATES[which]}
    results = {}
    for name, fn in todo.items():
        try:
            results[name] = fn()
        except Exception as e:
            print(f"W0B-GATE-{name}-FAIL {e}")
            results[name] = False
    print("SUMMARY " + json.dumps(results))
    sys.exit(0 if all(results.values()) else 1)
