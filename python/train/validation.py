"""Calendar-day grouped combinatorial validation with a sealed final holdout."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import combinations


@dataclass(frozen=True)
class Fold:
    """One validation fold; holdout folds intentionally have no training rows."""

    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    is_holdout: bool = False


def _calendar_day(value: object) -> date:
    if isinstance(value, datetime):
        return value.astimezone(UTC).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value) / 1000, UTC).date()
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError(
                "numeric timestamps must be UTC epoch milliseconds"
            ) from exc
    raise ValueError(
        "timestamps must be UTC epoch milliseconds or datetime/date values"
    )


def calendar_cpcv_splits(
    timestamps: Sequence[datetime | date | int | float],
    *,
    n_splits: int,
    purge_bars: int,
    embargo_bars: int,
    holdout_start: datetime | date | float,
    n_test_splits: int = 1,
    label_end_timestamps: Sequence[datetime | date | int | float] | None = None,
    max_label_horizon_bars: int | None = None,
) -> list[Fold]:
    """Enumerate CPCV test-group combinations and append the sealed holdout.

    Purge/embargo distances are measured in input-row bars. Optional label end
    timestamps provide direct overlap checks; alternatively a maximum horizon
    in bars conservatively removes training rows whose labels reach a test or
    holdout boundary.
    """
    if (
        n_splits < 2
        or n_test_splits < 1
        or n_test_splits >= n_splits
        or purge_bars < 0
        or embargo_bars < 0
    ):
        raise ValueError(
            "n_splits >= 2, 1 <= n_test_splits < n_splits, and non-negative purge/embargo required"
        )
    if not timestamps:
        raise ValueError("timestamps cannot be empty")
    days = [_calendar_day(ts) for ts in timestamps]
    if label_end_timestamps is not None:
        if len(label_end_timestamps) != len(timestamps):
            raise ValueError("label_end_timestamps must match timestamps length")
        label_ends = [_calendar_day(ts) for ts in label_end_timestamps]
        if any(end < start for start, end in zip(days, label_ends)):
            raise ValueError("label end cannot precede its sample timestamp")
    else:
        label_ends = None
    if max_label_horizon_bars is not None and max_label_horizon_bars < 0:
        raise ValueError("max_label_horizon_bars must be non-negative")
    if days != sorted(days):
        raise ValueError("timestamps must be sorted")
    if len(set(days)) < n_splits:
        raise ValueError("not enough distinct calendar days for requested splits")
    boundary = _calendar_day(holdout_start)
    holdout = tuple(i for i, day in enumerate(days) if day >= boundary)
    pre = tuple(i for i, day in enumerate(days) if day < boundary)
    holdout_first = min(holdout) if holdout else len(days)
    if not pre or not holdout or len({days[i] for i in pre}) < n_splits:
        raise ValueError(
            "holdout_start must leave enough pre-holdout days and holdout rows"
        )

    grouped_days = sorted({days[i] for i in pre})
    if len(grouped_days) < n_splits:
        raise ValueError("not enough pre-holdout calendar groups for requested splits")
    chunks = [list(chunk) for chunk in _split_evenly(grouped_days, n_splits)]
    exclusion_radius = max(purge_bars, embargo_bars)
    folds: list[Fold] = []
    for chosen in combinations(chunks, n_test_splits):
        test_days = {day for chunk in chosen for day in chunk}
        test = tuple(i for i in pre if days[i] in test_days)
        test_set = set(test)
        train = tuple(
            i
            for i in pre
            if i not in test_set
            and all(abs(i - test_i) > exclusion_radius for test_i in test)
            and not (
                label_ends is not None
                and any(
                    days[i] <= label_ends[test_i] and label_ends[i] >= days[test_i]
                    for test_i in test
                )
            )
            and not (
                label_ends is None
                and max_label_horizon_bars is not None
                and _forward_label_reaches_test(i, max_label_horizon_bars, test)
            )
            and (label_ends is None or label_ends[i] < boundary)
            and (
                label_ends is not None
                or max_label_horizon_bars is None
                or i + max_label_horizon_bars < holdout_first
            )
        )
        if not train or not test:
            raise ValueError("purge/embargo leaves an empty train or test fold")
        folds.append(Fold(train, test))
    folds.append(Fold((), holdout, is_holdout=True))
    return folds


def _forward_label_reaches_test(
    index: int, horizon_bars: int, test_indices: tuple[int, ...]
) -> bool:
    """True when a forward label from ``index`` overlaps the next test bar.

    Only test rows after ``index`` matter because labels look forward. Using
    the first such test row keeps this O(log M) even when the test set is a
    combination of non-adjacent groups.
    """
    position = bisect_left(test_indices, index)
    if position == len(test_indices):
        return False
    return index + horizon_bars >= test_indices[position]


def _split_evenly(items: list[date], count: int) -> list[list[date]]:
    quotient, remainder = divmod(len(items), count)
    chunks: list[list[date]] = []
    offset = 0
    for i in range(count):
        size = quotient + (1 if i < remainder else 0)
        chunks.append(items[offset : offset + size])
        offset += size
    return chunks
