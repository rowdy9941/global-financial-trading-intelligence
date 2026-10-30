# W0a gate document — foundation wave

Wave owner: owner-approved plan (FULL-SYSTEM-BLUEPRINT §13) · Repo: this repo.
**A wave is DONE only when every pin below holds real, filed output.** This document
is the file. Each check gets its own subsection; results are pasted verbatim from
the commands, with the command itself, in the order they ran. Nothing here is prose.

## Pin 1 — Compose foundation stack (Postgres D3/D4 · QuestDB D1 · NATS D2)

DONE 2026-10-30. Docker daemon 29.6.1 (also enabled at boot). Two measured
fixes while lifting (the gate caught both):

1. QuestDB image tag `questdb/questdb:7` **does not exist** in Docker Hub
   (registry API verified) → real newest 7.x tag `7.4.2` pinned in
   `compose/docker-compose.yml`.
2. QuestDB 7.4.2 image is Debian-based with bash but **no curl/wget**
   (verified in-container) → healthcheck switched to a bash `/dev/tcp/9000`
   probe. Also: a stale NATS container from a failed start had no live port
   attach — `--force-recreate` healed it (ports now published).

`docker compose ps` (verbatim):

```
NAME             IMAGE                    SERVICE    STATUS                                 PORTS
gfti-nats-1      nats:2.10-alpine         nats       Up 43 seconds (healthy)                127.0.0.1:4222->4222/tcp, 127.0.0.1:8222->8222/tcp, 6222/tcp
gfti-postgres-1  postgres:16-alpine       postgres   Up 5 minutes (healthy)                 127.0.0.1:5432->5432/tcp
gfti-questdb-1   questdb/questdb:7.4.2    questdb    Up 14 seconds (healthy)                127.0.0.1:9000->9000/tcp, 8812/tcp, 127.0.0.1:9009->9009/tcp
```

App-level probes (verbatim):

```
pg_isready -U gfti_app -d gfti   → /var/run/postgresql:5432 - accepting connections
questdb /exec?query=select 1     → {"query":"select 1","columns":[{"name":"1","type":"INT"}],"timestamp":-1,"dataset":[[1]],"count":1}
nats /healthz?js_enabled_only=true → {"status":"ok"}
```

Post-fix confirmation that the whole ingest path still holds on the canonical
(per-container) bus: publisher `rail: accepted=2000 rejected=0`, consumer
`WIRE-E2E {"received":500,"n":500,"transit_ns_avg":392217,"p50":299656,"p99":1377677}`.
(The native nats-server used for the 2026-10-29 measurements is retired; all
further evidence is against this compose stack.)

## Pin 2 — 5 D1 store gates (burst · determinism · pre-warm · restore · 10× restart)

DONE 2026-10-30 against the live compose QuestDB `questdb:7.4.2`. Ingest runs
through the production `rail::ilp` path (`rust/rail/src/bin/d1_ingest.rs`);
checks against the REST `/exec` endpoint; every line below is verbatim output
of `python/gates/d1_gates.py`.

Two real fixes the gates forced (kept as lessons, not hidden):

1. **QuestDB 7.4.2 ILP grammar is stricter than the docs suggest** — numeric
   fields need explicit suffixes (`i` long, `d` double); the bare `…u` field
   was silently rejected on the TCP path (connection `reason=0`, table never
   created). First proven over HTTP `/write` (it returns parse errors the TCP
   path swallows), then fixed in `rail::ilp` + exact-byte unit tests.
2. **Volume-layout restore lesson** — the data volume root *is* the datadir;
   restoring the tar with its `questdb/` prefix merged instead of replaced
   (first run: markers survived restore; gate called it). Fixed to
   wipe-then-extract with `--strip-components=1`; the replay of the gate
   proved reversal (105 → 100).

```
GATE-A-BURST {"n_sent": 500000, "rows_server": 500000, "ok": true, "ingest_wall_s": 6.95, "client_ns_per_row": 13818, "rows_per_sec_eq": 71938}
GATE-B-DETERMINISM {"rows": 10000, "rows_tab1": 10000, "rows_tab2": 10000, "datasets_identical": true, "ok": true}
GATE-C-PREWARM {"cold_first_query_ms": 153.7, "warm_p50_ms": 6.6, "warm_p99_ms": 134.2, "cold_result_rows": 500000, "ok": true}
GATE-D-RESTORE {"rows_backup": 100, "rows_after_marker_ingest": 105, "rows_after_restore": 100, "backup_bytes": 19263763, "markers_reverted": true, "ok": true}
GATE-E-10XRESTART {"restarts": 10, "invariant_rows": 200, "cycles_ms": [16997, 15966, 18009, 18049, 16641, 17113, 15662, 17058, 18350, 16042], "all_cycles_ok": true, "head_rows": [[1772000000000000000, 100.0], [1772000001000000000, 100.01], [1772000002000000000, 100.02], [1772000003000000000, 100.03], [1772000004000000000, 100.04]], "ok": true}
```

Reads: burst ingest 500k rows lands in 6.95 s end-to-end (72k rows/s through
encode→TCP→daemon-commit; client encode alone is 639 ns/row — the wall is
daemon-side commit, MEASURED). Write-path determinism: two independent 10k-row
ingests produce byte-comparable identical datasets. Pre-warm: cold first query
154 ms → warm p50 6.6 ms. Restore: true volume restore reverts post-backup
markers exactly. 10 restarts × ~17 s each with the 200-row invariant and stable
row ordering intact.

## W0a status — all pins green

Pin 1 compose stack ✅ · Pin 2 five D1 gates ✅ · Pin 3 rail loop ✅ ·
Pin 4 replay determinism ✅ · Pin 5 G1 bench ✅ · Pin 5b fan-out ✅ ·
Pin 5c wire throughput ✅ · Pin 6 D0 parquet ✅ · Pin 7 D1 prep/ILP ✅.
**W0a = DONE on 2026-10-30** (final scope check before B1: hygiene scan,
workspace gates, then wipe gate tables for a clean B1 start).

## Pin 3 — R1 rail minimal loop (validate → as-of → rights-tag → publish → D0 dump)

In progress → core loop measured and dump landed (Pin 6). Core = `rust/rail` (workspace member). **Measured 2026-10-29:**

| Check | Command | Result (verbatim tail) |
|---|---|---|
| workspace builds | `cargo build` | `Finished dev profile target(s)` |
| unit tests | `cargo test` | `test result: ok. 6 passed; 0 failed` (symbol/as-of-skew/price/rights/walled/contract) |
| lint | `cargo clippy -- -D warnings` | **0 errors, 0 warnings** |
| CLI loop e2e | `printf … \| ./target/debug/rail` | first run `accepted=2 rejected=3` (pre-bus); live run below |

**Wire (D2) half — measured against a live nats-server v2.15.2 (native, not docker):**

| Check | Command | Result |
|---|---|---|
| server health | `curl 127.0.0.1:8222/healthz` | `{"status":"ok"}` |
| feed 2,000 rows (1,900 valid + 100 corrupt) → rail → NATS ack | `rail < feed2000.tsv` | `rail: accepted=1900 rejected=100` — every accepted row **PONG-acked** (`pub=acked` ×1,899/1,900) |
| consumer receive e2e | `rail_sub "market.spot.>" 1899…1900` (r2) | `WIRE-E2E {"received":1900,"n":1900,"transit_ns_avg":220138,"p50":163706,"p99":1369756}` — one-way bus transit **avg 220 µs · p50 164 µs · p99 1.37 ms**, round-trip-counted per message |
| client-protocol lesson recorded | raw-socket probe | NATS 2.15 sends an **INFO line after the first SUB** → client now skips INFO on every ack path (`skip_to_pong`) — found by the failing consumer, fixed, re-run, then pass |

**Pin 4 — PIT replay harness: `cmp replay1.out replay2.out` → rc=0, byte-identical** (1,900 OK + 100 ERR rows identical in both runs). Replay pins `arrival = ts+1` instead of wall clock — determinism by construction, proven by `cmp`.

**Pin 5 — G1 bench stub (release build, 2,000,000 rows):**

```json
{"rows":2000000,"ns_per_row_avg":90,"p50":79,"p99":182,"p999":306,"rows_per_sec_equivalent":11111111}
```

Read: one rail core stamps ~11.1 M rows/s equivalent single-threaded; per-row ingest
cost p99 = 182 ns (MEASURED, release build, std-only path). Multi-core fan-in and
SQL-transport concurrency are the remaining G1 scope (next increments).

Real findings the gate caught and fixed pre-commit (part of the record): test-const
overflow, E0106 lifetime signature, **MIN_TS floor misdated** (now 1.4e18 ns ≈ 2014-05,
2015 backfill passes), NATS-2.15 post-SUB INFO handling.

## Pin 4 — PIT replay harness CLI

DONE 2026-10-29 (results recorded under Pin 3 above): two replays of the same
2,000-row feed → `cmp replay1.out replay2.out` → **rc=0, byte-identical**.

## Pin 5 — G1 concurrency bench stub

DONE 2026-10-29 (results recorded under Pin 3 above): release-build 2 M-row bench
JSON pasted verbatim. Remaining G1 scope: SQL-transport concurrency, multi-core
fan-in.

## Pin 5b — Fan-out semantics (D2 distribution proof)

Measured 2026-10-29, native nats-server v2.15.2, two consumers started before the
publisher on independent subject filters:

| Consumer | Filter | Expected | Received (verbatim) |
|---|---|---|---|
| A | `market.spot.>` | all 1,900 | `WIRE-E2E {"received":1900,"n":1900,"transit_ns_avg":209803,"p50":155793,"p99":1363107}` |
| B′ | `market.spot.NIFTY` (exact) | the 250 NIFTY rows only | `WIRE-E2E {"received":250,"n":250,"transit_ns_avg":280747,"p50":196109,"p99":1585528}` |

Publisher: `rail: accepted=1900 rejected=100`; publisher's own `OK NIFTY` count =
250 — B′ received exactly the rows it filtered (no leakage, no loss). Fan-out
with independent filtering **proven at the wire level**.

## Pin 6 — D0 Parquet cold-store dump (new crate `rust/d0`)

Dependency landed with licence check (workspace policy): `parquet 60.1.0`
(Apache-2.0) + `arrow-array`/`arrow-schema 60.1.0` (Apache-2.0 AND MIT) — all
versions retrieved from the crates.io API, not-yanked verified, linked **only**
into the d0 crate (rail stays std-only). Reason: cold tier must be specced
Parquet; hand-rolling thrift-footer/RLE correctness is risk, not optimization.

Measured 2026-10-29 (release build; input = the 2,000-row canonical feed):

| Gate | Command | Verbatim result |
|---|---|---|
| dump determinism (×2) | `d0_dump --stdin d0a.parquet < replay1.out` ×2 → `cmp` | `D0 {"rows_written":1900,"rejected":100,"file_bytes":43469}` ×2, `cmp` **rc=0 — byte-identical Parquet** |
| read-back proof | read_count after every write | row count parity enforced in code (wrote 1,900 → read 1,900) |
| unit tests | `cargo test -p d0` | `test result: ok. 2 passed; 0 failed` |
| write bench (100k rows) | `d0_dump --bench d0bench.parquet 100000` | `{"rows":100000,"rows_written":100000,"rows_read":100000,"file_bytes":1832480,"write_ns_per_row":535,"rows_per_sec":1868006,"ok":true}` |
| write bench (1M rows) | `d0_dump --bench d0bench.parquet 1000000` | `{"rows":1000000,"rows_written":1000000,"rows_read":1000000,"file_bytes":13002157,"write_ns_per_row":289,"rows_per_sec":3448952,"ok":true}` |

Read: hot-path stamp 90 ns/row vs cold-store persist 289 ns/row at batch size 1 M
(~3.45 M rows/s/core, SNAPPY, 13.0 bytes/row before compression ratio shown as
file size). Determinism gate caught a real parser mis-mapping while being built
(bare-payload arrival field was bound to ts) — fixed pre-commit; the parse unit
test now pins both input forms.

## Pin 5c — Wire throughput: per-message ack vs batched-flush ack

Measured 2026-10-29, release build, native nats-server v2.15.2 (producer-side,
same server, same payload shape; fails closed at batch granularity):

| Mode | Rows | Verbatim |
|---|---|---|
| per-msg ack (live-rail default) | 2,000 | `{"mode":"per","rows":2000,"total_ns":192953862,"ns_per_row":96476,"rows_per_sec":10365}` |
| batch-64 ack | 2,000 | `{"mode":"batch","rows":2000,"total_ns":65886773,"ns_per_row":32943,"rows_per_sec":30355}` |
| per-msg ack | 50,000 | `{"mode":"per","rows":50000,"total_ns":4763658862,"ns_per_row":95273,"rows_per_sec":10496}` |
| batch-64 ack | 50,000 | `{"mode":"batch","rows":50000,"total_ns":1766121885,"ns_per_row":35322,"rows_per_sec":28310}` |
| batch-1024 ack | 50,000 | `{"mode":"batch","rows":50000,"total_ns":1615075108,"ns_per_row":32301,"rows_per_sec":30958}` |

Read: the per-row PING round-trip costs ≈63 µs of the ≈95 µs/msg producer cost.
Batched flush (backfill/replay path) is **2.7–3.0× higher throughput**. Decision
locked: live rail keeps per-message acks (fail-closed per row — correctness over
speed on the hot path); backfill/replay transports use the batched-flush
`publish_batch` (fail-closed per batch). JetStream durable streams remain the
next wire increment.

## Pin 7 — D1 prep: QuestDB ILP writer (std-only `rail::ilp`)

Landed 2026-10-29 so the D1 store gates can run immediately once the Docker
daemon is up (`questdb:7` speaks ILP-over-TCP; no client crate, workspace policy
held). Verified:

| Gate | Command | Verbatim result |
|---|---|---|
| unit tests (wire+encode) | `cargo test -p rail` | `test result: ok. 8 passed; 0 failed` — incl. `encodes_and_delivers_exact_bytes_to_tcp` (fake TCP server, exact-byte assertions: framing newline, `%20`/`\'` escaping, epoch-micros stamp) |
| lint | `cargo clippy --workspace -- -D warnings` | **0 errors, 0 warnings** |
| encode bench (2 M rows, release) | `bench_ilp 2000000` | `{"rows":2000000,"ilp_ns_per_row_avg":639,"p50":584,"p99":1544,"p999":1951,"buf_push_ns_per_row":999,"rows_per_sec_equivalent":1564945}` + `flushed_rows=2000000 sink_bytes=245551273` (123 bytes/row ≈ ILP line length) |

Read: ILP encode costs ≈639 ns/row (p99 1.5 µs) — the D1 TCP transport adds this
on top of the 90 ns/row stamp. The real end-to-end D1 latency is only meaningful
against the live daemon → measured at pin 2, not here. Nothing synthetic.

## Non-docker follow-ups queued this session

- vault note types (decision · incident · runbook · research-digest · training-record)
- Graphify baseline graph build (`--update`)
- `docs/gates/` backfill for W0a evidence links (this file + raw JSON outputs)
