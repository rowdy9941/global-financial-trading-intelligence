-- W0c increment 1 — durable LLM-seat task queue in D3.
--
-- This is control-plane state only. It is not an order queue, and seats must
-- not receive the Compose bootstrap credential. Runtime-role ownership and
-- least-privilege grants are deliberately deferred until a seat runtime exists;
-- these invoker functions are not exposed to PUBLIC.
-- Payload/result JSON is expected to contain no credentials or raw secrets.
-- The queue is control-plane work only, never an order queue.
--
-- Trust boundary (increment 2): all seat processes authenticate as the ONE
-- d3_seat_runtime role, so a seat can read any task's lease_token via the
-- table SELECT it needs for payload lookup, and could present another
-- worker's token. This is a deliberate, documented same-domain trust model —
-- per-seat credentials + per-lease secret fencing are the next increment,
-- not security debt hidden here. The renewal cap is the concrete anti-hunger
-- defense until then.

CREATE SCHEMA IF NOT EXISTS d3;

CREATE TABLE IF NOT EXISTS d3.llm_tasks (
    task_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key  TEXT        NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    request_sha256   TEXT        NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    task_type        TEXT        NOT NULL CHECK (task_type ~ '^[a-z][a-z0-9_.-]{0,63}$'),
    payload          JSONB       NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    status           TEXT        NOT NULL DEFAULT 'queued'
                                 CHECK (status IN ('queued','leased','succeeded','failed','cancelled')),
    priority         SMALLINT    NOT NULL DEFAULT 0 CHECK (priority BETWEEN -100 AND 100),
    attempts         SMALLINT    NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts     SMALLINT    NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 20),
    available_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_owner      TEXT,
    lease_token      UUID,
    lease_expires_at TIMESTAMPTZ,
    lease_renewals   SMALLINT    NOT NULL DEFAULT 0
                                 CHECK (lease_renewals BETWEEN 0 AND 100),
    result           JSONB,
    error_code       TEXT CHECK (error_code IS NULL OR length(error_code) <= 64),
    error_detail     TEXT CHECK (error_detail IS NULL OR length(error_detail) <= 512),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ,
    CHECK (attempts <= max_attempts),
    CHECK (
        (status = 'leased' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL
         AND lease_expires_at IS NOT NULL AND completed_at IS NULL)
        OR
        (status <> 'leased' AND lease_owner IS NULL AND lease_token IS NULL
         AND lease_expires_at IS NULL)
    ),
    CHECK ((status IN ('succeeded','failed','cancelled')) = (completed_at IS NOT NULL)),
    CHECK (status <> 'succeeded' OR result IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS llm_tasks_claimable_idx
    ON d3.llm_tasks (priority DESC, available_at, task_id)
    WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS llm_tasks_expired_lease_idx
    ON d3.llm_tasks (lease_expires_at, task_id)
    WHERE status = 'leased';

CREATE TABLE IF NOT EXISTS d3.llm_task_events (
    event_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id     BIGINT      NOT NULL REFERENCES d3.llm_tasks(task_id),
    event_type  TEXT        NOT NULL CHECK (event_type IN
                ('enqueued','leased','lease_expired','succeeded','retry_scheduled','failed')),
    worker_id   TEXT,
    attempt     SMALLINT    NOT NULL CHECK (attempt >= 0),
    detail      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_task_events_task_idx
    ON d3.llm_task_events (task_id, event_id);

CREATE OR REPLACE FUNCTION d3.deny_llm_task_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'd3.llm_task_events is append-only: % denied', TG_OP;
END $$;

DROP TRIGGER IF EXISTS llm_task_events_append_only ON d3.llm_task_events;
CREATE TRIGGER llm_task_events_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON d3.llm_task_events
    FOR EACH STATEMENT EXECUTE FUNCTION d3.deny_llm_task_event_mutation();

CREATE OR REPLACE FUNCTION d3.enqueue_llm_task(
    p_idempotency_key TEXT,
    p_request_sha256 TEXT,
    p_task_type TEXT,
    p_payload JSONB,
    p_priority SMALLINT DEFAULT 0,
    p_max_attempts SMALLINT DEFAULT 3
) RETURNS BIGINT LANGUAGE plpgsql
SET search_path = pg_catalog, d3 AS $$
DECLARE
    v_task_id BIGINT;
    v_existing_hash TEXT;
    v_existing_type TEXT;
    v_existing_payload JSONB;
    v_existing_priority SMALLINT;
    v_existing_max_attempts SMALLINT;
BEGIN
    INSERT INTO d3.llm_tasks
        (idempotency_key, request_sha256, task_type, payload, priority, max_attempts)
    VALUES
        (p_idempotency_key, p_request_sha256, p_task_type, p_payload, p_priority, p_max_attempts)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING task_id INTO v_task_id;

    IF v_task_id IS NULL THEN
        SELECT task_id, request_sha256, task_type, payload, priority, max_attempts
        INTO v_task_id, v_existing_hash, v_existing_type, v_existing_payload,
             v_existing_priority, v_existing_max_attempts
        FROM d3.llm_tasks WHERE idempotency_key = p_idempotency_key;
        IF v_existing_hash IS DISTINCT FROM p_request_sha256
           OR v_existing_type IS DISTINCT FROM p_task_type
           OR v_existing_payload IS DISTINCT FROM p_payload
           OR v_existing_priority IS DISTINCT FROM p_priority
           OR v_existing_max_attempts IS DISTINCT FROM p_max_attempts THEN
            RAISE EXCEPTION 'idempotency key reused with a different request hash'
                USING ERRCODE = '23505';
        END IF;
        RETURN v_task_id;
    END IF;

    INSERT INTO d3.llm_task_events (task_id, event_type, attempt)
    VALUES (v_task_id, 'enqueued', 0);
    RETURN v_task_id;
END $$;

CREATE OR REPLACE FUNCTION d3.claim_llm_task(
    p_worker_id TEXT,
    p_task_type TEXT,
    p_lease_seconds INTEGER DEFAULT 60
) RETURNS SETOF d3.llm_tasks LANGUAGE plpgsql
SET search_path = pg_catalog, d3 AS $$
DECLARE
    v_task d3.llm_tasks%ROWTYPE;
    v_task_id BIGINT;
BEGIN
    IF p_worker_id IS NULL OR length(p_worker_id) NOT BETWEEN 1 AND 128
       OR p_task_type IS NULL OR p_task_type !~ '^[a-z][a-z0-9_.-]{0,63}$'
       OR p_lease_seconds NOT BETWEEN 1 AND 3600 THEN
        RAISE EXCEPTION 'invalid worker id, task type, or lease duration';
    END IF;

    -- Expired attempts at their cap become terminal; other expired leases
    -- return to queued state and can be reclaimed with a new fencing token.
    FOR v_task IN
        SELECT * FROM d3.llm_tasks
        WHERE status = 'leased' AND task_type = p_task_type
          AND lease_expires_at <= clock_timestamp()
        ORDER BY task_id FOR UPDATE SKIP LOCKED
    LOOP
        IF v_task.attempts >= v_task.max_attempts THEN
            UPDATE d3.llm_tasks SET status = 'failed', completed_at = now(),
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                error_code = 'lease_expired', error_detail = 'lease expired at attempt limit',
                updated_at = now()
            WHERE task_id = v_task.task_id;
            INSERT INTO d3.llm_task_events (task_id, event_type, worker_id, attempt, detail)
            VALUES (v_task.task_id, 'failed', v_task.lease_owner, v_task.attempts,
                    '{"reason":"lease_expired_at_attempt_limit"}'::jsonb);
        ELSE
            UPDATE d3.llm_tasks SET status = 'queued', available_at = now(),
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                error_code = 'lease_expired', error_detail = 'lease expired; task made claimable',
                updated_at = now()
            WHERE task_id = v_task.task_id;
            INSERT INTO d3.llm_task_events (task_id, event_type, worker_id, attempt, detail)
            VALUES (v_task.task_id, 'lease_expired', v_task.lease_owner, v_task.attempts,
                    '{}'::jsonb);
        END IF;
    END LOOP;

    SELECT task_id INTO v_task_id FROM d3.llm_tasks
    WHERE status = 'queued' AND task_type = p_task_type
      AND available_at <= clock_timestamp() AND attempts < max_attempts
    ORDER BY priority DESC, available_at, task_id
    LIMIT 1 FOR UPDATE SKIP LOCKED;

    IF v_task_id IS NULL THEN
        RETURN;
    END IF;

    UPDATE d3.llm_tasks SET status = 'leased', attempts = attempts + 1,
        lease_owner = p_worker_id, lease_token = gen_random_uuid(),
        lease_expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds),
        lease_renewals = 0,
        error_code = NULL, error_detail = NULL, updated_at = now()
    WHERE task_id = v_task_id
    RETURNING * INTO v_task;

    INSERT INTO d3.llm_task_events (task_id, event_type, worker_id, attempt)
    VALUES (v_task.task_id, 'leased', p_worker_id, v_task.attempts);
    RETURN NEXT v_task;
END $$;

CREATE OR REPLACE FUNCTION d3.finish_llm_task(
    p_task_id BIGINT,
    p_worker_id TEXT,
    p_lease_token UUID,
    p_result JSONB
) RETURNS BOOLEAN LANGUAGE plpgsql
SET search_path = pg_catalog, d3 AS $$
DECLARE
    v_attempt SMALLINT;
BEGIN
    IF p_result IS NULL OR jsonb_typeof(p_result) <> 'object' THEN
        RAISE EXCEPTION 'task result must be a JSON object';
    END IF;
    UPDATE d3.llm_tasks SET status = 'succeeded', result = p_result,
        lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
        completed_at = now(), updated_at = now()
    WHERE task_id = p_task_id AND status = 'leased' AND lease_owner = p_worker_id
      AND lease_token = p_lease_token AND lease_expires_at > clock_timestamp()
    RETURNING attempts INTO v_attempt;
    IF NOT FOUND THEN RETURN FALSE; END IF;
    INSERT INTO d3.llm_task_events (task_id, event_type, worker_id, attempt)
    VALUES (p_task_id, 'succeeded', p_worker_id, v_attempt);
    RETURN TRUE;
END $$;

CREATE OR REPLACE FUNCTION d3.fail_llm_task(
    p_task_id BIGINT,
    p_worker_id TEXT,
    p_lease_token UUID,
    p_error_code TEXT,
    p_error_detail TEXT DEFAULT '',
    p_retry_delay_seconds INTEGER DEFAULT 0
) RETURNS TEXT LANGUAGE plpgsql
SET search_path = pg_catalog, d3 AS $$
DECLARE
    v_task d3.llm_tasks%ROWTYPE;
    v_status TEXT;
BEGIN
    IF p_error_code IS NULL OR length(btrim(p_error_code)) = 0 THEN
        RAISE EXCEPTION 'error code must be non-empty';
    END IF;
    IF p_retry_delay_seconds NOT BETWEEN 0 AND 86400 THEN
        RAISE EXCEPTION 'retry delay must be between 0 and 86400 seconds';
    END IF;
    UPDATE d3.llm_tasks SET
        status = CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
        available_at = CASE WHEN attempts < max_attempts
            THEN now() + make_interval(secs => p_retry_delay_seconds) ELSE available_at END,
        lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
        error_code = left(p_error_code, 64), error_detail = left(p_error_detail, 512),
        completed_at = CASE WHEN attempts < max_attempts THEN NULL ELSE now() END,
        updated_at = now()
    WHERE task_id = p_task_id AND status = 'leased' AND lease_owner = p_worker_id
      AND lease_token = p_lease_token AND lease_expires_at > clock_timestamp()
    RETURNING * INTO v_task;
    IF NOT FOUND THEN RETURN 'stale_lease'; END IF;
    v_status := v_task.status;
    INSERT INTO d3.llm_task_events (task_id, event_type, worker_id, attempt, detail)
    VALUES (p_task_id,
        CASE WHEN v_status = 'queued' THEN 'retry_scheduled' ELSE 'failed' END,
        p_worker_id, v_task.attempts,
        jsonb_build_object('error_code', v_task.error_code,
                           'retry_delay_seconds', p_retry_delay_seconds));
    RETURN v_status;
END $$;

CREATE OR REPLACE FUNCTION d3.renew_llm_task(
    p_task_id BIGINT,
    p_worker_id TEXT,
    p_lease_token UUID,
    p_lease_seconds INTEGER
) RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, d3 AS $$
BEGIN
    IF p_lease_seconds NOT BETWEEN 1 AND 3600 THEN
        RAISE EXCEPTION 'lease duration must be between 1 and 3600 seconds';
    END IF;
    UPDATE d3.llm_tasks SET
        lease_expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds),
        lease_renewals = lease_renewals + 1,
        updated_at = now()
    WHERE task_id = p_task_id AND status = 'leased' AND lease_owner = p_worker_id
      AND lease_token = p_lease_token AND lease_expires_at > clock_timestamp()
      AND lease_renewals < 20;   -- bounded: a renew loop cannot hold a task forever;
    RETURN FOUND;                -- the lease then expires into the reclaim path
END $$;

COMMENT ON TABLE d3.llm_tasks IS
    'Mutable durable control-plane work queue; never an order queue. Do not persist secrets in payload/result.';
COMMENT ON TABLE d3.llm_task_events IS
    'Append-only D3 event history for durable LLM task lifecycle transitions.';

REVOKE ALL ON FUNCTION d3.enqueue_llm_task(TEXT,TEXT,TEXT,JSONB,SMALLINT,SMALLINT) FROM PUBLIC;
REVOKE ALL ON FUNCTION d3.claim_llm_task(TEXT,TEXT,INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION d3.finish_llm_task(BIGINT,TEXT,UUID,JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION d3.fail_llm_task(BIGINT,TEXT,UUID,TEXT,TEXT,INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION d3.renew_llm_task(BIGINT,TEXT,UUID,INTEGER) FROM PUBLIC;

-- W0c increment 2 — least-privilege queue ownership and runtime role.
--
-- gfti_queue_owner owns the queue tables and functions exclusively so the
-- SECURITY DEFINER functions do not run with the Compose bootstrap superuser's
-- authority. d3_seat_runtime is the only role seats ever authenticate as; it
-- gets function EXECUTE and read-only queue SELECT — no direct DML, no other
-- d3 or d4 privileges (no grants exist by default; denials are verified by the
-- W0c seats gate, not assumed here). The seat role has no password yet, so
-- remote authentication fails closed; the dev path is the container's local
-- unix socket, and production credentials are provisioned at deploy time.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gfti_queue_owner') THEN
        CREATE ROLE gfti_queue_owner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'd3_seat_runtime') THEN
        CREATE ROLE d3_seat_runtime LOGIN;   -- no password: remote auth fails closed
    END IF;
END $$;

GRANT USAGE ON SCHEMA d3 TO gfti_queue_owner, d3_seat_runtime;

ALTER TABLE d3.llm_tasks OWNER TO gfti_queue_owner;
ALTER TABLE d3.llm_task_events OWNER TO gfti_queue_owner;
ALTER SEQUENCE d3.llm_tasks_task_id_seq OWNER TO gfti_queue_owner;
ALTER SEQUENCE d3.llm_task_events_event_id_seq OWNER TO gfti_queue_owner;

ALTER FUNCTION d3.enqueue_llm_task(TEXT,TEXT,TEXT,JSONB,SMALLINT,SMALLINT)
    OWNER TO gfti_queue_owner;
ALTER FUNCTION d3.claim_llm_task(TEXT,TEXT,INTEGER)
    OWNER TO gfti_queue_owner;
ALTER FUNCTION d3.finish_llm_task(BIGINT,TEXT,UUID,JSONB)
    OWNER TO gfti_queue_owner;
ALTER FUNCTION d3.fail_llm_task(BIGINT,TEXT,UUID,TEXT,TEXT,INTEGER)
    OWNER TO gfti_queue_owner;
ALTER FUNCTION d3.renew_llm_task(BIGINT,TEXT,UUID,INTEGER)
    OWNER TO gfti_queue_owner;

-- SECURITY DEFINER runs these as gfti_queue_owner, never as the caller; the
-- force is explicit so a re-apply cannot silently leave the bootstrap role
-- owning function authority.
ALTER FUNCTION d3.enqueue_llm_task(TEXT,TEXT,TEXT,JSONB,SMALLINT,SMALLINT) SECURITY DEFINER;
ALTER FUNCTION d3.claim_llm_task(TEXT,TEXT,INTEGER) SECURITY DEFINER;
ALTER FUNCTION d3.finish_llm_task(BIGINT,TEXT,UUID,JSONB) SECURITY DEFINER;
ALTER FUNCTION d3.fail_llm_task(BIGINT,TEXT,UUID,TEXT,TEXT,INTEGER) SECURITY DEFINER;
ALTER FUNCTION d3.renew_llm_task(BIGINT,TEXT,UUID,INTEGER) SECURITY DEFINER;

GRANT EXECUTE ON FUNCTION d3.enqueue_llm_task(TEXT,TEXT,TEXT,JSONB,SMALLINT,SMALLINT)
    TO d3_seat_runtime;
GRANT EXECUTE ON FUNCTION d3.claim_llm_task(TEXT,TEXT,INTEGER) TO d3_seat_runtime;
GRANT EXECUTE ON FUNCTION d3.finish_llm_task(BIGINT,TEXT,UUID,JSONB) TO d3_seat_runtime;
GRANT EXECUTE ON FUNCTION d3.fail_llm_task(BIGINT,TEXT,UUID,TEXT,TEXT,INTEGER) TO d3_seat_runtime;
GRANT EXECUTE ON FUNCTION d3.renew_llm_task(BIGINT,TEXT,UUID,INTEGER) TO d3_seat_runtime;
GRANT SELECT ON d3.llm_tasks TO d3_seat_runtime;
