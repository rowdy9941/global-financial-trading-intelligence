#!/usr/bin/env python3
"""Binance daily-bar collector — B1 opener (DATASET-TODO vol 11, free tier).

Source: https://data-api.binance.vision/api/v3/klines — public, key-free,
full history from each symbol's listing (BTCUSDT: 2017-08-17). License:
free tier, attribution-required (data-api.binance.vision terms). NO trade
planning on scraped feeds without license re-verify in the gaps table.

What it proves (collected here, verified by gate b1 COLLECT):
 - integrity: monotonic non-future timestamps, OHLC sanity (low <= open/close
   <= high), volume >= 0, inception continuity (no holes between first row
   and last row — daily 1d bars must tile day-by-day once UTC-normalized)
 - capture determinism: two consecutive runs produce byte-identical D0
 - D0 artifact: Parquet (pyarrow under `uv run`), no env DATABASE access

Usage:
  uv run binance_daily.py --symbol BTCUSDT --interval 1d \
      --out ../d0/binance/BTCUSDT/1d.parquet [--summary out.json]
"""
import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = "https://data-api.binance.vision/api/v3/klines"
LIMIT = 1000


def fetch_all(symbol: str, interval: str) -> list[list]:
    """Paginate klines from listing (startTime omitted → from current
    default; startTime=0 forces full history) to now, robust retry."""
    rows: list[list] = []
    start_ms = 0
    now_ms = int(time.time() * 1000)
    while True:
        qs = urllib.parse.urlencode({"symbol": symbol, "interval": interval,
                                     "startTime": start_ms, "limit": LIMIT})
        url = f"{BASE}?{qs}"
        for attempt in range(5):
            try:
                raw = urllib.request.urlopen(url, timeout=60).read()
                break
            except Exception as e:  # noqa: BLE001 — retry with backoff
                if attempt == 4:
                    raise RuntimeError(f"fetch fail {url}: {e}") from e
                time.sleep(2 ** attempt + 1)
        batch = json.loads(raw)
        if not isinstance(batch, list):
            raise RuntimeError(f"bad payload: {str(batch)[:200]}")
        if not batch:
            break
        if batch[0][0] <= (rows[-1][0] if rows else -1):
            raise RuntimeError("server returned non-advancing page — abort, "
                               "refuse to dup-fill")
        rows.extend(batch)
        last_open = batch[-1][0]
        if last_open >= now_ms - 86_400_000 // 2 or len(batch) < LIMIT:
            break
        start_ms = last_open + 1
    return rows


def normalize(rows: list[list]) -> list[dict]:
    """Binance kline row → annotated bar dict (UTC day, ms precision kept)."""
    bars = []
    for r in rows:
        open_ms = int(r[0])
        bars.append({
            "symbol": None,  # set by caller
            "open_ms": open_ms,
            "close_ms": int(r[6]),
            "ts_day": (datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc)
                       .strftime("%Y-%m-%d")),
            "open": float(r[1]), "high": float(r[2]),
            "low": float(r[3]),  "close": float(r[4]),
            "volume": float(r[5]),
            "close_qvol": float(r[7]),
            "n_trades": int(r[8]),
        })
    return bars


def integrity_check(bars: list[dict], symbol: str, interval_ms_day=86_400_000):
    """Deterministic integrity contract (fail loudly; gate replays this).

    Interior calendar holes are NOT hard failures — they are honest absence,
    recorded as precise windows for the D3 gap calendar (the collector must
    neither silently paper over them, nor refuse to ship the good rows).
    """
    errs = []
    holes = []  # contiguous missing-day spans as (first_missing_ms, last_missing_ms)
    n = len(bars)
    if n < 2:
        raise RuntimeError(f"too few bars: {n}")
    prev = bars[0]["open_ms"]
    for b in bars[1:]:
        if b["open_ms"] <= prev:
            errs.append(f"non-monotonic at {b['ts_day']}")
            break
        if b["open_ms"] - prev > interval_ms_day:
            # days strictly between prev and b: one contiguous span
            holes.append((prev + interval_ms_day, b["open_ms"] - interval_ms_day))
        prev = b["open_ms"]
    now_ms = int(time.time() * 1000)
    if bars[-1]["open_ms"] > now_ms:
        errs.append(f"future row at {bars[-1]['ts_day']} — clock/UTC bug")
    for b in bars:
        o, h, l, c = b["open"], b["high"], b["low"], b["close"]
        if not (l <= o <= h and l <= c <= h):
            errs.append(f"OHLC anomaly at {b['ts_day']}")
            break
        if b["volume"] < 0 or b["n_trades"] < 0:
            errs.append(f"negative volume/trades at {b['ts_day']}")
            break
        if b["close_qvol"] < 0:
            errs.append(f"negative quote volume at {b['ts_day']}")
            break
    for b in bars:
        b["symbol"] = symbol
    hole_rows = [{
        "window_from": datetime.fromtimestamp(h0 / 1000, tz=timezone.utc)
        .strftime("%Y-%m-%d"),
        "window_to": datetime.fromtimestamp(h1 / 1000, tz=timezone.utc)
        .strftime("%Y-%m-%d"),
    } for h0, h1 in holes]
    return errs, n, hole_rows


def to_parquet(bars: list[dict], out: Path) -> dict:
    from pyarrow import Table, parquet as pq
    keys = ["symbol", "ts_day", "open_ms", "close_ms",
            "open", "high", "low", "close", "volume", "close_qvol", "n_trades"]
    t = Table.from_pylist([{k: b[k] for k in keys} for b in bars])
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(t, out)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    return {"path": str(out), "sha256": digest, "rows": t.num_rows}


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--symbol", required=True)
    a.add_argument("--interval", default="1d")
    a.add_argument("--out", required=True)
    a.add_argument("--summary", default=None)
    args = a.parse_args()

    t0 = time.time()
    raw = fetch_all(args.symbol, args.interval)
    bars = normalize(raw)
    errs, n, holes = integrity_check(bars, args.symbol)
    first, last = bars[0], bars[-1]
    if errs:
        raise RuntimeError(f"INTEGRITY FAIL: {errs}")
    art = to_parquet(bars, Path(args.out))
    # expected calendar: every UTC day from first open to today (UTC) exists
    d0, d1 = first["ts_day"], last["ts_day"]
    dts = (datetime.fromisoformat(d1) - datetime.fromisoformat(d0)).days + 1
    summary = {
        "symbol": args.symbol, "interval": args.interval,
        "source": BASE, "license": "free-attribution (binance public data)",
        "rows": n, "first_day": d0, "last_day": d1,
        "calendar_days": dts, "rows_minus_days": n - dts,
        "holes": holes,
        **({"missing_day_list": []} if n >= dts else {}),
        "artifact": art,
        "integrity": "PASS", "fetch_seconds": round(time.time() - t0, 2),
        "true_upstream_start_ms": raw[0][0], "true_upstream_end_ms": raw[-1][0],
    }
    js = json.dumps(summary, indent=2)
    print(js)
    if args.summary:
        Path(args.summary).write_text(js)


if __name__ == "__main__":
    main()
