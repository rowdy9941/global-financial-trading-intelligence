"""Causal, source-aware bar resampling and technical feature construction.

Input ``ts_ms`` values are UTC bar-open epoch milliseconds. ``FeatureSet``
timestamps are the corresponding UTC bar-close boundaries. Higher-frame values
are exposed only once the complete frame closes; incomplete windows are gaps,
not synthetic rows. Unavailable numeric values are NaN and always have a false
availability bit.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_INTERVAL_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_MS = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
_CORE_FEATURES = (
    "return_1",
    "momentum_5",
    "sma_3",
    "sma_10",
    "ema_3",
    "ema_10",
    "rsi_14",
    "macd",
    "macd_signal",
    "atr_14",
    "realized_vol_20",
    "range",
    "body",
    "volume_change_1",
    "volume_z_20",
    "trade_count_change_1",
    "trade_count_z_20",
    "vwap_20",
    "flow_1",
)


@dataclass(frozen=True)
class FeatureSet:
    """Immutable feature matrix and provenance contract."""

    timestamps: tuple[int, ...]
    values: tuple[tuple[float, ...], ...]
    feature_names: tuple[str, ...]
    availability: tuple[tuple[bool, ...], ...]
    source_hash: str
    feature_schema_hash: str


def _interval_ms(interval: str) -> int:
    match = _INTERVAL_RE.fullmatch(interval) if isinstance(interval, str) else None
    if not match:
        raise ValueError(f"invalid interval {interval!r}; use e.g. '1m', '4h', or '1d'")
    amount, unit = int(match.group(1)), match.group(2)
    if amount <= 0:
        raise ValueError(f"interval must be positive: {interval!r}")
    return amount * _UNIT_MS[unit]


def _ordered_validated(
    rows: list[dict], source_ms: int, volume_available: bool
) -> list[dict]:
    required = ("ts_ms", "open", "high", "low", "close")
    normalized: list[dict] = []
    for source_row in rows:
        missing = [key for key in required if key not in source_row]
        if missing:
            raise ValueError(f"bar missing required fields: {', '.join(missing)}")
        row = dict(source_row)
        try:
            row["ts_ms"] = int(row["ts_ms"])
            for key in ("open", "high", "low", "close"):
                row[key] = float(row[key])
        except (TypeError, ValueError) as exc:
            raise ValueError("bar timestamps and OHLC values must be numeric") from exc
        if any(not math.isfinite(row[key]) for key in ("open", "high", "low", "close")):
            raise ValueError("OHLC values must be finite")
        if row["high"] < max(row["open"], row["close"], row["low"]):
            raise ValueError("bar high violates OHLC ordering")
        if row["low"] > min(row["open"], row["close"], row["high"]):
            raise ValueError("bar low violates OHLC ordering")
        if row["ts_ms"] % source_ms:
            raise ValueError("bar timestamp is not aligned to the source interval")
        if (
            "volume_available" in row
            and row["volume_available"] is not volume_available
        ):
            raise ValueError("row volume_available conflicts with explicit metadata")
        row["volume_available"] = volume_available
        if "n_trades" in row:
            if "trade_count" in row and row["trade_count"] != row["n_trades"]:
                raise ValueError("trade_count conflicts with n_trades")
            row["trade_count"] = row.pop("n_trades")
        for key in ("volume", "trade_count"):
            if key in row and row[key] is not None:
                try:
                    row[key] = float(row[key])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} must be numeric or null") from exc
                if not math.isfinite(row[key]) or row[key] < 0:
                    raise ValueError(f"{key} must be finite and non-negative")
        normalized.append(row)
    normalized.sort(key=lambda row: row["ts_ms"])
    if any(a["ts_ms"] == b["ts_ms"] for a, b in itertools.pairwise(normalized)):
        raise ValueError("duplicate source bar timestamps")
    return normalized


def _bucket_start(ts_ms: int, interval_ms: int, tz: ZoneInfo) -> int:
    instant = datetime.fromtimestamp(ts_ms / 1000, UTC).astimezone(tz)
    local_midnight = datetime.combine(instant.date(), time.min, tzinfo=tz)
    anchor_ms = int(local_midnight.astimezone(UTC).timestamp() * 1000)
    return anchor_ms + ((ts_ms - anchor_ms) // interval_ms) * interval_ms


def _calendar_bucket_bounds(ts_ms: int, days: int, tz: ZoneInfo) -> tuple[int, int]:
    """Return UTC boundaries for a local-calendar bucket of ``days`` days."""
    local_date = datetime.fromtimestamp(ts_ms / 1000, UTC).astimezone(tz).date()
    epoch_date = date(1970, 1, 1)
    bucket_day = epoch_date + timedelta(
        days=((local_date - epoch_date).days // days) * days
    )
    next_bucket_day = bucket_day + timedelta(days=days)
    start = datetime.combine(bucket_day, time.min, tzinfo=tz)
    end = datetime.combine(next_bucket_day, time.min, tzinfo=tz)
    return (
        int(start.astimezone(UTC).timestamp() * 1000),
        int(end.astimezone(UTC).timestamp() * 1000),
    )


def resample_bars(
    rows: list[dict],
    interval: str,
    *,
    source_interval: str,
    volume_available: bool,
    session_timezone: str = "UTC",
) -> list[dict]:
    """Aggregate complete source bars into coarser UTC-aligned windows.

    ``ts_ms`` marks source-bar open. A result's ``ts_ms`` marks the bucket
    close, which is the first point at which its OHLC/volume may be consumed.
    Buckets are anchored to local midnight in ``session_timezone``; intervals
    must be an integer multiple of the source interval. Incomplete buckets are
    omitted, preserving gaps.
    """
    if not isinstance(volume_available, bool):
        raise TypeError("volume_available must be an explicit boolean")
    source_ms = _interval_ms(source_interval)
    target_ms = _interval_ms(interval)
    if target_ms < source_ms:
        raise ValueError("resampling interval must be coarser than source interval")
    if target_ms % source_ms:
        raise ValueError(
            "resampling interval must be an integer multiple of source interval"
        )
    try:
        tz = ZoneInfo(session_timezone)
    except (ZoneInfoNotFoundError, TypeError) as exc:
        raise ValueError(f"unknown session timezone {session_timezone!r}") from exc

    source = _ordered_validated(rows, source_ms, volume_available)
    if not source:
        return []
    if target_ms == source_ms:
        return [dict(row, ts_ms=row["ts_ms"] + source_ms) for row in source]
    calendar_days = (
        int(_INTERVAL_RE.fullmatch(interval).group(1))
        if _INTERVAL_RE.fullmatch(interval).group(2) == "d"
        else None
    )
    buckets: dict[int, list[dict]] = {}
    bucket_ends: dict[int, int] = {}
    for row in source:
        if calendar_days is not None:
            start, end = _calendar_bucket_bounds(row["ts_ms"], calendar_days, tz)
            bucket_ends[start] = end
        else:
            start = _bucket_start(row["ts_ms"], target_ms, tz)
        buckets.setdefault(start, []).append(row)

    result: list[dict] = []
    for start, members in sorted(buckets.items()):
        members.sort(key=lambda row: row["ts_ms"])
        end = bucket_ends.get(start, start + target_ms)
        expected_times = list(range(start, end, source_ms))
        if [row["ts_ms"] for row in members] != expected_times:
            continue
        first, last = members[0], members[-1]
        has_volume = all(
            row["volume_available"]
            and row.get("volume") is not None
            and math.isfinite(row["volume"])
            for row in members
        )
        out = {
            "ts_ms": end,
            "open": first["open"],
            "high": max(row["high"] for row in members),
            "low": min(row["low"] for row in members),
            "close": last["close"],
            "volume_available": has_volume,
            "_source_interval": interval,
            "_source_bar_count": len(members),
        }
        if any("volume" in row for row in members):
            out["volume"] = (
                sum(row["volume"] for row in members)
                if all(
                    "volume" in row
                    and row["volume"] is not None
                    and math.isfinite(row["volume"])
                    for row in members
                )
                else None
            )
        if any("trade_count" in row for row in members):
            out["trade_count"] = (
                sum(row["trade_count"] for row in members)
                if all(
                    "trade_count" in row
                    and row["trade_count"] is not None
                    and math.isfinite(row["trade_count"])
                    for row in members
                )
                else None
            )
        result.append(out)
    return result


def _number(value: float | None) -> float:
    return float("nan") if value is None or not math.isfinite(value) else float(value)


def _rolling_mean(items: list[float], width: int, end: int) -> float | None:
    if end + 1 < width:
        return None
    window = items[end - width + 1 : end + 1]
    return sum(window) / width if all(math.isfinite(x) for x in window) else None


def _ema(items: list[float], span: int) -> list[float | None]:
    out: list[float | None] = [None] * len(items)
    alpha = 2.0 / (span + 1)
    current: float | None = None
    for i, value in enumerate(items):
        if not math.isfinite(value):
            current = None
            continue
        if current is None:
            if i + 1 >= span and all(
                math.isfinite(v) for v in items[i + 1 - span : i + 1]
            ):
                current = sum(items[i + 1 - span : i + 1]) / span
        else:
            current = alpha * value + (1.0 - alpha) * current
        out[i] = current
    return out


def _features_for_bars(
    bars: list[dict], interval_ms: int
) -> tuple[list[str], list[list[float]], list[list[bool]]]:
    """Compute indicators independently per continuous run of observed bars."""
    if not bars:
        return list(_CORE_FEATURES), [], []
    split_points = [
        i
        for i in range(1, len(bars))
        if bars[i]["ts_ms"] - bars[i - 1]["ts_ms"] != interval_ms
    ]
    starts = [0, *split_points]
    ends = [*split_points, len(bars)]
    parts = [_features_contiguous(bars[start:end]) for start, end in zip(starts, ends)]
    names = parts[0][0]
    return (
        names,
        [row for _, rows, _ in parts for row in rows],
        [row for _, _, masks in parts for row in masks],
    )


def _features_contiguous(
    bars: list[dict],
) -> tuple[list[str], list[list[float]], list[list[bool]]]:
    names = list(_CORE_FEATURES)
    closes = [row["close"] for row in bars]
    volumes = [_number(row.get("volume")) for row in bars]
    counts = [_number(row.get("trade_count")) for row in bars]
    returns = [float("nan")]
    true_ranges = [bars[0]["high"] - bars[0]["low"]] if bars else []
    for i in range(1, len(bars)):
        prev_close = closes[i - 1]
        returns.append(closes[i] / prev_close - 1.0 if prev_close else float("nan"))
        true_ranges.append(
            max(
                bars[i]["high"] - bars[i]["low"],
                abs(bars[i]["high"] - prev_close),
                abs(bars[i]["low"] - prev_close),
            )
        )
    ema3, ema10, ema12, ema26 = (_ema(closes, n) for n in (3, 10, 12, 26))
    macd = [
        (a - b) if a is not None and b is not None else None
        for a, b in zip(ema12, ema26)
    ]
    macd_values = [_number(v) for v in macd]
    macd_signal = _ema(macd_values, 9)

    out_values: list[list[float]] = []
    out_masks: list[list[bool]] = []
    for i, row in enumerate(bars):
        gains = [max(0.0, returns[j]) for j in range(max(1, i - 13), i + 1)]
        losses = [max(0.0, -returns[j]) for j in range(max(1, i - 13), i + 1)]
        rsi = None
        if i >= 14 and len(gains) == 14:
            avg_gain, avg_loss = sum(gains) / 14, sum(losses) / 14
            rsi = (
                100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
            )
        rv = None
        recent_returns = returns[max(1, i - 19) : i + 1]
        if len(recent_returns) == 20 and all(math.isfinite(x) for x in recent_returns):
            mean = sum(recent_returns) / 20
            rv = math.sqrt(sum((x - mean) ** 2 for x in recent_returns) / 20)
        vol_ok = row.get("volume_available", False) is True and math.isfinite(
            volumes[i]
        )
        # Trade counts are a separate source field, not volume. Keep them
        # usable if D3 says volume is unavailable but the source has counts.
        count_ok = math.isfinite(counts[i])
        volume_change = None
        count_change = None
        if (
            i > 0
            and vol_ok
            and bars[i - 1].get("volume_available", False) is True
            and math.isfinite(volumes[i - 1])
        ):
            previous = volumes[i - 1]
            volume_change = (volumes[i] / previous - 1.0) if previous else None
        if i > 0 and count_ok and math.isfinite(counts[i - 1]):
            previous_count = counts[i - 1]
            count_change = (
                (counts[i] / previous_count - 1.0) if previous_count else None
            )
        vwap = None
        flow = None
        if vol_ok:
            lo = max(0, i - 19)
            window = bars[lo : i + 1]
            total_volume = sum(
                r["volume"] for r in window if r.get("volume") is not None
            )
            if (
                len(window) == min(20, i + 1)
                and all(
                    r.get("volume_available") is True
                    and r.get("volume") is not None
                    and math.isfinite(r["volume"])
                    for r in window
                )
                and total_volume > 0
            ):
                vwap = (
                    sum(
                        ((r["high"] + r["low"] + r["close"]) / 3) * r["volume"]
                        for r in window
                    )
                    / total_volume
                )
            flow = (row["close"] - row["open"]) * volumes[i]
        values = [
            returns[i],
            (closes[i] / closes[i - 5] - 1.0)
            if i >= 5 and closes[i - 5]
            else float("nan"),
            _rolling_mean(closes, 3, i),
            _rolling_mean(closes, 10, i),
            ema3[i],
            ema10[i],
            rsi,
            macd[i],
            macd_signal[i],
            _rolling_mean(true_ranges, 14, i),
            rv,
            (row["high"] - row["low"]) / row["close"] if row["close"] else None,
            (row["close"] - row["open"]) / row["open"] if row["open"] else None,
            volume_change,
            _zscore(volumes, i, 20) if vol_ok else None,
            count_change,
            _zscore(counts, i, 20) if count_ok else None,
            vwap,
            flow,
        ]
        mask = [v is not None and math.isfinite(float(v)) for v in values]
        out_values.append([_number(v) for v in values])
        out_masks.append(mask)
    return names, out_values, out_masks


def _zscore(values: list[float], end: int, width: int) -> float | None:
    if end + 1 < width:
        return None
    window = values[end + 1 - width : end + 1]
    if not all(math.isfinite(x) for x in window):
        return None
    mean = sum(window) / width
    variance = sum((x - mean) ** 2 for x in window) / width
    return (values[end] - mean) / math.sqrt(variance) if variance > 0 else None


def _canonical_hash(rows: list[dict]) -> str:
    payload = json.dumps(
        rows, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_features(
    rows: list[dict],
    *,
    source_interval: str,
    volume_available: bool,
    frames: tuple[str, ...] = ("5m", "15m", "1h", "4h", "1d"),
) -> FeatureSet:
    """Build sorted source-close observations with point-in-time frame features.

    Source rows are immutable bar records whose ``ts_ms`` is open time. Frame
    feature vectors are forward-aligned only from bars whose close boundary is
    no later than that source row's close. ``frames`` are additional derived
    frames; source-resolution features are always included as well.
    """
    source_ms = _interval_ms(source_interval)
    if not isinstance(volume_available, bool):
        raise TypeError("volume_available must be an explicit boolean")
    source = _ordered_validated(rows, source_ms, volume_available)
    if not source:
        raise ValueError("at least one source bar is required")
    frame_bars: dict[str, list[dict]] = {
        source_interval: [dict(r, ts_ms=r["ts_ms"] + source_ms) for r in source]
    }
    for frame in frames:
        target_ms = _interval_ms(frame)
        if target_ms < source_ms or target_ms % source_ms:
            raise ValueError(
                f"frame {frame!r} must be an integer-multiple coarser than source interval"
            )
        if frame in frame_bars:
            continue
        frame_bars[frame] = resample_bars(
            source,
            frame,
            source_interval=source_interval,
            volume_available=volume_available,
        )

    frame_series = {
        name: _features_for_bars(bars, _interval_ms(name))
        for name, bars in frame_bars.items()
    }
    feature_names = tuple(
        f"{frame}_{name}"
        for frame, (names, _, _) in frame_series.items()
        for name in names
    )
    timestamps = tuple(row["ts_ms"] + source_ms for row in source)
    values: list[tuple[float, ...]] = []
    masks: list[tuple[bool, ...]] = []
    frame_timestamps = {
        name: [bar["ts_ms"] for bar in bars] for name, bars in frame_bars.items()
    }
    for decision_ts in timestamps:
        row_values: list[float] = []
        row_masks: list[bool] = []
        for frame, (names, frame_values, frame_masks) in frame_series.items():
            # Close timestamps preserve exact-boundary availability, without
            # rescanning each higher-frame series for every source observation.
            available_i = bisect_right(frame_timestamps[frame], decision_ts) - 1
            if available_i < 0:
                row_values.extend([float("nan")] * len(names))
                row_masks.extend([False] * len(names))
            else:
                row_values.extend(frame_values[available_i])
                row_masks.extend(frame_masks[available_i])
        values.append(tuple(row_values))
        masks.append(tuple(row_masks))

    # Hash canonical, validated source content, independent of input ordering.
    normalized_for_hash = [
        {k: v for k, v in row.items() if not k.startswith("_")} for row in source
    ]
    source_payload = {
        "source_interval": source_interval,
        "volume_available": volume_available,
        "rows": normalized_for_hash,
    }
    schema_payload = {
        "version": 1,
        "timestamp_semantics": "UTC close boundary; input ts_ms is open",
        "source_interval": source_interval,
        "volume_available": volume_available,
        "frames": list(frame_bars),
        "features": list(feature_names),
        "warmup": {
            "sma": [3, 10],
            "ema": [3, 10, 12, 26],
            "rsi": 14,
            "atr": 14,
            "realized_vol": 20,
            "zscore": 20,
            "vwap": 20,
        },
    }
    return FeatureSet(
        timestamps=timestamps,
        values=tuple(values),
        feature_names=feature_names,
        availability=tuple(masks),
        source_hash=_canonical_hash(source_payload),
        feature_schema_hash=hashlib.sha256(
            json.dumps(schema_payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest(),
    )
