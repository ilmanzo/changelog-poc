# rpm-mcp

RPM packages on openSUSE/Fedora ship changelogs, spec files, CVE fixes, and openQA test mappings — this server ingests all of that into a shared PostgreSQL database with pgvector for semantic search. It exposes 22 tools over MCP (stdio), so any MCP client like Claude Code or gemini-cli can query it in natural language — "find CVEs fixed in curl last month" or "which tests cover dropped systemd features". The key insight is that the LLM reasoning stays in the client; the server is pure data retrieval, making it cheap to run as a shared service for 100 users with no per-user state.

**Documentation:**
- [User Guide](docs/user-guide.md) -- deploy, configure, ingest, query
- [Developer Guide](docs/developer-guide.md) -- code structure, patterns, how to extend
- [Architecture](docs/architecture.md) -- diagrams, env vars, source registry
- [Schema](docs/schema.md) -- database tables and indexes
- [Threat Model](docs/THREAT_MODEL.md) -- security boundaries
- [Dev Diary](docs/dev-diary.md) -- design history

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [Podman](https://podman.io/) (for the database container)
- Python 3.13 (uv manages this automatically)

## Quick Start

```bash
# 1. Install dependencies
uv sync

# 2. Start PostgreSQL + pgvector
./rpm-mcp start

# 3. Register the server with your MCP client (Claude Code, gemini-cli, or both)
./rpm-mcp register gemini       # or: claude   or: all

# 4. Populate the DB
uv run scripts/ingest.py vim curl openssl
```

Migrations run automatically on startup. Your MCP client spawns the server itself via stdio --
no need to keep it running manually. See the [User Guide](docs/user-guide.md) for the full flow.

## Configuration

All settings are environment variables (shown with defaults):

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp` | Matches infra defaults |
| `LOG_FORMAT` | `text` | `text` or `json` |
| `FETCH_STRATEGY` | `waterfall` | `waterfall` or `parallel` |

## Running Modes

```bash
# stdio (default — use this with Claude Desktop / MCP clients)
uv run mcp_server.py

# SSE transport
MCP_TRANSPORT=sse uv run mcp_server.py

# MCP Inspector UI at http://localhost:5173
uv run mcp dev mcp_server.py
```

## Register with an MCP client

The server runs over stdio — each client spawns it as a child process. Make sure
PostgreSQL is up (`infra/infra.sh start`) and `uv sync` has run once.

### One-liner (Claude Code + Gemini CLI)

```bash
./scripts/register.sh add all      # register with both
./scripts/register.sh status       # show current state
./scripts/register.sh remove all   # unregister from both
```

Targets are `claude`, `gemini`, or `all` (default). Set `DATABASE_URL` in the
environment before running `add` to bake a non-default DSN into the registration.
The script needs `jq` for the Gemini case and the `claude` CLI for the Claude
case; it backs up `~/.gemini/settings.json` before any edit.

### Manual

Substitute `/abs/path/to/rpm-mcp` with the absolute path to this directory.

#### Claude Code

```bash
claude mcp add rpm-mcp \
  --scope user \
  -- uv run --directory /abs/path/to/rpm-mcp python mcp_server.py
```

Verify: `claude mcp list` — should show `rpm-mcp Connected`. Inside a Claude
Code session, run `/mcp` to confirm the tools are exposed.

To override the DSN:

```bash
claude mcp add rpm-mcp \
  --scope user \
  --env DATABASE_URL=postgresql://user:pass@host:5432/db \
  -- uv run --directory /abs/path/to/rpm-mcp python mcp_server.py
```

Remove with `claude mcp remove rpm-mcp`.

#### Gemini CLI

Edit `~/.gemini/settings.json` and add an entry under `mcpServers`:

```json
{
  "mcpServers": {
    "rpm-mcp": {
      "command": "uv",
      "args": ["run", "python", "mcp_server.py"],
      "cwd": "/abs/path/to/rpm-mcp",
      "env": {
        "DATABASE_URL": "postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp"
      }
    }
  }
}
```

Verify: launch `gemini` and type `/mcp list`. To restrict a one-shot call to
this server:

```bash
gemini -y -p "Call sync_package with package='vim'" \
  --allowed-mcp-server-names=rpm-mcp
```

## Ingesting Data

```bash
# Ingest specific packages
uv run scripts/ingest.py curl bash openssl

# Ingest from a file, 4 parallel workers
uv run scripts/ingest.py --file packages.txt --concurrency 4

# Cron-driven worker (refresh all tracked packages)
uv run scripts/worker.py

# Refresh news feeds only
uv run scripts/worker.py --news
```

## Infrastructure Commands

```bash
infra/infra.sh start    # Start Postgres container on :5432
infra/infra.sh status   # Check container status
infra/infra.sh psql     # Interactive psql shell
infra/infra.sh stop     # Stop container
infra/infra.sh rm       # Remove container + data
```

## Development

```bash
# Unit tests
uv run pytest tests/ -v

# Integration tests (spins up a real Postgres via testcontainers + podman)
PYTHONPATH=. uv run pytest -m e2e

# Type checking
uv run mypy src mcp_server.py

# Latency benchmarks
uv run scripts/bench.py both
```

## MCP Tools (quick reference)

| Tool | What it does |
|---|---|
| `analyze_package_diff` | Changelog diff between two versions |
| `get_recent_releases` | Last N releases for a package |
| `get_changes_in_range` | Changes between two dates |
| `compare_versions` | Side-by-side version comparison |
| `find_cve` / `list_cves` | CVE lookup and listing |
| `find_bug` / `list_bugs` | Bug reference search (bsc#/boo#/bnc#) |
| `semantic_search` | pgvector cosine similarity search |
| `fts_search` | Full-text search with optional date filter |
| `sync_package` | Force re-ingest a package |
| `sync_all_distros` | Re-ingest a package across all distros |
| `get_dependencies` | Direct dependencies from DB |
| `get_reverse_dependencies` | Reverse dependency lookup |
| `get_dependency_changes` | BFS changelog walk over dep graph |
| `find_core_packages` | Identify high-fan-in core packages |
| `get_spec_details` | RPM spec file sections |
| `get_news` | Bodhi + RSS news for a package |
| `get_test_coverage` | openQA/TestCatalog test coverage |
| `find_bugs_in_tests` | Tests referencing known bugs |
| `get_sync_status` | Ingestion manifest and staleness info |
| `find_untested_changes` | Security fixes with no test coverage |

## Demos

Short walkthroughs with captured gemini-cli output and GIFs:

| Demo | What it shows |
|---|---|
| [Vim changelog query](docs/vhs/demo_changelog.md) | Diff two package versions, surface relevant changes |
| [CVE privilege escalation timeline](docs/vhs/demo_cve_timeline.md) | Track a CVE across versions and distros |
| [QA triage -- openssl](docs/vhs/demo_openssl_bugs.md) | Correlate bug fixes with test coverage |
| [QA triage -- systemd](docs/vhs/demo_systemd_bugs.md) | Same pattern on a larger package |
| [Semantic search](docs/vhs/demo_search.md) | Natural-language search over changelogs |
| [Cross-distro dependency blast radius](docs/vhs/demo_cross_distro.md) | Map an update across openSUSE / Ubuntu / Fedora |
| [Untested security fixes](docs/vhs/demo_untested.md) | Find security changes with no test coverage |
| [Stale test cleanup](docs/vhs/demo_stale_tests.md) | Find tests covering dropped/removed features |

## Prompt Examples

See [`prompt_examples.md`](prompt_examples.md) for a collection of useful queries and discovery patterns.

## Security

### External content sanitisation

All text fetched from external sources is run through `src/sanitize.py:scrub_external` at parse time — strips ANSI escapes, null bytes, BOM, and C0/C1 control bytes before storage or display.

## Code Structure

**Entry points**
- `mcp_server.py` — FastMCP entrypoint; wires lifespan, tools, CLI dispatch
- `src/runtime.py` — process-wide singletons (`db`, `source_registry`, `ingest_service`, etc.) shared by all tools and CLI
- `src/cli.py` — auto-generated argparse subcommands from tool signatures; handles one-shot CLI calls

**Tools layer** (`src/tools/`)
- `changelog.py` — 12 tools: version diff, CVE/bug search, FTS, semantic search, sync
- `deps.py` — 4 tools: direct/reverse deps, BFS dependency changes, core packages
- `spec.py` — 1 tool: spec file section parsing
- `news.py` — 5 tools: news, test coverage, untested changes, bugs-in-tests, sync status
- `_wrap.py` — decorator, structlog context vars, stale data banner
- `_helpers.py` — shared validation, formatters, fast-fail probe

**Data layer**
- `src/db.py` — all SQL lives here; asyncpg pool + pgvector codec + idempotent migrations
- `src/ingest.py` — orchestrates fetch → embed → upsert; used by tools, scripts, and worker
- `src/embedder.py` — fastembed singleton; `embed_one`, `embed_batch`, chunking

**Sources** (`src/sources/`)

Each implements the `Source` ABC from `base.py`, exposing only the capabilities it has:

| Source | Capabilities |
|---|---|
| `RpmSource` | changelog (local `rpm -q`) |
| `ObsSource` | changelog + spec |
| `GiteaSource` | changelog |
| `PagureSource` | spec |
| `BodhiSource` | news |
| `OpenSUSENewsSource` | news |
| `OpenQASource` | tests |

**Supporting modules**
- `src/spec_parser.py` — `python-specfile` AST → `SpecSection[]`
- `src/git_manager.py` — shallow clone, tag lookup, LRU disk eviction
- `src/rpm_manager.py` — `rpm -q` subprocess wrapper
- `src/sanitize.py` — strips ANSI/control bytes from external content before storage

**Scripts**
- `scripts/ingest.py` — one-shot batch ingest
- `scripts/worker.py` — cron/systemd daemon; runs sweep + news + ingest
- `scripts/bench.py` — p50/p95/p99 latency benchmarks
- `scripts/record_demos.sh` / `scripts/capture_demo_output.sh` — demo GIF and text capture

## Architecture

See [`CLAUDE.md`](CLAUDE.md) for module responsibilities, data flow, and design decisions.
