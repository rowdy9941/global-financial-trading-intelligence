# A0 — DECISION BASELINE (Phase 0 of the end-to-end build, 2026-11-03)

> Status: **BASELINE** — one authoritative reconciliation of the plan corpus with the
> repo as of commit `0bf1e83`. Where an older plan section contradicts a later
> finalized decision, **the later finalized decision governs**. Nothing here relaxes
> any existing gate (R1–R14, E4 DSR ≥ 0.95, owner promotion approval).
> Update rule: any decision change = a new dated entry appended to §9, never silent edits.

## 1 · Scope: what "the full system, end to end" means for the first release

| Row | Included in first release (private platform) | Deliberately later |
|---|---|---|
| Data | R1 rail, D0–D6 stores, 3 pilot instruments + fixture domains | wider fleet symbols, T3 world deep data |
| Engines | E1–E7 spine, E3/E4 research+eval, W1 gates | full 75-cell fleet scale-out |
| Control | W0c durable task queue + worker, kill cascade, audit | Cockpit dashboards, LLM fleet UI, approvals UI polish |
| Consumers | paper/shadow trading path, replay | Terminal (Row 1), News portal, Media hub, World Monitor |

The public products (Terminal, portal, media) are built **after** the private platform
passes its gates and consume only approved outputs through the rights firewall
(`FULL-SYSTEM-PLAN.md` Stage 7 — unchanged).

## 2 · Word status: "any OS" — the testable definition

| Tier | OS | Meaning | Verification |
|---|---|---|---|
| **A — primary** | Linux x86-64 (Ubuntu 26.04 = dev box) | native services, near-native loop | every gate runs here; release artifacts built here |
| **B — supported** | macOS (Apple Silicon) | dev + paper testing; services via the same bootstrap | clean install in CI, `cell`/`rail` spot latency checked |
| **C — supported** | Windows | WSL2 + Docker for services; native Rust binaries for E1/E2 hot path | clean install in CI; E5/E6 paper path E2E in WSL2 |

"Any OS" is never claimed beyond A/B/C above until each tier passes the clean-install
gate twice in a row. The installer ships with a `--tier` probe that refuses unsupported
configurations loudly instead of half-installing.

## 3 · Engine decisions — formerly conflicting wording, now reconciled

The §10.1 table row ("NautilusTrader … **fallback, not adopted**") and §10.3 row 4
("swap-in fallback") are **superseded** by the later finalized decisions (§§11–13,
Sessions 37–38, marked FINALIZED with owner sign-off):

| Decision | Governing text | Status |
|---|---|---|
| **NautilusTrader is ADOPTED** for E4-BT event-driven gear + HL execution kernel | §12 row A1, FINALIZED (S37): *"(a) E4-BT …, (b) E6 route via Nautilus-Hyperliquid …"* | integrated W1–W2 |
| Crypto execution owner = **Nautilus on Hyperliquid** | §13/C1 DECIDED (S38); delta ≤0.2% of venue RTT, W2 spike validates | W2 |
| Upstox/Indian + cTrader adapters = **our Nautilus adapter plugins** | §12 rows C2/C3 DECIDED (S38) | W2–W3 |
| MT5 = rejected unless a venue/broker forces it | §12 C3 | closed |
| Hummingbot = dropped | §12 C4 closed (S38) | closed |
| Freqtrade = not adopted | §12 row A | closed |
| Nautilus stays **behind our E6 contract**, never a bypass | §10.3 row 4 contract + §15 hot-path | enforced by E5/E6 tests |
| hftbacktest = fill-realism acceptance checklist, not runtime | §12 row A5 | W1 |
| VectorBT open vs qlib vs own-Numba = **W1 measured benchmark, not adopted yet** | §14.1 | W1 |
| Direct Rust E6 for crypto = contingency only | §13 (Rust 2–20 µs vs 20–200 µs callback) | kept as design note |

Stale paragraph-classified wording in §10.1/§10.3 remains as history; §3 above is the
authority going forward. E-docs generated from plans must cite this section.

## 4 · Pilot instruments — frozen identities (acceptance cases 1–3)

| # | Instrument | Venue-native product | Quote currency | Pilot order |
|---|---|---|---|---|
| P1 | BTC perpetual | Hyperliquid **BTC-USDC-PERP** (venue symbol `BTC`) | **USDC** | first |
| P2 | NIFTY options | NSE **NIFTY F&O option chain** (strike/expiry/master from a verified options source, not EOD bhavcopy) | INR | second |
| P3 | Gold | venue/feed **pending** (cTrader was prior plan; exact feed + sessions locked before backfill) | USD | third |

Expansion after pilots pass their gates: **ETH-USDC, HYPE-USDC** (Hyperliquid), then
the approved wider fleet. Existing datasets remain what they are — `binance/BTCUSDT/1d`
(Binance spot daily) and `hyperliquid/BTC-USD/1d` (hyperliquid daily) are **never
relabeled** or silently substituted for P1 identity; P1 collectors register
`hyperliquid/BTC-USDC/…` keys when they land.

## 5 · Store & data-plan truth (reconciliation of counts)

| Item | Truth | Evidence |
|---|---|---|
| Master dataset catalog | **1,114 rows / 18 volumes** | `docs/plans/DATASET-TODO.md` (S43 merge; 0 schema errors) + `FULL-SYSTEM-BLUEPRINT.md` §2 |
| Older count | 1,000 rows / 12 volumes = **superseded** | DATA-GATHERING-PLAN-V3 (S-era snapshot) |
| Catalog verification split | VERIFIED-UPSTREAM-DOC 378 · SURVEY-EVIDENCE 384 · UNVERIFIED-CANDIDATE 352 | DATASET-TODO.md merge header |
| Tick/volume semantics for pilots | per-source registration decision, never assumed: volume present/absent is a D3 column (`volume_available`), not an inference | migration 006 compatibility defaults |

No training may start on any dataset whose D3 row says `verified=false`, or which has an
open unjustified gap row (machine-enforced — walk_forward refuses; proven by W1-GATE-WF
`training_lock_refused:true`).

## 6 · Repository truth map (repo ↔ blueprint)

| Component | Repo state (2026-11-03) | Next action |
|---|---|---|
| Rust rail / d0 / ledger / engine | built, tests pass, release binaries exist | keep green; add E1-E5 contract tests in CI |
| Rust `featurize` / `evaluate` / `govern` / `exec` | empty (`.gitkeep` — are the E1/E2/E5/E6 crates) | build in Waves: E1 features → E5 govern → E6 exec |
| compose: Postgres, QuestDB, NATS | up locally; W0a/W0b gate evidence recorded | add service health checks to gates |
| D3/D4/D5 SQL migrations 001–006 | applied (006 confirmed twice idempotent) | migration history runner in installer |
| gates W0a/W0b/W0c/B1 | passing evidence recorded | re-run in CI where stateless |
| gate W1 (this build) | **green evidence on record** (`ALL`, 2026-11-03) | keep as release gate step |
| G-PILOT-E2E fixture arm | **green evidence on record** (`PILOT-E2E-SUITE ok:true`, 3 pilots, 2026-11-03; `docs/gates/PILOT-E2E.md`) | real-data arm needs registered verified datasets (Phase 4) |
| W1 gate + W0c cockpit worker | running in-process dev mode | W3 finalize |
| Nautilus integration | none in repo (by design — the E4 path) | W2 spike, contract-first |
| Installer / CI | being added with this baseline (§7) | iterate until clean-install passes |

## 7 · Verification gates — the definition of "end to end done"

G = fused from FULL-SYSTEM-BLUEPRINT §13 gates and §15 release checklist:

1. **G-INSTALL** clean install per §2 tier; twice consecutively.
2. **G-STORE** migrations reapply; least-privilege + append-only DB probes (existing
   W0b/W1 negative tests); d4 kill-cascade state.
3. **G-RAIL** non-strict schema, out-of-order, duplicate, gap-in-lookahead, stale-feed drill (rail unit + w0b G5).
4. **G-ENGINE** E1–E7 contract tests, incl. kill cascade + reconcile-before-order + ws-close (w0b G7 + W1-GATE arms).
5. **G-DATA** for each dataset: D3 row verified + gap coverage + B-GATE hash check before any training.
6. **G-RESEARCH** E4-WF CPCV/DSR fail-closed + training-lock negative + artifact negative tests (W1-GATE-WF, done.
7. **G-PILOT-E2E** for each of P1/P2/P3: ingest→features→paper→recon→replay on **fresh checkout data** (fixture tree), then on real-registered data; deterministic output either way (W1-GATE-REPLAY principle, twice). Fixture arm = **measured** (`python/gates/pilot_e2e.py ALL`, 2026-11-03: P1/P2/P3 each 8 decisions → 8 signed fills, bad_sigs 0, deterministic across both passes); real-data arm = **P1 measured** 2026-11-04 (`--real P1`, registered `hyperliquid/BTC-USDC/1m`, 7 intents → 7 signed fills, `bad_sigs 0`, deterministic); P2/P3 still pending their verified D3 rows.
8. **G-CI-OS** every tier's matrix job green on the release tag.

No gate can be waived by plan text; a green CI badge is not a gate pass.

## 8 · Build order (contract-freeze → installer → engines → data → train)

1. **Phase 0 (this doc)** — baseline locked; instrument IDs frozen.
2. **Phase 1** — installer + CI skeleton (`bootstrap/install.sh`, `.github/workflows/ci.yml`) proven by a real run on tier A.
3. **Phase 2** — E-spine completion in Rust: featurize (E1 feature API parity with the engine crate), evaluate (E2 signed-artifact loader), govern (E5 deterministic governor), exec (E6 scheduler/paper/shadow) against frozen contracts; ledger audits. **Status 2026-11-03: complete** — E1/E2/E5/E6 crates landed with cross-crate contract tests (`rust/exec/tests/spine.rs`), 34 cargo suites green, clippy `-D warnings` clean.
4. **Phase 3** — fixture E2E for P1/P2/P3 across the full spine (ticket#-traceable).
5. **Phase 4** — data backfills gated by B-GATE; pilots wired to real infra.
6. **Phase 5** — training/eval runs on P1 BTC-USDC using registered (verified) data only.
7. **Phase 6** — W0c finish + Cockpit; W2 Nautilus spike; Terminal/World/Media stay later-stage.

## 9 · Decision log

| Date | Decision | Evidence |
|---|---|---|
| 2026-11-04 | P1 accumulation was structurally broken and is fixed: the capture rewrote a rolling 3-day window against a venue that retains only 3 days, so the archive could never exceed 3 days (measured: 4,320 rows on every run) and the 300-bar target was unreachable. `--merge` now unions by `open_ms` (immutable closed bars, venue revisions reported not adopted), `hl_capture.sh` always merges and holds an `flock`; proven on live data by a 1-day window leaving 4,322 archive rows where a rewrite would have left 1,440 | `docs/gates/P1-DATA.md` §2.1 |
| 2026-11-04 | G-PILOT-E2E real-data arm opened for P1 and made impossible to confuse with the fixture arm: it refuses unless the D3 row is `verified=true`, feeds the venue's real 1m open times/closes, and labels itself `data_kind=registered-1m`; `install.sh --verify` runs it and skips loudly when the untracked `d0/` archive is absent | `docs/gates/PILOT-E2E.md` |
| 2026-11-04 | P1 tick capture hardened after a real data-loss incident: two concurrent recorders interleaved deflate streams inside one gzip member (13:00–13:49 lost, 0 of ~7,700 prints recoverable) and a pre-fix writer abandoned an open member on SIGTERM. Fixes are structural: one complete gzip member per flush batch, graceful stop via `asyncio.Event`, a per-directory `fcntl` lock (second writer exits 3 naming the holder pid), and `recover_trades_segments.py` for salvage with `.corrupt` backups. A bug in that recovery tool (discarding everything past the first 64 KiB feed block) was caught by cross-checking against `gzip -dc` and fixed | `docs/gates/P1-DATA.md` §4 |
| 2026-11-04 | Tick coverage is declared partial, not continuous: 4 segments / ~15.3k prints from 2026-11-03T12:55Z, with a 13 h 48 m unrecoverable gap (interleave + host reboot at 03:56Z). Ticks stay raw evidence under `d0/` with **no** D3 row — no independent venue file exists to verify a self-captured feed against | `docs/gates/P1-DATA.md` §4–§5 |
| 2026-11-03 | P1 accumulation wired to the training lane: `resample_1m_to_1d.py` derives only COMPLETE days (partial edges dropped, interior holes refused) and `hl_capture.sh` registers the derived `hyperliquid/BTC-USDC/1d`; derived-vs-API daily bars agree exactly on every overlapping day | `docs/gates/P1-DATA.md` |
| 2026-11-03 | P2 blocker recorded: NSE live chain endpoint returns 404 from this box while archives/holiday master are reachable; EOD bhavcopy works (5,060 NIFTY contract rows, integrity PASS) but cannot stand in for the chain identity — needs owner broker credentials (Upstox candidate) | `docs/gates/P2-DATA.md` |
| 2026-11-03 | P1 intraday dataset registered: `hyperliquid/BTC-USDC/1m` (4,320 real bars, 0 missing, verified via the B-GATE gap rule); measured API retention (1m ≈ 3 days) recorded — historical intraday backfill impossible, PIT accumulation now runs via `scripts/hl_capture.sh` | `docs/gates/P1-DATA.md` |
| 2026-11-03 | Baseline created; Nautilus conflicts reconciled (§3); pilots frozen (§4); OS tiers declared (§2); catalog truth pinned (§5); repo truth map (§6); gates formalized (§7) | this doc |
