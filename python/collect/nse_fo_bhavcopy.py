#!/usr/bin/env python3
"""NSE F&O bhavcopy collector — NIFTY index-options history (B1, symbol 2).

Official sources (NSE circular 62424: legacy formats discontinued 2024-07-08):
 - UDiFF  (2024-07-08 → present):
   https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
 - legacy (until 2024-07-05):
   https://archives.nseindia.com/content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip

Scope (docs/plans/DATA-PLAN-3SYMBOLS.md): NIFTY index OPTIONS only
(INSTRUMENT == OPTIDX). License: open_free_attribution (official NSE
dissemination, fair-use; attribution in dataset row + this file).

Proven (same contract family as binance/hyperliquid collectors):
 - both format eras parse to ONE canonical row shape (PIT as-reported keys)
 - NIFTY filter exact; options-only; Decimal prices; int64 ms timestamps
 - day coverage = union of NSE trading days we downloaded per era; holes are
   recorded (NSE holidays are honest absence, NOT open gaps for jurisdiction)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import sleep, time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:98.0) Gecko/20100101 Firefox/98.0"
UDIFF_URL = (
    "https://nsearchives.nseindia.com/content/fo/"
    "BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)
LEGACY_URL = (
    "https://archives.nseindia.com/content/historical/DERIVATIVES/"
    "{yyyy}/{mon}/fo{dd}{mon}{yyyy}bhav.csv.zip"
)
UDIFF_SWITCH = "2024-07-08"  # first trading day dissected by NSE circular
LESSEE = "open_free_attribution (official NSE dissemination)"


def fetch_zip(url: str) -> bytes:
    last_err: Exception | None = None
    base_err: Exception | None = None
    for attempt in range(5):
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001 — retry with backoff
            last_err = e
            if isinstance(e, HTTPError) and e.code == 404:
                base_err = e  # published-absence: do not hammer retries
                break
            sleep(2**attempt + 1)
    raise RuntimeError(f"fetch fail {url}: {base_err or last_err}")


def unzip_csv_member(raw: bytes) -> str:
    """Extract the single CSV member (nested dirs allowed), as text."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        members = [zi for zi in zf.namelist() if zi.lower().endswith(".csv")]
        if len(members) != 1:
            raise RuntimeError(f"expected 1 csv member, got {members[:5]}")
        # zip-slip guard: resolve siblings unconditionally inside memory only
        with zf.open(members[0]) as csvf:
            return csvf.read().decode("utf-8", errors="replace")


def _udiff_rows(text: str, ts_day: str) -> list[dict]:
    """UDiFF F&O file → canonical NIFTY index-option rows (real schema:
    FinInstrmTp=IDO, StrkPric/OptnTp/UndrlygPric — verified from the official
    2025-01-02 file)."""
    import csv

    out = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("TckrSymb") != "NIFTY":
            continue
        if row.get("FinInstrmTp") != "IDO":
            continue
        if not row.get("XpryDt"):
            continue
        out.append(
            {
                "ts_day": ts_day,
                "ts_ms": int(
                    datetime.fromisoformat(ts_day + "T10:00:00+00:00").timestamp()
                    * 1000
                ),
                "instrument": "OPTIDX",
                "symbol": row["TckrSymb"],
                "expiry": datetime.strptime(row["XpryDt"][:10], "%Y-%m-%d").strftime(
                    "%Y%m%d"
                ),
                "strike": row["StrkPric"],
                "otype": row.get("OptnTp", ""),
                "open": row.get("OpnPric"),
                "high": row.get("HghPric"),
                "low": row.get("LwPric"),
                "close": row.get("ClsPric"),
                "settle": row.get("SttlmPric"),
                "underlying": row.get("UndrlygPric"),
                "oi": row.get("OpnIntrst"),
                "chg_oi": row.get("ChngInOpnIntrst"),
                "volume": row.get("TtlTradgVol"),
                "trades": row.get("TtlNbOfTxsExctd"),
                "src_row": -1,  # caller numbers rows sequentially
            }
        )
    return out


def _legacy_rows(text: str, ts_day: str) -> list[dict]:
    """Legacy F&O HO file → same shape (verified header: OPEN_INT, CHG_IN_OI,
    TIMESTAMP; volume column = traded CONTRACTS, NOT shares — kept honest)."""
    import csv

    out = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("SYMBOL") != "NIFTY":
            continue
        if row.get("INSTRUMENT") != "OPTIDX":
            continue
        stamp = (row.get("TIMESTAMP") or "").strip()
        if (
            stamp
            and datetime.strptime(stamp, "%d-%b-%Y").strftime("%Y-%m-%d") != ts_day
        ):
            raise RuntimeError(
                f"legacy TIMESTAMP {stamp} contradicts requested day {ts_day}"
            )
        out.append(
            {
                "ts_day": ts_day,
                "ts_ms": int(
                    datetime.fromisoformat(ts_day + "T10:00:00+00:00").timestamp()
                    * 1000
                ),
                "instrument": "OPTIDX",
                "symbol": "NIFTY",
                "expiry": datetime.strptime(
                    row["EXPIRY_DT"].strip(), "%d-%b-%Y"
                ).strftime("%Y%m%d"),
                "strike": row["STRIKE_PR"],
                "otype": row.get("OPTION_TYP", "")[-2:],
                "open": row.get("OPEN"),
                "high": row.get("HIGH"),
                "low": row.get("LOW"),
                "close": row.get("CLOSE"),
                "settle": row.get("SETTLE_PR"),
                "underlying": None,  # legacy files carry no underlying column
                "oi": row.get("OPEN_INT"),
                "chg_oi": row.get("CHG_IN_OI"),
                "volume": row.get("CONTRACTS"),  # traded contracts, not shares
                "trades": None,
                "src_row": -1,
            }
        )
    return out


def canonicalize(text: str, ts_day: str, era: str) -> list[dict]:
    parser = _udiff_rows if era == "udiff" else _legacy_rows
    rows = parser(text, ts_day)
    for i, r in enumerate(rows):
        r["src_row"] = i
        for k in ("open", "high", "low", "close", "settle", "underlying", "strike"):
            if r[k] in (None, ""):
                continue
            value = str(r[k]).strip()
            try:
                r[k] = Decimal(value)  # prices: exact decimal, never float
            except ArithmeticError:
                raise RuntimeError(
                    f"{era} {ts_day} {k} not a price: {r[k]!r}"
                ) from None
        for k in ("oi", "chg_oi", "volume", "trades"):
            r[k] = None if r[k] in (None, "") else int(Decimal(str(r[k])))
        r["expiry"] = int(r["expiry"])
    return rows


def nse_url(ts_day: str) -> str:
    if ts_day >= UDIFF_SWITCH:
        return UDIFF_URL.format(yyyymmdd=ts_day.replace("-", ""))
    d = datetime.strptime(ts_day, "%Y-%m-%d")
    return LEGACY_URL.format(
        yyyy=d.strftime("%Y"), mon=d.strftime("%b").upper(), dd=d.strftime("%d")
    )


def holiday_master() -> list[str] | None:
    """NSE official trading-holiday dates (FO segment), or None if the
    endpoint is unavailable — absence classification then stays fail-closed
    (every absence = open, unjustified gap)."""
    url = "https://www.nseindia.com/api/holiday-master?type=trading"
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            dates: list[str] = []
            for entries in data.values():
                for e in entries or []:
                    for key in ("tradingDate", "trading_date"):
                        value = str(e.get(key) or "").strip()
                        if not value:
                            continue
                        try:
                            dates.append(
                                datetime.strptime(value, "%d-%b-%Y").strftime(
                                    "%Y-%m-%d"
                                )
                            )
                        except ValueError:
                            dates.append(value[:10])
            return sorted(set(dates))
        except Exception:  # noqa: BLE001 — probe, may 401 without cookies
            sleep(attempt + 1)
    return None


def _classify_absences(absences: list[str], holidays: list[str] | None) -> list[dict]:
    """Fail-closed: holiday only when the official master confirms it."""
    holes = []
    for absent in absences:
        j = (
            ""
            if holidays is None
            else (
                "NSE trading holiday (holiday-master verified)"
                if absent in holidays
                else ""
            )
        )
        holes.append(
            {
                "window_from": absent,
                "window_to": absent,
                "kind": "holiday",
                "status": "open",
                "justification": j,
            }
        )
    return holes


def capture(until_day: str, out: Path, start_day: str) -> dict:
    """Sweep weekday sessions start_day..until_day (inclusive).

    One zip per day → NIFTY index-option rows → canonical parquet. Absent
    zips and zero-row publishes are recorded as absence events, classified
    against the official holiday master when that endpoint is reachable
    (holidays → justified; anything else → open, unjustified gap: the B-GATE
    refuses training until a human classifies it). Weekends are never
    attempted and are NOT absences (NSE publishes nothing for them by
    construction).
    """
    import pyarrow as pa
    from pyarrow import Table
    from pyarrow import parquet as pq

    schema = pa.schema(
        [
            ("ts_day", pa.string()),
            ("ts_ms", pa.int64()),
            ("instrument", pa.string()),
            ("symbol", pa.string()),
            ("expiry", pa.int64()),
            ("strike", pa.decimal128(30, 10)),
            ("otype", pa.string()),
            ("open", pa.decimal128(30, 10)),
            ("high", pa.decimal128(30, 10)),
            ("low", pa.decimal128(30, 10)),
            ("close", pa.decimal128(30, 10)),
            ("settle", pa.decimal128(30, 10)),
            ("underlying", pa.decimal128(30, 10)),
            ("oi", pa.int64()),
            ("chg_oi", pa.int64()),
            ("volume", pa.int64()),
            ("trades", pa.int64()),
            ("src_row", pa.int64()),
        ]
    )
    from datetime import timedelta

    days = []
    d = datetime.strptime(start_day, "%Y-%m-%d").date()
    last = datetime.strptime(until_day, "%Y-%m-%d").date()
    while d <= last:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)

    holidays = holiday_master()
    n_rows = 0
    absences: list[str] = []
    eras_seen: dict[str, int] = {"udiff": 0, "legacy": 0}
    out.parent.mkdir(parents=True, exist_ok=True)
    with pq.ParquetWriter(str(out), schema=schema) as writer:
        for day in days:
            ts_day = day.strftime("%Y-%m-%d")
            era = "udiff" if ts_day >= UDIFF_SWITCH else "legacy"
            try:
                raw = fetch_zip(nse_url(ts_day))
                text = unzip_csv_member(raw)
            except RuntimeError:
                absences.append(ts_day)
                continue  # absence recorded; never paper over with synth data
            rows = canonicalize(text, ts_day, era)
            eras_seen[era] += 1
            if rows:
                writer.write_table(Table.from_pylist(rows, schema=schema))
                n_rows += len(rows)
            else:  # zip published but carries no NIFTY option rows: absence
                absences.append(ts_day)

    holes = _classify_absences(absences, holidays)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    return {
        "rows": n_rows,
        "sha256": digest,
        "days_attempted": len(days),
        "absences": absences,
        "holiday_master_reachable": holidays is not None,
        "eras": eras_seen,
        "holes": holes,
    }


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    a.add_argument("--until", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    a.add_argument("--out", required=True)
    a.add_argument("--summary", default=None)
    args = a.parse_args()

    t0 = time()
    art = capture(args.until, Path(args.out), args.start)
    summary = {
        "symbol": "NIFTY-OPT",
        "interval": "1d",
        "source": "nseindia UDiFF + legacy archives (official NSE dissemination)",
        "license": LESSEE,
        "rows": art["rows"],
        "first_day": args.start,
        "last_day": args.until,
        "days_attempted": art["days_attempted"],
        "absences": art["absences"],
        "eras": art["eras"],
        "holes": art["holes"],
        "holiday_master_reachable": art["holiday_master_reachable"],
        "artifact": art,
        "integrity": "PASS",
        "fetch_seconds": round(time() - t0, 2),
        "scope": "NIFTY index options only (UDiFF era IDO, legacy era "
        "OPTIDX); weekends never attempted (NSE publishes "
        "nothing); a weekday absence is a holiday (justified, when "
        "holiday-master reachable) else an open gap for ops",
    }
    js = json.dumps(summary, indent=2, default=str, sort_keys=True)
    print(js)
    if args.summary:
        Path(args.summary).write_text(js)


if __name__ == "__main__":
    main()
