-- B1 — data-plane registry: D0 dataset manifests + the D3 gap calendar
-- (FULL-SYSTEM-BLUEPRINT §5 data-store roles, §14 B1 gate).
--
-- Split of responsibilities:
--   d3.datasets — the manifest: what was captured, from where, licensed how,
--                 where the D0 artifact lives, its digest. Mutable registry
--                 (a later full refresh CORRECTS the row — UPDATE allowed).
--   d3.gaps     — the gap calendar (blueprint §14 B-GATE: gaps must be OPEN
--                 AND JUSTIFIED before any agent trains; 'missing' rows with
--                 no justification are hard evidence the dataset is not
--                 training-ready).
--
-- B-GATE contract: d3.v_open_gaps must be empty OR every row justified for
-- a dataset to reach verified = TRUE. The gate enforces this in the
-- training-lock check (no training while open, unjustified gaps exist).

CREATE TABLE IF NOT EXISTS d3.datasets (
    dataset_key   TEXT PRIMARY KEY,          -- e.g. binance/BTCUSDT/1d
    source        TEXT        NOT NULL,      -- endpoint actually used
    license_class TEXT        NOT NULL,      -- e.g. free-attribution / free
    granularity   TEXT        NOT NULL,      -- 1d / 1h / tick...
    expected_from TIMESTAMPTZ NOT NULL,      -- capture-window start
    expected_to   TIMESTAMPTZ NOT NULL,      -- capture-window end
    d0_path       TEXT        NOT NULL,      -- D0 artifact path (parquet)
    rows          BIGINT      NOT NULL,
    sha256        TEXT        NOT NULL,      -- digest of the D0 artifact
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified      BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS d3.gaps (
    gap_id        BIGINT GENERATED ALWAYS AS IDENTITY,
    dataset_key   TEXT NOT NULL REFERENCES d3.datasets(dataset_key),
    window_from   TIMESTAMPTZ NOT NULL,
    window_to     TIMESTAMPTZ NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('missing','late','exchange-closed')),
    status        TEXT NOT NULL CHECK (status IN ('open','closed','justified')),
    justification TEXT NOT NULL DEFAULT '',
    opened_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The B-GATE view: every gap that blocks training (open, unabated).
CREATE OR REPLACE VIEW d3.v_open_gaps AS
SELECT dataset_key, window_from, window_to, kind, status, justification
FROM d3.gaps
WHERE status = 'open' AND justification = '';

-- Datasets that passed the B-GATE rows-verified pin (stable digest,
-- integrity checks green, all gaps justified/closed or none).
CREATE OR REPLACE VIEW d3.v_training_ready AS
SELECT d.dataset_key, d.verified
FROM d3.datasets d
WHERE d.verified = TRUE
  AND NOT EXISTS (
      SELECT 1 FROM d3.gaps g
      WHERE g.dataset_key = d.dataset_key
        AND g.status = 'open' AND g.justification = '');

-- Gate verdict landing (append-only audit of gate runs).
CREATE TABLE IF NOT EXISTS d3.gate_b1 (
    gate     TEXT PRIMARY KEY,
    verdict  TEXT NOT NULL,
    payload  TEXT NOT NULL,
    landed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
