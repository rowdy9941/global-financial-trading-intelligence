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


def test_default_pre_inception_note_satisfies_its_own_sql_guard() -> None:
    """Regression: the default note once contained ';' and was rejected by the
    guard, breaking every plain (non-intraday) registration."""
    from register_b1 import pre_inception_note

    note = pre_inception_note(None, "2017-08-17")
    assert "2017-08-17" in note
    assert ";" not in note and "'" not in note


def test_explicit_pre_inception_note_is_used_verbatim() -> None:
    from register_b1 import pre_inception_note

    note = "public API retains only ~3 days of 1m, so nothing earlier exists"
    assert pre_inception_note(note, "2026-10-31") == note


def test_pre_inception_note_rejects_sql_relevant_characters() -> None:
    from register_b1 import pre_inception_note

    for bad in ("oops; DROP TABLE d3.gaps", "it's wrong"):
        with pytest.raises(SystemExit):
            pre_inception_note(bad, "2026-11-01")


# --- A-7: the rail receipt is the only acceptable provenance -------------------

def _rail_summary(tmp_path, **over):
    import hashlib
    import json

    receipt = {"accepted": 10, "rejected": 0, "by_symbol": {"BTC_USDC": 10}}
    doc = {"receipt": receipt, "market_prefix": "market.ingest.BTC_USDC"}
    rp = tmp_path / "x.rail.json"
    rp.write_text(json.dumps(doc))
    rail = {
        "accepted": 10,
        "rejected": 0,
        "market_prefix": "market.ingest.BTC_USDC",
        "receipt_path": str(rp),
        "receipt_sha256": hashlib.sha256(
            json.dumps(receipt, sort_keys=True).encode()
        ).hexdigest(),
    }
    rail.update(over)
    return {"rows": 10, "writer": "rail", "rail": rail}


def test_rail_receipt_is_accepted_when_it_self_verifies(tmp_path) -> None:
    from register_b1 import rail_evidence

    ev = rail_evidence(_rail_summary(tmp_path))
    assert ev is not None and ev["accepted"] == 10 and ev["rejected"] == 0


def test_absent_rail_block_is_none_so_the_caller_must_refuse() -> None:
    """None is the signal to demand a declared reason — never a silent pass."""
    from register_b1 import rail_evidence

    assert rail_evidence({"rows": 10}) is None


def test_rail_receipt_with_a_reject_is_refused(tmp_path) -> None:
    """A partial batch must not register as a dataset: the missing rows would be
    invisible in every later training read."""
    from register_b1 import rail_evidence

    with pytest.raises(SystemExit):
        rail_evidence(_rail_summary(tmp_path, rejected=3))


def test_rail_receipt_digest_must_match_the_file(tmp_path) -> None:
    from register_b1 import rail_evidence

    with pytest.raises(SystemExit):
        rail_evidence(_rail_summary(tmp_path, receipt_sha256="0" * 64))


def test_rail_receipt_accepted_must_equal_summary_rows(tmp_path) -> None:
    from register_b1 import rail_evidence

    s = _rail_summary(tmp_path)
    s["rows"] = 11
    with pytest.raises(SystemExit):
        rail_evidence(s)


def test_writer_must_be_rail_when_a_receipt_is_present(tmp_path) -> None:
    from register_b1 import rail_evidence

    s = _rail_summary(tmp_path)
    s["writer"] = "legacy-collector"
    with pytest.raises(SystemExit):
        rail_evidence(s)
