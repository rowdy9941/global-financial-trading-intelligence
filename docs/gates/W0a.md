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

In progress. Core = `rust/rail` (workspace member). **Measured 2026-10-29:**

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

Not started. Produces: two replays of the same input file → two output files →
`cmp` equality → line printed here.

## Pin 5 — G1 concurrency bench stub

Not started. Produces: bench JSON (headroom headcount per SQL transport) into this
file.

## Non-docker follow-ups queued this session

- vault note types (decision · incident · runbook · research-digest · training-record)
- Graphify baseline graph build (`--update`)
- `docs/gates/` backfill for W0a evidence links (this file + raw JSON outputs)
