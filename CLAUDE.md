# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Unified MCP server merging `changelog-poc` (openSUSE changelog ingestion + semantic search) and `rpm-spec-assistant` (Fedora/openSUSE spec parsing, modernization, news, openQA mappings) into a single Python codebase. Backed by **PostgreSQL + pgvector** — one service replaces Qdrant *and* SQLite from the original projects. Scale target: 13k packages, 100 concurrent users via per-user local stdio MCP pointed at a shared Postgres.

Architecture inherits from `changelog-poc`'s patterns: pluggable `Source` ABC, dependency-injected services, structlog, env-driven `pydantic-settings`, FastMCP lifespan-managed singletons. Feature surface inherits from both, minus Podman macro expansion (cut per scope decision).

## Development commands

Python 3.13, managed by `uv` (`.python-version`).

```bash
# Infrastructure (PostgreSQL + pgvector in one Podman container)
infra/infra.sh start          # boot rpm-mcp-postgres on :5432
infra/infra.sh status
infra/infra.sh psql           # interactive psql shell

uv sync                                            # install deps
uv run mcp_server.py                               # MCP server, stdio
uv run mcp_server.py <tool> --help                 # one-shot CLI dispatch for any registered tool
uv run mcp dev mcp_server.py                       # MCP Inspector at :5173

uv run scripts/ingest.py <pkg>... [--file FILE]    # offline batch ingest
uv run scripts/ingest_core.sh [N]                  # pre-ingest top N core packages (default 100)
uv run scripts/worker.py                           # centralised cron-driven worker
uv run scripts/bench.py {ingest|search|both}      # latency p50/p95/p99

PYTHONPATH=. uv run pytest tests/ -v               # all tests
PYTHONPATH=. uv run pytest tests/test_db.py -v     # one file
PYTHONPATH=. uv run pytest -m e2e                  # testcontainers Postgres + gemini-cli e2e
PYTHONPATH=. uv run mypy src mcp_server.py         # type check
```

`DATABASE_URL` overrides the default DSN (`postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp`). Migrations in `migrations/*.sql` are applied idempotently by `Database.connect()` on every startup.

## External services expected at runtime

- **PostgreSQL with pgvector + pg_trgm** at `DATABASE_URL` — required for everything.

## Architecture

### Data flow

```
MCP client (stdio)
  ↓
mcp_server.py  (FastMCP, structlog, lifespan-managed Database)
  ↓
                  ┌────────────────┐
                  │ IngestService  │  ── per-tool entry
                  └────────────────┘
                          ↓
                  ┌────────────────┐
                  │ SourceRegistry │  ── routes by capability
                  └────────────────┘
              ┌──────────┼──────────┬──────────┬──────────┬──────────┐
          RpmSource  ObsSource  GiteaSource  PagureSource  BodhiSource  OpenQASource
              ↓
         Database (asyncpg + pgvector)
              ↓
       PostgreSQL (single shared instance)
```

### Source registry

Generalised `Source` interface (see `src/sources/base.py`) exposes four optional capabilities — sources implement only what they have:

| Capability | Returned data | Implementing sources |
|---|---|---|
| `fetch_changelog` | `list[ChangelogEntry]` | `RpmSource`, `ObsSource`, `GiteaSource`, `GitSource` |
| `fetch_spec` | `str` (raw .spec) | `ObsSource`, `PagureSource` |
| `fetch_news` | `list[NewsItem]` | `BodhiSource`, `OpenSUSENewsSource` |
| `fetch_tests` | `list[OpenQATest]` | `OpenQASource` |

Registry dispatches per capability; fetch strategies (`waterfall` | `parallel`) only apply to `fetch_changelog`. Local sources (`is_local=True`) run first in parallel mode.

### Postgres schema

`migrations/001_init.sql` is the source of truth. Key tables:

- `packages` — `(name, distro)` unique; one row per package per distro
- `changelog_entries` — content-addressed UUID PK (= `uuid5(NAMESPACE, name||content)`), `tsv` generated column for FTS, HNSW index on `embedding vector(384)`
- `specs` — raw `.spec` per `(package_id, source)`
- `spec_sections` — chunked sections (1000 chars / 100 overlap), HNSW-indexed embeddings
- `news` — Bodhi + RSS, dedup on `(title, source)`
- `openqa_tests` — `(package_id, test_path)` unique
- `deps` — `(package_id, dep_name, kind)`; kind ∈ `requires|provides`
- `manifest` — `synced_at` per package, drives TTL-based eviction

### Module responsibilities

- **`mcp_server.py`** — FastMCP entrypoint; delegates singletons/lifespan to `src/runtime.py`, tool registration to `src/tools/`, CLI dispatch to `src/cli.py`.
- **`src/runtime.py`** — process-wide singletons (`db`, `rpm_mgr`, `git_mgr`, `source_registry`, `ingest_service`) + `lifespan` async context manager. Single source of truth shared by tools and CLI.
- **`src/tools/`** — tool modules grouped by concern: `changelog.py` (10 tools), `deps.py` (4 tools), `spec.py` (1 tool), `news.py` (3 tools). Each module exposes `register(mcp)` + a `CLI_TOOLS` tuple aggregated in `src/tools/__init__.py`. Cross-cutting helpers in `_wrap.py` (decorator, structlog ctxvars, stale banner) and `_helpers.py` (validation, formatters, `_ensure_or_queue` fast-fail probe).
- **`src/cli.py`** — argparse subparser auto-generated from each tool's signature; `run_cli(serve)` dispatches `serve` or one-shot tool call.
- **`src/db.py`** — `Database` class wraps the asyncpg pool, registers pgvector codec, applies migrations on startup. Owns *all* SQL — no other module talks to Postgres directly.
- **`src/embedder.py`** — fastembed singleton; `embed_one`, `embed_batch`, `chunk_text` (1000/100 sliding window).
- **`src/ingest.py`** — `IngestService(registry, db, embedder)`: fetch → embed → upsert. Shared by the MCP `sync_package` tool, `scripts/ingest.py`, and `scripts/worker.py`.
- **`src/sources/`** — see registry table above.
- **`src/spec_parser.py`** — `python-specfile` AST → `SpecSection[]`. Chunking happens in the ingest pipeline, not here.
- **`src/git_manager.py`** — shallow clone (`--depth 50`), tag lookup with `cat-file` verification, `@alru_cache`, LRU disk eviction.
- **`src/rpm_manager.py`** — `rpm -q` subprocess wrapper for local-only data (changelogs, deps, rdeps).
- **`scripts/worker.py`** — centralised ingestion daemon (cron / systemd timer). Each end-user runs only the MCP server; bulk ingestion runs once on a maintenance host.

### Key design decisions

- **One backing service**: Postgres holds vectors *and* relational data. No Qdrant, no separate FTS engine, no SQLite. Simplifies ops at 100-user scale.
- **Content-addressed dedup**: `uuid5(NAMESPACE, package||content)` makes the same `.changes` block converge across OBS / Gitea / git sources without comparing fields.
- **HNSW for vector search**: pgvector HNSW indexes give sub-100ms p95 on ~650k vectors (~13k packages × 50 entries). Tune `HNSW.ef_search` in Phase 4.
- **Per-capability source dispatch**: changelog-poc's single-purpose `ChangelogSource` is widened to a multi-capability `Source` so news/openQA/spec fetchers share registry + lifecycle.
- **No Podman dependency**: `get_expanded_spec` and `expand` CLI from rpm-spec-assistant were dropped.

## MCP tool surface (target — built phase-by-phase)

| Tool | Phase | Notes |
|---|---|---|
| `analyze_package_diff(pkg, v_start, v_end, deep, refresh)` | 1 | semver/fuzzy/string version filter |
| `get_recent_releases(pkg, n, refresh)` | 1 | last *n* versions, grouped |
| `get_changes_in_range(pkg, since, until, refresh)` | 1 | ISO 8601 or natural-language dates via `dateparser` |
| `get_dependencies(pkg)` / `get_reverse_dependencies(pkg)` | 1 | from `deps` table |
| `find_cve(cve_id, package)` | 1 | substring search on changelog content |
| `list_cves(package, since)` | 1 | list all CVE IDs in a package changelog (optional date filter) |
| `find_bug(bug_id, package)` | 1 | search for bsc#/boo#/bnc# bug reference in changelogs |
| `list_bugs(package, since)` | 1 | list all SUSE/openSUSE bugzilla refs in a package changelog |
| `get_dependency_changes(pkg, n, depth, refresh)` | 1 | BFS over `deps`, capped by `F4_MAX_PACKAGES` |
| `sync_package(pkg)` | 1 | thin wrapper over `IngestService.ingest` |
| `semantic_search(query, limit)` | 1 | pgvector cosine over `changelog_entries.embedding` |
| `fts_search(query, limit, since)` | 1 | tsvector over `changelog_entries.tsv`, optional date filter |
| `get_spec_details(pkg, source)` | 2 | AST sections via `python-specfile` |
| `get_news(pkg, limit)` | 3 | from `news` table |
| `get_openqa_tests(pkg)` | 3 | from `openqa_tests` table |

## Phased build status

- Phase 0 — scaffold: done
- Phase 1 — changelog parity with changelog-poc: done (all 8 tools wired to Postgres)
- Phase 2 — spec assistant features: done (get_spec_details only; LLM-backed tools dropped — MCP clients have their own LLM)
- Phase 3 — news + openQA: done (get_news, get_openqa_tests)
- Phase 4 — centralised worker + bench tuning
- Phase 4.5 — unit tests + coverage: done (181 tests, 73% coverage; see plan.md)
- Phase 5 — production hardening: resilience, worker daemon, ops artifacts (see plan.md Phase 5 section)
- Phase 6 — Go port (deferred)

## Test infrastructure

```bash
# Unit tests only (fast, no container)
./scripts/test.sh unit

# DB integration tests (requires podman socket)
./scripts/test.sh e2e-db

# All tests
./scripts/test.sh all
```

Podman notes:
- `TESTCONTAINERS_RYUK_DISABLED=true` required — Ryuk can't mount the Podman socket
- `scripts/test.sh` sets both `DOCKER_HOST` and `TESTCONTAINERS_RYUK_DISABLED` automatically
- Module-scoped async fixtures need `loop_scope="module"` + `pytestmark = pytest.mark.asyncio(loop_scope="module")`

E2E tests via gemini-cli: `tests/test_e2e_gemini.py` — testcontainers Postgres + real openSUSE packages (vim, CVE-2023-4738)
- Settings patched in `~/.gemini/settings.json` during test session, restored on teardown

## Production design decisions

- **Deployment**: local stdio per user, shared Postgres — no SSE, no auth
- **Source failure**: serve stale cached data with `WARNING: source fetch failed; serving cached data from <timestamp>` banner
- **LLM tooling**: server has no embedded LLM — MCP client (Claude/gemini-cli) provides reasoning on raw data returned by tools
- **Out of scope**: auth, TLS, Prometheus, Containerfile
