# rpm-mcp

MCP server for querying openSUSE/Fedora RPM changelogs, specs, CVEs, news, and openQA test mappings. Backed by PostgreSQL + pgvector.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [Podman](https://podman.io/) (for the database container)
- Python 3.13 (uv manages this automatically)

## Quick Start

```bash
cd rpm-mcp

# 1. Start PostgreSQL + pgvector
infra/infra.sh start

# 2. Install dependencies
uv sync

# 3. Run the MCP server (stdio transport)
uv run mcp_server.py
```

Migrations run automatically on startup. No manual DB setup needed.

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
| `find_cve` / `list_cves` | CVE lookup |
| `find_bug` / `list_bugs` | Bug reference search |
| `semantic_search` | pgvector cosine similarity search |
| `fts_search` | Full-text search |
| `get_spec_details` | RPM spec file sections |
| `get_news` | Bodhi + RSS news for a package |
| `get_openqa_tests` | openQA test mappings |
| `sync_package` | Force re-ingest a package |

## Prompt Examples

See [`prompt_examples.md`](prompt_examples.md) for a collection of useful queries and discovery patterns.

## Security

### External content sanitisation

All text fetched from external sources is run through `src/sanitize.py:scrub_external` at parse time — strips ANSI escapes, null bytes, BOM, and C0/C1 control bytes before storage or display.

## Architecture

See [`CLAUDE.md`](CLAUDE.md) for module responsibilities, data flow, and design decisions.
