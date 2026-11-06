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
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:  # run as a script: python/gfti and python are both plausible sys.path[0]
    import gfti_config as gcfg
except ImportError:  # imported as a package module
    from . import gfti_config as gcfg

VERSION = "0.1.0"

REPO = Path(__file__).resolve().parent.parent.parent
COMPOSE_DIR = REPO / "compose"
COMPOSE_FILE = COMPOSE_DIR / "docker-compose.yml"
GATES = REPO / "python" / "gates"
RUNTIME_SERVICES = ("rail", "cell", "e7")
SECRET_RE = gcfg.SECRET_KEY_RE  # one definition, in gfti_config

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
    # A-4: the config file, not compose defaults, supplies every non-secret value.
    # If the file is broken, `up` must say so instead of starting a half-configured
    # stack from interpolated fallbacks.
    try:
        env = gcfg.process_env(gcfg.load())
    except gcfg.ConfigError as e:
        print(f"gfti: {e}", file=sys.stderr)
        return Result(argv, EXIT_ENV, "", str(e), 0.0)
    return sh(argv, cwd=COMPOSE_DIR, timeout=timeout, capture=capture, env=env)


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
    """Host-side ledger DSN, from the one config file plus the secret file.

    A-4: user, port and database are not repeated here — they come from
    `configs/gfti.toml` through the same loader the gates use. The password is the
    one value that may not live in the config, so it comes from `compose/.env`.
    There is deliberately **no default password**: a defaulted credential is a
    second source of truth that works on a developer box and silently points at
    nothing anywhere else. Fail closed and say which value is missing.
    """
    env = gcfg.process_env(gcfg.load())
    need = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_PORT", "POSTGRES_DB")
    missing = [k for k in need if not env.get(k)]
    if missing:
        raise gcfg.ConfigError(
            f"ledger DSN needs {', '.join(missing)} — see `gfti doctor`"
        )
    return (
        f"postgres://{env['POSTGRES_USER']}:{env['POSTGRES_PASSWORD']}"
        f"@127.0.0.1:{env['POSTGRES_PORT']}/{env['POSTGRES_DB']}"
    )


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


def cmd_tier(args) -> int:
    """A0 §2's tier probe, as a verb. The installer asks this instead of keeping
    its own copy of the OS table — one definition, one answer."""
    tier, detail = detect_tier()
    known = tier in ("A", "B", "C")
    if args.json:
        _json_out({"tier": tier, "detail": detail, "supported": known})
    else:
        say(f"tier {tier} — {detail}")
        if not known:
            say("unsupported: A0 §2 covers Linux (A), macOS (B), Windows via WSL2 (C)")
    return EXIT_OK if known else EXIT_ENV


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


# A-5: a heartbeat is "stale" once it has missed more than three beats. One missed
# beat is scheduling noise on a loaded box; three is a stopped loop.
HB_BUDGET_FACTOR = 3


def inspect_services(names) -> dict:
    """state / paused / health / restart count for every container, in ONE call.

    `docker inspect` is used rather than `docker exec` on purpose: a **paused**
    container cannot be exec'd (the call blocks until its timeout), so an exec-based
    view would time out on exactly the state a health view exists to report. A paused
    process also still reports `state=running` with its last health verdict, which is
    why `compose ps` alone can never see it.
    """
    names = [n for n in names if n]
    if not names:
        return {}
    r = sh(
        [
            "docker",
            "inspect",
            *names,
            "--format",
            (
                "{{.Name}}|{{.State.Status}}|{{.State.Paused}}"
                "|{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}"
                "|{{.RestartCount}}"
            ),
        ],
        timeout=45,
    )
    out = {}
    for line in r.out.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 5:
            continue
        name, state, paused, health, restarts = parts
        out[name.lstrip("/")] = {
            "state": state.strip().lower(),
            "paused": paused.strip().lower() == "true",
            # an empty string means "this container declares no healthcheck"
            "health": "" if health.strip() in ("", "-") else health.strip(),
            "restart_count": int(restarts) if restarts.strip().isdigit() else 0,
        }
    return out


# One round trip, one JSON object. `order by evt_id desc limit 1` is the kill state:
# the append-only log's last row is the state now in force.
LEDGER_SQL = (
    "select json_build_object("
    "'last_fill_epoch',(select extract(epoch from max(ts_est))::bigint from d4.fills),"
    "'fills_60s',(select count(*) from d4.fills "
    "where ts_est > now() - interval '60 seconds'),"
    "'queued',(select count(*) from d3.llm_tasks where status = 'queued'),"
    "'leased',(select count(*) from d3.llm_tasks where status = 'leased'),"
    "'failed',(select count(*) from d3.llm_tasks where status = 'failed'),"
    "'kill_engaged',(select engaged from d4.kill_events "
    "order by evt_id desc limit 1),"
    "'kill_level',(select level from d4.kill_events order by evt_id desc limit 1))"
)


def _ledger_facts() -> dict:
    """d4 + queue + kill state. A failure is reported, never rendered as zeroes."""
    try:
        cfg = gcfg.load()
        r = compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-X",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            gcfg.value(cfg, "stores.postgres_user"),
            "-d",
            gcfg.value(cfg, "stores.postgres_db"),
            "-c",
            LEDGER_SQL,
            timeout=60,
        )
    except gcfg.ConfigError as e:
        return {"reason": f"config: {e}"}
    if r.rc != 0:
        line = (r.err or r.out).strip().splitlines()
        return {"reason": f"psql rc={r.rc}: {(line[0] if line else 'no output')[:120]}"}
    try:
        doc = json.loads(r.out.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"reason": f"unparseable psql output: {r.out.strip()[:120]}"}
    return doc if isinstance(doc, dict) else {"reason": "psql returned no object"}


def _questdb(sql: str, port: str):
    url = f"http://127.0.0.1:{port}/exec?query=" + urllib.parse.quote_plus(sql)
    with urllib.request.urlopen(url, timeout=4) as resp:
        return json.load(resp)


def _d1_facts(port) -> dict:
    """D1 write rate. `ticks` does not exist until A-9, and a hardcoded 0 rows/s for
    a store that holds nothing would be a fabricated number — so say why instead."""
    try:
        tables = _questdb("select table_name from tables()", port)
        names = {row[0] for row in tables.get("dataset", [])}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return {
            "live_table": None,
            "rows": None,
            "rows_per_s": None,
            "reason": f"QuestDB unreachable: {e}",
        }
    if "ticks" not in names:
        return {
            "live_table": None,
            "rows": None,
            "rows_per_s": None,
            "reason": (
                "no live D1 table yet (A-9): QuestDB holds "
                f"{len(names)} fixture table(s), no `ticks`"
            ),
        }
    first = _questdb("select count() from ticks", port)["dataset"][0][0]
    time.sleep(0.5)
    second = _questdb("select count() from ticks", port)["dataset"][0][0]
    return {
        "live_table": "ticks",
        "rows": second,
        "rows_per_s": round((second - first) / 0.5, 1),
        "reason": None,
    }


def _d2_facts(port) -> dict:
    """JetStream depth. Zero streams is a real measurement (A-8 has not declared
    them yet), not a missing value."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/jsz?streams=true", timeout=4
        ) as resp:
            doc = json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return {"streams": None, "messages": None, "reason": f"NATS monitor: {e}"}
    details = (doc.get("account_details") or [{}])[0].get("stream_detail") or []
    return {
        "streams": len(details),
        "messages": sum(int(s.get("state", {}).get("messages", 0)) for s in details),
        "reason": None,
    }


def _stores(cfg) -> dict:
    env = gcfg.compose_env(cfg)
    ledger = _ledger_facts()
    if "reason" in ledger:
        d4 = {"last_fill_epoch": None, "fills_60s": None, "reason": ledger["reason"]}
        queue = {
            "queued": None,
            "leased": None,
            "failed": None,
            "reason": ledger["reason"],
        }
        kill = {"engaged": None, "level": None, "reason": ledger["reason"]}
    else:
        d4 = {
            "last_fill_epoch": ledger.get("last_fill_epoch"),
            "fills_60s": ledger.get("fills_60s"),
            "reason": None,
        }
        queue = {
            "queued": ledger.get("queued"),
            "leased": ledger.get("leased"),
            "failed": ledger.get("failed"),
            "reason": None,
        }
        kill = {
            "engaged": ledger.get("kill_engaged"),
            "level": ledger.get("kill_level"),
            "reason": None,
        }
    return {
        "d4": d4,
        "queue": queue,
        "kill": kill,
        "d1": _d1_facts(env.get("QUESTDB_HTTP_PORT", "9000")),
        "d2": _d2_facts(env.get("NATS_MONITOR_PORT", "8222")),
    }


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
    add(
        "python",
        "ok" if sys.version_info >= (3, 11) else "fail",
        py + " (tomllib needs 3.11+)",
    )

    tier, tier_detail = detect_tier()
    # An unknown tier is a hard fail, not a warning: every other check below is
    # then describing a host the product does not support (A2 A-6, item 18).
    add("tier", "ok" if tier in ("A", "B", "C") else "fail", f"{tier} — {tier_detail}")

    if not COMPOSE_FILE.is_file():
        add("compose_file", "fail", f"missing {COMPOSE_FILE}")
    else:
        add("compose_file", "ok", str(COMPOSE_FILE.relative_to(REPO)))

    # A-4: one config file. Everything below reads it; a missing or malformed file
    # is a hard fail because every later check would then be checking a guess.
    cfg, config_error = None, None
    try:
        cfg = gcfg.load()
        add(
            "config_file",
            "ok",
            f"{gcfg.path_from_env().relative_to(REPO)} (tier {cfg['host']['tier']})",
        )
    except gcfg.ConfigError as e:
        config_error = str(e)
        add("config_file", "fail", str(e))

    if cfg is not None:
        want = cfg["host"]["tier"]
        if want != "auto" and want != tier:
            add("config_tier", "fail", f"host.tier={want} but this host is tier {tier}")
        else:
            add("config_tier", "ok", f"host.tier={want} (effective {tier})")

    env_file = COMPOSE_DIR / ".env"
    env = dotenv(env_file)
    if not env_file.is_file():
        add("env", "fail", "compose/.env missing — copy compose/.env.example")
    else:
        problems = gcfg.secret_problems(cfg) if cfg is not None else []
        if problems:
            add("env", "fail", "; ".join(problems))
        else:
            add("env", "ok", f"{env_file.name}: {len(env)} secret(s), redacted")

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

    # A-7 (invariant R1): the rail is the only writer. This is the only place an
    # operator sees the debt before a gate does — an undeclared direct write, and
    # how much of the registry is still legacy-with-a-reason.
    if cfg is not None and reachable:
        sql = (
            "SELECT coalesce((SELECT count(*) FROM d3.v_r1_violations), -1) || '|' || "
            "coalesce((SELECT count(*) FROM d3.legacy_writes), 0) || '|' || "
            "coalesce((SELECT count(*) FROM d3.datasets WHERE writer = 'rail'), 0)"
        )
        r = sh(
            [*gcfg.psql_argv(cfg), "-A", "-t", "-c", sql],
            timeout=30,
            cwd=COMPOSE_DIR,
            # compose interpolates `${VAR:?}` — the same rendered environment every
            # other compose call gets (A-4).
            env=gcfg.process_env(cfg),
        )
        out = (r.out or "").strip().splitlines()[-1] if r.out.strip() else ""
        if not r.ok or out.count("|") != 2:
            add("rail_writer", "skip", "d3 views absent — run `gfti install`", hard=False)
        else:
            viol, declared, rail_rows = (int(x) for x in out.split("|"))
            if viol < 0:
                add("rail_writer", "skip", "migration 007 not applied", hard=False)
            elif viol:
                add(
                    "rail_writer",
                    "fail",
                    f"{viol} dataset(s) written outside the rail with no declared "
                    "reason (A-7) — see d3.v_r1_violations",
                )
            else:
                add(
                    "rail_writer",
                    "ok",
                    f"{rail_rows} rail-written, {declared} declared legacy write(s), "
                    "0 undeclared",
                )

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
                "config": gcfg.compose_env(cfg) if cfg is not None else {},
                "secrets": redacted(env),
                "config_source": str(gcfg.path_from_env()),
                "config_error": config_error,
            }
        )
        return EXIT_OK if ok else EXIT_FAIL

    say(f"gfti {VERSION} · tier {tier} ({tier_detail})")
    for c in checks:
        mark = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "skip": "----"}[
            c["status"]
        ]
        say(f"  {mark} {c['name']:<18} {c['detail']}")
    if cfg is not None:
        say(f"\neffective config — {gcfg.path_from_env().relative_to(REPO)}")
        for k, v in gcfg.compose_env(cfg).items():
            say(f"  {k}={v}")
        if env:
            say("\nsecrets — compose/.env (redacted)")
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
    argv = ["sh", str(script)]
    if args.verify:
        argv.append("--verify")
    if args.tier:
        argv.append(f"--tier={args.tier}")
    # POSIX sh, not bash: install.sh is the one program that must run where bash
    # is absent (A2 A-6). Native Windows has no sh at all — it is tier C only
    # through WSL2, so it gets the instruction instead of a confusing OSError.
    if os.name == "nt":
        say("gfti install: native Windows is not a supported host (A0 §2).")
        say("  run install inside WSL2:  wsl -d Ubuntu -- sh bootstrap/install.sh")
        say("  the CLI alone works natively:  pwsh -File bootstrap/gfti.ps1 --help")
        return EXIT_ENV
    say(f"gfti install: sh bootstrap/install.sh {' '.join(argv[2:])}".rstrip())
    r = sh(["sh", str(script)], cwd=REPO, capture=False)
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
    """The health view (A2 A-5): every fact needed to decide, and no invented ones."""
    reachable, detail = docker_up()
    if not reachable:
        payload = {"ok": False, "error": "docker daemon unreachable", "detail": detail}
        if args.json:
            _json_out(payload)
        else:
            say(f"gfti status: docker daemon unreachable — {detail}")
        return EXIT_ENV

    try:
        cfg = gcfg.load()
    except gcfg.ConfigError as e:
        say(f"gfti status: {e}")
        return EXIT_ENV
    hb_ms = int(gcfg.value(cfg, "runtime.heartbeat_ms"))
    interval_s = round(hb_ms / 1000.0, 3)
    budget_s = HB_BUDGET_FACTOR * interval_s

    rows = ps_rows()
    names = [row.get("Name") or row.get("name") or "" for row in rows]
    inspected = inspect_services(names)

    services = []
    for row in rows:
        name = row.get("Name") or row.get("name") or "?"
        svc = row.get("Service") or row.get("service") or name
        facts = inspected.get(name, {})
        state = (
            facts.get("state") or (row.get("State") or row.get("state") or "").lower()
        )
        health = facts.get("health", "")
        paused = bool(facts.get("paused"))
        # A paused container cannot be exec'd, so its heartbeat is unreadable rather
        # than stale — but `paused` is already a down signal on its own.
        hb = heartbeat_age(name) if svc in RUNTIME_SERVICES and not paused else None
        stale_ok = hb is None or hb <= budget_s
        up = (
            state == "running" and not paused and stale_ok and health in ("", "healthy")
        )
        entry = {
            "service": svc,
            "name": name,
            "state": state,
            "health": health,
            "up": up,
            "paused": paused,
            "restart_count": int(facts.get("restart_count", 0)),
            "hb_age_s": hb,
            "stale_ok": stale_ok,
        }
        if not up:
            if paused:
                entry["why"] = "paused"
            elif state != "running":
                entry["why"] = f"state={state}"
            elif not stale_ok:
                entry["why"] = f"heartbeat {hb}s > {budget_s:g}s budget"
            elif health not in ("", "healthy"):
                entry["why"] = f"health={health}"
        services.append(entry)

    present = {s["service"] for s in services}
    missing = [s for s in ("postgres", "questdb", "nats") if s not in present]
    degraded = [s["service"] for s in services if not s["up"]]
    stores = _stores(cfg)
    ok = not missing and not degraded

    if args.json:
        _json_out(
            {
                "ok": ok,
                "services": services,
                "missing": missing,
                "degraded": degraded,
                "stores": stores,
                "interval_s": interval_s,
                "heartbeat_budget_s": round(budget_s, 3),
            }
        )
        return EXIT_OK if ok else EXIT_FAIL

    if not services:
        say("gfti status: no containers for this project — run `gfti up`")
        return EXIT_FAIL
    say(
        f"{'SERVICE':<11}{'STATE':<10}{'HEALTH':<10}{'UP':<4}{'RST':<5}"
        f"{'HB':<9}{'FILL60s':<9}{'QUEUE':<7}{'KILL':<6}{'STREAMS':<8}"
    )
    d4, queue, kill, d2 = stores["d4"], stores["queue"], stores["kill"], stores["d2"]
    for s in services:
        hb = s.get("hb_age_s")
        hb_txt = f"{hb}s" if isinstance(hb, int) else "-"
        fill = d4.get("fills_60s")
        q = queue.get("queued")
        kill_txt = "-"
        if kill.get("engaged") is True:
            kill_txt = f"ON/{kill.get('level') or '?'}"
        elif kill.get("engaged") is False:
            kill_txt = "off"
        streams = d2.get("streams")
        say(
            f"{s['service']:<11}{s['state']:<10}{(s['health'] or '-'):<10}"
            f"{'yes' if s['up'] else 'NO':<4}{s['restart_count']:<5}"
            f"{hb_txt:<9}{('-' if fill is None else fill):<9}"
            f"{('-' if q is None else q):<7}{kill_txt:<6}"
            f"{('-' if streams is None else streams):<8}"
        )
    if missing:
        say(f"missing: {', '.join(missing)}")
    if degraded:
        why = {s["service"]: s.get("why", "down") for s in services if not s["up"]}
        say("degraded: " + ", ".join(f"{k} ({v})" for k, v in why.items()))
        say("  `gfti logs <service>` for the reason")
    d1 = stores["d1"]
    last = d4.get("last_fill_epoch")
    say(
        "stores: d4 last_fill="
        + (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last))
            if isinstance(last, int)
            else "never"
        )
        + f" · queue q/l/f={queue.get('queued')}/{queue.get('leased')}/{queue.get('failed')}"
        + f" · d2 streams={d2.get('streams')}"
    )
    if d1.get("reason"):
        say(f"  d1: {d1['reason']}")
    elif d1.get("live_table"):
        say(f"  d1: {d1['live_table']} rows={d1['rows']} (+{d1['rows_per_s']}/s)")
    return EXIT_OK if ok else EXIT_FAIL


def cmd_logs(args) -> int:
    argv = ["logs", "--tail", str(args.tail)]
    if args.follow:
        argv.append("--follow")
    argv += args.service
    r = compose(*argv, capture=False)
    return r.rc


# ----------------------------------------------------------------------- config


def cmd_config(args) -> int:
    """Print the effective configuration (A2 §4 A-4 items 13–15).

    Three shapes, one source: human (default), `--json` for machines, `--sh` for a
    POSIX shell to `eval`. `--sh` is how `install.sh` gets the *same* environment
    `gfti up` uses, so the installer and the CLI cannot start different stacks.
    """
    try:
        cfg = gcfg.load()
    except gcfg.ConfigError as e:
        say(f"gfti config: {e}")
        return EXIT_ENV

    env = gcfg.compose_env(cfg)
    sec = gcfg.secrets()

    if args.sh:
        # export only what compose needs; secrets last so a config key can never
        # shadow one. Never echo a value here that a caller did not ask for.
        print(gcfg.sh_export({**env, **sec}), end="")
        return EXIT_OK

    problems = gcfg.secret_problems(cfg)
    if args.json:
        _json_out(
            {
                "ok": not problems,
                "source": str(gcfg.path_from_env()),
                "config": cfg,
                "compose_env": env,
                "secrets": redacted(sec),
                "problems": problems,
            }
        )
        return EXIT_OK if not problems else EXIT_FAIL

    say(f"config — {gcfg.path_from_env()}")
    for key in (
        "POSTGRES_PORT",
        "QUESTDB_HTTP_PORT",
        "QUESTDB_ILP_PORT",
        "NATS_PORT",
        "NATS_URL",
        "CELL_SUBJECT",
        "CELL_INTENT_PREFIX",
        "E7_SUBJECT",
        "HEARTBEAT_MS",
        "CELL_TICKS",
        "E7_EXPECT",
    ):
        say(f"  {key}={env[key]}")
    say(f"\nsecrets — {gcfg.ENV_FILE.name} (redacted)")
    for k, v in redacted(sec).items():
        say(f"  {k}={v}")
    if problems:
        for p in problems:
            say(f"  FAIL {p}")
    say(f"\nconfig: {'OK' if not problems else 'FAILED'} ({len(env)} non-secret keys)")
    return EXIT_OK if not problems else EXIT_FAIL


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


def cmd_d2(args) -> int:
    """D2 = JetStream, applied and read back (A2 A-8 items 27/30).

    `apply` is convergent: the config is the definition, so an existing stream is
    updated to match rather than assumed correct. `status` is the other direction —
    what the server holds, compared with what the file declares, because "declared"
    and "exists" are different facts and D2 shipped for months with zero streams.
    """
    try:  # same dual import shape as gfti_config above
        import d2
    except ImportError:  # pragma: no cover - package import path
        from . import d2

    try:
        cfg = gcfg.load()
        if args.action == "apply":
            out = d2.apply(cfg)
        else:
            out = d2.status(cfg)
    except (gcfg.ConfigError, d2.D2Error, OSError) as e:
        say(f"gfti d2 {args.action}: {e}")
        return EXIT_ENV

    if args.json:
        _json_out(out)
        return EXIT_OK if out.get("ok") else EXIT_FAIL

    if args.action == "apply":
        say(
            f"D2 apply: {len(out['streams_created'])} created, "
            f"{len(out['streams_updated'])} updated, "
            f"{len(out['streams_existing'])} already present"
        )
        say("declared: " + ", ".join(out["declared_streams"]))
        say("consumers: " + ", ".join(out["consumers"]))
        return EXIT_OK

    say(f"{'STREAM':<10}{'MSGS':>10}{'BYTES':>14}  SUBJECTS")
    for name, info in out["streams"].items():
        if not info.get("exists"):
            say(f"{name:<10}{'-':>10}{'-':>14}  MISSING")
            continue
        say(
            f"{name:<10}{info['messages']:>10}{info['bytes']:>14}  "
            + " ".join(info["subjects"])
        )
    for name, info in out["consumers"].items():
        state = "present" if info.get("exists") else "MISSING"
        say(f"consumer {name}: {state} (pending {info.get('pending', 0)})")
    for problem in out["problems"]:
        say(f"problem: {problem}")
    return EXIT_OK if out["ok"] else EXIT_FAIL


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
    try:
        url = os.environ.get("LEDGER_URL") or ledger_url()
    except gcfg.ConfigError as e:
        say(f"gfti kill: {e}")
        return EXIT_ENV
    r = sh(argv, env=dict(os.environ, LEDGER_URL=url), capture=False)
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

    s = sub.add_parser("tier", help="report the A0 §2 OS tier (A/B/C) of this host")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_tier)

    s = sub.add_parser("logs", help="follow or tail service logs")
    s.add_argument("service", nargs="*", help="service names (default: all)")
    s.add_argument("-f", "--follow", action="store_true")
    s.add_argument("-n", "--tail", type=int, default=80)
    s.set_defaults(fn=cmd_logs)

    s = sub.add_parser("smoke", help="fast end-to-end liveness proof")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_smoke)

    s = sub.add_parser("config", help="print the effective configuration (A2 A-4)")
    s.add_argument(
        "--json", action="store_true", help="JSON, including the raw TOML tree"
    )
    s.add_argument("--sh", action="store_true", help="POSIX export lines, for eval")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("verify", help="run the full gate suite")
    s.add_argument("--install", action="store_true", help="include G-INSTALL (slow)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser(
        "d2", help="JetStream streams: apply the declaration, read it back"
    )
    s.add_argument("action", choices=["apply", "status"])
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_d2)

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


def _safe_console() -> None:
    """A legacy Windows console (cp1252) cannot encode `→`, `·` or `—`, and the
    CLI's help text is full of them — measured on windows-latest: `gfti --help`
    died with UnicodeEncodeError instead of printing. Keep the user's encoding
    (PYTHONIOENCODING stays authoritative) but never let a glyph abort a run:
    an unrepresentable character degrades to `?`, the exit code stays honest."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None) -> int:
    _safe_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:  # argparse already printed; keep the code
        return int(e.code or 0)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
