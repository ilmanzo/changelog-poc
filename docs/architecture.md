# Architecture

rpm-mcp is a unified MCP server for querying RPM package changelogs, specs, CVEs, news, and
openQA test mappings. A single PostgreSQL + pgvector instance replaces the Qdrant + SQLite
backends from the legacy changelog-poc and rpm-spec-assistant projects.

---

## Component overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MCP client layer                                    │
│                                                                             │
│   Gemini CLI          Claude Desktop          Any MCP-compatible client     │
│   (stdio / SSE)       (stdio)                 (SSE or stdio)                │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │  JSON-RPC (MCP protocol)
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        mcp_server.py  (FastMCP)                             │
│                                                                             │
│   14 tools  ─  analyze_package_diff, get_recent_releases,                  │
│               get_changes_in_range, find_cve, list_cves,                   │
│               get_dependencies, get_reverse_dependencies,                   │
│               get_dependency_changes, sync_package,                         │
│               semantic_search, fts_search,                                  │
│               get_spec_details,                                             │
│               get_news, get_openqa_tests                                    │
│                                                                             │
│   Lifespan:  Database.connect() → yield → source_registry.close()          │
│   Helpers:   _ensure_fresh(), _load_entries(), _ensure_spec()               │
└──────┬──────────────┬───────────────────────────────┬───────────────────────┘
       │              │                               │
       ▼              ▼                               ▼
┌─────────────┐ ┌──────────────────┐        ┌────────────────────┐
│  Database   │ │  IngestService   │        │    RPMManager /    │
│  (src/db.py)│ │  (src/ingest.py) │        │    GitManager      │
│             │ │                  │        │                    │
│  asyncpg    │ │  fetch → embed → │        │  rpm_manager.py:   │
│  pool +     │ │  upsert          │        │  rpm -q subprocess │
│  pgvector   │ │                  │        │                    │
│  codec      │ └────────┬─────────┘        │  git_manager.py:   │
└──────┬──────┘          │                  │  shallow clone,    │
       │                 ▼                  │  tag lookup        │
       │         ┌──────────────────┐       │                    │
       │         │  SourceRegistry  │       └────────────────────┘
       │         │  (src/sources/)  │
       │         │                  │
       │         │  waterfall |     │
       │         │  parallel        │
       │         └──┬───────┬───────┘
       │            │       │
       │   ┌────────┘       └────────────┐
       │   ▼                             ▼
       │ RpmSource                    ObsSource / GiteaSource
       │ (local rpm -q,               (OBS API + src.opensuse.org,
       │  is_local=True,               HTTP + tenacity retries)
       │  runs first)
       │
       ▼
┌──────────────────────────────────────────┐
│  PostgreSQL 17  (pgvector/pgvector:pg17) │
│                                          │
│  packages          (name, distro) UNIQUE │
│  changelog_entries  UUID PK (uuid5),     │
│                     tsv GENERATED,       │
│                     embedding VECTOR(384)│
│  specs              (package_id, source) │
│  spec_sections      embedding VECTOR(384)│
│  news               (title, source)      │
│  openqa_tests       (package_id, path)   │
│  deps               (package_id, kind)   │
│  manifest           synced_at TTL        │
│                                          │
│  Indexes:  HNSW on embeddings            │
│            GIN on tsv                    │
│            pg_trgm for fuzzy             │
└──────────────────────────────────────────┘
```

---

## Sequence — `sync_package` + `semantic_search`

```mermaid
sequenceDiagram
    participant C as MCP client
    participant T as FastMCP tool
    participant I as IngestService
    participant SR as SourceRegistry
    participant RS as RpmSource
    participant E as Embedder (fastembed)
    participant DB as Database (asyncpg)
    participant PG as PostgreSQL + pgvector

    C->>T: sync_package("vim")
    T->>I: ingest("vim")
    I->>SR: fetch_changelog("vim")
    SR->>RS: fetch("vim")   [local, runs first]
    RS-->>SR: ChangelogEntry[] from rpm -q
    SR-->>I: entries (waterfall stops here on hit)
    I->>E: embed_batch(entry.content for each)
    E-->>I: list[list[float]]  (384-dim, ONNX in-thread)
    I->>DB: upsert_changelog_entries(entries, embeddings)
    DB->>PG: INSERT ... ON CONFLICT (id) DO NOTHING
    DB->>DB: touch_manifest(package_id)
    I-->>T: IngestResult(INDEXED, entries=N, source="rpm")
    T-->>C: "Successfully indexed N entries for vim"

    C->>T: semantic_search("memory leak fix", limit=5)
    T->>E: embed_one("memory leak fix")
    E-->>T: query_vector[384]
    T->>DB: semantic_search(query_vector, limit=5)
    DB->>PG: ORDER BY embedding <=> $1::vector LIMIT 5
    PG-->>DB: rows (package, version, entry_date, content, distance)
    DB-->>T: list[Record]
    T-->>C: formatted text with top-5 entries
```

---

## Sequence — `fts_search` with `since` filter

```mermaid
sequenceDiagram
    participant C as MCP client
    participant T as FastMCP tool
    participant PW as parse_when()
    participant DB as Database

    C->>T: fts_search("buffer overflow", since="2023-01-01")
    T->>PW: parse_when("2023-01-01")
    PW-->>T: datetime(2023,1,1, tzinfo=UTC)
    T->>DB: fts_search("buffer overflow", limit=10, since=datetime(...))
    DB->>DB: plainto_tsquery('english', $1) AND entry_date >= $3
    DB-->>T: rows ordered by ts_rank DESC
    T-->>C: formatted entries with dates ≥ 2023-01-01
```

---

## Data flow — write path (ingest)

```
rpm -q --changelog vim
          │
          ▼
    RpmSource.fetch()
          │  list[ChangelogEntry(version, author, date, content)]
          ▼
    IngestService.ingest()
          │
          ├── embed_batch(content)   ←── fastembed ONNX in asyncio.to_thread
          │       └── list[list[float]]  (384-dim per entry)
          │
          └── db.upsert_changelog_entries()
                  │
                  │  uuid5(PKG_NAMESPACE, package + content)  ← stable dedup key
                  │  ON CONFLICT (id) DO NOTHING              ← same .changes block
                  │                                              across sources converges
                  ▼
          changelog_entries  (+ tsv GENERATED ALWAYS AS tsvector)
                  │
                  ▼
          HNSW index on embedding  ←── sub-100ms p95 cosine search at 650k vectors
          GIN index on tsv         ←── tsvector FTS via plainto_tsquery
```

---

## Source dispatch

| Source | `is_local` | Capability | Notes |
|---|---|---|---|
| `RpmSource` | yes | `fetch_changelog` | `rpm -q --changelog`; no network; runs first |
| `ObsSource` | no | `fetch_changelog`, `fetch_spec` | OBS Factory API; `.changes` parser |
| `GiteaSource` | no | `fetch_changelog` | `src.opensuse.org` mirror; fallback |
| `PagureSource` | no | `fetch_spec` | Fedora Pagure API |
| `BodhiSource` | no | `fetch_news` | Fedora Bodhi updates feed |
| `OpenSUSENewsSource` | no | `fetch_news` | openSUSE RSS |
| `OpenQASource` | yes | `fetch_tests` | local `os-autoinst-distri-opensuse` checkout |

Fetch strategy (env `FETCH_STRATEGY`):

- **`waterfall`** (default): tries sources in order, stops at first non-empty result.
- **`parallel`**: runs local sources first; races remaining sources via `asyncio.gather`.

---

## Layers summary

| Layer | Implementation |
|---|---|
| Transport | FastMCP stdio (default) or SSE (`MCP_TRANSPORT=sse`) |
| Validation | `validate_package_name()` regex on every tool input |
| Cache / TTL | `manifest(package_id, kind, synced_at)` — per-kind freshness; `is_fresh(pkg_id, ttl, kind)` checks against `CACHE_TTL_{NEWS,CHANGELOG,SPEC}_S` |
| Embedding | `fastembed` `BAAI/bge-small-en-v1.5` (384-dim) in `asyncio.to_thread` |
| FTS | PostgreSQL `tsvector` generated column + `plainto_tsquery` |
| Vector search | pgvector HNSW index, cosine distance (`<=>`) |
| Dedup | `uuid5(PKG_NAMESPACE, package + content)` — stable across sources |
| Storage | `asyncpg` connection pool (min/max via `PG_POOL_MIN/MAX_SIZE`) |
| Logging | `structlog` — `LOG_FORMAT=json` for production |

---

## E2E test interaction

```
┌──────────────────────────────────────────────────────────────────┐
│  pytest session  (tests/test_e2e_gemini.py)                      │
│                                                                  │
│  pg_dsn fixture                                                  │
│    └── PostgresContainer("pgvector/pgvector:pg17")               │
│          via testcontainers  (Podman or Docker socket)           │
│                                                                  │
│  gemini_mcp fixture                                              │
│    └── patches ~/.gemini/settings.json                           │
│          mcpServers["rpm-mcp-e2e"] = {                           │
│            command: "uv run python mcp_server.py",               │
│            env: {DATABASE_URL: <testcontainer DSN>}              │
│          }                                                       │
│    └── restores settings.json on teardown                        │
│                                                                  │
│  vim_ingested / curl_ingested fixtures                           │
│    └── call sync_package via gemini-cli once per session         │
│          seeds real openSUSE RPM data into the container DB      │
│                                                                  │
│  Each test:                                                      │
│    _gemini("Call <tool> …")                                      │
│      └── subprocess: gemini -y -p <prompt>                       │
│               --allowed-mcp-server-names=rpm-mcp-e2e             │
│            gemini-cli spawns mcp_server.py (stdio), calls tool,  │
│            returns natural-language answer                       │
│    assert keyword in output                                      │
└──────────────────────────────────────────────────────────────────┘

  DOCKER_HOST=unix:///run/user/$UID/podman/podman.sock  (for Podman)
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp` | asyncpg DSN |
| `FETCH_STRATEGY` | `waterfall` | `waterfall` or `parallel` |
| `CACHE_TTL_NEWS_S` | `86400` | News (RSS/Bodhi) refresh interval — 24h |
| `CACHE_TTL_CHANGELOG_S` | `86400` | Per-package changelog freshness — 24h |
| `CACHE_TTL_SPEC_S` | `604800` | Per-package spec freshness — 7d |
| `CACHE_MAX_ENTRIES` | `1000` | Max changelog entries fetched per package |
| `F4_MAX_PACKAGES` | `50` | Max deps expanded by `get_dependency_changes` |
| `PG_POOL_MIN_SIZE` | `2` | asyncpg pool min connections |
| `PG_POOL_MAX_SIZE` | `10` | asyncpg pool max connections |
| `LOG_FORMAT` | `text` | `json` for structured production logs |
| `MCP_TRANSPORT` | `stdio` | `sse` for HTTP/SSE transport |
