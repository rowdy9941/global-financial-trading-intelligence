# A1 — Full-software roadmap (owner restatement)

**Status:** adopted 2026-11-04 · extends [A0](A0-DECISION-BASELINE.md) §8 (build order), does **not**
supersede A0 §4 (frozen identities) or §7 (the eight gates).

This file exists because the requirement below was stated in conversation and had never been
written down. A0 records *what* the system is; A1 records *what "done building" means* and the
order in which it is reached.

---

## 1 · The requirement, as restated by the owner

1. **First** build the full infrastructure: database, engines, and every wire, **merged as one
   software** — one repository, installable on any OS, containing both the custom engines and the
   adopted open-source engines.
2. **Then** bring in NIFTY, BTC/USDC and GOLD at **tick-by-tick** timeframe plus the related
   domains.
3. **Then** build, train, find bugs, fix them, and drive the full system end to end.

Data collection and training come **last**, after the software is one installable thing.

## 2 · Reconciliation with the earlier record

Read this before quoting A0 §8 as the order of work.

| Owner statement | In the earlier record? | Note |
|---|---|---|
| one repo, everything wired | **yes** | A0 §8 phase order already said platform → data → train |
| "as one software" | **no** | A0 describes components and gates; it never says they must ship as one installable artifact |
| installable on **any OS** | **yes — `A0` §2** | A0 §2 defines tiers A/B/C with a testable rule: *"'Any OS' is never claimed beyond A/B/C above until each tier passes the clean-install gate twice in a row."* Only tier A is implemented; CI is `os: [ubuntu-latest]`. The **blueprint never mentions install or OS at all**, which is how this was missed while reading the wave table |
| merge custom **and open-source** engines | **no** | the plans record adopt/port decisions (Nautilus, proton, brigade, ruflo) as **decision-only**; nothing was integrated |
| tick-by-tick for all three pilots | **partial** | A0 §4 freezes the P1→P2→P3 order; P1 has ticks, P2/P3 are blocked (see §5) |
| profits from day one | **no — contradicted** | A0 and the gates state no profitability guarantee; the current candidate is DSR 0.4894 and is refused |

### Document vs. code drift this reconciliation exposed

A0 §2 states that *"the installer ships with a `--tier` probe that refuses unsupported
configurations loudly instead of half-installing."* `bootstrap/install.sh` has **no `--tier`
argument** — only a comment on line 2 and an `echo "[1/5] prerequisite probe (tier check)"`.
The promise is in the baseline, the implementation is not. That is exactly the class of gap §3
tracks, and it is why the any-OS requirement read as "not saved": it was saved in A0, not in the
blueprint, and the code never matched it.

### Corrections to claims made about this repo

These were asserted in session and are wrong or imprecise; they are recorded so they are not
repeated:

- *"the cost model is zero code"* — **wrong.** `python/train/walk_forward.py:40` charges a flat
  `COST_BPS = 8.0` per flip. What is missing is a *venue-aware* model (fee / spread / impact /
  funding), not any cost handling.
- *"E6 paper/shadow has no process"* — **wrong.** `engine/src/bin/cell.rs:90` runs `Router::paper`
  and `w0b_gates.py` `gate_engine()` drives cell → E6 → E7 → 4 signed fills under kill-open. The
  precise gap is that `Router::shadow` is constructed only in tests, so shadow mode is unreachable
  at runtime.
- *"`rust/crates/`"* — **wrong path.** The workspace members are `rust/{rail,d0,ledger,engine,govern,evaluate,featurize,exec}`.

## 3 · What exists today vs. "one software"

Measured 2026-11-04. Eight gaps, each with its evidence.

| # | Gap | Evidence |
|---|---|---|
| 1 | **The engines are fixtures, not services.** `cell` and `e7` exit after a tick count; setting the count to 0 exits immediately instead of running forever | `engine/src/bin/cell.rs:110` `while ticks < ticks_want` · `engine/src/bin/e7.rs:42` `while got < want` |
| 2 | **Nothing supervises anything.** No `restart:` policy on any service; the only always-on processes are three datastores, one tick recorder and two cron lines | `compose/docker-compose.yml` (3 services, no `restart:`) |
| 3 | **No single entry point.** Nine ways to start things: `cargo run`, `uv run`, `docker compose`, `bootstrap/install.sh`, cron, ad-hoc `subprocess` from gates | repo-wide |
| 4 | **No cross-OS install.** Installer is bash + Linux paths + `docker`/`cargo`/`uv`; no macOS or Windows branch anywhere, and the `--tier` probe A0 §2 promises does not exist | `bootstrap/install.sh:18` · `.github/workflows/ci.yml:13` |
| 5 | **Config is scattered.** Env lives in `compose/.env`, `w0b.LEDGER_ENV`, per-binary env vars, and cron | `python/gates/w0b_gates.py`, `compose/.env` |
| 6 | **Only 2 of the 5 declared subject families exist.** No publisher for `signal.*`, `risk.*`, `seat.*` — the seats poll Postgres instead | literal subject strings in Rust: `market.spot.*`, `order.spot.*` |
| 7 | **No health view.** Eight gates answer "is it built", nothing answers "is it running" | `python/gates/status.py` runs gates, not services |
| 8 | **Declared directories are empty.** | `python/research/.gitkeep`, `python/tools/.gitkeep` |

Counted for scale: 163 tracked files · 8 crates · 16 binaries · 6 SQL migrations · 3 Compose
services · 16 Python test modules.

## 4 · Build order

### Phase A — one software (no data, no training)

Exit pin: **on a clean checkout, one command installs, starts, and reports healthy; the same
command sequence is byte-identical on a second run.**

| Step | Deliverable | Gate |
|---|---|---|
| A1 | `cell`/`e7` run-forever mode + graceful stop + heartbeat line | process stays up, exits 0 on SIGTERM, heartbeat observable |
| A2 | `gfti` CLI: `install · up · down · status · doctor` | one entry point for every action |
| A3 | Compose runtime profile, `restart: unless-stopped` for every service | kill -9 each service → it comes back |
| A4 | One config file; one `gfti status` health view over the same DB the gates read | status matches DB state |
| A5 | Cross-OS: POSIX `sh` + PowerShell shim, per-OS preflight, macOS runner as tier B | CI matrix green on ≥2 OS families |
| A6 | End-to-end smoke on the installed software: tick → decision → signed fill → status | twice, identical |

**A5 caveat, non-negotiable:** Docker on macOS/Windows gives install and development parity. It
does not give latency parity — the µs-class rail budgets (90 ns/row, FANOUT p99 ≤ 2 ms) were
measured on bare Linux and are meaningless inside a VM. Latency claims stay Linux-only, forever.

### Phase B — data, tick by tick

P1 BTC/USDC (already has ticks + 1m + 1d) → P2 NIFTY → P3 Gold. Blockers in §5 are owner inputs,
not code. Cross-domain feeds (news, macro, filings) follow in the order of
`docs/plans/DATA-GATHERING-PLAN-V3.md`.

### Phase C — train, find bugs, fix

Cost-model correctness first (venue fees, funding, slippage), then shadow P&L, then the promotion
ladder. Bugs are fixed against the gate that catches them; a fix without a regression case is not
a fix.

## 5 · Hard constraints

1. **No profitability promise.** The system can measure expectancy honestly and refuse to promote;
   it cannot guarantee profit. Current candidate: pooled OOS Sharpe 0.8145, **DSR 0.4894 < 0.95 →
   refused**. "Towards profits" means every P&L number survives its own gates.
2. **P2 (NIFTY) is credential-blocked for tick data.** The live chain needs an authenticated broker
   (Upstox candidate; owner must complete the OAuth login). NSE bhavcopy is EOD only and may never
   be labelled as the live chain.
3. **P3 (Gold) is identity-blocked.** No venue/feed is frozen, so no D3 row may be written.
4. **The tick path is Python-owned** (`python/collect/hl_trades_ws.py` owns the venue websocket;
   `rail` reads stdin). It must move into Rust only if a tick feeds a *live* decision.
5. **Third-party code under `marketplaces/` is read-only**, and no generated artifact is hand-edited.

## 6 · Open decisions

| # | Decision | Blocks |
|---|---|---|
| D-1 | Cross-OS scope: Docker-first (all three OS families reach install parity) vs Nix flake vs both | A5 |
| D-2 | Dashboards: minimal exporter vs cockpit reading Postgres directly | A4, W0c |
| D-3 | P2: broker credentials, or amend A0 §4 to an EOD-only pilot | Phase B |
| D-4 | P3: `histdata/XAU-USD/1d` · `ctrader/XAUUSD/1m` · `dukascopy/XAUUSD/tick` | Phase B |
| D-5 | Daily lane: keep 1,347 bars (75 pre-funding) or re-cut to 2023-05-12 (1,272) | Phase C |

## 7 · Relationship to the wave table

`README.md` §9 and blueprint §13 describe **waves** (W0a → Stages 7–10): capability increments.
A1 describes **phases**: what must be true before the next kind of work starts. The waves still
apply inside Phase C and beyond; A1 governs the ordering *between* them, and it is why W0c/W2/W3
do not start before Phase A closes.
