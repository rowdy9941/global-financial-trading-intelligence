"""Preregistered, append-only accounting for research candidate attempts.

This module deliberately does not fit models or inspect holdout values. It
validates the data gate, freezes a candidate manifest, and records each attempt.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .validation import _calendar_day, calendar_cpcv_splits

_ALLOWED_STYLES = {"trend", "momentum", "mean_reversion", "breakout"}
_MAX_BUDGET = 1000


@dataclass(frozen=True)
class ResearchResult:
    trial_count: int
    trials: tuple[dict, ...]
    holdout_indices: tuple[int, ...]
    preregistration_hash: str
    folds: tuple[object, ...]


def run_search(
    dataset: dict,
    features: object,
    *,
    style: str,
    candidates: Iterable[dict],
    budget: int = 1000,
    ledger_path: str | Path,
    holdout_start: datetime | date,
) -> ResearchResult:
    """Validate gates, preregister candidates, and append every allowed attempt.

    Search candidates contain configurations only. Holdout timestamps are used
    to identify the sealed index range; no holdout rows/labels are loaded or
    included in the selection indices. An evaluator/trainer must consume only
    train indices from CPCV and must separately pass the final promotion gate.
    """
    # Check the trust boundary before looking up rows, labels, or feature values.
    if dataset.get("provenance_verified") is not True:
        raise ValueError("dataset provenance is unverified")
    if dataset.get("gaps_verified") is not True or dataset.get("open_gaps") != []:
        raise ValueError("dataset gap state is unknown or contains open gaps")
    dataset_hash = dataset.get("dataset_hash")
    feature_hash = dataset.get("feature_hash")
    if not isinstance(dataset_hash, str) or not dataset_hash:
        raise ValueError("verified dataset hash is required")
    if not isinstance(feature_hash, str) or not feature_hash:
        raise ValueError("verified feature hash is required")
    actual_feature_hash = getattr(features, "feature_schema_hash", None)
    if actual_feature_hash is None:
        actual_feature_hash = getattr(features, "feature_hash", None)
    if actual_feature_hash != feature_hash:
        raise ValueError("feature provenance hash is missing or does not match")
    if style not in _ALLOWED_STYLES:
        raise ValueError(f"style must be preregistered from {_ALLOWED_STYLES}")
    if (
        not isinstance(budget, int)
        or isinstance(budget, bool)
        or not 1 <= budget <= _MAX_BUDGET
    ):
        raise ValueError(f"budget must be between 1 and {_MAX_BUDGET} per style")
    timestamps = dataset.get("timestamps")
    if not isinstance(timestamps, (list, tuple)) or not timestamps:
        raise ValueError("verified timestamps are required")
    horizon = dataset.get("label_horizon_bars")
    if not isinstance(horizon, int) or horizon < 1:
        raise ValueError("positive maximum label_horizon_bars is required")

    # Construct an immutable declaration before consuming the candidates.
    candidate_list = list(candidates)
    path = Path(ledger_path)
    declaration = {
        "style": style,
        "budget": budget,
        "candidate_configs": candidate_list,
        "dataset_hash": dataset_hash,
        "feature_hash": feature_hash,
        "label_horizon_bars": horizon,
    }
    preregistration_hash = hashlib.sha256(
        json.dumps(
            declaration, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()

    folds = calendar_cpcv_splits(
        timestamps,
        n_splits=min(3, len({_calendar_day(ts) for ts in timestamps}) - 1),
        purge_bars=0,
        embargo_bars=int(dataset.get("embargo_bars", horizon)),
        holdout_start=holdout_start,
        max_label_horizon_bars=horizon,
    )
    holdout = next(fold for fold in folds if fold.is_holdout)
    selection_indices = tuple(
        sorted(
            {
                i
                for fold in folds
                if not fold.is_holdout
                for i in fold.train_indices + fold.test_indices
            }
        )
    )
    if set(selection_indices) & set(holdout.test_indices):
        raise AssertionError("holdout leaked into candidate selection")
    # Do not duplicate the full index list into every ledger row: at M1 scale
    # that would be gigabytes. Persist a content hash plus the reproducible
    # fold specification instead; the folds themselves are returned.
    selection_hash = hashlib.sha256(
        json.dumps(selection_indices, separators=(",", ":")).encode()
    ).hexdigest()
    fold_spec = {
        "n_splits": min(3, len({_calendar_day(ts) for ts in timestamps}) - 1),
        "purge_bars": 0,
        "embargo_bars": int(dataset.get("embargo_bars", horizon)),
        "holdout_start": _calendar_day(holdout_start).isoformat(),
        "max_label_horizon_bars": horizon,
    }

    records: list[dict] = []
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    manifest_path = path.with_suffix(path.suffix + ".manifest.jsonl")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        prior_rows = []
        if path.exists():
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                try:
                    prior_rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"trial ledger is corrupt at line {line_number}"
                    ) from exc
        prior_count = sum(row.get("style") == style for row in prior_rows)
        remaining = max(0, budget - prior_count)
        accepted = candidate_list[:remaining]
        manifest = {**declaration, "reserved_candidate_configs": accepted}
        manifest_json = json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), default=str
        )
        manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()
        with manifest_path.open("a", encoding="utf-8") as manifest_ledger:
            manifest_ledger.write(
                json.dumps(
                    {"manifest_hash": manifest_hash, **manifest},
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                + "\n"
            )
            manifest_ledger.flush()
        with path.open("a", encoding="utf-8") as ledger:
            for offset, config in enumerate(accepted, start=1):
                number = prior_count + offset
                if not isinstance(config, dict) or not config.get("id"):
                    candidate_id = f"invalid-{number}"
                    status = "invalid"
                else:
                    candidate_id = str(config["id"])
                    status = (
                        "invalid"
                        if config.get("invalid")
                        else "retrain"
                        if config.get("retrain")
                        else "regate"
                        if config.get("regate")
                        else "attempted"
                    )
                row = {
                    "style": style,
                    "candidate_id": candidate_id,
                    "candidate_number": number,
                    "config": config,
                    "status": status,
                    "preregistered": True,
                    "preregistration_hash": preregistration_hash,
                    "manifest_hash": manifest_hash,
                    "dataset_hash": dataset_hash,
                    "feature_hash": feature_hash,
                    "label_horizon_bars": horizon,
                    "fold_spec": fold_spec,
                    "selection_index_hash": selection_hash,
                    "selection_count": len(selection_indices),
                    "holdout_excluded": True,
                }
                ledger.write(
                    json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                )
                ledger.flush()
                records.append(row)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return ResearchResult(
        len(records),
        tuple(records),
        holdout.test_indices,
        preregistration_hash,
        tuple(folds),
    )
