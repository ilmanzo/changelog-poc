# Database schema

PostgreSQL 17 + extensions `vector` (pgvector) and `pg_trgm`. Source of
truth is `migrations/001_init.sql`, applied idempotently by
`src.db.Database.connect()` on every startup. All inserts go through
`src/db.py` -- no other module talks to Postgres directly.

```
                          packages
                         (id PK)
                            |
       +--------+-----------+-----------+---------+----------+
       |        |           |           |         |          |
   changelog   specs    openqa_tests  deps    manifest    news
   _entries     |                                          (FK ON DELETE SET NULL)
                |
         spec_sections
```

All child tables FK to `packages.id ON DELETE CASCADE` except `news`,
which uses `ON DELETE SET NULL` because news items can outlive a package
row (e.g. removed from a distro).

---

## Extensions

| Extension | Used by |
|---|---|
| `vector` | `changelog_entries.embedding`, `spec_sections.embedding`; HNSW indexes |
| `pg_trgm` | Available for future trigram-based fuzzy search; not yet wired into a tool |

Embedding dimension is fixed at **384** (matches the default fastembed
model in `src/embedder.py`). Migrating to a different model requires a
schema change and re-embed -- tracked under plan item DD8/N4.

---

## Tables

### `packages` -- canonical identity

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL` PK | referenced by every other table |
| `name` | `TEXT NOT NULL` | RPM package name; validated against `^[a-zA-Z0-9_\-\.+]+$` |
| `distro` | `TEXT NOT NULL` | `'opensuse'`, `'fedora'`, or `'local'` |
| `latest_version` | `TEXT` | optional; set when known |
| `upstream_url` | `TEXT` | set from `FetchResult.upstream_url` during ingest |

**Unique:** `(name, distro)` -- one row per package per distro.
**Index:** `packages_name_idx` on `(name)`.

### `changelog_entries` -- parsed `.changes` blocks

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | **content-addressed**: `uuid5(NAMESPACE, name \|\| content)` |
| `package_id` | `BIGINT FK -> packages` | `ON DELETE CASCADE` |
| `version` | `TEXT` | as parsed from the changelog header |
| `author` | `TEXT` | maintainer email/name |
| `entry_date` | `TIMESTAMPTZ` | parsed via `version_utils.parse_when` |
| `content` | `TEXT NOT NULL` | raw entry body |
| `source_name` | `TEXT NOT NULL` | `'rpm'`, `'obs'`, `'gitea'`, or `'git'` |
| `tsv` | `TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED` | full-text index |
| `embedding` | `VECTOR(384)` | fastembed output |

**Indexes:**
- `changelog_entries_pkg_idx` on `(package_id)`
- `changelog_entries_date_idx` on `(entry_date DESC)` -- backs `get_recent_releases`, `get_changes_in_range`
- `changelog_entries_version_idx` on `(package_id, version)` -- backs `analyze_package_diff`
- `changelog_entries_tsv_idx` GIN on `(tsv)` -- backs `fts_search`
- `changelog_entries_embedding_idx` HNSW on `(embedding vector_cosine_ops)` -- backs `semantic_search`

**Why content-addressed PK:** the same `.changes` block returned from
OBS *and* its Gitea mirror collapses into one row under `ON CONFLICT
(id) DO NOTHING`. No field-by-field equality check required, no
cross-source dedup logic.

### `specs` -- raw `.spec` content

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL` PK | |
| `package_id` | `BIGINT FK -> packages` | `ON DELETE CASCADE` |
| `source` | `TEXT NOT NULL` | `'opensuse'` or `'fedora'` |
| `version` | `TEXT` | spec `Version:` field |
| `content` | `TEXT NOT NULL` | full spec body |
| `last_updated` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | refresh timestamp |

**Unique:** `(package_id, source)`.
**Indexes:** `specs_pkg_idx` on `(package_id)`, `specs_fts_idx` GIN on
`to_tsvector('english', content)`.

### `spec_sections` -- chunked spec for vector search

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL` PK | |
| `spec_id` | `BIGINT FK -> specs` | `ON DELETE CASCADE` |
| `section_name` | `TEXT NOT NULL` | `'header'`, `'%prep'`, `'%build'`, ... |
| `chunk_index` | `INT NOT NULL DEFAULT 0` | sliding window order within a section |
| `content` | `TEXT NOT NULL` | chunk body (1000 chars, 100 overlap) |
| `embedding` | `VECTOR(384)` | per-chunk |

**Indexes:** `spec_sections_spec_idx` on `(spec_id)`,
`spec_sections_embedding_idx` HNSW on `(embedding vector_cosine_ops)`.

Chunking happens in `src/ingest.py` via `chunk_sections` from
`src/spec_parser.py`. Not in the parser itself -- the parser only
returns whole sections.

### `news` -- Bodhi + RSS

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL` PK | |
| `package_id` | `BIGINT FK -> packages ON DELETE SET NULL` | nullable -- news can outlive a package |
| `title` | `TEXT NOT NULL` | dedup key |
| `source` | `TEXT NOT NULL` | `'bodhi'` or `'opensuse-rss'` |
| `item_type` | `TEXT` | `'security'`, `'update'`, ... |
| `importance` | `TEXT` | `'CRITICAL'`, `'Routine'`, `'Security'` |
| `content` | `TEXT` | item body |
| `url` | `TEXT` | source URL |
| `item_date` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | publish timestamp |

**Unique:** `(title, source)` -- the same title can legitimately appear
in different feeds.
**Indexes:** `news_pkg_idx` on `(package_id)`, `news_date_idx` on
`(item_date DESC)`.

### `openqa_tests` -- test-to-package map

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL` PK | |
| `package_id` | `BIGINT FK -> packages` | `ON DELETE CASCADE` |
| `test_path` | `TEXT NOT NULL` | path within `os-autoinst-distri-opensuse` |
| `summary` | `TEXT` | optional one-line description |

**Unique:** `(package_id, test_path)`.
**Index:** `openqa_tests_pkg_idx` on `(package_id)`.

Populated by `src/openqa_fetcher.py` scanning `# Package:` headers in
`.pm` files of a local `os-autoinst-distri-opensuse` checkout.

### `deps` -- dependency graph

| Column | Type | Notes |
|---|---|---|
| `package_id` | `BIGINT FK -> packages` | `ON DELETE CASCADE` |
| `dep_name` | `TEXT NOT NULL` | package or capability name |
| `kind` | `TEXT NOT NULL` | `'requires'` or `'provides'` |

**Primary key:** `(package_id, dep_name, kind)` -- composite, no surrogate.
**Indexes:** `deps_dep_name_idx` on `(dep_name)` (reverse-dep lookup),
`deps_kind_idx` on `(kind)`.

Backs `get_dependencies`, `get_reverse_dependencies`, and the BFS in
`get_dependency_changes`.

### `manifest` -- per-package sync state

| Column | Type | Notes |
|---|---|---|
| `package_id` | `BIGINT` PK FK -> packages | `ON DELETE CASCADE` |
| `synced_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | last full sync |

**Index:** `manifest_synced_at_idx` on `(synced_at)`.

Used for:
- TTL eviction (`is_fresh(pkg_id, ttl_seconds)`)
- Stale-data banner (`get_synced_at(pkg_id)` feeds the `WARNING:` prefix
  emitted by `_stale_banner`)
- Touched at the end of every successful ingest via `touch_manifest`.

---

## Design decisions

- **Single backing store.** Postgres holds vectors *and* relational
  data. No Qdrant, no SQLite, no separate FTS engine. Acceptable at
  the 13k-package / 100-user target.
- **Content-addressed dedup.** `uuid5(NAMESPACE, package_name ||
  content)` collapses the same `.changes` block fetched from multiple
  mirrors without per-field comparison or unique constraints on
  variable-length text.
- **HNSW over IVF.** Sub-100ms p95 at ~650k vectors (13k packages times
  ~50 entries) with default settings. `HNSW.ef_search` left at default
  pending DD13 bench.
- **Generated `tsv` column.** Updated atomically with `content` -- no
  trigger, no application-side sync.
- **`ON DELETE CASCADE` everywhere except `news`.** Dropping a package
  row should not orphan its changelog/specs/deps, but news items
  reference a package as metadata; losing the package should not
  delete the news.
- **No advisory locks for ingest coalescing.** Cross-process races are
  handled by `ON CONFLICT DO NOTHING` on content-addressed PKs;
  in-process coalescing is handled by `IngestService._pending`
  (plan item DD10/N3).

---

## Migration policy

`migrations/*.sql` runs idempotently on every startup. Every statement
uses `CREATE ... IF NOT EXISTS` or `ALTER ... IF NOT EXISTS`. There is
no migration tracking table -- order is filename-sorted and each file
must be safe to re-run.

For destructive changes (column renames, type changes, embedding
dimension swap) add a new numbered file that performs the change
idempotently; do not edit a previously-shipped migration.
