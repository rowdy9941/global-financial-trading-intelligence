#!/usr/bin/env python3
"""Ingest a capture through the rail — the sanctioned way into the lake (A-7).

Invariant R1 says D0 has exactly one writer. Until A-7 that was a comment: every
collector called `pq.write_table` itself and then registered the file in
`d3.datasets`, so the registry's provenance was the writer's own claim. This
script removes that path. It does not write Parquet and it does not touch D3:

    rows ──► rail --ingest-file ──► (validate, stamp, publish, receipt)
                              └──► d0_dump --stdin  ──► D0 Parquet
                                                   └──► summary for register_b1

The two things the rail can prove and a collector cannot are the counts: how
many rows it *accepted* and how many it *rejected* at its own boundary. Those go
into the receipt, the receipt's digest goes into the D3 row, and
`register_b1.py` refuses a registration that cannot name them.

Sources (v1): `hl-trades` (recorded Hyperliquid trade segments) and `rows`
(an already-formatted `symbol ts_nanos price rights` file). Adding a venue means
adding a reader here, in one place, not another writer in a collector.

Usage:
  uv run rail_ingest.py --source hl-trades \
      --dir ../../d0/hyperliquid/BTC-USDC/trades \
      --symbol BTC-USDC --out ../../d0/hyperliquid/BTC-USDC/tick.parquet \
      [--summary out.json] [--rights public] [--market-prefix market.ingest.BTC_USDC]

Exit codes: 0 ok · 1 failed · 2 rail/ingest precondition (bus unreachable, binary
missing) · 3 source unreadable/empty.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUST_BIN = REPO / "rust" / "target" / "release"
TRADE_SEGMENT = "*.jsonl.gz"


def die(code: int, msg: str) -> None:
    print(f"RAIL-INGEST-FAIL {msg}", file=sys.stderr)
    raise SystemExit(code)


def rail_symbol(symbol: str) -> str:
    """D3 symbols carry `-`; the rail's identity regex does not. One mapping."""
    out = symbol.replace("-", "_")
    if not out or not all(c.isalnum() or c == "_" for c in out):
        die(3, f"symbol {symbol!r} cannot be mapped to a rail identity")
    return out


def rows_from_hl_trades(directory: Path, symbol: str, rights: str) -> tuple[list[str], dict]:
    """Recorded Hyperliquid trade segments → rail rows, de-duplicated by `tid`.

    The recorder is append-only and may re-emit a trade after a reconnect, so
    de-duplication happens here on the trade id rather than being assumed of the
    source. Out-of-order arrivals are sorted by exchange time, which is what the
    rail's deterministic D0 ordering already requires downstream.
    """
    files = sorted(directory.glob(TRADE_SEGMENT))
    if not files:
        die(3, f"no {TRADE_SEGMENT} segments under {directory}")
    sym = rail_symbol(symbol)
    seen: set[str] = set()
    trades: list[tuple[int, float]] = []
    skipped = 0
    for f in files:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    px = float(rec["px"])
                    # `ts_ms` is the recorder's field; `time` is the raw ws field.
                    # The rail stamps nanoseconds.
                    ts = int(rec.get("ts_ms", rec.get("time"))) * 1_000_000
                except (ValueError, KeyError, TypeError):
                    skipped += 1
                    continue
                tid = rec.get("tid")
                if tid is not None:
                    key = str(tid)
                    if key in seen:
                        skipped += 1
                        continue
                    seen.add(key)
                trades.append((ts, px))
    if not trades:
        die(3, f"no trades parsed from {len(files)} segment(s) under {directory}")
    trades.sort()
    rows = [f"{sym} {ts} {px!r} {rights}" for ts, px in trades]
    return rows, {
        "segments": [str(f.relative_to(REPO)) for f in files],
        "duplicates_or_bad_lines": skipped,
        "trades": len(trades),
        "first_ts_nanos": trades[0][0],
        "last_ts_nanos": trades[-1][0],
    }


def rows_from_file(path: Path) -> tuple[list[str], dict]:
    if not path.is_file():
        die(3, f"rows file not found: {path}")
    rows = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not rows:
        die(3, f"rows file is empty: {path}")
    return rows, {"segments": [str(path)], "rows_in_file": len(rows)}


def day_holes(first_nanos: int, last_nanos: int, days_with_rows: set[str]) -> list[dict]:
    """Interior UTC days with no rows — the B-GATE shape, not a guess.

    A tick capture that skips a whole day is a hole a training run must not
    paper over, so it is reported rather than smoothed away.
    """
    start = datetime.fromtimestamp(first_nanos / 1e9, tz=UTC).date()
    end = datetime.fromtimestamp(last_nanos / 1e9, tz=UTC).date()
    holes: list[dict] = []
    run: list[str] = []
    cur = start
    from datetime import timedelta

    while cur <= end:
        day = cur.isoformat()
        if day in days_with_rows:
            if run:
                holes.append({"window_from": run[0], "window_to": run[-1]})
                run = []
        else:
            run.append(day)
        cur += timedelta(days=1)
    # The trailing day is "still trading", not a hole — register_b1 owns that row.
    if len(run) > 1:
        holes.append({"window_from": run[0], "window_to": run[-2]})
    return holes


def run(argv: list[str], *, stdin_path: Path | None = None, env: dict | None = None) -> str:
    r = subprocess.run(
        argv,
        stdin=stdin_path.open("rb") if stdin_path else None,
        capture_output=True,
        text=True,
        env=env,
        timeout=3600,
        check=False,
    )
    if r.returncode != 0:
        die(2, f"{' '.join(argv[:2])} rc={r.returncode}: {(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--source", required=True, choices=["hl-trades", "rows"])
    a.add_argument("--dir", default=None, help="hl-trades segment directory")
    a.add_argument("--rows", default=None, help="pre-formatted rows file")
    a.add_argument("--symbol", required=True, help="D3 symbol, e.g. BTC-USDC")
    a.add_argument("--out", required=True, help="D0 parquet path (written by d0_dump)")
    a.add_argument("--summary", default=None, help="summary JSON path (default: <out>.summary.json")
    a.add_argument("--rights", default="public", choices=["public", "public_research_only", "walled"])
    a.add_argument("--license", default="free (hyperliquid public ws trades)")
    a.add_argument(
        "--source-name",
        default=None,
        help="registered source string; must satisfy register_b1's identity regex "
        "(ASCII alnum plus . _ / : + -)",
    )
    a.add_argument("--market-prefix", default=None)
    a.add_argument(
        "--skip-publish-ack",
        action="store_true",
        help="accepted for symmetry only — the rail is fail-closed and never skips the ack",
    )
    args = a.parse_args()

    rail_bin, d0_bin = RUST_BIN / "rail", RUST_BIN / "d0_dump"
    for b in (rail_bin, d0_bin):
        if not b.is_file():
            die(2, f"{b} missing — run `gfti install` (cargo build --release)")

    if args.source == "hl-trades":
        if not args.dir:
            die(3, "--source hl-trades needs --dir")
        seg_dir = Path(args.dir)
        if not seg_dir.is_absolute():
            # run-dir-relative first (the documented usage), then repo-relative:
            # one of the two is what the operator meant, and both are resolved
            # rather than guessed from the current directory alone.
            seg_dir = seg_dir if seg_dir.is_dir() else REPO / args.dir
        rows, meta = rows_from_hl_trades(seg_dir.resolve(), args.symbol, args.rights)
    else:
        if not args.rows:
            die(3, "--source rows needs --rows")
        rows, meta = rows_from_file(Path(args.rows))

    out = Path(args.out)
    if not out.is_absolute():
        # same rule as --dir: run-dir first, then repo-relative.
        out = out.resolve() if out.parent.is_dir() else (REPO / args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / (out.name + ".rows.txt")
    work.write_text("\n".join(rows) + "\n", encoding="utf-8")

    prefix = args.market_prefix or f"market.ingest.{rail_symbol(args.symbol)}"
    env = dict(os.environ, MARKET_PREFIX=prefix)
    # The rail writes the stream; its stdout is rows + one receipt line. Capturing
    # it keeps the receipt (which d0 deliberately skips) parseable.
    rail_out = run([str(rail_bin), "--ingest-file", str(work)], env=env)
    staged = out.parent / (out.name + ".rail.stdout.txt")
    staged.write_text(rail_out, encoding="utf-8")

    receipt = None
    for line in rail_out.splitlines():
        if line.startswith("RAIL-INGEST "):
            receipt = json.loads(line[len("RAIL-INGEST ") :])
    if receipt is None:
        die(1, "rail produced no RAIL-INGEST receipt")
    if receipt["accepted"] != len(rows) or receipt["rejected"] != 0:
        die(
            1,
            f"rail accepted {receipt['accepted']}/{len(rows)} rows, rejected {receipt['rejected']}"
            " — a partial batch must not be registered as a dataset",
        )

    d0_out = run([str(d0_bin), "--stdin", str(out)], stdin_path=staged)
    d0_line = next((ln for ln in d0_out.splitlines() if ln.startswith("D0 ")), "")
    try:
        d0 = json.loads(d0_line[len("D0 ") :])
    except ValueError:
        die(1, f"d0_dump produced no D0 line: {d0_out.strip()[:300]}")
    if d0["rows_written"] != receipt["accepted"]:
        die(1, f"d0 wrote {d0['rows_written']} rows but the rail accepted {receipt['accepted']}")

    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    receipt_sha = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
    receipt_path = out.with_suffix(".rail.json")
    receipt_path.write_text(
        json.dumps({"receipt": receipt, "market_prefix": prefix, "sha256": receipt_sha}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    try:
        receipt_rel = str(receipt_path.relative_to(REPO))
    except ValueError:
        receipt_rel = str(receipt_path)

    first_n = receipt_by_symbol_first(receipt, rows)
    days = days_with_rows(out)
    holes = day_holes(first_n, _last_ts(rows), days)
    first_day = datetime.fromtimestamp(first_n / 1e9, tz=UTC).strftime("%Y-%m-%d")
    last_day = datetime.fromtimestamp(_last_ts(rows) / 1e9, tz=UTC).strftime("%Y-%m-%d")

    if args.source == "hl-trades":
        default_source = "hyperliquid-wss-trades-segments"
    else:
        default_source = f"rows-file-{Path(args.rows).name}"

    summary = {
        "symbol": args.symbol,
        "interval": "tick",
        "source": args.source_name or default_source,
        "license": args.license,
        "rows": receipt["accepted"],
        "first_day": first_day,
        "last_day": last_day,
        "holes": holes,
        "artifact": {
            "path": str(out.relative_to(REPO)) if out.is_relative_to(REPO) else str(out),
            "sha256": sha,
            "rows": d0["rows_written"],
        },
        "rail": {
            "accepted": receipt["accepted"],
            "rejected": receipt["rejected"],
            "market_prefix": prefix,
            "receipt_path": receipt_rel,
            "receipt_sha256": receipt_sha,
        },
        "ingest": meta,
        "writer": "rail",
    }
    if args.summary:
        Path(args.summary).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        "RAIL-INGEST-OK "
        + json.dumps(
            {
                "symbol": args.symbol,
                "rows": summary["rows"],
                "d0_path": summary["artifact"]["path"],
                "sha256": sha,
                "receipt_sha256": receipt_sha,
            }
        )
    )


def receipt_by_symbol_first(receipt: dict, rows: list[str]) -> int:
    """First accepted timestamp, from the receipt's own row set (not a re-read)."""
    for line in rows:
        parts = line.split()
        if parts and parts[0] in receipt.get("by_symbol", {}):
            return int(parts[1])
    die(1, "no row of this symbol in the receipt")


def _last_ts(rows: list[str]) -> int:
    return max(int(line.split()[1]) for line in rows if len(line.split()) > 1)


def days_with_rows(parquet: Path) -> set[str]:
    """UTC days that actually landed in D0 — read back, not inferred."""
    sys.path.insert(0, str(REPO / "python" / "collect"))
    try:
        import pyarrow.parquet as pq
    except ImportError:  # pragma: no cover - environment problem, stated loudly
        die(2, "pyarrow missing — run under `uv run` in python/collect")
    t = pq.read_table(parquet, columns=["ts_nanos"])
    return {
        datetime.fromtimestamp(ts / 1e9, tz=UTC).strftime("%Y-%m-%d")
        for ts in t.column("ts_nanos").to_pylist()
    }


if __name__ == "__main__":
    main()
