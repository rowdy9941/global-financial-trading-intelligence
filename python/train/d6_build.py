#!/usr/bin/env python3
"""D6 — the research cache, materialized by one process and read read-only
(A2 A-9 item 32).

What D6 is *for* (blueprint §2.1): vectorized research scans over D0 Parquet. What
it must never be: a critical path, or a directory of `.duckdb` files that half the
tools write to. Before this module D6 was "a DuckDB call somewhere inside
`walk_forward.py`" — every consumer invented its own connection, its own SQL, and
its own answer to "which parquet files count as datasets?".

So D6 is now a product, not an idiom:

  * **One builder.** `gfti research build` runs this file. It is the only writer.
  * **Atomic replace.** The new database is built at a temporary path and renamed
    over the old one, so a reader either sees the previous complete build or the
    new complete build — never a half-copied file.
  * **One writer at a time.** A lock file with an owner pid; a second build refuses
    instead of racing.
  * **Read-only by construction.** Builders open the file for writing and close it;
    every reader (Mode R, E4 replays) opens with `read_only=True`, which DuckDB
    enforces at the file level.
  * **Lineage.** Every table records the D0 path it came from, its row count and
    sha256, so a research result can be traced to exact bytes. That is the same
    rule the D3 registry applies to Parquet (A-7's lineage).

The table set is derived from the lake, not hardcoded: adding a capture to `d0/`
and re-running the build is the whole procedure for adding a dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

# Table-name rules: DuckDB identifiers are quoted on the way in, but a name that is
# a hash of the path is unreadable and one that keeps punctuation needs quoting
# everywhere. So the name is the path with `/` `-` `.` folded to `_`.
FOLD = str.maketrans({"/": "_", "-": "_", ".": "_", " ": "_"})

META_TABLE = "d6_datasets"


def table_name(rel: str) -> str:
    """`hyperliquid/BTC-USDC/1d.parquet` → `d0_hyperliquid_BTC_USDC_1d`."""
    stem = rel.removesuffix(".parquet")
    return ("d0_" + stem.translate(FOLD)).lower()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover(d0: Path) -> list:
    """Every parquet under D0, sorted so two builds see the same order.

    A `.corrupt` sibling is ignored by the extension check — those exist on purpose
    (a truncated capture is evidence, not data) and must never become a table.
    """
    found = []
    for path in sorted(d0.rglob("*.parquet")):
        if not path.is_file():
            continue
        rel = path.relative_to(d0).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        found.append((rel, path))
    return found


class Lock:
    """One builder at a time. A stale lock (dead pid or older than an hour) is taken
    over *loudly* — a lockfile that nothing can clear is a broken build, not a safe one.
    """

    def __init__(self, path: Path):
        self.path = path
        self.held = False

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (1, 2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if attempt == 2:
                    raise SystemExit(
                        f"d6: another build holds {self.path.name} ({self.read() or 'unknown'})"
                    )
                if not self.stale():
                    raise SystemExit(
                        f"d6: another build holds {self.path.name} ({self.read() or 'unknown'})"
                    )
                print(
                    f"d6: WARNING taking over a stale lock {self.path.name} "
                    f"({self.read() or 'unknown'})",
                    file=sys.stderr,
                )
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(f"pid={os.getpid()} at={int(time.time())}")
            self.held = True
            return self
        raise SystemExit("d6: could not acquire the build lock")

    def read(self) -> str:
        try:
            return self.path.read_text().strip()
        except OSError:
            return ""

    def stale(self) -> bool:
        text = self.read()
        pid = age = None
        for part in text.split():
            if part.startswith("pid="):
                pid = int(part[4:] or 0)
            elif part.startswith("at="):
                age = time.time() - int(part[3:] or 0)
        if age is not None and age > 3600:
            return True
        if pid:
            try:
                os.kill(pid, 0)  # signal 0 = "does this process exist"
            except PermissionError:
                return False  # alive, not ours
            except OSError:
                return True  # dead owner
        return False

    def release(self):
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False


def build(d0: Path, out: Path, only: list | None = None, do_hash: bool = True) -> dict:
    import duckdb

    datasets = discover(d0)
    if only:
        wanted = set(only)
        datasets = [d for d in datasets if d[0] in wanted or table_name(d[0]) in wanted]
    if not datasets:
        raise SystemExit(f"d6: no parquet datasets under {d0}")

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    for leftover in out.parent.glob(f".{out.name}.*.tmp"):
        leftover.unlink(missing_ok=True)
    tmp.unlink(missing_ok=True)

    built_at = int(time.time())
    rows = {}
    con = duckdb.connect(str(tmp))
    try:
        con.execute(
            f"CREATE TABLE {META_TABLE} (dataset VARCHAR, table_name VARCHAR, "
            "src_path VARCHAR, rows BIGINT, sha256 VARCHAR, built_at BIGINT)"
        )
        for rel, path in datasets:
            name = table_name(rel)
            # Parameterised path: a capture directory with a quote in its name must
            # not be able to change this statement.
            con.execute(
                f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM read_parquet(?)',
                [str(path)],
            )
            n = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            rows[name] = n
            con.execute(
                f"INSERT INTO {META_TABLE} VALUES (?, ?, ?, ?, ?, ?)",
                [rel, name, str(path), n, sha256(path) if do_hash else None, built_at],
            )
        con.execute("CHECKPOINT")
    finally:
        con.close()

    # Verify what was written, from a *read-only* connection: the same mode every
    # consumer uses. A build that only its own writer can read is not a build.
    check = duckdb.connect(str(tmp), read_only=True)
    try:
        for name, n in rows.items():
            got = check.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            if got != n:
                raise SystemExit(f"d6: {name} wrote {n} rows but reads {got}")
        listed = check.execute(f"SELECT count(*) FROM {META_TABLE}").fetchone()[0]
    finally:
        check.close()
    if listed != len(rows):
        raise SystemExit(f"d6: {META_TABLE} holds {listed} rows for {len(rows)} tables")

    # Atomic replace: a reader holding the old file keeps a working file, a new
    # reader gets a complete new one, and neither can observe a partial copy.
    previous = out.stat().st_size if out.exists() else None
    os.replace(tmp, out)
    return {
        "ok": True,
        "out": str(out),
        "bytes": out.stat().st_size,
        "previous_bytes": previous,
        "datasets": len(rows),
        "rows": sum(rows.values()),
        "tables": sorted(rows),
        "built_at": built_at,
        "hashes": do_hash,
    }


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    repo = here.parent.parent
    p = argparse.ArgumentParser(description="build the D6 research cache from D0")
    p.add_argument("--d0", default=str(repo / "d0"))
    p.add_argument("--out", default=str(repo / "d6" / "gfti.duckdb"))
    p.add_argument(
        "--dataset",
        action="append",
        help="only this D0 path (repeatable): 'hyperliquid/BTC-USDC/1d.parquet'",
    )
    p.add_argument(
        "--no-hash",
        action="store_true",
        help="skip sha256 over the source parquet (fast, no lineage)",
    )
    args = p.parse_args(argv)

    out = Path(args.out)
    lock = Lock(out.with_name(out.name + ".lock"))
    lock.acquire()
    try:
        payload = build(
            Path(args.d0), out, only=args.dataset, do_hash=not args.no_hash
        )
    finally:
        lock.release()
    print("D6-BUILD " + json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
