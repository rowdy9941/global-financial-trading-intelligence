from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

import pytest

from .alpha_search import run_search
from .metrics import evaluate_trades
from .validation import calendar_cpcv_splits


def _days(start: date, count: int) -> list[datetime]:
    return [
        datetime.combine(
            start.fromordinal(start.toordinal() + i), datetime.min.time(), UTC
        )
        for i in range(count)
    ]


class _Features:
    feature_schema_hash = "feature-sha256"


def test_calendar_cpcv_groups_days_purges_embargoes_and_reserves_holdout():
    timestamps = _days(date(2026, 1, 1), 12)
    folds = calendar_cpcv_splits(
        timestamps,
        n_splits=3,
        purge_bars=2,
        embargo_bars=1,
        holdout_start=timestamps[9],
    )
    assert len(folds) == 4  # three CPCV folds and one final holdout
    holdout = folds[-1]
    assert holdout.is_holdout
    assert holdout.test_indices == tuple(range(9, 12))
    assert all(
        i < 9 for fold in folds[:-1] for i in fold.train_indices + fold.test_indices
    )
    for fold in folds[:-1]:
        assert set(fold.train_indices).isdisjoint(fold.test_indices)
        assert all(
            abs(a - b) > 2 for a in fold.train_indices for b in fold.test_indices
        )


def test_cpcv_enumerates_combinations_and_purges_label_horizon_at_test_and_holdout():
    timestamps = _days(date(2026, 1, 1), 14)
    label_ends = timestamps[:]
    label_ends[8] = timestamps[13]
    folds = calendar_cpcv_splits(
        timestamps,
        n_splits=4,
        n_test_splits=2,
        purge_bars=0,
        embargo_bars=0,
        holdout_start=timestamps[12],
        label_end_timestamps=label_ends,
    )
    search_folds = [fold for fold in folds if not fold.is_holdout]
    assert len(search_folds) == 6  # C(4, 2), not four walk-forward chunks
    assert all(12 not in f.train_indices + f.test_indices for f in search_folds)
    for fold in search_folds:
        assert set(fold.train_indices).isdisjoint(fold.test_indices)
    # Index 8's label reaches the final holdout boundary, so it cannot train.
    assert all(8 not in f.train_indices for f in search_folds)


def test_holdout_label_overlap_can_be_purged_with_explicit_horizon():
    timestamps = _days(date(2026, 1, 1), 10)
    folds = calendar_cpcv_splits(
        timestamps,
        n_splits=3,
        purge_bars=1,
        embargo_bars=0,
        holdout_start=timestamps[8],
        max_label_horizon_bars=2,
    )
    assert all(6 not in f.train_indices for f in folds[:-1])


def test_horizon_purge_covers_gaps_between_non_adjacent_test_groups():
    timestamps = _days(date(2026, 1, 1), 16)
    horizon = 1
    folds = calendar_cpcv_splits(
        timestamps,
        n_splits=4,
        n_test_splits=2,
        purge_bars=0,
        embargo_bars=0,
        holdout_start=timestamps[15],
        max_label_horizon_bars=horizon,
    )
    search_folds = [fold for fold in folds if not fold.is_holdout]
    assert search_folds
    for fold in search_folds:
        for train_i in fold.train_indices:
            assert not any(
                test_i > train_i and train_i + horizon >= test_i
                for test_i in fold.test_indices
            ), (fold, train_i)


def test_cpcv_requires_calendar_timestamp_contract_and_valid_boundaries():
    with pytest.raises(ValueError):
        calendar_cpcv_splits(
            [1, 2, 3], n_splits=2, purge_bars=1, embargo_bars=1, holdout_start=3
        )
    with pytest.raises(ValueError):
        calendar_cpcv_splits(
            _days(date(2026, 1, 1), 4),
            n_splits=2,
            purge_bars=0,
            embargo_bars=1,
            holdout_start=_days(date(2026, 1, 1), 4)[3],
        )


def test_cpcv_accepts_utc_epoch_milliseconds_and_keeps_same_day_rows_together():
    timestamps = [
        int(datetime(2026, 1, 1 + day, hour, tzinfo=UTC).timestamp() * 1000)
        for day in range(6)
        for hour in (0, 12)
    ]
    folds = calendar_cpcv_splits(
        timestamps,
        n_splits=2,
        purge_bars=1,
        embargo_bars=1,
        holdout_start=timestamps[10],
    )
    for fold in folds[:-1]:
        for day in {timestamps[i] // 86_400_000 for i in fold.test_indices}:
            assert all(
                i in fold.test_indices
                for i, timestamp in enumerate(timestamps)
                if timestamp // 86_400_000 == day and timestamp < timestamps[10]
            )
    assert folds[-1].test_indices == (10, 11)


def test_trade_metrics_are_net_cost_aware_and_expose_required_risk_fields():
    trades = [
        {"gross_pnl": 120, "cost": 20, "risk": 100, "turnover": 2},
        {"gross_pnl": -60, "cost": 10, "risk": 100, "turnover": 1},
        {"gross_pnl": 90, "cost": 10, "risk": 100, "turnover": 1},
    ]
    result = evaluate_trades(trades, [1000, 1100, 1030, 1110], 252, trials=3)
    assert result["sample_count"] == 3
    assert result["net_expectancy_per_trade"] == pytest.approx(110 / 3)
    assert result["expectancy_r"] == pytest.approx(11 / 30)
    assert result["win_rate"] == pytest.approx(2 / 3)
    assert result["avg_win"] == pytest.approx(90)
    assert result["avg_loss"] == pytest.approx(70)
    assert result["risk_reward"] == pytest.approx(90 / 70)
    assert result["breakeven_win_rate"] == pytest.approx(70 / 160)
    assert result["profit_factor"] == pytest.approx(180 / 70)
    assert result["max_drawdown"] == pytest.approx(70 / 1100)
    assert result["turnover"] == 4
    assert result["trial_count"] == 3
    assert result["sharpe"] is None  # trades are not equally spaced periodic returns
    assert result["psr"] is None
    assert result["dsr"] is None and result["certified"] is False
    assert "reference" in result["certification_blocker"].lower()
    for key in ("time_underwater", "recovery", "cvar_es", "effective_trial_count"):
        assert key in result


def test_metric_degenerate_inputs_fail_closed():
    result = evaluate_trades([], [100, 100], 252, trials=0)
    assert result["sample_count"] == 0
    assert result["certified"] is False
    with pytest.raises(ValueError):
        evaluate_trades([{"gross_pnl": 1, "cost": -1}], [100], 252, trials=1)


def test_psr_uses_unannualized_periodic_sharpe_and_es_is_positive_loss():
    periodic = [0.01, -0.02, 0.03, 0.00, 0.02]
    result = evaluate_trades(
        [{"gross_pnl": 5}, {"gross_pnl": -2}],
        [100, 103],
        252,
        trials=2,
        periodic_returns=periodic,
    )
    assert result["observation_semantics"] == "equally_spaced_periodic_net_returns"
    assert result["periodic_sample_count"] == len(periodic)
    assert result["sharpe"] == pytest.approx(
        (sum(periodic) / len(periodic))
        / math.sqrt(
            sum((x - sum(periodic) / len(periodic)) ** 2 for x in periodic)
            / (len(periodic) - 1)
        )
        * math.sqrt(252)
    )
    assert 0 <= result["psr"] <= 1
    assert result["cvar_es"] == 2
    assert result["gap_coverage"] is None


def test_metrics_fold_regime_and_gap_summaries_need_explicit_metadata():
    result = evaluate_trades(
        [],
        [100],
        252,
        trials=0,
        fold_returns=[[0.01, -0.01], [0.02, 0.01]],
        regime_returns={"calm": [0.01, 0.02]},
        gap_metadata={"verified": True, "expected": 3, "covered": 2},
    )
    assert len(result["fold_metrics"]) == 2
    assert result["regime_metrics"]["calm"]["sample_count"] == 2
    assert result["gap_coverage"] == pytest.approx(2 / 3)


def _verified_dataset():
    return {
        "rows": [{"timestamp": i, "label": 1} for i in range(6)],
        "timestamps": _days(date(2026, 1, 1), 6),
        "dataset_hash": "dataset-sha256",
        "feature_hash": "feature-sha256",
        "provenance_verified": True,
        "gaps_verified": True,
        "open_gaps": [],
        "label_horizon_bars": 1,
    }


def test_search_preregisters_caps_trials_and_records_invalid_attempts_append_only(
    tmp_path,
):
    ledger = tmp_path / "trials.jsonl"
    dataset = _verified_dataset()
    result = run_search(
        dataset,
        _Features(),
        style="momentum",
        candidates=[{"id": "a"}, {"id": "bad", "invalid": True}],
        budget=1,
        ledger_path=ledger,
        holdout_start=_days(date(2026, 1, 1), 6)[5],
    )
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["candidate_id"] == "a"
    assert rows[0]["dataset_hash"] == "dataset-sha256"
    assert rows[0]["feature_hash"] == "feature-sha256"
    assert rows[0]["preregistered"] is True
    assert rows[0]["config"] == {"id": "a"}
    manifests = list(tmp_path.glob("*.manifest.jsonl"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text().splitlines()[0])[
        "candidate_configs"
    ] == [{"id": "a"}, {"id": "bad", "invalid": True}]
    assert result.trial_count == 1


def test_search_logs_invalid_and_retrain_trials_and_blocks_unknown_gaps(tmp_path):
    ledger = tmp_path / "trials.jsonl"
    dataset = _verified_dataset()
    dataset["open_gaps"] = ["2026-01-02"]
    with pytest.raises(ValueError, match="gap|provenance"):
        run_search(
            dataset,
            _Features(),
            style="breakout",
            candidates=[{"id": "a"}],
            ledger_path=ledger,
            holdout_start=_days(date(2026, 1, 1), 6)[5],
        )
    assert not ledger.exists()  # fail before labels/candidate evaluation

    dataset = _verified_dataset()
    result = run_search(
        dataset,
        _Features(),
        style="breakout",
        candidates=[
            {"id": "bad", "invalid": True},
            {"id": "retrain", "retrain": True},
            {"id": "regate", "regate": True},
        ],
        ledger_path=ledger,
        holdout_start=_days(date(2026, 1, 1), 6)[5],
    )
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [r["status"] for r in rows] == ["invalid", "retrain", "regate"]
    assert result.trial_count == 3


def test_search_refuses_unverified_provenance_before_label_access(tmp_path):
    class NoLabelRead(dict):
        def __getitem__(self, key):
            if key == "rows":
                raise AssertionError("labels read before provenance verification")
            return super().__getitem__(key)

    dataset = NoLabelRead(_verified_dataset())
    dataset["provenance_verified"] = False
    with pytest.raises(ValueError, match="provenance"):
        run_search(
            dataset,
            _Features(),
            style="trend",
            candidates=[],
            ledger_path=tmp_path / "trials.jsonl",
            holdout_start=_days(date(2026, 1, 1), 6)[5],
        )


def test_search_rejects_mismatched_feature_hash_and_accumulates_style_budget(tmp_path):
    dataset = _verified_dataset()
    with pytest.raises(ValueError, match="feature provenance hash"):
        run_search(
            dataset,
            object(),
            style="momentum",
            candidates=[{"id": "x"}],
            ledger_path=tmp_path / "mismatch.jsonl",
            holdout_start=_days(date(2026, 1, 1), 6)[5],
        )

    ledger = tmp_path / "budget.jsonl"
    run_search(
        dataset,
        _Features(),
        style="trend",
        candidates=[{"id": "one"}],
        budget=1,
        ledger_path=ledger,
        holdout_start=_days(date(2026, 1, 1), 6)[5],
    )
    second = run_search(
        dataset,
        _Features(),
        style="trend",
        candidates=[{"id": "two"}],
        budget=1,
        ledger_path=ledger,
        holdout_start=_days(date(2026, 1, 1), 6)[5],
    )
    assert second.trial_count == 0
    assert len(ledger.read_text().splitlines()) == 1


def test_search_never_exposes_holdout_rows_to_candidate_selection(tmp_path):
    dataset = _verified_dataset()
    result = run_search(
        dataset,
        _Features(),
        style="trend",
        candidates=[{"id": "candidate"}],
        ledger_path=tmp_path / "trials.jsonl",
        holdout_start=_days(date(2026, 1, 1), 6)[5],
    )
    assert result.holdout_indices == (5,)
    holdout = set(result.holdout_indices)
    selection = {
        i
        for fold in result.folds
        if not fold.is_holdout
        for i in fold.train_indices + fold.test_indices
    }
    assert selection and not selection & holdout
    assert all("selection_indices" not in trial for trial in result.trials)
    assert all(trial["holdout_excluded"] is True for trial in result.trials)


def test_search_candidate_sequence_is_monotonic_across_calls(tmp_path):
    ledger = tmp_path / "sequence.jsonl"
    dataset = _verified_dataset()
    for candidate_id in ("one", "two"):
        run_search(
            dataset,
            _Features(),
            style="trend",
            candidates=[{"id": candidate_id}],
            ledger_path=ledger,
            holdout_start=_days(date(2026, 1, 1), 6)[5],
        )
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [row["candidate_number"] for row in rows] == [1, 2]


def test_concurrent_search_reservations_cannot_exceed_style_budget(tmp_path):
    ledger = tmp_path / "concurrent.jsonl"
    dataset = _verified_dataset()

    def invoke(i):
        return run_search(
            dataset,
            _Features(),
            style="momentum",
            candidates=[{"id": str(i)}],
            budget=3,
            ledger_path=ledger,
            holdout_start=_days(date(2026, 1, 1), 6)[5],
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(invoke, range(12)))
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert len(rows) == 3
    assert sorted(row["candidate_number"] for row in rows) == [1, 2, 3]


# --- daily-lane guard + intraday ordering (discovered on real 1m data) ------

def test_daily_lane_accepts_only_day_granularities():
    from .walk_forward import require_daily_lane

    for ok in ("1d", "1day", "day", "D"):
        require_daily_lane(ok)


def test_daily_lane_refuses_intraday_instead_of_emitting_numbers():
    from .walk_forward import require_daily_lane

    for bad in ("1m", "5m", "15m", "1h"):
        with pytest.raises(SystemExit) as exc:
            require_daily_lane(bad)
        assert "not implemented" in str(exc.value)


def test_load_close_orders_intraday_rows_by_open_ms_not_by_date(tmp_path):
    """All 1m bars of a session share one ts_day; ordering by the date alone
    silently scrambles the series (measured on hyperliquid/BTC-USDC/1m)."""
    import duckdb

    from .walk_forward import load_close

    path = tmp_path / "intraday.parquet"
    duckdb.sql(f"""
        COPY (
            SELECT * FROM (VALUES
                ('2026-11-03', 1762_000_060_000::BIGINT, 101.0),
                ('2026-11-03', 1762_000_000_000::BIGINT, 100.0),
                ('2026-11-03', 1762_000_120_000::BIGINT, 102.0)
            ) AS t(ts_day, open_ms, close)
        ) TO '{path}' (FORMAT PARQUET)
    """)
    assert load_close(path) == [100.0, 101.0, 102.0]


def test_walk_forward_produces_no_folds_on_a_tiny_series():
    """A derived daily projection starts life with 2-3 bars; the lane must
    detect 'no fold possible' so main() can refuse instead of crashing on an
    empty fold list."""
    from .walk_forward import walk_forward

    closes = [100.0, 101.0, 102.0]
    with pytest.raises(SystemExit) as exc:
        walk_forward(closes, 6)
    assert "too short for even one fold" in str(exc.value)
