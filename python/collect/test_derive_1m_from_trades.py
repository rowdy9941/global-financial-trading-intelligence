"""Unit tests for derive_1m_from_trades.py — pure aggregation, no network."""

from __future__ import annotations

import gzip
import json
from decimal import Decimal

import derive_1m_from_trades as dt
import pytest

MIN = dt.MINUTE_MS
T0 = 1_793_700_000_000  # minute-aligned on purpose


def _print(
    ts: int, px: str = "77000.0", sz: str = "0.1", tid: int | None = None
) -> dict:
    """A row shaped as read_prints returns it: px/sz are Decimals, never floats."""
    return {
        "ts_ms": ts,
        "px": Decimal(px),
        "sz": Decimal(sz),
        "tid": tid if tid is not None else ts,
    }


def _write(path, rows: list[dict]) -> None:
    """Write rows as the recorder does: px/sz are decimal STRINGS on disk."""
    with gzip.open(path, "at", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({**r, "px": str(r["px"]), "sz": str(r["sz"])}) + "\n")


def test_read_prints_dedupes_tids_across_segments(tmp_path):
    a = tmp_path / "2026-11-03T12.jsonl.gz"
    b = tmp_path / "2026-11-03T13.jsonl.gz"
    _write(a, [_print(T0, tid=1), _print(T0 + 1000, tid=2)])
    _write(b, [_print(T0 + 1000, tid=2), _print(T0 + 2000, tid=3)])  # 2 repeats 1
    prints, cov = dt.read_prints([a, b])
    assert [p["tid"] for p in prints] == [1, 2, 3]
    assert cov["duplicates_dropped"] == 1
    assert cov["unique_prints"] == 3
    assert len(cov["segments"]) == 2
    assert cov["segments"][0]["start_ms"] == T0


def test_read_prints_counts_unparseable_lines_instead_of_crashing(tmp_path):
    path = tmp_path / "x.jsonl.gz"
    _write(path, [_print(T0)])
    with gzip.open(path, "at", encoding="utf-8") as fh:
        fh.write('{"ts_ms": 1, "px": "77')
    prints, cov = dt.read_prints([path])
    assert len(prints) == 1
    assert cov["bad_lines_dropped"] == 1


def test_bucket_minutes_ohlcv_and_order():
    prints = [
        _print(T0 + 5_000, px="100.0", sz="1.0", tid=1),
        _print(T0 + 1_000, px="90.0", sz="2.0", tid=2),  # arrives late, sorts first
        _print(T0 + 30_000, px="110.0", sz="3.0", tid=3),
        _print(T0 + MIN, px="120.0", sz="4.0", tid=4),
    ]
    prints.sort(key=lambda r: (r["ts_ms"], r["tid"]))
    bars = dt.bucket_minutes(prints, [{"start_ms": T0, "end_ms": T0 + 2 * MIN}])
    assert len(bars) == 2
    first = bars[0]
    assert first["open"] == Decimal("90.0")
    assert first["close"] == Decimal("110.0")
    assert first["high"] == Decimal("110.0")
    assert first["low"] == Decimal("90.0")
    assert first["volume"] == Decimal("6.0")
    assert first["n_trades"] == 3


def test_a_minute_is_covered_only_when_one_window_brackets_it_whole():
    prints = [_print(T0 + 30_000), _print(T0 + MIN + 30_000)]
    # window starts mid-way through the first minute -> that minute is NOT covered
    spans = [{"start_ms": T0 + 10_000, "end_ms": T0 + 2 * MIN}]
    bars = dt.bucket_minutes(prints, spans)
    assert bars[0]["covered"] is False
    assert bars[1]["covered"] is True


def test_two_windows_do_not_combine_into_coverage():
    """A gap between sessions must never be papered over by a neighbouring window."""
    prints = [_print(T0 + 1000)]
    spans = [
        {"start_ms": T0 - 5 * MIN, "end_ms": T0 + 1000},
        {"start_ms": T0 + MIN, "end_ms": T0 + 5 * MIN},
    ]
    bars = dt.bucket_minutes(prints, spans)
    assert bars[0]["covered"] is False


def _venue(minute: int, o: str, h: str, low: str, c: str, v: str, n: int) -> dict:
    return {
        "t": minute,
        "T": minute + MIN - 1,
        "o": o,
        "h": h,
        "l": low,
        "c": c,
        "v": v,
        "n": n,
    }


def test_compare_reports_exact_matches_and_field_level_diffs():
    bars = [
        {
            "open_ms": T0,
            "close_ms": T0 + MIN - 1,
            "open": Decimal(100),
            "high": Decimal(110),
            "low": Decimal(90),
            "close": Decimal(105),
            "volume": Decimal(3),
            "n_trades": 2,
            "covered": True,
        },
        {
            "open_ms": T0 + MIN,
            "close_ms": T0 + 2 * MIN - 1,
            "open": Decimal(105),
            "high": Decimal(106),
            "low": Decimal(104),
            "close": Decimal("105.5"),
            "volume": Decimal(1),
            "n_trades": 1,
            "covered": True,
        },
    ]
    venue = [
        _venue(T0, "100", "110", "90", "105", "3", 2),  # identical
        _venue(T0 + MIN, "105", "106", "104", "105.5", "2", 3),  # volume + n differ
        _venue(T0 + 2 * MIN, "1", "1", "1", "1", "1", 1),  # not captured at all
    ]
    out = dt.compare(bars, venue)
    assert out["compared"] == 2
    assert out["exact_matches"] == 1
    assert out["minutes_not_in_capture"] == 1
    assert out["all_identical"] is False
    diff = out["mismatch_examples"][0]["fields"]
    assert set(diff) == {"volume", "n_trades"}


def test_compare_skips_uncovered_minutes_and_says_so():
    bars = [
        {
            "open_ms": T0,
            "close_ms": T0 + MIN - 1,
            "open": Decimal(1),
            "high": Decimal(1),
            "low": Decimal(1),
            "close": Decimal(1),
            "volume": Decimal(1),
            "n_trades": 1,
            "covered": False,
        }
    ]
    out = dt.compare(bars, [_venue(T0, "1", "1", "1", "1", "1", 1)])
    assert out["compared"] == 0
    assert out["minutes_skipped_uncovered"] == 1
    assert out["all_identical"] is False


def test_matching_minutes_flags_each_minute_independently():
    bars = [
        {
            "open_ms": T0,
            "close_ms": T0 + MIN - 1,
            "open": Decimal(1),
            "high": Decimal(2),
            "low": Decimal(1),
            "close": Decimal(2),
            "volume": Decimal(5),
            "n_trades": 4,
            "covered": True,
        },
        {
            "open_ms": T0 + MIN,
            "close_ms": T0 + 2 * MIN - 1,
            "open": Decimal(1),
            "high": Decimal(2),
            "low": Decimal(1),
            "close": Decimal(2),
            "volume": Decimal(5),
            "n_trades": 4,
            "covered": True,
        },
    ]
    verdict = dt.matching_minutes(
        bars,
        [
            _venue(T0, "1", "2", "1", "2", "5", 4),
            _venue(T0 + MIN, "1", "2", "1", "2", "6", 5),  # short volume
        ],
    )
    assert verdict == {T0: True, T0 + MIN: False}


@pytest.mark.parametrize(
    "field", ["open", "high", "low", "close", "volume", "n_trades"]
)
def test_a_single_differing_field_disqualifies_the_minute(field):
    bar = {
        "open_ms": T0,
        "close_ms": T0 + MIN - 1,
        "open": Decimal(1),
        "high": Decimal(2),
        "low": Decimal(1),
        "close": Decimal(2),
        "volume": Decimal(5),
        "n_trades": 4,
        "covered": True,
    }
    good = _venue(T0, "1", "2", "1", "2", "5", 4)
    broken = dict(good)
    broken[
        {
            "open": "o",
            "high": "h",
            "low": "l",
            "close": "c",
            "volume": "v",
            "n_trades": "n",
        }[field]
    ] = "999" if field != "n_trades" else 999
    assert dt.matching_minutes([bar], [good]) == {T0: True}
    assert dt.matching_minutes([bar], [broken]) == {T0: False}
