#!/usr/bin/env python3
"""HistData.com XAU/USD M1 and derived daily collector — GOLD fallback.

Locked venue for GOLD is cTrader OpenAPI (owner credentials pending, see
docs/plans/FX-SCALPING-BUILD-PLAN.md); until then this fallback collects
HistData.com free XAUUSD ASCII M1 zips and aggregates them to daily bars:

  1. GET  https://www.histdata.com/download-free-forex-data/?/ascii/1-minute-bar-quotes/xauusd/{year}
     (cookies are part of the session; token `tk` is per-response)
  2. POST https://www.histdata.com/get.php  with tk/date/datemonth/platform=ASCII/
     timeframe=M1/fxpair=XAUUSD + Referer = the year page
     → zip containing DAT_ASCII_XAUUSD_M1_{year}.csv
   3. rows "YYYYMMDD HHMMSS;o;h;l;c;v" (no header), UTC timestamps, semicolons
   4. retain every M1 price row in an optional raw artifact, then derive daily

Honesty notes (verified on 2024 + 2010 probes):
  * NO native D1 product exists at HistData — daily bars are aggregated
    from M1 and the provenance is stated exactly that way everywhere.
  * the M1 volume column is identically zero/unavailable → preserve literal zero
    and explicitly mark ``volume_available=false``; it is not real volume.
  * YEARS: 2009-2025 available for XAUUSD M1 ASCII; pages 2008 and older
    return no download form (verified 2003/2005/2008 → no form).
  * weekend absence is structural for a 24x5 FX venue: every Saturday is
    classified justified; any other absent UTC day stays an OPEN gap
    (fail-closed, ops must classify: holidays, data outages).
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import io
import json
import re
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE = "https://www.histdata.com"
YEAR_URL = BASE + "/download-free-forex-data/?/ascii/1-minute-bar-quotes/xauusd/{year}"
GET_URL = BASE + "/get.php"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:98.0) Gecko/20100101 Firefox/98.0"
LESSEE = "open_free_attribution (HistData.com free download files)"
CSV_NAME = "DAT_ASCII_XAUUSD_M1_{year}.csv"
ROW_RE = re.compile(
    r"^(\d{8}) (\d{6});([-0-9.]+);([-0-9.]+);([-0-9.]+);([-0-9.]+);([-0-9.]+)$"
)


def _opener() -> build_opener:  # cookie jar is required: tk is per-session
    jar = http.cookiejar.CookieJar()
    return build_opener(HTTPCookieProcessor(jar)), jar


def session_token(op: build_opener, year: int) -> str | None:
    """GET the year page, return its per-session `tk` token (None = no form:
    year not offered — HISTDATA simply omits the download form)."""
    req = Request(YEAR_URL.format(year=year), headers={"User-Agent": UA})
    with op.open(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    m = re.search(r'name="tk" id="tk" value="([^"]*)"', html)
    return m.group(1) if m else None


def fetch_year_zip(op: build_opener, year: int) -> bytes | None:
    """One year-zip per POST; returns None when the year is not offered."""
    tk = session_token(op, year)
    if tk is None:
        return None
    body = urlencode(
        {
            "tk": tk,
            "date": year,
            "datemonth": year,
            "platform": "ASCII",
            "timeframe": "M1",
            "fxpair": "XAUUSD",
        }
    ).encode()
    req = Request(
        GET_URL,
        data=body,
        headers={
            "User-Agent": UA,
            "Referer": YEAR_URL.format(year=year),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with op.open(req, timeout=300) as resp:
        raw = resp.read()
    return _ensure_zip(raw, year)


def _ensure_zip(raw: bytes, year: int) -> bytes:
    if not raw.startswith(b"PK"):
        raise RuntimeError(f"{year}: get.php returned non-zip ({len(raw)} bytes)")
    return raw


def unzip_csv_member(raw: bytes, year: int) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        csvs = [n for n in zf.namelist() if n.endswith(".csv")]
        expected = CSV_NAME.format(year=year)
        if csvs != [expected]:
            raise RuntimeError(f"{year}: unexpected members {zf.namelist()[:4]}")
        with zf.open(expected) as f:  # zip-slip impossible: name is derived,
            return f.read().decode("utf-8", errors="replace")  # not from the zip


def _parse_m1_source(text: str) -> list[tuple]:
    """Parse all source fields as (ts_ms, open, high, low, close, volume)."""
    out = []
    day_epoch: dict[str, int] = {}  # per-date UTC epoch cache (repeats ~1440×)
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = ROW_RE.match(line)
        if not m:
            raise ValueError(f"invalid HistData M1 row at line {line_number}")
        date, hh = m.group(1), m.group(2)
        base = day_epoch.get(date)
        if base is None:
            base = int(
                datetime.strptime(date, "%Y%m%d").replace(tzinfo=UTC).timestamp()
            )
            day_epoch[date] = base
        ts_ms = (base + int(hh[:2]) * 3600 + int(hh[2:4]) * 60 + int(hh[4:6])) * 1000
        out.append(
            (
                ts_ms,
                Decimal(m.group(3)),
                Decimal(m.group(4)),
                Decimal(m.group(5)),
                Decimal(m.group(6)),
                Decimal(m.group(7)),
            )
        )
    return out


def parse_m1(text: str) -> list[tuple[int, Decimal, Decimal, Decimal, Decimal]]:
    """Compatibility parser → (ts_ms, open, high, low, close)."""
    return [row[:5] for row in _parse_m1_source(text)]


def raw_m1_records(m1: list[tuple]) -> list[dict]:
    """Convert source records to raw M1 rows without filling or reordering."""
    validate_source_volume(m1)
    previous: int | None = None
    records = []
    for row in m1:
        ts_ms, open_, high, low, close, volume = row[:6]
        if previous is not None and ts_ms <= previous:
            raise ValueError("HistData timestamps must be strictly increasing")
        previous = ts_ms
        records.append(
            {
                "ts_ms": ts_ms,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "volume_available": False,
            }
        )
    return records


def validate_source_volume(m1: list[tuple]) -> None:
    """Enforce HistData's observed zero-volume source contract on every run."""
    for row in m1:
        if len(row) < 6:
            raise ValueError("HistData M1 row is missing source volume field")
        if row[5] != 0:
            raise ValueError("HistData volume contract violated: expected source zero")


def to_raw_parquet(records: list[dict], out: Path) -> dict:
    """Write source-resolution M1 price records with explicit volume semantics."""
    import pyarrow as pa
    from pyarrow import parquet as pq

    schema = pa.schema(
        [
            ("ts_ms", pa.int64()),
            ("open", pa.decimal128(30, 10)),
            ("high", pa.decimal128(30, 10)),
            ("low", pa.decimal128(30, 10)),
            ("close", pa.decimal128(30, 10)),
            ("volume", pa.decimal128(30, 10)),
            ("volume_available", pa.bool_()),
        ]
    )
    table = pa.Table.from_pylist(records, schema=schema)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out, compression="snappy")
    return {
        "path": str(out),
        "rows": table.num_rows,
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
    }


def aggregate_daily(m1: list[tuple]) -> list[dict]:
    """UTC-day OHLC aggregation. Volume ≡ 0 in this product → recorded
    honestly as 0; n_minutes = M1 rows touching that day."""
    days: dict[str, list[tuple]] = {}
    for r in m1:
        day = r[0] // 86_400_000
        key = datetime.fromtimestamp(day * 86_400, tz=UTC).strftime("%Y-%m-%d")
        days.setdefault(key, []).append(r)
    out = []
    for key in sorted(days):
        rows = days[key]
        out.append(
            {
                "ts_day": key,
                "ts_ms": int(
                    datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
                    * 1000
                ),
                "open": rows[0][1],
                "high": max(r[2] for r in rows),
                "low": min(r[3] for r in rows),
                "close": rows[-1][4],
                "volume": Decimal(0),
                "n_minutes": len(rows),
            }
        )
    return out


def integrity_check(bars: list[dict]) -> tuple[list[str], list[dict], int]:
    """Contiguous UTC days in-range → holes:
    Saturday → justified (structural 24x5 weekend close);
    anything else → OPEN, unjustified (fail-closed B-GATE)."""
    errs: list[str] = []
    holes: list[dict] = []
    if len(bars) < 2:
        return ["too few rows"], holes, 0
    d0 = datetime.strptime(bars[0]["ts_day"], "%Y-%m-%d").date()
    d1 = datetime.strptime(bars[-1]["ts_day"], "%Y-%m-%d").date()
    present = {b["ts_day"] for b in bars}
    day = d0
    n_absent = 0
    while day <= d1:
        key = day.strftime("%Y-%m-%d")
        if key not in present:
            n_absent += 1
            holes.append(
                {
                    "window_from": key,
                    "window_to": key,
                    "kind": "holiday",
                    "status": "open",
                    "justification": (
                        "venue closed (Saturday): structural 24x5 "
                        "FX weekend, every collected Saturday "
                        "carries zero rows"
                        if day.weekday() == 5
                        else ""
                    ),
                }
            )
        day += timedelta(days=1)
    return errs, holes, n_absent


def to_parquet(bars: list[dict], out: Path) -> dict:
    import pyarrow as pa
    from pyarrow import parquet as pq

    schema = pa.schema(
        [
            ("ts_day", pa.string()),
            ("ts_ms", pa.int64()),
            ("open", pa.decimal128(30, 10)),
            ("high", pa.decimal128(30, 10)),
            ("low", pa.decimal128(30, 10)),
            ("close", pa.decimal128(30, 10)),
            ("volume", pa.decimal128(30, 10)),
            ("n_minutes", pa.int64()),
        ]
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(bars, schema=schema), out, compression="snappy")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    return {"path": str(out), "rows": len(bars), "sha256": digest}


def capture(
    start_year: int, until_year: int, out: Path, raw_out: Path | None = None
) -> dict:
    """Sweep year-zips [start_year..until_year], aggregate all M1 → daily."""
    op, _jar = _opener()
    all_m1: list[tuple] = []
    years_offered: list[int] = []
    for year in range(start_year, until_year + 1):
        raw = fetch_year_zip(op, year)
        if raw is None:
            continue  # not offered by the site — never synthesized
        years_offered.append(year)
        all_m1.extend(_parse_m1_source(unzip_csv_member(raw, year)))
    # The source-volume contract is enforced even when only the daily artifact
    # is requested; --raw-out must not control whether input is validated.
    validate_source_volume(all_m1)
    bars = aggregate_daily(all_m1)
    errs, holes, n_absent = integrity_check(bars)
    if errs:
        raise RuntimeError(f"integrity: {errs}")
    art = to_parquet(bars, out)
    raw_art = to_raw_parquet(raw_m1_records(all_m1), raw_out) if raw_out else None
    return {
        "artifact": art,
        "holes": holes,
        "n_absent": n_absent,
        "years_offered": years_offered,
        "m1_rows": len(all_m1),
        "raw_artifact": raw_art,
        "first_day": bars[0]["ts_day"],
        "last_day": bars[-1]["ts_day"],
    }


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--start-year", type=int, default=2009)
    a.add_argument("--until-year", type=int, default=datetime.now(UTC).year)
    a.add_argument("--out", required=True)
    a.add_argument(
        "--raw-out",
        default=None,
        help="optional raw source-resolution M1 Parquet output",
    )
    a.add_argument("--summary", default=None)
    args = a.parse_args()

    from time import time

    t0 = time()
    art = capture(
        args.start_year,
        args.until_year,
        Path(args.out),
        Path(args.raw_out) if args.raw_out else None,
    )
    summary = {
        "symbol": "XAU-USD",
        "interval": "1d",
        "source": BASE,
        "license": LESSEE,
        "rows": art["artifact"]["rows"],
        "first_day": art["first_day"],
        "last_day": art["last_day"],
        "holes": art["holes"],
        "years_offered": art["years_offered"],
        "artifact": art["artifact"],
        "integrity": "PASS",
        "m1_rows": art["m1_rows"],
        "aggregation": "UTC daily from HistData ASCII M1 "
        "(no native D1 product exists at the source; documented)",
        "raw_m1_artifact": art["raw_artifact"],
        "volume_available": False,
        "volume_note": "source M1 volume column is identically zero/unavailable; "
        "literal zero retained, not real volume",
        "fetch_seconds": round(time() - t0, 2),
    }
    js = json.dumps(summary, indent=2, default=str, sort_keys=True)
    print(js)
    if args.summary:
        Path(args.summary).write_text(js)


if __name__ == "__main__":
    main()
