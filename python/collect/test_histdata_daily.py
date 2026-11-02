"""Unit tests for histdata_daily.py — fixtures of the REAL ASCII M1 format,
token extraction, and the fail-closed hole classification. No network.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import histdata_daily as hd

M1_TEXT = """20260102 170000;2062.598000;2064.525000;2062.405000;2064.235000;0
20260102 170100;2063.795000;2064.135000;2063.435000;2064.125000;0
20260102 170200;2064.145000;2064.165000;2063.905000;2063.995000;0
20260102 235900;2064.000000;2064.500000;2063.900000;2064.200000;0
20260103 000000;2064.235000;2065.526000;2063.894000;2064.025000;0
20260103 235900;2065.185000;2065.194000;2064.515000;2064.515000;0

"""


def test_parse_m1_real_format() -> None:
    rows = hd.parse_m1(M1_TEXT)
    assert len(rows) == 6
    ts_ms, o, h, _l, c = rows[0]
    # 2026-01-02 17:00:00 UTC
    assert ts_ms == 1767373200000
    assert o == Decimal("2062.598000")
    assert h == Decimal("2064.525000")
    assert c == Decimal("2064.235000")


def test_raw_m1_retains_every_price_record_and_marks_volume_unavailable(
    tmp_path,
) -> None:
    records = hd.raw_m1_records(hd._parse_m1_source(M1_TEXT))
    assert len(records) == 6
    assert [r["ts_ms"] for r in records] == sorted(r["ts_ms"] for r in records)
    assert records[0] == {
        "ts_ms": 1767373200000,
        "open": Decimal("2062.598000"),
        "high": Decimal("2064.525000"),
        "low": Decimal("2062.405000"),
        "close": Decimal("2064.235000"),
        "volume": Decimal(0),
        "volume_available": False,
    }
    artifact = hd.to_raw_parquet(records, tmp_path / "raw.parquet")
    import pyarrow.parquet as pq

    table = pq.read_table(artifact["path"])
    assert table.column_names == [
        "ts_ms",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "volume_available",
    ]
    assert table["volume_available"].to_pylist() == [False] * 6


def test_raw_m1_rejects_duplicate_or_out_of_order_timestamps() -> None:
    rows = hd._parse_m1_source(M1_TEXT)
    with pytest.raises(ValueError, match="strictly increasing"):
        hd.raw_m1_records([rows[1], rows[0]])
    with pytest.raises(ValueError, match="strictly increasing"):
        hd.raw_m1_records([rows[0], rows[0]])


def test_raw_m1_refuses_nonzero_histdata_volume() -> None:
    rows = hd._parse_m1_source(M1_TEXT)
    with pytest.raises(ValueError, match="expected source zero"):
        hd.raw_m1_records([(*rows[0][:5], Decimal(1))])


def test_daily_capture_checks_source_volume_without_raw_output(
    tmp_path, monkeypatch
) -> None:
    rows = hd._parse_m1_source(M1_TEXT)
    rows[0] = (*rows[0][:5], Decimal(1))
    monkeypatch.setattr(hd, "_opener", lambda: (None, None))
    monkeypatch.setattr(hd, "fetch_year_zip", lambda _op, _year: b"zip")
    monkeypatch.setattr(hd, "unzip_csv_member", lambda _raw, _year: M1_TEXT)
    monkeypatch.setattr(hd, "_parse_m1_source", lambda _text: rows)

    with pytest.raises(ValueError, match="expected source zero"):
        hd.capture(2026, 2026, tmp_path / "daily.parquet")


def test_daily_aggregation_does_not_fill_missing_source_minutes() -> None:
    rows = hd.parse_m1(M1_TEXT)
    # Fixture minutes are sparse: aggregation counts only observed source rows.
    assert sum(b["n_minutes"] for b in hd.aggregate_daily(rows)) == len(rows)


def test_aggregate_daily_utc_ohlc() -> None:
    bars = hd.aggregate_daily(hd.parse_m1(M1_TEXT))
    assert [b["ts_day"] for b in bars] == ["2026-01-02", "2026-01-03"]
    d1 = bars[0]
    assert d1["open"] == Decimal("2062.598000")  # first M1 row of the day
    assert d1["high"] == Decimal("2064.525000")  # max across all rows
    assert d1["low"] == Decimal("2062.405000")
    assert d1["close"] == Decimal("2064.200000")  # LAST M1 row of the day
    assert d1["n_minutes"] == 4
    assert d1["volume"] == Decimal(0)  # source column ≡0 — honest, documented


def test_zip_member_name_is_derived_not_trusted(tmp_path) -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../etc/evil.csv", "x")
    with pytest.raises(RuntimeError, match="unexpected members"):
        hd.unzip_csv_member(buf.getvalue(), 2030)


def test_bad_zip_aborts() -> None:
    with pytest.raises(RuntimeError, match="non-zip"):
        hd._ensure_zip(b"<html>error</html>", 2030)
    assert hd._ensure_zip(b"PK\x03\x04rest", 2030) == b"PK\x03\x04rest"


def test_integrity_classifies_saturday_only() -> None:
    # Fri Jan 2 present; Sat 3 + Sun 4 missing + Mon 5 present → 2 absent
    bars2 = [{"ts_day": "2026-01-02"}, {"ts_day": "2026-01-05"}]
    _errs, holes, n_absent = hd.integrity_check(bars2)
    assert n_absent == 2
    by_day = {h["window_from"]: h for h in holes}
    assert by_day["2026-01-03"]["justification"].startswith(
        "venue closed (Saturday)"
    )  # justified: structural weekend
    assert by_day["2026-01-04"]["justification"] == ""  # Sunday w/o rows: OPEN
    assert all(h["status"] == "open" for h in holes)


def test_full_range_year_bounds_refused() -> None:
    # a single-bar capture must abort as too-few-rows, never ship silently
    bars = [{"ts_day": "2026-01-02"}]
    errs, _holes, _n = hd.integrity_check(bars)
    assert errs == ["too few rows"]
