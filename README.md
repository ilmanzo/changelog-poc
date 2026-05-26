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
| `LLM_BASE_URL` | `http://localhost:11438` | OpenAI-compatible proxy; only needed for LLM tools |
| `LLM_MODEL` | `microsoft_Phi-4-mini-instruct-Q4_K_M.gguf` | Model for `analyze_package`, `explain_build`, etc. |
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
| `analyze_package` | LLM Q&A on spec |
| `explain_build` | LLM walkthrough of build stages |
| `modernize_package` | Detect + rewrite deprecated macros |
| `get_news` | Bodhi + RSS news for a package |
| `get_openqa_tests` | openQA test mappings |
| `sync_package` | Force re-ingest a package |

> Tools marked with "LLM" require a running OpenAI-compatible proxy at `LLM_BASE_URL`.

## Security

### LLM proxy must not expose tool-use / function-calling

`ask_llm` feeds untrusted third-party content (OBS changelogs, spec files, news) into the model as CONTEXT. The proxy at `LLM_BASE_URL` **must be configured with tool-use / function-calling disabled**, otherwise an injected instruction inside a malicious package's changelog could trigger side-effectful tool calls on the host running the proxy.

Concretely:
- llama.cpp / llama-server: do not pass `--chat-template tool-calling` or any function-calling flag
- vLLM: do not enable `--enable-auto-tool-choice`
- Ollama: the default Modelfile has no tools — safe
- Hosted providers: pick an endpoint that does not advertise tools

The system prompt and nonce-fenced context (`src/llm.py`) reduce the prompt-injection blast radius but cannot prevent a tool-call-enabled proxy from executing maliciously-crafted instructions.

### External content sanitisation

All text fetched from external sources is run through `src/sanitize.py:scrub_external` at parse time — strips ANSI escapes, null bytes, BOM, and C0/C1 control bytes before storage or display.

## Architecture

See [`CLAUDE.md`](CLAUDE.md) for module responsibilities, data flow, and design decisions.
