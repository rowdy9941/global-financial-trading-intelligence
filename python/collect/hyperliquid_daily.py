#!/usr/bin/env python3
"""Hyperliquid BTC-USD perp candle collector — B1 (3-symbol E2E data plan).

Source: POST https://api.hyperliquid.xyz/info {"type":"candleSnapshot","req":
  {"coin":"BTC","interval":"1d","startTime":ms,"endTime":ms}} — public,
key-free, up to 5000 candles per call; probe-verified full 1d history back to
perp inception 2023-11-09 (docs/plans/DATA-PLAN-3SYMBOLS.md).

NOTE: HL spot has no native BTC/USDC (only thin wrapped UBTC) — the perp is
the real BTC venue on Hyperliquid; requester-pays S3 archives are out of
scope. License: Hyperliquid terms (public API data), attribution class
"open_free_attribution (hyperliquid public api)".

Proves (collected here, verified downstream):
 - closed bars only (endTime = now - interval; never ships a partial day)
 - Decimal px/vol straight from HL's string protocol (never float in-flight)
 - integrity: monotonic non-future timestamps, OHLC sanity, volume >= 0,
   calendar tiling of UTC days (holes are recorded, never papered over)
 - capture determinism: two consecutive runs → byte-identical D0

Usage:
  uv run hyperliquid_daily.py --coin BTC --interval 1D \
      --out ../d0/hyperliquid/BTC-USD/1d.parquet [--summary out.json]
"""

import argparse
import hashlib
import json
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import sleep, time

BASE = "https://api.hyperliquid.xyz/info"
VALID_INTERVALS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
    "1M": 2_592_000_000,  # month is the only capital interval
}
WINDOW_CANDLES = 4_000  # ≤ 5000 server cap, leaves room for clock skew
API_MIN_START_MS = 1  # startTime=0 is rejected by the public API (422)
LESSEE = "open_free_attribution (hyperliquid public api)"


def fetch_window(
    coin: str, interval: str, start_ms: int, end_ms: int, _post=None
) -> list[dict]:
    """One candleSnapshot window (≤5000 candles), robust retry."""
    post = _post or _post_info
    payload = json.dumps(
        {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        }
    ).encode()
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            return json.loads(post(payload))
        except Exception as e:  # noqa: BLE001 — retry with backoff
            last_err = e
            sleep(2**attempt + 1)
    raise RuntimeError(f"candleSnapshot fail [{start_ms},{end_ms}]: {last_err}")


def _post_info(payload: bytes) -> bytes:
    req = urllib.request.Request(
        BASE,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_all(
    coin: str, interval: str, until_ms: int, _post=None, max_windows: int = 64
) -> list[dict]:
    """Full closed-bar history paginating BACKWARD from until_ms.

    The API rejects startTime 0 with 422 (verified live), so windows walk
    from [until-step, until) down to inception; a 4000-candle window is
    already inside the server's 5000-candle response cap. Overlap across
    windows, or duplicates inside one, abort the capture.
    """
    interval_ms = VALID_INTERVALS[interval]
    window_ms = interval_ms * WINDOW_CANDLES
    chunks: list[list[dict]] = []
    end = until_ms
    for _ in range(max_windows):
        # endTime is inclusive. Keep each request to at most WINDOW_CANDLES
        # aligned bars, then move the next end before the prior first candle.
        start = max(API_MIN_START_MS, end - window_ms + interval_ms)
        batch = fetch_window(coin, interval, start, end, _post=_post)
        for r in batch:
            if not isinstance(r, dict) or "t" not in r or "c" not in r:
                raise RuntimeError(f"bad candle row: {str(r)[:120]}")
            if chunks and r["t"] >= chunks[0][0]["t"]:
                raise RuntimeError(
                    "overlapping/non-advancing window — abort, refuse to dup-fill"
                )
        if batch:
            if any(batch[i]["t"] >= batch[i + 1]["t"] for i in range(len(batch) - 1)):
                raise RuntimeError(
                    "server returned non-monotonic page — abort, refuse to dup-fill"
                )
            chunks.insert(0, batch)
        if start == API_MIN_START_MS:
            return [r for c in chunks for r in c]  # inception reached
        end = batch[0]["t"] - 1 if batch else start  # inclusive API boundary
    raise RuntimeError(
        f"pagination exceeded {max_windows} windows without inception — "
        "refusing ambiguous capture (raise WINDOW_CANDLES or max_windows)"
    )


def fetch_range(
    coin: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    _post=None,
    max_windows: int = 64,
) -> list[dict]:
    """Fetch closed candles in the aligned half-open range [start_ms, end_ms).

    Hyperliquid's ``endTime`` behaves inclusively (a bounded live probe returned
    a candle whose open time equalled endTime), so each request ends at the last
    included candle. Results outside the requested interval or malformed,
    unaligned, partial, duplicate, or non-advancing pages fail closed. Sparse
    timestamps are retained as gaps; no bar is synthesized.
    """
    if interval not in VALID_INTERVALS:
        raise ValueError(f"unsupported interval: {interval}")
    step = VALID_INTERVALS[interval]
    if start_ms < API_MIN_START_MS or end_ms <= start_ms:
        raise ValueError("start_ms/end_ms must define a positive API-supported range")
    if start_ms % step or end_ms % step:
        raise ValueError(f"range bounds must be aligned to {interval}")
    count = (end_ms - start_ms) // step
    required_windows = (count + WINDOW_CANDLES - 1) // WINDOW_CANDLES
    if required_windows > max_windows:
        raise RuntimeError(
            f"range requires {required_windows} windows, exceeding max_windows={max_windows}; "
            "refusing truncated capture"
        )

    rows: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        page_count = min(WINDOW_CANDLES, (end_ms - cursor) // step)
        page_end = cursor + (page_count - 1) * step
        batch = fetch_window(coin, interval, cursor, page_end, _post=_post)
        if len(batch) > page_count:
            raise RuntimeError("API response exceeds requested page range/cap")
        previous = cursor - step
        for row in batch:
            if (
                not isinstance(row, dict)
                or not {"t", "T", "o", "h", "l", "c", "v", "n"} <= row.keys()
            ):
                raise RuntimeError(f"bad candle row: {str(row)[:120]}")
            if row.get("i", interval) != interval or row.get("s", coin) != coin:
                raise RuntimeError("API candle symbol/interval does not match request")
            ts = int(row["t"])
            if ts < cursor or ts > page_end or ts % step:
                raise RuntimeError(
                    "API returned candle outside aligned requested range"
                )
            if ts <= previous:
                raise RuntimeError("overlapping/duplicate/non-advancing candle page")
            if int(row["T"]) != ts + step - 1:
                raise RuntimeError(
                    "candle is not a complete interval-aligned closed bar"
                )
            try:
                prices = [Decimal(str(row[key])) for key in ("o", "h", "l", "c")]
                volume = Decimal(str(row["v"]))
                trade_count = int(row["n"])
            except Exception as exc:
                raise RuntimeError(
                    "API candle contains invalid numeric fields"
                ) from exc
            open_, high, low, close = prices
            if not (low <= open_ <= high and low <= close <= high):
                raise RuntimeError("API candle violates OHLC bounds")
            if volume < 0 or trade_count < 0:
                raise RuntimeError("API candle has negative volume/trade count")
            previous = ts
            rows.append(row)
        cursor = page_end + step
    return rows


def normalize(rows: list[dict], until_ms: int) -> list[dict]:
    """HL candle dict → bar dict (UTC day from open_t; Decimal px/vol)."""
    bars = []
    for r in rows:
        open_ms = int(r["t"])
        if open_ms >= until_ms:  # never ship an in-progress bar
            continue
        bars.append(
            {
                "ts_day": datetime.fromtimestamp(open_ms / 1000, tz=UTC).strftime(
                    "%Y-%m-%d"
                ),
                "open_ms": open_ms,
                "close_ms": int(r["T"]),
                "open": Decimal(str(r["o"])),
                "high": Decimal(str(r["h"])),
                "low": Decimal(str(r["l"])),
                "close": Decimal(str(r["c"])),
                "volume": Decimal(str(r["v"])),
                "n_trades": int(r["n"]),
            }
        )
    return bars


def venue_onset(bars: list[dict]) -> tuple[list[dict], str | None, int]:
    """Trim to the first row with real market activity (n_trades > 0).

    The API backfills pre-venue days as volume-0/0-trade reference prices
    (verified: 2020-08→2023-10 all n=0). Those rows are NOT on-venue market
    data — shipping them into a PIT registry would be dishonest, so the
    collector excludes them and reports the cutoff explicitly.
    """
    for i, b in enumerate(bars):
        if b["n_trades"] > 0:
            return bars[i:], b["ts_day"], i
    return [], None, len(bars)


def integrity_check(bars: list[dict], coin: str, step_ms: int):
    """Same contract as binance_daily: holes recorded, fail on anomalies."""
    errs: list[str] = []
    holes: list[dict] = []
    n = len(bars)
    if n < 2:
        raise RuntimeError(f"too few rows: {n} — refusing ambiguous capture")
    prev = bars[0]["open_ms"]
    for b in bars[1:]:
        if b["open_ms"] <= prev:
            errs.append(f"non-monotonic at {b['ts_day']}")
            break
        if b["open_ms"] - prev > step_ms:
            span = b["open_ms"] - prev
            # only a *whole-bar* gap is an honest absent window
            if span % step_ms == 0:
                holes.append(
                    {
                        "window_from": datetime.fromtimestamp(
                            (prev + step_ms) / 1000, tz=UTC
                        ).strftime("%Y-%m-%d"),
                        "window_to": datetime.fromtimestamp(
                            (b["open_ms"] - step_ms) / 1000, tz=UTC
                        ).strftime("%Y-%m-%d"),
                    }
                )
        prev = b["open_ms"]
    now_ms = int(time() * 1000)
    for b in bars:
        if b["open_ms"] >= now_ms:
            errs.append(f"future-row at {b['ts_day']} — clock/UTC bug")
            break
        o, h, l, c = b["open"], b["high"], b["low"], b["close"]
        if not (l <= o <= h and l <= c <= h):
            errs.append(f"OHLC anomaly at {b['ts_day']}")
            break
        if b["volume"] < 0 or b["n_trades"] < 0:
            errs.append(f"negative volume/trades at {b['ts_day']}")
            break
    return errs, n, holes


def to_parquet(bars: list[dict], out: Path) -> dict:
    import pyarrow as pa
    from pyarrow import Table
    from pyarrow import parquet as pq

    schema = pa.schema(
        [
            ("ts_day", pa.string()),
            ("open_ms", pa.int64()),
            ("close_ms", pa.int64()),
            ("open", pa.decimal128(30, 10)),
            ("high", pa.decimal128(30, 10)),
            ("low", pa.decimal128(30, 10)),
            ("close", pa.decimal128(30, 10)),
            ("volume", pa.decimal128(30, 10)),
            ("n_trades", pa.int64()),
        ]
    )
    keyed = [{k: b[k] for k in schema.names} for b in bars]
    t = Table.from_pylist(keyed, schema=schema)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(t, out)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    return {"path": str(out), "sha256": digest, "rows": t.num_rows}


def read_parquet_rows(path: Path) -> list[dict]:
    """Read a previously written artifact back into bar dicts (collector schema)."""
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    cols = {name: table.column(name).to_pylist() for name in table.column_names}
    rows = []
    for i in range(table.num_rows):
        rows.append(
            {
                "ts_day": cols["ts_day"][i],
                "open_ms": int(cols["open_ms"][i]),
                "close_ms": int(cols["close_ms"][i]),
                "open": Decimal(str(cols["open"][i])),
                "high": Decimal(str(cols["high"][i])),
                "low": Decimal(str(cols["low"][i])),
                "close": Decimal(str(cols["close"][i])),
                "volume": Decimal(str(cols["volume"][i])),
                "n_trades": int(cols["n_trades"][i]),
            }
        )
    return rows


def merge_bars(existing: list[dict], new: list[dict]) -> tuple[list[dict], dict]:
    """Union an existing archive with a freshly captured window, by `open_ms`.

    Why this exists: the public API retains ~3 days of 1m bars, so a capture that
    simply REWRITES the trailing window never grows — it stays at three days
    forever (measured 2026-11-04: rows == 4,320 == exactly 3 days, every run).
    Accumulation requires a union with the local archive.

    Conflict rule: a closed bar is immutable, so an overlap must be identical. If
    the venue's numbers for an already-captured minute differ, the EXISTING row is
    kept and the difference is reported as a `revision` — silently adopting either
    side would rewrite history without evidence.
    """
    merged: dict[int, dict] = {b["open_ms"]: b for b in existing}
    added = 0
    overlapping = 0
    revisions = []
    for bar in new:
        prev = merged.get(bar["open_ms"])
        if prev is None:
            merged[bar["open_ms"]] = bar
            added += 1
            continue
        overlapping += 1
        fields = ("open", "high", "low", "close", "volume", "n_trades")
        diff = {f: [str(prev[f]), str(bar[f])] for f in fields if prev[f] != bar[f]}
        if diff:
            revisions.append(
                {"open_ms": bar["open_ms"], "kept": "existing", "diff": diff}
            )
    ordered = [merged[k] for k in sorted(merged)]
    return ordered, {
        "existing_rows": len(existing),
        "new_rows": len(new),
        "added": added,
        "overlapping": overlapping,
        "revisions": len(revisions),
        "revision_examples": revisions[:5],
        "merged_rows": len(ordered),
    }


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--coin", required=True)
    a.add_argument(
        "--symbol",
        default=None,
        help="identity override for the summary/registry (default <coin>-USD);\n        the Hyperliquid perp quotes in USDC, so P1 registers BTC-USDC — an\n        explicit identity, never a post-hoc rename of an existing dataset",
    )
    a.add_argument("--interval", default="1d", choices=sorted(VALID_INTERVALS))
    a.add_argument("--out", required=True)
    a.add_argument("--summary", default=None)
    a.add_argument(
        "--start-ms",
        type=int,
        default=None,
        help="aligned inclusive start for a bounded capture",
    )
    a.add_argument(
        "--end-ms",
        type=int,
        default=None,
        help="aligned exclusive end for a bounded capture",
    )
    a.add_argument("--max-windows", type=int, default=64)
    a.add_argument(
        "--merge",
        action="store_true",
        help="union the captured window with the existing --out archive instead of "
        "replacing it; required for any scheduling whose window is shorter than "
        "the history you intend to keep",
    )
    args = a.parse_args()

    step_ms = VALID_INTERVALS[args.interval]
    until_ms = int(time() * 1000) - step_ms  # closed bars only
    t0 = time()
    requested_start_ms = None
    requested_end_ms = None
    expected_bar_slots = None
    if (args.start_ms is None) != (args.end_ms is None):
        raise ValueError("--start-ms and --end-ms must be supplied together")
    if args.start_ms is None:
        raw = fetch_all(
            args.coin, args.interval, until_ms, max_windows=args.max_windows
        )
    else:
        requested_start_ms = args.start_ms
        requested_end_ms = args.end_ms
        closed_end_ms = (int(time() * 1000) // step_ms) * step_ms
        if args.end_ms > closed_end_ms:
            raise ValueError(
                "--end-ms must not extend beyond the latest closed interval"
            )
        expected_bar_slots = (args.end_ms - args.start_ms) // step_ms
        raw = fetch_range(
            args.coin,
            args.interval,
            args.start_ms,
            args.end_ms,
            max_windows=args.max_windows,
        )
        until_ms = args.end_ms
    bars_full = normalize(raw, until_ms)
    bars, onset_day, excluded = venue_onset(bars_full)
    merge_report = None
    out_path = Path(args.out)
    if args.merge and out_path.exists():
        bars, merge_report = merge_bars(read_parquet_rows(out_path), bars)
        bars = sorted(bars, key=lambda b: b["open_ms"])
    errs, n, holes = integrity_check(bars, args.coin, step_ms)
    if errs:
        raise RuntimeError(f"INTEGRITY FAIL: {errs}")
    art = to_parquet(bars, Path(args.out))
    d0, d1 = bars[0]["ts_day"], bars[-1]["ts_day"]
    calendar_days = (datetime.fromisoformat(d1) - datetime.fromisoformat(d0)).days + 1
    summary = {
        "symbol": args.symbol or f"{args.coin}-USD",
        "interval": args.interval,
        "source": BASE,
        "license": LESSEE,
        "rows": n,
        "first_day": d0,
        "last_day": d1,
        "calendar_days": calendar_days,
        "rows_minus_days": n - calendar_days,
        "holes": holes,
        "venue_onset_day": onset_day,
        "pre_onset_excluded_rows": excluded,
        "closed_until_ms": until_ms,
        "requested_start_ms": requested_start_ms,
        "requested_end_exclusive_ms": requested_end_ms,
        "expected_bar_slots": expected_bar_slots,
        "returned_bar_rows": len(raw),
        "missing_bar_slots": (
            expected_bar_slots - len(raw) if expected_bar_slots is not None else None
        ),
        "archive": (
            {
                **merge_report,
                "span_open_ms": [bars[0]["open_ms"], bars[-1]["open_ms"]],
                "expected_slots": (bars[-1]["open_ms"] - bars[0]["open_ms"]) // step_ms
                + 1,
            }
            if merge_report is not None
            else None
        ),
        "artifact": art,
        "integrity": "PASS",
        "fetch_seconds": round(time() - t0, 2),
    }
    # whitespace-shape determinism: bytes identical each run for stable hashes
    js = json.dumps(summary, indent=2, default=str, sort_keys=True)
    print(js)
    if args.summary:
        Path(args.summary).write_text(js)


if __name__ == "__main__":
    main()
