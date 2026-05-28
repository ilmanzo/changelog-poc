-- 005_embedding_versioning.sql
-- Track which embedding model generated each vector, enabling future model swaps.
-- Idempotent: ADD COLUMN IF NOT EXISTS.
--
-- When the active model changes (EMBEDDING_MODEL env var), new rows get the new
-- model name; old rows keep 'BAAI/bge-small-en-v1.5'. Semantic search serves
-- mixed results until the worker re-embeds stale rows (graceful degradation).
-- A future migration adds a second vector column when a model with a different
-- dimension is introduced.

ALTER TABLE changelog_entries
    ADD COLUMN IF NOT EXISTS embedding_model TEXT NOT NULL DEFAULT 'BAAI/bge-small-en-v1.5';

ALTER TABLE spec_sections
    ADD COLUMN IF NOT EXISTS embedding_model TEXT NOT NULL DEFAULT 'BAAI/bge-small-en-v1.5';
