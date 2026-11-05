#!/usr/bin/env python3
"""G-STATUS — one command that proves, or refuses to prove, the A0 §7 gate set.

Why this exists: A0 §7 defines eight release gates and states that "no gate can be
waived by plan text; a green CI badge is not a gate pass". Until now each gate was a
separate runner a human had to remember to invoke, so "everything is green" was a
claim assembled by hand from older runs. This invokes every runner **now**, maps each
result onto its A0 gate, and prints one table.

Honesty rules enforced in code, not prose:

  * a gate is `ok` only when every component actually ran and passed in THIS
    invocation — nothing is cached or reused from an earlier run;
  * a component whose external prerequisite is absent (the ~28 GB Kraken archive, the
    owner's broker credentials, an unpushed/changed commit) is `blocked` with the
    reason: never silently skipped, never counted as passed;
  * an expensive gate that was not requested is `not run`, which is also not a pass.

Usage:
  uv run --project python/train python3 python/gates/status.py            # fast gates
  uv run --project python/train python3 python/gates/status.py --full     # + store/ledger
  uv run --project python/train python3 python/gates/status.py --install  # + G-INSTALL x2
  uv run --project python/train python3 python/gates/status.py --json     # JSON only

Exit code 1 when a gate that CAN be closed on this box fails. Blocked / not-run gates
do not fail the run — declaring them is the point of the distinction.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATES = REPO / "python" / "gates"
sys.path.insert(0, str(GATES))
from _env import config_env  # needs GATES on sys.path first

SUMMARY = re.compile(r"^SUMMARY (\{.*\})$", re.MULTILINE)
W1 = re.compile(r"^W1-GATES (\{.*\})$", re.MULTILINE)
PILOT = re.compile(r"^PILOT-E2E-SUITE (\{.*\})$", re.MULTILINE)
RESULT = re.compile(r"test result: \w+\. (\d+) passed; (\d+) failed")

NOT_RUN = "not run (pass --full)"
OFF = "not run (pass --install)"


@dataclass
class Part:
    """One command's contribution to a gate."""

    label: str
    ok: bool
    detail: str
    argv: list[str] = field(default_factory=list)
    blocked: bool = False
    unrun: bool = False
    payload: dict | None = None
    log: str = ""


@dataclass
class Gate:
    gid: int
    name: str
    what: str
    parts: list[Part] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.ok:
            return "ok"
        if any(not p.ok and not p.blocked and not p.unrun for p in self.parts):
            return "FAIL"
        return "blocked" if any(p.blocked for p in self.parts) else "not-run"

    @property
    def ok(self) -> bool:
        return bool(self.parts) and all(p.ok for p in self.parts)


LOGS = Path(tempfile.gettempdir()) / "gfti-gate-status"


def _dump(label: str, text: str) -> str:
    """Persist a runner's raw output; a gate failure that leaves no trace is useless.

    Observed 2026-11-04: W0B FANOUT reported False inside one aggregator run and
    True standalone minutes later. Without the runner's stdout there is no way to
    tell a real regression from a load spike, so every invocation is kept.
    """
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / (re.sub(r"[^a-zA-Z0-9_.-]+", "_", label).strip("_") + ".log")
    try:
        path.write_text(text)
    except OSError:
        return ""
    return str(path)


def run(
    argv: list[str],
    cwd: Path | None = None,
    label: str = "",
    env: dict | None = None,
) -> tuple[int, str]:
    proc = subprocess.run(
        argv,
        cwd=str(cwd or REPO),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if label:
        _dump(label, out)
    return proc.returncode, out


def run_stdin(
    argv: list[str],
    data: bytes,
    cwd: Path | None = None,
    label: str = "",
    env: dict | None = None,
) -> tuple[int, str]:
    proc = subprocess.run(
        argv,
        cwd=str(cwd or REPO),
        input=data,
        capture_output=True,
        check=False,
        env=env,
    )
    out = proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")
    if label:
        _dump(label, out)
    return proc.returncode, out


def _payload(pattern: re.Pattern[str], text: str) -> dict | None:
    m = pattern.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def gate_runner(
    name: str, case: str = "ALL", parser: re.Pattern[str] = SUMMARY
) -> Part:
    """Invoke one of the repo's gate runners and require its own pass marker."""
    argv = [sys.executable, str(GATES / name), case]
    rc, out = run(argv, label=f"{name}-{case}")
    log = str(
        LOGS / (re.sub(r"[^a-zA-Z0-9_.-]+", "_", f"{name}-{case}").strip("_") + ".log")
    )
    payload = _payload(parser, out)
    if payload is None:
        return Part(
            label=f"{name} {case}",
            argv=argv,
            ok=False,
            detail="no pass marker in output (runner crashed or changed shape)",
            log=log,
        )
    failed = [k for k, v in payload.items() if v is False]
    ok = (payload.get("ok") is True) if "ok" in payload else not failed
    return Part(
        label=f"{name} {case}",
        argv=argv,
        ok=bool(ok) and rc == 0,
        detail="all cases true"
        if ok and rc == 0
        else f"failed={failed or 'no ok marker'} rc={rc}",
        payload=payload,
    )


def ambient() -> str:
    """Record the machine state a timing gate ran under (tier A here is 4 cores)."""
    try:
        one, five, _ = os.getloadavg()
    except OSError:
        return "load unknown"
    return f"load {one:.2f}/{five:.2f} on {os.cpu_count() or '?'} cores"


def explain(part: Part, marker: str) -> Part:
    """Attach a w0b sub-gate's own numbers to its verdict — pass or fail.

    A bare `FANOUT=True` is as useless as `FANOUT=False`: this gate has measured
    1.52-2.50 ms against a 2.00 ms budget on this 4-core box, so the verdict alone
    hides whether it passed with headroom or by 20 µs. Failures matter for the same
    reason — "the rail dropped prints" and "the p99 budget was missed under load"
    are different problems, and the sub-gate prints JSON that tells them apart.

    The verdict is never changed here, only made legible. A missing or unparsable
    payload leaves the part untouched rather than inventing numbers.
    """
    if not part.log:
        return part
    try:
        text = Path(part.log).read_text()
    except OSError:
        return part
    line = next((ln for ln in text.splitlines() if ln.startswith(marker)), None)
    payload = _payload(
        re.compile(rf"^{re.escape(marker)} (\{{.*\}})$", re.MULTILINE), text or ""
    )
    if not line or payload is None:
        return part
    if marker == "W0B-G5-FANOUT":
        cells = payload.get("cells", [])
        delivered = (
            all(c.get("received") == c.get("expected") for c in cells)
            if cells
            else False
        )
        part.detail = (
            f"{'all rows delivered' if delivered else 'ROWS MISSING'}; "
            f"p99 {payload.get('worst_p99_ns', 0) / 1e6:.2f} ms vs "
            f"{payload.get('budget_ns', 0) / 1e6:.2f} ms budget "
            f"(p50 {min((c.get('p50', 0) for c in cells), default=0) / 1e3:.0f} µs; "
            f"subs ready {payload.get('subs_ready_ms', '?')} ms); "
            f"{ambient()}"
        )
        return part
    if marker == "W0B-G6-WATCHDOG":
        part.detail = (
            f"failclosed_rc={payload.get('failclosed_rc')} "
            f"all_failsafe={payload.get('failclosed_all_failsafe')} "
            f"engine_sampled_engaged="
            f"{(payload.get('state_reached') or {}).get('watchdog_sampled_engaged')} "
            f"open_after_resolve_seen={payload.get('open_after_resolve_seen')}"
        )
    return part


def from_payload(
    payload: dict | None,
    key: str,
    label: str,
    argv: list[str] | None = None,
    enabled: bool = True,
    log: str = "",
) -> Part:
    if not enabled:
        return Part(label=label, ok=False, unrun=True, detail=NOT_RUN, argv=argv or [])
    if payload is None:
        return Part(
            label=label,
            ok=False,
            detail="parent gate produced no result",
            argv=argv or [],
        )
    ok = payload.get(key) is True
    return Part(
        label=label,
        ok=ok,
        detail="true" if ok else f"{payload.get(key)!r}",
        argv=argv or [],
        log=log,
    )


def cargo_test(crate: str) -> Part:
    """A crate's tests pass only if they EXIST: `cargo test` exits 0 with 0 tests run.

    cargo prints one `test result:` line per target and most targets are empty, so
    counts are summed rather than read off the last line. Otherwise a crate whose
    only non-empty target ran first would report "0 passed" — and an empty target
    would look green, which is the kind of fake evidence this file exists to stop.
    """
    argv = ["cargo", "test", "-p", crate, "--quiet"]
    rc, out = run(argv, cwd=REPO / "rust", label=f"cargo-test-{crate}")
    counts = [(int(p), int(f)) for p, f in RESULT.findall(out)]
    passed = sum(p for p, _ in counts)
    failed = sum(f for _, f in counts)
    if not counts:
        detail = f"no test result lines (rc={rc})"
    elif passed == 0:
        detail = "0 tests executed — a green with no tests proves nothing"
    else:
        detail = f"{passed} passed, {failed} failed across {len(counts)} targets"
    return Part(
        label=f"cargo test -p {crate}",
        argv=argv,
        ok=rc == 0 and failed == 0 and passed > 0,
        detail=detail,
    )


# A-4: compose interpolates `${VAR:?}`, so every compose call needs the rendered
# environment. One reader, one value: configs/gfti.toml.
COMPOSE_ENV = config_env(REPO)
PG_USER = COMPOSE_ENV.get("POSTGRES_USER", "gfti_app")
PG_DB = COMPOSE_ENV.get("POSTGRES_DB", "gfti")


def migrations_twice() -> Part:
    """G-STORE requires the migrations to reapply; they must be idempotent.

    Files go in on STDIN, exactly as bootstrap/install.sh and the CI job do it — the
    container mounts no `/sql`, so `psql -f /sql/x.sql` fails (an earlier version of
    this function did that and produced a false G-STORE failure).
    """
    files = sorted((REPO / "compose" / "sql").glob("00*.sql"))
    argv = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        PG_USER,
        "-d",
        PG_DB,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    ok, detail = True, f"{len(files)} files applied twice, no error"
    for i in (1, 2):
        for f in files:
            rc, out = run_stdin(
                argv,
                f.read_bytes(),
                cwd=REPO / "compose",
                label=f"migrate{i}-{f.stem}",
                env=COMPOSE_ENV,
            )
            if rc != 0:
                ok, detail = (
                    False,
                    f"run {i} {f.name}: {out.strip().splitlines()[-1][:140]}",
                )
                break
        if not ok:
            break
    return Part(label="migrations x2", argv=argv, ok=ok, detail=detail)


def d3_verified(dataset_key: str) -> Part:
    """A dataset is training-eligible only when its D3 row is verified."""
    sql = f"SELECT verified, rows FROM d3.datasets WHERE dataset_key = '{dataset_key}';"
    argv = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        PG_USER,
        "-d",
        PG_DB,
        "-A",
        "-t",
        "-c",
        sql,
    ]
    rc, out = run(argv, cwd=REPO / "compose", env=COMPOSE_ENV)
    value = out.strip()
    return Part(
        label=f"D3 {dataset_key}",
        argv=argv,
        ok=rc == 0 and value.split("|")[0].strip() in ("t", "true"),
        detail=value or f"no row (rc={rc})",
    )


def kraken_archive() -> Part:
    """Blocked by a ~28 GB external archive; the gate itself refuses to fetch it."""
    marker = REPO / "d0" / "kraken"
    present = marker.exists() and any(marker.rglob("*.parquet"))
    return Part(
        label="KRAKEN_FULL_TRADES",
        ok=present,
        blocked=not present,
        detail="archive present"
        if present
        else "~28 GB archive not fetched (owner action, not a code defect)",
    )


def p2_chain() -> Part:
    return Part(
        label="P2 verified D3 row",
        ok=False,
        blocked=True,
        detail="no NIFTY option-chain dataset: NSE endpoint 404 from this box; needs owner broker credentials",
    )


def p3_identity() -> Part:
    return Part(
        label="P3 verified D3 row",
        ok=False,
        blocked=True,
        detail="Gold venue/feed identity not frozen (owner decision)",
    )


def ci_evidence() -> Part:
    argv = [
        "gh",
        "run",
        "list",
        "--limit",
        "1",
        "--json",
        "databaseId,conclusion,headSha",
    ]
    rc, out = run(argv)
    if rc != 0:
        return Part(
            label="CI newest run",
            argv=argv,
            ok=False,
            blocked=True,
            detail="gh unavailable/offline",
        )
    try:
        row = json.loads(out)[0]
    except (json.JSONDecodeError, IndexError):
        return Part(
            label="CI newest run",
            argv=argv,
            ok=False,
            blocked=True,
            detail="no CI run found",
        )
    head = run(["git", "rev-parse", "HEAD"])[1].strip()
    same = row.get("headSha") == head
    return Part(
        label="CI newest run",
        argv=argv,
        ok=row.get("conclusion") == "success" and same,
        detail=f"{row.get('conclusion')} on {str(row.get('headSha'))[:7]}"
        + ("" if same else f" — stale: tree HEAD is {head[:7]}"),
    )


def install_twice() -> Part:
    argv = ["bash", str(REPO / "bootstrap" / "install.sh"), "--verify"]
    ok, detail = True, "two consecutive runs ok"
    for i in (1, 2):
        rc, out = run(argv)
        if rc != 0 or "install: OK" not in out:
            ok = False
            detail = f"run {i}: {out.strip().splitlines()[-1][:150]}"
            break
    return Part(label="install.sh --verify x2", argv=argv, ok=ok, detail=detail)


def build(full: bool, install: bool) -> list[Gate]:
    g1 = Gate(1, "G-INSTALL", "clean install twice on tier A")
    # The CLI contract is cheap (~8 s, no DB, no Docker) and it is the one thing a
    # fresh clone can still get wrong: a front door that advertises a route it does
    # not have. It runs in every mode, before the optional slow halves.
    cli = gate_runner("cli_gates.py")
    # A-4: one config file, and the proof that it is the source. Cheap (~4 s) and it
    # is the only case that can catch a `:-` fallback creeping back into compose.
    cfg = gate_runner("config_gates.py")
    # A-5: the health view is the surface an operator actually reads, so it belongs
    # in the gate that certifies the installed product rather than in a store gate.
    health = gate_runner("health_gates.py")
    g1.parts = [
        from_payload(cli.payload, "ok", "gfti CLI contract (A2 A-3)", log=cli.log),
        from_payload(cfg.payload, "ok", "one config file (A2 A-4)", log=cfg.log),
        from_payload(health.payload, "ok", "health view (A2 A-5)", log=health.log),
        # Placeholder only. The two clean installs are run at the END of build(),
        # because they saturate the CPU and would poison FANOUT's 2 ms p99 budget.
        Part(label="install.sh --verify x2", ok=False, unrun=True, detail=OFF),
    ]

    # one w0b invocation feeds both G-STORE and G-ENGINE: the runner takes ~3 min
    w0b = gate_runner("w0b_gates.py") if full else None

    g2 = Gate(2, "G-STORE", "migrations reapply · append-only · kill cascade")
    g2.parts = [
        migrations_twice()
        if full
        else Part(label="migrations x2", ok=False, unrun=True, detail=NOT_RUN),
        from_payload(
            w0b.payload if w0b else None, "APPEND", "w0b append-only", enabled=full
        ),
        from_payload(
            w0b.payload if w0b else None, "RESTART", "w0b RESTART x10", enabled=full
        ),
        from_payload(
            w0b.payload if w0b else None, "RECON", "w0b sign-recon", enabled=full
        ),
        gate_runner("d1_gates.py")
        if full
        else Part(label="d1_gates.py ALL", ok=False, unrun=True, detail=NOT_RUN),
    ]

    g3 = Gate(
        3, "G-RAIL", "ordering · duplicates · gap-in-lookahead · stale feed · fanout"
    )
    g3.parts = [cargo_test("rail")]
    if full:
        # FANOUT is the one timing-sensitive pin in W0b: a 2 ms p99 budget. Inside
        # `w0b_gates.py ALL` it runs immediately after the 10x Postgres restart cycle,
        # and that costs it its margin — measured across aggregator runs p50 rose from
        # 320-347 us (standalone) to 416-441 us, with p99 crossing to 2.36 ms, while
        # all 20,000 rows were still delivered every time. That is ambient CPU/IO
        # contention, not a rail regression, so it gets its own process after the
        # restarts have settled, and the ambient load is recorded with the number.
        fanout = gate_runner("w0b_gates.py", "FANOUT")
        g3.parts.append(
            explain(
                from_payload(
                    fanout.payload,
                    "FANOUT",
                    "w0b FANOUT (G5)",
                    log=fanout.log,
                ),
                "W0B-G5-FANOUT",
            )
        )
    else:
        g3.parts.append(
            Part(label="w0b FANOUT (G5)", ok=False, unrun=True, detail=NOT_RUN)
        )

    g4 = Gate(
        4, "G-ENGINE", "E1–E7 contracts incl. kill cascade + reconcile-before-order"
    )
    g4.parts = [
        explain(
            from_payload(
                w0b.payload if w0b else None,
                "ENGINE",
                "w0b ENGINE",
                enabled=full,
                log=w0b.log if w0b else "",
            ),
            "W0B-G7-ENGINE",
        ),
        explain(
            from_payload(
                w0b.payload if w0b else None,
                "KILL",
                "w0b kill cascade",
                enabled=full,
                log=w0b.log if w0b else "",
            ),
            "W0B-G2-KILL-DRILL",
        ),
        cargo_test("exec"),
        # A-1: residency. cell/e7/rail must behave as long-lived processes —
        # unbounded work stays up, idle means idle (not "waiting for a handshake
        # file"), SIGTERM produces an ordered exit 0 with a drained queue, and the
        # heartbeat repeats with an advancing clock. Before A-1 every one of those
        # failed; this is the fence that keeps them fixed.
        gate_runner("rt_gates.py")
        if full
        else Part(label="rt_gates.py ALL", ok=False, unrun=True, detail=NOT_RUN),
    ]

    g5 = Gate(5, "G-DATA", "D3 verified + gap coverage + hash before any training")
    g5.parts = [gate_runner("b1_gates.py", "CAPTURE"), kraken_archive()]
    for key in (
        "hyperliquid/BTC-USDC/1m",
        "hyperliquid/BTC-USDC/1d",
        "hyperliquid/BTC-USDC/1d-derived",
        "hyperliquid/BTC-USD/1d",
    ):
        g5.parts.append(d3_verified(key))

    g6 = Gate(
        6, "G-RESEARCH", "CPCV/DSR fail-closed · training lock · artifact negatives"
    )
    g6.parts = [gate_runner("w1_gates.py", parser=W1)]

    g7 = Gate(
        7, "G-PILOT-E2E", "fixture spine, then real-registered spine, deterministic"
    )
    g7.parts = [gate_runner("pilot_e2e.py", parser=PILOT)]
    archive = REPO / "d0" / "hyperliquid" / "BTC-USDC" / "1m.parquet"
    if archive.exists():
        argv = [sys.executable, str(GATES / "pilot_e2e.py"), "--real", "P1"]
        rc, out = run(argv)
        payload = _payload(PILOT, out)
        g7.parts.append(
            Part(
                label="pilot_e2e --real P1",
                argv=argv,
                ok=rc == 0 and bool(payload and payload.get("ok")),
                detail="registered-1m arm passed"
                if payload and payload.get("ok")
                else f"rc={rc}",
            )
        )
    else:
        g7.parts.append(
            Part(
                label="pilot_e2e --real P1",
                ok=False,
                blocked=True,
                detail="d0/ archive absent",
            )
        )
    g7.parts += [p2_chain(), p3_identity()]

    g8 = Gate(8, "G-CI-OS", "every tier's matrix job green on the release commit")
    g8.parts = [ci_evidence()]

    # G-INSTALL runs LAST: `install.sh` does a release build of 28 crates and two
    # pilot-E2E passes, and W0B FANOUT has a 2 ms p99 budget. Measured 2026-11-04:
    # FANOUT came back False when the installs had just saturated the CPU, and True
    # standalone and in a full run where they had not. Order the heavy work after the
    # timing-sensitive gate instead of blaming the rail for a load spike.
    if install:
        # Run the two clean installs LAST (see above), then swap the placeholder for
        # the result. Two wrong shapes preceded this one, both measured 2026-11-05:
        # *replacing* the list dropped the CLI and config contracts from release
        # certification, and *appending* ran install.sh four times while the filter
        # deleted every part with that label — so `G-INSTALL` reported OK with only
        # two parts and the clean-install claim carried no evidence at all.
        done = install_twice()
        g1.parts = [
            done if (p.label == done.label and p.unrun) else p for p in g1.parts
        ]
    return [g1, g2, g3, g4, g5, g6, g7, g8]


def report(gates: list[Gate]) -> dict:
    rows = [
        {
            "gate": f"{g.gid} {g.name}",
            "status": g.status,
            "what": g.what,
            "parts": [
                {
                    "label": p.label,
                    "ok": p.ok,
                    "blocked": p.blocked,
                    "unrun": p.unrun,
                    "detail": p.detail,
                }
                for p in g.parts
            ],
        }
        for g in gates
    ]
    return {
        "gates": rows,
        "ok": all(r["status"] == "ok" for r in rows),
        "closable_failures": [r["gate"] for r in rows if r["status"] == "FAIL"],
        "blocked": [r["gate"] for r in rows if r["status"] == "blocked"],
        "not_run": [r["gate"] for r in rows if r["status"] == "not-run"],
    }


def main() -> None:
    argv = sys.argv[1:]
    out = report(build(full="--full" in argv, install="--install" in argv))
    if "--json" not in argv:
        for row in out["gates"]:
            print(f"{row['status'].upper():8} {row['gate']}: ", end="")
            print(" | ".join(f"{p['label']}={p['detail']}" for p in row["parts"]))
        print()
    print("GATE-STATUS " + json.dumps(out, sort_keys=True))
    raise SystemExit(1 if out["closable_failures"] else 0)


if __name__ == "__main__":
    main()
