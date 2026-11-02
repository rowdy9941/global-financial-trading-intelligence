# W0c — control-plane build evidence

Status: **IN PROGRESS**. This record covers only the first bounded increment:
the durable D3 task-queue contract. It does not certify the rest of W0c or
production readiness.

## Increment 1 — `llm_tasks` durable queue

Implemented in `compose/sql/004_w0c_llm_tasks.sql`:

- Idempotent enqueue keyed by a caller-supplied SHA-256 request digest; reuse
  with a different digest or request fields is rejected.
- Task-type-scoped atomic claim with `FOR UPDATE SKIP LOCKED`, lease expiry,
  fencing tokens, bounded retries, and terminal success/failure states.
- Append-only task lifecycle events, including a database trigger that rejects
  UPDATE, DELETE, and TRUNCATE.
- Queue functions are invoker-rights and not executable by `PUBLIC`. Seat
  runtime roles and least-privilege grants are **superseded by increment 2
  below** (the functions became SECURITY DEFINER under a dedicated owner).
- Payload/result JSON is expected to contain no credentials or raw secrets.
  The queue is control-plane work only, never an order queue.

The integration gate provisions a randomly named disposable database in the
existing local Compose Postgres, applies the migration twice, tests lifecycle
and concurrency invariants with deterministic fixtures, then drops the test
database. The concurrency probe holds the first claimant's transaction open
while confirming the second claim returns no lease. It does not touch the
persistent `gfti` database, fetch market data, call an LLM, or train any model.

Command:

```bash
python3 python/gates/w0c_tasks.py
```

Observed output:

```text
W0C-LLM-TASKS {"checks": {"attempt_cap_is_terminal": true, "claim_attempt_incremented": true, "claim_matches_own_fixture": true, "concurrent_claim_is_single": true, "concurrent_claim_overlap_observed": true, "conflicting_key_rejected": true, "duplicate_completion_refused": true, "event_log_append_only": true, "expired_lease_rejected_before_reclaim": true, "migration_idempotent": true, "reclaim_fences_old_worker": true, "retry_then_complete": true, "same_key_idempotent": true, "second_claim_empty_for_live_lease": true}, "fixture_only": true, "ok": true}
```

This is plumbing evidence only. It does not evaluate LLM quality, model
training, data coverage, approval enforcement, D4 audit integration, NATS
delivery, a seat runtime, dashboards, memory substrates, or cockpit behavior.
Those W0c components remain outstanding.

## Increment 2 — least-privilege seat runtime + deterministic worker

Implemented in the same `004_w0c_llm_tasks.sql` (updated in place while
uncommitted; the persistent `gfti` database was re-migrated with it):

- `gfti_queue_owner` (NOLOGIN) owns the queue tables, sequences, and all five
  SECURITY DEFINER functions (`enqueue/claim/finish/fail/renew_llm_task`,
  each with a pinned `search_path`), so nothing runs with the Compose
  bootstrap superuser's authority.
- `d3_seat_runtime` (LOGIN, no password — remote auth fails closed; dev path
  is the container-local unix socket) gets EXECUTE on exactly the five queue
  functions plus SELECT on `d3.llm_tasks`. No DML, no other d3/d4 objects, no
  DDL.
- New `renew_llm_task`: extends a live lease + `lease_token` + worker id,
  bounded by a `lease_renewals` cap (claim resets to 0; renew refuses at 20).
- Trust boundary on record: every seat authenticates as the same
  `d3_seat_runtime` role, so a seat can read any task's `lease_token` and
  present another worker's token in principle. This is deliberate,
  same-domain trust for the dev plane; per-seat credentials and per-lease
  secret fencing are the next increment, not hidden debt.

`python/gates/w0c_seats.py` applies **001 + 002 + 003 + 004** to a random
disposable database (so every denial probe exercises a real ACL, not a
missing relation), runs 29 checks, then drops the database. The
`--allow ""` deny-all case, the apostrophe round-trip (payload/`result`
escapes exactly once at the SQL literal boundary), and the renewal cap were
added in response to the increment-2 review.

Command:

```bash
python3 python/gates/w0c_seats.py
```

Observed output:

```text
W0C-SEATS {"checks": {"seat_d4_approvals_denied": true, "seat_d4_fills_denied": true, "seat_datasets_denied": true, "seat_delete_tasks_denied": true, "seat_gaps_denied": true, "seat_identity": true, "seat_insert_events_denied": true, "seat_kill_events_denied": true, "seat_may_claim": true, "seat_may_enqueue": true, "seat_may_finish": true, "seat_may_read_own_queue": true, "seat_may_renew": true, "seat_renew_cap_counter": true, "seat_renew_cap_enforced": true, "seat_renew_within_cap": true, "seat_roles_secrets_denied": true, "seat_schema_create_denied": true, "seat_select_events_denied": true, "seat_symbols_denied": true, "seat_tickets_denied": true, "seat_truncate_tasks_denied": true, "seat_update_tasks_denied": true, "worker_allow_empty_denies": true, "worker_apostrophe_round_trip": true, "worker_noop_event_history": true, "worker_noop_succeeds": true, "worker_unknown_handler_code": true, "worker_unknown_handler_fails": true}, "fixture_only": true, "ok": true}
```

`python/seats/queue_worker.py` is the first deterministic seat consumer: it
authenticates as `d3_seat_runtime` (`docker compose exec` unix socket),
verifies its own identity, claims work scoped by `--task-type` + `--allow`
and executes only locally registered handlers resolved from the payload
(`handler` field, `noop` shipped). No LLM calls. Exit codes: 0 clean stop,
3 operational failure (fail-closed; the held lease expires into the reclaim
path), 4 identity failure. Its noop E2E (claim → succeed with echoed
payload → terminal `succeeded` event history `enqueued,leased,succeeded`)
and the unknown-handler bounded-failure E2E are the `worker_*` checks above.

Still outstanding (later increments, in order): per-seat credentials +
per-lease secret fencing; LLM seats; real D4 audit wiring from queue
outcomes; NATS seat transport; dashboards; memory-substrate evaluation;
cockpit chat. Not certified by this file: production readiness, training,
market-data coverage, approval-flow integration with the queue.
