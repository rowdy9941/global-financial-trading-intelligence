# W0b — D4 append-only ledger + kill-cascade gates (blueprint §13)

Status: **DONE 2026-10-30** — all four gates green with verbatim evidence below.

Scope built this wave: `rust/ledger` (crate + 5 binaries: init / fill / kill /
check / recon), `compose/sql/001_d3_d4_init.sql` (D3 registry + D4 append-only
ledger + `d4.kill_state` view + `d3.gate_w0b`), gate harness
`python/gates/w0b_gates.py`.

## Contracts (enforced, not conventional)

| Contract | How it is *enforced* |
|---|---|
| Append-only D4 | Postgres BEFORE UPDATE/DELETE triggers on all four d4 tables (fills, kill_events, approvals, pnl_recon) raise `d4 append-only: … denied`. The crate never issues UPDATE/DELETE — and even if a future bug did, the DB refuses. |
| Fail-closed kill | `ledger_check` exits 7 on engaged **or on DB-unreachable** (worst interpretation wins: a broken store must never look "open"). |
| Signed fills | HMAC-SHA256 over the canonical string `fill\|source\|symbol\|side\|qty.8\|px.8` under the role secret (env `LEDGER_SECRET_OWNER`; the secret is never persisted — only its sha256 in `d3.roles`). qty/px are stored as exact NUMERIC via `$n::text::numeric` with the same `.8` text the signature covers, so storage and verification cannot drift. |
| Kill flips are rows, not updates | engage/resolve append rows; `d4.kill_state` view reads the latest. |

## G1 — append-only enforcement (DB-level, real trigger text)

`python3 python/gates/w0b_gates.py APPEND` →

```
W0B-G1-APPEND-ONLY {"update_fills_denied": true, "delete_fills_denied": true, "delete_kill_events_denied": true, "ok": true, "evidence": ["ERROR:  d4 append-only: UPDATE on fills denied (audit ledger is immutable)\nCONTEXT:  PL/pgSQL function d4.deny_mutation(", "ERROR:  d4 append-only: DELETE on fills denied (audit ledger is immutable)\nCONTEXT:  PL/pgSQL function d4.deny_mutation(", "ERROR:  d4 append-only: DELETE on kill_events denied (audit ledger is immutable)\nCONTEXT:  PL/pgSQL function d4.deny_mut"]}
```

Honest lesson: the first cut of this gate "passed" for the wrong reason (docker
ran without the compose cwd → `no configuration file provided: not found` →
nonzero rc). The harness was fixed to require BOTH a nonzero rc AND the actual
`d4 append-only` trigger text in stderr before reporting denial — the gate may
not pass on infrastructure noise.

## G2 — kill drill (engage → refuse · fail-closed · resolve → open)

`python3 python/gates/w0b_gates.py KILL` →

```
W0B-G2-KILL-DRILL {"open_check": "LEDGER-CHECK {\"state\":\"open\",\"risk_allowed\":true}", "engaged_check_rc7": "LEDGER-CHECK {\"state\":\"engaged\",\"level\":\"L0\",\"source\":\"gate-drill\",\"reason\":\"W0b-kill-drill\",\"risk_a", "failclosed_unreachable_rc7": "LEDGER-FAIL-CLOSED db unreachable: error connecting to server\nLEDGER-CHECK {\"state\":\"failsafe_engage", "resolved_check": "LEDGER-CHECK {\"state\":\"open\",\"risk_allowed\":true}", "ok": true}
```

Exit-code contract: open = rc 0, engaged = rc 7, **DB unreachable = rc 7 with
`"state":"failsafe_engaged"`** (the fail-closed proof — never empty output).

## G3 — Postgres restart ×10 with a signed append each cycle

`python3 python/gates/w0b_gates.py RESTART` →

```
W0B-G3-RESTART-X10 {"restarts": 10, "cycles_ms": [14002, 13358, 15316, 16286, 11804, 18920, 19494, 20634, 22311, 10638], "ok": true}
```

Each cycle: stop + start compose postgres → wait for `healthy` → one signed
append succeeds ⇒ the ledger is writable 10/10 after restarts, and the
append-only triggers survive restarts (they are part of the schema).

## G4 — signed 20k append (batched hot path) + signature verification

`python3 python/gates/w0b_gates.py RECON` →
(two runs shown: bench run and the final P&L-visible run — final binaries, 2026-10-30)

```
W0B-G4-SIGN-RECON {"fills_signed": 20000, "bad_sigs": 0, "ok": true, "mode": "same-run secret signs then verifies (ephemeral, dev)", "bench": {"rows": 20000, "chunks": 20, "wall_ns": 8249710738, "batch_ns_avg": 412418742, "per_row_ns_avg": 412485, "per_row_p50": 398332, "per_row_p99": 489335, "rows_per_sec": 2424}, "pnl_lines": []}

W0B-G4-SIGN-RECON {"fills_signed": 20000, "bad_sigs": 0, "ok": true, "mode": "same-run secret signs then verifies (ephemeral, dev)", "bench": {"rows": 20000, "chunks": 20, "wall_ns": 9811266440, "batch_ns_avg": 490494024, "per_row_ns_avg": 490563, "per_row_p50": 427709, "per_row_p99": 795870, "rows_per_sec": 2038}, "pnl_lines": ["PNL NIFTY fills=20000 gross=2084.60"]}
```

MEASURED: the batched hot path (`append_fills_batch`: one transaction per
1000-row chunk, one prepared statement, sign-before-txn) appends at ~0.4–0.5
ms/row (**~2,000–2,400 rows/s**) versus the measured single-row autocommit
baseline **25.8 ms/row** (500-row drill, WAL fsync per row) — **~50–60×**.
`ledger_recon` re-signs every row from stored qty/px and compares: 20,000/20,000
match, `bad_sigs=0`; per-symbol gross P&L lands in `d4.pnl_recon`
(PNL NIFTY fills=20000 gross=2084.60 — synthetic feed: buy/sell imbalance of the
deterministic feeder, not a trading result).

One honest swallow fixed on the way: `gross_pnl` originally used the QuestDB
grammar `count()` (this is Postgres) and returned an empty vector via
`unwrap_or_default` in recon; the first G4 run recorded empty `pnl_lines`. Fixed
to `count(*)` with error propagation through the bin (`PNL NIFTY …` above).

## Bench honesty notes

- Postgres here runs in Docker on a desktop SATA/NVMe dev box; production uses
  the owner-side store — the numbers are relative comparisons on identical
  hardware (autocommit vs batched on the same box, same schema, same secret).
- The 500-row single-append drill used `append_fill` (kept in the crate for
  single-row callers); production appends MUST use `append_fills_batch`.
- Dev gate secret is ephemeral per run (`dev-w0b-<urandom-hex>`), never a repo
  constant; rows from earlier runs are signed with secrets that are gone by
  design, so the recon gate TRUNCATEs first (the only reset possible on an
  append-only table — UPDATE/DELETE denied at DB level).

## Gate-verdict landing (`d3.gate_w0b`)

The harness inserts a verdict row into `d3.gate_w0b` after every run
(append-row only). Verbatim readback:

```
           gate            | verdict |            ran_at
---------------------------+---------+-------------------------------
 w0b_batch_20261030T111331 | pass    | 2026-10-30 05:43:31.354597+00
 w0b_batch_20261030T111319 | pass    | 2026-10-30 05:43:19.715114+00
 w0b_batch_20261030T111317 | pass    | 2026-10-30 05:43:17.303116+00
(3 rows)
```

## Remaining W0b scope (next increments)

Per blueprint §13: E-cell skeletons (E1/E2/E5/E7 with flight-recorder traces),
L1–L4 approvals + watchdog binary (out-of-band, no app dependency), formal
E1→E5→E6→wire fan-out p99 ≤ ~2 ms bench, `d3.gate_w0b` landing rows (the table
exists; harness lands verdicts there next).
