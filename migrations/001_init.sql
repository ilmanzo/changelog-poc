-- 001_init.sql
-- Initial schema: packages, changelog_entries, specs, spec_sections, news, openqa_tests,
-- deps, manifest. Applied once via schema_migrations tracking in src/db.py.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================================
-- packages: identity for every package across distros
-- ============================================================================
CREATE TABLE IF NOT EXISTS packages (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    distro          TEXT NOT NULL,              -- 'opensuse' | 'fedora' | 'local'
    latest_version  TEXT,
    upstream_url    TEXT,
    UNIQUE(name, distro)
);
CREATE INDEX IF NOT EXISTS packages_name_idx ON packages (name);

-- ============================================================================
-- changelog_entries: one row per parsed entry. Content-addressed UUID prevents
-- duplicate inserts when the same .changes block is fetched from multiple
-- sources (OBS + Gitea mirror).
-- ============================================================================
CREATE TABLE IF NOT EXISTS changelog_entries (
    id              UUID PRIMARY KEY,
    package_id      BIGINT NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    version         TEXT,
    author          TEXT,
    entry_date      TIMESTAMPTZ,
    content         TEXT NOT NULL,
    source_name     TEXT NOT NULL,              -- 'rpm' | 'obs' | 'gitea' | 'git'
    tsv             TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED,
    embedding       VECTOR(384)
);
CREATE INDEX IF NOT EXISTS changelog_entries_pkg_idx     ON changelog_entries (package_id);
CREATE INDEX IF NOT EXISTS changelog_entries_date_idx    ON changelog_entries (entry_date DESC);
CREATE INDEX IF NOT EXISTS changelog_entries_version_idx ON changelog_entries (package_id, version);
CREATE INDEX IF NOT EXISTS changelog_entries_tsv_idx     ON changelog_entries USING GIN (tsv);
CREATE INDEX IF NOT EXISTS changelog_entries_embedding_idx
    ON changelog_entries USING hnsw (embedding vector_cosine_ops);

-- ============================================================================
-- specs: raw .spec file content per (package, source).
-- ============================================================================
CREATE TABLE IF NOT EXISTS specs (
    id              BIGSERIAL PRIMARY KEY,
    package_id      BIGINT NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    source          TEXT NOT NULL,              -- 'fedora' | 'opensuse'
    version         TEXT,
    content         TEXT NOT NULL,
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(package_id, source)
);
CREATE INDEX IF NOT EXISTS specs_pkg_idx ON specs (package_id);
CREATE INDEX IF NOT EXISTS specs_fts_idx ON specs USING GIN (to_tsvector('english', content));

-- ============================================================================
-- spec_sections: AST-parsed sections (header/%prep/%build/...) of each spec,
-- chunked at 1000 chars with 100-char overlap, embedded per chunk.
-- ============================================================================
CREATE TABLE IF NOT EXISTS spec_sections (
    id              BIGSERIAL PRIMARY KEY,
    spec_id         BIGINT NOT NULL REFERENCES specs(id) ON DELETE CASCADE,
    section_name    TEXT NOT NULL,
    chunk_index     INT NOT NULL DEFAULT 0,
    content         TEXT NOT NULL,
    embedding       VECTOR(384)
);
CREATE INDEX IF NOT EXISTS spec_sections_spec_idx ON spec_sections (spec_id);
CREATE INDEX IF NOT EXISTS spec_sections_embedding_idx
    ON spec_sections USING hnsw (embedding vector_cosine_ops);

-- ============================================================================
-- news: Fedora Bodhi updates + openSUSE RSS items.
-- ============================================================================
CREATE TABLE IF NOT EXISTS news (
    id              BIGSERIAL PRIMARY KEY,
    package_id      BIGINT REFERENCES packages(id) ON DELETE SET NULL,
    title           TEXT NOT NULL,
    source          TEXT NOT NULL,              -- 'bodhi' | 'opensuse-rss'
    item_type       TEXT,                       -- 'security' | 'update' | ...
    importance      TEXT,                       -- 'CRITICAL' | 'Routine' | 'Security'
    content         TEXT,
    url             TEXT,
    item_date       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(title, source)
);
CREATE INDEX IF NOT EXISTS news_pkg_idx  ON news (package_id);
CREATE INDEX IF NOT EXISTS news_date_idx ON news (item_date DESC);

-- ============================================================================
-- openqa_tests: mapping of `# Package:` headers in os-autoinst-distri-opensuse
-- .pm files to package names.
-- ============================================================================
CREATE TABLE IF NOT EXISTS openqa_tests (
    id              BIGSERIAL PRIMARY KEY,
    package_id      BIGINT NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    test_path       TEXT NOT NULL,
    summary         TEXT,
    UNIQUE(package_id, test_path)
);
CREATE INDEX IF NOT EXISTS openqa_tests_pkg_idx ON openqa_tests (package_id);

-- ============================================================================
-- deps: forward + reverse dependency graph.
-- ============================================================================
CREATE TABLE IF NOT EXISTS deps (
    package_id      BIGINT NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    dep_name        TEXT NOT NULL,
    kind            TEXT NOT NULL,              -- 'requires' | 'provides'
    PRIMARY KEY (package_id, dep_name, kind)
);
CREATE INDEX IF NOT EXISTS deps_dep_name_idx ON deps (dep_name);
CREATE INDEX IF NOT EXISTS deps_kind_idx     ON deps (kind);

-- ============================================================================
-- manifest: tracks last full sync time per package for TTL-based eviction.
-- ============================================================================
CREATE TABLE IF NOT EXISTS manifest (
    package_id      BIGINT PRIMARY KEY REFERENCES packages(id) ON DELETE CASCADE,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS manifest_synced_at_idx ON manifest (synced_at);
