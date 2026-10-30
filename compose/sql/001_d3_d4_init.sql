-- W0b — D3 registry + D4 append-only ledger + kill-cascade state
-- (FULL-SYSTEM-BLUEPRINT §2.2, §6; schema bootstrap for the compose Postgres).
--
-- Immutability contract: D4 tables accept INSERT only. UPDATE and DELETE are
-- denied at the DATABASE level (BEFORE triggers), not by convention. The
-- ledger does not trust its own process: a future bug or role change cannot
-- delete fills. Corrections are appended as new rows with explicit reasons.
--
-- Kill-cascade contract (§6): kill state = latest row of d4.kill_events by
-- (engaged_at DESC, id DESC); engine must fail CLOSED on read failure.

CREATE SCHEMA IF NOT EXISTS d3;
CREATE SCHEMA IF NOT EXISTS d4;

-- D3 — reference/registry data (versioned, mutable by build role).
CREATE TABLE IF NOT EXISTS d3.symbols (
    symbol        TEXT PRIMARY KEY,
    rights_class  TEXT        NOT NULL
                  CHECK (rights_class IN ('public','public_research_only','walled')),
    enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
    valid_ts      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS d3.roles (
    role_name     TEXT PRIMARY KEY,
    canon_claim   BOOLEAN NOT NULL DEFAULT FALSE, -- L3 claim role (owner-only)
    secret_hash   TEXT    NOT NULL                -- sha256 hex of the role secret; never the secret
);

-- D4 — append-only ledger.
CREATE TABLE IF NOT EXISTS d4.fills (
    fill_id       BIGINT GENERATED ALWAYS AS IDENTITY,
    ts_est        TIMESTAMPTZ NOT NULL DEFAULT now(),
    source        TEXT        NOT NULL,              -- engine/cell id
    symbol        TEXT        NOT NULL,
    side          TEXT        NOT NULL CHECK (side IN ('buy','sell')),
    qty           NUMERIC(38,10) NOT NULL CHECK (qty  > 0),
    px            NUMERIC(38,10) NOT NULL CHECK (px   > 0),
    signer_role   TEXT        NOT NULL REFERENCES d3.roles(role_name),
    sig           TEXT        NOT NULL                -- HMAC-SHA256 hex over the canonical string
);

CREATE TABLE IF NOT EXISTS d4.kill_events (
    evt_id        BIGINT GENERATED ALWAYS AS IDENTITY,
    engaged_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    level         TEXT        NOT NULL CHECK (level IN ('L0','L1','L2','L3','L4')),
    engaged       BOOLEAN     NOT NULL,               -- TRUE = engines must refuse all risk
    source        TEXT        NOT NULL,               -- watchdog | owner | auto-check
    reason        TEXT        NOT NULL
);

CREATE TABLE IF NOT EXISTS d4.approvals (
    appr_id       BIGINT GENERATED ALWAYS AS IDENTITY,
    ts_est        TIMESTAMPTZ NOT NULL DEFAULT now(),
    ticket        TEXT        NOT NULL,               -- approval target (e.g. a trade plan)
    level         TEXT        NOT NULL CHECK (level IN ('L0','L1','L2','L3','L4')),
    role_name     TEXT        NOT NULL REFERENCES d3.roles(role_name),
    decision      TEXT        NOT NULL CHECK (decision IN ('grant','deny')),
    reason        TEXT        NOT NULL
);

CREATE TABLE IF NOT EXISTS d4.pnl_recon (
    recon_id      BIGINT GENERATED ALWAYS AS IDENTITY,
    ts_est        TIMESTAMPTZ NOT NULL DEFAULT now(),
    window_from   TIMESTAMPTZ NOT NULL,
    window_to     TIMESTAMPTZ NOT NULL,
    gross_pnl     NUMERIC(38,10) NOT NULL,             -- zp? signed (validated from fills)
    n_fills       BIGINT      NOT NULL,
    recon_verdict TEXT        NOT NULL CHECK (recon_verdict IN ('match','mismatch')),
    payload       TEXT        NOT NULL                 -- canonical artifacts digest kept for audit
);

-- Append-only enforcement: UPDATE or DELETE on any d4 table aborts the
-- transaction with an exception (fail-closed by construction).
CREATE OR REPLACE FUNCTION d4.deny_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'd4 append-only: % on % denied (audit ledger is immutable)',
        TG_OP, TG_TABLE_NAME;
END $$;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['fills','kill_events','approvals','pnl_recon'] LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS gta_%s ON d4.%I',
            t, t);
        EXECUTE format(
            'CREATE TRIGGER gta_%s BEFORE UPDATE OR DELETE ON d4.%I FOR EACH STATEMENT EXECUTE FUNCTION d4.deny_mutation()',
            t, t
        );
    END LOOP;
END $$;

-- Kill state = latest event (append log gives full history).
CREATE OR REPLACE VIEW d4.kill_state AS
SELECT engaged, level, reason, engaged_at, source
FROM d4.kill_events
ORDER BY engaged_at DESC, evt_id DESC
LIMIT 1;

-- p99 numbers + verification counts land here for the W0b gate doc.
CREATE TABLE IF NOT EXISTS d3.gate_w0b (
    gate          TEXT PRIMARY KEY,
    ran_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    verdict       TEXT        NOT NULL,
    payload       TEXT        NOT NULL
);
