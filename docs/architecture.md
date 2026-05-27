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
│   Claude Desktop / Code      Gemini CLI       Any MCP-compatible client     │
│                              (all use stdio transport — one process / user) │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │  JSON-RPC over stdio  (MCP protocol)
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        mcp_server.py  (FastMCP)                             │
│                                                                             │
│   21 tools, grouped by module under src/tools/:                             │
│     changelog.py  analyze_package_diff, get_recent_releases,                │
│                   get_changes_in_range, compare_versions,                   │
│                   find_cve, list_cves, find_bug, list_bugs,                 │
│                   semantic_search, fts_search, sync_package,                │
│                   sync_all_distros                                          │
│     deps.py       get_dependencies, get_reverse_dependencies,               │
│                   get_dependency_changes, find_core_packages                │
│     spec.py       get_spec_details                                          │
│     news.py       get_news, get_openqa_tests, get_sync_status,              │
│                   find_untested_changes                                     │
│                                                                             │
│   Lifespan:  db.connect() → yield → source_registry.close() → db.close()    │
│   Shared:    _tool_wrapper decorator (structlog ctxvars, stale banner,      │
│              prompt-injection envelope, fast-fail probe via _Readiness)     │
└──────┬──────────────┬───────────────────────────────┬───────────────────────┘
       │              │                               │
       ▼              ▼                               ▼
┌─────────────┐ ┌──────────────────┐        ┌────────────────────┐
│  Database   │ │  IngestService   │        │    RPMManager /    │
│  (src/db.py)│ │  (src/ingest.py) │        │    GitManager      │
│             │ │                  │        │                    │
│  asyncpg    │ │  fetch → embed → │        │  rpm_manager.py:   │
│  pool +     │ │  upsert          │        │  rpm -q subprocess │
│  pgvector   │ │                  │        │  (timeout-capped)  │
│  codec      │ │  enrichment:     │        │                    │
└──────┬──────┘ │  parse_upstream  │        │  git_manager.py:   │
       │        │  _url(...) →     │        │  shallow clone,    │
       │        │  url_router.py   │        │  tag lookup        │
       │        └────────┬─────────┘        └────────────────────┘
       │                 │
       │                 ▼
       │       ┌──────────────────┐
       │       │  SourceRegistry  │   (src/sources/)
       │       │                  │
       │       │  waterfall |     │
       │       │  parallel        │
       │       └──┬───────┬───────┘
       │          │       │
       │   ┌──────┘       └──────────────────────┐
       │   ▼                                     ▼
       │ RpmSource (local, runs first)    ObsSource, GiteaSource,
       │ rpm -q --changelog               FedoraSource, UbuntuSource
       │                                  (HttpSource base: tenacity
       │                                   retries, SourceNotFound on 404)
       │
       │   ┌── upstream enrichment (instantiated per URL, not registered):
       │   │     GitHubSource, GitLabSource via ReleaseSource base
       │   │     url_router.parse_upstream_url(spec_url) → ReleaseSource | None
       │   │
       │   ├── spec sources (separate SpecSource ABC):
       │   │     ObsSpecSource, PagureSpecSource
       │   │
       │   └── news + openQA (plain modules, not Source ABC):
       │         news_fetcher.fetch_bodhi / fetch_opensuse_news
       │         openqa_fetcher.fetch_openqa_tests
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
│  manifest           synced_at TTL,       │
│                     per-kind freshness   │
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
    I->>SR: fetch("vim")
    SR->>RS: fetch("vim")   [local, runs first]
    RS-->>SR: ChangelogEntry[] from rpm -q
    SR-->>I: FetchResult (waterfall stops here on hit)
    I->>E: embed_batch(entry.content)   [asyncio.to_thread → ONNX]
    E-->>I: list[list[float]]  (384-dim)
    I->>DB: upsert_changelog_entries(entries, embeddings)
    DB->>PG: INSERT ... ON CONFLICT (id) DO NOTHING
    DB->>DB: touch_manifest(package_id, kind="changelog")
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
          │  FetchResult(entries=[ChangelogEntry(version, author, date, content)])
          ▼
    IngestService.ingest()
          │
          ├── (optional) _resolve_upstream_url(pkg)  ← spec / _service parser
          │       └── url_router.parse_upstream_url(url) → ReleaseSource | None
          │             └── GitHubSource | GitLabSource releases merged in
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

`SourceRegistry` only orchestrates the **changelog** capability (5 sources). Spec, news,
upstream-release, and openQA fetchers live in their own modules and are invoked directly.

### Registered changelog sources (SourceRegistry)

| Source | `is_local` | Distro | Notes |
|---|---|---|---|
| `RpmSource` | yes | opensuse | `rpm -q --changelog`; no network; runs first |
| `ObsSource` | no | opensuse | OBS Factory API (`api.opensuse.org`); `.changes` parser |
| `GiteaSource` | no | opensuse | `src.opensuse.org` mirror; fallback |
| `FedoraSource` | no | fedora | Pagure dist-git: standalone `changelog` file or `%changelog` in spec |
| `UbuntuSource` | no | ubuntu | Launchpad `changelog` endpoint |

### Upstream release sources (instantiated dynamically by `url_router`)

| Source | Trigger | Notes |
|---|---|---|
| `GitHubSource` | spec URL matches `github.com/<owner>/<repo>` | GitHub Releases API; optional `GITHUB_TOKEN` |
| `GitLabSource` | spec URL matches gitlab forge allowlist | GitLab Releases API; optional `GITLAB_TOKEN` |

Both inherit from `ReleaseSource` (HTTP plumbing via `HttpSource`). Activated when
`IngestService._resolve_upstream_url` finds a forge URL in the spec or `_service` file.

### Spec sources (separate SpecSource ABC)

| Source | Distro | Notes |
|---|---|---|
| `ObsSpecSource` | opensuse | `build.opensuse.org/public/source/openSUSE:Factory/...` |
| `PagureSpecSource` | fedora | `src.fedoraproject.org/api/0/rpms/...` |

Used by the `get_spec_details` tool via `fetch_any_spec()` (waterfall, no concurrent fetch).

### Standalone fetchers (plain modules)

| Module | Function | Backend |
|---|---|---|
| `news_fetcher.py` | `fetch_bodhi`, `fetch_opensuse_news` | Bodhi JSON + openSUSE RSS via aiohttp |
| `openqa_fetcher.py` | test path mapping | local `os-autoinst-distri-opensuse` checkout |

### Fetch strategy (env `FETCH_STRATEGY`)

- **`waterfall`** (default): tries sources in order, stops at first non-empty result.
- **`parallel`**: runs local sources first; races remaining sources via `asyncio.gather`,
  picks the result with the most entries (content-addressed UUIDs make later merging idempotent).

---

## Layers summary

| Layer | Implementation |
|---|---|
| Transport | FastMCP stdio (local process per MCP client) |
| Validation | `validate_package_name()` regex on every tool input |
| Cache / TTL | `manifest(package_id, kind, synced_at)` — per-kind freshness; `is_fresh()` checks against `CACHE_TTL_{NEWS,CHANGELOG,SPEC}_S` |
| Eviction | `evict_stale()` deletes manifest + changelog rows in one transaction (atomic) |
| Stale fallback | source fetch failure → serve cached rows with `WARNING: serving cached data from <ts>` banner |
| Embedding | `fastembed` `BAAI/bge-small-en-v1.5` (384-dim) in `asyncio.to_thread` |
| FTS | PostgreSQL `tsvector` generated column + `plainto_tsquery` |
| Vector search | pgvector HNSW index, cosine distance (`<=>`) |
| Dedup | `uuid5(PKG_NAMESPACE, package + content)` — stable across sources |
| Storage | `asyncpg` connection pool (min/max via `PG_POOL_MIN/MAX_SIZE`) |
| HTTP | single `aiohttp` stack (shared `make_client_session`, `HttpSource` retries via tenacity) |
| Subprocess | `run_subprocess()` with `SUBPROCESS_TIMEOUT_S` ceiling, SIGKILL on timeout |
| Sanitisation | `scrub_external()` strips control chars + caps to `CACHE_MAX_ENTRY_BYTES` per entry |
| Logging | `structlog` — `LOG_FORMAT=json` for production |

---

## Centralised ingestion (worker)

End users run only the MCP server (stdio, per-process). Bulk ingestion runs **once** on a
maintenance host via `scripts/worker.py` — a cron-driven daemon that walks the registered
sources at `WORKER_CONCURRENCY` parallelism and writes into the shared Postgres. The MCP
server treats Postgres as a read-mostly cache and only invokes `IngestService.ingest()`
on cache miss (gated by `_ensure_or_queue`'s fast-fail probe).

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
  TESTCONTAINERS_RYUK_DISABLED=true                     (Ryuk can't mount socket)
```

---

## Environment variables

All settings load from env via `pydantic-settings`. Defaults are in `src/config.py`.

For convenience, a `.env` file in the working directory is loaded automatically — OS
environment variables still take precedence. See `.env.example` for the variables most
operators actually touch (DSN, forge tokens, log format, fetch strategy); everything
else uses the documented defaults.

### Storage

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp` | asyncpg DSN |
| `PG_POOL_MIN_SIZE` | `2` | asyncpg pool min connections |
| `PG_POOL_MAX_SIZE` | `20` | asyncpg pool max connections |

### Cache / TTL

| Variable | Default | Description |
|---|---|---|
| `CACHE_TTL_NEWS_S` | `86400` | News (RSS/Bodhi) refresh interval — 24h |
| `CACHE_TTL_CHANGELOG_S` | `86400` | Per-package changelog freshness — 24h |
| `CACHE_TTL_SPEC_S` | `604800` | Per-package spec freshness — 7d |
| `CACHE_MAX_ENTRIES` | `1000` | Max changelog entries fetched per package |
| `CACHE_MAX_ENTRY_BYTES` | `8192` | Per-entry sanitiser truncation cap (not a cache size limit) |
| `EVICTION_MIN_INTERVAL_S` | `3600` | Minimum gap between opportunistic stale-row sweeps |

### Embedding

| Variable | Default | Description |
|---|---|---|
| `EMBEDDING_MODEL` | `""` | Empty → fastembed default (`BAAI/bge-small-en-v1.5`) |
| `EMBEDDING_DIM` | `384` | Vector dimensionality (must match the model) |
| `EMBEDDING_BATCH_SIZE` | `100` | Items per `embed_batch` call |
| `EMBEDDING_CHUNK_SIZE` | `1000` | Spec-section chunk size (chars) |
| `EMBEDDING_CHUNK_OVERLAP` | `100` | Sliding-window overlap between chunks |

### Sources & fetching

| Variable | Default | Description |
|---|---|---|
| `FETCH_STRATEGY` | `waterfall` | `waterfall` or `parallel` |
| `OBS_TIMEOUT_TOTAL` | `30` | Total per-request timeout (s) for all aiohttp calls |
| `OBS_TIMEOUT_CONNECT` | `10` | Connect-phase timeout (s) |
| `OBS_MAX_RETRIES` | `3` | Tenacity retry ceiling for transient (5xx, connection) errors |
| `GITHUB_TOKEN` | `""` | Optional bearer token for GitHub Releases (anonymous if empty) |
| `GITLAB_TOKEN` | `""` | Optional bearer token for GitLab Releases |

### Subprocess & worker

| Variable | Default | Description |
|---|---|---|
| `SUBPROCESS_TIMEOUT_S` | `60` | Ceiling for `rpm`, `git`, etc. subprocess calls |
| `WORKER_CONCURRENCY` | `10` | Parallel `IngestService.ingest` jobs in `scripts/worker.py` |
| `GIT_CACHE_MAX_ENTRIES` | `50` | LRU bound for `GitManager` shallow-clone cache |

### Tool caps & test data

| Variable | Default | Description |
|---|---|---|
| `F4_MAX_PACKAGES` | `50` | Max deps expanded by `get_dependency_changes` |
| `TEST_REPO_URL` | `https://github.com/os-autoinst/os-autoinst-distri-opensuse` | openQA test repo for `find_untested_changes` |
| `TEST_REPO_PATH` | `~/.cache/rpm-mcp/os-autoinst` | Local checkout path |

### Logging

| Variable | Default | Description |
|---|---|---|
| `LOG_FORMAT` | `text` | `json` for structured production logs |
