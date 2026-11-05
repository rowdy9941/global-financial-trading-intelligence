#!/usr/bin/env python3
"""gfti CLI contract gates (A2 §4 A-3, items 10–12).

A2 asks for one entry point (`install`, `up`, `down`, `status`, `doctor`, `logs`,
`smoke`) and a gate proving "every action in the blueprint's workflow list is
reachable from `gfti` alone". Reachable is a claim about *routing*, so these cases
assert routing, not infrastructure:

  HELP    every verb is advertised by `gfti --help`
  WF      the workflow table has all 8 rows, every route is a real verb, and a
          `missing` row names what is missing (no silent empties)
  DOCTOR  `--json` is machine-readable and `ok` agrees with the checks
  STATUS  `--json` is machine-readable, and works with the stack up OR down
  SMOKE   `--json` is machine-readable, and `ok` agrees with the checks
  REFUSE  an unimplemented workflow exits 3 and says so — never 0, never a traceback
  SHIM    `sh bootstrap/gfti --version` runs (the command a user actually types)

Deliberately no DB, no Docker requirement, no Rust build: this must pass on a fresh
clone so it can fence the one thing a fresh clone can still get wrong — a CLI that
advertises a route it does not have.

Usage: python3 python/gates/cli_gates.py [HELP|WF|DOCTOR|STATUS|SMOKE|REFUSE|SHIM|ALL]

Exit codes: 0 = every selected case passed · 1 = at least one failed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SHIM = REPO / "bootstrap" / "gfti"
CLI = REPO / "python" / "gfti" / "cli.py"
POSIX = os.name == "posix"

CORE_VERBS = (
    "install",
    "up",
    "down",
    "status",
    "doctor",
    "logs",
    "smoke",
    "config",
    "verify",
    "ingest",
    "research",
    "kill",
    "workflows",
)


def gfti(*args: str) -> tuple[int, str]:
    """Invoke through the shim — the real entry point, not the module."""
    argv = (["sh", str(SHIM)] if POSIX else [sys.executable, str(CLI)]) + list(args)
    p = subprocess.run(argv, cwd=str(REPO), capture_output=True, text=True, check=False)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def parse_json(out: str):
    """The verb prints one JSON document; ignore any trailing prose."""
    start = out.find("{")
    if start < 0:
        return None
    try:
        return json.loads(out[start:])
    except json.JSONDecodeError:
        return None


def case_help() -> tuple[bool, str]:
    rc, out = gfti("--help")
    missing = [v for v in CORE_VERBS if v not in out]
    ok = rc == 0 and not missing
    detail = f"rc={rc} verbs={len(CORE_VERBS)}" + (
        f" missing={missing}" if missing else ""
    )
    return ok, detail


def case_wf() -> tuple[bool, str]:
    rc, out = gfti("workflows", "--json")
    doc = parse_json(out)
    if rc != 0 or not isinstance(doc, dict):
        return False, f"rc={rc} unparseable: {out.strip()[:120]!r}"
    rows = doc.get("workflows") or []
    problems = []
    if sorted(r.get("n") for r in rows) != list(range(1, 9)):
        problems.append("not the 8 numbered workflows")
    _, help_out = gfti("--help")
    for r in rows:
        if r.get("verb") not in help_out:
            problems.append(f"wf{r.get('n')} routes to absent verb {r.get('verb')!r}")
        if r.get("status") not in ("ready", "partial", "missing"):
            problems.append(f"wf{r.get('n')} bad status {r.get('status')!r}")
        if r.get("status") == "missing" and not (r.get("note") or "").strip():
            problems.append(f"wf{r.get('n')} missing with no named gap")
    ok = not problems
    tally = {
        s: sum(1 for r in rows if r.get("status") == s)
        for s in ("ready", "partial", "missing")
    }
    detail = f"rc={rc} rows={len(rows)} {tally}" + (
        f" problems={problems}" if problems else ""
    )
    return ok, detail


def case_doctor() -> tuple[bool, str]:
    rc, out = gfti("doctor", "--json")
    doc = parse_json(out)
    if not isinstance(doc, dict) or "checks" not in doc:
        return False, f"rc={rc} unparseable: {out.strip()[:120]!r}"
    checks = doc["checks"]
    consistent = doc.get("ok") == all(c.get("status") != "fail" for c in checks)
    ok = (
        rc in (0, 1)
        and bool(checks)
        and consistent
        and doc.get("tier") in ("A", "B", "C")
    )
    detail = (
        f"rc={rc} checks={len(checks)} fails="
        f"{sum(1 for c in checks if c.get('status') == 'fail')} "
        f"tier={doc.get('tier')} consistent={consistent}"
    )
    return ok, detail


def case_status() -> tuple[bool, str]:
    rc, out = gfti("status", "--json")
    doc = parse_json(out)
    if not isinstance(doc, dict):
        return False, f"rc={rc} unparseable: {out.strip()[:120]!r}"
    if rc == 2:  # daemon unreachable is an answer, not a crash, and must be labelled
        ok = doc.get("ok") is False and "daemon" in json.dumps(doc).lower()
        return ok, f"rc=2 (daemon unreachable, reported not crashed: {ok})"
    services = doc.get("services") or []
    expected = ("postgres", "questdb", "nats")
    well_formed = all({"service", "state", "up"} <= set(s) for s in services)
    # `up` is the single derived boolean the view publishes; the verdict must be
    # exactly "nothing missing and everything up". health_gates.FIELDS separately
    # proves each `up` agrees with that row's state/paused/heartbeat, so the two
    # gates cannot disagree about what green means.
    derived = not doc.get("missing") and all(s.get("up") is True for s in services)
    consistent = doc.get("ok") == derived
    ok = rc in (0, 1) and well_formed and consistent
    detail = f"rc={rc} services={len(services)} expected={list(expected)} ok={doc.get('ok')} consistent={consistent}"
    return ok, detail


def case_smoke() -> tuple[bool, str]:
    rc, out = gfti("smoke", "--json")
    doc = parse_json(out)
    if not isinstance(doc, dict) or "checks" not in doc:
        return False, f"rc={rc} unparseable: {out.strip()[:120]!r}"
    checks = doc["checks"]
    consistent = doc.get("ok") == all(c.get("status") != "fail" for c in checks)
    skipped = [c["name"] for c in checks if c.get("status") == "skip"]
    ok = rc in (0, 1) and bool(checks) and consistent
    detail = f"rc={rc} checks={len(checks)} skipped={skipped} consistent={consistent}"
    return ok, detail


def case_refuse() -> tuple[bool, str]:
    """Every `missing` workflow must refuse with 3 and name its gap."""
    _, wf_out = gfti("workflows", "--json")
    rows = (parse_json(wf_out) or {}).get("workflows") or []
    targets = [r for r in rows if r.get("status") == "missing"]
    if not targets:
        return True, "no workflow is declared missing (nothing to refuse)"
    bad = []
    for r in targets:
        rc, out = gfti(r["verb"])
        if rc != 3:
            bad.append(f"{r['verb']} exited {rc}, expected 3")
        elif "not implemented" not in out.lower():
            bad.append(f"{r['verb']} did not say 'not implemented'")
        elif "Traceback" in out:
            bad.append(f"{r['verb']} crashed instead of refusing")
    ok = not bad
    return ok, f"refused={len(targets) - len(bad)}/{len(targets)}" + (
        f" {bad}" if bad else ""
    )


def case_shim() -> tuple[bool, str]:
    if not POSIX:
        return True, "skipped (non-POSIX host: shim is a POSIX sh script)"
    p = subprocess.run(
        ["sh", str(SHIM), "--version"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    ok = p.returncode == 0 and p.stdout.strip().startswith("gfti ")
    return ok, f"rc={p.returncode} out={p.stdout.strip()[:60]!r}"


CASES = {
    "HELP": case_help,
    "WF": case_wf,
    "DOCTOR": case_doctor,
    "STATUS": case_status,
    "SMOKE": case_smoke,
    "REFUSE": case_refuse,
    "SHIM": case_shim,
}


def main() -> None:
    which = sys.argv[1].upper() if len(sys.argv) > 1 else "ALL"
    selected = list(CASES) if which == "ALL" else [which]
    unknown = [c for c in selected if c not in CASES]
    if unknown:
        print(f"CLI-GATES unknown case(s) {unknown}; choose from {list(CASES)}")
        sys.exit(2)

    summary, bad = {}, []
    for name in selected:
        ok, detail = CASES[name]()
        summary[name] = ok
        print(f"CLI-{name} {'ok' if ok else 'FAIL'} — {detail}", flush=True)
        if not ok:
            bad.append(name)
    summary["ok"] = not bad
    print("SUMMARY " + json.dumps(summary, sort_keys=True))
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
