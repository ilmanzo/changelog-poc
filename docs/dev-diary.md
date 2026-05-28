# rpm-mcp Development Diary

**Project:** rpm-mcp -- MCP server for openSUSE package changelog and spec analysis
**Event:** SUSE AI Hackathon Workshop, 25--29 May 2026, Nuremberg
**Author:** Andrea Manzini

---

## Project genesis

A QA engineer files a bug. The test has been green for months. Nothing changed in the app.
But `openssl` was updated in OBS yesterday.

This is the **environment gap**: test failures that have nothing to do with application code, caused by
system-level package changes in the [Open Build Service](https://build.opensuse.org/).
Today diagnosing this means manually navigating XML API responses, clicking through the OBS web UI,
or running `rpm -q --changelog` and hoping you remember what the previous version looked like.

Worse: if `libfoo` was bumped, which packages re-link against the new ABI? There's no quick answer.

The hackathon prompt was simple: build a tool that lets an LLM answer these questions from the terminal,
in under 60 seconds, without leaving your editor.

---

## What we built

**rpm-mcp** -- a [Model Context Protocol](https://modelcontextprotocol.io/) server that gives any
MCP-compatible AI assistant unified access to:

- openSUSE OBS changelogs and `.spec` file diffs
- RPM changelog history from the local database
- Fedora Pagure and Ubuntu/Debian changelogs (cross-distro)
- GitHub / GitLab upstream release notes
- Bodhi update feeds and openQA test results
- Semantic and full-text search across all of the above

```
  Claude Code / gemini-cli / any MCP client
               |  JSON-RPC over stdio
               v
         mcp_server.py  (FastMCP)
         21 tools: changelog | deps | spec | news
               |
       +-------+-------+
       |       |       |
       v       v       v
  Database  Sources  RPMManager
  asyncpg   7 total  rpm -q subprocess
       |
       v
  PostgreSQL + pgvector
  FTS . vector search . relational
```

Since MCP is a standard protocol, it works out of the box with Claude Code, gemini-cli, OpenCode,
Cursor, Zed, Continue.dev, Windsurf -- any client that speaks the protocol.
For gemini-cli it's a few lines in `~/.gemini/settings.json`:

```json
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

One important distinction: this is a **server-side** MCP tool, not a zero-setup plugin.
It requires a running PostgreSQL instance.
That infrastructure cost is what enables the capability -- semantic search, FTS, version history,
dependency graphs -- none of which fit in a stateless plugin.
The upside: deploy one instance, every engineer's editor and every CI agent in the org connects to
the same data.

---

## Day 1 -- From zero to working MCP server (Monday 25 May 2026)

The first design decision set the tone for everything else: **one Postgres instead of two separate stores**.

The previous proof-of-concept used Qdrant for vectors and SQLite for relational data. Replacing both with
a single PostgreSQL instance running [pgvector](https://github.com/pgvector/pgvector) and `pg_trgm`
simplified ops dramatically. One container, one connection pool, one migration file.

The data model is content-addressed: every changelog entry gets a UUID primary key derived from
`uuid5(NAMESPACE, package_name + content)`. The same `.changes` block fetched from OBS and from Gitea
converges to the same row without any field comparison. No dedup logic, no conflicts.

```python
import uuid

NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

def entry_id(package: str, content: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, package + content)
```

**Ingest pipeline:**

```
  rpm -q --changelog vim
         |
         v
  SourceRegistry
  (waterfall: local rpm first -> OBS -> GitHub releases)
         |
         v
  fastembed ONNX  ->  384-dim vectors  (runs in background thread)
         |
         v
  PostgreSQL
  uuid5 dedup . tsv FTS index . HNSW vector index
```

Sources are pluggable via a `Source` ABC with four optional capabilities:
`fetch_changelog`, `fetch_spec`, `fetch_news`, `fetch_tests`.
Adding a new source is ~50 lines: implement what you have, skip the rest.

The OBS integration hit the first real friction: the API returns XML where everyone else uses JSON.
System package diffs can be massive -- a single `glibc` diff across two releases can run hundreds of kilobytes.
The server pre-processes content into 1000-character overlapping chunks before sending anything to an LLM.

By end of day 1 all changelog tools were wired and returning real data.
The first end-to-end win: asking `find_cve("CVE-2023-4738", "vim")` and getting back the exact `.changes`
entry where the fix landed.

**Where it went wrong:** the first SQL migration wasn't idempotent. `CREATE TABLE` without `IF NOT EXISTS`,
which would break every restart after the first. Caught it in review and fixed it, but it's a reminder
that generated code still needs a human pass.

### Demo

*Asking: "What are the 5 most relevant changes in vim between version 9.0 and 9.2?"*

![Changelog query demo](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_changelog.gif)

---

## Day 2 -- Spec assistant, news, and openQA (Tuesday 26 May 2026)

Day 2 was about making the server genuinely useful for the spec-analysis use case.

`python-specfile` gives a proper AST for `.spec` parsing -- section names, macros, and structure -- instead
of fragile regex over raw text. Where it breaks is non-standard specs (hand-written macros, includes,
conditional blocks). For those, the fallback is a sliding-window chunker that splits on section headers
and sends chunks to the LLM with overlap to preserve context.

Bodhi and openQA sources use a **parallel fetch strategy** for local sources (fast, no network)
and **waterfall** for remote: try the fastest authoritative source first, fall back on failure.

What got cut: Podman macro expansion (`get_expanded_spec` from the original prototype).
The cost/complexity ratio was wrong for a three-day sprint. Deferred.

### Demo

*Asking: "show me 5 packages with recent security fixes that don't have openQA coverage"*

![Untested changes demo](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_untested.gif)

---

## Day 3 -- 0 to 257 tests (Wednesday 27 May 2026)

The repo started day 3 with 35 end-to-end tests driven by gemini-cli -- slow, fragile, and useless
for CI without the full stack running. Zero unit tests.

The strategy: pure functions first. `version_utils`, `obs_parser`, `spec_parser` --
no mocks, no fixtures, just input/output assertions. Instant confidence, runs in milliseconds.
Then work outward: mock `asyncio.create_subprocess_exec` for subprocess-backed managers,
mock aiohttp sessions for HTTP sources, testcontainers for the database layer.

The refactoring ran in parallel. A `_tool_wrapper` decorator collapsed repeated boilerplate across
all 21 MCP tools -- timing, structured logging, error formatting -- into a single decorator:

```python
def _tool_wrapper(tool_name: str):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            start = time.monotonic()
            try:
                result = await fn(*args, **kwargs)
                _tlog(tool=tool_name, elapsed=time.monotonic() - start)
                return result
            except Exception as exc:
                _tlog(tool=tool_name, error=str(exc), elapsed=time.monotonic() - start)
                raise
        return wrapper
    return decorator
```

`@pytest.mark.parametrize` collapsed 30+ near-identical test functions.

End state: **257 unit tests, full suite runs in under 3 seconds**.
The DB integration tests run separately via testcontainers + Podman -- 19 tests, all green.

### Day 3, afternoon: code review burndown

With tests in place the afternoon was a focused code review pass -- 27 items, prioritised by
production impact. The interesting ones:

**Event loop blocking in the embedder.** `fastembed`'s `model.embed()` is synchronous CPU-bound
code running directly inside `async def embed_batch()`. During ingest it was freezing the entire
asyncio event loop -- no other tool call could be served until embedding finished.
Fix: `await asyncio.to_thread(lambda: list(model.embed(texts)))`. One line, measurable throughput improvement.

**Non-atomic eviction.** `evict_stale()` ran two separate `DELETE` statements -- one for
`changelog_entries`, one for `manifest` -- without a transaction. A crash between them would
leave an orphaned manifest row that would permanently mark a package as fresh even after its
data was gone. Fix: wrap both deletes in `async with conn.transaction()`.

**SSRF surface in GitLabSource.** The regex matching GitLab URLs accepted any host with a
two-segment path. A malicious upstream URL extracted from a spec file could redirect requests
to an internal host. Fix: constrain to an explicit forge allowlist already present elsewhere
in the codebase.

**GitHub and GitLab sources were 95% identical.** ~200 lines duplicated; only the URL template,
auth header key, and three JSON field names differed. Extracted a `ReleaseSource` base class
with provider-specific config injected at construction. Both sources collapsed to ~40 lines each.

**HTTP stack split.** At some point `httpx` crept in alongside `aiohttp` -- the news fetcher and
spec sources were using it while everything else used aiohttp. Two HTTP clients, two retry
policies, two session lifetimes. Consolidated to a single aiohttp stack with a shared
`make_client_session()` factory. `httpx` dropped from direct dependencies.

**Linting baseline.** Wired `ruff` with rule packs covering style, imports, type annotations,
security, and asyncio patterns. First run: 45 issues. All cleared in two commits -- 28 auto-fixed,
17 manual. Imports standardised to relative throughout `src/`.

### Demo

*Asking: "show me a summary of packages updated since last month with CVE fixes related to privilege escalation"*

![CVE timeline demo](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_cve_timeline.gif)

### Day 3, end of day: configuration and documentation

**`.env` file support.** With 25 environment variables the setup story was getting unwieldy.
`pydantic-settings` already supports `.env` file loading -- one line in the `Settings` model
config enables it. Added `.env.example` with the five variables operators actually need to touch.
OS environment variables still take precedence; the `.env` file is just a convenience.

**Architecture diagrams.** The architecture document had ASCII art and inline mermaid blocks
that only rendered in specific viewers. Converted to proper Mermaid source files and a
`scripts/render_diagrams.sh` that pulls the official mermaid-cli container via Podman to render
SVGs -- no Node.js or npm required on the host. Four diagrams committed: component overview,
ingest data flow, sync+search sequence, FTS sequence.

**This diary and the GitHub wiki.** Wrote this development diary and published it to the project
wiki at [github.com/ilmanzo/changelog-poc/wiki](https://github.com/ilmanzo/changelog-poc/wiki)
with animated GIF demos embedded directly in the page.

### Demo

*Asking: "find network-related packages whose changelog entries mention new command line flags in the last 2 months"*

![Semantic search demo](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_search.gif)

---

## Day 4 -- Hardening sprint (Thursday 28 May 2026)

A focused session to close the remaining plan items and sharpen the rough edges before wider use.

### Error hierarchy

Every unhandled exception previously surfaced as `"Error in <tool>: <raw python message>"`.
A typed hierarchy (`src/errors.py`) fixes this:

```
RPMMcpError
  ValidationError  -- invalid package name, bad date, bad query param
  DBError          -- Postgres not running, pool exhausted
  IngestError      -- ingest pipeline failure
SourceError(RPMMcpError)   -- source unavailable (already existed, now inherits base)
SourceNotFound(RPMMcpError) -- package not in any source (404)
```

`_tool_wrapper` catches each type and emits a specific, actionable message:

| Exception | User sees |
|---|---|
| `ValidationError` | `Invalid input: ...` |
| `SourceNotFound` | `Package not found in any configured source. Try sync_package first.` |
| `SourceError` | `Data source unavailable -- <msg>. Try again later.` |
| `DBError` / `asyncpg.PostgresError` | `Database error -- <msg>. Is PostgreSQL running?` |
| `TimeoutError` | `Tool '<name>' exceeded the Ns time limit. ...` |

### Tool timeouts

Two new config settings gate how long a tool call can block:

```
TOOL_TIMEOUT_FAST_S=10    # DB-read tools: find_*, list_*, get_*, compare_*
TOOL_TIMEOUT_SEARCH_S=30  # vector/FTS/live-API: semantic_search, fts_search, get_test_coverage
```

Sync/ingest tools (`sync_package`, `sync_all_distros`, `get_dependency_changes`) have no cap --
they're blocking by design and can legitimately run for minutes on large packages.

The timeout is injected as `asyncio.wait_for(fn(...), timeout)` inside `_tool_wrapper`, so no
per-tool change was needed. Every `@_tool_wrapper` call now carries `category="fast"` or
`category="search"` to select the right budget.

### Postgres startup retry

Previously the MCP server crashed immediately if Postgres wasn't ready when gemini spawned it.
`Database.connect()` now retries 5 times with exponential backoff (2s, 4s, 8s, 16s, 30s) before
raising `DBError`. Starting gemini before the container is fully ready no longer causes a
disconnect.

### Versioned migration tracking

`apply_migrations` previously re-ran every `.sql` file on every startup (safe because all files
are idempotent via `CREATE ... IF NOT EXISTS`, but wasteful). Now a `schema_migrations` table
records which files have been applied:

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

Each `.sql` file runs at most once per database. The tracking table itself is created inline --
no bootstrap paradox.

### Embedding model versioning

All vectors are currently generated by `BAAI/bge-small-en-v1.5` (384-dim). Migration
`005_embedding_versioning.sql` adds an `embedding_model TEXT` column to `changelog_entries`
and `spec_sections`. Every upsert writes the active model name. When a new model is
configured, old rows keep their original model tag. Semantic search serves mixed results
during the transition (graceful degradation); a future migration adds a second vector column
when a model with a different dimension is introduced.

### Nightly backup

`scripts/backup.sh` runs `pg_dump -Fc` and prunes files older than 7 days:

```bash
./rpm-mcp start   # Postgres
systemctl --user enable --now rpm-mcp-backup.timer   # daily backup
```

### Control script

`rpm-mcp` at the repo root wraps all infra operations:

```
./rpm-mcp start    # boot Postgres; MCP server is spawned by the client automatically
./rpm-mcp stop     # stop Postgres
./rpm-mcp status   # container state + live package/entry counts from DB
./rpm-mcp dev      # Postgres + MCP Inspector at localhost:5173
```

The server speaks stdio, so there is nothing to "pre-start" -- the MCP client (Claude Code,
gemini-cli) spawns it on demand via the entry in its config file.

### Refactors and doc improvements

- R8: removed redundant `is_local = False` from `HttpSource` (base class already defaults it)
- R10: `_HEADER_RE` and `_BACKFILL_VERSION_RE` moved from `RPMManager` class body to module level
- D1: `_init_conn` -- comment explains the per-connection pgvector codec requirement
- D3: `chunk_text` -- concrete sliding-window example in docstring
- D4: `CLEAN_RE` -- examples: `"1.2.3+git…"→"1.2.3"`, `"9.2p1"→"9.2"`
- D5: `_HEADER_RE` in obs_parser -- example matched line as comment
- D8: `get_dependencies` / `get_reverse_dependencies` -- noted that source is `rpm -q` subprocess, local-only

### get_test_coverage (replaces get_openqa_tests)

Following the TestCatalog integration a design review surfaced a cleaner interface:

- `get_openqa_tests` retired (openQA-only, DB-only)
- `get_test_coverage(package, source=None)` is the new unified tool:
  - `source=None` -- returns rows from both openQA and TestCatalog
  - `source='openqa'` -- DB-only (no live call)
  - `source='testcatalog'` -- TTL-gated live API + DB cache
  - Output: flat list with `[openqa]` / `[testcatalog]` label per row
  - Stale TestCatalog data shows the same `WARNING: source fetch failed` banner as changelogs

### Demo

The most complex demo so far -- one prompt, three tool calls, a real cross-distro answer:

*Asking: "openssl was updated last week. Which packages in my system depend on it, and did
their changelogs mention that update? Give me a cross-distro status comparison between
OpenSUSE, Ubuntu and Fedora. Summarise all findings."*

![Cross-distro demo](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_cross_distro.gif)

The MCP client picks `get_reverse_dependencies` to find local dependents, `get_dependency_changes`
to check their changelogs, and `compare_versions` for the cross-distro version table -- without
the prompt naming any tool.

---

---

## After the sprint -- production hardening

The three days got the surface area right. The week that followed made it boring enough to run
unattended.

**Stale-data is a feature, not a failure.** When OBS or Pagure is unreachable, the tool no longer
returns an error -- it serves the previously-cached data and prepends a banner:
`WARNING: source fetch failed; serving cached data from 2026-05-23T14:02Z`. The MCP client sees
the warning, the user knows the timestamp, and the agent can decide whether to trust it. Hard
failures are reserved for things that genuinely cannot be answered.

**Tiered cache by source.** News feeds, package changelogs, and spec files all change on
different timescales. The `manifest` table grew a `kind` discriminator -- news every 24h,
changelogs every 24h, specs every 7d. Saves bandwidth and respects upstream rate limits.

**Worker as a systemd timer, not a daemon.** The ingestion worker runs once per hour as a
`Type=oneshot` user unit. No long-lived process, no PID file, no supervisor --
`systemctl --user status` and `journalctl --user -u rpm-mcp-worker` are the entire ops interface.

**Fast-fail for unindexed packages.** First query on a never-seen package used to block for
5--35 seconds while ingest ran inline. Now the tool returns "package not yet indexed; ingestion
queued" immediately and dispatches the ingest as a background task. Concurrent calls for the same
package share one task. Cross-process races are absorbed by `ON CONFLICT DO NOTHING` upserts.

**Security cleanup.** Dropped `http://` from allowed git URL schemes, replaced the regex-based RSS
parser with `defusedxml.ElementTree` to neutralise XXE, replaced load-bearing `assert`s with
explicit `raise` so `python -O` doesn't silently strip safety checks. Subprocess calls are
timeout-capped; a wedged `git clone` no longer hangs a worker slot indefinitely.

**Prompt-injection defence.** Tool output is wrapped in `<rpm-mcp:untrusted-data sources="...">` so
a downstream LLM treats it as data, not instructions. `scrub_external` truncates entries at 8 KB
and counts injection marker tokens -- if >= 2 markers are found, it logs a warning.

---

## Cross-distro support (shipped post-sprint)

**F2a -- FedoraSource.** Fedora moved to `rpmautospec`; most specs have `%autochangelog` rather
than a static `%changelog`. Fix: try the standalone `changelog` file in dist-git first, fall back
to `%changelog` extraction. Auto-detect format by header signature
(`* Day Mon DD YYYY Author` = RPM style; `---` separators = OBS style).

**F2b -- UbuntuSource.** The `changelogs.ubuntu.com` binary endpoint returns 404 for most packages.
Fix: switched to the Launchpad HTML changelog page, extract `<pre>` blocks, unescape HTML entities,
parse with the existing Debian changelog parser.

**F3 -- Upstream release notes.** `IngestService._resolve_upstream_url` reads spec `URL:` /
`Source0:` tags and OBS `_service` XML to find the upstream forge URL.
`url_router.parse_upstream_url` dispatches to `GitHubSource` or `GitLabSource` which fetch
release notes via the respective REST APIs and store them as changelog entries tagged
`source='github_release'` or `source='gitlab_release'`. Optional auth via `GITHUB_TOKEN` /
`GITLAB_TOKEN` environment variables (GitHub anonymous API limit: 60 req/hr).

---

## Design decisions log

| ID | Decision | Rationale |
|---|---|---|
| DD1 | Single PostgreSQL backing store | One container, one pool, one migration file. pgvector + pg_trgm cover vectors + FTS. |
| DD2 | Content-addressed UUID PKs | `uuid5(NAMESPACE, pkg+content)` -- same block from multiple sources converges to one row. |
| DD3 | Stale-data with WARNING banner | Serve cached data on source failure rather than erroring. Let the LLM client decide trust. |
| DD4 | Per-capability source dispatch | Sources implement only what they have. Registry routes by capability. |
| DD5 | No embedded LLM | MCP clients have their own LLM. The server returns raw data. |
| DD6 | Worker as systemd timer | `Type=oneshot`, hourly, `RandomizedDelaySec=15min`. No daemon. |
| DD7 | Fast-fail readiness probe | Never block on first-time ingest. Return "queued" immediately, background dispatch. |
| DD8 | Tiered cache TTL | News 24h, changelogs 24h, specs 7d. `manifest.kind` discriminator. |
| DD9 | No Podman dependency | Macro expansion cut per scope. Security risk + complexity too high for sprint. |
| DD10 | Ingest coalescing | `_pending: dict[str, asyncio.Task]` -- concurrent calls for same package share one task. |
| DD11 | Single HTTP stack (aiohttp) | Dropped httpx after it crept in alongside aiohttp. One session factory, one retry policy. |
| DD12 | Relative imports inside src/ | Enforced by ruff. Prevents accidental coupling to the install path. |
| DD13 | .env file autoload | pydantic-settings loads `.env` from the working directory; OS env still wins. One config system. |

---

## TestCatalog integration (post-sprint)

The existing openQA coverage source works by cloning `os-autoinst-distri-opensuse` locally and
scanning `.pm` files for `# Package:` headers. This requires a fresh clone and a full disk scan
on every refresh cycle.

SUSE runs a live HTTP service -- the TestCatalog API (`testcatalog.qa.suse.de:3001`) -- that
exposes the same metadata through a searchable REST API. It is effectively the same test repo
served as a microservice, with a Swagger UI and OpenSearch-backed full-text search.

### Why add it

Three reasons:

1. **No local clone needed.** `GET /api/v1/tests?q=vim&limit=200` returns test entries with
   `sourcePath`, `comments` (containing `Package:` / `Summary:` headers), and `fullPath`
   (direct GitHub URL). The same data, without cloning gigabytes.

2. **Dual-source gap detection.** `find_untested_changes` now flags packages that have neither
   openQA rows (from the local scan) nor TestCatalog rows (from the API). False positives drop
   because the two sources have slightly different coverage -- a package not yet in the local
   clone may already be in the API's index.

3. **Future analytics.** The API also exposes `/api/v1/analytics/search` with `scope=bugs,openqa,gitlog`.
   That opens the door to "which bugs are associated with tests for this package?" -- a future
   `find_bugs_in_tests` tool without new data ingestion.

### What changed

The `openqa_tests` table gained a `source TEXT NOT NULL DEFAULT 'openqa'` column (migration
`004_testcatalog.sql`). The UNIQUE constraint now covers `(package_id, test_path, source)`,
so the same `.pm` path can exist once per source without conflict.

`upsert_openqa(tests, source="testcatalog")` and `get_openqa_tests(package, source=...)` both
accept an optional `source` parameter. Callers that don't pass it get the old behavior.

A new `TestCatalogClient` (aiohttp, same session factory as all other HTTP sources) handles
paging, package filtering (same `_PKG_RE` regex as `openqa_fetcher.py`), and content sanitization.

The new `get_test_coverage(package, source=None)` MCP tool unifies openQA and TestCatalog
data; it queries the live TestCatalog API when the cache is stale, writes results to the DB,
and falls back to cached rows with a WARNING banner when the API is unreachable.
(An earlier `get_testcatalog_tests` was superseded by this unified tool.)

### Auth

Read endpoints are public -- no credentials required. A Bearer JWT is accepted for write
operations (summary reviews). `TESTCATALOG_API_KEY` env var carries the token when set.

## What's next

Once the server is stable it can be exposed as a shared service -- one instance per team,
every engineer's editor (and every CI job) connects to the same data.

Imagine a SLES installation where any engineer can ask:

> "What changed in SLES 16 over the last 3 months that could affect my package?"

That's a single query to this server. The packaging process becomes observable and explainable
to anyone with an MCP-capable tool, without needing to know the OBS API, XML parsing, or
where changelogs live.
