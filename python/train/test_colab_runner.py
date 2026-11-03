"""Local tests for the Colab runner contract; no GPU or Colab credentials."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from .colab_runner import (
    REQUIRED_ENV,
    detect_device,
    file_sha256,
    load_config,
    run,
    validate_manifest,
    verify_source,
)

MINUTE = 60_000


def _rows(days: int = 6, per_day: int = 60) -> list[dict]:
    rows = []
    day_ms = 24 * 60 * MINUTE
    for day in range(days):
        start = day * day_ms
        for minute in range(per_day):
            ts = start + minute * MINUTE
            close = 100.0 + day + math.sin(minute / 7.0)
            rows.append(
                {
                    "ts_ms": ts,
                    "open": close - 0.05,
                    "high": close + 0.1,
                    "low": close - 0.1,
                    "close": close,
                    "volume": 0.0,
                }
            )
    return rows


def _write(tmp_path, rows=None) -> Path:
    path = tmp_path / "bars.parquet"
    pq.write_table(pa.Table.from_pylist(rows or _rows()), str(path))
    return path


def _env(tmp_path, path, **overrides):
    env = {
        "GFTI_DATA_PATH": str(path),
        "GFTI_DATASET_KEY": "test/BARS/1m",
        "GFTI_SOURCE_SHA256": file_sha256(path),
        "GFTI_OUTPUT_DIR": str(tmp_path / "out"),
        "GFTI_STYLE": "momentum",
        "GFTI_SOURCE_INTERVAL": "1m",
        "GFTI_VOLUME_AVAILABLE": "false",
    }
    env.update(overrides)
    return env


def test_missing_environment_lists_every_required_key():
    with pytest.raises(ValueError, match="GFTI_DATA_PATH"):
        load_config({})
    message = str(pytest.raises(ValueError, load_config, {}).value)
    assert all(key in message for key in REQUIRED_ENV)


def test_config_rejects_bad_style_hash_and_volume_flag(tmp_path):
    path = _write(tmp_path)
    with pytest.raises(ValueError, match="GFTI_STYLE"):
        load_config(_env(tmp_path, path, GFTI_STYLE="not-a-style"))
    with pytest.raises(ValueError, match="GFTI_SOURCE_SHA256"):
        load_config(_env(tmp_path, path, GFTI_SOURCE_SHA256="deadbeef"))
    with pytest.raises(ValueError, match="GFTI_VOLUME_AVAILABLE"):
        load_config(_env(tmp_path, path, GFTI_VOLUME_AVAILABLE="maybe"))
    with pytest.raises(ValueError, match="does not exist"):
        load_config(_env(tmp_path, path, GFTI_DATA_PATH=str(tmp_path / "nope.parquet")))


def test_verify_source_refuses_mismatched_bytes(tmp_path):
    path = _write(tmp_path)
    config = load_config(_env(tmp_path, path))
    assert verify_source(config) == config.source_sha256
    tampered = load_config(_env(tmp_path, path, GFTI_SOURCE_SHA256="0" * 64))
    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_source(tampered)


def test_detect_device_only_reports_real_devices():
    assert detect_device() in {"cuda", "cpu", "cpu-numpy"}


def test_run_writes_unpromoted_manifest_with_hashes(tmp_path):
    path = _write(tmp_path)
    config = load_config(_env(tmp_path, path))

    manifest = run(config)

    on_disk = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert on_disk == manifest
    assert manifest["source_sha256"] == config.source_sha256
    assert manifest["dataset_key"] == "test/BARS/1m"
    assert manifest["style"] == "momentum"
    assert manifest["trial_count"] == manifest["fold_count"] * 6
    assert manifest["fold_count"] >= 2
    assert manifest["dsr"] is None
    assert manifest["certified"] is False
    assert manifest["promotion_allowed"] is False
    assert manifest["holdout_indices"]
    assert all(result["sample_count"] > 0 for result in manifest["results"])
    assert len(manifest["results"]) == manifest["trial_count"]
    assert (tmp_path / "out" / "model.json").is_file()
    validate_manifest(manifest)


def test_manifest_rejects_certification_or_promotion_claims():
    manifest = {
        "dataset_key": "test/BARS/1m",
        "style": "momentum",
        "source_sha256": "a" * 64,
        "feature_schema_hash": "b" * 64,
        "fold_count": 2,
        "trial_count": 1,
        "device": "cpu-numpy",
        "dsr": None,
        "certified": False,
        "promotion_allowed": False,
        "results": [{"fold": 0}],
    }
    validate_manifest(manifest)
    with pytest.raises(ValueError, match="must not claim"):
        validate_manifest({**manifest, "certified": True})
    with pytest.raises(ValueError, match="must not claim"):
        validate_manifest({**manifest, "dsr": 0.99})
    missing = {key: value for key, value in manifest.items() if key != "results"}
    with pytest.raises(ValueError, match="missing keys"):
        validate_manifest(missing)
    with pytest.raises(ValueError, match="device"):
        validate_manifest({**manifest, "device": "tpu"})
    with pytest.raises(ValueError, match="trial_count"):
        validate_manifest({**manifest, "trial_count": 0})


def test_run_refuses_a_source_whose_hash_does_not_match(tmp_path):
    path = _write(tmp_path)
    config = load_config(
        _env(tmp_path, path, GFTI_SOURCE_SHA256=hashlib.sha256(b"x").hexdigest())
    )
    with pytest.raises(ValueError, match="sha256 mismatch"):
        run(config)
    assert not (tmp_path / "out" / "manifest.json").exists()
