# W0c seat worker — deterministic queue consumer (FULL-SYSTEM-BLUEPRINT §13).
#
# Runs ONLY as the least-privilege D3 runtime role and executes ONLY locally
# registered deterministic handlers. No LLM calls here: LLM seats are a later
# W0c increment. The loop is poll-based (seconds scale, control plane); the
# renewal hook exists for future long-running handlers.
#
# Exit codes: 0 = clean stop (claims processed or nothing claimable),
# 3 = operational failure (SQL/infra; the task's lease is left to expire into
# the reclaim path), 4 = identity check failed.

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SEAT_ROLE = "d3_seat_runtime"
DEFAULT_ALLOWED_TASK_TYPES = ("noop",)
PSQL_TIMEOUT = 30
EXIT_OK = 0
EXIT_OPERATIONAL = 3
EXIT_IDENTITY = 4

COMPOSE_DIR = Path(__file__).resolve().parents[2] / "compose"
REPO = COMPOSE_DIR.parent


def _compose_env() -> dict:
    """A-4: `docker compose` interpolates `${VAR:?}`, so `exec` needs the rendered
    config. Read it lazily through the one loader rather than duplicating values."""
    sys.path.insert(0, str(REPO / "python" / "gfti"))
    import gfti_config

    return gfti_config.process_env(gfti_config.load())


COMPOSE_ENV = _compose_env()

HANDLERS: dict[str, Any] = {}
_stats: dict[str, int] = {"claimed": 0, "succeeded": 0, "failed": 0, "lost": 0}
_stopped = False


class OperationalError(RuntimeError):
    """A queue operation could not be executed; stop fail-closed."""


def compose_psql(db: str, sql: str) -> list[str]:
    return [
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
        SEAT_ROLE,
        "-d",
        db,
        "-c",
        sql,
    ]


def psql(db: str, sql: str, what: str) -> str | None:
    try:
        result = subprocess.run(
            compose_psql(db, sql),
            cwd=COMPOSE_DIR,
            env=COMPOSE_ENV,
            check=False,
            capture_output=True,
            text=True,
            timeout=PSQL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise OperationalError(
            f"{what}: psql timed out after {PSQL_TIMEOUT}s"
        ) from None
    if result.returncode:
        raise OperationalError(f"{what}: {result.stderr.strip()}")
    return result.stdout.strip()


def psql_or_exit(db: str, sql: str, what: str) -> str:
    try:
        return psql(db, sql, what) or ""
    except OperationalError as exc:
        print(f"queue-worker-fatal {exc}", file=sys.stderr)
        sys.exit(EXIT_OPERATIONAL)


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def register_default_handlers() -> None:
    HANDLERS["noop"] = lambda payload: ({"noop": True, "echo": payload}, None)


def on_stop(_signum: int, _frame: Any) -> None:
    global _stopped
    _stopped = True


def verify_identity(db: str) -> str:
    identity = psql_or_exit(db, "SELECT current_user;", "identity-check").strip()
    if identity != SEAT_ROLE:
        print(
            f"queue-worker-fatal identity: expected role {SEAT_ROLE}, got {identity!r}",
            file=sys.stderr,
        )
        sys.exit(EXIT_IDENTITY)
    return identity


def claim(
    db: str, worker_id: str, task_type: str, lease_seconds: int
) -> dict[str, Any] | None:
    raw = psql(
        db,
        "SELECT json_build_object("
        "'task_id',task_id,'attempts',attempts,'payload',payload,"
        "'lease_token',lease_token)::text "
        "FROM d3.claim_llm_task("
        + literal(worker_id)
        + ", "
        + literal(task_type)
        + ", "
        + str(lease_seconds)
        + ");",
        "claim",
    )
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise OperationalError(f"claim: malformed claim json {raw[:200]}") from exc


def renew(
    db: str, task_id: int, worker_id: str, token: str, lease_seconds: int
) -> bool:
    return (
        psql(
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
            "renew",
        )
        == "t"
    )


def finish(
    db: str, task_id: int, worker_id: str, token: str, result: dict[str, Any]
) -> bool:
    result_json = json.dumps(result)
    return (
        psql(
            db,
            "SELECT d3.finish_llm_task("
            + str(task_id)
            + ", "
            + literal(worker_id)
            + ", "
            + literal(token)
            + "::uuid, "
            + literal(result_json)
            + "::jsonb);",
            "finish",
        )
        == "t"
    )


def fail(
    db: str,
    task_id: int,
    worker_id: str,
    token: str,
    code: str,
    detail: str,
    retry_delay: int = 0,
) -> str:
    return (
        psql(
            db,
            "SELECT d3.fail_llm_task("
            + str(task_id)
            + ", "
            + literal(worker_id)
            + ", "
            + literal(token)
            + "::uuid, "
            + literal(code)
            + ", "
            + literal(detail)
            + ", "
            + str(retry_delay)
            + ");",
            "fail",
        )
        or ""
    )


def run_handler(allowed: frozenset[str], task_type: str, payload: dict[str, Any]):
    if task_type not in allowed:
        return None, "unsupported_task_type"
    handler_name = payload.get("handler") if isinstance(payload, dict) else None
    if not handler_name:
        return None, "handler_not_registered"
    handler = HANDLERS.get(handler_name)
    if handler is None:
        return None, "handler_not_registered"
    try:
        return handler(payload)
    except Exception as exc:  # noqa: BLE001 - handler isolation: bounded, never silent
        return None, f"handler_error: {type(exc).__name__}: {exc}"[:400]


def worker_loop(args: argparse.Namespace) -> int:
    global _stopped
    _stopped = False
    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGINT, on_stop)
    # Handlers are resolved from the payload's "handler" name; the task-type
    # allowlist (--allow) is the permission gate, not the registry.
    register_default_handlers()
    worker_id = args.worker_id or f"{args.worker_prefix}-{int(time.time())}"
    verify_identity(args.db)

    empty_polls = 0
    while not _stopped:
        if args.max_claims and _stats["claimed"] >= args.max_claims:
            break
        try:
            held = claim(args.db, worker_id, args.task_type, args.lease_seconds)
        except OperationalError as exc:
            print(f"queue-worker-fatal {exc}", file=sys.stderr)
            return EXIT_OPERATIONAL
        if held is None:
            empty_polls += 1
            if args.once or empty_polls >= args.max_empty_polls:
                break
            time.sleep(args.poll_seconds)
            continue
        empty_polls = 0
        _stats["claimed"] += 1
        try:
            if args.renew_seconds and not renew(
                args.db,
                int(held["task_id"]),
                worker_id,
                str(held["lease_token"]),
                args.renew_seconds,
            ):
                # cap refusal or fenced token is not fatal — finish() still
                # fences on its own lease check — but it must be seen.
                _stats["renew_refused"] = _stats.get("renew_refused", 0) + 1
            ok, detail = run_handler(args.allowed, args.task_type, held["payload"])
            task_id = int(held["task_id"])
            token = str(held["lease_token"])
            if detail is None:
                if finish(args.db, task_id, worker_id, token, ok):
                    _stats["succeeded"] += 1
                else:  # fenced or expired; leave for the reclaim path
                    _stats["lost"] += 1
            else:
                outcome = fail(
                    args.db,
                    task_id,
                    worker_id,
                    token,
                    detail,
                    "",
                    args.retry_delay_seconds,
                )
                if outcome in ("queued", "failed"):
                    _stats["failed"] += 1
                else:  # stale_lease: lost without owning the failure
                    _stats["lost"] += 1
        except OperationalError as exc:
            # the held task keeps its lease and is reclaimed by expiry
            print(f"queue-worker-fatal {exc}", file=sys.stderr)
            return EXIT_OPERATIONAL
        if args.once:
            break

    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="W0c deterministic seat worker over the D3 task queue."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--worker-prefix", default="seat")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--task-type", required=True)
    parser.add_argument(
        "--allow",
        default=None,
        help="comma-separated allowed task types; default=(noop). An explicit "
        "empty value allowing nothing (deny-all).",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-claims", type=int, default=0)
    parser.add_argument("--max-empty-polls", type=int, default=3)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--renew-seconds", type=int, default=0)
    parser.add_argument("--retry-delay-seconds", type=int, default=0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.allowed = (
        DEFAULT_ALLOWED_TASK_TYPES
        if args.allow is None
        else frozenset(t.strip() for t in args.allow.split(",") if t.strip())
    )
    code = worker_loop(args)
    print("QUEUE-WORKER " + json.dumps(_stats, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
