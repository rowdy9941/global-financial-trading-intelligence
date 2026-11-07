"""D1 (QuestDB) as a *declared* store (A2 A-9 item 31).

Before this module `ticks` did not exist. D1 held nine `gate_*` tables that the
drills created and dropped, and the live table was going to be created — without
anyone saying so — by the first ILP row QuestDB happened to receive. QuestDB's
implicit typing is friendly and wrong for a store: the designated timestamp column
would be called `timestamp` instead of `ts`, the symbol capacity would be a guess,
and whether the table is WAL-enabled would depend on a server default that a
compose upgrade can change.

So the schema is stated here, once, and `gfti d1 apply` makes the server match it.
Same shape as `gfti d2 apply` (A-8): the store's definition is data a human can
read, and the verb is convergent, so re-running it is safe and a missing table is
created rather than assumed.

`status` is the other direction on purpose. `apply` says "the declaration was
applied"; `status` says "the server holds this". They are different facts, and D2
shipped for months with the first one and not the second.

Only the ILP-TCP *writer* is elsewhere (`rust/rail/src/ilp.rs`); this module never
writes rows.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

# table -> (ddl, columns it must have, (name, type) pairs).
#
# `ts` is the designated timestamp and the ILP writer frames it in nanoseconds —
# the unit the daemon actually uses. `ts_nanos`/`arrival_nanos` are kept as LONG
# columns so the rail's stamped pair survives bit-exact even where `ts` is a
# resolution-limited TIMESTAMP.
TABLES = {
    "ticks": {
        "ddl": (
            "CREATE TABLE IF NOT EXISTS ticks ("
            "symbol SYMBOL CAPACITY 4096 CACHE, "
            "ts_nanos LONG, "
            "arrival_nanos LONG, "
            "price DOUBLE, "
            "ts TIMESTAMP"
            ") timestamp(ts) PARTITION BY DAY WAL"
        ),
        # Declared shape. A column that appears or changes type is drift, and drift
        # is reported rather than "fixed" — silently re-typing a store that already
        # holds rows is a data-loss bug wearing a migration's clothes.
        "columns": [
            ("symbol", "SYMBOL"),
            ("ts_nanos", "LONG"),
            ("arrival_nanos", "LONG"),
            ("price", "DOUBLE"),
            ("ts", "TIMESTAMP"),
        ],
        "designated": "ts",
    },
}

# Timescale of the store's purpose (source: blueprint §2.1 "Live window only").
PURPOSE = "live window only (days-weeks) · written by R1 only · read by E1 pre-warm, PIT replay, dashboards"


class D1Error(RuntimeError):
    """A D1 problem the operator can act on; never a traceback."""


def _base(cfg) -> str:
    port = cfg["stores"]["questdb_http_port"]
    return f"http://127.0.0.1:{port}"


def _exec(base: str, sql: str):
    url = f"{base}/exec?query=" + urllib.parse.quote_plus(sql)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        raise D1Error(f"D1 rejected {sql[:60]!r}: {body}") from None
    except (urllib.error.URLError, OSError) as e:
        raise D1Error(f"D1 unreachable at {base}: {e}") from None
    except json.JSONDecodeError as e:
        raise D1Error(f"D1 returned non-JSON ({e})") from None


def _names(base: str) -> set:
    doc = _exec(base, "select table_name from tables()")
    return {row[0] for row in doc.get("dataset", [])}


def _shape(base: str, table: str) -> tuple:
    """(columns as [(name, type)], designated, partition, wal) for one table."""
    cols = [
        (row[0], row[1])
        for row in _exec(base, f"table_columns('{table}')").get("dataset", [])
    ]
    meta = _exec(
        base,
        "select designatedTimestamp, partitionBy, walEnabled from tables() "
        f"where table_name='{table}'",
    ).get("dataset", [])
    designated, partition, wal = (meta[0][0], meta[0][1], meta[0][2]) if meta else (None, None, None)
    return cols, designated, partition, wal


def apply(cfg) -> dict:
    """Create what is missing; report (never rewrite) what differs."""
    base = _base(cfg)
    existing = _names(base)
    created, present, problems = [], [], []
    for table, spec in TABLES.items():
        if table not in existing:
            _exec(base, spec["ddl"])
            created.append(table)
        else:
            present.append(table)
        cols, designated, _partition, _wal = _shape(base, table)
        have = {name: typ for name, typ in cols}
        want = dict(spec["columns"])
        missing = [name for name in want if name not in have]
        mistyped = [
            f"{name}: server {have[name]} != declared {want[name]}"
            for name in want
            if name in have and have[name] != want[name]
        ]
        extra = [name for name in have if name not in want]
        if designated != spec["designated"]:
            problems.append(
                f"{table}: designated timestamp is {designated!r}, declared {spec['designated']!r}"
            )
        if missing:
            problems.append(f"{table}: missing columns {missing}")
        if mistyped:
            problems.append(f"{table}: {', '.join(mistyped)}")
        if extra:
            problems.append(f"{table}: undeclared columns {extra}")
    return {
        "created": created,
        "present": present,
        "declared": sorted(TABLES),
        "problems": problems,
        "ok": not problems,
    }


def status(cfg) -> dict:
    """What the server holds, with row counts, and whether it matches the file."""
    base = _base(cfg)
    existing = _names(base)
    out = {"tables": {}, "problems": [], "purpose": PURPOSE}
    for table, spec in TABLES.items():
        if table not in existing:
            out["tables"][table] = {"exists": False}
            out["problems"].append(f"{table}: declared but absent (run `gfti d1 apply`)")
            continue
        cols, designated, partition, wal = _shape(base, table)
        rows = _exec(base, f"select count() from {table}").get("dataset", [[0]])[0][0]
        have = {name: typ for name, typ in cols}
        want = dict(spec["columns"])
        drift = {name: [have.get(name), want[name]] for name in want if have.get(name) != want[name]}
        if drift:
            out["problems"].append(f"{table}: drifted {drift}")
        if designated != spec["designated"]:
            out["problems"].append(
                f"{table}: designated timestamp {designated!r} (declared {spec['designated']!r})"
            )
        out["tables"][table] = {
            "exists": True,
            "rows": rows,
            "columns": cols,
            "designated": designated,
            "partition_by": partition,
            "wal": wal,
        }
    out["ok"] = not out["problems"]
    return out
