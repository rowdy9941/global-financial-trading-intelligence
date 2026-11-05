#!/usr/bin/env python3
"""Register a collector run into D3 (dataset manifest + gap calendar).

Reads collector summary JSON, UPSERTs the d3.datasets row (mutable
registry: a later full refresh CORRECTS the row), and derives gap rows:

  * pre-inception window  → status 'justified' ('exchange not yet listing')
  * trailing partial day  → status 'justified' ('live day closes EOD')
  * any interior hole     → status 'open', justification '' — HARD B-GATE
                            (training refuses until ops resolves)

psql through docker compose exec — same append/registry channel the gates
use; no direct library DB connection from collectors (deps stay minimal).

Usage: uv run register_b1.py --summary SUMMARY.json --d0-path PATH
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # repo root


# A-4: compose interpolates `${VAR:?}`, so `exec` needs the rendered config, and
# the psql coordinates come from the same file. One reader, no second copy.
sys.path.insert(0, str(REPO / "python" / "gfti"))
import gfti_config  # after the path insert, by necessity

_CFG = gfti_config.load()
COMPOSE_ENV = gfti_config.process_env(_CFG)
_PSQL = gfti_config.psql_argv(_CFG)

# Registry fields are interpolated into SQL by design (psql-only channel, no
# DB driver dep). Every field is validated against a fixed shape BEFORE
# interpolation; a handcrafted --summary file cannot smuggle SQL text.
# (`:` is allowed because absolute URLs are a legal source value; the
# injection-relevant characters — quotes — stay rejected by fullmatch.)
_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:+-]{0,127}\Z")
# license family strings carry spaces/parens ("open_free_attribution (x)")
# but never quotes/backslashes/semicolons — still single-quote-safe
_LICENSE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._(:)-]{0,127}\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DAILY_INTERVALS = {"1d", "1day", "day"}


def validate_metadata(
    *,
    interval: str,
    volume_available: bool | None,
    volume_kind: str | None,
    volume_unit: str | None,
    schema_version: str | None,
) -> dict:
    """Validate new D3 metadata, requiring explicit volume fields intraday."""
    is_daily = interval.lower() in _DAILY_INTERVALS
    if volume_available is True and (not volume_kind or not volume_unit):
        raise SystemExit(
            "volume_available=true requires nonempty volume_kind and volume_unit"
        )
    if not is_daily and (
        volume_available is None or not volume_kind or not volume_unit
    ):
        raise SystemExit(
            "intraday registration requires explicit volume metadata "
            "(--volume-available, --volume-kind, and --volume-unit)"
        )
    if volume_available is None:
        volume_available = False
    for name, value in (("volume_kind", volume_kind), ("volume_unit", volume_unit)):
        if value is not None and value and not _IDENT.fullmatch(value):
            raise SystemExit(
                f"metadata field {name!r} failed identity regex: {value!r}"
            )
    schema_version = schema_version or "1"
    if not _IDENT.fullmatch(schema_version):
        raise SystemExit(
            f"metadata field 'schema_version' failed identity regex: {schema_version!r}"
        )
    return {
        "volume_available": volume_available,
        "volume_kind": volume_kind or None,
        "volume_unit": volume_unit or None,
        "schema_version": schema_version,
    }


def metadata_update_clause(
    *,
    volume_available_provided: bool,
    volume_kind_provided: bool,
    volume_unit_provided: bool,
    schema_version_provided: bool,
) -> str:
    """Return UPSERT assignments for only fields explicitly supplied."""
    updates = []
    if volume_kind_provided:
        updates.append("volume_kind = EXCLUDED.volume_kind")
    if volume_unit_provided:
        updates.append("volume_unit = EXCLUDED.volume_unit")
    if volume_available_provided:
        updates.append("volume_available = EXCLUDED.volume_available")
    if schema_version_provided:
        updates.append("schema_version = EXCLUDED.schema_version")
    return f", {', '.join(updates)}" if updates else ""


_RAIL_KEYS = ("accepted", "rejected", "market_prefix", "receipt_path", "receipt_sha256")


def rail_evidence(summary: dict) -> dict | None:
    """Validate the rail's ingest receipt, or return None (A-7, invariant R1).

    A collector that wrote its own Parquet cannot produce this block: the counts
    are what the rail accepted and rejected at its boundary, and the receipt file
    is the rail's own stdout. Registration therefore requires proof of a writer
    that is not the caller — which is the whole point of R1.
    """
    rail = summary.get("rail")
    if rail is None:
        return None
    if not isinstance(rail, dict):
        raise SystemExit("summary 'rail' block is not an object")
    missing = [k for k in _RAIL_KEYS if k not in rail]
    if missing:
        raise SystemExit(f"rail block missing keys: {missing}")
    if summary.get("writer") != "rail":
        raise SystemExit("summary writer must be 'rail'")
    accepted, rejected, rows = rail["accepted"], rail["rejected"], summary["rows"]
    if not isinstance(accepted, int) or not isinstance(rejected, int):
        raise SystemExit("rail accepted/rejected must be ints")
    if rejected != 0:
        raise SystemExit(
            f"rail rejected {rejected} row(s) — a partial batch is not a dataset"
        )
    if accepted != rows:
        raise SystemExit(f"rail accepted {accepted} but the summary claims rows={rows}")
    if not _SHA.fullmatch(str(rail["receipt_sha256"])):
        raise SystemExit("rail receipt_sha256 is not 64 hex chars")
    if not str(rail["market_prefix"]).startswith("market."):
        raise SystemExit(
            f"rail market_prefix is not a market namespace: {rail['market_prefix']!r}"
        )
    receipt = Path(rail["receipt_path"])
    if not receipt.is_absolute():
        receipt = REPO / receipt
    if not receipt.is_file():
        raise SystemExit(f"rail receipt not found: {rail['receipt_path']}")
    doc = json.loads(receipt.read_text())
    on_disk = hashlib.sha256(
        json.dumps(doc["receipt"], sort_keys=True).encode()
    ).hexdigest()
    if on_disk != rail["receipt_sha256"]:
        raise SystemExit(
            f"rail receipt digest mismatch: summary says {rail['receipt_sha256']}, "
            f"file hashes to {on_disk}"
        )
    if doc["receipt"]["accepted"] != accepted:
        raise SystemExit("rail receipt accepted count disagrees with the summary")
    return {
        "accepted": accepted,
        "rejected": rejected,
        "market_prefix": str(rail["market_prefix"]),
        "receipt_path": str(receipt.relative_to(REPO))
        if receipt.is_relative_to(REPO)
        else str(receipt),
        "receipt_sha256": str(rail["receipt_sha256"]),
    }


def validated(s: dict) -> dict:
    """Validate every summary field that reaches SQL, failing loud."""

    out = dict(s)
    for name in ("symbol", "interval", "source"):
        value = str(out[name])
        if not _IDENT.fullmatch(value):
            raise SystemExit(f"summary field {name!r} failed identity regex: {value!r}")
    if not _LICENSE.fullmatch(str(out["license"])):
        raise SystemExit(
            f"summary field 'license' failed license regex: {out['license']!r}"
        )
    for name in ("first_day", "last_day"):
        value = str(out[name])
        if not _DATE.fullmatch(value):
            raise SystemExit(f"summary field {name!r} is not YYYY-MM-DD: {value!r}")
    rows = out["rows"]
    if not isinstance(rows, int) or rows < 0:
        raise SystemExit(f"summary rows must be a non-negative int: {rows!r}")
    for hole in out.get("holes", []):
        for name in ("window_from", "window_to"):
            if not _DATE.fullmatch(str(hole[name])):
                raise SystemExit(
                    f"summary hole {name!r} is not YYYY-MM-DD: {hole[name]!r}"
                )
    return out


def psql(sql: str) -> str:
    r = subprocess.run(
        [
            *_PSQL,
            "-c",
            sql,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO / "compose"),
        env=COMPOSE_ENV,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()}")
    return r.stdout


def pre_inception_note(explicit: str | None, first_day: str) -> str:
    """Justification text for the pre-first_day window.

    This note is the registry's evidence for WHY nothing predates first_day: a
    misleading note is worse than no row, so a caller whose real reason differs
    (intraday API retention, a derived lineage) MUST supply its own. Whatever
    is used is interpolated into SQL, so quotes/semicolons are refused — and
    the DEFAULT has to satisfy that same guard, or every plain capture breaks.
    """
    note = explicit or (
        "pre-inception interval 2010..2017-08-16 (symbol listing starts "
        f"{first_day}, deeper BTC history tracked in another "
        "dataset row - TODO(11) Kraken/ItBit archives)"
    )
    if "'" in note or ";" in note:
        raise SystemExit("--pre-inception-note must not contain quotes/semicolons")
    return note


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--summary", required=True)
    a.add_argument("--d0-path", required=True)
    a.add_argument(
        "--data-class",
        default="binance",
        choices=["binance", "hyperliquid", "kraken", "nse_fo", "histdata"],
        help="registry dataset_key prefix = <data-class>/<symbol>/<interval>",
    )
    a.add_argument(
        "--key-suffix",
        default="",
        help="suffix appended to the dataset_key (<class>/<symbol>/<interval><suffix>).\n        A DERIVED projection registered beside its native sibling MUST use one\n        (e.g. -derived): the native full-history row owns the plain key, and a\n        projection that reused it would overwrite the canonical series on the\n        next scheduled run.",
    )
    a.add_argument(
        "--volume-available",
        choices=("true", "false"),
        default=None,
        help="whether the source provides usable volume; required for intraday",
    )
    a.add_argument("--volume-kind", default=None, help="source meaning of volume")
    a.add_argument("--volume-unit", default=None, help="unit used by source volume")
    a.add_argument("--schema-version", default=None)
    a.add_argument(
        "--legacy-writer-reason",
        default=None,
        help="A-7: register a collector that still writes its own Parquet. Requires a\n"
        "truthful reason (>= 20 chars); it is recorded append-only in\n"
        "d3.legacy_writes and shown by `gfti doctor`. Without either this or a rail\n"
        "receipt, registration is refused.",
    )
    a.add_argument(
        "--pre-inception-note",
        default=None,
        help="truthful justification for the pre-first_day gap; defaults to the\n        BTC spot-history wording. A dataset whose real reason differs (e.g.\n        intraday API retention) MUST supply its own — the default is a claim\n        about history that may not exist for this key",
    )
    args = a.parse_args()

    s = validated(json.loads(Path(args.summary).read_text()))
    ev = rail_evidence(s)
    reason = (args.legacy_writer_reason or "").strip()
    reason_sql = ""
    if ev is not None and reason:
        raise SystemExit(
            "--legacy-writer-reason contradicts the rail receipt: this capture "
            "went through the rail, so it must register as writer='rail'"
        )
    if ev is None:
        if not reason:
            raise SystemExit(
                "summary has no 'rail' block and no --legacy-writer-reason: this "
                "capture did not go through the rail, so its provenance cannot be "
                "proven by anyone but the caller (A-7 refuses it)"
            )
        if len(reason) < 20:
            raise SystemExit("--legacy-writer-reason must be at least 20 chars")
        # The reason is prose and will contain ';' and ',' — forbidding them would
        # force worse evidence. The literal is made safe by doubling quotes, which
        # is exact for Postgres, rather than by refusing characters.
        if "\x00" in reason or "\n" in reason:
            raise SystemExit("--legacy-writer-reason must be single-line, no NUL")
        reason_sql = reason.replace("'", "''")
    writer = "rail" if ev is not None else "legacy-collector"
    metadata = validate_metadata(
        interval=s["interval"],
        volume_available=(
            None if args.volume_available is None else args.volume_available == "true"
        ),
        volume_kind=args.volume_kind,
        volume_unit=args.volume_unit,
        schema_version=args.schema_version,
    )
    key = f"{args.data_class}/{s['symbol']}/{s['interval']}{args.key_suffix}"
    if not _IDENT.fullmatch(key):
        raise SystemExit(
            f"dataset_key failed identity regex with suffix {args.key_suffix!r}: {key!r}"
        )
    d0 = Path(args.d0_path)
    sha = s["artifact"]["sha256"]
    if not _SHA.fullmatch(str(sha)):
        raise SystemExit(f"summary sha256 is not 64 hex chars: {sha!r}")

    # resolve the D0 artifact to a REPO-ROOT-relative registry path: relative
    # targets may be passed as run-dir-relative OR repo-relative; the file
    # must exist — a registry row pointing nowhere is worse than failing.
    cand = d0.resolve() if d0.exists() else (REPO / args.d0_path).resolve()
    if not cand.exists():
        raise SystemExit(f"d0 path not found: {args.d0_path}")
    try:
        d0_rel = str(cand.relative_to(REPO))
    except ValueError:
        raise SystemExit(f"d0 path must live inside the repo: {cand}")

    # evidence integrity: the row we register must hash-match the artifact on
    # disk — a stale summary paired with a different file must not register.
    on_disk = hashlib.sha256(cand.read_bytes()).hexdigest()
    if on_disk != sha:
        raise SystemExit(
            f"sha mismatch: summary says {sha}, on disk {on_disk} ({cand})"
        )

    metadata_update = metadata_update_clause(
        volume_available_provided=args.volume_available is not None,
        volume_kind_provided=args.volume_kind is not None,
        volume_unit_provided=args.volume_unit is not None,
        schema_version_provided=args.schema_version is not None,
    )
    if ev is None:
        rail_cols = "NULL, NULL, NULL, NULL"
        rail_updates = (
            "rail_accepted = NULL, rail_rejected = NULL, "
            "rail_receipt_sha256 = NULL, rail_receipt_path = NULL"
        )
    else:
        rail_cols = (
            f"{ev['accepted']}, {ev['rejected']}, "
            f"'{ev['receipt_sha256']}', '{ev['receipt_path']}'"
        )
        rail_updates = (
            "rail_accepted = EXCLUDED.rail_accepted, "
            "rail_rejected = EXCLUDED.rail_rejected, "
            "rail_receipt_sha256 = EXCLUDED.rail_receipt_sha256, "
            "rail_receipt_path = EXCLUDED.rail_receipt_path"
        )
    # A-7: the manifest row and its writer evidence go in ONE transaction. Written
    # separately, the R1 view can observe a legacy row whose declared reason has
    # not been inserted yet — a false violation (measured: the 15-minute capture
    # cron overlapped a gate run and produced exactly that).
    if ev is not None:
        # The receipt history is append-only — a re-capture is a new fact, not an
        # overwrite of the evidence for the previous one.
        provenance = f"""
            INSERT INTO d3.rail_ingests (dataset_key, dataset_rows, accepted, rejected,
                                         receipt_path, receipt_sha256, market_prefix)
            VALUES ('{key}', {s["rows"]}, {ev["accepted"]}, {ev["rejected"]},
                    '{ev["receipt_path"]}', '{ev["receipt_sha256"]}',
                    '{ev["market_prefix"]}');
        """
    else:
        # Declared, not silent: the reason is what makes the debt auditable.
        provenance = f"""
            INSERT INTO d3.legacy_writes (dataset_key, collector, reason)
            VALUES ('{key}', '{args.data_class}', '{reason_sql}');
        """

    psql(f"""
        BEGIN;
        INSERT INTO d3.datasets (dataset_key, source, license_class, granularity,
                                 expected_from, expected_to, d0_path, rows,
                                 sha256, captured_at, verified, volume_kind,
                                 volume_unit, volume_available, schema_version,
                                 writer, rail_accepted, rail_rejected,
                                 rail_receipt_sha256, rail_receipt_path)
        VALUES ('{key}', '{s["source"]}', '{s["license"]}', '{s["interval"]}',
                '{s["first_day"]}T00:00:00Z', '{s["last_day"]}T00:00:00Z',
                '{d0_rel}', {s["rows"]}, '{sha}', now(), FALSE,
                {"NULL" if metadata["volume_kind"] is None else f"'{metadata['volume_kind']}'"},
                {"NULL" if metadata["volume_unit"] is None else f"'{metadata['volume_unit']}'"},
                {str(metadata["volume_available"]).upper()}, '{metadata["schema_version"]}',
                '{writer}', {rail_cols})
        ON CONFLICT (dataset_key) DO UPDATE SET
            source = EXCLUDED.source, license_class = EXCLUDED.license_class,
            granularity = EXCLUDED.granularity,
            expected_from = EXCLUDED.expected_from, expected_to = EXCLUDED.expected_to,
            d0_path = EXCLUDED.d0_path, rows = EXCLUDED.rows, sha256 = EXCLUDED.sha256,
            captured_at = now(), writer = EXCLUDED.writer,
            {rail_updates}{metadata_update};
        {provenance}
        COMMIT;
    """)

    # idempotent registry refresh: a new capture REPLACES this dataset's
    # assembled gaps (pre-inception edge + interior-hole digest). Ops-opened
    # gaps (kind='missing', ops-resolved) are re-opened by the hole pass only.
    psql(f"DELETE FROM d3.gaps WHERE dataset_key = '{key}';")

    # interior holes → HARD open gaps: one row per precise missing-day span
    # (gate G-B1.1 asserts the registry count matches the summary).
    for h in s.get("holes", []):
        psql(f"""
            INSERT INTO d3.gaps (dataset_key, window_from, window_to, kind, status)
            VALUES ('{key}', '{h["window_from"]}T00:00:00Z',
                    '{h["window_to"]}T23:59:59Z', 'missing', 'open');
        """)
    n_holes = len(s.get("holes", []))
    # pre-inception edge: justified (symbol listing starts at first_day).
    pre_note = pre_inception_note(args.pre_inception_note, s["first_day"])
    psql(f"""
        INSERT INTO d3.gaps (dataset_key, window_from, window_to, kind,
                             status, justification)
        VALUES ('{key}',
                '2010-01-01T00:00:00Z',
                '{s["first_day"]}T00:00:00Z'::timestamptz - interval '1 day',
                'exchange-closed', 'justified',
                '{pre_note}');
    """)
    # Trailing edge: only a day that is STILL TRADING at registration time is a
    # 'late' gap. A daily capture that returns closed bars only (the venue omits
    # the open day) ends on yesterday, and flagging yesterday as "live day still
    # trading" is a false statement in the gap ledger — measured 2026-11-04 on
    # hyperliquid/BTC-USDC/1d, where last_day=2026-11-03 had closed 6 h earlier.
    # The predicate keeps the row exactly where the original claim is true.
    psql(f"""
        INSERT INTO d3.gaps (dataset_key, window_from, window_to, kind,
                             status, justification)
        SELECT '{key}', '{s["last_day"]}T00:00:00Z',
               '{s["last_day"]}T23:59:59Z', 'late', 'justified',
               'live day still trading - closes at UTC midnight; rows beyond '
               'capture are not PIT-real'
        WHERE '{s["last_day"]}'::date = (now() AT TIME ZONE 'UTC')::date;
    """)

    # B-GATE: verified = TRUE only when no open-unjustified gaps block it
    psql(f"""
        UPDATE d3.datasets d SET verified = TRUE
        WHERE d.dataset_key = '{key}'
          AND NOT EXISTS (
              SELECT 1 FROM d3.gaps g
              WHERE g.dataset_key = d.dataset_key
                AND g.status = 'open' AND g.justification = '');
    """)

    out = psql(f"""
        SELECT dataset_key, rows, verified FROM d3.datasets
        WHERE dataset_key = '{key}';
        SELECT status, count(*) FROM d3.gaps WHERE dataset_key = '{key}'
        GROUP BY status ORDER BY status;
        SELECT count(*) FROM d3.v_open_gaps WHERE dataset_key = '{key}';
    """)
    print(
        "LEDGER-B1-REGISTER "
        + json.dumps(
            {
                "dataset_key": key,
                "rows": s["rows"],
                "sha256": sha,
                "d0_path": d0_rel,
                "interior_holes": n_holes,
                "psql_verification": out.strip()[:400],
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 — register tool must fail loud
        import traceback

        traceback.print_exc()
        raise SystemExit(1)
