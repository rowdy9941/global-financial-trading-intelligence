#!/usr/bin/env python3
"""Hyperliquid trade-tick recorder (P1) — the only route to genuine tick data.

Why: the public REST API serves candles only, and 1m candles are retained for
~3 days (docs/gates/P1-DATA.md). Per-trade prints are available **only** as a
live WebSocket subscription, so tick history cannot be reconstructed — it can
only be recorded going forward.

Transport: wss://api.hyperliquid.xyz/ws, one subscription
`{"type":"trades","coin":"BTC"}` (verified live 2026-11-03). Message shape:
`{"channel":"trades","data":[{coin,side,px,sz,time,hash,tid,users}]}` where
side "B" = taker buy, "A" = taker sell, time = ms epoch, px/sz are decimal
strings (parsed as Decimal, never float in flight).

Output: append-only gzipped JSONL, one trade per line, hourly segments under
`d0/hyperliquid/<SYMBOL>/trades/<YYYY-MM-DDTHH>.jsonl.gz`. Live capture is NOT
reproducible (unlike the candle collectors) — the same run cannot be replayed,
so segments are kept raw and hashable rather than rewritten.

Operational discipline:
  * `--duration` bounds a supervised run (cron/systemd slices);
  * `--idle-timeout` fails the run if the feed goes silent (a hung socket must
    not look like a quiet market);
  * reconnect with bounded exponential backoff, resume without duplicating:
    rows are de-duplicated by `tid` and out-of-order arrivals are counted, not
    dropped;
  * rows are flushed+fsynced every `--flush-every` rows so a kill -9 loses at
    most a handful of prints.

Usage:
  uv run hl_trades_ws.py --coin BTC --symbol BTC-USDC \
      --out-dir ../../d0/hyperliquid/BTC-USDC/trades --duration 570
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import signal
import sys
import time
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

WS_URL = "wss://api.hyperliquid.xyz/ws"
SIDE_MAP = {"B": "buy", "A": "sell"}
DEDUP_WINDOW = 50_000  # tids retained for reconnect overlap de-duplication


class RecorderLockedError(RuntimeError):
    """Another recorder already writes this directory."""


def acquire_dir_lock(out_dir: Path):
    """Take an exclusive lock on the output directory; return the open handle.

    Measured incident (2026-11-03): two recorders ran on one directory, each
    appending to the same hourly file through a long-lived gzip handle. Their
    deflate streams interleaved inside a single member and the whole hour became
    undecodable — 0 of ~7,700 prints were recoverable. A lock is the only way to
    make that structurally impossible, whatever launches the process.
    """
    import fcntl

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ".recorder.lock"
    # Open WITHOUT truncation: `open(..., "w")` would empty the holder's pid
    # before flock fails, so a refused second process would destroy the only
    # record of who holds the directory (observed 2026-11-04: 0-byte lock).
    # the handle intentionally lives for the whole process (lock lifetime)
    handle = os.fdopen(
        os.open(path, os.O_RDWR | os.O_CREAT, 0o644),
        "r+",
        encoding="utf-8",
    )
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        # the previous holder's pid is still in the file — surface it, so a
        # "who is writing this directory" question is answerable from the error
        holder = handle.read().strip() or "unknown"
        handle.close()
        raise RecorderLockedError(
            f"another recorder holds the lock in {out_dir} (holder pid {holder})"
        ) from exc
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


class TradeFormatError(ValueError):
    """A trade row that cannot be trusted — refused, never half-written."""


def normalize_trade(raw: object, coin: str) -> dict | None:
    """Validate one trade print; return a canonical row or None if not ours.

    Anything malformed raises: a corrupt print must surface loudly, not become
    a silently-skipped row that makes volume look lower than reality.
    """
    if not isinstance(raw, dict):
        raise TradeFormatError(f"trade is not an object: {raw!r:.80}")
    if raw.get("coin") != coin:
        return None
    side = SIDE_MAP.get(raw.get("side"))
    if side is None:
        raise TradeFormatError(f"unknown side {raw.get('side')!r}")
    try:
        px = Decimal(str(raw["px"]))
        sz = Decimal(str(raw["sz"]))
    except (KeyError, InvalidOperation) as exc:
        raise TradeFormatError(f"bad px/sz: {raw!r:.120}") from exc
    if px <= 0 or sz <= 0:
        raise TradeFormatError(f"non-positive px/sz: px={px} sz={sz}")
    try:
        ts_ms = int(raw["time"])
        tid = int(raw["tid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TradeFormatError(f"bad time/tid: {raw!r:.120}") from exc
    if ts_ms <= 0 or tid <= 0:
        raise TradeFormatError(f"non-positive time/tid: time={ts_ms} tid={tid}")
    return {
        "ts_ms": ts_ms,
        "px": px,
        "sz": sz,
        "side": side,
        "tid": tid,
        "hash": str(raw.get("hash") or ""),
    }


def trades_from_message(message: object, coin: str) -> tuple[list[dict], list[str]]:
    """Extract canonical rows from one WS message.

    Returns (rows, ignorable_notes). Non-trade channels (subscriptionResponse,
    pong frames) are noted and skipped; malformed trade rows raise.
    """
    if not isinstance(message, dict):
        raise TradeFormatError(f"message is not an object: {message!r:.80}")
    channel = message.get("channel")
    if channel != "trades":
        return [], [f"channel:{channel}"]
    data = message.get("data")
    if not isinstance(data, list):
        raise TradeFormatError(f"trades data is not a list: {data!r:.80}")
    rows = []
    for raw in data:
        row = normalize_trade(raw, coin)
        if row is not None:
            rows.append(row)
    return rows, []


class Deduper:
    """Drop prints already written (reconnect overlap), counting the drops."""

    def __init__(self, window: int = DEDUP_WINDOW) -> None:
        self.seen: set[int] = set()
        self.order: deque[int] = deque()
        self.window = window
        self.dropped = 0

    def keep(self, row: dict) -> bool:
        tid = row["tid"]
        if tid in self.seen:
            self.dropped += 1
            return False
        self.seen.add(tid)
        self.order.append(tid)
        if len(self.order) > self.window:
            self.seen.discard(self.order.popleft())
        return True


def segment_path(out_dir: Path, ts_ms: int) -> Path:
    hour = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H")
    return out_dir / f"{hour}.jsonl.gz"


class SegmentWriter:
    """Append-only gzipped JSONL, one hourly file per UTC hour.

    Durability contract (measured, after a `timeout` kill corrupted a member):
    each flush writes a COMPLETE gzip member by opening, writing and closing the
    file — never one long-lived handle. Concatenated members are standard gzip,
    so `gzip -dc` reads them end to end, and a `kill -9` can damage at most the
    batch in flight instead of the whole segment. `close_segment()` flushes the
    tail, so an orderly SIGTERM loses nothing.
    """

    def __init__(self, out_dir: Path, flush_every: int = 200) -> None:
        self.out_dir = out_dir
        self.flush_every = max(1, flush_every)
        self._path: Path | None = None
        self._pending: list[dict] = []
        self.rows_written = 0
        self.members_written = 0
        self.segments: list[str] = []

    def _flush(self) -> None:
        if not self._pending or self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(
                {
                    "ts_ms": row["ts_ms"],
                    "px": str(row["px"]),
                    "sz": str(row["sz"]),
                    "side": row["side"],
                    "tid": row["tid"],
                    "hash": row["hash"],
                },
                sort_keys=True,
            )
            for row in self._pending
        ]
        with gzip.open(self._path, "at", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:  # pragma: no cover — fsync unsupported on some fs
                pass
        self._pending.clear()
        self.members_written += 1

    def write(self, row: dict) -> None:
        path = segment_path(self.out_dir, row["ts_ms"])
        if path != self._path:
            self._flush()  # close the tail of the outgoing hour first
            self._path = path
            if str(path) not in self.segments:
                self.segments.append(str(path))
        self._pending.append(row)
        self.rows_written += 1
        if len(self._pending) >= self.flush_every:
            self._flush()

    def close_segment(self) -> None:
        self._flush()
        self._path = None


async def consume(messages, coin: str, writer: SegmentWriter, state: dict) -> None:
    """Consume an async message stream into the writer (network-free, testable)."""
    dedup = state.setdefault("dedup", Deduper())
    last_ts = state.get("last_ts_ms", 0)
    async for raw in messages:
        state["messages"] = state.get("messages", 0) + 1
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            state["ignored"] = state.get("ignored", 0) + 1
            state.setdefault("notes", []).append(f"non-json:{exc.msg}")
            continue
        rows, notes = trades_from_message(msg, coin)
        for note in notes:
            state.setdefault("notes", []).append(note)
        for row in rows:
            if not dedup.keep(row):
                continue
            if row["ts_ms"] < last_ts:
                state["out_of_order"] = state.get("out_of_order", 0) + 1
            last_ts = max(last_ts, row["ts_ms"])
            writer.write(row)
    state["last_ts_ms"] = last_ts
    state["dedup"] = dedup


async def record(
    *,
    coin: str,
    out_dir: Path,
    duration_s: float,
    idle_timeout_s: float,
    flush_every: int,
    max_rows: int | None = None,
    connect=None,
    stop: asyncio.Event | None = None,
) -> dict:
    """Run the recorder for up to `duration_s`, reconnecting on failure.

    `stop` allows an ORDERLY shutdown: setting it unwinds through the writer's
    `finally`, which flushes the tail batch as a complete gzip member. A signal
    handler that raises through the event loop instead would abandon the
    coroutine and leave a truncated member behind (observed once, fixed here).
    """
    stopping = stop if stop is not None else asyncio.Event()
    if connect is None:  # imported here so pure parsing needs no network stack
        import websockets

        connect = websockets.connect

    writer = SegmentWriter(out_dir, flush_every=flush_every)
    state: dict = {"dedup": Deduper()}
    started = time.time()
    reconnects = 0
    failures: list[str] = []
    backoff = 1.0

    try:
        while time.time() - started < duration_s and not stopping.is_set():
            try:
                async with connect(WS_URL, open_timeout=20, ping_interval=20) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "method": "subscribe",
                                "subscription": {"type": "trades", "coin": coin},
                            }
                        )
                    )
                    backoff = 1.0

                    async def stream():
                        while True:
                            if stopping.is_set():
                                return
                            if max_rows is not None and writer.rows_written >= max_rows:
                                return
                            if time.time() - started >= duration_s:
                                return
                            yield await asyncio.wait_for(
                                ws.recv(), timeout=idle_timeout_s
                            )

                    await consume(stream(), coin, writer, state)
            except TimeoutError:
                # silent feed: fail the run rather than record an empty market
                failures.append(f"idle {idle_timeout_s}s with no message")
                if writer.rows_written:
                    break
                reconnects += 1
            except Exception as exc:  # noqa: BLE001 — any socket error reconnects
                failures.append(f"{type(exc).__name__}: {exc}")
                reconnects += 1
            if max_rows is not None and writer.rows_written >= max_rows:
                break
            if stopping.is_set() or time.time() - started >= duration_s:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
    finally:
        writer.close_segment()

    dedup: Deduper = state["dedup"]
    summary = {
        "channel": "trades",
        "coin": coin,
        "rows": writer.rows_written,
        "segments": writer.segments,
        "duplicates_dropped": dedup.dropped,
        "out_of_order": state.get("out_of_order", 0),
        "messages": state.get("messages", 0),
        "reconnects": reconnects,
        "failures": failures[:5],
        "duration_s": round(time.time() - started, 1),
        "members_written": writer.members_written,
        "stopped_early": stopping.is_set(),
        "ok": writer.rows_written > 0,
    }
    return summary


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--coin", default="BTC")
    a.add_argument("--symbol", default="BTC-USDC")
    a.add_argument("--out-dir", default=None)
    a.add_argument("--duration", type=float, default=570.0)
    a.add_argument("--idle-timeout", type=float, default=60.0)
    a.add_argument("--flush-every", type=int, default=200)
    a.add_argument("--max-rows", type=int, default=None)
    args = a.parse_args()

    repo = Path(__file__).resolve().parents[2]
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else repo / "d0" / "hyperliquid" / args.symbol / "trades"
    )

    try:
        lock = acquire_dir_lock(out_dir)
    except RecorderLockedError as exc:
        print(f"HL-TRADES-RECORD-FAILED {exc}", file=sys.stderr, flush=True)
        raise SystemExit(3) from exc

    # SIGTERM/SIGINT must still flush. Raising from a signal handler would
    # abandon the coroutine (and truncate the open gzip member), so the handler
    # only SETS an event; the recorder unwinds normally through its finally.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    try:
        summary = loop.run_until_complete(
            record(
                coin=args.coin,
                out_dir=out_dir,
                duration_s=args.duration,
                idle_timeout_s=args.idle_timeout,
                flush_every=args.flush_every,
                max_rows=args.max_rows,
                stop=stop,
            )
        )
    finally:
        loop.close()
        lock.close()
    summary["symbol"] = args.symbol
    print("HL-TRADES-RECORD " + json.dumps(summary, sort_keys=True), flush=True)
    raise SystemExit(0 if summary["ok"] else 1)


if __name__ == "__main__":
    main()
