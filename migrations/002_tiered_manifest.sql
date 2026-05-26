-- 002_tiered_manifest.sql
-- DD12: per-kind cache TTL.
-- manifest gains a `kind` discriminator so news / changelog / spec entries
-- can be refreshed and evicted on independent schedules.
-- Idempotent: ALTER ... IF [NOT] EXISTS + DROP/RECREATE PK.

ALTER TABLE manifest
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'changelog';

ALTER TABLE manifest DROP CONSTRAINT IF EXISTS manifest_pkey;
ALTER TABLE manifest ADD CONSTRAINT manifest_pkey PRIMARY KEY (package_id, kind);

DROP INDEX IF EXISTS manifest_synced_at_idx;
CREATE INDEX IF NOT EXISTS manifest_kind_synced_idx ON manifest (kind, synced_at);
