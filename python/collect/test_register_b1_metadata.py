"""Registration metadata tests for source-specific volume semantics."""

import pytest
from register_b1 import metadata_update_clause, validate_metadata


def test_trade_volume_metadata_is_accepted() -> None:
    metadata = validate_metadata(
        interval="1m",
        volume_available=True,
        volume_kind="base_asset",
        volume_unit="BTC",
        schema_version="2",
    )

    assert metadata == {
        "volume_available": True,
        "volume_kind": "base_asset",
        "volume_unit": "BTC",
        "schema_version": "2",
    }


def test_histdata_unavailable_volume_metadata_is_accepted() -> None:
    metadata = validate_metadata(
        interval="1m",
        volume_available=False,
        volume_kind="unavailable",
        volume_unit="unavailable",
        schema_version="2",
    )

    assert metadata["volume_available"] is False
    assert metadata["volume_kind"] == "unavailable"
    assert metadata["volume_unit"] == "unavailable"


@pytest.mark.parametrize(
    ("volume_kind", "volume_unit"),
    [(None, "BTC"), ("base_asset", None), ("", "BTC"), ("base_asset", "")],
)
def test_available_volume_requires_kind_and_unit(
    volume_kind: str | None, volume_unit: str | None
) -> None:
    with pytest.raises(SystemExit, match="volume_available=true"):
        validate_metadata(
            interval="1m",
            volume_available=True,
            volume_kind=volume_kind,
            volume_unit=volume_unit,
            schema_version="2",
        )


def test_intraday_registration_requires_explicit_volume_metadata() -> None:
    with pytest.raises(SystemExit, match="explicit volume metadata"):
        validate_metadata(
            interval="1m",
            volume_available=None,
            volume_kind=None,
            volume_unit=None,
            schema_version="1",
        )


@pytest.mark.parametrize(("volume_kind", "volume_unit"), [("", ""), ("", "ticks")])
def test_intraday_unavailable_volume_requires_nonempty_semantics(
    volume_kind: str, volume_unit: str
) -> None:
    with pytest.raises(SystemExit, match="explicit volume metadata"):
        validate_metadata(
            interval="1m",
            volume_available=False,
            volume_kind=volume_kind,
            volume_unit=volume_unit,
            schema_version="1",
        )


def test_upsert_updates_only_explicit_metadata_fields() -> None:
    assert (
        metadata_update_clause(
            volume_available_provided=False,
            volume_kind_provided=False,
            volume_unit_provided=False,
            schema_version_provided=False,
        )
        == ""
    )
    assert (
        metadata_update_clause(
            volume_available_provided=False,
            volume_kind_provided=False,
            volume_unit_provided=False,
            schema_version_provided=True,
        )
        == ", schema_version = EXCLUDED.schema_version"
    )


def test_daily_registration_keeps_legacy_metadata_defaults() -> None:
    metadata = validate_metadata(
        interval="1d",
        volume_available=None,
        volume_kind=None,
        volume_unit=None,
        schema_version="1",
    )

    assert metadata == {
        "volume_available": False,
        "volume_kind": None,
        "volume_unit": None,
        "schema_version": "1",
    }
