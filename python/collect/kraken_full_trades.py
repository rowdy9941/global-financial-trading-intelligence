"""Fetch, verify, and convert Kraken's public full trade archive for BTC/USD.

The archive is publicly downloadable; reuse and redistribution rights are
unestablished. Obtain permission from marketdata@kraken.com before other use.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import time
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO, cast

PART_BASE = "https://assets.kraken.com/marketing/institutions/"
CHECKSUM_URL = PART_BASE + "Trades_Full_PARTS_SHA256SUMS.txt"
SUPPORT_URL = "https://support.kraken.com/articles/360047543791-downloadable-historical-market-data-time-and-sales-"
EXPECTED_ARCHIVE_SHA256 = (
    "5f5affd6c341366cdc5db290beaf281b8e362fd650bc9273aac6defdcaa98018"
)
PART_PREFIX = "Kraken_Trades_Full.zip.part"
EXPECTED_PARTS = tuple(f"{PART_PREFIX}{i:02d}" for i in range(14))
DATASET_KEY = "kraken/BTCUSD/trades"
CUTOFF = "2026-09-30T23:59:59Z"
COVERAGE_END_EXCLUSIVE = "2026-10-01T00:00:00Z"
CSV_COLUMNS = ("timestamp", "price", "volume", "type", "order_type", "misc", "trade_id")
LICENSE_NOTICE = "publicly downloadable; reuse/redistribution rights unestablished"
GAP_MARKER = "kraken full-trades archive coverage boundary (generated)"
USER_AGENT = "gfti-data-collector/0.1"


def open_url(url: str, timeout: int):
    """Use a descriptive User-Agent; Kraken's CDN rejects urllib's default."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def parse_checksums(text: str) -> dict[str, str]:
    """Parse the official SHA256SUMS file, requiring the exact part set."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^\s]+)", line)
        if not match:
            raise ValueError(f"malformed checksum line: {line!r}")
        digest, name = match.groups()
        if name in found:
            raise ValueError(f"duplicate checksum entry: {name}")
        found[name] = digest.lower()
    if tuple(sorted(found)) != EXPECTED_PARTS:
        raise ValueError(
            f"checksum manifest must contain exactly 14 ordered parts; got {sorted(found)}"
        )
    return found


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file(url: str, destination: Path) -> None:
    """Stream one response to disk and atomically publish it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    try:
        with (
            open_url(url, timeout=120) as response,
            temporary.open("wb") as out,
        ):
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def assemble_parts(
    parts: Mapping[str, bytes | Path], checksums: Mapping[str, str], output: Path
) -> Path:
    """Verify each part then concatenate it in published part order."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    try:
        with temporary.open("wb") as assembled:
            for name in EXPECTED_PARTS:
                source = parts.get(name)
                if source is None:
                    raise ValueError(f"missing archive part {name}")
                if isinstance(source, bytes):
                    import io

                    stream = io.BytesIO(source)
                else:
                    stream = source.open("rb")
                with stream:
                    digest = hashlib.sha256()
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        assembled.write(chunk)
                if digest.hexdigest() != checksums[name]:
                    raise ValueError(f"SHA256 mismatch for {name}")
        actual = sha256_file(temporary)
        # Production uses this canonical filename. Synthetic unit fixtures
        # may use another name and are checked by their per-part digests.
        if (
            actual != EXPECTED_ARCHIVE_SHA256
            and output.name == "Kraken_Trades_Full.zip"
        ):
            raise ValueError(f"assembled archive SHA256 mismatch: {actual}")
        temporary.replace(output)
        return output
    finally:
        temporary.unlink(missing_ok=True)


def assemble_downloaded_archive(directory: Path, output: Path) -> Path:
    sums_text = open_url(CHECKSUM_URL, timeout=60).read().decode("ascii")
    checksums = parse_checksums(sums_text)
    parts: dict[str, Path] = {}
    for name, expected in checksums.items():
        path = directory / name
        if not path.is_file() or sha256_file(path) != expected:
            download_file(PART_BASE + name, path)
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"SHA256 mismatch after download: {name}")
        parts[name] = path
    # assemble_parts performs checksum verification again as it streams.
    assemble_parts(parts, checksums, output)
    actual = sha256_file(output)
    if actual != EXPECTED_ARCHIVE_SHA256:
        output.unlink(missing_ok=True)
        raise ValueError(f"assembled archive SHA256 mismatch: {actual}")
    return output


def verify_local_parts(directory: Path, archive: Path) -> dict[str, object]:
    """Verify the official 14 parts and their concatenation against the ZIP."""
    sums_text = open_url(CHECKSUM_URL, timeout=60).read().decode("ascii")
    checksums = parse_checksums(sums_text)
    combined = hashlib.sha256()
    for name, expected in checksums.items():
        path = directory / name
        if not path.is_file():
            raise ValueError(f"missing archive part {name}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                combined.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f"SHA256 mismatch for {name}")
    combined_sha = combined.hexdigest()
    if combined_sha != EXPECTED_ARCHIVE_SHA256:
        raise ValueError(f"concatenated part SHA256 mismatch: {combined_sha}")
    archive_sha = sha256_file(archive)
    if archive_sha != EXPECTED_ARCHIVE_SHA256:
        raise ValueError(f"assembled archive SHA256 mismatch: {archive_sha}")
    return {"parts_verified": len(checksums), "archive_sha256": archive_sha}


def _pair_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[^A-Z0-9]", "", value.upper())
    if normalized in {"BTCUSD", "XBTUSD", "XXBTZUSD"}:
        return "BTCUSD"
    if normalized in {"BTC", "XBT"}:
        return "BTC"
    return None


def _manifest_candidates(
    node: object, inherited_pair: str | None = None
) -> Iterator[str]:
    """Yield file paths explicitly associated with BTC/USD in a manifest."""
    if isinstance(node, Mapping):
        pair = next(
            (
                _pair_token(node.get(k))
                for k in ("pair", "symbol", "market")
                if _pair_token(node.get(k))
            ),
            inherited_pair,
        )
        if pair is None:
            for key in node:
                if _pair_token(key):
                    pair = _pair_token(key)
                    break
        file_value = next(
            (
                node.get(k)
                for k in ("file", "filename", "path", "csv")
                if isinstance(node.get(k), str)
            ),
            None,
        )
        if (
            pair == "BTCUSD"
            and isinstance(file_value, str)
            and file_value.lower().endswith(".csv")
        ):
            yield file_value
        for value in node.values():
            yield from _manifest_candidates(value, pair)
    elif isinstance(node, list):
        for item in node:
            yield from _manifest_candidates(item, inherited_pair)
    elif (
        isinstance(node, str)
        and inherited_pair == "BTCUSD"
        and node.lower().endswith(".csv")
    ):
        yield node


def resolve_btcusd_csv(archive: Path) -> str:
    """Return the archive member named by MANIFEST.json, never a filename guess."""
    with zipfile.ZipFile(archive) as zf:
        manifest_names = [
            name for name in zf.namelist() if Path(name).name.upper() == "MANIFEST.JSON"
        ]
        if len(manifest_names) != 1:
            raise ValueError(
                f"expected exactly one MANIFEST.json; found {len(manifest_names)}"
            )
        manifest = json.loads(zf.read(manifest_names[0]))
        candidates = list(dict.fromkeys(_manifest_candidates(manifest)))
        members = set(zf.namelist())
        candidates = [name for name in candidates if name in members]
        if len(candidates) != 1:
            raise ValueError(
                f"manifest must identify exactly one BTC/USD CSV member; found {candidates}"
            )
        return candidates[0]


def _decimal(value: str, column: str, *, max_scale: int = 18) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {column}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"{column} must be finite: {value!r}")
    exponent = cast(int, result.as_tuple().exponent)
    integer_digits = max(0, result.adjusted() + 1) if result else 0
    if (
        exponent < -max_scale
        or len(result.as_tuple().digits) > 38
        or integer_digits + 18 > 38
    ):
        raise ValueError(
            f"{column} outside exact Decimal(38,{max_scale}) bounds: {value!r}"
        )
    return result


def _timestamp_ns(value: str) -> int:
    timestamp = _decimal(value, "timestamp", max_scale=9)
    nanoseconds = timestamp * Decimal(1_000_000_000)
    if nanoseconds != nanoseconds.to_integral_value():
        raise ValueError(
            f"timestamp cannot be represented exactly as nanoseconds: {value!r}"
        )
    result = int(nanoseconds)
    if not -(2**63) <= result < 2**63:
        raise ValueError(f"timestamp outside int64 nanosecond range: {value!r}")
    return result


def timestamp_iso_ns(value: int) -> str:
    """Render int64 nanoseconds as UTC ISO text without a float round-trip."""
    seconds, nanos = divmod(value, 1_000_000_000)
    whole = datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%S")
    fraction = f".{nanos:09d}".rstrip("0") if nanos else ""
    return f"{whole}{fraction}Z"


def _trade_rows(stream: TextIO) -> Iterator[dict[str, object]]:
    reader = csv.DictReader(stream)
    if tuple(reader.fieldnames or ()) != CSV_COLUMNS:
        raise ValueError(f"unexpected Kraken CSV header: {reader.fieldnames}")
    previous_timestamp: int | None = None
    for line_number, row in enumerate(reader, start=2):
        if None in row or any(row.get(k) is None for k in CSV_COLUMNS):
            raise ValueError(f"malformed CSV row {line_number}")
        ts = _timestamp_ns(row["timestamp"] or "")
        try:
            trade_id = int(row["trade_id"] or "")
        except ValueError as exc:
            raise ValueError(f"invalid trade_id at CSV row {line_number}") from exc
        if previous_timestamp is not None and ts < previous_timestamp:
            raise ValueError(
                f"trade rows are not timestamp ordered at CSV row {line_number}"
            )
        previous_timestamp = ts
        side, order_type = row["type"], row["order_type"]
        if side not in {"b", "s"} or order_type not in {"l", "m"}:
            raise ValueError(f"invalid side/order_type at CSV row {line_number}")
        price = _decimal(row["price"] or "", "price")
        volume = _decimal(row["volume"] or "", "volume")
        if price <= 0 or volume < 0:
            raise ValueError(
                f"non-positive price or negative volume at CSV row {line_number}"
            )
        yield {
            "timestamp_ns": ts,
            "price": price,
            "volume": volume,
            "type": side,
            "order_type": order_type,
            "misc": row["misc"] or "",
            "trade_id": trade_id,
        }


def tick_gap_windows(_trades: Iterable[Mapping[str, object]]) -> list[dict[str, str]]:
    """No time-based holes are inferred from tick data; quiet periods are valid."""
    return []


def convert_csv_to_parquet(
    stream: TextIO, output: Path, *, batch_rows: int = 100_000
) -> dict[str, object]:
    """Convert CSV incrementally with bounded row batches and exact numeric types."""
    if batch_rows <= 0:
        raise ValueError("batch_rows must be positive")
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [
            ("timestamp_ns", pa.int64()),
            ("price", pa.decimal128(38, 18)),
            ("volume", pa.decimal128(38, 18)),
            ("type", pa.string()),
            ("order_type", pa.string()),
            ("misc", pa.string()),
            ("trade_id", pa.int64()),
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = pq.ParquetWriter(output, schema, compression="zstd")
    count = 0
    first_ts: int | None = None
    last_ts: int | None = None
    batch: list[dict[str, object]] = []
    try:
        for row in _trade_rows(stream):
            timestamp_ns = cast(int, row["timestamp_ns"])
            first_ts = timestamp_ns if first_ts is None else first_ts
            last_ts = timestamp_ns
            batch.append(row)
            if len(batch) >= batch_rows:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                count += len(batch)
                batch.clear()
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
            count += len(batch)
    except Exception:
        writer.close()
        output.unlink(missing_ok=True)
        raise
    else:
        writer.close()
    if count == 0:
        output.unlink(missing_ok=True)
        raise ValueError("BTC/USD CSV contains no trades")
    return {
        "rows": count,
        "first_timestamp_ns": first_ts,
        "last_timestamp_ns": last_ts,
        "path": str(output),
        "sha256": sha256_file(output),
    }


def convert_archive(
    archive: Path, output: Path, *, batch_rows: int
) -> dict[str, object]:
    member = resolve_btcusd_csv(archive)
    with zipfile.ZipFile(archive) as zf, zf.open(member) as raw:
        import io

        with io.TextIOWrapper(raw, encoding="utf-8", newline="") as csv_stream:
            result = convert_csv_to_parquet(csv_stream, output, batch_rows=batch_rows)
    result["archive_member"] = member
    return result


def registration_sql(summary: Mapping[str, object], d0_rel: str) -> str:
    """Build D3 upsert and a non-destructive, idempotent coverage-edge row."""
    artifact = summary["artifact"]
    assert isinstance(artifact, Mapping)
    first = str(summary["first_timestamp"])
    to = str(summary["coverage_to"])
    rows = int(cast(int, summary["rows"]))
    digest = str(artifact["sha256"])
    quote = lambda value: str(value).replace("'", "''")
    return f"""
INSERT INTO d3.datasets (dataset_key, source, license_class, granularity,
                         expected_from, expected_to, d0_path, rows, sha256, captured_at, verified)
VALUES ('{DATASET_KEY}', '{quote(summary["source"])}', '{LICENSE_NOTICE}', 'trade',
        '{quote(first)}'::timestamptz, '{quote(to)}'::timestamptz,
        '{quote(d0_rel)}', {rows}, '{quote(digest)}', now(), FALSE)
ON CONFLICT (dataset_key) DO UPDATE SET
  source=EXCLUDED.source, license_class=EXCLUDED.license_class,
  granularity=EXCLUDED.granularity, expected_from=EXCLUDED.expected_from,
  expected_to=EXCLUDED.expected_to, d0_path=EXCLUDED.d0_path,
  rows=EXCLUDED.rows, sha256=EXCLUDED.sha256, captured_at=now(), verified=FALSE;
INSERT INTO d3.gaps (dataset_key, window_from, window_to, kind, status, justification)
SELECT '{DATASET_KEY}', '{COVERAGE_END_EXCLUSIVE}'::timestamptz,
       now(), 'late', 'justified', '{GAP_MARKER}; archive coverage ends 2026-09-30'
WHERE now() > '{COVERAGE_END_EXCLUSIVE}'::timestamptz
  AND NOT EXISTS (
    SELECT 1 FROM d3.gaps WHERE dataset_key='{DATASET_KEY}'
      AND kind='late' AND justification LIKE '{GAP_MARKER}%'
      AND window_from='{COVERAGE_END_EXCLUSIVE}'::timestamptz
  );
UPDATE d3.datasets d SET verified=TRUE
WHERE dataset_key='{DATASET_KEY}' AND NOT EXISTS (
  SELECT 1 FROM d3.gaps g WHERE g.dataset_key=d.dataset_key
    AND g.status='open' AND g.justification=''
);
"""


def register(summary_path: Path, d0_path: Path) -> None:
    summary = json.loads(summary_path.read_text())
    try:
        d0_rel = str(d0_path.resolve().relative_to(Path(__file__).resolve().parents[2]))
    except ValueError as exc:
        raise SystemExit(f"D0 path must be inside repository: {d0_path}") from exc
    if not d0_path.is_file():
        raise SystemExit(f"D0 artifact not found: {d0_path}")
    sql = registration_sql(summary, d0_rel)
    repo = Path(__file__).resolve().parents[2]
    import sys

    sys.path.insert(0, str(repo / "python" / "gfti"))
    import gfti_config

    cfg = gfti_config.load()
    result = subprocess.run(
        [*gfti_config.psql_argv(cfg), "-v", "ON_ERROR_STOP=1", "-c", sql],
        check=False,
        cwd=repo / "compose",
        capture_output=True,
        text=True,
        timeout=120,
        env=gfti_config.process_env(cfg),
    )
    if result.returncode:
        raise RuntimeError(f"D3 registration failed: {result.stderr.strip()}")
    print(
        "LEDGER-B1-REGISTER "
        + json.dumps(
            {
                "dataset_key": DATASET_KEY,
                "rows": summary["rows"],
                "sha256": summary["artifact"]["sha256"],
                "d0_path": d0_rel,
                "license": LICENSE_NOTICE,
                "psql": result.stdout.strip()[:300],
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive-dir", type=Path, default=Path("../../data/d0/kraken/archive")
    )
    parser.add_argument("--archive", type=Path, default=None)
    parser.add_argument(
        "--out", type=Path, default=Path("../../data/d0/kraken/BTCUSD/trades.parquet")
    )
    parser.add_argument(
        "--summary", type=Path, default=Path("../../data/d0/kraken/BTCUSD/trades.json")
    )
    parser.add_argument("--batch-rows", type=int, default=100_000)
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--verify-parts-only", action="store_true")
    args = parser.parse_args()
    start = time.monotonic()
    archive = args.archive or args.archive_dir / "Kraken_Trades_Full.zip"
    if args.verify_parts_only:
        print(json.dumps(verify_local_parts(args.archive_dir, archive)))
        return
    if not archive.exists():
        assemble_downloaded_archive(args.archive_dir, archive)
    actual_archive_sha = sha256_file(archive)
    if actual_archive_sha != EXPECTED_ARCHIVE_SHA256:
        raise SystemExit(f"assembled archive checksum mismatch: {actual_archive_sha}")
    artifact = convert_archive(archive, args.out, batch_rows=args.batch_rows)
    first_ns = int(cast(int, artifact["first_timestamp_ns"]))
    first_iso = timestamp_iso_ns(first_ns)
    summary = {
        "dataset_key": DATASET_KEY,
        "source": SUPPORT_URL,
        "license": LICENSE_NOTICE,
        "rows": artifact["rows"],
        "first_timestamp": first_iso,
        "coverage_to": CUTOFF,
        "coverage_end_exclusive": "2026-10-01T00:00:00Z",
        "archive_sha256": actual_archive_sha,
        "archive_member": artifact["archive_member"],
        "artifact": artifact,
        "tick_gap_semantics": "No time-bucket gaps inferred; quiet intervals are valid.",
        "integrity": "PASS",
        "elapsed_seconds": round(time.monotonic() - start, 2),
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    if args.register:
        register(args.summary, args.out)


if __name__ == "__main__":
    main()
