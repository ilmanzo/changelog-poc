# Architecture

rpm-mcp is a unified MCP server for querying RPM package changelogs, specs, CVEs, news, and
openQA test mappings. A single PostgreSQL + pgvector instance replaces the Qdrant + SQLite
backends from the legacy changelog-poc and rpm-spec-assistant projects.

---

## Component overview

![Component overview](diagrams/component_overview.svg)

Source: [`diagrams/src/component_overview.mmd`](diagrams/src/component_overview.mmd).
Regenerate with `./scripts/render_diagrams.sh`.

---

## Sequence — `sync_package` + `semantic_search`

![sync + semantic search sequence](diagrams/sync_search_sequence.svg)

Source: [`diagrams/src/sync_search_sequence.mmd`](diagrams/src/sync_search_sequence.mmd).

---

## Sequence — `fts_search` with `since` filter

![FTS search sequence](diagrams/fts_search_sequence.svg)

Source: [`diagrams/src/fts_search_sequence.mmd`](diagrams/src/fts_search_sequence.mmd).

---

## Data flow — write path (ingest)

![Ingest data flow](diagrams/ingest_dataflow.svg)

Source: [`diagrams/src/ingest_dataflow.mmd`](diagrams/src/ingest_dataflow.mmd).

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
| `PG_POOL_MIN_SIZE` | `10` | asyncpg pool min connections. Sized for ~100 concurrent stdio clients (each holds one conn for the duration of a query). Must stay below Postgres `max_connections` (default 100). |
| `PG_POOL_MAX_SIZE` | `80` | asyncpg pool max connections. Leaves headroom under Postgres `max_connections=100` for the worker daemon + admin sessions. |

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
