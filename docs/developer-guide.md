# rpm-mcp Developer Guide

Code structure, key abstractions, design patterns, and how to extend the project.

For the user-facing setup/usage flow see [user-guide.md](user-guide.md).
For high-level diagrams and env vars see [architecture.md](architecture.md).

---

## 1. Code organisation

```
rpm-mcp/
├── mcp_server.py              FastMCP entrypoint (~30 lines; wires runtime + tools + CLI)
├── rpm-mcp                    Shell wrapper: infra subcommands + CLI dispatch
├── migrations/                Idempotent .sql files applied at startup
├── src/
│   ├── runtime.py             Process-wide singletons (db, rpm_mgr, ingest_service, ...)
│   ├── config.py              pydantic-settings Settings class (env-driven)
│   ├── errors.py              Typed exception hierarchy
│   ├── db.py                  Database class (asyncpg + pgvector); owns ALL SQL
│   ├── ingest.py              IngestService -- fetch -> embed -> upsert
│   ├── embedder.py            fastembed singleton, chunk_text
│   ├── models.py              Frozen dataclasses (ChangelogEntry, OpenQATest, ...)
│   ├── cli.py                 argparse subparsers auto-generated from tool signatures
│   ├── sanitize.py            scrub_external() -- prompt-injection sanitisation
│   ├── http_utils.py          aiohttp session factory shared by all HTTP sources
│   ├── process.py             run_subprocess() helper used by rpm_manager / git_manager
│   ├── rpm_manager.py         rpm -q subprocess wrapper + changelog parser
│   ├── git_manager.py         Shallow clone, log/tag lookups, alru_cache
│   ├── obs_parser.py          Parser for OBS/Gitea .changes file format
│   ├── debian_parser.py       Parser for Debian/Ubuntu changelog format
│   ├── spec_parser.py         python-specfile AST wrapper
│   ├── spec_url_extractor.py  Extract forge URLs from spec headers
│   ├── service_file_parser.py Parse OBS _service XML (defusedxml)
│   ├── news_fetcher.py        Bodhi + openSUSE RSS feeds (standalone)
│   ├── openqa_fetcher.py      Scan local os-autoinst-distri-opensuse for # Package: headers
│   ├── testcatalog_client.py  HTTP client for the SUSE TestCatalog API
│   ├── test_repo_manager.py   Clone/pull os-autoinst-distri-opensuse
│   ├── test_coverage_parser.py Heuristic package extraction from .pm files
│   ├── logging_config.py      structlog setup
│   ├── version_utils.py       Pure helpers: clean_version, parse_when, CVE_RE, BSC_RE
│   ├── sources/               Pluggable source layer (see § 5)
│   └── tools/                 MCP tool modules (see § 4)
├── scripts/
│   ├── ingest.py              Offline batch ingest
│   ├── worker.py              Centralised ingestion daemon (cron / systemd)
│   ├── ingest_core.sh         Pre-ingest top N installed packages
│   ├── backup.sh              Nightly pg_dump + 7-day retention
│   ├── register.sh            Add/remove the MCP server in Claude or gemini config
│   ├── doctor.py              Health check + --fix mode (./rpm-mcp doctor)
│   └── lint.sh                ruff check/format + mypy wrapper
├── tests/                     Unit tests (default) + e2e (testcontainers + gemini-cli)
├── packaging/systemd/         User-mode .service / .timer units for worker + backup
└── docs/                      User and developer documentation
```

---

## 2. Key abstractions

### `Database` -- `src/db.py:56`

Owns the asyncpg pool, the pgvector codec registration, and *every* SQL statement in the project.
No other module talks to Postgres directly.

```python
db = Database()                          # from src.runtime
await db.connect()                       # idempotent; retries 5x on failure
await db.upsert_package("vim")           # -> package_id
await db.upsert_changelog_entries(...)   # bulk; content-addressed UUID dedup
rows = await db.fts_search("CVE-2024-1234")
```

Connection initialisation runs `register_vector(conn)` on every new pool member -- without it
the asyncpg driver returns raw bytes instead of float lists.

### `ChangelogSource` -- `src/sources/base.py:39`

ABC with one abstract method (`fetch`) and a default `close()` hook. Subclasses set three class
variables:

```python
class MySource(ChangelogSource):
    name = "mysrc"
    distro = "opensuse"     # | "fedora" | "ubuntu" | ...
    is_local = False        # True -> tried first in PARALLEL strategy

    async def fetch(self, package: str) -> FetchResult:
        ...
```

Errors are typed: `SourceNotFound` (HTTP 404 / package absent -- next source tried),
`SourceError` (transient 5xx / connection error -- registry logs warning, sets
`fetch_failed=True` on the result).

### `SourceRegistry` -- `src/sources/registry.py:20`

Holds the ordered list of sources, applies a fetch strategy:

| Strategy | Behaviour |
|---|---|
| `WATERFALL` (default) | Try sources left-to-right; return on first non-empty result |
| `PARALLEL` | Local sources first sequentially; then network sources fan out via `asyncio.gather` |

Both filter by `distro` when `fetch(package, distro=...)` is called.

### `IngestService` -- `src/ingest.py:53`

Coordinates fetch -> embed -> upsert for a single package. Two public entry points:

| Method | Use case |
|---|---|
| `await ingest(pkg)` | Block on the result; `IngestResult` returned |
| `schedule(pkg)` | Fire-and-forget; returns the in-flight `asyncio.Task` |

In-flight ingests are **coalesced** via `_pending: dict[(pkg, distro), Task]`. Multiple callers
for the same package share one task. Cleanup is automatic on task completion.

The `_enrich_upstream` step resolves a GitHub/GitLab URL from the spec or OBS `_service` XML
and pulls release notes -- best-effort, never blocks the main ingest.

### `_tool_wrapper` -- `src/tools/_wrap.py:76`

Decorator wrapping every MCP tool. Provides:

1. Per-task `contextvar` reset (prevents cross-task leakage)
2. Structured logging with bound tool name + arg fields
3. Timeout enforcement via `asyncio.wait_for` (category-driven)
4. Typed exception dispatch -> actionable user-facing message
5. Stale-data WARNING banner if `_mark_stale()` was called inside the body
6. Output envelope `<rpm-mcp:untrusted-data sources="...">` for prompt-injection defence (S7b)
7. Terminal log record with `tool.duration_ms` + `tool.category` (DD21)

```python
@_tool_wrapper("get_recent_releases", untrusted_sources=("rpm", "obs"), category="fast")
async def get_recent_releases(package: str, n: int = 5) -> str:
    ...
```

### `Settings` -- `src/config.py`

pydantic-settings `BaseSettings`. Loads from environment variables, with `.env` file fallback
in the working directory. ~25 settings; defaults are production-safe for local use.

### Frozen dataclasses -- `src/models.py`

All domain types are `@dataclass(frozen=True)`. No business logic on them -- pure data
carriers. Frozen because they're passed around freely between layers; immutability prevents
spooky-action-at-a-distance bugs.

---

## 3. Design patterns

### Process-wide singletons via `src/runtime.py`

Instead of dependency injection or service-locator gymnastics, long-lived objects are
instantiated as module-level names in `src/runtime.py`. Tools import what they need:

```python
from ..runtime import db, ingest_service, rpm_mgr
```

This works because tools never need a *different* DB or ingest service. The `lifespan` async
context manager (passed to FastMCP) handles `db.connect()` / `db.close()` cleanly. No globals
are touched between requests.

### Content-addressed UUIDs (DD2)

Every changelog entry's primary key is `uuid5(NAMESPACE, package_name + content)`. The same
`.changes` block fetched from OBS *and* Gitea converges to one row -- `ON CONFLICT (id) DO NOTHING`
makes every upsert idempotent. No dedup logic, no comparing fields.

### Capability-based source dispatch (DD4)

Rather than a fat base class with optional methods, each source advertises capabilities. The
registry dispatches `fetch_changelog`, `fetch_spec`, `fetch_news`, `fetch_tests` separately.
Sources implement only what they have. (`ChangelogSource` is the historical name -- newer
capabilities live in sibling ABCs like `SpecSource`.)

### Stale-data banner via `contextvar` (DD3)

Inside a tool body, `_mark_stale(synced_at)` flags the call as serving stale data. The
decorator's finally block prepends a `WARNING: source fetch failed; serving cached data
from <ts>` banner *outside* the untrusted-data envelope (so the warning itself is trusted).

```python
if source_failed and cache_exists:
    _mark_stale(synced_at)
    return cached_rows
```

### Fast-fail readiness probe (DD7)

For never-ingested packages, the tools return *"package not yet indexed; ingestion queued"*
**immediately** and dispatch ingest in the background. Helper:

```python
if (msg := await queued_msg_or_none(package)) is not None:
    return msg
```

Callers don't block on a first-time ingest. The next call (~5 s later) sees fresh data.

### Tiered cache TTL via `manifest.kind` (DD12)

Each `(package_id, kind)` pair in the `manifest` table has a `synced_at` timestamp.
`db.is_fresh(pkg_id, ttl_s, kind)` makes the decision per kind:

| Kind | Default TTL | Env var |
|---|---|---|
| `changelog` | 24h | `CACHE_TTL_CHANGELOG_S` |
| `news` | 1h | `CACHE_TTL_NEWS_S` |
| `spec` | 7d | `CACHE_TTL_SPEC_S` |
| `testcatalog` | 24h (reuses changelog TTL) | -- |

### Whitelisted dynamic SQL (S1)

`_fetch_text_search` builds WHERE clauses dynamically. Every clause must match a strict regex:
`^[a-zA-Z0-9_. ]+\s*(=|ILIKE|~\*|>=|<=|@@|BETWEEN)\s*\$\d+$`. RHS is *always* a parameter
placeholder. Anything else raises `ValueError`.

### Untrusted content sanitisation (S7c)

Every parser calls `scrub_external(text, package, source)` before constructing dataclass
instances. It strips ANSI escapes, control chars, BOM, null bytes; truncates per
`cache_max_entry_bytes`; logs `possible_injection` at WARNING when high-signal markers
(`<|`, `[INST]`, `### `, `SYSTEM`) cluster.

### Output envelope (S7b)

`_tool_wrapper` wraps every tool response in `<rpm-mcp:untrusted-data sources="...">...</rpm-mcp:untrusted-data>`
when `untrusted_sources` is non-empty. Suppressed on the CLI path (`suppress_untrusted_envelope()`)
so humans don't see XML tags.

### Typed exception hierarchy (DD23)

```
RPMMcpError                        src/errors.py
├── ValidationError                Invalid input (package name, date)
├── DBError                        Postgres failures
├── IngestError                    Pipeline failures outside source/DB
SourceError(RPMMcpError)           Transient source failure   (src/sources/base.py)
SourceNotFound(RPMMcpError)        Package not in source (404)
```

`_tool_wrapper` matches by type and emits actionable messages -- never a raw stack trace.

### Per-category tool timeouts (DD16)

Each `@_tool_wrapper(category="fast" | "search" | None)` selects a timeout from settings.
FAST = 10s for DB reads, SEARCH = 30s for vector/FTS/live-API, `None` = no cap for sync/ingest.

### Versioned migration tracking (DD20)

`apply_migrations` consults a `schema_migrations(version PK, applied_at)` table -- each
`migrations/NNN_*.sql` runs at most once. The tracking table itself is created inline (no
bootstrap paradox). Migration files must still be idempotent (`CREATE ... IF NOT EXISTS`,
guarded `ALTER`s).

---

## 4. Adding a new MCP tool

1. Pick the right module (`changelog.py`, `deps.py`, `spec.py`, `news.py`).
2. Write an `async def my_tool(...) -> str` with the `@_tool_wrapper` decorator:

   ```python
   @_tool_wrapper("my_tool", untrusted_sources=("obs",), category="fast")
   async def my_tool(package: str, limit: int = 10) -> str:
       validate_package_name(package)
       if (msg := await queued_msg_or_none(package)) is not None:
           return msg
       rows = await db.my_query(package, limit)
       _tlog(count=len(rows))
       if not rows:
           return f"No data for {package!r}."
       return _format_results(rows)
   ```

3. Append the function to that module's `CLI_TOOLS` tuple at the bottom.
   `register(mcp)` and `src/cli.py` pick it up automatically -- no other wiring needed.
4. Add a unit test in `tests/test_<module>.py`. Use `parametrize` for the truth table; mock
   `db.my_query` with `AsyncMock`.

The argparse CLI subparser is auto-generated from the function signature (types inferred via
`_resolve_param_type`). Defaulted params become `--flags`; required positional params stay
positional.

---

## 5. Adding a new source

1. Inherit from `HttpSource` (for HTTP-based sources, gets retries + session factory for free)
   or `ChangelogSource` directly:

   ```python
   class MySource(HttpSource):
       name = "mysrc"
       distro = "opensuse"

       async def fetch(self, package: str) -> FetchResult:
           url = f"https://example.com/api/{package}"
           text = await self._fetch_text(url)        # raises typed exceptions
           entries = my_parser.parse(text)
           return FetchResult(entries=entries, source_name=self.name)
   ```

2. Register it in `src/runtime.py:source_registry` ordered alongside the existing sources.
   Order matters for WATERFALL.
3. Tests: mock `aiohttp.ClientSession.get` with `AsyncMock`. See `tests/test_obs_source.py`
   for the pattern.

For non-changelog data (specs, news, tests), create a parallel ABC -- see `SpecSource` in
`src/sources/spec_sources.py` for the template.

---

## 6. Adding a new migration

1. Create `migrations/NNN_description.sql` with a number higher than any existing one.
2. Make every statement idempotent (`CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`,
   `DROP CONSTRAINT IF EXISTS`, `DO $$ BEGIN IF NOT EXISTS ... END $$` for constraints).
3. The migration runs on next `db.connect()`. The `schema_migrations` table records it; no
   manual bookkeeping needed.
4. To force a re-run during development: `DELETE FROM schema_migrations WHERE version='NNN_*.sql'`
   then restart.

---

## 7. Testing patterns

### Pure-function tests (fastest, no mocks)

For `version_utils`, `obs_parser`, `spec_parser`, `sanitize`: input/output assertions over
inline fixture strings.

### Mocked subprocess tests

`tests/test_rpm_manager.py` patches `asyncio.create_subprocess_exec` with `AsyncMock` to
simulate `rpm -q` output. The pattern:

```python
with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
    result = await rpm_mgr.get_dependencies("vim")
```

### Mocked HTTP tests

`tests/test_obs_source.py` etc. patch `aiohttp.ClientSession.get` via `AsyncMock`. Returns
a context-manager-style mock with `status` + `text()`.

### Database integration tests (testcontainers)

`tests/test_db.py` boots a real `pgvector/pgvector:pg17` container via testcontainers. Marked
with `@pytest.mark.e2e` so they're opt-in:

```bash
PYTHONPATH=. uv run pytest -m e2e tests/test_db.py
```

Requires Podman socket and `TESTCONTAINERS_RYUK_DISABLED=true` (Ryuk can't mount the Podman
socket). `scripts/test.sh` sets both automatically.

### End-to-end tests (gemini-cli + real Postgres)

`tests/test_e2e_gemini.py` spawns gemini-cli pointed at a testcontainers Postgres with real
openSUSE packages ingested. The slowest tier; run on-demand.

### Parametrize over the truth table

Most tests use `@pytest.mark.parametrize` with `ids=` for readable failure output. See
`tests/test_version_utils.py` for the canonical example -- 30+ assertions collapsed into one
decorator block.

---

## 8. Tooling

```bash
./scripts/lint.sh ci      # ruff check + ruff format --check + mypy (CI mode)
./scripts/lint.sh fix     # ruff check --fix + ruff format
./scripts/lint.sh format  # ruff format only
./scripts/lint.sh check   # ruff check only

PYTHONPATH=. uv run pytest tests/           # all unit tests (~5s)
PYTHONPATH=. uv run pytest -m e2e tests/    # opt-in integration tests
PYTHONPATH=. uv run mypy src mcp_server.py  # type check
```

`pyproject.toml` configures ruff with rule packs `E/W/F/I/UP/B/SIM/RUF/ASYNC/S/PTH`, line
length 110, target py313.

### Conventions

- **Relative imports** inside `src/` (`from ..config`, `from ._helpers`). Enforced by ruff.
- **No emoji** in code or docs -- plain ASCII only.
- **No code duplication** -- extract at the second occurrence, not the third.
- **No backwards-compat shims** unless explicitly required -- prefer breaking changes that
  delete the old code.
- **Comments explain WHY**, not WHAT. Skip docstrings on trivial functions; one short line max.

---

## 9. Background work

`scripts/worker.py` is the centralised ingestion daemon. It:

- Iterates the `packages.txt` file or `rpm -qa` output
- Re-ingests packages whose `manifest.synced_at` is past the TTL
- Refreshes news / openQA / TestCatalog data (with flags)
- Runs `evict_stale()` per kind (with `--sweep`)
- Exits cleanly; meant to be cron-driven (systemd `Type=oneshot` + `.timer`)

Each end-user runs the MCP server; the worker runs once on a maintenance host. Both share the
same Postgres.

---

## 10. Where to look first

| Task | Start here |
|---|---|
| "Why is this tool returning empty results?" | `src/tools/<module>.py:<tool>` -> `db.<query>` |
| "How does cross-distro work?" | `src/sources/registry.py:fetch` + `src/ingest.py:ingest_all_distros` |
| "Why is a fetch failing?" | Set `LOG_FORMAT=json` and grep `tool_source_error` / `tool_source_not_found` |
| "How is search ranking computed?" | `src/db.py:_fetch_text_search` (FTS), `src/db.py:semantic_search` (HNSW) |
| "What's the deduplication contract?" | `src/db.py:content_uuid` |
| "How do I trace a slow tool?" | `LOG_FORMAT=json | jq 'select(.tool=="X") | {tool,duration_ms,category,stale}'` |
