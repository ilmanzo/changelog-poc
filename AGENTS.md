# AGENTS.md

Compact ramp-up for OpenCode sessions. See `CLAUDE.md` for full architecture.

## Commands

```bash
uv sync                                    # install deps (Python 3.13, managed by uv)
infra/infra.sh start                       # boot Postgres+pgvector container on :5432
infra/infra.sh psql                        # interactive psql

# Tests — always use scripts/test.sh, not bare pytest (it sets Podman env vars)
./scripts/test.sh unit                     # fast, no container
./scripts/test.sh e2e-db                   # testcontainers Postgres (requires Podman socket)
./scripts/test.sh e2e-opencode             # OpenCode+Ollama e2e (requires SUSE internal network)
./scripts/test.sh e2e-edge [filter]        # subset of e2e-opencode: edge_* prompt cases
./scripts/test.sh all

# Focused unit test (PYTHONPATH=. required)
PYTHONPATH=. uv run pytest tests/test_foo.py::test_bar -v

# Type check
uv run mypy src mcp_server.py

# One-shot CLI call for any MCP tool
uv run mcp_server.py <tool-name> --help
uv run mcp_server.py get-recent-releases vim --n 5

# MCP Inspector UI
uv run mcp dev mcp_server.py
```

## Testing quirks

- `pyproject.toml` sets `addopts = "-m 'not e2e'"` → bare `pytest` **always skips e2e**.
- e2e tests require `DOCKER_HOST=unix:///run/user/$UID/podman/podman.sock` and
  `TESTCONTAINERS_RYUK_DISABLED=true`. `scripts/test.sh` sets both; manual runs must set them.
- `tests/test_db.py`, `tests/test_e2e_gemini.py`, and `tests/test_e2e_opencode.py` are the e2e files; everything else is unit.
- `tests/test_e2e_opencode.py` auto-skips when the SUSE internal Ollama endpoint is unreachable (no VPN = skip, not fail).
- `asyncio_mode = "auto"` is set — no `@pytest.mark.asyncio` needed on individual tests.
- Module-scoped async fixtures must use `loop_scope="module"` + `pytestmark = pytest.mark.asyncio(loop_scope="module")`.

## Architecture invariants

- **All SQL lives in `src/db.py`** — never add DB calls in tool modules or services.
- **`src/runtime.py`** owns every process-wide singleton (`db`, `ingest_service`, `source_registry`, etc.).
  Tool modules import from there; never instantiate these elsewhere.
- **Migrations** (`migrations/*.sql`) are applied idempotently by `Database.connect()` on every startup.
  New schema changes → new numbered `.sql` file; never edit existing ones.
- Tool output in MCP mode is wrapped in `<rpm-mcp:untrusted-data>` XML envelope.
  CLI mode suppresses it via `suppress_untrusted_envelope()` in `src/tools/_wrap.py`.

## Adding a new tool

1. Implement the async function in the relevant `src/tools/<module>.py`.
2. Export it in that module's `CLI_TOOLS` tuple and call `mcp.tool()(fn)` inside `register(mcp)`.
3. No changes to `mcp_server.py` needed; `register_all` calls each module's `register`.

## Adding a new changelog source

1. Implement `ChangelogSource` ABC from `src/sources/base.py`.
2. Add an instance to the `sources=[...]` list in `src/runtime.py`.

## Style

- `ruff` lints + formats: line-length 110, double quotes, space indent.
- `mypy` strict: `disallow_untyped_defs`, `disallow_incomplete_defs` — all functions need annotations.
- `from __future__ import annotations` in every module (PEP 563 — already standard here).
- Use `structlog` for logging; no `logging.getLogger` in tool/service code.
- SQL params always positional placeholders (`$1`, `$2`, …); `db.py` enforces a safe-WHERE-clause whitelist.

## Env / config

- Settings via `pydantic_settings` (`src/config.py`); `.env` auto-loaded if present.
- `DATABASE_URL` default: `postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp`
- `LOG_FORMAT=json` for structured prod logs (default: `text`).
- `GITHUB_TOKEN` / `GITLAB_TOKEN` — optional; anonymous access works without them.
- `FETCH_STRATEGY=waterfall|parallel` (default: `waterfall`).
