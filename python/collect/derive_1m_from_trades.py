#!/usr/bin/env python3
"""Derive 1-minute bars from captured trade ticks, then check them against the venue.

Why this exists: ticks are the only series in P1 that cannot be backfilled, and
`docs/gates/P1-DATA.md` refuses to call a self-captured feed `verified` on its own
word. The venue's own 1m candles ARE an independent source (for the ~3 days they
are retained), so this tool turns ticks into bars and compares the result field by
field. Agreement is evidence; disagreement is a coverage bug and is reported, never
smoothed.

Rules (no invention):

  * prints are de-duplicated by `tid` across ALL segments before aggregation — a
    recorder restart can repeat prints, and double-counting is silent corruption of
    volume;
  * a minute is only marked `covered` when a single contiguous capture window
    contains it end to end; a partial minute is emitted with `covered=false` and is
    EXCLUDED from the cross-check, because its close/volume are not the minute's;
  * OHLC comes from the prints in time order (open = first print, close = last);
    volume is the sum of sizes and `n_trades` the print count.

Usage:
  uv run derive_1m_from_trades.py --summary /tmp/ticks_1m.json
  uv run derive_1m_from_trades.py --cross-check --summary /tmp/ticks_1m.json
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

MINUTE_MS = 60_000
REPO = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO / "d0" / "hyperliquid" / "BTC-USDC" / "trades"


def read_prints(paths: list[Path]) -> tuple[list[dict], dict]:
    """Read every segment, de-duplicated by `tid`, ordered by (ts_ms, tid).

    Returns the prints plus a coverage report: the contiguous span of each segment
    and how many duplicate prints were dropped. The spans are what makes
    "is this minute complete?" answerable at all.
    """
    by_tid: dict[int, dict] = {}
    duplicates = 0
    bad_lines = 0
    spans: list[dict] = []
    for path in sorted(paths):
        first = last = None
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    tid = int(row["tid"])
                    ts_ms = int(row["ts_ms"])
                    px = Decimal(str(row["px"]))
                    sz = Decimal(str(row["sz"]))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    bad_lines += 1
                    continue
                first = ts_ms if first is None else min(first, ts_ms)
                last = ts_ms if last is None else max(last, ts_ms)
                if tid in by_tid:
                    duplicates += 1
                    continue
                by_tid[tid] = {"ts_ms": ts_ms, "px": px, "sz": sz, "tid": tid}
        if first is not None:
            spans.append({"path": str(path), "start_ms": first, "end_ms": last})
    prints = sorted(by_tid.values(), key=lambda r: (r["ts_ms"], r["tid"]))
    return prints, {
        "segments": spans,
        "duplicates_dropped": duplicates,
        "bad_lines_dropped": bad_lines,
        "unique_prints": len(prints),
    }


def is_covered(minute_ms: int, spans: list[dict]) -> bool:
    """True when ONE contiguous capture window contains the whole minute."""
    end = minute_ms + MINUTE_MS
    return any(s["start_ms"] <= minute_ms and s["end_ms"] >= end for s in spans)


def bucket_minutes(prints: list[dict], spans: list[dict]) -> list[dict]:
    """Aggregate prints into 1m bars, flagging which minutes are fully covered."""
    buckets: dict[int, list[dict]] = {}
    for p in prints:
        buckets.setdefault(p["ts_ms"] // MINUTE_MS * MINUTE_MS, []).append(p)
    bars = []
    for minute in sorted(buckets):
        rows = buckets[minute]  # already in (ts_ms, tid) order
        bars.append(
            {
                "open_ms": minute,
                "close_ms": minute + MINUTE_MS - 1,
                "open": rows[0]["px"],
                "high": max(r["px"] for r in rows),
                "low": min(r["px"] for r in rows),
                "close": rows[-1]["px"],
                "volume": sum((r["sz"] for r in rows), Decimal(0)),
                "n_trades": len(rows),
                "covered": is_covered(minute, spans),
            }
        )
    return bars


def compare(bars: list[dict], venue_rows: list[dict]) -> dict:
    """Field-by-field comparison of derived bars against venue 1m candles.

    Only minutes the venue actually has AND that we consider fully covered are
    compared. Anything else is counted, not ignored: an un-covered minute we skip
    is reported as such.
    """
    venue = {int(r["t"]): r for r in venue_rows}
    ours_by_minute = {b["open_ms"]: b for b in bars}
    compared = matched = 0
    missing_in_capture = 0
    un_covered = 0
    diffs: list[dict] = []
    for minute, v in sorted(venue.items()):
        ours = ours_by_minute.get(minute)
        if ours is None:
            missing_in_capture += 1
            continue
        if not ours["covered"]:
            un_covered += 1
            continue
        compared += 1
        expected = {
            "open": Decimal(str(v["o"])),
            "high": Decimal(str(v["h"])),
            "low": Decimal(str(v["l"])),
            "close": Decimal(str(v["c"])),
            "volume": Decimal(str(v["v"])),
            "n_trades": int(v["n"]),
        }
        actual = {k: ours[k] for k in expected}
        if expected == actual:
            matched += 1
            continue
        diffs.append(
            {
                "open_ms": minute,
                "fields": {
                    k: {"venue": str(expected[k]), "ticks": str(actual[k])}
                    for k in expected
                    if expected[k] != actual[k]
                },
            }
        )
    return {
        "venue_minutes": len(venue),
        "compared": compared,
        "exact_matches": matched,
        "minutes_not_in_capture": missing_in_capture,
        "minutes_skipped_uncovered": un_covered,
        "mismatches": len(diffs),
        "mismatch_examples": diffs[:10],
        "all_identical": compared > 0 and matched == compared,
    }


def matching_minutes(bars: list[dict], venue_rows: list[dict]) -> dict[int, bool]:
    """Minute -> does it equal the venue candle on every field?

    The venue's own 1m candles are the authority for the ~3 days they exist. A
    minute that disagrees is NOT a rounding artifact: the recorder missed prints
    (reconnect, session boundary, or a repaired segment), so its close and volume
    are not the market's. Such a minute must never be published as a bar.
    """
    venue = {int(r["t"]): r for r in venue_rows}
    ours = {b["open_ms"]: b for b in bars if b["covered"]}
    out: dict[int, bool] = {}
    for minute, v in venue.items():
        bar = ours.get(minute)
        if bar is None:
            continue
        out[minute] = (
            bar["open"] == Decimal(str(v["o"]))
            and bar["high"] == Decimal(str(v["h"]))
            and bar["low"] == Decimal(str(v["l"]))
            and bar["close"] == Decimal(str(v["c"]))
            and bar["volume"] == Decimal(str(v["v"]))
            and bar["n_trades"] == int(v["n"])
        )
    return out


def summarize(bars: list[dict], prints: dict, cross: dict | None = None) -> dict:
    covered = [b for b in bars if b["covered"]]
    return {
        "prints": prints,
        "minutes_total": len(bars),
        "minutes_covered": len(covered),
        "prints_total": prints["unique_prints"],
        "volume_total": str(sum((b["volume"] for b in bars), Decimal(0))),
        "first_minute_ms": bars[0]["open_ms"] if bars else None,
        "last_minute_ms": bars[-1]["open_ms"] if bars else None,
        "cross_check": cross,
    }


def bars_to_rows(bars: list[dict]) -> list[dict]:
    """Bars in the collector's parquet schema, so loaders stay identical."""
    return [
        {
            "ts_day": datetime.fromtimestamp(b["open_ms"] / 1000, tz=UTC).strftime(
                "%Y-%m-%d"
            ),
            "open_ms": b["open_ms"],
            "close_ms": b["close_ms"],
            "open": b["open"],
            "high": b["high"],
            "low": b["low"],
            "close": b["close"],
            "volume": b["volume"],
            "n_trades": b["n_trades"],
        }
        for b in bars
    ]


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--dir", default=str(DEFAULT_DIR))
    a.add_argument("--coin", default="BTC")
    a.add_argument("--cross-check", action="store_true")
    a.add_argument("--summary", default=None)
    a.add_argument("--out", default=None, help="write kept 1m bars to this parquet")
    a.add_argument(
        "--verified-only",
        action="store_true",
        help="keep only minutes that match the venue candle exactly (requires "
        "--cross-check); every excluded minute is listed, never silently dropped",
    )
    args = a.parse_args()
    if args.verified_only and not args.cross_check:
        raise SystemExit(
            "--verified-only needs --cross-check: the venue is the authority"
        )

    paths = sorted(Path(args.dir).glob("*.jsonl.gz"))
    if not paths:
        raise SystemExit(f"no tick segments under {args.dir}")
    prints, coverage = read_prints(paths)
    if not prints:
        raise SystemExit("no prints read — refusing to emit an empty bar set")
    bars = bucket_minutes(prints, coverage["segments"])

    cross = None
    kept = bars
    if args.cross_check:
        from hyperliquid_daily import fetch_range, normalize

        covered = [b for b in bars if b["covered"]]
        if not covered:
            raise SystemExit("no fully covered minute — nothing to cross-check")
        start, end = covered[0]["open_ms"], covered[-1]["open_ms"] + MINUTE_MS
        venue_rows = fetch_range(
            args.coin, "1m", start, end
        )  # half-open, closed bars only
        venue = [b for b in normalize(venue_rows, until_ms=end) if b["n_trades"] > 0]
        cross = compare(
            bars,
            [
                {
                    "t": b["open_ms"],
                    "T": b["close_ms"],
                    "o": b["open"],
                    "h": b["high"],
                    "l": b["low"],
                    "c": b["close"],
                    "v": b["volume"],
                    "n": b["n_trades"],
                }
                for b in venue
            ],
        )
        if args.verified_only:
            verdict = matching_minutes(bars, venue_rows)
            kept = [b for b in bars if b["covered"] and verdict.get(b["open_ms"])]
            excluded = [
                b["open_ms"]
                for b in bars
                if b["covered"] and not verdict.get(b["open_ms"])
            ]
            cross.update(
                {
                    "kept_minutes": len(kept),
                    "excluded_mismatched_minutes": len(excluded),
                    "excluded_examples": excluded[:10],
                }
            )
            if args.out and kept:
                from hyperliquid_daily import to_parquet

                cross["written"] = to_parquet(bars_to_rows(kept), Path(args.out))

    summary = summarize(kept, coverage, cross)
    text = json.dumps(summary, sort_keys=True, default=str)
    if args.summary:
        Path(args.summary).write_text(text + "\n")
    print("TICKS-TO-1M " + text, flush=True)


if __name__ == "__main__":
    main()
