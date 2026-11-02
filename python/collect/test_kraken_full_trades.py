from __future__ import annotations

import hashlib
import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path

import kraken_full_trades as kraken
import pytest


def test_parse_checksum_manifest_requires_all_ordered_parts() -> None:
    text = "".join(
        f"{'a' * 64}  Kraken_Trades_Full.zip.part{i:02d}\n" for i in range(14)
    )

    checksums = kraken.parse_checksums(text)

    assert len(checksums) == 14
    assert next(iter(checksums)) == "Kraken_Trades_Full.zip.part00"
    assert next(reversed(checksums)) == "Kraken_Trades_Full.zip.part13"


def test_parse_checksum_manifest_rejects_missing_or_malformed_parts() -> None:
    with pytest.raises(ValueError, match="14"):
        kraken.parse_checksums("a" * 64 + "  Kraken_Trades_Full.zip.part00\n")
    with pytest.raises(ValueError, match="checksum"):
        kraken.parse_checksums("z" * 64 + "  Kraken_Trades_Full.zip.part00\n")


def test_part_verification_and_assembly_are_ordered_and_exact(tmp_path: Path) -> None:
    parts = {
        f"Kraken_Trades_Full.zip.part{i:02d}": bytes([i]) * (i + 1) for i in range(14)
    }
    expected = {name: hashlib.sha256(data).hexdigest() for name, data in parts.items()}

    output = kraken.assemble_parts(parts, expected, tmp_path / "full.zip")

    assert output.read_bytes() == b"".join(parts[name] for name in expected)
    assert kraken.sha256_file(output) == hashlib.sha256(output.read_bytes()).hexdigest()
    altered = {**expected, "Kraken_Trades_Full.zip.part00": "0" * 64}
    with pytest.raises(ValueError, match="part00"):
        kraken.assemble_parts(parts, altered, tmp_path / "bad.zip")


def test_member_is_resolved_only_from_manifest(tmp_path: Path) -> None:
    archive = tmp_path / "sample.zip"
    manifest = {"files": [{"pair": "XBT/USD", "file": "obscure-member.csv"}]}
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("MANIFEST.json", json.dumps(manifest))
        zf.writestr(
            "obscure-member.csv",
            "timestamp,price,volume,type,order_type,misc,trade_id\n",
        )
        zf.writestr("BTCUSD.csv", "must not guess this file\n")

    assert kraken.resolve_btcusd_csv(archive) == "obscure-member.csv"


def test_archive_conversion_streams_only_manifest_btc_member(tmp_path: Path) -> None:
    archive = tmp_path / "fixture.zip"
    output = tmp_path / "btc-usd.parquet"
    manifest = {
        "files": [
            {"pair": "XBT/USD", "file": "pairs/obscure-btc-member.csv"},
            {"pair": "ETH/USD", "file": "pairs/eth.csv"},
        ]
    }
    btc_csv = (
        "timestamp,price,volume,type,order_type,misc,trade_id\n"
        "1700000000.123456789,36500.123456789012345678,0.000000000000000001,b,l,,7\n"
    )
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("MANIFEST.json", json.dumps(manifest))
        zf.writestr("pairs/obscure-btc-member.csv", btc_csv)
        zf.writestr(
            "pairs/eth.csv",
            "timestamp,price,volume,type,order_type,misc,trade_id\n"
            "1700000000,1,1,b,l,,8\n",
        )

    result = kraken.convert_archive(archive, output, batch_rows=1)

    import pyarrow.parquet as pq

    rows = pq.read_table(output).to_pylist()
    assert result["archive_member"] == "pairs/obscure-btc-member.csv"
    assert result["rows"] == 1
    assert rows[0]["timestamp_ns"] == 1_700_000_000_123_456_789
    assert rows[0]["price"] == Decimal("36500.123456789012345678")
    assert rows[0]["volume"] == Decimal("0.000000000000000001")


def test_manifest_without_btc_usd_csv_fails_closed(tmp_path: Path) -> None:
    archive = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "MANIFEST.json",
            json.dumps({"files": [{"pair": "ETH/USD", "file": "eth.csv"}]}),
        )
        zf.writestr("eth.csv", "timestamp,price,volume,type,order_type,misc,trade_id\n")

    with pytest.raises(ValueError, match="BTC/USD"):
        kraken.resolve_btcusd_csv(archive)


def test_trade_csv_batches_preserve_decimal_and_timestamp_precision(
    tmp_path: Path,
) -> None:
    data = (
        "timestamp,price,volume,type,order_type,misc,trade_id\n"
        "1700000000.123456,36500.12345678,0.00001234,b,l,,101\n"
        "1700000000.123457,36500.12345679,0.00001235,s,m,,102\n"
        "1700000000.123458,36500.12345680,0.00001236,b,l,,103\n"
    )
    output = tmp_path / "trades.parquet"
    result = kraken.convert_csv_to_parquet(io.StringIO(data), output, batch_rows=2)

    import pyarrow.parquet as pq

    rows = pq.read_table(output).to_pylist()
    assert result["rows"] == 3
    assert rows[0]["timestamp_ns"] == 1_700_000_000_123_456_000
    assert rows[0]["price"] == Decimal("36500.12345678")
    assert rows[0]["volume"] == Decimal("0.00001234")
    assert rows[2]["trade_id"] == 103


def test_tick_gap_semantics_do_not_infer_empty_time_buckets() -> None:
    trades = [
        {"timestamp_ns": 10, "trade_id": 1},
        {"timestamp_ns": 10_000_000_000, "trade_id": 2},
    ]

    assert kraken.tick_gap_windows(trades) == []


def test_iso_timestamp_format_keeps_nanoseconds_exact() -> None:
    assert (
        kraken.timestamp_iso_ns(1_700_000_000_123_456_789)
        == "2023-11-14T22:13:20.123456789Z"
    )


def test_equal_timestamps_follow_archive_order_without_trade_id_assumption() -> None:
    rows = kraken._trade_rows(
        io.StringIO(
            "timestamp,price,volume,type,order_type,misc,trade_id\n"
            "1700000000.1,1,1,b,l,,9\n"
            "1700000000.1,1,1,s,m,,2\n"
        )
    )

    assert [row["trade_id"] for row in rows] == [9, 2]


def test_registration_sql_keeps_operator_gaps_and_is_idempotent() -> None:
    sql = kraken.registration_sql(
        {
            "source": "Kraken full archive",
            "first_timestamp": "2013-10-06T00:00:00Z",
            "coverage_to": "2026-09-30T23:59:59Z",
            "rows": 2,
            "artifact": {"sha256": "a" * 64},
        },
        "data/d0/kraken/BTCUSD/trades.parquet",
    )

    assert "kraken/BTCUSD/trades" in sql
    assert "ON CONFLICT (dataset_key) DO UPDATE" in sql
    assert "verified=FALSE" in sql
    assert "DELETE FROM d3.gaps" not in sql
    assert "NOT EXISTS" in sql
    assert "kind, status, justification" in sql
    assert "'late', 'justified'" in sql
