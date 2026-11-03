"""Unit tests for resample_1m_to_1d.py — synthetic rows, no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import resample_1m_to_1d as rs

DAY_MS = 86_400_000
MIN_MS = 60_000
# a fixed UTC day start (2026-11-01)
DAY0 = 1793491200000


def _minute(start_ms: int, px: str, vol: str = "1.0") -> dict:
    p = Decimal(px)
    return {
        "ts_day": rs._day_str(start_ms),
        "open_ms": start_ms,
        "close_ms": start_ms + MIN_MS - 1,
        "open": p,
        "high": p + Decimal(2),
        "low": p - Decimal(2),
        "close": p,
        "volume": Decimal(vol),
        "n_trades": 3,
    }


def _full_day(start_ms: int, base: int = 100) -> list[dict]:
    return [_minute(start_ms + i * MIN_MS, str(base + i / 1000)) for i in range(1440)]


def test_complete_day_aggregates_to_one_bar_with_correct_ohlcv():
    rows = _full_day(DAY0)
    bars, rep = rs.resample(rows)
    assert len(bars) == 1
    bar = bars[0]
    assert bar["ts_day"] == "2026-11-01"
    assert bar["open_ms"] == DAY0
    assert bar["close_ms"] == DAY0 + DAY_MS - 1
    assert bar["open"] == rows[0]["open"]
    assert bar["close"] == rows[-1]["close"]
    assert bar["high"] == max(r["high"] for r in rows)
    assert bar["low"] == min(r["low"] for r in rows)
    assert bar["volume"] == sum((r["volume"] for r in rows), Decimal(0))
    assert bar["n_trades"] == 1440 * 3
    assert rep["dropped_days"] == []


def test_shuffled_input_still_orders_by_open_ms():
    rows = _full_day(DAY0)
    rows = list(reversed(rows))
    bars, _ = rs.resample(rows)
    assert bars[0]["close"] == rows[0]["close"]  # rows[0] is the LAST minute


def test_edge_partial_days_are_dropped_not_papered_over():
    # capture starts mid-day then covers one full day: the partial edges must
    # never become day bars (their last 1m close is not the day's close)
    rows = _full_day(DAY0 - DAY_MS)[600:] + _full_day(DAY0) + _full_day(DAY0 + DAY_MS)[:600]
    bars, rep = rs.resample(rows)
    assert [b["ts_day"] for b in bars] == ["2026-11-01"]
    assert {d["day"] for d in rep["dropped_days"]} == {"2026-10-31", "2026-11-02"}
    assert all("edge-partial" in d["reason"] for d in rep["dropped_days"])


def test_interior_hole_is_refused_by_default():
    rows = _full_day(DAY0 - DAY_MS) + _full_day(DAY0)[:1000] + _full_day(DAY0 + DAY_MS)
    with pytest.raises(SystemExit) as exc:
        rs.resample(rows)
    assert "interior day 2026-11-01" in str(exc.value)


def test_interior_hole_can_be_excluded_explicitly():
    rows = _full_day(DAY0 - DAY_MS) + _full_day(DAY0)[:1000] + _full_day(DAY0 + DAY_MS)
    bars, rep = rs.resample(rows, allow_partial=True)
    assert {b["ts_day"] for b in bars} == {"2026-10-31", "2026-11-02"}
    hole = [d for d in rep["dropped_days"] if d["day"] == "2026-11-01"]
    assert hole and "interior-hole" in hole[0]["reason"]


def test_no_complete_day_is_an_error_not_an_empty_artifact():
    with pytest.raises(SystemExit) as exc:
        rs.resample(_full_day(DAY0)[:10])
    assert "no complete day" in str(exc.value)


def test_missing_minute_mid_session_makes_the_day_incomplete():
    rows = _full_day(DAY0)
    del rows[720]  # one missing minute, same row count as a 1439-bar day
    with pytest.raises(SystemExit):
        rs.resample(_full_day(DAY0 - DAY_MS) + rows + _full_day(DAY0 + DAY_MS))


def test_ts_day_disagreeing_with_open_ms_is_refused():
    rows = _full_day(DAY0)
    rows[0]["ts_day"] = "2026-11-02"
    with pytest.raises(SystemExit) as exc:
        rs.resample(rows)
    assert "disagrees with open_ms" in str(exc.value)


def test_interval_must_divide_a_utc_day():
    with pytest.raises(SystemExit):
        rs.resample(_full_day(DAY0), interval_ms=7_000)


def test_edge_day_kept_when_it_is_actually_complete():
    # a capture ending exactly on a day boundary yields a complete last day
    rows = _full_day(DAY0) + _full_day(DAY0 + DAY_MS)
    bars, rep = rs.resample(rows)
    assert len(bars) == 2
    assert rep["dropped_days"] == []
    assert bars[-1]["ts_day"] == datetime.fromtimestamp(
        (DAY0 + DAY_MS) / 1000, tz=UTC
    ).date().isoformat()
    assert bars[-1]["open_ms"] - bars[0]["open_ms"] == DAY_MS
    assert timedelta(milliseconds=DAY_MS).days == 1
