import math
from datetime import UTC, datetime

import pytest

from .features import build_features, resample_bars


def minute_rows(count, *, start_ms=0, missing=(), volume_available=True):
    rows = []
    for index in range(count):
        if index in missing:
            continue
        close = 100.0 + index
        rows.append(
            {
                "ts_ms": start_ms + index * 60_000,
                "open": close - 0.5,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": float(index + 1) if volume_available else 0.0,
                "trade_count": index + 2,
                "volume_available": volume_available,
            }
        )
    return rows


def test_resample_uses_exact_utc_boundaries_and_sums_real_volume():
    rows = minute_rows(10)

    bars = resample_bars(rows, "5m", source_interval="1m", volume_available=True)

    assert [bar["ts_ms"] for bar in bars] == [300_000, 600_000]
    assert [bar["open"] for bar in bars] == [99.5, 104.5]
    assert [bar["close"] for bar in bars] == [104.0, 109.0]
    assert [bar["volume"] for bar in bars] == [15.0, 40.0]
    assert [bar["trade_count"] for bar in bars] == [20, 45]
    assert all(bar["volume_available"] for bar in bars)


def test_resampling_preserves_missing_windows_and_never_fabricates_rows():
    rows = minute_rows(15, missing={7})

    bars = resample_bars(rows, "5m", source_interval="1m", volume_available=True)

    assert [bar["ts_ms"] for bar in bars] == [300_000, 900_000]


def test_indicator_warmup_restarts_after_source_gap():
    result = build_features(
        minute_rows(40, missing={20}),
        source_interval="1m",
        volume_available=True,
        frames=(),
    )
    row_at_21 = result.timestamps.index(22 * 60_000)
    return_idx = result.feature_names.index("1m_return_1")
    sma_idx = result.feature_names.index("1m_sma_3")

    assert not result.availability[row_at_21][return_idx]
    assert not result.availability[row_at_21][sma_idx]


def test_rejects_finer_or_invalid_interval_conversion():
    with pytest.raises(ValueError, match="coarser"):
        resample_bars(
            minute_rows(10), "30s", source_interval="1m", volume_available=True
        )
    with pytest.raises(ValueError, match="interval"):
        resample_bars(
            minute_rows(10), "5 minutes", source_interval="1m", volume_available=True
        )


def test_higher_timeframe_features_are_unavailable_until_bar_close():
    # At 00:05, the 00:00-00:05 five-minute bar is complete. At 00:04 it is not.
    rows = minute_rows(22)
    result = build_features(
        rows, source_interval="1m", volume_available=True, frames=("5m",)
    )

    position = {int(ts): i for i, ts in enumerate(result.timestamps)}
    sma_name = "5m_sma_3"
    sma_idx = result.feature_names.index(sma_name)
    range_idx = result.feature_names.index("5m_range")
    assert not result.availability[position[240_000]][sma_idx]
    assert not result.availability[position[240_000]][range_idx]
    assert not result.availability[position[300_000]][sma_idx]
    assert result.availability[position[300_000]][range_idx]
    # First 5m bar is available at 05:00, but SMA(3) still has indicator warm-up.
    assert not result.availability[position[360_000]][sma_idx]
    # By the end of the third completed 5m bar, its rolling SMA is available.
    assert result.availability[position[900_000]][sma_idx]


def test_histdata_volume_features_are_masked_not_observed_as_zero():
    result = build_features(
        minute_rows(80, volume_available=False),
        source_interval="1m",
        volume_available=False,
        frames=(),
    )

    volume_indices = [
        i
        for i, name in enumerate(result.feature_names)
        if any(token in name for token in ("volume", "vwap", "flow"))
    ]
    assert volume_indices
    assert all(not row[i] for row in result.availability for i in volume_indices)
    assert all(
        math.isnan(value)
        for row in result.values
        for i, value in enumerate(row)
        if i in volume_indices
    )


def test_trade_count_features_are_available_without_volume():
    result = build_features(
        minute_rows(40, volume_available=False),
        source_interval="1m",
        volume_available=False,
        frames=(),
    )
    count_idx = result.feature_names.index("1m_trade_count_change_1")
    assert any(row[count_idx] for row in result.availability)


def test_feature_set_sorts_rows_and_hashes_are_deterministic():
    rows = minute_rows(80)
    first = build_features(
        rows, source_interval="1m", volume_available=True, frames=("5m",)
    )
    second = build_features(
        list(reversed(rows)),
        source_interval="1m",
        volume_available=True,
        frames=("5m",),
    )

    assert list(first.timestamps) == sorted(first.timestamps)
    assert first.source_hash == second.source_hash
    assert first.feature_schema_hash == second.feature_schema_hash
    assert all(
        (math.isnan(a) and math.isnan(b)) or a == b
        for row_a, row_b in zip(first.values, second.values)
        for a, b in zip(row_a, row_b)
    )
    assert len(first.values) == len(first.availability)
    assert all(len(row) == len(first.feature_names) for row in first.values)
    assert all(len(row) == len(first.feature_names) for row in first.availability)


def test_session_timezone_controls_bucket_boundaries():
    # 00:00 UTC is 05:30 in Kolkata; the 5m boundary remains 00:00 UTC.
    rows = minute_rows(5)
    bars = resample_bars(
        rows,
        "5m",
        source_interval="1m",
        volume_available=True,
        session_timezone="Asia/Kolkata",
    )
    assert [bar["ts_ms"] for bar in bars] == [300_000]


def test_timestamps_are_utc_epoch_milliseconds():
    rows = minute_rows(1)
    result = build_features(
        rows, source_interval="1m", volume_available=True, frames=()
    )
    assert (
        datetime.fromtimestamp(int(result.timestamps[0]) / 1000, UTC).isoformat()
        == "1970-01-01T00:01:00+00:00"
    )


def test_resampling_requires_explicit_source_interval_and_checks_each_timestamp():
    rows = minute_rows(2)
    with pytest.raises(TypeError, match="source_interval"):
        resample_bars(rows, "2m", volume_available=True)
    rows[1]["ts_ms"] += 30_000
    with pytest.raises(ValueError, match="aligned"):
        resample_bars(rows, "2m", source_interval="1m", volume_available=True)


def test_volume_availability_is_explicit_and_conflicting_row_flag_fails_closed():
    rows = minute_rows(3, volume_available=True)
    with pytest.raises(ValueError, match="conflicts"):
        build_features(rows, source_interval="1m", volume_available=False, frames=())
    rows = minute_rows(30, volume_available=True)
    result = build_features(
        rows, source_interval="1m", volume_available=True, frames=()
    )
    volume_idx = result.feature_names.index("1m_flow_1")
    assert any(row[volume_idx] for row in result.availability)


def test_source_and_feature_hashes_pin_source_interval_and_volume_semantics():
    rows = minute_rows(3)
    one_minute = build_features(
        rows, source_interval="1m", volume_available=True, frames=()
    )
    one_second = build_features(
        rows, source_interval="1s", volume_available=True, frames=()
    )
    no_volume_rows = [dict(row) for row in rows]
    for row in no_volume_rows:
        row.pop("volume_available")
    no_volume = build_features(
        no_volume_rows,
        source_interval="1m",
        volume_available=False,
        frames=(),
    )

    assert one_minute.source_hash != one_second.source_hash
    assert one_minute.feature_schema_hash != one_second.feature_schema_hash
    assert one_minute.source_hash != no_volume.source_hash
    assert one_minute.feature_schema_hash != no_volume.feature_schema_hash


def test_hyperliquid_n_trades_populates_trade_count_features():
    rows = minute_rows(30)
    for row in rows:
        row["n_trades"] = row.pop("trade_count")
    result = build_features(
        rows, source_interval="1m", volume_available=True, frames=()
    )
    idx = result.feature_names.index("1m_trade_count_change_1")
    assert any(result.availability[i][idx] for i in range(len(rows)))


def test_no_lookahead_at_higher_frame_close_boundaries():
    result = build_features(
        minute_rows(20), source_interval="1m", volume_available=True, frames=("5m",)
    )
    idx = result.feature_names.index("5m_range")
    by_timestamp = dict(zip(result.timestamps, result.availability))
    assert not by_timestamp[240_000][idx]
    assert by_timestamp[300_000][idx]


def test_null_volume_and_trade_count_are_unavailable_without_fake_resample_sums():
    rows = minute_rows(10)
    rows[2]["volume"] = None
    rows[7]["trade_count"] = None

    result = build_features(
        rows,
        source_interval="1m",
        volume_available=True,
        frames=(),
    )
    volume_idx = result.feature_names.index("1m_flow_1")
    count_idx = result.feature_names.index("1m_trade_count_change_1")
    assert not result.availability[2][volume_idx]
    assert math.isnan(result.values[2][volume_idx])
    assert not result.availability[7][count_idx]
    assert math.isnan(result.values[7][count_idx])
    # Counts remain independent from volume availability and null volume does
    # not suppress a valid trade-count feature.
    assert result.availability[2][count_idx]

    bars = resample_bars(rows, "5m", source_interval="1m", volume_available=True)
    assert len(bars) == 2
    assert bars[0]["volume"] is None
    assert bars[0]["trade_count"] == sum(row["trade_count"] for row in rows[:5])
    assert bars[1]["volume"] == sum(row["volume"] for row in rows[5:])
    assert bars[1]["trade_count"] is None
    assert bars[0]["volume_available"] is False


@pytest.mark.parametrize(
    ("day", "expected_hours", "close_hour"),
    [("2026-03-08", 23, 4), ("2026-11-01", 25, 5)],
)
def test_daily_resampling_uses_local_calendar_days_across_dst(
    day, expected_hours, close_hour
):
    from datetime import date, time, timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    local_start = datetime.combine(date.fromisoformat(day), time.min, tzinfo=tz)
    local_end = datetime.combine(
        date.fromisoformat(day) + timedelta(days=1), time.min, tzinfo=tz
    )
    start_ms = int(local_start.astimezone(UTC).timestamp() * 1000)
    end_ms = int(local_end.astimezone(UTC).timestamp() * 1000)
    rows = [
        {
            "ts_ms": start_ms + hour * 3_600_000,
            "open": 100.0 + hour,
            "high": 101.0 + hour,
            "low": 99.0 + hour,
            "close": 100.5 + hour,
            "volume": 1.0,
            "trade_count": 2.0,
        }
        for hour in range(expected_hours)
    ]

    bars = resample_bars(
        rows,
        "1d",
        source_interval="1h",
        volume_available=True,
        session_timezone="America/New_York",
    )

    assert len(rows) == expected_hours
    assert len(bars) == 1
    assert bars[0]["ts_ms"] == end_ms
    assert datetime.fromtimestamp(end_ms / 1000, UTC).hour == close_hour
    assert bars[0]["open"] == rows[0]["open"]
    assert bars[0]["close"] == rows[-1]["close"]
    assert bars[0]["volume"] == float(expected_hours)
    assert bars[0]["trade_count"] == float(expected_hours * 2)


def test_fixed_utc_duration_keeps_24_hour_bucket_across_dst():
    from datetime import date, time
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    # The explicit 24h interval remains a fixed-duration bucket; the calendar
    # day beginning after the spring transition is again exactly 24 UTC hours.
    local_start = datetime.combine(date(2026, 3, 9), time.min, tzinfo=tz)
    start_ms = int(local_start.astimezone(UTC).timestamp() * 1000)
    rows = [
        {
            "ts_ms": start_ms + hour * 3_600_000,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 1.0,
        }
        for hour in range(24)
    ]

    bars = resample_bars(
        rows,
        "24h",
        source_interval="1h",
        volume_available=True,
        session_timezone="America/New_York",
    )

    assert len(bars) == 1
    assert bars[0]["ts_ms"] == start_ms + 24 * 3_600_000
