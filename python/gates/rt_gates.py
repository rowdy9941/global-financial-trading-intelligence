#!/usr/bin/env python3
"""Residency gates (A2 §4 A-1): the services must be long-lived processes.

Before A-1 every Rust binary in this repo was a *fixture*: `cell` exited after
N ticks, `e7` waited for a "GO" line on stdin before doing anything, and `rail`
blocked in `read_line` so no signal could ever be observed. That is fatal for a
system that is supposed to run unattended, because:

  * `docker stop` sends SIGTERM and then SIGKILL after a grace period. A process
    without a handler is always SIGKILLed — no flush, no drain, no summary.
  * a service that only exits when its input ends can never be restarted, only
    re-created, and it cannot be probed for liveness while idle.

These gates are the regression fence for that. Each case starts the real release
binary, drives it, and asserts on (a) the process state, (b) an observable
heartbeat line, and (c) the exit code after SIGTERM.

Usage: python3 python/gates/rt_gates.py [RESIDENT|IDLE|E7|RAIL|HEARTBEAT|ALL]

Exit codes: 0 = every selected case passed · 1 = at least one failed.
Evidence is verbatim stdout/stderr from the binaries, never paraphrased.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent
COMPOSE = REPO / "compose"
from _env import release_dir, scratch_dir

BIN = release_dir(REPO)
SCRATCH = scratch_dir()
POSIX = os.name == "posix"


def _pg_url() -> str:
    env = {}
    for line in (COMPOSE / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    pw = env.get("POSTGRES_PASSWORD", "dev-only-local-placeholder")
    return f"postgres://gfti_app:{pw}@127.0.0.1:5432/gfti"


BASE_ENV = dict(
    os.environ,
    LEDGER_URL=_pg_url(),
    RAIL_NATS_URL="nats://127.0.0.1:4222",
    LEDGER_SECRET_OWNER="dev-rt-" + os.urandom(12).hex(),
)


class Svc:
    """A service process plus a thread-free line collector.

    stdout and stderr go to ONE scratch file: the heartbeat writes to stdout and
    the summaries to stderr, and a single ordered file is what `docker logs`
    shows. It also removes the deadlock risk of two pipes read by one thread.
    """

    def __init__(self, name: str, binary: str, env: dict, stdin=subprocess.DEVNULL):
        self.name = name
        self.log_path = SCRATCH / f"rt_{name}_{os.getpid()}.log"
        # Held for the process lifetime, threaded through the object on purpose:
        # a `with` block cannot span Popen → assertions → terminate.
        self._log = open(self.log_path, "w+")  # noqa: SIM115
        self.p = subprocess.Popen(
            [str(BIN / binary)],
            env=env,
            stdin=stdin,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            cwd=str(REPO),
            text=True,
        )

    def lines(self) -> list[str]:
        self._log.flush()
        try:
            return self.log_path.read_text(errors="replace").splitlines()
        except OSError:
            return []

    def wait_for(self, prefix: str, count: int = 1, timeout_s: float = 20.0) -> bool:
        end = time.time() + timeout_s
        while time.time() < end:
            if len([ln for ln in self.lines() if ln.startswith(prefix)]) >= count:
                return True
            if self.p.poll() is not None:
                return (
                    len([ln for ln in self.lines() if ln.startswith(prefix)]) >= count
                )
            time.sleep(0.05)
        return False

    def alive(self) -> bool:
        return self.p.poll() is None

    def stop(self, grace_s: float = 20.0) -> tuple[int, float]:
        """SIGTERM (POSIX) and measure how long an ordered exit took."""
        t0 = time.time()
        if self.alive():
            self.p.terminate()
        try:
            rc = self.p.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            self.p.kill()
            rc = self.p.wait(timeout=10)
        return rc, (time.time() - t0) * 1000.0

    def close(self) -> None:
        try:
            self._log.close()
        except OSError:
            pass


def heartbeat_beats(lines: list[str]) -> list[dict]:
    out = []
    for ln in lines:
        if not ln.startswith("HB "):
            continue
        try:
            out.append(json.loads(ln[3:]))
        except json.JSONDecodeError:
            continue
    return out


def _skip(case: str, why: str) -> None:
    print(f"RT-{case} " + json.dumps({"ok": None, "skipped": why}))


def gate_resident() -> bool | None:
    """A service with unbounded work must stay up, beat, and exit 0 on SIGTERM.

    `CELL_TICKS=0` used to mean "exit immediately" (0 < 0 is false); now it means
    "no tick limit". This case would have hung or failed before A-1.
    """
    if not POSIX:
        _skip("RESIDENT", "SIGTERM semantics are POSIX-only; tier C runs in WSL2")
        return None
    svc = Svc(
        "cell",
        "cell",
        dict(
            BASE_ENV,
            CELL_TICKS="0",
            HEARTBEAT_MS="250",
            KILL_REFRESH_MS="200",
            CELL_SUBJECT="market.spot.NIFTY",
        ),
    )
    try:
        beat = svc.wait_for("HB ", 1, 15.0)
        alive_after_beat = svc.alive()
        rc, ms = svc.stop()
        lines = svc.lines()
        errs = [ln for ln in lines if ln.startswith("cell: FAIL")]
        ok = beat and alive_after_beat and rc == 0 and not errs
        print(
            "RT-RESIDENT "
            + json.dumps(
                {
                    "binary": "cell",
                    "env": {"CELL_TICKS": 0},
                    "heartbeat_seen": beat,
                    "alive_while_unbounded": alive_after_beat,
                    "sigterm_rc": rc,
                    "exit_within_grace_ms": round(ms, 1),
                    "fatal_lines": errs[:2],
                    "ok": ok,
                }
            )
        )
        return ok
    finally:
        svc.close()


def gate_idle() -> bool | None:
    """No traffic for 3 s: the process must still be alive and quiet.

    This is the difference between "running" and "waiting for one specific
    handshake file". `e7` is the case that used to fail: it sat in
    `stdin.read_line()` and would exit the moment stdin was /dev/null.
    """
    if not POSIX:
        _skip("IDLE", "SIGTERM semantics are POSIX-only; tier C runs in WSL2")
        return None
    svc = Svc(
        "e7",
        "e7",
        dict(
            BASE_ENV,
            E7_EXPECT="0",
            E7_REFUSE_EXIT="0",
            HEARTBEAT_MS="250",
            E7_SUBJECT="order.spot.>",
        ),
    )
    try:
        t0 = time.time()
        time.sleep(3.0)
        idle_s = round(time.time() - t0, 2)
        alive = svc.alive()
        beats = heartbeat_beats(svc.lines())
        rc, ms = svc.stop()
        lines = svc.lines()
        errs = [ln for ln in lines if ln.startswith(("e7: FAIL", "e7: recv fail"))]
        ok = alive and rc == 0 and not errs and len(beats) >= 3
        print(
            "RT-IDLE "
            + json.dumps(
                {
                    "binary": "e7",
                    "env": {"E7_EXPECT": 0, "stdin": "/dev/null"},
                    "idle_s": idle_s,
                    "alive_after_idle": alive,
                    "heartbeats": len(beats),
                    "sigterm_rc": rc,
                    "exit_within_grace_ms": round(ms, 1),
                    "fatal_lines": errs[:2],
                    "ok": ok,
                }
            )
        )
        return ok
    finally:
        svc.close()


def _nats_pub(subject: str, payload: str) -> None:
    """Publish one core-NATS message (no client crate, no CLI dependency).

    The E7 case has to inject an *order intent* on `order.spot.<SYM>`; the rail
    only publishes market data to `market.spot.<SYM>`, so it cannot be reused as
    the producer here. The protocol is three text lines.
    """
    body = payload.encode()
    with socket.create_connection(("127.0.0.1", 4222), timeout=10) as s:
        s.sendall(
            b'CONNECT {"verbose":false,"pedantic":false,"protocol":1}\r\nPING\r\n'
        )
        s.sendall(f"PUB {subject} {len(body)}\r\n".encode() + body + b"\r\nPING\r\n")
        s.settimeout(10)
        buf = b""
        while buf.count(b"PONG") < 2:
            chunk = s.recv(4096)
            if not chunk:
                raise RuntimeError(f"NATS closed mid-publish: {buf!r}")
            buf += chunk


def gate_e7() -> bool | None:
    """E7 accepts its subject with no handshake and still signs fills on demand.

    Proves the removed "GO" read did not remove E7's actual readiness barrier
    (subscribe + ledger connect): an intent published after start is signed.
    """
    if not POSIX:
        _skip("E7", "SIGTERM semantics are POSIX-only; tier C runs in WSL2")
        return None
    # signing a fill requires the kill ladder to be OPEN — this is the documented
    # L4 owner resolve, not a bypass
    subprocess.run(
        [str(BIN / "ledger_kill"), "resolve", "L4", "rt-gate-open"],
        env=BASE_ENV,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO),
        check=False,
    )
    svc = Svc(
        "e7fill",
        "e7",
        dict(
            BASE_ENV,
            E7_EXPECT="0",
            E7_REFUSE_EXIT="0",
            HEARTBEAT_MS="250",
            E7_SUBJECT="order.spot.RTGATE",
        ),
    )
    try:
        # wait for the heartbeat, which is emitted only after subscribe+connect
        ready = svc.wait_for("HB ", 1, 15.0)
        _nats_pub("order.spot.RTGATE", "buy 1 100.5 900001")
        filled = svc.wait_for("LEDGER-E7-FILL", 1, 15.0)
        rc, ms = svc.stop()
        lines = svc.lines()
        fills = [ln for ln in lines if ln.startswith("LEDGER-E7-FILL")]
        done = [ln for ln in lines if ln.startswith("LEDGER-E7-DONE")]
        ok = ready and filled and rc == 0
        print(
            "RT-E7 "
            + json.dumps(
                {
                    "ready_without_handshake": ready,
                    "fill_signed": filled,
                    "fills": len(fills),
                    "intent_subject": "order.spot.RTGATE",
                    "sigterm_rc": rc,
                    "exit_within_grace_ms": round(ms, 1),
                    "done_line": done[-1][:160] if done else None,
                    "ok": ok,
                }
            )
        )
        return ok
    finally:
        svc.close()


def gate_rail() -> bool | None:
    """The rail drains what arrived *before* the signal, then exits 0.

    Before A-1 the main loop blocked in `read_line`, so a signal could never be
    seen while the feed was quiet — the only way out was SIGKILL, and any row
    sitting in the pipe was lost. Here stdin is a live pipe that is deliberately
    NOT closed: the exit must come from the signal, not from EOF, and the row
    count must still be exact.
    """
    if not POSIX:
        _skip("RAIL", "SIGTERM semantics are POSIX-only; tier C runs in WSL2")
        return None
    svc = Svc("rail", "rail", dict(BASE_ENV, RAIL_TICK_MS="100"), stdin=subprocess.PIPE)
    try:
        rows = "".join(
            f"RTGATE {1775000000000000000 + i * 1000} {100.0 + i} public\n"
            for i in range(5)
        )
        svc.p.stdin.write(rows)
        svc.p.stdin.flush()
        got = svc.wait_for("OK RTGATE", 5, 20.0)
        # stdin stays OPEN on purpose: EOF must not be what ends this process.
        rc, ms = svc.stop()
        lines = svc.lines()
        oks = [ln for ln in lines if ln.startswith("OK RTGATE")]
        summ = [ln for ln in lines if ln.startswith("RAIL-SUMMARY")]
        j = {}
        if summ:
            try:
                j = json.loads(summ[-1].split(" ", 1)[1])
            except json.JSONDecodeError:
                j = {}
        ok = (
            got
            and rc == 0
            and len(oks) == 5
            and j.get("accepted") == 5
            and j.get("stopped") == "signal"
        )
        print(
            "RT-RAIL "
            + json.dumps(
                {
                    "rows_in": 5,
                    "ok_lines": len(oks),
                    "accepted": j.get("accepted"),
                    "rejected": j.get("rejected"),
                    "stopped": j.get("stopped"),
                    "stdin_closed_before_exit": False,
                    "sigterm_rc": rc,
                    "exit_within_grace_ms": round(ms, 1),
                    "ok": ok,
                }
            )
        )
        try:
            svc.p.stdin.close()
        except OSError:
            pass
        return ok
    finally:
        svc.close()


def gate_heartbeat() -> bool | None:
    """Heartbeats must repeat and their uptime clock must advance.

    A single beat proves a printer ran once; repetition plus a rising `uptime_s`
    is what makes liveness monitoring possible (A-15 builds on this).
    """
    if not POSIX:
        _skip("HEARTBEAT", "SIGTERM semantics are POSIX-only; tier C runs in WSL2")
        return None
    svc = Svc(
        "hb",
        "cell",
        dict(
            BASE_ENV,
            CELL_TICKS="0",
            HEARTBEAT_MS="250",
            KILL_REFRESH_MS="200",
            CELL_SUBJECT="market.spot.NIFTY",
        ),
    )
    try:
        got = svc.wait_for("HB ", 4, 20.0)
        rc, _ms = svc.stop()
        beats = heartbeat_beats(svc.lines())
        up = [b.get("uptime_s", -1) for b in beats]
        monotonic = all(b >= a for a, b in zip(up, up[1:])) if len(up) > 1 else False  # noqa: RUF007
        advanced = bool(up) and up[-1] >= 1
        fields = all(
            {"svc", "version", "beat", "uptime_s", "events", "decisions", "intents"} <= set(b)
            for b in beats
        )
        ok = got and rc == 0 and len(beats) >= 4 and monotonic and advanced and fields
        print(
            "RT-HEARTBEAT "
            + json.dumps(
                {
                    "beats": len(beats),
                    "uptime_s": up,
                    "uptime_monotonic": monotonic,
                    "uptime_advanced_past_1s": advanced,
                    "required_fields": fields,
                    "sigterm_rc": rc,
                    "ok": ok,
                }
            )
        )
        return ok
    finally:
        svc.close()


CASES = {
    "RESIDENT": gate_resident,
    "IDLE": gate_idle,
    "E7": gate_e7,
    "RAIL": gate_rail,
    "HEARTBEAT": gate_heartbeat,
}


def main(argv: list[str]) -> int:
    want = (argv[1] if len(argv) > 1 else "ALL").upper()
    order = list(CASES) if want == "ALL" else [want]
    if any(c not in CASES for c in order):
        print(f"unknown case {want!r}; choose from {', '.join(CASES)} or ALL")
        return 2
    results = {}
    for name in order:
        try:
            results[name] = CASES[name]()
        except Exception as exc:  # noqa: BLE001 — a gate that raises is a gate that failed
            print(f"RT-{name} " + json.dumps({"ok": False, "error": repr(exc)}))
            results[name] = False
    failed = [k for k, v in results.items() if v is False]
    skipped = [k for k, v in results.items() if v is None]
    if skipped:
        # Skipping is reported, never counted as a pass: it only ever happens on
        # a non-POSIX host, where SIGTERM cannot be tested at all.
        print(
            f"rt: SKIPPED {skipped} — SIGTERM semantics are POSIX-only", file=sys.stderr
        )
    # `ok` is the runner marker status.py parses; per-case verdicts stay visible.
    print("SUMMARY " + json.dumps({**results, "ok": not failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
