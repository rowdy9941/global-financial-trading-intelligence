-- Add explicit volume semantics and manifest schema version to D3 datasets.
-- Existing rows receive compatibility defaults; this migration does not
-- rewrite their source, coverage, hashes, or other historical provenance.
ALTER TABLE d3.datasets
    ADD COLUMN IF NOT EXISTS volume_kind TEXT,
    ADD COLUMN IF NOT EXISTS volume_unit TEXT,
    ADD COLUMN IF NOT EXISTS volume_available BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS schema_version TEXT NOT NULL DEFAULT '1';
