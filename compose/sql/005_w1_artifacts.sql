-- W1: D5 artifact registry (blueprint §2 store ladder: "filesystem + Postgres
-- index, R3 rule — frozen formulas / model artifacts, feature-hash + schema-
-- version signed, versioned, rollback").
--
-- The artifact FILE lives on the filesystem under d5/<artifact_id>.json with
-- the training payload inside; this table is the append-only INDEX that pins:
--   * sha256 of the exact file bytes (rollback = load an older row)
--   * dataset_key + dataset sha256 the artifact was TRAINED on (D3 lineage —
--     an artifact is only any good against the data it saw)
--   * feature_hash (E2 must refuse to evaluate an artifact whose hash does
--     not match its own computed features — same-E1-code rule, blueprint §3)
--   * HMAC-SHA256 signature over the canonical row (same scheme as D4 fills;
--     dev runs keep per-run ephemeral secrets, owner production secrets come
--     from the owner-side secret store)
--   * walk-forward evidence: OOS Sharpe + DSR recorded AS-IS (no promotion
--     ladder bypass: E6-PAPER/SHADOW promotion stays owner-gated, W3)
--
-- Append-only: UPDATE/DELETE denied at the DB role level, same pattern as
-- d4.fills (001_d3_d4_init.sql).

CREATE SCHEMA IF NOT EXISTS d5;

CREATE TABLE IF NOT EXISTS d5.artifacts (
    artifact_id     TEXT PRIMARY KEY,          -- e.g. sma-cross-BTCUSD-v1
    kind            TEXT NOT NULL DEFAULT 'rule',
    status          TEXT NOT NULL DEFAULT 'candidate'
                    CHECK (status IN ('candidate','frozen','retired')),
    discriminator   TEXT NOT NULL,             -- formula/deterministic rule name
    params          JSONB NOT NULL,            -- frozen parameters (E2 constants)
    feature_hash    TEXT NOT NULL,             -- pin: what features it consumes
    dataset_key     TEXT NOT NULL,             -- training data (D3 lineage)
    dataset_sha256  TEXT NOT NULL,             -- exact D0 bytes it saw
    wf_oos_sharpe   REAL NOT NULL,             -- walk-forward OOS Sharpe (as-is)
    wf_dsr          REAL NOT NULL,             -- deflated Sharpe (as-is)
    wf_folds        INT  NOT NULL,
    signer_role     TEXT NOT NULL REFERENCES d3.roles(role_name),
    sig             TEXT NOT NULL,             -- HMAC over canonical string
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS d5.gate_w1 (
    gate        TEXT PRIMARY KEY,
    verdict     TEXT NOT NULL,
    evidence    TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
DECLARE t TEXT;
BEGIN
    FOR t IN SELECT unnest(ARRAY['artifacts','gate_w1'])
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS gta_%s ON d5.%I', t, t);
        EXECUTE format('CREATE TRIGGER gta_%s BEFORE UPDATE OR DELETE ON d5.%I
                        FOR EACH STATEMENT EXECUTE FUNCTION d4.deny_mutation()', t, t);
    END LOOP;
END $$;
