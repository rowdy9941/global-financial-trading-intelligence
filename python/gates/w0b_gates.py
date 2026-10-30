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

def gate_fanout():
    """W0b pin: R1→D2 fan-out → cell receive ≤ ~2 ms P99 (formal bench).

    20k deterministic rows published through the production `rail` hot path;
    4 consumer cells on the compose NATS: three filtered (one symbol each)
    + one catch-all dashboard cell. Zero loss + zero cross-leak + per-cell p99.
    """
    n_total = 20000
    per = n_total // 4
    specs = [("market.spot.NIFTY", per), ("market.spot.BTC_USDT", per),
             ("market.spot.USDJPY", per), ("market.spot.>", n_total)]
    procs, logs = [], []
    for i, (subj, cnt) in enumerate(specs):
        lg = open(f"/tmp/opencode/w0b_fan_{i}.log", "w")
        logs.append(lg)
        procs.append(subprocess.Popen(
            [str(BIN / "rail_sub"), subj, str(cnt), "nats://127.0.0.1:4222"],
            stdout=subprocess.DEVNULL, stderr=lg, cwd=str(REPO)))
    time.sleep(1.0)  # subscriptions bound before publish
    feed = "/tmp/opencode/w0b_fan_feed.tsv"
    with open(feed, "w") as f:
        for i in range(n_total):
            sym = ["NIFTY", "SENSEX", "USDJPY", "BTC_USDT"][i % 4]
            f.write(f"{sym} {1772000000000000000 + i * 1_000_000_000} "
                    f"{round(100.5 + i * 0.01, 2)} public\n")
    r = subprocess.run([str(BIN / "rail")], stdin=open(feed), capture_output=True,
                       text=True, timeout=180, cwd=str(REPO))
    pub_ok = r.returncode == 0 and f"accepted={n_total}" in r.stderr
    for i, p in enumerate(procs):
        p.wait(timeout=90)
        logs[i].close()
    cells, all_ok = [], pub_ok
    worst_p99 = 0
    for i, (subj, cnt) in enumerate(specs):
        txt = Path(f"/tmp/opencode/w0b_fan_{i}.log").read_text()
        line = [x for x in txt.splitlines() if x.startswith("WIRE-E2E ")]
        if line:
            d = json.loads(line[-1].split(" ", 1)[1])
            cell_ok = d["received"] == cnt and d["p99"] <= 2_000_000
            worst_p99 = max(worst_p99, d["p99"])
        else:
            d = {"received": 0, "p50": None, "p99": None}
            cell_ok = False
        all_ok &= cell_ok
        cells.append({"subject": subj, **d, "expected": cnt, "ok": cell_ok})
    print("W0B-G5-FANOUT " + json.dumps({
        "rows_published": n_total, "pub_accepted": pub_ok,
        "cells": cells, "worst_p99_ns": worst_p99,
        "budget_ns": 2_000_000, "ok": all_ok,
    }))
    return all_ok or False


def gate_watchdog():
    """W0b pin: out-of-band watchdog observes the kill cascade.

    (a) fail-closed: unreachable DB → every tick state=failsafe, strict exit 7
    (b) live drill: engage tick observed after the engage row, open tick again
        after the resolve row — detectable at the given poll interval.
    """
    e, _ = owner_env()
    run(BIN / "ledger_init", str(SQL), env=e)
    # (a) unreachable
    fc_log = "/tmp/opencode/w0b_watchdog_failclosed.log"
    p = subprocess.Popen(
        [str(BIN / "ledger_watchdog"), "--interval-ms", "200",
         "--timeout-ms", "900", "--strict"],
        env={**LEDGER_ENV, "LEDGER_URL": "postgres://gfti_app:x@127.0.0.1:5999/none"},
        stdout=open(fc_log, "w"), stderr=subprocess.STDOUT, cwd=str(REPO))
    rc_fc = p.wait(timeout=20)
    fc_ticks = [x for x in Path(fc_log).read_text().splitlines()
                if x.startswith("LEDGER-WATCHDOG ") and x not in ("LEDGER-WATCHDOG-DONE",)
                and "state" in x]
    fc_ok = (rc_fc == 7 and len(fc_ticks) >= 3
             and all('"state":"failsafe"' in x for x in fc_ticks))
    # (b) live transitions under observation
    live_log = "/tmp/opencode/w0b_watchdog_live.log"
    run(BIN / "ledger_kill", "resolve", "L4", "wd-reset")   # baseline open
    p2 = subprocess.Popen(
        [str(BIN / "ledger_watchdog"), "--interval-ms", "250",
         "--timeout-ms", "2600"],
        env=LEDGER_ENV, stdout=open(live_log, "w"), stderr=subprocess.STDOUT, cwd=str(REPO))
    time.sleep(0.7)
    t_engage = int(time.time() * 1000)
    run(BIN / "ledger_kill", "engage", "L0", "W0b-watchdog-drill")
    time.sleep(0.9)
    t_resolve = int(time.time() * 1000)
    run(BIN / "ledger_kill", "resolve", "L4", "wd-resolve")
    p2.wait(timeout=15)
    ticks = []
    for x in Path(live_log).read_text().splitlines():
        if x.startswith("LEDGER-WATCHDOG ") and "tunix_ms" in x:
            ticks.append(json.loads(x.split(" ", 1)[1]))
    engaged_seen = [t["tunix_ms"] for t in ticks if t["state"] == "engaged"]
    open_after = [t["tunix_ms"] for t in ticks if t["state"] == "open"
                  and t["tunix_ms"] >= t_resolve]
    live_ok = bool(engaged_seen and engaged_seen[0] >= t_engage - 300 and open_after)
    engage_detect = (engaged_seen[0] - t_engage) if engaged_seen else None
    print("W0B-G6-WATCHDOG " + json.dumps({
        "failclosed_rc": rc_fc, "failclosed_tick_count": len(fc_ticks),
        "failclosed_all_failsafe": fc_ok,
        "live_ticks": len(ticks), "engaged_tick_ms_delta": engage_detect,
        "open_after_resolve_seen": bool(open_after), "ok": fc_ok and live_ok,
    }))
    return fc_ok and live_ok or False


def gate_engine():
    """W0b pin: E1/E2/E5/E7 skeleton — the deterministic decision spine.

    Phase A (kill ENGAGED): 200 ticks, 4 decisions — every one must refuse on
    the kill ladder and ZERO order intents published (fail-closed decision).
    Phase B (kill OPEN): fresh cell + fresh E7 — 4 allows, 4 signed fills in
    D4, signature recon clean, E1+E2+E5 internal p99 within the <1 ms budget.
    """
    e, secret = owner_env()
    e = dict(e, LEDGER_SECRET_OWNER=secret, RAIL_NATS_URL="nats://127.0.0.1:4222",
             KILL_REFRESH_MS="200", CELL_TICKS="220", CELL_DECIDE_EVERY="50",
             CELL_SUBJECT="market.spot.NIFTY")
    run(BIN / "ledger_init", str(SQL), env=e)
    psql_exec("TRUNCATE d4.fills; TRUNCATE d4.pnl_recon;")

    def feed(path, base_price, base_ts):
        with open(path, "w") as fh:
            for i in range(220):
                fh.write(f"NIFTY {base_ts + i * 1_000_000_000} "
                         f"{round(base_price + i * 0.02, 2)} public\n")

    def run_cell_phase(tag, price_base, ts_base):
        log = f"/tmp/opencode/w0b_cell_{tag}.log"
        with open(log, "w") as fh:
            p = subprocess.Popen([str(BIN / "cell")], env=e, stdout=fh,
                                 stderr=subprocess.STDOUT, cwd=str(REPO))
        time.sleep(1.2)  # cell subscribed
        feed(f"/tmp/opencode/w0b_cell_{tag}_feed.tsv", price_base, ts_base)
        subprocess.run([str(BIN / "rail")], stdin=open(f"/tmp/opencode/w0b_cell_{tag}_feed.tsv"),
                       capture_output=True, timeout=120, cwd=str(REPO))
        p.wait(timeout=60)
        return Path(log).read_text()

    # ---- Phase A: engaged → every decision refuses, zero intents ----
    run(BIN / "ledger_kill", "engage", "L0", "W0b-engine-failclosed", env=e)
    txtA = run_cell_phase("A", 100.50, 1773000000000000000)
    summA = [x for x in txtA.splitlines() if x.startswith("LEDGER-CELL-SUMMARY")]
    sa = json.loads(summA[-1].split(" ", 1)[1]) if summA else {}
    a_ok = (sa.get("decisions") == 4 and sa.get("refuse_kill") == 4
            and sa.get("intents_published") == 0)

    # ---- Phase B: open → allow → E6 intent → E7 signed fill → recon ----
    run(BIN / "ledger_kill", "resolve", "L4", "W0b-engine-open", env=e)
    with open("/tmp/opencode/w0b_e7_in.txt", "w") as gfh:
        gfh.write("GO\n")
    e7log = open("/tmp/opencode/w0b_e7.log", "w")
    p_e7 = subprocess.Popen([str(BIN / "e7")],
                            env=dict(e, E7_EXPECT="4", E7_SUBJECT="order.spot.>",
                                     LEDGER_SECRET_OWNER=secret),
                            stdin=open("/tmp/opencode/w0b_e7_in.txt"),
                            stdout=subprocess.DEVNULL, stderr=e7log, cwd=str(REPO))
    time.sleep(1.2)
    txtB = run_cell_phase("B", 200.00, 1774000000000000000)
    p_e7.wait(timeout=60)
    e7log.close()
    summB = [x for x in txtB.splitlines() if x.startswith("LEDGER-CELL-SUMMARY")]
    sb = json.loads(summB[-1].split(" ", 1)[1]) if summB else {}
    e7txt = Path("/tmp/opencode/w0b_e7.log").read_text()
    e7_done = [x for x in e7txt.splitlines() if x.startswith("LEDGER-E7-DONE")]
    sev = json.loads(e7_done[-1].split(" ", 1)[1]) if e7_done else {}
    # signature verification over everything E7 signed
    recon = subprocess.run([str(BIN / "ledger_recon")], env=e,
                           capture_output=True, text=True, timeout=300)
    rline = [x for x in recon.stdout.splitlines() if x.startswith("LEDGER-RECON ")]
    rj = json.loads(rline[-1].split(" ", 1)[1]) if rline else {}
    b_ok = (sb.get("decisions") == 4 and sb.get("allows") == 4
            and sb.get("intents_published") == 4 and sb.get("e12_p99", 1e12) < 1_000_000
            and sev.get("fills") == 4 and rj.get("bad_sigs") == 0 and recon.returncode == 0)
    ok = a_ok and b_ok
    print("W0B-G7-ENGINE-CELL " + json.dumps({
        "phaseA": {"decisions": sa.get("decisions"), "refuse_kill": sa.get("refuse_kill"),
                   "intents_published": sa.get("intents_published"), "ok": a_ok},
        "phaseB": {"decisions": sb.get("decisions"), "allows": sb.get("allows"),
                   "intents_published": sb.get("intents_published"),
                   "e12_p50_ns": sb.get("e12_p50"), "e12_p99_ns": sb.get("e12_p99"),
                   "budget_ns": 1_000_000, "fills_signed": sev.get("fills"),
                   "bad_sigs": rj.get("bad_sigs"), "ok": b_ok},
        "ok": ok,
    }))
    for x in txtB.splitlines():
        if x.startswith("LEDGER-CELL-DECISION "):
            print("  " + x[:150])
    return ok


GATES = {"APPEND": gate_append_only, "KILL": gate_kill_drill,
         "RESTART": gate_restart_x10, "RECON": gate_sign_recon,
         "FANOUT": gate_fanout, "WATCHDOG": gate_watchdog,
         "ENGINE": gate_engine}

if __name__ == "__main__":
    which = sys.argv[1].upper() if len(sys.argv) > 1 else "ALL"
    todo = GATES if which == "ALL" else {which: GATES[which]}
    results = {}
    last_output = {}
    for name, fn in todo.items():
        try:
            results[name] = fn()
        except Exception as e:
            print(f"W0B-GATE-{name}-FAIL {e}")
            results[name] = False
    print("SUMMARY " + json.dumps(results))
    # land verdicts in d3.gate_w0b (append-only audit of gate runs)
    try:
        stamp = json.dumps({k: bool(v) for k, v in results.items()})
        psql_exec(
            "INSERT INTO d3.gate_w0b (gate, verdict, payload) VALUES "
            f"('w0b_batch_{time.strftime('%Y%m%dT%H%M%S')}', "
            f"'{'pass' if all(results.values()) else 'fail'}', $BODY${stamp}$BODY$"
            + ") ON CONFLICT (gate) DO NOTHING;"
        )
    except Exception as e:  # landing failure must not fake a green gate
        print(f"W0B-LAND-FAIL d3.gate_w0b: {e}")
    sys.exit(0 if all(results.values()) else 1)
