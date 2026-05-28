# rpm-mcp

MCP server for querying RPM package changelogs, CVEs, specs, news, and openQA test mappings.
Backed by PostgreSQL + pgvector. Works with Claude Code, gemini-cli, Cursor, Zed, and any
MCP-compatible client.

## Pages

| Page | What's in it |
|---|---|
| [User Guide](User-Guide) | Deploy, configure, ingest, query, troubleshoot |
| [Developer Guide](Developer-Guide) | Code structure, key abstractions, design patterns, how to extend |
| [Architecture](Architecture) | Component diagrams, source registry, env vars |
| [Schema](Schema) | PostgreSQL tables, indexes, content-addressed UUIDs |
| [Threat Model](Threat-Model) | Trust boundaries, prompt-injection defences, accepted risks |
| [Development Diary](Development-Diary) | Sprint journal with design decisions and demos |
| [Changelog](Changelog) | CalVer release history |

## Demos

| Demo | Description |
|---|---|
| [Vim changelog query](Demo-Changelog) | Compare two versions of a package and surface the most relevant changes |
| [Untested security fixes](Demo-Untested) | Find packages with security fixes that lack openQA test coverage |
| [CVE privilege escalation timeline](Demo-CVE-Timeline) | Summarise packages with privilege-escalation CVE fixes in the last month |
| [Semantic search](Demo-Search) | Find packages by topic across all cached changelog entries |
| [Cross-distro blast radius](Demo-Cross-Distro) | Trace openssl dependents and compare versions across distros |
| [QA triage -- systemd](Demo-Systemd-Bugs) | Bugs + tests + changelog for systemd in one prompt |
| [QA triage -- openssl](Demo-Openssl-Bugs) | Bugs + tests + changelog for openssl in one prompt |
| [Stale test cleanup](Demo-Stale-Tests) | Find tests covering features that were removed upstream |

## Quick start

```bash
git clone https://github.com/ilmanzo/changelog-poc.git
cd changelog-poc
uv sync

# 1. Start the Postgres container
./rpm-mcp start

# 2. Register with your MCP client (Claude Code, gemini-cli, or both)
./rpm-mcp register gemini       # or: claude   or: all

# 3. Populate the DB with a few packages
uv run scripts/ingest.py vim curl openssl
```

That's it -- launch your MCP client and start asking questions.

Then ask your AI assistant questions like:

> *"What are the 5 most relevant changes in vim between version 9.0 and 9.2?"*
> *"openssl was updated last week. Which packages depend on it, and did their changelogs mention it?"*
> *"Show me packages with security fixes in the last 30 days that have no openQA coverage."*

See the [User Guide](User-Guide) for the full deployment + usage flow.

---

_Source repo: <https://github.com/ilmanzo/changelog-poc>_
