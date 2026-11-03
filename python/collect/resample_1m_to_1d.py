#!/usr/bin/env python3
"""Derive a DAILY series from a registered 1-minute capture (P1 accumulation).

Why: the WF lane is day-bar based (ts_day ordering, sqrt(252) annualization,
day-granular embargo) and refuses intraday datasets on purpose
(`walk_forward.require_daily_lane`). Accumulated 1m bars therefore need an
honest 1d projection — one that never fabricates a bar out of a partial day.

Derivation rules (fail-closed, no invention):

  * a day is kept only if it is COMPLETE: every expected slot present, the
    first slot is the UTC midnight open, and no interior hole;
  * an incomplete EDGE day (capture started/ended mid-day) is DROPPED — its
    last 1m close is not the day's close, and pretending otherwise would
    inject a wrong return into the training series;
  * an incomplete INTERIOR day is a real data hole: refused outright unless
    `--allow-partial` is given, in which case it is dropped and listed;
  * ordering is by `open_ms`, never by `ts_day` (which ties across a session).

Output parquet uses the collector's own schema (`hyperliquid_daily.to_parquet`)
so a derived dataset is directly consumable by the same loaders, and the
summary is shaped for `register_b1.py` with `source` carrying the lineage
string (`derived:resample-1m-to-1d:<key>:sha-<digest>`).

Usage:
  uv run resample_1m_to_1d.py --d0-path ../../d0/hyperliquid/BTC-USDC/1m.parquet \
      --dataset-key hyperliquid/BTC-USDC/1m \
      --out ../../d0/hyperliquid/BTC-USDC/1d.parquet --summary /tmp/x.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from hyperliquid_daily import to_parquet

DAY_MS = 86_400_000
REQUIRED_COLUMNS = (
    "ts_day",
    "open_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "n_trades",
)


def read_rows(path: Path) -> list[dict]:
    """Read the parquet into plain rows, ordered by the true tick time."""
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in table.column_names]
    if missing:
        raise SystemExit(f"{path}: missing columns {missing}")
    rows = table.select(list(REQUIRED_COLUMNS)).to_pylist()
    if not rows:
        raise SystemExit(f"{path}: empty capture")
    rows.sort(key=lambda r: r["open_ms"])
    return rows


def _day_start(ms: int) -> int:
    return (ms // DAY_MS) * DAY_MS


def _day_str(start_ms: int) -> str:
    return datetime.fromtimestamp(start_ms / 1000, tz=UTC).date().isoformat()


def resample(
    rows: list[dict], *, interval_ms: int = 60_000, allow_partial: bool = False
) -> tuple[list[dict], dict]:
    """Aggregate sub-daily rows into day bars; return (kept, report)."""
    if interval_ms <= 0 or DAY_MS % interval_ms:
        raise SystemExit(f"interval_ms must divide one UTC day: {interval_ms}")
    expected_per_day = DAY_MS // interval_ms

    groups: dict[int, list[dict]] = {}
    for row in rows:
        ms = int(row["open_ms"])
        start = _day_start(ms)
        # the stored ts_day must agree with the epoch timestamp: a mismatch
        # means the capture's calendar column cannot be trusted
        if str(row["ts_day"]) != _day_str(start):
            raise SystemExit(
                f"ts_day {row['ts_day']!r} disagrees with open_ms {ms} "
                f"({_day_str(start)}) — refusing to derive from it"
            )
        groups.setdefault(start, []).append(row)

    report_incomplete: list[dict] = []
    complete: list[dict] = []
    for start in sorted(groups):
        bar_rows = groups[start]
        bar_rows.sort(key=lambda r: int(r["open_ms"]))
        slots = {int(r["open_ms"]) for r in bar_rows}
        contiguous = len(slots) == len(bar_rows) and (
            int(bar_rows[-1]["open_ms"]) - int(bar_rows[0]["open_ms"])
            == interval_ms * (len(bar_rows) - 1)
        )
        is_complete = (
            contiguous
            and int(bar_rows[0]["open_ms"]) == start
            and len(bar_rows) == expected_per_day
        )
        day = {
            "ts_day": _day_str(start),
            "open_ms": start,
            "close_ms": start + DAY_MS - 1,
            "open": bar_rows[0]["open"],
            "high": max(r["high"] for r in bar_rows),
            "low": min(r["low"] for r in bar_rows),
            "close": bar_rows[-1]["close"],
            "volume": sum((r["volume"] for r in bar_rows), Decimal(0)),
            "n_trades": sum(int(r["n_trades"]) for r in bar_rows),
        }
        if is_complete:
            complete.append(day)
        else:
            report_incomplete.append(
                {"day": day["ts_day"], "bars": len(bar_rows), "expected": expected_per_day}
            )

    first_start, last_start = min(groups), max(groups)
    kept = list(complete)
    dropped: list[dict] = []
    for item in report_incomplete:
        start = int(
            datetime.fromisoformat(item["day"]).replace(tzinfo=UTC).timestamp() * 1000
        )
        edge = start in (first_start, last_start)
        if edge:
            dropped.append({**item, "reason": "edge-partial (capture window cuts the day)"})
            continue
        if not allow_partial:
            raise SystemExit(
                f"interior day {item['day']} has {item['bars']}/{item['expected']} bars "
                "— a real hole; refusing to derive (pass --allow-partial to drop and "
                "record it instead)"
            )
        dropped.append({**item, "reason": "interior-hole (excluded)"})

    if not kept:
        raise SystemExit(
            "no complete day in the capture — nothing to derive "
            f"(incomplete: {report_incomplete})"
        )
    kept.sort(key=lambda d: d["open_ms"])
    return kept, {"expected_bars_per_day": expected_per_day, "dropped_days": dropped}


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--d0-path", required=True, help="source sub-daily parquet")
    a.add_argument("--out", required=True, help="derived daily parquet path")
    a.add_argument("--summary", default=None)
    a.add_argument("--dataset-key", default=None, help="source registry key (lineage)")
    a.add_argument("--symbol", default="BTC-USDC")
    a.add_argument("--interval-ms", type=int, default=60_000)
    a.add_argument(
        "--license", default="open_free_attribution (hyperliquid public api)"
    )
    a.add_argument("--allow-partial", action="store_true")
    args = a.parse_args()

    src = Path(args.d0_path)
    rows = read_rows(src)
    # independent digest of the SOURCE, so the derived row names exactly which
    # artifact it came from and a later re-capture is visibly a different input
    src_sha = hashlib.sha256(src.read_bytes()).hexdigest()

    bars, rep = resample(
        rows, interval_ms=args.interval_ms, allow_partial=args.allow_partial
    )
    out = Path(args.out)
    art = to_parquet(bars, out)

    key = args.dataset_key or src.name
    source = f"derived:resample-1m-to-1d:{key}:sha-{src_sha[:16]}"
    summary = {
        "symbol": args.symbol,
        "interval": "1d",
        "source": source,
        "license": args.license,
        "first_day": bars[0]["ts_day"],
        "last_day": bars[-1]["ts_day"],
        "rows": len(bars),
        "holes": [],
        "integrity": "PASS",
        "artifact": {"path": str(out), "sha256": art["sha256"], "rows": art["rows"]},
        "derived_from": {
            "dataset_key": args.dataset_key,
            "d0_path": str(src),
            "sha256": src_sha,
            "rows": len(rows),
            "interval_ms": args.interval_ms,
        },
        "expected_bars_per_day": rep["expected_bars_per_day"],
        "dropped_partial_days": rep["dropped_days"],
    }
    if args.summary:
        Path(args.summary).write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
