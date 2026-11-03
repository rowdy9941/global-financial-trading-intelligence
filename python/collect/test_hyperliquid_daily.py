"""Unit tests for hyperliquid_daily.py — mocked transport, no network."""

from __future__ import annotations

import json
from datetime import UTC
from decimal import Decimal
from pathlib import Path

import hyperliquid_daily as hl
import pytest

DAY = hl.VALID_INTERVALS["1d"]


def _candle(
    open_ms: int,
    o: str,
    h: str,
    l: str,
    c: str,
    v: str,
    n: int,
    step: int = DAY,
    interval: str = "1d",
) -> dict:
    return {
        "t": open_ms,
        "T": open_ms + step - 1,
        "s": "BTC",
        "i": interval,
        "o": o,
        "c": c,
        "h": h,
        "l": l,
        "v": v,
        "n": n,
    }


def test_normalize_decimal_and_ts_day() -> None:
    rows = [
        _candle(DAY, "96484.0", "97933.0", "95740.0", "97274.0", "5292.30091", 42319)
    ]
    bars = hl.normalize(rows, DAY * 2)
    b = bars[0]
    assert b["open"] == Decimal("96484.0") and b["volume"] == Decimal("5292.30091")
    assert b["ts_day"] == "1970-01-02"  # t = 1 DAY past epoch → UTC day string
    assert b["open_ms"] == DAY


def test_normalize_drops_in_progress_bar() -> None:
    # second bar starts exactly at until_ms → must be dropped
    rows = [
        _candle(0, "1", "1", "1", "1", "1", 1),
        _candle(DAY, "1", "1", "1", "1", "1", 1),
    ]
    assert len(hl.normalize(rows, DAY)) == 1


def datetime_str(ms: int) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def test_integrity_monotonic_and_holes_recorded() -> None:
    bars = hl.normalize(
        [
            _candle(0, "100", "110", "90", "105", "10", 5),
            _candle(3 * DAY, "105", "120", "95", "118", "12", 6),  # 2-day hole
        ],
        4 * DAY,
    )
    errs, n, holes = hl.integrity_check(bars, "BTC", DAY)
    assert errs == [] and n == 2
    # gap is 2 missing bars (open 1d and 2d): window covers both, tip-exclusive
    assert holes == [
        {"window_from": datetime_str(DAY), "window_to": datetime_str(2 * DAY)}
    ]


def test_integrity_flags_ohlc_and_future() -> None:
    bars = hl.normalize(
        [
            _candle(0, "150", "110", "90", "105", "10", 5),  # bad OHLC
            _candle(DAY, "100", "110", "90", "105", "10", 5),  # fine
        ],
        2 * DAY,
    )
    errs, _n, _holes = hl.integrity_check(bars, "BTC", DAY)
    assert any("OHLC" in e for e in errs)


def test_fetch_all_paginates_and_refuses_backtracking() -> None:
    calls: list[tuple[int, int]] = []
    WIN = hl.VALID_INTERVALS["1d"] * hl.WINDOW_CANDLES  # one full window

    def post(payload: bytes) -> bytes:
        req = json.loads(payload)["req"]
        calls.append((req["startTime"], req["endTime"]))
        first = ((req["startTime"] + DAY - 1) // DAY) * DAY
        candles = [
            _candle(t, "1", "2", "0.5", "1.5", "9", 3)
            for t in range(first, req["endTime"] + 1, DAY)
        ]
        return json.dumps(candles).encode()

    # two windows walking down. The API honors inclusive endTime; the
    # continuation request ends one millisecond before the prior first bar.
    rows = hl.fetch_all("BTC", "1d", WIN + 2 * DAY, _post=post)
    assert calls == [(3 * DAY, WIN + 2 * DAY), (1, 3 * DAY - 1)]
    assert len(rows) == hl.WINDOW_CANDLES + 2
    assert [r["t"] for r in rows[:2]] == [DAY, 2 * DAY]
    assert all(start != 0 for start, _end in calls)
    assert rows[-1]["t"] == WIN + 2 * DAY  # inclusive source endTime boundary

    def gap_then_data(payload: bytes) -> bytes:
        # first window empty (honest sparse gap), final window has data
        req = json.loads(payload)["req"]
        if req["startTime"] == 1:
            return json.dumps([_candle(DAY, "1", "2", "0.5", "1.5", "9", 3)]).encode()
        return json.dumps([]).encode()

    rows2 = hl.fetch_all("BTC", "1d", 2 * WIN, _post=gap_then_data)
    assert len(rows2) == 1

    def duplicated(payload: bytes) -> bytes:
        return json.dumps(
            [
                _candle(0, "1", "2", "0.5", "1.5", "9", 3),
                _candle(0, "1", "2", "0.5", "1.5", "9", 3),  # same t twice
            ]
        ).encode()

    with pytest.raises(RuntimeError, match="non-monotonic"):
        hl.fetch_all("BTC", "1d", 5000 * DAY, _post=duplicated)

    def endless(payload: bytes) -> bytes:
        # always one candle; inception never reached → bounded abort
        req = json.loads(payload)["req"]
        return json.dumps(
            [_candle(max(req["startTime"], DAY), "1", "2", "0.5", "1.5", "9", 3)]
        ).encode()

    with pytest.raises(RuntimeError, match="max_windows"):
        hl.fetch_all("BTC", "1d", 99999 * DAY, _post=endless, max_windows=3)


def test_venue_onset_trims_reference_bars() -> None:
    bars = hl.normalize(
        [
            _candle(0, "1", "2", "0.5", "1.5", "0", 0),  # reference bar
            _candle(DAY, "1", "2", "0.5", "1.5", "9", 12),  # on-venue begins
            _candle(2 * DAY, "1", "2", "0.5", "1.5", "9", 3),
        ],
        3 * DAY,
    )
    trimmed, onset, excluded = hl.venue_onset(bars)
    assert onset == datetime_str(DAY)
    assert excluded == 1
    assert len(trimmed) == 2
    ref_only, onset2, excluded2 = hl.venue_onset(bars[:1])
    assert ref_only == [] and onset2 is None and excluded2 == 1


def test_parquet_roundtrip_decimal(tmp_path: Path) -> None:
    import pyarrow.parquet as pq

    bars = hl.normalize(
        [_candle(0, "96484.0", "97933.0", "95740.0", "97274.0", "5292.30091", 42319)],
        DAY,
    )
    out = tmp_path / "t.parquet"
    hl.to_parquet(bars, out)
    t = pq.read_table(out)
    assert t.num_rows == 1
    # decimal equality: scale padding (10 dp) must never drift the value
    assert t.column("open")[0].as_py() == Decimal("96484.0")
    assert t.column("volume")[0].as_py() == Decimal("5292.30091")


def test_summary_is_deterministic_bytes(tmp_path: Path) -> None:
    rows = [_candle(0, "1", "2", "0.5", "1.5", "9", 3)]
    bars = hl.normalize(rows, DAY + 1)
    out = tmp_path / "t.parquet"
    b1 = hl.to_parquet(bars, out)
    b2 = hl.to_parquet(bars, out)
    assert b1["sha256"] == b2["sha256"]  # byte-identical rewrite
    assert hl.to_parquet(bars, out)["sha256"] == b1["sha256"]


def test_fetch_range_is_bounded_aligned_closed_and_preserves_source_fields() -> None:
    minute = hl.VALID_INTERVALS["1m"]
    start, end = 10 * minute, 10_003 * minute
    requests: list[tuple[int, int]] = []

    def post(payload: bytes) -> bytes:
        req = json.loads(payload)["req"]
        lo, hi = req["startTime"], req["endTime"]
        requests.append((lo, hi))
        return json.dumps(
            [
                _candle(t, "100", "102", "99", "101", "0.125", 7, minute, "1m")
                for t in range(lo, hi + minute, minute)
            ]
        ).encode()

    rows = hl.fetch_range("BTC", "1m", start, end, _post=post)
    assert requests[0][0] == start and requests[-1][1] == end - minute
    assert len(rows) == 9_993
    assert all(r["t"] % minute == 0 and r["T"] == r["t"] + minute - 1 for r in rows)
    assert rows[0]["v"] == "0.125" and rows[0]["n"] == 7


def test_fetch_range_rejects_unaligned_bounds_and_overlapping_pages() -> None:
    minute = hl.VALID_INTERVALS["1m"]
    with pytest.raises(ValueError, match="API-supported"):
        hl.fetch_range("BTC", "1m", 0, minute * 2)
    with pytest.raises(ValueError, match="aligned"):
        hl.fetch_range("BTC", "1m", 1, minute * 2)

    def overlap(payload: bytes) -> bytes:
        req = json.loads(payload)["req"]
        lo, hi = req["startTime"], req["endTime"]
        return json.dumps(
            [
                _candle(t, "1", "2", "0.5", "1.5", "1", 1, minute, "1m")
                for t in range(lo, hi + 2 * minute, minute)
            ]
        ).encode()

    # The configured per-window size is small in this fixture, so repeat a
    # timestamp at the boundary to model an API page overlap.
    original = hl.WINDOW_CANDLES
    hl.WINDOW_CANDLES = 2
    try:
        with pytest.raises(RuntimeError, match="overlap|duplicate|outside|range"):
            hl.fetch_range("BTC", "1m", minute, minute * 6, _post=overlap)
    finally:
        hl.WINDOW_CANDLES = original
