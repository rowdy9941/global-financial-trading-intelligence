#!/usr/bin/env python3
"""D2 = JetStream, declared in `configs/gfti.toml [d2]` and applied from there.

Before A-8 the stack ran `nats-server -js` and nothing created a single stream, so
"the messages are retained" was an assumption with a zero behind it: `/jsz` read
`"streams": 0` while the documentation said D2 was the bus store. This module makes
the declaration real in both directions —

* `apply` creates/updates exactly the streams and durables in the config, so the
  file is the definition (one source, as with every other A-4 value);
* `status` reads the server back, so "declared" and "exists with these subjects"
  are two different facts the gate can compare.

Why a hand-written client: `gfti` is stdlib-only on purpose (it must run on a bare
`python3` before anything is installed), and the JetStream control surface is
request/reply over the core NATS text protocol. The subset here is small and
checked: CONNECT/PING, SUB/PUB with a reply inbox, and MSG parsing with an exact
byte count. No TLS/auth — this is the loopback foundation the rest of the stack
also runs on, and `wire.rs` makes the same trade for the same reason.

Control API calls used:
    $JS.API.STREAM.CREATE.<NAME> | STREAM.UPDATE.<NAME> | STREAM.INFO.<NAME>
    $JS.API.STREAM.NAMES
    $JS.API.CONSUMER.DURABLE.CREATE.<STREAM>.<DURABLE> | CONSUMER.INFO.<...>
    $JS.API.CONSUMER.CREATE.<STREAM> | CONSUMER.MSG.NEXT.<STREAM>.<NAME>
"""

from __future__ import annotations

import json
import os
import random
import socket
import time
from pathlib import Path

try:  # `gfti` runs as a script; gates import the package
    import gfti_config as gcfg
except ImportError:  # pragma: no cover - package import path
    from . import gfti_config as gcfg

REPO = Path(__file__).resolve().parents[2]

NS = 1_000_000_000  # JetStream speaks nanoseconds


class D2Error(RuntimeError):
    """A D2 problem the operator can act on; never a traceback."""


# ----------------------------------------------------------------- wire


class Nats:
    """Minimal synchronous NATS client: connect, publish, request, subscribe."""

    def __init__(self, url: str, timeout: float = 5.0):
        host, port = _parse_url(url)
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = b""
        self._sid = 0
        line = self._readline()
        if not line.startswith(b"INFO "):
            raise D2Error(f"{url}: expected INFO, got {line[:60]!r}")
        self._send(
            b'CONNECT {"verbose":false,"pedantic":false,"lang":"python","version":"gfti",'
            b'"protocol":1}\r\nPING\r\n'
        )
        if self._readline() != b"PONG":
            raise D2Error(f"{url}: no PONG from server")

    # -- primitives
    def _send(self, blob: bytes) -> None:
        self._sock.sendall(blob)

    def _fill(self) -> bool:
        chunk = self._sock.recv(65536)
        if not chunk:
            return False
        self._buf += chunk
        return True

    def _readline(self) -> bytes:
        while b"\r\n" not in self._buf:
            if not self._fill():
                raise D2Error("connection closed by server")
        line, self._buf = self._buf.split(b"\r\n", 1)
        return line

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n + 2:
            if not self._fill():
                raise D2Error("connection closed mid-payload")
        body, self._buf = self._buf[:n], self._buf[n:]
        if self._buf.startswith(b"\r\n"):
            self._buf = self._buf[2:]
        return body

    def publish(self, subject: str, payload: bytes | str | dict) -> None:
        if isinstance(payload, dict):
            body = json.dumps(payload).encode()
        else:
            body = payload.encode() if isinstance(payload, str) else payload
        self._send(f"PUB {subject} {len(body)}\r\n".encode() + body + b"\r\n")

    def request(self, subject: str, payload: dict | str, timeout: float = 5.0) -> dict:
        body = (
            json.dumps(payload).encode()
            if isinstance(payload, dict)
            else payload.encode()
        )
        inbox = f"_INBOX.gfti.{random.getrandbits(48):012x}"
        self._sid += 1
        sid = str(self._sid)
        self._send(f"SUB {inbox} {sid}\r\n".encode())
        self._send(f"PUB {subject} {inbox} {len(body)}\r\n".encode() + body + b"\r\n")
        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() > deadline:
                raise D2Error(f"{subject}: no reply within {timeout}s")
            head = self._readline()
            if head == b"PING":
                self._send(b"PONG\r\n")
                continue
            if head in (b"PONG", b"+OK") or head.startswith(b"INFO "):
                continue
            if not head.startswith(b"MSG "):
                continue
            parts = head.split()
            # MSG <subject> <sid> [reply] <bytes>
            nbytes = int(parts[-1])
            body = self._read_exact(nbytes)
            self._send(f"UNSUB {sid}\r\n".encode())
            try:
                return json.loads(body)
            except json.JSONDecodeError as e:
                raise D2Error(f"{subject}: reply is not JSON ({e})") from None

    def request_batch(
        self, subject: str, payload: dict, want: int, timeout: float
    ) -> list:
        """Subscribe to a one-shot inbox, then request a batch.

        Order matters and is the whole reason this method exists: `CONSUMER.MSG.NEXT`
        replies are ordinary messages, so subscribing *after* publishing drops every
        reply the server was fast enough to send first — which, on a loopback bus, is
        all of them.
        """
        inbox = f"_INBOX.gfti.batch.{random.getrandbits(48):012x}"
        self._sid += 1
        sid = str(self._sid)
        body = json.dumps(payload).encode()
        self._send(f"SUB {inbox} {sid}\r\n".encode())
        self._send(f"PUB {subject} {inbox} {len(body)}\r\n".encode() + body + b"\r\n")
        try:
            return self._collect(sid, want, timeout)
        finally:
            self._send(f"UNSUB {sid}\r\n".encode())

    def _collect(self, sid: str, want: int, timeout: float) -> list:
        out = []
        deadline = time.monotonic() + timeout
        while len(out) < want:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._sock.settimeout(max(0.05, min(remaining, 1.0)))
            try:
                head = self._readline()
            except TimeoutError:
                continue
            if head == b"PING":
                self._send(b"PONG\r\n")
                continue
            if not head.startswith(b"MSG "):
                continue
            out.append(self._read_exact(int(head.split()[-1])))
        return out

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def _parse_url(url: str) -> tuple:
    rest = url.split("://", 1)[-1]
    host, _, port = rest.partition(":")
    return (host or "127.0.0.1", int(port or 4222))


# ----------------------------------------------------------------- declarations


def declarations(cfg: dict) -> tuple:
    """(streams, consumers) from the config, validated before anything is created.

    `[d2]` is outside the sparse SCHEMA because it is structured data rather than a
    scalar, so the owning module validates it — a stream with no subjects, or a
    consumer naming an undeclared stream, is refused by name instead of reaching
    the server as a confusing API error.
    """
    d2 = cfg.get("d2") or {}
    streams = d2.get("streams") or []
    consumers = d2.get("consumers") or []
    if not streams:
        raise D2Error(
            "configs/gfti.toml: [d2] declares no streams — D2 would retain nothing"
        )
    names = set()
    for s in streams:
        name = str(s.get("name") or "")
        if not name or not name.isupper():
            raise D2Error(f"[d2.streams]: name must be upper-case, got {name!r}")
        if name in names:
            raise D2Error(f"[d2.streams]: duplicate stream {name}")
        names.add(name)
        if not s.get("subjects"):
            raise D2Error(f"[d2.streams].{name}: subjects must be non-empty")
        for key in ("max_age_s", "storage", "retention"):
            if key not in s:
                raise D2Error(f"[d2.streams].{name}: missing {key}")
    seen = set()
    for c in consumers:
        if c.get("stream") not in names:
            raise D2Error(
                f"[d2.consumers].{c.get('name')}: stream {c.get('stream')!r} is not declared"
            )
        if c.get("name") in seen:
            raise D2Error(f"[d2.consumers]: duplicate durable {c.get('name')}")
        seen.add(c.get("name"))
    return streams, consumers


def _stream_config(spec: dict) -> dict:
    return {
        "name": spec["name"],
        "subjects": list(spec["subjects"]),
        "retention": spec.get("retention", "limits"),
        "max_age": int(spec["max_age_s"]) * NS,
        "storage": spec.get("storage", "file"),
        "discard": spec.get("discard", "old"),
        "num_replicas": 1,
        "allow_direct": False,
    }


def _consumer_config(spec: dict) -> dict:
    return {
        "durable_name": spec["name"],
        "filter_subject": spec.get("filter", ">"),
        "ack_policy": spec.get("ack_policy", "explicit"),
        "max_deliver": int(spec.get("max_deliver", 100000)),
        "ack_wait": int(spec.get("ack_wait_s", 30)) * NS,
        # `new`, not `all`: an engine must not replay yesterday's market data into a
        # live decision at boot. Historical replay is an explicit read (`fetch`,
        # ephemeral, `all`) — never a side effect of starting a service.
        "deliver_policy": "new",
        "replay_policy": "instant",
    }


# The server returns many more fields than are declared (max_msgs, discard_new_per_subject,
# duplicate windows, …). Comparing whole configs would report drift on every run, which
# is a false alarm rather than a check, so only declared intent is compared.
_DECLARED_STREAM_KEYS = ("name", "subjects", "retention", "max_age", "storage")


def _drift(live: dict, wanted: dict) -> bool:
    return {k: live.get(k) for k in _DECLARED_STREAM_KEYS} != {
        k: wanted.get(k) for k in _DECLARED_STREAM_KEYS
    }


def _immutable(err: dict) -> bool:
    """Is this the "field cannot be updated" class of consumer-create error?"""
    text = str(err.get("description", "")).lower()
    return err.get("err_code") in (10052, 10148) or "can not be updated" in text


def _err(resp: dict) -> dict | None:
    return resp.get("error") if isinstance(resp, dict) else None


def apply(cfg: dict, url: str | None = None) -> dict:
    """Create or update every declared stream and durable. Idempotent by design."""
    streams, consumers = declarations(cfg)
    url = url or cfg["stores"]["nats_url_host"]
    client = Nats(url)
    created, updated, existed, consumers_applied, recreated = [], [], [], [], []
    try:
        for spec in streams:
            body = _stream_config(spec)
            # Ask first, then act. STREAM.CREATE is idempotent when the config is
            # identical, so calling it alone cannot tell "created" from "already
            # there" — the previous version reported six creations on every run,
            # which is a measurement that looks like work and proves nothing.
            probe = client.request(f"$JS.API.STREAM.INFO.{spec['name']}", {})
            exists = not _err(probe)
            verb = "UPDATE" if exists else "CREATE"
            resp = client.request(f"$JS.API.STREAM.{verb}.{spec['name']}", body)
            if err := _err(resp):
                raise D2Error(f"stream {spec['name']}: {err.get('description')}")
            if not exists:
                created.append(spec["name"])
            elif _drift((probe.get("config") or {}), body):
                updated.append(spec["name"])
            else:
                existed.append(spec["name"])
        for c in consumers:
            # `stream_name` (not `stream`): the server compares this field with the
            # stream in the API subject and rejects a mismatch with err 10056 — the
            # reason a consumer create fails while every stream create succeeds.
            payload = {"stream_name": c["stream"], "config": _consumer_config(c)}
            resp = client.request(
                f"$JS.API.CONSUMER.DURABLE.CREATE.{c['stream']}.{c['name']}", payload
            )
            err = _err(resp)
            if err and _immutable(err):
                # JetStream refuses to change `deliver_policy` (and a few other config
                # fields) on an existing durable: it answers "deliver policy can not be
                # updated" rather than converging. Delete + create is the only route,
                # and it is safe here because a durable is a position, not data.
                client.request(f"$JS.API.CONSUMER.DELETE.{c['stream']}.{c['name']}", {})
                again = client.request(
                    f"$JS.API.CONSUMER.DURABLE.CREATE.{c['stream']}.{c['name']}",
                    payload,
                )
                if err2 := _err(again):
                    raise D2Error(f"consumer {c['name']}: {err2.get('description')}")
                recreated.append(f"{c['stream']}/{c['name']}")
            elif err and err.get("err_code") == 10148:  # consumer already exists
                inp = client.request(
                    f"$JS.API.CONSUMER.INFO.{c['stream']}.{c['name']}", {}
                )
                if _err(inp):
                    raise D2Error(f"consumer {c['name']}: {err.get('description')}")
                existed.append(f"{c['stream']}/{c['name']}")
            elif err:
                raise D2Error(f"consumer {c['name']}: {err.get('description')}")
            else:
                consumers_applied.append(f"{c['stream']}/{c['name']}")
    finally:
        client.close()
    return {
        "ok": True,
        "url": url,
        "streams_created": created,
        "streams_updated": updated,
        "streams_existing": existed,
        "consumers": consumers_applied or existed,
        "consumers_recreated": recreated,
        "declared_streams": [s["name"] for s in streams],
        "declared_consumers": [c["name"] for c in consumers],
    }


def status(cfg: dict, url: str | None = None) -> dict:
    """What the server actually holds, compared against what the config declares."""
    streams, consumers = declarations(cfg)
    url = url or cfg["stores"]["nats_url_host"]
    client = Nats(url)
    out = {"ok": True, "url": url, "streams": {}, "consumers": {}, "problems": []}
    try:
        for spec in streams:
            name = spec["name"]
            info = client.request(f"$JS.API.STREAM.INFO.{name}", {})
            if err := _err(info):
                out["streams"][name] = {
                    "exists": False,
                    "error": err.get("description"),
                }
                out["problems"].append(f"stream {name} does not exist")
                continue
            state = info.get("state") or {}
            subjects = list((info.get("config") or {}).get("subjects") or [])
            out["streams"][name] = {
                "exists": True,
                "messages": state.get("messages", 0),
                "bytes": state.get("bytes", 0),
                "subjects": subjects,
                "declared_subjects": list(spec["subjects"]),
                "consumer_count": state.get("consumer_count", 0),
            }
            missing = [p for p in spec["subjects"] if p not in subjects]
            if missing:
                out["problems"].append(f"stream {name} does not capture {missing}")
        for c in consumers:
            info = client.request(
                f"$JS.API.CONSUMER.INFO.{c['stream']}.{c['name']}", {}
            )
            if err := _err(info):
                out["consumers"][c["name"]] = {"exists": False}
                out["problems"].append(
                    f"consumer {c['name']} on {c['stream']} does not exist"
                )
                continue
            out["consumers"][c["name"]] = {
                "exists": True,
                "stream": c["stream"],
                # `num_pending` is "not yet delivered to the durable"; ack_pending is
                # only non-zero once something is actually pulling. A durable with no
                # reader moves the former and never the latter.
                "pending": info.get("num_pending", 0),
                "ack_pending": info.get("num_ack_pending", 0),
                "delivered": (info.get("delivered") or {}).get("consumer_seq", 0),
                "filter": (info.get("config") or {}).get("filter_subject"),
            }
    finally:
        client.close()
    out["ok"] = not out["problems"]
    return out


def captured_by(cfg: dict, subject: str) -> str | None:
    """Which declared stream would capture `subject`? None means it is dropped.

    A publisher writing to a subject no stream captures is the failure mode this
    whole module exists to prevent, and it is invisible from both ends: the
    publisher succeeds and the store is simply empty.
    """
    streams, _ = declarations(cfg)
    for spec in streams:
        for pattern in spec["subjects"]:
            if subject_matches(pattern, subject):
                return spec["name"]
    return None


def subject_matches(pattern: str, subject: str) -> bool:
    """NATS subject matching for the `*` / `>` wildcards (no regex, no surprises)."""
    p, s = pattern.split("."), subject.split(".")
    for i, tok in enumerate(p):
        if tok == ">":
            return i < len(s)
        if i >= len(s):
            return False
        if tok != "*" and tok != s[i]:
            return False
    return len(p) == len(s)


# ----------------------------------------------------------------- readback


def fetch(
    cfg: dict,
    stream: str,
    filter_subject: str,
    limit: int = 1000,
    url: str | None = None,
    timeout: float = 10.0,
) -> list:
    """Pull retained messages back in publish order (ephemeral consumer).

    Used by the REPLAY gate: the same retained bytes must yield the same decisions
    when read twice, which is a property of the store, not of a file on disk.
    """
    url = url or cfg["stores"]["nats_url_host"]
    client = Nats(url)
    try:
        resp = client.request(
            f"$JS.API.CONSUMER.CREATE.{stream}",
            {
                "stream_name": stream,
                "config": {
                    "filter_subject": filter_subject,
                    "ack_policy": "none",
                    "deliver_policy": "all",
                    "replay_policy": "instant",
                },
            },
        )
        if err := _err(resp):
            raise D2Error(f"ephemeral consumer on {stream}: {err.get('description')}")
        name = resp.get("name")
        if not name:
            raise D2Error(f"ephemeral consumer on {stream}: no name in reply")
        bodies = []
        while len(bodies) < limit:
            want = min(limit - len(bodies), 500)
            chunk = client.request_batch(
                f"$JS.API.CONSUMER.MSG.NEXT.{stream}.{name}",
                {"batch": want, "no_wait": True},
                want,
                timeout,
            )
            if not chunk:
                break
            # A pull request for a batch larger than the backlog is answered with a
            # status/no-message frame whose body is empty or a NATS/1.0 header.
            # Those are protocol frames, not payloads, and counting them would make
            # "replayed 61 of 60 messages" look like a real measurement.
            bodies.extend(b for b in chunk if b.startswith(b"{"))
        client.request(f"$JS.API.CONSUMER.DELETE.{stream}.{name}", {})
        return bodies
    finally:
        client.close()


def jsz(url: str | None = None, monitor: str | None = None) -> dict:
    """Server-side JetStream totals (the `/jsz` endpoint), for honest reporting."""
    import urllib.request

    base = monitor or _monitor_from(url or _default_url())
    with urllib.request.urlopen(f"{base}/jsz?streams=true", timeout=5) as r:
        return json.loads(r.read().decode())


def _default_url() -> str:
    return gcfg.load()["stores"]["nats_url_host"]


def _monitor_from(url: str) -> str:
    host, _port = _parse_url(url)
    monitor_port = os.environ.get("NATS_MONITOR_PORT")
    if not monitor_port:
        monitor_port = str(gcfg.load()["stores"]["nats_monitor_port"])
    return f"http://{host}:{monitor_port}"
