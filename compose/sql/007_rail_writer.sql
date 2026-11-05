-- A-7 — make the rail the only writer (invariant R1 / defect I1).
--
-- Before this migration every collector wrote its own Parquet and then registered
-- itself in d3.datasets. That made `d3.datasets` a claim by the writer about
-- itself: no second party could tell a rail-mediated capture from a direct
-- write, and nothing stopped a new collector from bypassing the rail entirely.
--
-- After it, a dataset row carries its writer and, when the writer is the rail,
-- the rail's own receipt (accepted/rejected counts + the receipt digest). The
-- contract instant below is the cutoff the R1 gate uses: any row captured
-- *after* it must say writer = 'rail'. Legacy rows keep their history — the
-- migration never rewrites what it did not observe.

ALTER TABLE d3.datasets
    ADD COLUMN IF NOT EXISTS writer TEXT NOT NULL DEFAULT 'legacy-collector',
    ADD COLUMN IF NOT EXISTS rail_accepted BIGINT,
    ADD COLUMN IF NOT EXISTS rail_rejected BIGINT,
    ADD COLUMN IF NOT EXISTS rail_receipt_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS rail_receipt_path TEXT;

-- 'rail' is a claim that a receipt exists; 'legacy-collector' is the honest label
-- for everything captured before the contract. A third value would be a writer
-- nobody audited, so the constraint is closed.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'd3_datasets_writer_check'
    ) THEN
        ALTER TABLE d3.datasets
            ADD CONSTRAINT d3_datasets_writer_check
            CHECK (writer IN ('rail', 'legacy-collector'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'd3_datasets_rail_receipt_check'
    ) THEN
        -- A rail-written row without its receipt is exactly the unverifiable
        -- provenance this migration exists to remove.
        ALTER TABLE d3.datasets
            ADD CONSTRAINT d3_datasets_rail_receipt_check
            CHECK (
                writer <> 'rail'
                OR (rail_accepted IS NOT NULL
                    AND rail_rejected IS NOT NULL
                    AND rail_receipt_sha256 IS NOT NULL)
            );
    END IF;
END $$;

-- Append-only ingest receipts. One row per rail batch that landed a dataset;
-- d3.datasets keeps the latest, this table keeps every one (a re-capture is a
-- new fact, not an overwrite).
CREATE TABLE IF NOT EXISTS d3.rail_ingests (
    ingest_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_key   TEXT NOT NULL REFERENCES d3.datasets(dataset_key),
    dataset_rows  BIGINT NOT NULL,
    accepted      BIGINT NOT NULL,
    rejected      BIGINT NOT NULL,
    receipt_path  TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    market_prefix TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The contract instant: one row, written once. `ON CONFLICT DO NOTHING` keeps a
-- re-run of the migrations (G-STORE applies them twice) from moving the cutoff,
-- which would retroactively bless rows written in between.
CREATE TABLE IF NOT EXISTS d3.rail_contract (
    singleton  BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    note       TEXT NOT NULL DEFAULT ''
);
INSERT INTO d3.rail_contract (singleton, note)
VALUES (TRUE, 'A-7: every dataset captured after this instant must be rail-written')
ON CONFLICT (singleton) DO NOTHING;

-- The R1 view: rows that claim a writer they cannot prove. `legacy-collector`
-- after the cutoff is the violation; it is a view, not a trigger, because a
-- refusal at INSERT time would also block an honest historical backfill.
--
-- A declared exception is a recorded reason, not a bypass: `d3.legacy_writes`
-- is append-only, so the debt is visible and countable, and it must be newer
-- than the capture it explains. An undeclared direct write is the violation.
CREATE TABLE IF NOT EXISTS d3.legacy_writes (
    write_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_key  TEXT NOT NULL REFERENCES d3.datasets(dataset_key),
    collector    TEXT NOT NULL,
    reason       TEXT NOT NULL CHECK (length(reason) >= 20),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW d3.v_r1_violations AS
SELECT d.dataset_key, d.writer, d.captured_at, c.applied_at
FROM d3.datasets d
CROSS JOIN d3.rail_contract c
WHERE d.writer <> 'rail'
  AND d.captured_at > c.applied_at
  AND NOT EXISTS (
      SELECT 1 FROM d3.legacy_writes w
      WHERE w.dataset_key = d.dataset_key AND w.created_at >= d.captured_at
  );

-- The audit surface: how much of the registry is still offline from the rail.
CREATE OR REPLACE VIEW d3.v_r1_writer_mix AS
SELECT writer, count(*) AS datasets, max(captured_at) AS last_capture
FROM d3.datasets
GROUP BY writer;
