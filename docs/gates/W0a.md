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
| workspace builds | `cargo build` | `Finished dev profile target(s) in 1.98s` |
| unit tests | `cargo test` | `test result: ok. 6 passed; 0 failed` (symbol/as-of-skew/price/rights/walled/contract) |
| lint | `cargo clippy -- -D warnings` | `Finished dev profile — 0 warnings` |
| CLI loop e2e | `printf … \| ./target/debug/rail` | `accepted=2 rejected=3` with reasons (FutureAsOf · Price · Symbol) + `OK`-stamped lines for NIFTY(public) & XAU_USD(walled) |

Real findings the gate caught and fixed pre-commit (part of the record): test-const
overflow, E0106 lifetime signature, and **MIN_TS floor misdated** (would have
rejected the 2015 backfill; now 1.4e18 ns ≈ 2014-05).

Scope of the skeleton (honest): the loop's **ingest contract** — row validation,
as-of stamping, rights-class tagging, and the CLI loop that stamps and fails closed
on clock failure. **The publish (`market.*` fan-out) lands after pin 1 unblocks**
(same commit, since it needs NATS); the D0 parquet dump is the following increment.

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
