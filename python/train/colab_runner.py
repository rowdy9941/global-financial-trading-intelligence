"""Optional Colab execution lane for the non-LLM research pipeline.

The runner is deliberately thin: it reuses the local feature, split and metric
implementations, executes a preregistered candidate grid on the detected
device, and writes an *unpromoted* candidate manifest. DSR certification stays
blocked here exactly as it is locally; no artifact from this lane may be
promoted without E4 and the owner-controlled gates.

Configuration is environment-only because ``colab exec -f`` passes a source
file, not trailing script arguments::

    GFTI_DATA_PATH        D0 parquet with ts_ms/open/high/low/close[/volume]
    GFTI_DATASET_KEY      D3 dataset key, e.g. hyperliquid/BTC-USD/1m
    GFTI_SOURCE_SHA256    expected sha256 of GFTI_DATA_PATH
    GFTI_OUTPUT_DIR       directory for manifest.json + model.json
    GFTI_STYLE            trend | momentum | mean_reversion | breakout
    GFTI_SOURCE_INTERVAL  source bar interval, e.g. 1m (required)
    GFTI_VOLUME_AVAILABLE true|false, taken from D3 metadata (required)
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

try:  # package import when tested locally
    from .alpha_search import _ALLOWED_STYLES, _MAX_BUDGET
    from .features import build_features
    from .metrics import DSR_CERTIFICATION_BLOCKER, evaluate_trades
    from .validation import calendar_cpcv_splits
except ImportError:  # pragma: no cover - `colab exec -f` runs this as __main__
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from alpha_search import _ALLOWED_STYLES, _MAX_BUDGET  # type: ignore
    from features import build_features  # type: ignore
    from metrics import DSR_CERTIFICATION_BLOCKER, evaluate_trades  # type: ignore
    from validation import calendar_cpcv_splits  # type: ignore

REQUIRED_ENV = (
    "GFTI_DATA_PATH",
    "GFTI_DATASET_KEY",
    "GFTI_SOURCE_SHA256",
    "GFTI_OUTPUT_DIR",
    "GFTI_STYLE",
    "GFTI_SOURCE_INTERVAL",
    "GFTI_VOLUME_AVAILABLE",
)

# Preregistered candidate grid: ridge strength x label horizon in bars.
RIDGE_ALPHAS = (1.0, 10.0)
LABEL_HORIZONS = (1, 3, 6)
COST_BPS = 8.0
# Scale-free feature suffixes; every frame prefix is stripped before matching.
FEATURE_SUFFIXES = (
    "return_1",
    "momentum_5",
    "rsi_14",
    "macd",
    "realized_vol_20",
    "range",
    "body",
)
HOLDOUT_FRACTION = 0.2


@dataclass(frozen=True)
class RunnerConfig:
    data_path: Path
    dataset_key: str
    source_sha256: str
    output_dir: Path
    style: str
    source_interval: str
    volume_available: bool


def load_config(env: dict[str, str] | None = None) -> RunnerConfig:
    """Validate the environment contract, failing on every missing key."""
    env = os.environ if env is None else env
    missing = [key for key in REQUIRED_ENV if not env.get(key)]
    if missing:
        raise ValueError(
            "missing required environment variables: " + ", ".join(missing)
        )
    style = env["GFTI_STYLE"]
    if style not in _ALLOWED_STYLES:
        raise ValueError(f"GFTI_STYLE must be one of {sorted(_ALLOWED_STYLES)}")
    sha = env["GFTI_SOURCE_SHA256"].lower()
    if len(sha) != 64 or any(char not in "0123456789abcdef" for char in sha):
        raise ValueError("GFTI_SOURCE_SHA256 must be 64 lowercase hex characters")
    volume = env["GFTI_VOLUME_AVAILABLE"].strip().lower()
    if volume not in {"true", "false"}:
        raise ValueError("GFTI_VOLUME_AVAILABLE must be 'true' or 'false'")
    data_path = Path(env["GFTI_DATA_PATH"]).expanduser()
    if not data_path.is_file():
        raise ValueError(f"GFTI_DATA_PATH does not exist: {data_path}")
    return RunnerConfig(
        data_path=data_path,
        dataset_key=env["GFTI_DATASET_KEY"],
        source_sha256=sha,
        output_dir=Path(env["GFTI_OUTPUT_DIR"]).expanduser(),
        style=style,
        source_interval=env["GFTI_SOURCE_INTERVAL"],
        volume_available=volume == "true",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_source(config: RunnerConfig) -> str:
    """Refuse to run on a file whose bytes differ from the D3 lineage hash."""
    actual = file_sha256(config.data_path)
    if actual != config.source_sha256:
        raise ValueError(
            f"source sha256 mismatch: expected {config.source_sha256}, got {actual}"
        )
    return actual


def detect_device() -> str:
    """Report the real execution device; never claim a GPU that is absent."""
    try:
        import torch
    except ImportError:
        return "cpu-numpy"
    try:
        if torch.cuda.is_available():
            return "cuda"
    except (RuntimeError, OSError):  # broken or missing driver
        return "cpu"
    return "cpu"


def _read_rows(path: Path) -> list[dict]:
    import pyarrow.parquet as pq

    table = pq.read_table(str(path))
    required = {"ts_ms", "open", "high", "low", "close"}
    missing = required - set(table.schema.names)
    if missing:
        raise ValueError(f"source parquet missing columns: {sorted(missing)}")
    rows = table.to_pylist()
    rows.sort(key=lambda row: int(row["ts_ms"]))
    return rows


def _feature_columns(feature_set) -> list[int]:
    chosen = []
    for suffix in FEATURE_SUFFIXES:
        matches = [
            index
            for index, name in enumerate(feature_set.feature_names)
            if name == suffix or name.endswith("_" + suffix)
        ]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one feature ending in {suffix!r}")
        chosen.append(matches[0])
    return chosen


def _standardize(
    matrix: list[list[float]],
) -> tuple[list[list[float]], list[float], list[float]]:
    width = len(matrix[0])
    count = len(matrix)
    means = [sum(row[j] for row in matrix) / count for j in range(width)]
    stds = []
    for j in range(width):
        variance = sum((row[j] - means[j]) ** 2 for row in matrix) / count
        stds.append(math.sqrt(variance) or 1.0)
    scaled = [
        [max(-5.0, min(5.0, (row[j] - means[j]) / stds[j])) for j in range(width)]
        for row in matrix
    ]
    return scaled, means, stds


def _ridge(matrix: list[list[float]], target: list[float], alpha: float) -> list[float]:
    """Solve (XᵀX + αI)β = Xᵀy with a small Gaussian elimination.

    Pure Python keeps the lane dependency-free on CPU and deterministic across
    devices; the matrix is at most 8x8 here.
    """
    width = len(matrix[0])
    xtx = [[0.0] * width for _ in range(width)]
    xty = [0.0] * width
    for row, y in zip(matrix, target):
        for i in range(width):
            xty[i] += row[i] * y
            for j in range(width):
                xtx[i][j] += row[i] * row[j]
    for i in range(width):
        xtx[i][i] += alpha
    augmented = [xtx[i] + [xty[i]] for i in range(width)]
    for column in range(width):
        pivot = max(range(column, width), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("singular design matrix")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(width):
            if row != column and augmented[row][column]:
                factor = augmented[row][column]
                augmented[row] = [
                    value - factor * base
                    for value, base in zip(augmented[row], augmented[column])
                ]
    return [augmented[i][width] for i in range(width)]


def _dot(weights: list[float], row: list[float]) -> float:
    return sum(weight * value for weight, value in zip(weights, row))


def _horizon_returns(
    closes: list[float], interval_ms: int, timestamps: list[int], horizon: int
) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    for i in range(len(closes) - horizon):
        if timestamps[i + horizon] - timestamps[i] != horizon * interval_ms:
            continue  # gaps are never bridged into a synthetic label
        out[i] = closes[i + horizon] / closes[i] - 1.0
    return out


def _evaluate_fold(
    observations: list[dict],
    *,
    train_indices: tuple[int, ...],
    test_indices: tuple[int, ...],
    alpha: float,
    horizon: int,
    interval_ms: int,
    bars_per_year: float,
    style: str,
) -> tuple[dict, dict, int]:
    train_rows = [observations[i] for i in train_indices]
    train_rows = [
        row for row in train_rows if row["label"] is not None and row["x"] is not None
    ]
    if len(train_rows) < 10:
        raise ValueError("not enough finite training labels in fold")
    matrix = [row["x"] for row in train_rows]
    scaled, means, stds = _standardize(matrix)
    weights = _ridge(scaled, [row["label"] for row in train_rows], alpha)

    test_rows = [observations[i] for i in test_indices]
    trades: list[dict] = []
    periodic: list[float] = []
    equity = [1.0]
    previous_position = 0.0
    previous_ts: int | None = None
    for row in test_rows:
        if row["label_next"] is None or row["x"] is None:
            continue
        contiguous = (
            previous_ts is not None and row["ts_ms"] - previous_ts == interval_ms
        )
        standardized = [
            max(-5.0, min(5.0, (value - mean) / std))
            for value, mean, std in zip(row["x"], means, stds)
        ]
        prediction = _dot(weights, standardized)
        position = (
            0.0
            if prediction == 0 or not math.isfinite(prediction)
            else (1.0 if prediction > 0 else -1.0)
        )
        turnover = abs(position - (previous_position if contiguous else 0.0))
        cost = turnover * COST_BPS / 10_000.0
        gross = position * row["label_next"]
        net = gross - cost
        periodic.append(net)
        equity.append(equity[-1] * (1.0 + net))
        trades.append(
            {
                "gross_pnl": gross,
                "cost": cost,
                "risk": abs(position),
                "turnover": turnover,
                "regime": "unclassified",
            }
        )
        previous_position = position
        previous_ts = row["ts_ms"]
    if not periodic:
        raise ValueError("fold produced no evaluable trades")
    metrics = evaluate_trades(
        trades,
        equity,
        bars_per_year,
        trials=1,
        periodic_returns=periodic,
        fold_returns=[periodic],
        regime_returns={"unclassified": periodic},
    )
    fold = {
        "style": style,
        "alpha": alpha,
        "horizon_bars": horizon,
        "train_rows": len(train_rows),
        "test_rows": len(periodic),
        "weights": weights,
        "feature_means": means,
        "feature_stds": stds,
    }
    return fold, metrics, len(periodic)


def run(config: RunnerConfig) -> dict:
    """Execute the preregistered grid and write an unpromoted manifest."""
    source_sha = verify_source(config)
    rows = _read_rows(config.data_path)
    if not rows:
        raise ValueError("source parquet has no rows")
    feature_set = build_features(
        rows,
        source_interval=config.source_interval,
        volume_available=config.volume_available,
        frames=(),
    )
    timestamps = list(feature_set.timestamps)
    closes = [float(row["close"]) for row in rows]
    interval_ms = timestamps[1] - timestamps[0] if len(timestamps) > 1 else 0
    if interval_ms <= 0:
        raise ValueError("source needs at least two contiguous bars")
    columns = _feature_columns(feature_set)
    days = [datetime.fromtimestamp(ts / 1000, UTC).date() for ts in timestamps]
    distinct_days = sorted(set(days))
    if len(distinct_days) < 5:
        raise ValueError("need at least five calendar days for purged validation")
    holdout_day = distinct_days[
        max(1, int(len(distinct_days) * (1 - HOLDOUT_FRACTION)))
    ]
    folds = calendar_cpcv_splits(
        timestamps,
        n_splits=min(3, len(distinct_days) - 2),
        purge_bars=0,
        embargo_bars=3,
        holdout_start=holdout_day,
        max_label_horizon_bars=max(LABEL_HORIZONS),
    )
    search_folds = [fold for fold in folds if not fold.is_holdout]
    if not search_folds:
        raise ValueError("no search folds after purging")

    grid_size = len(RIDGE_ALPHAS) * len(LABEL_HORIZONS)
    if grid_size * len(search_folds) > _MAX_BUDGET:
        raise ValueError(
            "preregistered grid across folds exceeds the per-style trial cap"
        )
    bars_per_year = 365 * 24 * 3600 * 1000 / interval_ms

    observations: list[dict] = []
    labels: dict[int, list[float | None]] = {
        horizon: _horizon_returns(closes, interval_ms, timestamps, horizon)
        for horizon in LABEL_HORIZONS
    }
    for index in range(len(timestamps)):
        masks = feature_set.availability[index]
        x = [feature_set.values[index][column] for column in columns]
        observations.append(
            {
                "ts_ms": timestamps[index],
                "x": x if all(masks[column] for column in columns) else None,
                "label_next": closes[index + 1] / closes[index] - 1.0
                if index + 1 < len(closes)
                else None,
                "labels": {h: labels[h][index] for h in LABEL_HORIZONS},
            }
        )

    results: list[dict] = []
    folds_out: list[dict] = []
    for fold_index, fold in enumerate(search_folds):
        for alpha in RIDGE_ALPHAS:
            for horizon in LABEL_HORIZONS:
                for observation in observations:
                    observation["label"] = observation["labels"][horizon]
                fold_metrics, metrics, sample_count = _evaluate_fold(
                    observations,
                    train_indices=fold.train_indices,
                    test_indices=fold.test_indices,
                    alpha=alpha,
                    horizon=horizon,
                    interval_ms=interval_ms,
                    bars_per_year=bars_per_year,
                    style=config.style,
                )
                folds_out.append(fold_metrics)
                results.append(
                    {
                        "fold": fold_index,
                        "alpha": alpha,
                        "horizon_bars": horizon,
                        "metrics": metrics,
                        "sample_count": sample_count,
                        "selection_index_hash": hashlib.sha256(
                            json.dumps(
                                sorted(fold.train_indices + fold.test_indices)
                            ).encode()
                        ).hexdigest(),
                    }
                )

    manifest = {
        "manifest_version": 1,
        "dataset_key": config.dataset_key,
        "style": config.style,
        "source_interval": config.source_interval,
        "volume_available": config.volume_available,
        "source_sha256": source_sha,
        "feature_schema_hash": feature_set.feature_schema_hash,
        "source_hash": feature_set.source_hash,
        "holdout_start": holdout_day.isoformat(),
        "holdout_indices": list(next(f for f in folds if f.is_holdout).test_indices),
        "fold_count": len(search_folds),
        "trial_count": len(results),
        "candidate_grid": {
            "ridge_alpha": list(RIDGE_ALPHAS),
            "label_horizon_bars": list(LABEL_HORIZONS),
        },
        "device": detect_device(),
        "backend": "python-ridge",
        "cost_bps_per_turnover": COST_BPS,
        "dsr": None,
        "certified": False,
        "certification_blocker": DSR_CERTIFICATION_BLOCKER,
        "promotion_allowed": False,
        "dependency_versions": {
            "python": platform.python_version(),
            "numpy": _version("numpy"),
            "torch": _version("torch"),
            "pyarrow": _version("pyarrow"),
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "results": results,
        "folds": folds_out,
    }
    validate_manifest(manifest)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    (config.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (config.output_dir / "model.json").write_text(
        json.dumps({"folds": folds_out}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _version(name: str) -> str | None:
    try:
        module = __import__(name)
    except ImportError:
        return None
    return getattr(module, "__version__", "unknown")


def validate_manifest(manifest: dict) -> None:
    """Reject a manifest that claims certification it cannot support."""
    required = (
        "dataset_key",
        "style",
        "source_sha256",
        "feature_schema_hash",
        "fold_count",
        "trial_count",
        "device",
        "dsr",
        "certified",
        "promotion_allowed",
        "results",
    )
    missing = [key for key in required if key not in manifest]
    if missing:
        raise ValueError(f"manifest missing keys: {', '.join(missing)}")
    if (
        manifest["dsr"] is not None
        or manifest["certified"]
        or manifest["promotion_allowed"]
    ):
        raise ValueError(
            "candidate manifest must not claim DSR certification or promotion"
        )
    if not 1 <= int(manifest["trial_count"]) <= _MAX_BUDGET:
        raise ValueError("manifest trial_count is outside the preregistered cap")
    if len(manifest["results"]) != int(manifest["trial_count"]):
        raise ValueError("manifest results do not match trial_count")
    if int(manifest["fold_count"]) < 1:
        raise ValueError("manifest must report at least one searched fold")
    if manifest["device"] not in {"cuda", "cpu", "cpu-numpy"}:
        raise ValueError("manifest device must be a detected device label")


def main() -> int:  # pragma: no cover - exercised only on the VM
    config = load_config()
    manifest = run(config)
    print(
        json.dumps(
            {
                k: manifest[k]
                for k in ("device", "trial_count", "fold_count", "certified")
            }
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
