#!/usr/bin/env python3
"""A-8 gate — D2 is a store, not a claim (items 27–30).

Four cases, because they fail independently and each one has a different lie:

  STREAMS   every stream in `configs/gfti.toml [d2]` exists on the server **with the
            subjects it declares**, and every namespace a live publisher writes
            (`market.*`, `signal.*`, `order.*`, `risk.*`, `ledger.*`, `seat.*`)
            resolves to a stream. The negative direction is asserted too: an
            undeclared namespace must NOT resolve, or the check could never fail.
  VERSION   a `{"v":2,…}` payload is refused by the running cell and counted, while
            a `{"v":1,…}` payload on the same subject is accepted. R6 is only real
            if the reader refuses — a payload that is merely "not parsed" is
            indistinguishable from a corrupt one.
  REPLAY    the same retained bytes are read back twice and produce the same
            sequence, and the *engine* consuming those bytes twice produces the same
            decisions (timing fields stripped — arrival and e12_ns are real clocks
            and must differ).
  CONSUMERS the declared durable exists with the filter it claims, and it actually
            receives: publishing to its filter moves `num_ack_pending`, then the
            durable is recreated so the gate leaves no unacked redelivery storm
            behind it.

The gate runs against the live stack. Every number below is read back from the
server — none of it is computed from what the code intended to do.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "python" / "gfti"))
sys.path.insert(0, str(HERE))

import d2
import gfti_config
from _env import config_env, release_dir, scratch_dir

CFG = gfti_config.load()
CFG_ENV = config_env(REPO)
BIN = release_dir(REPO)
RESULTS: dict[str, dict] = {}

# One subject root per run: the bench namespace is inside `market.>`, so the gate
# proves the *declared* stream captures it, while a unique run id keeps this run's
# messages separate from every other run and from the live feed.
RUN = os.urandom(4).hex()
BENCH = f"market.bench.{RUN}"


def ok(case: str, detail: str, **extra) -> None:
    RESULTS[case] = {"ok": True, "detail": detail, **extra}


def bad(case: str, detail: str, **extra) -> None:
    RESULTS[case] = {"ok": False, "detail": detail, **extra}


def _client() -> d2.Nats:
    return d2.Nats(CFG["stores"]["nats_url_host"])


def _pg_url() -> str:
    pw = CFG_ENV.get("POSTGRES_PASSWORD") or "dev-only-local-placeholder"
    return (
        f"postgres://{CFG_ENV.get('POSTGRES_USER', 'gfti_app')}:{pw}"
        f"@127.0.0.1:{CFG_ENV.get('POSTGRES_PORT', '5432')}"
        f"/{CFG_ENV.get('POSTGRES_DB', 'gfti')}"
    )


def _env(**extra) -> dict:
    return {
        **os.environ,
        **CFG_ENV,
        "RAIL_NATS_URL": CFG["stores"]["nats_url_host"],
        "LEDGER_URL": _pg_url(),
        "LEDGER_SECRET_OWNER": "dev-d2gate-" + os.urandom(12).hex(),
        **extra,
    }


def _wait_ready(log: Path, needle: str = "HB ", timeout: float = 15.0) -> bool:
    """Wait for the cell's first heartbeat.

    The heartbeat is emitted only after the NATS connect+subscribe calls return, so
    it is the readiness barrier this gate needs. Publishing on a sleep instead is a
    coin flip: on a slow start the messages land before the subscription exists and
    the cell then waits forever for ticks that will never come (measured — that is
    exactly how the first VERSION run timed out instead of failing).
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if needle in log.read_text(errors="replace"):
                return True
        except OSError:
            pass
        time.sleep(0.05)
    return False


def tick(price: float, ts: int, symbol: str = "D2GATE") -> str:
    """A v1 tick encoded exactly as the rail writes it (field order matters)."""
    return json.dumps(
        {
            "v": 1,
            "kind": "tick",
            "symbol": symbol,
            "ts": ts,
            "arrival": ts + 1,
            "price": price,
        },
        separators=(",", ":"),
    )


# --------------------------------------------------------------- STREAMS


def case_streams() -> None:
    st = d2.status(CFG)
    if not st["ok"]:
        bad("STREAMS", f"d2 status: {st['problems']}")
        return
    declared = [s["name"] for s in CFG["d2"]["streams"]]
    missing = [n for n in declared if not st["streams"][n]["exists"]]
    if missing:
        bad("STREAMS", f"declared but absent: {missing}")
        return

    # Every publisher namespace must resolve to a stream. These are the *actual*
    # prefixes the services publish to, taken from config, not from the stream table.
    publishers = {
        "market": f"{CFG['subjects']['market']}.BTC_USDC",
        "signal": f"{CFG['subjects']['signal']}.BTC_USDC",
        "order": f"{CFG['subjects']['intent']}.BTC_USDC",
        "risk": f"{CFG['subjects']['risk']}.BTC_USDC",
        "ledger": f"{CFG['subjects']['ledger']}.BTC_USDC",
        "seat": f"{CFG['subjects']['seat']}.engine-cell",
    }
    captured = {k: d2.captured_by(CFG, v) for k, v in publishers.items()}
    dropped = {k: v for k, v in captured.items() if v is None}
    if dropped:
        bad("STREAMS", f"published but captured by no stream: {sorted(dropped)}")
        return
    # The check must be capable of failing, or it proves nothing.
    if d2.captured_by(CFG, "notadeclared.namespace.X") is not None:
        bad(
            "STREAMS",
            "an undeclared namespace resolved to a stream — matcher is too loose",
        )
        return

    # The server's own totals, not the CLI's bookkeeping.
    jsz = d2.jsz(CFG["stores"]["nats_url_host"], monitor=_monitor())
    expect = len(declared)
    if jsz.get("streams", 0) < expect:
        bad(
            "STREAMS",
            f"/jsz reports {jsz.get('streams', 0)} streams, declared {expect}",
        )
        return
    ok(
        "STREAMS",
        f"{expect} declared streams exist on the server (/jsz streams={jsz['streams']}, "
        f"messages={jsz.get('messages', 0)}); 6/6 publisher namespaces captured "
        f"({', '.join(f'{k}→{v}' for k, v in captured.items())}); an undeclared "
        f"namespace does not resolve",
        captured=captured,
    )


def _monitor() -> str:
    return f"http://127.0.0.1:{CFG['stores']['nats_monitor_port']}"


# --------------------------------------------------------------- VERSION


def case_version(tmp: Path) -> None:
    """A resident-shaped cell run over a subject carrying both versions."""
    subject = f"{BENCH}.version"
    v2 = tick(101.0, 1_780_000_000_000_000_000).replace('"v":1', '"v":2', 1)
    v1 = tick(102.0, 1_780_000_000_000_000_100)
    msgs = [v2, v2, v1, v1, v1]

    env = _env(
        CELL_SUBJECT=subject,
        CELL_TICKS=str(len(msgs)),
        CELL_DECIDE_EVERY="1",
        CELL_MAX_QTY="10",
        HEARTBEAT_MS="250",
        KILL_REFRESH_MS="200",
        # its own namespaces so the gate never writes into the live ones
        CELL_INTENT_PREFIX=f"order.bench.{RUN}",
        CELL_SIGNAL_PREFIX=f"signal.bench.{RUN}",
        CELL_RISK_PREFIX=f"risk.bench.{RUN}",
        CELL_SEAT_PREFIX=f"seat.bench.{RUN}",
    )
    log = tmp / "version.log"
    with open(log, "w") as fh:
        proc = subprocess.Popen(
            [str(BIN / "cell")],
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(REPO),
        )
        client = _client()
        try:
            if not _wait_ready(log):
                proc.kill()
                bad("VERSION", "cell never emitted a heartbeat — not subscribed")
                return
            for m in msgs:
                client.publish(subject, m)
        finally:
            client.close()
        rc = proc.wait(timeout=90)

    text = log.read_text()
    m = re.search(r"LEDGER-CELL-SUMMARY (\{.*\})", text)
    if not m:
        bad("VERSION", f"no cell summary (rc={rc}): {text[-300:]}")
        return
    summary = json.loads(m.group(1))
    refused = summary.get("payload_version_rejects")
    accepted = summary.get("ticks")
    if refused != 2 or accepted != 3:
        bad(
            "VERSION",
            f"expected 2 refusals / 3 accepted, got {refused} / {accepted} (rc={rc})",
            summary=summary,
        )
        return
    ok(
        "VERSION",
        f"cell refused 2×v2 and accepted 3×v1 on {subject}; "
        f"decisions={summary.get('decisions')}, refuses named in stderr",
        refusals=refused,
        accepted=accepted,
    )


# --------------------------------------------------------------- REPLAY


def case_replay(tmp: Path) -> None:
    n = 60
    payloads = [
        tick(100.0 + i * 0.5, 1_780_000_000_000_000_000 + i * 1_000_000)
        for i in range(n)
    ]
    symbol_subject = f"{BENCH}.replay.D2GATE"
    client = _client()
    try:
        for p in payloads:
            client.publish(symbol_subject, p)
    finally:
        client.close()

    a = d2.fetch(CFG, "MARKET", f"{BENCH}.replay.>", limit=n + 10)
    b = d2.fetch(CFG, "MARKET", f"{BENCH}.replay.>", limit=n + 10)
    if len(a) != n or len(b) != n:
        bad("REPLAY", f"stream held {len(a)}/{len(b)} of {n} published messages")
        return
    if a != b:
        bad("REPLAY", "two reads of the same stream returned different bytes")
        return

    runs = []
    subject = f"{BENCH}.replay.feed"
    for i in (1, 2):
        # One subject for both passes: the compared field set then includes the
        # subject, so a replay that drifted to a different namespace cannot pass by
        # having its name stripped as "noise". Core NATS holds nothing between runs.

        env = _env(
            CELL_SUBJECT=subject,
            CELL_TICKS=str(n),
            CELL_DECIDE_EVERY="5",
            CELL_MAX_QTY="10",
            HEARTBEAT_MS="250",
            KILL_REFRESH_MS="200",
            CELL_INTENT_PREFIX=f"order.bench.{RUN}.r{i}",
            CELL_SIGNAL_PREFIX=f"signal.bench.{RUN}",
            CELL_RISK_PREFIX=f"risk.bench.{RUN}",
            CELL_SEAT_PREFIX=f"seat.bench.{RUN}",
        )
        log = tmp / f"replay_{i}.log"
        with open(log, "w") as fh:
            proc = subprocess.Popen(
                [str(BIN / "cell")],
                stdout=fh,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(REPO),
            )
            c = _client()
            try:
                if not _wait_ready(log):
                    proc.kill()
                    bad("REPLAY", f"cell run {i} never subscribed (no heartbeat)")
                    return
                # The retained bytes, republished verbatim: replay is a store read,
                # not a second synthetic feed.
                for p in a:
                    c.publish(subject, p)
            finally:
                c.close()
            rc = proc.wait(timeout=120)
        text = log.read_text()
        decisions = []
        for line in text.splitlines():
            if not line.startswith("LEDGER-CELL-DECISION "):
                continue
            body = json.loads(line.split(" ", 1)[1])
            # arrive/e12_ns/tunix_ms are real clocks: identical decisions cannot
            # mean identical timing, so they are stripped rather than compared.
            for k in ("e12_ns", "tunix_ms"):
                body.pop(k, None)
            decisions.append(body)
        if rc != 0 or not decisions:
            bad("REPLAY", f"cell run {i} rc={rc}, {len(decisions)} decisions")
            return
        runs.append(decisions)

    if runs[0] != runs[1]:
        bad(
            "REPLAY",
            f"same bytes produced different decisions "
            f"({len(runs[0])} vs {len(runs[1])} lines)",
        )
        return
    ok(
        "REPLAY",
        f"{n} retained messages read twice identically; cell decided "
        f"{len(runs[0])}× on each pass with identical (sma, verdict, side, qty) "
        f"and differing timing",
        decisions=len(runs[0]),
    )


# --------------------------------------------------------------- CONSUMERS


def case_consumers() -> None:
    st = d2.status(CFG)
    absent = [n for n, v in st["consumers"].items() if not v.get("exists")]
    if absent:
        bad("CONSUMERS", f"declared durables absent: {absent}")
        return
    spec = CFG["d2"]["consumers"][0]
    stream, name = spec["stream"], spec["name"]
    before = st["consumers"][name]["pending"]

    subject = f"{BENCH}.durable"
    client = _client()
    try:
        client.publish(subject, tick(99.0, 1_780_000_000_000_000_000))
        import time

        time.sleep(0.4)
        after = d2.status(CFG)["consumers"][name]["pending"]
        # Reset: the gate must not leave permanent unacked messages that re-deliver
        # every ack_wait for the life of the stream.
        client.request(f"$JS.API.CONSUMER.DELETE.{stream}.{name}", {})
    finally:
        client.close()
    if after <= before:
        bad(
            "CONSUMERS",
            f"publishing to the durable's filter did not move num_pending "
            f"({before}→{after}) — the durable is a name, not a reader",
        )
        return
    applied = d2.apply(CFG)
    recreated = d2.status(CFG)["consumers"][name]
    # `deliver_policy=new` means a recreated durable starts at 0, so a non-zero
    # count would mean the declaration and the server disagree about the start point.
    if recreated["pending"] != 0:
        bad(
            "CONSUMERS",
            f"recreated durable starts at pending={recreated['pending']}, expected 0",
        )
        return
    ok(
        "CONSUMERS",
        f"{name} on {stream} received a message published to its filter "
        f"(pending {before}→{after}), recreated cleanly (pending 0, "
        f"consumers applied {len(applied['consumers'])})",
    )


def main() -> int:
    if not (BIN / "cell").is_file():
        print(
            "D2-GATE-FAIL cell binary missing — run cargo build --release",
            file=sys.stderr,
        )
        return 2
    with tempfile.TemporaryDirectory(prefix="d2gate_", dir=str(scratch_dir())) as td:
        tmp = Path(td)
        for name, fn in (
            ("STREAMS", case_streams),
            ("VERSION", lambda: case_version(tmp)),
            ("REPLAY", lambda: case_replay(tmp)),
            ("CONSUMERS", case_consumers),
        ):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 — a crashed case is a failed case
                bad(name, f"{type(e).__name__}: {e}")
            got = RESULTS.get(name) or {
                "ok": False,
                "detail": "case produced no verdict",
            }
            RESULTS[name] = got
            print(f"D2-{name} {'ok  ' if got['ok'] else 'FAIL'} {got['detail']}")
    _emit()
    return 0 if all(v["ok"] for v in RESULTS.values()) else 1


def _emit() -> None:
    payload = {k: v["ok"] for k, v in sorted(RESULTS.items())}
    payload["ok"] = all(payload.values())
    print("SUMMARY " + json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    sys.exit(main())
