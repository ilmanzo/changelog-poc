# rpm-mcp User Guide

End-to-end guide for deploying and using rpm-mcp -- an MCP server that gives any
MCP-compatible AI assistant unified access to openSUSE/Fedora/Ubuntu package metadata.

---

## 1. Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.13+ | Managed by `uv` |
| `uv` | latest | <https://docs.astral.sh/uv/> |
| Podman (or Docker) | any recent | For the PostgreSQL container |
| Operating system | Linux | Tested on openSUSE Tumbleweed; should work on any modern Linux |
| Disk | ~3 GB | fastembed model + Postgres data + a few thousand ingested packages |
| RAM | 2 GB free | Postgres + Python + fastembed ONNX runtime |
| Optional: `rpm` binary | local | Required only for local-RPM tools (`get_dependencies`, etc.) |

No cloud services, no API keys required by default. All defaults work for local use.

---

## 2. Install

```bash
git clone https://github.com/ilmanzo/changelog-poc.git
cd changelog-poc
uv sync
```

`uv sync` installs the project and pins all dependencies from `uv.lock`.

---

## 3. Configure

All settings are environment variables. Defaults work for local use. Override what you need
via a `.env` file at the repo root (loaded automatically by `pydantic-settings`):

```bash
cp .env.example .env
$EDITOR .env
```

Most-touched variables:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp` | Postgres DSN |
| `LOG_FORMAT` | `text` | Set `json` for structured logs in production |
| `CACHE_TTL_CHANGELOG_S` | `86400` | Re-fetch a package's changelog after this many seconds |
| `CACHE_TTL_NEWS_S` | `86400` | News feed TTL |
| `CACHE_TTL_SPEC_S` | `604800` | Spec file TTL (7 days) |
| `TOOL_TIMEOUT_FAST_S` | `10` | Timeout for DB-read tools |
| `TOOL_TIMEOUT_SEARCH_S` | `30` | Timeout for vector/FTS/live-API tools |
| `GITHUB_TOKEN` | empty | Anonymous works (60 req/h); set token for 5000 req/h |
| `GITLAB_TOKEN` | empty | Same as above |
| `TESTCATALOG_URL` | `http://testcatalog.qa.suse.de:3001` | SUSE TestCatalog endpoint |
| `TESTCATALOG_API_KEY` | empty | Optional Bearer JWT (only needed for write ops) |

See `src/config.py` for the full list (~25 settings).

---

## 4. Start the infrastructure

The MCP server depends on PostgreSQL with pgvector. The `./rpm-mcp` script wraps the container
lifecycle:

```bash
./rpm-mcp start    # boot Postgres + pgvector container
./rpm-mcp status   # container state + package/entry/test row counts
./rpm-mcp stop     # stop Postgres
./rpm-mcp logs     # tail Postgres logs
./rpm-mcp psql     # interactive psql shell
./rpm-mcp dev      # start Postgres + MCP Inspector UI at http://localhost:5173
```

`./rpm-mcp start` prints a JSON snippet to paste into your MCP client config.

The MCP server itself is **not** pre-started -- it is spawned automatically by your MCP client
(Claude Code, gemini-cli, etc.) via stdio.

---

## 5. Register with your MCP client

### One-liner (recommended)

```bash
./rpm-mcp register claude       # register with Claude Code
./rpm-mcp register gemini       # register with gemini-cli
./rpm-mcp register all          # both at once
```

This delegates to `scripts/register.sh`, which auto-detects the repo root and writes the
correct paths. To remove a registration: `./rpm-mcp unregister {claude|gemini|all}`.

### Manual gemini-cli setup

If you'd rather edit `~/.gemini/settings.json` yourself:

```json
{
  "mcpServers": {
    "rpm": {
      "command": "uv",
      "args": ["run", "python", "mcp_server.py"],
      "cwd": "/abs/path/to/changelog-poc",
      "env": {
        "DATABASE_URL": "postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp"
      }
    }
  }
}
```

### Verify

```bash
./scripts/register.sh status              # both clients
gemini /mcp                               # gemini-side check
claude mcp list                           # claude-side check
```

---

## 6. Ingest data

The DB starts empty. Populate it with the worker:

```bash
# Quick start: ingest a few packages by name
uv run scripts/ingest.py vim curl openssl

# Or: ingest the top-N installed packages on this host
./scripts/ingest_core.sh 100

# Or: full worker pass (refresh news, openQA, then ingest)
uv run scripts/worker.py --all --file packages.txt

# Cross-distro: also pull from Fedora and Ubuntu sources
CROSS_DISTRO=1 ./scripts/ingest_core.sh 50

# Periodic batch: enable the systemd timer
systemctl --user enable --now rpm-mcp-worker.timer
```

Worker flags:

| Flag | Effect |
|---|---|
| `--file packages.txt` | Ingest packages listed in file (one per line) |
| `--news` | Refresh Bodhi + openSUSE news feeds |
| `--openqa PATH` | Scan a local os-autoinst-distri-opensuse checkout for test coverage |
| `--test-repo` | Clone the openQA repo automatically and scan it |
| `--testcatalog` | Fetch test coverage from the TestCatalog API |
| `--sweep` | Evict cache rows older than their per-kind TTL |
| `--all` | Sweep + news + ingest from `--file` |
| `--concurrency N` | Override worker fan-out (default 10) |

---

## 7. Use it from your MCP client

Once the server is registered and the DB has data, ask your AI assistant questions like:

> "What are the 5 most relevant changes in vim between version 9.0 and 9.2?"
> "Show me packages with security fixes in the last 30 days that have no openQA coverage."
> "openssl was updated last week. Which packages depend on it, and did their changelogs mention it?"
> "Find a CVE affecting curl since 2025-01-01."

The assistant picks the right tool from rpm-mcp's surface (18 tools) automatically.

---

## 8. Direct CLI use

Every MCP tool is also runnable from the shell. Useful for scripting and one-off queries:

```bash
./rpm-mcp serve                           # run as MCP stdio server
./rpm-mcp find-cve CVE-2023-4738          # find a CVE across all packages
./rpm-mcp get-test-coverage vim           # tests covering a package
./rpm-mcp compare-versions openssl        # cross-distro version comparison
./rpm-mcp semantic-search "ssl handshake" # semantic search
./rpm-mcp fts-search "buffer overflow"    # full-text search
./rpm-mcp sync-package curl               # ingest one package on demand
./rpm-mcp sync-all-distros openssl        # ingest from all distro sources
./rpm-mcp <tool-name> --help              # per-tool help
```

Full tool list:

| Category | Tools |
|---|---|
| Changelog | `analyze_package_diff`, `get_recent_releases`, `get_changes_in_range`, `find_cve`, `list_cves`, `find_bug`, `list_bugs`, `semantic_search`, `fts_search`, `compare_versions`, `sync_package`, `sync_all_distros` |
| Dependencies | `get_dependencies`, `get_reverse_dependencies`, `get_dependency_changes`, `find_core_packages` |
| Spec files | `get_spec_details` |
| News / coverage | `get_news`, `get_test_coverage`, `get_sync_status`, `find_untested_changes` |

---

## 9. Backup

A daily `pg_dump` to `~/rpm-mcp-backup/` with 7-day retention is provided as a systemd user unit:

```bash
# Manual backup
./scripts/backup.sh

# Or enable the daily timer
mkdir -p ~/.config/systemd/user
ln -s "$PWD/packaging/systemd/rpm-mcp-backup.service" ~/.config/systemd/user/
ln -s "$PWD/packaging/systemd/rpm-mcp-backup.timer" ~/.config/systemd/user/
ln -s "$PWD/scripts/backup.sh" ~/.local/share/rpm-mcp/backup.sh
systemctl --user enable --now rpm-mcp-backup.timer
```

Override the backup dir with `RPM_MCP_BACKUP_DIR=/path` in the unit's environment.

Restore from a dump:

```bash
./rpm-mcp stop
podman exec rpm-mcp-postgres dropdb -U rpm_mcp rpm_mcp
podman exec rpm-mcp-postgres createdb -U rpm_mcp rpm_mcp
pg_restore -d "$DATABASE_URL" ~/rpm-mcp-backup/rpm-mcp-YYYYMMDD-HHMM.pgdump
./rpm-mcp start
```

---

## 10. Troubleshooting

### "MCP issues detected" in gemini-cli or Claude Code

The server failed to start. Most likely cause: Postgres isn't running.

```bash
./rpm-mcp status                          # check the container
./rpm-mcp start                           # boot it
```

The server itself retries Postgres 5 times with exponential backoff before failing, so a brief
race at boot is usually self-healing.

### "Database error -- Database.connect() not called"

The `lifespan` async context manager failed. Check the server output:

```bash
uv run python mcp_server.py 2>&1 | head -30
```

### Slow first query

The fastembed ONNX model downloads on first use (~50 MB into `~/.cache/fastembed/`). Subsequent
queries reuse the cached model.

### `EACCES: permission denied, scandir 'infra/pg_data'`

The Postgres container creates `infra/pg_data` with restrictive permissions. The `.geminiignore`
file at the repo root tells gemini-cli to skip it. If your client doesn't honour that file,
move `pg_data` elsewhere and bind-mount it.

### Tool times out (`Tool 'X' exceeded the 30s time limit`)

Either your query is too broad (try a more specific filter) or the underlying source is slow.
Raise the budget if needed:

```bash
TOOL_TIMEOUT_SEARCH_S=60 ./rpm-mcp start
```

Sync/ingest tools (`sync_package`, `sync_all_distros`) have no timeout by design.

### "WARNING: source fetch failed; serving cached data from <ts>"

The upstream source (OBS, Gitea, GitHub, TestCatalog) is unreachable. The server fell back to
cached rows from the timestamp shown. Either wait for the source to recover or pass `refresh=False`
to force the cache path.

### Migration errors on startup

The `schema_migrations` table records which `.sql` files have been applied. To force a re-run
of a specific migration:

```sql
DELETE FROM schema_migrations WHERE version = '004_testcatalog.sql';
```

Then restart the server.

---

## 11. Architecture cheat sheet

```
  MCP client (Claude Code / gemini-cli / OpenCode / Cursor / ...)
              |
              |  JSON-RPC over stdio
              v
        mcp_server.py  (FastMCP)
              |
        +-----+-----+-----+-----+
        |     |     |     |     |
        v     v     v     v     v
    changelog deps spec news cli  (src/tools/)
              |
              v
        src/runtime.py
        (db, source_registry, ingest_service, ...)
              |
        +-----+-----+
        |           |
        v           v
   Database     SourceRegistry
   (asyncpg)    waterfall: rpm -> obs -> gitea -> fedora -> ubuntu
        |           |
        v           v
   PostgreSQL   HTTP / subprocess
   + pgvector
   + pg_trgm
```

For deeper dives, see `docs/architecture.md` (component diagrams) and `docs/THREAT_MODEL.md`
(security boundaries).

---

## 12. Next steps

- Read the [Development Diary](dev-diary.md) for the project's design history.
- Read the [Architecture doc](architecture.md) for the schema and source registry details.
- Read the [Threat Model](THREAT_MODEL.md) for security considerations.
- File issues at <https://github.com/ilmanzo/changelog-poc/issues>.
