#!/usr/bin/env python3
"""gfti — the one entry point for a GFI/FinOS installation (A2 §4 A-3).

The repo grew a shell per concern: `bootstrap/install.sh` installs, `docker compose`
runs the stack, `python/gates/*` prove it, `python/collect/*` capture, `python/train/*`
research, `rust/target/release/ledger_kill` flips the kill cascade. That is six
interfaces, each with its own argument conventions, and the blueprint's promise is
one installable piece of software. This module is the one front door.

Design rules it keeps:

* **stdlib only.** An installer cannot require the toolchain it is about to install,
  so `gfti` runs on a bare `python3` (3.9+) with no venv, no `uv`, no `pip`.
* **it delegates, it does not reimplement.** Every verb shells out to the thing that
  already owns the job; if a verb and its implementation disagree, that is a bug in
  one file rather than a second implementation to drift.
* **it refuses honestly.** A workflow with no implementation exits 3 and names the
  missing package. It never exits 0 having done nothing.

Exit codes (stable, because the gate asserts on them):

    0  ok
    1  a check failed
    2  the environment cannot answer (daemon down, prereq missing)
    3  not implemented — an honest refusal, not a failure

Usage: gfti <verb> [options]   ·   `gfti workflows` for the blueprint mapping.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

VERSION = "0.1.0"

REPO = Path(__file__).resolve().parent.parent.parent
COMPOSE_DIR = REPO / "compose"
COMPOSE_FILE = COMPOSE_DIR / "docker-compose.yml"
GATES = REPO / "python" / "gates"
RUNTIME_SERVICES = ("rail", "cell", "e7")
SECRET_RE = re.compile(r"(SECRET|PASSWORD|PASSWD|TOKEN|_KEY|PRIVATE)", re.IGNORECASE)

EXIT_OK, EXIT_FAIL, EXIT_ENV, EXIT_UNIMPL = 0, 1, 2, 3


# --------------------------------------------------------------------------- io


class Result:
    """A finished child process, with stdout/stderr already decoded."""

    def __init__(self, argv, rc, out, err, seconds):
        self.argv, self.rc, self.out, self.err = argv, rc, out, err
        self.seconds = seconds

    @property
    def ok(self):
        return self.rc == 0


def sh(argv, cwd=None, timeout=None, capture=True, env=None) -> Result:
    """Run a command. Never raises on a non-zero exit; raises only if it cannot run."""
    started = time.time()
    try:
        p = subprocess.run(
            [str(a) for a in argv],
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=capture,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return Result(argv, 127, "", f"not found: {argv[0]}", time.time() - started)
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else ""
        return Result(
            argv, 124, out, f"timeout after {timeout}s", time.time() - started
        )
    return Result(
        argv, p.returncode, p.stdout or "", p.stderr or "", time.time() - started
    )


def compose(*args, profile=True, timeout=None, capture=True) -> Result:
    argv = ["docker", "compose", "-f", str(COMPOSE_FILE)]
    if profile:
        argv += ["--profile", "runtime"]
    argv += [str(a) for a in args]
    return sh(argv, cwd=COMPOSE_DIR, timeout=timeout, capture=capture)


def say(text=""):
    print(text, flush=True)


def _json_out(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True), flush=True)


def dotenv(path: Path) -> dict:
    """Read a KEY=VALUE file without importing a dependency. Missing file -> {}."""
    out = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def redacted(env: dict) -> dict:
    """Secrets become `***`; everything else is shown, because a config you cannot
    read is a config you cannot debug (A2 §4 A-4 will generate this file)."""
    return {k: ("***" if SECRET_RE.search(k) else v) for k, v in sorted(env.items())}


def ledger_url() -> str:
    """Host-side DSN, built the same way the gates build it (compose/.env)."""
    pw = dotenv(COMPOSE_DIR / ".env").get(
        "POSTGRES_PASSWORD", "dev-only-local-placeholder"
    )
    return f"postgres://gfti_app:{pw}@127.0.0.1:5432/gfti"


# ------------------------------------------------------------------- discovery


def detect_tier() -> tuple:
    """A0 §2: A = Linux, B = macOS, C = Windows/WSL2. Anything else is unknown."""
    if sys.platform.startswith("win"):
        return "C", "Windows (native)"
    release = platform.uname().release.lower()
    machine = platform.uname().machine or "?"
    if sys.platform == "darwin":
        return "B", f"macOS ({platform.mac_ver()[0] or '?'}, {machine})"
    if sys.platform.startswith("linux"):
        if "microsoft" in release or os.environ.get("WSL_DISTRO_NAME"):
            return "C", f"WSL2 ({release}, {machine})"
        return "A", f"{platform.system()} {platform.release()} ({machine})"
    return "?", f"{sys.platform} {release}"


def docker_up() -> tuple:
    """(reachable, version-or-error). A 10 s cap: a wedged daemon must not hang a CLI."""
    r = sh(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=10)
    if r.rc == 0 and r.out.strip():
        return True, r.out.strip()
    return False, (r.err.strip() or r.out.strip() or f"docker info exited {r.rc}")


def ps_rows() -> list:
    """`compose ps --format json` is NDJSON on compose <2.24 and an array later."""
    r = compose("ps", "--format", "json", timeout=120)
    body = r.out.strip()
    if not body:
        return []
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        return [json.loads(line) for line in body.splitlines() if line.strip()]


def heartbeat_stamp(name: str):
    """The service's own last-beat mtime (epoch seconds), or None."""
    r = sh(
        [
            "docker",
            "exec",
            name,
            "sh",
            "-c",
            'stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null',
        ],
        timeout=15,
    )
    if r.rc != 0 or not r.out.strip().isdigit():
        return None
    return int(r.out.strip())


def heartbeat_age(name: str):
    """Seconds since the service's own last beat, read inside the container."""
    stamp = heartbeat_stamp(name)
    return None if stamp is None else max(0, int(time.time()) - stamp)


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


# --------------------------------------------------------------------- doctor


def cmd_doctor(args) -> int:
    checks = []

    def add(name, status, detail, hard=True):
        checks.append({"name": name, "status": status, "detail": detail, "hard": hard})

    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    add("python", "ok" if sys.version_info >= (3, 9) else "fail", py)

    tier, tier_detail = detect_tier()
    add("tier", "ok" if tier in ("A", "B", "C") else "warn", f"{tier} — {tier_detail}")

    if not COMPOSE_FILE.is_file():
        add("compose_file", "fail", f"missing {COMPOSE_FILE}")
    else:
        add("compose_file", "ok", str(COMPOSE_FILE.relative_to(REPO)))

    env_file = COMPOSE_DIR / ".env"
    env = dotenv(env_file)
    if not env_file.is_file():
        add("env", "fail", "compose/.env missing — copy compose/.env.example")
    elif not env.get("POSTGRES_PASSWORD"):
        add("env", "fail", "compose/.env has no POSTGRES_PASSWORD")
    else:
        add("env", "ok", f"{env_file.name}: {len(env)} key(s), secrets redacted")

    reachable, detail = docker_up()
    add("docker_daemon", "ok" if reachable else "fail", detail)

    if shutil.which("docker"):
        r = sh(["docker", "compose", "version", "--short"], timeout=20)
        add("compose_plugin", "ok" if r.ok else "fail", (r.out or r.err).strip()[:80])
    else:
        add("compose_plugin", "fail", "docker not on PATH")

    for tool in ("cargo", "uv"):
        path = shutil.which(tool)
        add(
            tool,
            "ok" if path else "warn",
            path or "not on PATH (needed by `gfti install`)",
        )

    if reachable:
        r = compose("config", "-q", timeout=60)
        add(
            "compose_config",
            "ok" if r.ok else "fail",
            (r.err or "valid").strip().splitlines()[0][:120],
        )
    else:
        add("compose_config", "skip", "daemon unreachable", hard=False)

    # ports: free, or held by this project's own container (a re-run is not a conflict)
    mine = set()
    if reachable:
        r = sh(["docker", "ps", "--format", "{{.Names}}"], timeout=20)
        mine = {line.strip() for line in r.out.splitlines() if line.strip()}
    for port, svc in (
        (5432, "postgres"),
        (9000, "questdb"),
        (9009, "questdb"),
        (4222, "nats"),
        (8222, "nats"),
    ):
        if port_open(port):
            owned = f"gfti-{svc}-1" in mine
            add(
                f"port_{port}",
                "ok" if owned else "fail",
                f"held by gfti-{svc}-1"
                if owned
                else "already in use by another process",
            )
        else:
            add(f"port_{port}", "ok", "free")

    for binary in ("rail", "cell", "e7", "ledger_kill"):
        path = REPO / "rust" / "target" / "release" / binary
        add(
            f"binary_{binary}",
            "ok" if path.exists() else "warn",
            "built" if path.exists() else "missing — run `gfti install`",
            hard=False,
        )

    writable = os.access(REPO, os.W_OK)
    add("repo_writable", "ok" if writable else "fail", str(REPO))

    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]
    ok = not failed and not (args.strict and warned)

    if args.json:
        _json_out(
            {
                "ok": ok,
                "version": VERSION,
                "tier": tier,
                "strict": args.strict,
                "checks": checks,
                "config": redacted(env),
            }
        )
        return EXIT_OK if ok else EXIT_FAIL

    say(f"gfti {VERSION} · tier {tier} ({tier_detail})")
    for c in checks:
        mark = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "skip": "----"}[
            c["status"]
        ]
        say(f"  {mark} {c['name']:<18} {c['detail']}")
    if env:
        say("\neffective config (compose/.env, secrets redacted)")
        for k, v in redacted(env).items():
            say(f"  {k}={v}")
    say(
        f"\ndoctor: {'OK' if ok else 'FAILED'}"
        + (f" ({len(failed)} failed, {len(warned)} warned)" if not ok else "")
    )
    if not ok:
        say("fix the FAIL rows, then re-run `gfti doctor`")
    return EXIT_OK if ok else EXIT_FAIL


# ------------------------------------------------------------------- lifecycle


def cmd_install(args) -> int:
    script = REPO / "bootstrap" / "install.sh"
    if not script.is_file():
        say(f"gfti install: missing {script}")
        return EXIT_ENV
    argv = ["bash", str(script)]
    if args.verify:
        argv.append("--verify")
    if args.tier:
        argv.append(f"--tier={args.tier}")
    say(f"gfti install: {' '.join(argv[1:]) or '(no flags)'}")
    r = sh(argv, cwd=REPO, capture=False)
    return r.rc


def cmd_up(args) -> int:
    reachable, detail = docker_up()
    if not reachable:
        say(f"gfti up: docker daemon unreachable — {detail}")
        return EXIT_ENV
    argv = ["up", "-d", "--wait"]
    if args.build:
        argv.append("--build")
    r = compose(*argv, profile=not args.stores_only, capture=False)
    if r.rc != 0:
        say("gfti up: failed — `gfti logs` and `gfti status` explain why")
        return EXIT_FAIL
    say(
        "gfti up: ok"
        + (" (stores only)" if args.stores_only else " (stores + runtime)")
    )
    return EXIT_OK


def cmd_down(args) -> int:
    if args.volumes and not args.yes:
        say("gfti down --volumes DELETES pgdata/qdbdata (every D3/D4 row).")
        say("re-run with --yes if that is what you want.")
        return EXIT_FAIL
    argv = ["down"]
    if args.volumes:
        argv.append("--volumes")
    r = compose(*argv, capture=False)
    return r.rc


def cmd_status(args) -> int:
    reachable, detail = docker_up()
    if not reachable:
        payload = {"ok": False, "error": "docker daemon unreachable", "detail": detail}
        if args.json:
            _json_out(payload)
        else:
            say(f"gfti status: docker daemon unreachable — {detail}")
        return EXIT_ENV

    rows = ps_rows()
    services = []
    for row in rows:
        name = row.get("Name") or row.get("name") or "?"
        svc = row.get("Service") or row.get("service") or name
        state = (row.get("State") or row.get("state") or "").lower()
        health = (row.get("Health") or row.get("health") or "").strip()
        entry = {"service": svc, "name": name, "state": state, "health": health}
        if svc in RUNTIME_SERVICES and state == "running":
            entry["hb_age_s"] = heartbeat_age(name)
        services.append(entry)

    present = {s["service"] for s in services}
    missing = [s for s in ("postgres", "questdb", "nats") if s not in present]
    unhealthy = [s for s in services if s["health"] and s["health"] != "healthy"]
    down = [s for s in services if s["state"] not in ("running",)]
    ok = not (missing or unhealthy or down)

    if args.json:
        _json_out({"ok": ok, "services": services, "missing": missing})
        return EXIT_OK if ok else EXIT_FAIL

    if not services:
        say("gfti status: no containers for this project — run `gfti up`")
        return EXIT_FAIL
    say(f"{'SERVICE':<12}{'STATE':<12}{'HEALTH':<12}HEARTBEAT")
    for s in services:
        hb = s.get("hb_age_s")
        hb_txt = f"{hb}s ago" if isinstance(hb, int) else "-"
        say(f"{s['service']:<12}{s['state']:<12}{(s['health'] or '-'):<12}{hb_txt}")
    if missing:
        say(f"missing: {', '.join(missing)}")
    if unhealthy or down:
        say("degraded — `gfti logs <service>` for the reason")
    return EXIT_OK if ok else EXIT_FAIL


def cmd_logs(args) -> int:
    argv = ["logs", "--tail", str(args.tail)]
    if args.follow:
        argv.append("--follow")
    argv += args.service
    r = compose(*argv, capture=False)
    return r.rc


# ----------------------------------------------------------------------- smoke


def cmd_smoke(args) -> int:
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    r = compose("config", "-q", timeout=60)
    add("compose_config", "ok" if r.ok else "fail", (r.err or "valid").strip()[:160])

    binaries = {
        b: (REPO / "rust" / "target" / "release" / b).exists()
        for b in ("rail", "cell", "e7", "ledger_kill")
    }
    add(
        "binaries",
        "ok" if all(binaries.values()) else "fail",
        ", ".join(f"{k}={'yes' if v else 'NO'}" for k, v in binaries.items()),
    )

    add(
        "env_file",
        "ok" if (COMPOSE_DIR / ".env").is_file() else "fail",
        "compose/.env" if (COMPOSE_DIR / ".env").is_file() else "compose/.env missing",
    )

    reachable, detail = docker_up()
    if not reachable:
        add("stack", "skip", f"daemon unreachable: {detail}")
    else:
        rows = {row.get("Service") or row.get("service"): row for row in ps_rows()}
        live = [s for s in RUNTIME_SERVICES if s in rows]
        if not live:
            add("stack", "skip", "runtime profile is down — `gfti up` starts it")
        else:
            bad = [
                s
                for s in live
                if (rows[s].get("Health") or rows[s].get("health")) != "healthy"
            ]
            add(
                "stack_health",
                "ok" if not bad else "fail",
                "all healthy" if not bad else f"unhealthy: {', '.join(bad)}",
            )
            stamps1 = {
                s: heartbeat_stamp(rows[s].get("Name") or rows[s].get("name"))
                for s in live
            }
            time.sleep(2.2)
            stamps2 = {
                s: heartbeat_stamp(rows[s].get("Name") or rows[s].get("name"))
                for s in live
            }
            advancing = [
                s
                for s in live
                if isinstance(stamps1[s], int)
                and isinstance(stamps2[s], int)
                and stamps2[s] > stamps1[s]
            ]
            add(
                "heartbeat_advancing",
                "ok" if adv_ok(advancing, live) else "fail",
                f"{len(advancing)}/{len(live)} beats advanced in 2.2s",
            )

    failed = [c for c in checks if c["status"] == "fail"]
    ok = not failed
    if args.json:
        _json_out({"ok": ok, "checks": checks})
        return EXIT_OK if ok else EXIT_FAIL
    for c in checks:
        mark = {"ok": "ok  ", "fail": "FAIL", "skip": "----"}[c["status"]]
        say(f"  {mark} {c['name']:<20} {c['detail']}")
    say(f"smoke: {'OK' if ok else 'FAILED'}")
    return EXIT_OK if ok else EXIT_FAIL


def adv_ok(advancing, live) -> bool:
    """A beat that never advances is a wedged service, not a fast one."""
    return len(advancing) == len(live) and bool(live)


def cmd_verify(args) -> int:
    # The aggregator spawns each gate with `sys.executable`, so whichever Python runs
    # status.py must already have the lane's dependencies (pyarrow/duckdb). Running it
    # under `uv` is what makes that true; measured 2026-11-05 — plain `python3` gave
    # `ModuleNotFoundError: pyarrow` inside w1_gates and a false G-RESEARCH failure.
    if shutil.which("uv"):
        argv = [
            "uv",
            "run",
            "--project",
            str(REPO / "python" / "train"),
            "python3",
            str(GATES / "status.py"),
        ]
    else:
        argv = [sys.executable, str(GATES / "status.py")]
    if args.install:
        argv.append("--install")
    if args.json:
        argv.append("--json")
    r = sh(argv, cwd=REPO, capture=False)
    return r.rc


# ----------------------------------------------------------------- the lanes


def collectors() -> list:
    return sorted(
        p.stem
        for p in (REPO / "python" / "collect").glob("*.py")
        if not p.name.startswith("_")
    )


def cmd_ingest(args) -> int:
    available = collectors()
    if args.source not in available:
        say(f"gfti ingest: unknown source {args.source!r}")
        say("available: " + ", ".join(available))
        return EXIT_FAIL
    script = REPO / "python" / "collect" / f"{args.source}.py"
    argv = [
        "uv",
        "run",
        "--project",
        str(REPO / "python" / "collect"),
        "python3",
        str(script),
        *args.rest,
    ]
    say(f"gfti ingest: {args.source}")
    return sh(argv, cwd=REPO / "python" / "collect", capture=False).rc


def cmd_research(args) -> int:
    out = args.out or str(
        REPO / "d5" / f"research-{args.dataset.replace('/', '_')}.json"
    )
    argv = [
        "uv",
        "run",
        "--project",
        str(REPO / "python" / "train"),
        "python3",
        str(REPO / "python" / "train" / "walk_forward.py"),
        "--dataset",
        args.dataset,
        "--folds",
        str(args.folds),
        "--out",
        out,
    ]
    say(f"gfti research: {args.dataset} · {args.folds} folds → {out}")
    return sh(argv, cwd=REPO / "python" / "train", capture=False).rc


def cmd_kill(args) -> int:
    binary = REPO / "rust" / "target" / "release" / "ledger_kill"
    if not binary.exists():
        say(
            "gfti kill: ledger_kill not built — run `gfti install` or `cargo build --release` in rust/"
        )
        return EXIT_ENV
    argv = [str(binary), args.action, args.level, args.reason]
    env = dict(os.environ, LEDGER_URL=os.environ.get("LEDGER_URL") or ledger_url())
    r = sh(argv, env=env, capture=False)
    return r.rc


def cmd_unimplemented(args) -> int:
    """An honest refusal. Exit 3 is a distinct, assertable state."""
    say(f"gfti {args.verb}: not implemented — {args.missing}")
    say(
        "this verb is a declared route with no code behind it yet; see `gfti workflows`."
    )
    return EXIT_UNIMPL


# ------------------------------------------------------------------ workflows

"""The blueprint's eight workflows (FULL-SYSTEM-BLUEPRINT §5.3) mapped to `gfti`.

`status` is a claim about the tree today, not about the design:

  ready    a route that runs real code end to end
  partial  real code, one known gap named in `note`
  missing  no implementation; invoking the verb exits 3 and names the gap

The gate asserts that every row's `verb` exists in the parser, so the table cannot
drift into advertising a command that was never built.
"""
WORKFLOWS = [
    {
        "n": 1,
        "name": "DATA INGESTION",
        "verb": "ingest",
        "status": "ready",
        "note": "python/collect/*.py writers + rail; rail's live input is stdin until A-10",
    },
    {
        "n": 2,
        "name": "RESEARCH (Mode R)",
        "verb": "research",
        "status": "ready",
        "note": "E4-WF walk-forward + DSR via python/train/walk_forward.py",
    },
    {
        "n": 3,
        "name": "LIVE TRADING (Mode T)",
        "verb": "up",
        "status": "partial",
        "note": "rail/cell/e7 resident on the runtime profile; rail has no live feed until A-10",
    },
    {
        "n": 4,
        "name": "FIX-A-BUG",
        "verb": "verify",
        "status": "ready",
        "note": "reproduce from E7/D4, then re-run the gate suite",
    },
    {
        "n": 5,
        "name": "TRAINING / SELF-LEARNING",
        "verb": "research",
        "status": "partial",
        "note": "re-gate a candidate artifact; the weekly/drift scheduler is not built",
    },
    {
        "n": 6,
        "name": "CROSS-CELL SWARM TESTING",
        "verb": "swarm",
        "status": "missing",
        "note": "no union-instrument batch runner (needs D5 multi-artifact input)",
    },
    {
        "n": 7,
        "name": "COCKPIT CHAT",
        "verb": "cockpit",
        "status": "missing",
        "note": "no report/cockpit surface (cell health, portfolio, kill-state, approvals)",
    },
    {
        "n": 8,
        "name": "KILL (Emergency Response)",
        "verb": "kill",
        "status": "ready",
        "note": "ledger_kill engage/resolve at L0–L4, append-only D4 event",
    },
]


def cmd_workflows(args) -> int:
    if args.json:
        _json_out({"workflows": WORKFLOWS})
        return EXIT_OK
    say(f"{'#':<3}{'WORKFLOW':<30}{'gfti':<11}{'STATUS':<9}NOTE")
    mark = {"ready": "ready", "partial": "PARTIAL", "missing": "MISSING"}
    for w in WORKFLOWS:
        say(
            f"{w['n']:<3}{w['name']:<30}{w['verb']:<11}{mark[w['status']]:<9}{w['note']}"
        )
    say(
        "\nready = real code end to end · partial = real code with one named gap · "
        "missing = invoking it exits 3"
    )
    return EXIT_OK


# ---------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gfti",
        description="One entry point for a GFI/FinOS installation.",
        epilog="gfti workflows — the blueprint's eight workflows and their route",
    )
    p.add_argument("--version", action="version", version=f"gfti {VERSION}")
    sub = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    s = sub.add_parser("install", help="install/verify prerequisites, build, migrate")
    s.add_argument(
        "--verify", action="store_true", help="run the W1 + pilot gate suites after"
    )
    s.add_argument("--tier", choices=["A", "B", "C"], help="assert the OS tier (A0 §2)")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("up", help="start the stack and wait until healthy")
    s.add_argument(
        "--stores-only", action="store_true", help="skip the runtime profile"
    )
    s.add_argument(
        "--build", action="store_true", help="rebuild the runtime image first"
    )
    s.set_defaults(fn=cmd_up)

    s = sub.add_parser("down", help="stop the stack")
    s.add_argument(
        "--volumes", action="store_true", help="DELETE pgdata/qdbdata (needs --yes)"
    )
    s.add_argument("--yes", action="store_true", help="confirm a destructive down")
    s.set_defaults(fn=cmd_down)

    s = sub.add_parser("status", help="service state, health, heartbeat age")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("doctor", help="prerequisites, ports, tier, effective config")
    s.add_argument("--json", action="store_true")
    s.add_argument("--strict", action="store_true", help="treat warnings as failures")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("logs", help="follow or tail service logs")
    s.add_argument("service", nargs="*", help="service names (default: all)")
    s.add_argument("-f", "--follow", action="store_true")
    s.add_argument("-n", "--tail", type=int, default=80)
    s.set_defaults(fn=cmd_logs)

    s = sub.add_parser("smoke", help="fast end-to-end liveness proof")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_smoke)

    s = sub.add_parser("verify", help="run the full gate suite")
    s.add_argument("--install", action="store_true", help="include G-INSTALL (slow)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("ingest", help="WORKFLOW 1 — run a collector")
    s.add_argument("source", help="collector name, e.g. hyperliquid_daily")
    s.add_argument(
        "rest", nargs=argparse.REMAINDER, help="passed through to the collector"
    )
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser(
        "research", help="WORKFLOW 2/5 — walk-forward + DSR on a D3 dataset"
    )
    s.add_argument("--dataset", default="hyperliquid/BTC-USD/1d")
    s.add_argument("--folds", type=int, default=4)
    s.add_argument("--out", help="artifact path (default d5/research-<dataset>.json)")
    s.set_defaults(fn=cmd_research)

    s = sub.add_parser("kill", help="WORKFLOW 8 — engage/resolve the kill cascade")
    s.add_argument("action", choices=["engage", "resolve"])
    s.add_argument("--level", default="L0", choices=["L0", "L1", "L2", "L3", "L4"])
    s.add_argument("--reason", default="owner command")
    s.set_defaults(fn=cmd_kill)

    for verb, missing in (
        ("swarm", "no union-instrument batch runner (WORKFLOW 6)"),
        ("cockpit", "no report/cockpit surface (WORKFLOW 7)"),
    ):
        s = sub.add_parser(verb, help=f"WORKFLOW — not implemented ({missing})")
        s.set_defaults(fn=cmd_unimplemented, verb=verb, missing=missing)

    s = sub.add_parser(
        "workflows", help="the blueprint's eight workflows → gfti routes"
    )
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_workflows)

    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:  # argparse already printed; keep the code
        return int(e.code or 0)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
