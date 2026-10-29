# W0a gate document — foundation wave

Wave owner: owner-approved plan (FULL-SYSTEM-BLUEPRINT §13) · Repo: this repo.
**A wave is DONE only when every pin below holds real, filed output.** This document
is the file. Each check gets its own subsection; results are pasted verbatim from
the commands, with the command itself, in the order they ran. Nothing here is prose.

## Pin 1 — Compose foundation stack (Postgres D3/D4 · QuestDB D1 · NATS D2)

Command(s): `docker compose up -d` + `docker compose ps` + per-service health probe
(`pg_isready` · QuestDB `/exec` 200 · NATS `/healthz?js_enabled_only=true` 200).

Result: **BLOCKED — 2026-10-29: host Docker daemon inactive; `sudo systemctl start
docker` requires interactive authentication.** Owner action agreed: start the
daemon, then this pin is re-run and its real output is pasted here. No synthetic
output will be written.

## Pin 2 — 5 D1 store gates (burst · determinism · pre-warm · restore · 10× restart)

Not started. Runs against the Compose stack of pin 1. Each gate = a small harness
printing its own pass/fail JSON into this file.

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

## Non-docker follow-ups queued this session

- vault note types (decision · incident · runbook · research-digest · training-record)
- Graphify baseline graph build (`--update`)
- `docs/gates/` backfill for W0a evidence links (this file + raw JSON outputs)
