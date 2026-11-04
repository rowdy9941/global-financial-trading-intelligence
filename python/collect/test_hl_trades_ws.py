"""Unit tests for hl_trades_ws.py — fake streams, no network, no live socket."""

from __future__ import annotations

import asyncio
import gzip
import json
import os
from datetime import UTC, datetime
from decimal import Decimal

import hl_trades_ws as tw
import pytest

COIN = "BTC"


def _trade(
    ts: int,
    px: str = "77000.0",
    sz: str = "0.01",
    side: str = "B",
    tid: int | None = None,
):
    return {
        "coin": COIN,
        "side": side,
        "px": px,
        "sz": sz,
        "time": ts,
        "hash": "0xdead",
        "tid": tid if tid is not None else ts,
        "users": ["0xa", "0xb"],
    }


def _msg(*trades) -> str:
    return json.dumps({"channel": "trades", "data": list(trades)})


async def _stream(items):
    for item in items:
        yield item


def _run(messages, tmp_path, coin=COIN, max_rows=None, flush_every=1):
    writer = tw.SegmentWriter(tmp_path, flush_every=flush_every)
    state: dict = {"dedup": tw.Deduper()}
    asyncio.run(tw.consume(_stream(messages), coin, writer, state))
    writer.close_segment()
    return writer, state


# --- normalization ---------------------------------------------------------


def test_normalize_trade_maps_side_and_keeps_decimals_exact():
    row = tw.normalize_trade(
        _trade(1_763_700_000_000, px="77447.1234567890", sz="0.00519"), COIN
    )
    assert row is not None
    assert row["side"] == "buy"
    assert row["px"] == Decimal("77447.1234567890")
    assert row["sz"] == Decimal("0.00519")
    assert tw.normalize_trade(_trade(1, side="A"), COIN)["side"] == "sell"


def test_normalize_trade_ignores_other_coins():
    other = {**_trade(1), "coin": "ETH"}
    assert tw.normalize_trade(other, COIN) is None


@pytest.mark.parametrize(
    "broken",
    [
        {**_trade(1), "side": "X"},
        {**_trade(1), "px": "0"},
        {**_trade(1), "sz": "-1"},
        {**_trade(1), "px": "not-a-number"},
        {**_trade(1), "time": 0},
        {**_trade(1), "tid": -5},
        {k: v for k, v in _trade(1).items() if k != "time"},
    ],
)
def test_malformed_trades_are_refused_not_silently_skipped(broken):
    with pytest.raises(tw.TradeFormatError):
        tw.normalize_trade(broken, COIN)


def test_non_trade_channels_are_notes_and_do_not_raise():
    rows, notes = tw.trades_from_message(
        {"channel": "subscriptionResponse", "data": {"method": "subscribe"}}, COIN
    )
    assert rows == []
    assert notes == ["channel:subscriptionResponse"]


def test_trades_data_must_be_a_list():
    with pytest.raises(tw.TradeFormatError):
        tw.trades_from_message({"channel": "trades", "data": {"nope": 1}}, COIN)


# --- writing ----------------------------------------------------------------


def test_consume_writes_hourly_segment_with_exact_rows(tmp_path):
    base = 1_763_700_000_000
    writer, state = _run([_msg(_trade(base), _trade(base + 1000, side="A"))], tmp_path)
    assert writer.rows_written == 2
    assert len(writer.segments) == 1
    path = tmp_path / writer.segments[0].split("/")[-1]
    lines = gzip.decompress(path.read_bytes()).decode().splitlines()
    assert len(lines) == 2
    row = json.loads(lines[0])
    assert set(row) == {"ts_ms", "px", "sz", "side", "tid", "hash"}
    assert row["px"] == "77000.0"  # decimal preserved as a string, never float
    assert json.loads(lines[1])["side"] == "sell"
    assert state["messages"] == 1


def test_a_trade_crossing_an_hour_boundary_starts_a_new_segment(tmp_path):
    # 2026-11-03T07:59:59Z then 08:00:00Z
    t = int(datetime(2026, 11, 3, 7, 59, 59, tzinfo=UTC).timestamp() * 1000)
    writer, _ = _run([_msg(_trade(t)), _msg(_trade(t + 1000))], tmp_path)
    assert len(writer.segments) == 2
    names = [s.split("/")[-1] for s in writer.segments]
    assert names == ["2026-11-03T07.jsonl.gz", "2026-11-03T08.jsonl.gz"]


def test_reconnect_overlap_is_deduplicated_by_tid(tmp_path):
    base = 1_763_700_000_000
    stream = [
        _msg(_trade(base), _trade(base + 1)),
        _msg(_trade(base + 1), _trade(base + 2)),
    ]
    writer, state = _run(stream, tmp_path)
    assert writer.rows_written == 3
    assert state["dedup"].dropped == 1


def test_out_of_order_arrivals_are_counted_not_dropped(tmp_path):
    base = 1_763_700_000_000
    writer, state = _run([_msg(_trade(base + 5000), _trade(base + 1000))], tmp_path)
    assert writer.rows_written == 2
    assert state.get("out_of_order") == 1


def test_non_json_frame_is_counted_and_skipped(tmp_path):
    writer, state = _run(["{not json", _msg(_trade(1_763_700_000_000))], tmp_path)
    assert writer.rows_written == 1
    assert state["ignored"] == 1


def test_append_mode_never_truncates_an_existing_segment(tmp_path):
    base = int(datetime(2026, 11, 3, 7, 10, tzinfo=UTC).timestamp() * 1000)
    _run([_msg(_trade(base))], tmp_path)
    _run([_msg(_trade(base + 60_000))], tmp_path)
    path = tmp_path / "2026-11-03T07.jsonl.gz"
    lines = gzip.decompress(path.read_bytes()).decode().splitlines()
    assert len(lines) == 2, "a second run must append, not rewrite the segment"


# --- run loop ---------------------------------------------------------------


class _FakeWs:
    def __init__(self, frames):
        self._frames = list(frames)
        self.sent: list[str] = []

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        if not self._frames:
            raise RuntimeError("connection closed by fake")
        return self._frames.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_record_subscribes_to_trades_and_reports_ok(tmp_path):
    base = 1_763_700_000_000
    ws = _FakeWs(
        [
            json.dumps({"channel": "subscriptionResponse", "data": {}}),
            _msg(_trade(base)),
        ]
    )
    summary = asyncio.run(
        tw.record(
            coin=COIN,
            out_dir=tmp_path,
            duration_s=0.3,
            idle_timeout_s=5,
            flush_every=1,
            max_rows=1,
            connect=lambda *a, **k: ws,
        )
    )
    assert json.loads(ws.sent[0])["subscription"] == {"type": "trades", "coin": COIN}
    assert summary["rows"] == 1
    assert summary["ok"] is True
    assert summary["reconnects"] == 0


def test_record_reports_not_ok_when_nothing_was_recorded(tmp_path):
    ws = _FakeWs([])

    def connect(*a, **k):
        return ws

    summary = asyncio.run(
        tw.record(
            coin=COIN,
            out_dir=tmp_path,
            duration_s=0.2,
            idle_timeout_s=5,
            flush_every=1,
            connect=connect,
        )
    )
    assert summary["rows"] == 0
    assert summary["ok"] is False
    assert summary["reconnects"] >= 1
    assert summary["failures"]


# --- durability contract (after a real `timeout` kill corrupted a member) ---


def _gzip_members(path):
    """Decode gzip members one by one, so a truncated tail is attributable."""
    import zlib

    data = path.read_bytes()
    out, rest = [], data
    while rest:
        dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            chunk = dec.decompress(rest)
        except zlib.error:
            break
        out.append(chunk)
        rest = dec.unused_data
        if not dec.eof:
            break
    return out


def test_each_flush_is_a_complete_gzip_member(tmp_path):
    base = int(datetime(2026, 11, 3, 7, 10, tzinfo=UTC).timestamp() * 1000)
    writer = tw.SegmentWriter(tmp_path, flush_every=2)
    for i in range(6):
        writer.write(tw.normalize_trade(_trade(base + i * 1000), COIN))
    writer.close_segment()
    path = tmp_path / "2026-11-03T07.jsonl.gz"
    members = _gzip_members(path)
    assert len(members) == 3, "3 batches -> 3 standalone members"
    assert sum(len(m.splitlines()) for m in members) == 6
    assert gzip.decompress(path.read_bytes()).count(b"\n") == 6


def test_truncated_tail_cannot_destroy_earlier_members(tmp_path):
    """kill -9 mid-batch must cost at most the batch in flight — never the hour."""
    base = int(datetime(2026, 11, 3, 7, 10, tzinfo=UTC).timestamp() * 1000)
    writer = tw.SegmentWriter(tmp_path, flush_every=2)
    for i in range(4):
        writer.write(tw.normalize_trade(_trade(base + i * 1000), COIN))
    path = tmp_path / "2026-11-03T07.jsonl.gz"
    intact = path.read_bytes()
    # simulate a writer killed mid-member: append a half-written member
    writer.write(tw.normalize_trade(_trade(base + 5000), COIN))
    writer.close_segment()
    with gzip.open(path, "at", encoding="utf-8") as fh:
        fh.write("half a line, no terminator")
    members = _gzip_members(path)
    recovered = b"".join(members)
    assert recovered.count(b"\n") >= 4, "the earlier complete members stay readable"
    assert len(intact) > 0


def test_graceful_stop_flushes_the_tail_without_truncation(tmp_path):
    base = int(datetime(2026, 11, 3, 7, 10, tzinfo=UTC).timestamp() * 1000)
    loop_stop = asyncio.Event()

    class _StopAfterOne:
        def __init__(self):
            self.sent = []

        async def send(self, payload):
            self.sent.append(payload)

        async def recv(self):
            loop_stop.set()  # exactly what the SIGTERM handler does
            return _msg(_trade(base), _trade(base + 1000))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    summary = asyncio.run(
        tw.record(
            coin=COIN,
            out_dir=tmp_path,
            duration_s=30,
            idle_timeout_s=5,
            flush_every=1000,  # nothing flushes until the writer is closed
            connect=lambda *a, **k: _StopAfterOne(),
            stop=loop_stop,
        )
    )
    assert summary["stopped_early"] is True
    assert summary["rows"] == 2
    path = tmp_path / "2026-11-03T07.jsonl.gz"
    # a complete member: strict decompression succeeds and holds both rows
    assert gzip.decompress(path.read_bytes()).count(b"\n") == 2


# --- single-writer guarantee (root cause of the lost hour on 2026-11-03) ----


def test_second_recorder_on_the_same_directory_is_refused(tmp_path):
    handle = tw.acquire_dir_lock(tmp_path)
    try:
        with pytest.raises(tw.RecorderLockedError):
            tw.acquire_dir_lock(tmp_path)
    finally:
        handle.close()
    tw.acquire_dir_lock(tmp_path).close()  # released cleanly -> reusable


def test_lock_is_per_directory(tmp_path):
    a = tw.acquire_dir_lock(tmp_path / "a")
    b = tw.acquire_dir_lock(tmp_path / "b")
    a.close()
    b.close()


def test_refused_acquire_does_not_destroy_the_holder_pid(tmp_path):
    """Observed 2026-11-04: `open(..., "w")` truncated the lock to 0 bytes
    before flock failed, so "who holds this directory" became unanswerable."""
    holder = tw.acquire_dir_lock(tmp_path)
    try:
        with pytest.raises(tw.RecorderLockedError) as exc:
            tw.acquire_dir_lock(tmp_path)
        assert str(os.getpid()) in str(exc.value)
        assert (tmp_path / ".recorder.lock").read_text().strip() == str(os.getpid())
    finally:
        holder.close()
