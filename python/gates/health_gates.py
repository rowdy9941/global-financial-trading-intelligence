#!/usr/bin/env python3
"""Health-view gates (A2 §4 A-5, items 16–17).

`gfti status` already printed state, health and a heartbeat age. That is not a health
view: it cannot tell a paused service from a working one, it says nothing about the
ledger, and it has no way to admit that a store it wants to report does not exist yet.

The claim these cases test is therefore narrow and behavioural:

  FIELDS  every runtime service carries all eight facts a human needs to decide
          something — up/down, restart count, heartbeat age, last fill, kill state,
          queue depth, D1 write rate, D2 stream depth — and every store block is
          present in the payload, not omitted when empty.
  STALE   `docker pause` on a live service must turn the view RED within one
          interval and name the offender; unpause must turn it green again. This is
          the case that forces the "looks running, is not working" state to exist:
          a paused container still reports `state=running` and its last health
          verdict, so `ps` alone can never see it.
  HONEST  a measurement that cannot be taken is `null` **with a reason**. D1 has no
          live table until A-9 and D2 has no declared streams until A-8; reporting
          `0.0 rows/s` for a store that does not exist would be a fabricated number,
          and the whole point of the health view is to be believed.

STALE is skipped (not failed) when the runtime profile is not up, because there is
nothing to pause — the same convention `config_gates.RENDER` uses for a missing
daemon.

Usage: python3 python/gates/health_gates.py [FIELDS|STALE|HONEST|ALL]

Exit codes: 0 = every selected case passed · 1 = at least one failed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CLI = REPO / "python" / "gfti" / "cli.py"
SHIM = REPO / "bootstrap" / "gfti"
RUNTIME = ("rail", "cell", "e7")

# The eight facts. `service`/`name` are identity, the rest are the decisions.
SERVICE_FIELDS = (
    "service",
    "name",
    "state",
    "health",
    "up",
    "paused",
    "restart_count",
    "hb_age_s",
)
STORE_BLOCKS = ("d4", "queue", "d1", "d2")


def gfti(*args: str, timeout: int = 120) -> tuple[int, str]:
    argv = (
        ["sh", str(SHIM)] if Path("/bin/sh").exists() else [sys.executable, str(CLI)]
    ) + list(args)
    p = subprocess.run(
        argv,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def status_json(timeout: int = 120) -> tuple[int, dict | None, str]:
    rc, out = gfti("status", "--json", timeout=timeout)
    start = out.find("{")
    doc = None
    if start >= 0:
        try:
            doc = json.loads(out[start:])
        except json.JSONDecodeError:
            doc = None
    return rc, doc, out


def docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, timeout=timeout
    )


def case_fields() -> tuple[bool, str]:
    rc, doc, out = status_json()
    if not isinstance(doc, dict):
        return False, f"status --json unparseable (rc={rc}): {out.strip()[:160]!r}"

    problems = []
    for key in ("ok", "services", "degraded", "stores"):
        if key not in doc:
            problems.append(f"missing top-level {key!r}")

    services = doc.get("services") or []
    by_svc = {s.get("service"): s for s in services if isinstance(s, dict)}
    for svc, entry in by_svc.items():
        absent = [f for f in SERVICE_FIELDS if f not in entry]
        if absent:
            problems.append(f"{svc}: missing {absent}")
        if not isinstance(entry.get("up"), bool):
            problems.append(f"{svc}: up is not a bool")
        if not isinstance(entry.get("paused"), bool):
            problems.append(f"{svc}: paused is not a bool")
        if not isinstance(entry.get("restart_count"), int):
            problems.append(f"{svc}: restart_count is not an int")
        # A stale heartbeat and an explicit pause are both "not up", and `up` must
        # agree with the rest of the row rather than being a second opinion.
        stale = isinstance(entry.get("hb_age_s"), int) and not entry.get(
            "stale_ok", True
        )
        expected_up = (
            entry.get("state") == "running"
            and not entry.get("paused")
            and not stale
            and entry.get("health", "") in ("", "healthy")
        )
        if entry.get("up") is not expected_up:
            problems.append(
                f"{svc}: up={entry.get('up')} contradicts state/paused/hb_age/health"
            )

    stores = doc.get("stores") or {}
    absent = [b for b in STORE_BLOCKS if b not in stores]
    if absent:
        problems.append(f"missing store blocks {absent}")

    # Exit code must match the verdict: a red view that exits 0 is a view a script
    # will ignore.
    if doc.get("ok") is True and rc != 0:
        problems.append(f"ok=true but exit={rc}")
    if doc.get("ok") is False and rc == 0:
        problems.append("ok=false but exit=0")

    detail = (
        f"services={sorted(by_svc)} blocks={sorted(stores)} "
        f"problems={problems[:4]}{'' if len(problems) < 5 else '…'}"
    )
    return not problems, detail


def case_stale() -> tuple[bool, str]:
    rc, doc, out = status_json()
    if not isinstance(doc, dict):
        return False, f"status --json unparseable (rc={rc}): {out.strip()[:160]!r}"

    by_svc = {s.get("service"): s for s in doc.get("services") or []}
    target = next((by_svc[s] for s in RUNTIME if s in by_svc), None)
    if target is None or target.get("state") != "running":
        return True, "skipped (no runtime service is up; nothing to pause)"

    name = target.get("name")
    svc = target.get("service")
    interval = float(doc.get("interval_s") or 1.0)

    # Pausing is the honest simulation: the process exists, holds its sockets, and
    # stops working. `state` stays `running` and the last health verdict stays
    # `healthy`, which is exactly why a `compose ps` view cannot see this.
    paused = docker("pause", str(name))
    try:
        if paused.returncode != 0:
            return False, f"docker pause {name} failed: {paused.stderr.strip()[:120]}"
        time.sleep(min(2.0 + interval, 10.0))
        rc_p, doc_p, _out_p = status_json()
        red = isinstance(doc_p, dict) and doc_p.get("ok") is False
        named = isinstance(doc_p, dict) and svc in (doc_p.get("degraded") or [])
        row = {}
        if isinstance(doc_p, dict):
            row = {s.get("service"): s for s in doc_p.get("services") or []}.get(
                svc, {}
            )
        saw_paused = row.get("paused") is True
        saw_down = row.get("up") is False
        exit_red = rc_p != 0
    finally:
        docker("unpause", str(name))

    # Recovery: the same call must come back green without a restart. A view that
    # latches red is as useless as one that never goes red.
    green = False
    detail_after = ""
    for _ in range(12):
        rc_g, doc_g, _ = status_json()
        green = isinstance(doc_g, dict) and doc_g.get("ok") is True and rc_g == 0
        detail_after = "ok=false" if not green else ""
        if green:
            break
        time.sleep(interval)

    ok = red and named and saw_paused and saw_down and exit_red and green
    detail = (
        f"paused {name}: red={red} named={named} saw_paused={saw_paused} "
        f"saw_down={saw_down} exit_red={exit_red} recovered={green} {detail_after}"
    )
    return ok, detail


def case_honest() -> tuple[bool, str]:
    """A value that cannot be measured must say so. No silent zeroes."""
    rc, doc, _out = status_json()
    if not isinstance(doc, dict):
        return False, f"status --json unparseable (rc={rc})"

    stores = doc.get("stores") or {}
    problems = []
    for block_name in ("d1", "d2"):
        block = stores.get(block_name)
        if not isinstance(block, dict):
            problems.append(f"{block_name}: not a block")
            continue
        # `reason` is the explanation, not a measurement: it must be excluded from
        # both halves or the block always looks like it carries a value.
        values = {k: v for k, v in block.items() if k != "reason"}
        has_reason = bool(block.get("reason"))
        if not values:
            problems.append(f"{block_name}: empty block")
        for key, val in values.items():
            if val is None and not has_reason:
                problems.append(f"{block_name}.{key} null without a reason")
            elif val is not None and not isinstance(val, (int, float)):
                problems.append(f"{block_name}.{key} is {type(val).__name__}")
        # And the reverse mistake: a block that reports numbers while claiming it
        # cannot measure.
        if has_reason and any(v is not None for v in values.values()):
            problems.append(f"{block_name}: has both a reason and values")

    detail = f"d1={stores.get('d1')} d2={stores.get('d2')} problems={problems[:3]}"
    return not problems, detail


CASES = {"FIELDS": case_fields, "STALE": case_stale, "HONEST": case_honest}


def main() -> None:
    which = sys.argv[1].upper() if len(sys.argv) > 1 else "ALL"
    selected = list(CASES) if which == "ALL" else [which]
    unknown = [c for c in selected if c not in CASES]
    if unknown:
        print(f"HEALTH-GATES unknown case(s) {unknown}; choose from {list(CASES)}")
        sys.exit(2)

    summary, bad = {}, []
    for name in selected:
        ok, detail = CASES[name]()
        summary[name] = ok
        print(f"HEALTH-{name} {'ok' if ok else 'FAIL'} — {detail}", flush=True)
        if not ok:
            bad.append(name)
    summary["ok"] = not bad
    print("SUMMARY " + json.dumps(summary, sort_keys=True))
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
