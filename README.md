# Global financial trading intelligence

A from-scratch global trading intelligence ecosystem: one shared data foundation →
three parallel engines (financial · news · world) → a private trading stack
(**FinOS**) with governed deterministic agents — plus an LLM cockpit layer that
organizes, scores, monitors and talks with the operator. The public content engines
(Terminal · News portal · Media) are built last, rights-firewalled.

> The complete architecture and build plan lives in [`docs/plans/`](docs/plans/).
> Start here, in this order:
>
> 1. **[FINOS-TRADING-PLAN.pdf](docs/plans/FINOS-TRADING-PLAN.pdf)** — the visual edition: 10 architecture diagram pages (data pipeline, runtime layers, latency-banded execution path, streaming platform, engine ladder, workforces, memory/control, build waves).
> 2. **[FULL-SYSTEM-BLUEPRINT.md](docs/plans/FULL-SYSTEM-BLUEPRINT.md)** — the pinned build document: every part explicit, wave-by-wave exit gates.
> 3. **[FULL-SYSTEM-MASTER-PLAN.md](docs/plans/FULL-SYSTEM-MASTER-PLAN.md)** — executive overview wiring all specialist plans together.
> 4. Then the specialist docs: `AGENT-FLEET-BLUEPRINT` (75-brain non-LLM fleet) ·
>    `LLM-FLEET-BLUEPRINT` (12 LLM seats) · `ENGINE-DATABASE-PLAN` (stores D0–D7 +
>    engines E1–E7) + the full PDF shelf (15 documents) in the same folder.

## The shape of the system

| Workforce | Size | Role |
|---|---|---|
| Non-LLM fleet = the hands | **75 research brains** → 480–545 executor cells (≤75 active at once) | deterministic execution across NSE/BSE options · FX ECN · separate-account scalping · crypto perps — every artifact bit-for-bit replayable |
| LLM cockpit = the brain's management layer | **12 seats** (build · review · cybersec · data-QA · news · research · scoring · ops · fleet-control · training · compliance · cockpit) | decide-and-explain, never the hot path; writes land only through approval gates (L0–L3) |
| Gates | E2/E4 ladder | DSR ≥ 0.95 + CPCV + crisis replay + 10³-candidate cap → owner approves ≤5/week → live |

Non-negotiable invariants: collect once/reuse many · rights firewall at the boundary ·
**no LLM in fast paths** · PIT + survivorship discipline · provenance everywhere ·
versioned contracts · train→eval→paper→approve→deploy · venue RTT dominates (<5 ms
internal budget) · guards G1–G6 · kill cascade L0–L4 with an out-of-band kill that
works without the app.

## Repo map (monorepo, created at wave W0a per the blueprint §2.1)

```
rust/        hot-path kernel (edition 2024): rail · featurize · evaluate · govern · exec · ledger
python/      research & LLM edge: research · gates · seats · tools
configs/     subjects · schemas · rights classes · calendars
compose/     container stack (Postgres · QuestDB · NATS · cockpit)
docs/plans/  the 15 planning PDFs + markdown sources (this build's source of truth)
```

Language boundary is a wall: R1/E1/E2/E5/E6/E7 are Rust; research/gates/seats are
Python; contracts across, no shared runtime, no framework in the hot path.

## Build order (waves with exit pins — see blueprint §13)

`W0a` store picks + replay harness → `B1` data-first backfill (6–10 y, all domains,
gaps verified) → `W0b` ledger/signing/kill-cascade → `W0c` LLM cockpit + memory
substrate → `W1` research stack + paper engine → `W2` venue adapters + seats live →
`W3` first cells to shadow → `W4–W5` microstructure + crypto → `Stage 6+` cockpit GA,
then public-content engines last.

A wave is done only when its pin commands produce real, filed output.

## Status

- **Initial seed** — plans + skeleton only; kernel work starts at W0a.
- No secrets in this repository (scanned at seed time); configuration uses env vars by design.
- License: to be decided by the owner (unlicensed = all rights reserved for now).
