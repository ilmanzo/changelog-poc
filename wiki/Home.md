# rpm-mcp

MCP server for querying RPM package changelogs, CVEs, specs, news, and openQA test mappings.
Backed by PostgreSQL + pgvector. Works with Claude Code, gemini-cli, Cursor, Zed, and any
MCP-compatible client.

## Pages

- [Development Diary](Development-Diary) -- sprint journal, architecture, design decisions, demos
- [Architecture](https://github.com/ilmanzo/changelog-poc/blob/main/docs/architecture.md) -- full technical reference (in repo)
- [Schema](https://github.com/ilmanzo/changelog-poc/blob/main/docs/schema.md) -- PostgreSQL schema reference
- [Threat Model](https://github.com/ilmanzo/changelog-poc/blob/main/docs/THREAT_MODEL.md) -- security analysis

## Quick start

```bash
# Start Postgres
./infra/infra.sh start

# Install deps and run
uv sync
uv run mcp_server.py

# Configure gemini-cli (~/.gemini/settings.json)
{
  "mcpServers": {
    "rpm": {
      "command": "uv",
      "args": ["run", "mcp_server.py"],
      "cwd": "/path/to/rpm-mcp"
    }
  }
}
```

Copy `.env.example` to `.env` and set `DATABASE_URL` and optionally `GITHUB_TOKEN`.
