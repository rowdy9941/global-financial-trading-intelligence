# W0c seats gate — least-privilege runtime role + deterministic seat worker.
#
# Verifies (all against a disposable Postgres database):
#   1. d3_seat_runtime may enqueue/claim/renew/finish ONLY via the queue
#      functions and read its own queue rows; direct DML is denied.
#   2. The seat role has no access to d4, d3.roles, d3.tickets, or schema DDL,
#      proving the L0-L3 order path stays out of seat reach.
#   3. The seat worker drains a noop task end to end under the seat role
#      (claim -> succeed with echo result) and fails bounded for an unknown
#      handler (max_attempts=1 -> terminal failed with the stale attempt).
#
# Deterministic fixtures only: no LLM calls, no training, no market data.

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
COMPOSE = REPO / "compose"
MIGRATION = COMPOSE / "sql" / "004_w0c_llm_tasks.sql"
SEAT_ROLE = "d3_seat_runtime"

from _env import config_env

# A-4: one config file decides; compose needs its rendered environment to `exec`.
CFG_ENV = config_env(REPO)
ADMIN_USER = CFG_ENV.get("POSTGRES_USER", "gfti_app")


def psql(db: str, user: str, sql: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-X",
            "-A",
            "-t",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            db,
            "-c",
            sql,
        ],
        cwd=COMPOSE,
        env=CFG_ENV,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def seat_out(db: str, sql: str) -> str:
    result = psql(db, SEAT_ROLE, sql)
    if result.returncode:
        raise RuntimeError(
            f"seat psql failed on db={db}: {result.stderr.strip()} "
            f"[rc={result.returncode}, stdout={result.stdout[:120]!r}]"
        )
    return result.stdout.strip()


def seat_denied(db: str, sql: str, needle: str = "permission denied") -> bool:
    result = psql(db, SEAT_ROLE, sql)
    return result.returncode != 0 and needle in result.stderr


def admin_out(db: str, sql: str) -> str:
    result = psql(db, ADMIN_USER, sql)
    if result.returncode:
        raise RuntimeError(f"admin psql failed: {result.stderr.strip()}")
    return result.stdout.strip()


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def enqueue_row(
    db: str, key: str, task_type: str, payload: dict, max_attempts: int
) -> int:
    payload_json = json.dumps(payload).replace("'", "''")
    return int(
        admin_out(
            db,
            "INSERT INTO d3.llm_tasks "
            "(idempotency_key, request_sha256, task_type, payload, max_attempts) "
            "VALUES ("
            + literal(key)
            + ", '"
            + "f" * 64
            + "', "
            + literal(task_type)
            + ", '"
            + payload_json
            + "'::jsonb, "
            + str(max_attempts)
            + "::smallint) "
            "RETURNING task_id;\n"
            "INSERT INTO d3.llm_task_events (task_id, event_type, attempt) "
            "VALUES (currval(pg_get_serial_sequence('d3.llm_tasks','task_id')), "
            "'enqueued', 0);",
        )
    )


def seat_claim(
    db: str, worker_id: str, task_type: str, lease_seconds: int = 60
) -> dict:
    raw = seat_out(
        db,
        "SELECT json_build_object("
        "'task_id',task_id,'attempts',attempts,'lease_token',lease_token)::text "
        "FROM d3.claim_llm_task("
        + literal(worker_id)
        + ", "
        + literal(task_type)
        + ", "
        + str(lease_seconds)
        + ");",
    )
    return json.loads(raw) if raw else {}


def renew(db: str, task_id: int, worker_id: str, token: str, lease_seconds: int) -> str:
    return seat_out(
        db,
        "SELECT d3.renew_llm_task("
        + str(task_id)
        + ", "
        + literal(worker_id)
        + ", "
        + literal(token)
        + "::uuid, "
        + str(lease_seconds)
        + ");",
    )


def finish(db: str, task_id: int, worker_id: str, token: str) -> str:
    return seat_out(
        db,
        "SELECT d3.finish_llm_task("
        + str(task_id)
        + ", "
        + literal(worker_id)
        + ", "
        + literal(token)
        + "::uuid, '{\"seat_ok\":true}'::jsonb);",
    )


def run_worker(db: str, task_type: str, extra: list[str] | None = None) -> dict:
    command = [
        sys.executable,
        str(REPO / "python" / "seats" / "queue_worker.py"),
        "--db",
        db,
        "--task-type",
        task_type,
        "--allow",
        task_type,
        "--once",
        "--max-empty-polls",
        "1",
    ] + (extra or [])
    result = subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=120, cwd=REPO
    )
    if result.returncode:
        raise RuntimeError(
            f"seat worker failed: {result.stderr.strip()[-500:]} out: "
            f"{result.stdout.strip()[-500:]}"
        )
    marker = result.stdout.rfind("QUEUE-WORKER ")
    if marker < 0:
        raise RuntimeError(f"worker did not report JSON: {result.stdout[:200]}")
    return json.loads(result.stdout[marker + len("QUEUE-WORKER ") :])


def run_gate() -> dict:
    run_id = uuid.uuid4().hex
    db = f"gfti_w0cs_{run_id}"
    admin_out("gfti", f'CREATE DATABASE "{db}";')
    report: dict = {"checks": {}, "fixture_only": True}
    try:
        # 001/002 bring up the real d3/d4 objects (symbols, roles, d4 ledger,
        # tickets) so the seat's denial matrix exercises actual ACLs — an
        # empty schema would produce vacuous "relation does not exist" errors.
        for migration_name, migration in (
            ("001", COMPOSE / "sql" / "001_d3_d4_init.sql"),
            ("002", COMPOSE / "sql" / "002_w0b_approval_flow.sql"),
            ("003", COMPOSE / "sql" / "003_b1_data_plane.sql"),
            ("004", MIGRATION),
        ):
            migration_result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    ADMIN_USER,
                    "-d",
                    db,
                ],
                cwd=COMPOSE,
                env=CFG_ENV,
                input=migration.read_text(encoding="utf-8"),
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if migration_result.returncode:
                raise RuntimeError(
                    f"migration {migration_name} failed: "
                    f"{migration_result.stderr.strip()[-500:]}"
                )
        task_type = f"gate.{run_id}"
        worker_id = f"seat-{run_id}"
        digest = "a" * 64
        checks = report["checks"]

        checks["seat_identity"] = seat_out(db, "SELECT current_user;") == SEAT_ROLE
        task_id = int(
            seat_out(
                db,
                "SELECT d3.enqueue_llm_task("
                + literal(f"w0cs-{run_id}")
                + ", "
                + literal(digest)
                + ", "
                + literal(task_type)
                + ", '{\"fixture\":true}'::jsonb, "
                "0::smallint, 3::smallint);",
            )
        )
        checks["seat_may_enqueue"] = task_id > 0
        checks["seat_may_read_own_queue"] = (
            seat_out(db, f"SELECT count(*) FROM d3.llm_tasks WHERE task_id={task_id};")
            == "1"
        )
        claimed = seat_claim(db, worker_id, task_type)
        checks["seat_may_claim"] = int(claimed["task_id"]) == task_id
        first_token = str(claimed["lease_token"])
        checks["seat_may_renew"] = (
            renew(db, task_id, worker_id, first_token, 120) == "t"
        )
        checks["seat_may_finish"] = finish(db, task_id, worker_id, first_token) == "t"

        checks["seat_update_tasks_denied"] = seat_denied(
            db, f"UPDATE d3.llm_tasks SET priority=1 WHERE task_id={task_id};"
        )
        checks["seat_delete_tasks_denied"] = seat_denied(
            db, f"DELETE FROM d3.llm_tasks WHERE task_id={task_id};"
        )
        checks["seat_truncate_tasks_denied"] = seat_denied(db, "TRUNCATE d3.llm_tasks;")
        checks["seat_insert_events_denied"] = seat_denied(
            db,
            "INSERT INTO d3.llm_task_events (task_id, event_type, attempt) "
            f"VALUES ({task_id}, 'leased', 0);",
        )
        checks["seat_d4_fills_denied"] = seat_denied(
            db, "SELECT count(*) FROM d4.fills;"
        )
        checks["seat_d4_approvals_denied"] = seat_denied(
            db,
            "INSERT INTO d4.approvals (ticket, level, role_name, decision, reason) "
            f"VALUES ('seat{run_id}', 'L1', 'compliance', 'grant', 'seat probe');",
        )
        checks["seat_roles_secrets_denied"] = seat_denied(
            db, "SELECT count(*) FROM d3.roles;"
        )
        checks["seat_tickets_denied"] = seat_denied(
            db, "SELECT count(*) FROM d3.tickets;"
        )
        checks["seat_schema_create_denied"] = seat_denied(
            db, f"CREATE TABLE d3.seat_probe_{run_id} (id INT);"
        )
        checks["seat_select_events_denied"] = seat_denied(
            db, "SELECT count(*) FROM d3.llm_task_events;"
        )
        checks["seat_symbols_denied"] = seat_denied(
            db, "SELECT count(*) FROM d3.symbols;"
        )
        checks["seat_kill_events_denied"] = seat_denied(
            db, "SELECT count(*) FROM d4.kill_events;"
        )
        checks["seat_datasets_denied"] = seat_denied(
            db, "SELECT count(*) FROM d3.datasets;"
        )
        checks["seat_gaps_denied"] = seat_denied(db, "SELECT count(*) FROM d3.gaps;")

        noop_task = enqueue_row(
            db,
            f"w0cs-noop-{run_id}",
            task_type,
            {"handler": "noop", "fixture": True},
            2,
        )
        worker_report = run_worker(db, task_type)
        noop_state = json.loads(
            admin_out(
                db,
                "SELECT json_build_object('status',status,'result',result)::text "
                f"FROM d3.llm_tasks WHERE task_id={noop_task};",
            )
        )
        noop_events = admin_out(
            db,
            "SELECT string_agg(event_type, ',' ORDER BY event_id) "
            f"FROM d3.llm_task_events WHERE task_id={noop_task};",
        )
        checks["worker_noop_succeeds"] = (
            worker_report["succeeded"] == 1
            and noop_state["status"] == "succeeded"
            and noop_state["result"]["noop"] is True
            and noop_state["result"]["echo"] == {"handler": "noop", "fixture": True}
        )
        checks["worker_noop_event_history"] = noop_events == "enqueued,leased,succeeded"

        broken_task = enqueue_row(
            db,
            f"w0cs-broken-{run_id}",
            task_type,
            {"handler": "missing_fixture_handler"},
            1,
        )
        broken_worker = run_worker(db, task_type)
        broken_state = admin_out(
            db, f"SELECT status FROM d3.llm_tasks WHERE task_id={broken_task};"
        )
        checks["worker_unknown_handler_fails"] = (
            broken_worker["failed"] == 1 and broken_state == "failed"
        )
        broken_error = admin_out(
            db,
            f"SELECT coalesce(error_code,'') FROM d3.llm_tasks WHERE task_id={broken_task};",
        )
        checks["worker_unknown_handler_code"] = broken_error == "handler_not_registered"

        # Apostrophe round-trip (reviewer HIGH): a payload/result containing
        # quotes must survive the literal() escaping boundary exactly once.
        quote_note = "it's a fixture — 'three' double\" quotes"
        quote_task = enqueue_row(
            db,
            f"w0cs-quote-{run_id}",
            task_type,
            {"handler": "noop", "fixture": True, "note": quote_note},
            1,
        )
        quote_worker = run_worker(db, task_type)
        quote_state = json.loads(
            admin_out(
                db,
                "SELECT json_build_object('status',status,'result',result)::text "
                f"FROM d3.llm_tasks WHERE task_id={quote_task};",
            )
        )
        checks["worker_apostrophe_round_trip"] = (
            quote_worker["succeeded"] == 1
            and quote_state["status"] == "succeeded"
            and quote_state["result"].get("echo", {}).get("note") == quote_note
        )

        # --allow "" deny-all: the seat may claim, but an empty allowlist must
        # refuse to execute the handler (terminal failure, owned error code).
        denyall_task = enqueue_row(
            db,
            f"w0cs-denyall-{run_id}",
            task_type,
            {"handler": "noop", "fixture": True},
            1,
        )
        denyall_worker = run_worker(db, task_type, extra=["--allow", ""])
        denyall_status = admin_out(
            db, f"SELECT status FROM d3.llm_tasks WHERE task_id={denyall_task};"
        )
        checks["worker_allow_empty_denies"] = (
            denyall_worker["failed"] == 1 and denyall_status == "failed"
        )

        # Renewal cap: renew may not hold a task forever. The first 20 renewals
        # (cap in 004) succeed, the 21st is refused; claim resets the counter.
        enqueue_row(db, f"w0cs-cap-{run_id}", task_type, {"handler": "noop"}, 1)
        cap_claim = seat_claim(db, f"cap-{run_id}", task_type)
        cap_id = int(cap_claim["task_id"])
        cap_token = str(cap_claim["lease_token"])
        cap_renews = [
            renew(db, cap_id, f"cap-{run_id}", cap_token, 2) for _ in range(21)
        ]
        checks["seat_renew_within_cap"] = all(v == "t" for v in cap_renews[:20])
        checks["seat_renew_cap_enforced"] = cap_renews[20] == "f"
        checks["seat_renew_cap_counter"] = (
            admin_out(
                db,
                f"SELECT lease_renewals FROM d3.llm_tasks WHERE task_id={cap_id};",
            )
            == "20"
        )

        report["ok"] = all(checks.values())
        return report
    finally:
        admin_out("gfti", f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE);')


if __name__ == "__main__":
    try:
        seats_report = run_gate()
        print("W0C-SEATS " + json.dumps(seats_report, sort_keys=True))
        sys.exit(0 if seats_report["ok"] else 1)
    except (
        AssertionError,
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"W0C-SEATS ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
