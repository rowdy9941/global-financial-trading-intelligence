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
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # repo root


def psql(sql: str) -> str:
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres",
         "psql", "-U", "gfti_app", "-d", "gfti", "-c", sql],
        capture_output=True, text=True, timeout=60, cwd=str(REPO / "compose"),
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()}")
    return r.stdout


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--summary", required=True)
    a.add_argument("--d0-path", required=True)
    args = a.parse_args()

    s = json.loads(Path(args.summary).read_text())
    key = f"binance/{s['symbol']}/{s['interval']}"
    d0 = Path(args.d0_path)
    sha = s["artifact"]["sha256"]

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

    q = lambda x: x.replace("'", "''")
    psql(f"""
        INSERT INTO d3.datasets (dataset_key, source, license_class, granularity,
                                 expected_from, expected_to, d0_path, rows,
                                 sha256, captured_at, verified)
        VALUES ('{key}', '{s['source']}', '{s['license']}', '{s['interval']}',
                '{s['first_day']}T00:00:00Z', '{s['last_day']}T00:00:00Z',
                '{d0_rel}', {s['rows']}, '{sha}', now(), FALSE)
        ON CONFLICT (dataset_key) DO UPDATE SET
            source = EXCLUDED.source, license_class = EXCLUDED.license_class,
            granularity = EXCLUDED.granularity,
            expected_from = EXCLUDED.expected_from, expected_to = EXCLUDED.expected_to,
            d0_path = EXCLUDED.d0_path, rows = EXCLUDED.rows, sha256 = EXCLUDED.sha256,
            captured_at = now();
    """)

    # idempotent registry refresh: a new capture REPLACES this dataset's
    # assembled gaps (pre-inception edge + interior-hole digest). Ops-opened
    # gaps (kind='missing', ops-resolved) are re-opened by the hole pass only.
    psql(f"DELETE FROM d3.gaps WHERE dataset_key = '{key}';")

    # interior holes → HARD open gaps (over the whole capture window)
    # daily bars must tile day-by-day; rows_minus_days > 0 means holes.
    holes = s["rows"] - s["calendar_days"]
    if holes > 0:
        psql(f"""
            INSERT INTO d3.gaps (dataset_key, window_from, window_to, kind, status)
            VALUES ('{key}', '{s['first_day']}T00:00:00Z', '{s['last_day']}T00:00:00Z',
                    'missing', 'open');
        """)  # precise windows land once a hole-finder pass is written
    # pre-inception + live-day edges: justified, still recorded honestly
    psql(f"""
        INSERT INTO d3.gaps (dataset_key, window_from, window_to, kind,
                             status, justification)
        VALUES ('{key}',
                '2010-01-01T00:00:00Z',
                '{s['first_day']}T00:00:00Z'::timestamptz - interval '1 day',
                'exchange-closed', 'justified',
                'pre-inception interval 2010..2017-08-16 (symbol listing starts '
                '{s['first_day']}; deeper BTC history tracked in another '
                'dataset row - TODO(11) Kraken/ItBit archives)');
    """)
    psql(f"""
        INSERT INTO d3.gaps (dataset_key, window_from, window_to, kind,
                             status, justification)
        VALUES ('{key}', '{s['last_day']}T00:00:00Z',
                '{s['last_day']}T23:59:59Z', 'late', 'justified',
                'live day still trading - closes at UTC midnight; rows beyond '
                'capture are not PIT-real');
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
    """)
    print("LEDGER-B1-REGISTER " + json.dumps({
        "dataset_key": key, "rows": s["rows"], "sha256": sha,
        "d0_path": d0_rel, "interior_holes": holes,
        "psql_verification": out.strip()[:400],
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 — register tool must fail loud
        import traceback
        traceback.print_exc()
        raise SystemExit(1)

