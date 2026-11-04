#!/usr/bin/env python3
"""Recover trade segments truncated by an ungraceful kill.

Why this exists: tick capture is unrecoverable (no historical feed), so a
damaged segment must be salvaged, never discarded. The recorder writes one
complete gzip member per flush batch, which makes damage bounded — a `kill -9`
(or a `timeout` that does not let the process unwind) can only truncate the
member in flight. Every earlier member is still a valid gzip stream.

This tool decodes member by member, keeps what decompresses, drops the
truncated tail, de-duplicates by `tid` (a reconnect or two overlapping writers
can repeat prints), sorts by `ts_ms`, and reports exactly what was recovered
and what was lost.

Usage:
  uv run recover_trades_segments.py --dry-run        # report only
  uv run recover_trades_segments.py                  # rewrite in place
  uv run recover_trades_segments.py path/to/seg.jsonl.gz [...]
"""

from __future__ import annotations

import argparse
import gzip
import json
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO / "d0" / "hyperliquid" / "BTC-USDC" / "trades"


FEED = 64 * 1024  # feed zlib in chunks: output already produced is kept


def decode_members(data: bytes) -> tuple[bytes, int, int]:
    """Decode concatenated gzip members; return (payload, members, dropped_tail).

    Two salvage levels, because both damage shapes occur in practice:
      * a truncated FINAL member (the recorder's normal failure) — every earlier
        member is a complete stream and is decoded whole;
      * a single truncated member (a long-lived handle killed mid-stream) — its
        deflate prefix still expands to valid lines up to the cut, so output is
        accumulated in small feeds and whatever decompressed before the error is
        retained. Only the cut itself is lost.
    """
    rest = data
    chunks: list[bytes] = []
    members = 0
    while rest:
        dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
        produced: list[bytes] = []
        failed = False
        pos = 0
        while pos < len(rest):
            block = rest[pos : pos + FEED]
            pos += len(block)
            try:
                produced.append(dec.decompress(block))
            except zlib.error:
                failed = True
                break
            if dec.eof:
                break
        chunks.append(b"".join(produced))
        if failed or not dec.eof:
            return b"".join(chunks), members, 1
        members += 1
        # `unused_data` is only the tail of the LAST block fed; the blocks never
        # fed (because eof arrived early) must survive too, or every member after
        # the first 64 KiB of the file is silently discarded — a healthy segment
        # would then be "repaired" down to a prefix. Bug found 2026-11-04 by
        # comparing this function against `gzip -dc` on a 71 KB segment.
        rest = dec.unused_data + rest[pos:]
    return b"".join(chunks), members, 0


def recover_bytes(data: bytes) -> dict:
    """Recover rows from raw segment bytes; pure, so tests need no filesystem."""
    payload, members, dropped = decode_members(data)
    rows: list[dict] = []
    bad_lines = 0
    for line in payload.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            rows.append(
                {
                    "ts_ms": int(row["ts_ms"]),
                    "px": str(row["px"]),
                    "sz": str(row["sz"]),
                    "side": str(row["side"]),
                    "tid": int(row["tid"]),
                    "hash": str(row.get("hash") or ""),
                }
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            bad_lines += 1
    unique = {row["tid"]: row for row in rows}
    ordered = sorted(unique.values(), key=lambda r: (r["ts_ms"], r["tid"]))
    return {
        "members_decoded": members,
        "truncated_tail_dropped": dropped,
        "rows_read": len(rows),
        "duplicate_rows_dropped": len(rows) - len(ordered),
        "bad_lines_dropped": bad_lines,
        "rows_recovered": len(ordered),
        "rows": ordered,
    }


def backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".corrupt")


def write_segment(rows: list[dict], path: Path) -> int:
    """Rewrite the segment as ONE complete member (idempotent, deterministic)."""
    lines = [json.dumps(row, sort_keys=True) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(filename=str(path), mode="wb", mtime=0) as fh:
        fh.write(("\n".join(lines) + ("\n" if lines else "")).encode())
    return path.stat().st_size


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("paths", nargs="*", help="segments (default: every segment)")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument(
        "--no-backup",
        action="store_true",
        help="skip the .corrupt copy (default keeps it: destroying the only "
        "copy of damaged evidence is worse than a few wasted KB)",
    )
    args = a.parse_args()

    paths = [Path(p) for p in args.paths] or sorted(DEFAULT_DIR.glob("*.jsonl.gz"))
    if not paths:
        raise SystemExit(f"no segments found under {DEFAULT_DIR}")
    for path in paths:
        data = path.read_bytes()  # read ONCE: the rewrite replaces these bytes
        result = recover_bytes(data)
        rows = result.pop("rows")
        intact = (
            result["truncated_tail_dropped"] == 0 and result["bad_lines_dropped"] == 0
        )
        result.update({"path": str(path), "intact": intact})
        if not args.dry_run and not intact:
            if not args.no_backup:
                backup_path(path).write_bytes(data)
                result["backup"] = str(backup_path(path))
            result["rewritten_bytes"] = write_segment(rows, path)
        print("HL-TRADES-RECOVER " + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
