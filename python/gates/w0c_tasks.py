"""Integration gate for the W0c D3 durable seat-task queue.

Runs only deterministic control-plane fixtures against the existing local
Compose Postgres. It does not fetch market data, call an LLM, or train models.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
COMPOSE = REPO / "compose"
MIGRATION = COMPOSE / "sql" / "004_w0c_llm_tasks.sql"
DB_NAME = "gfti"


def psql(sql: str) -> str:
    result = subprocess.run(
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
            "gfti_app",
            "-d",
            DB_NAME,
            "-c",
            sql,
        ],
        check=False,
        cwd=COMPOSE,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError(
            f"psql failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def psql_rejects(sql: str, expected_error: str) -> bool:
    result = subprocess.run(
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
            "gfti_app",
            "-d",
            DB_NAME,
            "-c",
            sql,
        ],
        check=False,
        cwd=COMPOSE,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode != 0 and expected_error in result.stderr


def apply_migration() -> None:
    migration_sql = MIGRATION.read_text(encoding="utf-8")
    for _ in range(2):
        result = subprocess.run(
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
                "gfti_app",
                "-d",
                DB_NAME,
            ],
            check=False,
            cwd=COMPOSE,
            input=migration_sql,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError(f"migration failed: {result.stderr.strip()}")


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def enqueue(key: str, digest: str, task_type: str, *, max_attempts: int = 3) -> int:
    out = psql(
        "SELECT d3.enqueue_llm_task("
        f"{literal(key)}, {literal(digest)}, {literal(task_type)}, '{{\"fixture\":true}}'::jsonb, "
        f"0::smallint, {max_attempts}::smallint);"
    )
    return int(out)


def claim(
    worker: str, task_type: str, lease_seconds: int = 60
) -> dict[str, object] | None:
    out = psql(
        "SELECT json_build_object('task_id',task_id,'attempts',attempts,"
        "'lease_token',lease_token)::text "
        f"FROM d3.claim_llm_task({literal(worker)}, {literal(task_type)}, {lease_seconds});"
    )
    return json.loads(out) if out else None


def finish(task_id: int, worker: str, token: str) -> str:
    return psql(
        "SELECT d3.finish_llm_task("
        f"{task_id}, {literal(worker)}, {literal(token)}::uuid, '{{\"fixture_ok\":true}}'::jsonb);"
    )


def claim_activity_visible(application_name: str) -> bool:
    for _ in range(100):
        active = psql(
            "SELECT count(*) FROM pg_stat_activity "
            f"WHERE datname={literal(DB_NAME)} AND application_name={literal(application_name)} "
            "AND state='active' AND query LIKE '%pg_sleep%';"
        )
        if int(active) > 0:
            return True
        time.sleep(0.05)
    return False


def gate() -> dict[str, object]:
    global DB_NAME
    run_id = uuid.uuid4().hex
    DB_NAME = f"gfti_w0c_{run_id}"
    subprocess.run(
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
            "gfti_app",
            "-d",
            "gfti",
            "-c",
            f'CREATE DATABASE "{DB_NAME}";',
        ],
        cwd=COMPOSE,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        return run_gate(run_id)
    finally:
        subprocess.run(
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
                "gfti_app",
                "-d",
                "gfti",
                "-c",
                f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE);',
            ],
            cwd=COMPOSE,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )


def run_gate(run_id: str) -> dict[str, object]:
    apply_migration()
    digest = "a" * 64
    key = f"w0c-gate-{run_id}"
    task_type = f"fixture.gate_{run_id}"

    task_id = enqueue(key, digest, task_type)
    duplicate_id = enqueue(key, digest, task_type)
    same_key_idempotent = task_id == duplicate_id
    conflicting_key_rejected = psql_rejects(
        "SELECT d3.enqueue_llm_task("
        f"{literal(key)}, {literal('b' * 64)}, {literal(task_type)}, '{{\"fixture\":true}}'::jsonb, "
        "0::smallint, 3::smallint);",
        "idempotency key reused with a different request hash",
    )

    first_claim = claim(f"worker-{run_id}-a", task_type)
    second_claim_empty = claim(f"worker-{run_id}-b", task_type) is None
    assert first_claim is not None
    claim_matches_fixture = int(first_claim["task_id"]) == task_id
    first_token = str(first_claim["lease_token"])
    first_attempt = int(first_claim["attempts"])

    retry_status = psql(
        "SELECT d3.fail_llm_task("
        f"{task_id}, {literal(f'worker-{run_id}-a')}, {literal(first_token)}::uuid, "
        "'fixture_error', 'deterministic retry check', 0);"
    )
    retry_claim = claim(f"worker-{run_id}-a", task_type)
    assert retry_claim is not None
    retry_token = str(retry_claim["lease_token"])
    retry_attempt = int(retry_claim["attempts"])
    completed = finish(task_id, f"worker-{run_id}-a", retry_token) == "t"
    duplicate_completion_refused = (
        finish(task_id, f"worker-{run_id}-a", retry_token) == "f"
    )

    concurrent_task = enqueue(f"{key}-concurrent", "c" * 64, task_type)
    first_worker = f"worker-{run_id}-c"
    second_worker = f"worker-{run_id}-d"
    first_app = f"w0c-first-{run_id}"
    second_app = f"w0c-second-{run_id}"

    def held_claim_sql(worker: str, seconds: float) -> str:
        return (
            "BEGIN; SELECT json_build_object('task_id',task_id,'attempts',attempts,"
            "'lease_token',lease_token)::text "
            f"FROM d3.claim_llm_task({literal(worker)}, {literal(task_type)}, 60); "
            f"SELECT pg_sleep({seconds}); COMMIT;"
        )

    def launch_claim(
        worker: str, application_name: str, seconds: float
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "-e",
                f"PGAPPNAME={application_name}",
                "postgres",
                "psql",
                "-X",
                "-A",
                "-t",
                "-q",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "gfti_app",
                "-d",
                DB_NAME,
                "-c",
                held_claim_sql(worker, seconds),
            ],
            cwd=COMPOSE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    processes: list[subprocess.Popen[str]] = []

    def observe_claim(
        stdout: str, stderr: str, proc: subprocess.Popen[str]
    ) -> dict[str, object] | None:
        if proc.returncode:
            raise RuntimeError(f"held claim failed: {stderr.strip()}")
        rows = [line for line in stdout.splitlines() if line.startswith("{")]
        return json.loads(rows[0]) if rows else None

    try:
        first_process = launch_claim(first_worker, first_app, 8.0)
        processes.append(first_process)
        first_active = claim_activity_visible(first_app)
        second_process = launch_claim(second_worker, second_app, 4.0)
        processes.append(second_process)
        second_active = claim_activity_visible(second_app)
        first_still_active = claim_activity_visible(first_app)
        overlap_observed = first_active and second_active and first_still_active
        second_stdout, second_stderr = second_process.communicate(timeout=10)
        second_item = observe_claim(second_stdout, second_stderr, second_process)
        first_stdout, first_stderr = first_process.communicate(timeout=10)
        held_item = observe_claim(first_stdout, first_stderr, first_process)
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()

    concurrent_single_claim = (
        overlap_observed
        and held_item is not None
        and int(held_item["task_id"]) == concurrent_task
        and second_item is None
    )
    if held_item is not None:
        finish(concurrent_task, first_worker, str(held_item["lease_token"]))

    stale_task = enqueue(f"{key}-expired", "d" * 64, task_type)
    stale_claim = claim(f"worker-{run_id}-old", task_type, lease_seconds=1)
    assert stale_claim is not None
    old_token = str(stale_claim["lease_token"])
    time.sleep(1.1)
    expired_completion_refused = (
        finish(stale_task, f"worker-{run_id}-old", old_token) == "f"
    )
    replacement = claim(f"worker-{run_id}-new", task_type, lease_seconds=60)
    assert replacement is not None
    replacement_token = str(replacement["lease_token"])
    stale_completion_refused = (
        finish(stale_task, f"worker-{run_id}-old", old_token) == "f"
    )
    replacement_completion = (
        finish(stale_task, f"worker-{run_id}-new", replacement_token) == "t"
    )

    capped_id = enqueue(f"{key}-capped", "e" * 64, task_type, max_attempts=1)
    capped_lease = claim(f"worker-{run_id}-capped", task_type, lease_seconds=1)
    assert capped_lease is not None
    time.sleep(1.1)
    no_reclaim_after_cap = claim(f"worker-{run_id}-capped-next", task_type) is None
    capped_terminal = (
        psql(f"SELECT status FROM d3.llm_tasks WHERE task_id={capped_id};") == "failed"
    )

    event_update_denied = psql_rejects(
        "TRUNCATE d3.llm_task_events;",
        "d3.llm_task_events is append-only",
    )

    checks = {
        "migration_idempotent": True,
        "same_key_idempotent": same_key_idempotent,
        "conflicting_key_rejected": conflicting_key_rejected,
        "claim_matches_own_fixture": claim_matches_fixture,
        "claim_attempt_incremented": first_attempt == 1 and retry_attempt == 2,
        "second_claim_empty_for_live_lease": second_claim_empty,
        "retry_then_complete": retry_status == "queued" and completed,
        "duplicate_completion_refused": duplicate_completion_refused,
        "concurrent_claim_is_single": concurrent_single_claim,
        "concurrent_claim_overlap_observed": overlap_observed,
        "expired_lease_rejected_before_reclaim": expired_completion_refused,
        "reclaim_fences_old_worker": stale_completion_refused
        and replacement_completion,
        "attempt_cap_is_terminal": no_reclaim_after_cap and capped_terminal,
        "event_log_append_only": event_update_denied,
    }
    return {"checks": checks, "ok": all(checks.values()), "fixture_only": True}


if __name__ == "__main__":
    try:
        report = gate()
        print("W0C-LLM-TASKS " + json.dumps(report, sort_keys=True))
        sys.exit(0 if report["ok"] else 1)
    except (
        AssertionError,
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"W0C-LLM-TASKS ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
