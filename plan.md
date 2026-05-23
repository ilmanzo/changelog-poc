# Plan: Phase 4.5 — Unit Tests + Coverage ✓ COMPLETE

**Result (2026-05-23):** 181 tests passing, 1 xfailed, 73% coverage (target was 70%).

## Context

All previous plan items (e2e tests, tool gap fixes, BSC bug tools, architecture doc, code review)
are complete. The project had zero unit tests — only 35 e2e tests via gemini-cli. This plan
added a unit test suite targeting the pure-logic and lightly-mocked layers first, then integration
tests for the DB layer.

The `addopts = "-m 'not e2e'"` in `pyproject.toml` ensures unit tests run by default and
e2e tests are opt-in — no config change needed.

---

## Test files to create (in priority order)

### 1. `tests/test_version_utils.py` — pure functions, zero deps

`src/version_utils.py` exports `clean_version`, `content_matches`, `parse_when`, `CVE_RE`, `BSC_RE`.
All are pure; no mocking needed.

Key cases:
- `clean_version`: epoch prefix (`1:9.2`→`9.2`), suffix stripping (`9.2p1`→`9.2`), empty string, None-equivalent
- `parse_when`: ISO-8601 with and without tz, natural language ("1 year ago"), invalid string → None, empty string → None
- `CVE_RE` / `BSC_RE`: valid and invalid IDs

### 2. `tests/test_obs_parser.py` — pure text parsing, zero deps

`src/obs_parser.py` exposes `parse_obs_changes(raw_text) -> list[ChangelogEntry]`.
Use inline fixture strings (real `.changes` format snippets).

Key cases:
- well-formed multi-entry block → correct `(version, author, date, content)` tuples
- missing date line → entry still parsed with `date=None`
- empty string → empty list
- single entry without version tag

### 3. `tests/test_spec_parser.py` — pure text parsing, zero deps

`src/spec_parser.py`: `extract_sections(content) -> dict[str, str]`, `chunk_sections(sections) -> list[SpecSection]`.

Key cases:
- standard spec with `%build`, `%install`, `%check` → correct section names + bodies
- spec with no recognized sections → fallback split produces at least one chunk
- `chunk_sections` with content > 1000 chars → multiple chunks with 100-char overlap

### 4. `tests/test_modernize.py` — pure regex, zero deps

`src/modernize.py`: `check_modernization(content) -> list[Suggestion]`.

Key cases:
- spec with `%{make_jobs}` → suggestion with replacement
- spec with `%makeinstall` → suggestion
- clean modern spec → empty list
- parametrize over all 10 patterns in `MODERN_MACROS`

### 5. `tests/test_rpm_manager.py` — mock `asyncio.create_subprocess_exec`

`src/rpm_manager.py`: `RPMManager.get_dependencies()`, `get_reverse_dependencies()`, `get_metadata()`.
`parse_changelog()` is a `@staticmethod` — test it directly without any mock.

Key cases:
- `parse_changelog`: real rpm changelog text → list of `ChangelogEntry` with correct dates/versions
- `parse_changelog`: empty string, malformed entry
- `get_dependencies`: mock subprocess stdout → parsed dep list
- `get_dependencies`: subprocess exit code 1 → `RuntimeError`
- Use `unittest.mock.AsyncMock` to patch `asyncio.create_subprocess_exec`

### 6. `tests/test_embedder.py` — mock fastembed + pure `chunk_text`

`src/embedder.py`: `chunk_text(text, size, overlap)` is pure. `embed_one` / `embed_batch` call `TextEmbedding`.

Key cases:
- `chunk_text`: text shorter than chunk size → single chunk
- `chunk_text`: long text → multiple chunks, each ≤ size, with expected overlap
- `embed_one`: mock `_model.embed()` → returns flat list
- `embed_batch`: mock → returns list of lists; empty input → empty list
- Patch `fastembed.TextEmbedding` via `unittest.mock.patch`

### 7. `tests/test_sources_registry.py` — mock `Source` objects

`src/sources/registry.py`: `SourceRegistry` waterfall and parallel strategies.

Key cases:
- waterfall: first source returns entries → second not called
- waterfall: first raises `SourceNotFound` → second tried
- waterfall: all sources fail → `FetchResult` with empty entries
- parallel: local source (fast mock) wins → result used
- registry `close()` called on teardown → all sources closed
- Use `AsyncMock` for source `.fetch()` methods

### 8. `tests/test_ingest.py` — mock registry + db + embedder

`src/ingest.py`: `IngestService.ingest(package)` → `IngestResult`.
`validate_package_name(name)` is a pure function — test directly.

Key cases:
- `validate_package_name`: valid names, names with `+`, `.`, `-`; invalid: `../etc`, `; rm -rf`, too long
- `ingest`: mock registry returns 3 entries → mock embedder → mock db → `IngestStatus.INDEXED`
- `ingest`: mock registry returns 0 entries → `IngestStatus.EMPTY`
- `ingest`: registry raises `SourceError` → `IngestStatus.FAILED`

### 9. `tests/test_db.py` — testcontainers Postgres (marked `e2e`)

`src/db.py`: full `Database` class. Use `PostgresContainer("pgvector/pgvector:pg17")` (same as e2e tests).
Mark with `@pytest.mark.e2e` so they're opt-in.

Key cases:
- `connect()` / `close()` → pool created, migrations applied
- `upsert_package` + `get_package_id` round-trip
- `upsert_changelog_entries` + `fetch_entries` — content-addressed dedup (same entry twice → 1 row)
- `fts_search` — entry with "security" keyword → returned; unrelated entry → not returned
- `fts_search` with `since` filter — entries before cutoff excluded
- `semantic_search` — requires embedding vector (use a zero vector or small real embedding)
- `list_package_cves` — entry with "CVE-2024-1234" → returned; no-CVE entry → not returned
- `list_package_bugs` — entry with "bsc#1234567" → returned
- `find_bug` — specific bug ref found; absent ref → empty list
- `is_fresh` / `touch_manifest` — freshness check after touch
- `evict_stale` — old manifest entry deleted

### 10. `tests/test_llm.py` — mock httpx via `aioresponses`

`src/llm.py`: `ask_llm(question, context) -> str`.

Key cases:
- success: mock `/v1/chat/completions` → 200 with choices → returns content string
- HTTP 500 → raises or returns error string
- connection error → raises or returns error string
- Use `aioresponses` (already in dev deps)

---

## Implementation notes

- All non-e2e tests use `pytest-asyncio` with `asyncio_mode = "auto"` (already configured)
- `aioresponses` is already in dev deps for HTTP mocking
- `testcontainers[postgres]` already in dev deps for `test_db.py`
- No new deps needed
- `conftest.py` — add shared fixtures: `sample_changes_text`, `sample_spec_text`, `sample_rpm_changelog_text`

---

## Critical files

| File to create | Tests for | Mock strategy |
|---|---|---|
| `tests/conftest.py` | Shared text fixtures | None |
| `tests/test_version_utils.py` | `src/version_utils.py` | None |
| `tests/test_obs_parser.py` | `src/obs_parser.py` | None |
| `tests/test_spec_parser.py` | `src/spec_parser.py` | None |
| `tests/test_modernize.py` | `src/modernize.py` | None |
| `tests/test_rpm_manager.py` | `src/rpm_manager.py` | `AsyncMock` on subprocess |
| `tests/test_embedder.py` | `src/embedder.py` | `patch("fastembed.TextEmbedding")` |
| `tests/test_sources_registry.py` | `src/sources/registry.py` | `AsyncMock` sources |
| `tests/test_ingest.py` | `src/ingest.py` | `AsyncMock` registry + db + embedder |
| `tests/test_db.py` | `src/db.py` | testcontainers Postgres (`@pytest.mark.e2e`) |
| `tests/test_llm.py` | `src/llm.py` | `aioresponses` |

---

## Verification

```bash
cd /home/andrea/projects/changelog_mcp/rpm-mcp

# Run unit tests (fast — no container, no network)
PYTHONPATH=. uv run pytest tests/ -v --ignore=tests/test_e2e_gemini.py

# Run with coverage report
PYTHONPATH=. uv run pytest tests/ --ignore=tests/test_e2e_gemini.py \
  --cov=src --cov-report=term-missing

# Run db integration tests (requires podman socket)
export DOCKER_HOST=unix:///run/user/$UID/podman/podman.sock
PYTHONPATH=. uv run pytest tests/test_db.py -v -m e2e

# Full suite including e2e
PYTHONPATH=. uv run pytest tests/ -v -m "e2e" \
  --ignore=tests/test_e2e_gemini.py  # db only, skip gemini
```

Expected: unit tests run in < 30 seconds; `test_db.py` in ~60 seconds (container startup).
Target coverage: > 70% on `src/` after all unit tests.

---

## Phase 5 — Resilience + Production Hardening

Deployment: local stdio per user, shared Postgres. No auth needed.

### Resilience (priority 1)

- **LLM retry**: wire `tenacity` in `src/llm.py` — 3 retries, exponential backoff, then raise
- **Source fetch failure**: catch network/source errors in `IngestService`; if stale data exists return it with a `"⚠ fetch failed, data from <timestamp>"` caveat in the tool response
- **Postgres startup retry**: add retry loop in `Database.connect()` for when the MCP process starts before Postgres is ready
- **Graceful SIGTERM**: verify FastMCP lifespan closes the asyncpg pool cleanly on shutdown

### Phase 4 worker (not started)

- **`scripts/worker.py`**: cron-driven ingestion daemon — iterate `manifest`, re-ingest packages past TTL, delegate to `IngestService.ingest`
- **TTL eviction**: `evict_stale()` exists in `db.py`; wire it into the worker run loop
- **HNSW `ef_search` tuning**: run `scripts/bench.py`, pick optimal value, set it via `SET LOCAL hnsw.ef_search` in `Database.connect()` or per-query
- **systemd user unit**: `.service` + `.timer` for the worker daemon (not the MCP server — that stays per-user stdio)

### Ops / day-2

- **`.env.example`**: document all env vars (`DATABASE_URL`, `LLM_BASE_URL`, etc.) with defaults and required/optional annotations
- **Migration docs**: note that migrations are append-only idempotent; add version comments to `migrations/*.sql`
- **Coverage**: run `tests/test_db.py` with podman socket to reach 70% target

### Not in scope

Auth, TLS, Prometheus metrics, OpenTelemetry, Containerfile — not needed for local stdio deployment.
