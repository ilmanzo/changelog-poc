# Session memory — rpm-mcp

## User profile
- Senior software engineer. Caveman communication style: no articles, no filler, code over prose.
- Operates `/home/andrea/projects/changelog_mcp/` monorepo: two sibling Python MCP projects (`changelog-poc`, `rpm-spec-assistant`) merged into a third (`rpm-mcp`).
- Uses **podman**, not docker. No compose plugin installed — `podman run` directly.
- Uses **uv** for Python; Python 3.13+.
- Uses **fish** shell; rtk proxy for token-optimised CLI ops.
- Local LLM proxy expected at `localhost:11438` (Phi-4-mini).

## Feedback / preferences (validated this session)
- "Goal set: continue until you are done" → execute multi-phase plans autonomously without per-phase approval prompts. **Why**: user values throughput over per-step confirmation. **How to apply**: when given a plan, drive it end-to-end; only stop on hard blockers.
- Storage decisions justified by scale (13k packages × 100 concurrent users). User picked **PostgreSQL + pgvector** after I presented Qdrant vs sqlite-vec vs pgvector tradeoffs. **Why**: single backing store, HNSW, MVCC for concurrent reads. **How to apply**: when proposing infra, lead with concurrency + index-type tradeoffs, not just feature lists.
- Architectural decisions like "changelog-poc as base, absorb rpm-spec-assistant" came out of explicit AskUserQuestion. **Why**: user preferred picking from a small enumerated set vs free-form. **How to apply**: for architecture forks, present 2-4 named options with one-line tradeoffs.

## Project context — rpm-mcp build state (as of 2026-05-22)
- **Phases 0–4 complete.** 16 MCP tools live, verified against running Postgres container.
- Phase 5 (Go port) explicitly out of scope per plan.
- Task 22 (unit tests) deferred — never written.
- Originals (`changelog-poc/`, `rpm-spec-assistant/`) untouched; archival is a follow-up after dogfooding.

### Key implementation gotchas hit this session
- **pgvector codec must be registered AFTER `CREATE EXTENSION`.** Pool's `init=` callback runs per new connection; if it calls `register_vector` before the extension exists, you get `unknown type: public.vector`. Fix: bootstrap connect → `CREATE EXTENSION vector` → then create pool. See `src/db.py:42-52`.
- **FastMCP `@mcp.tool()` returns the underlying coroutine function**, not a `.fn`-wrapped object. Call tools directly (`await m.semantic_search(...)`) in smoke tests.
- **`infra/infra.sh psql` is interactive only** — no `-c` passthrough. For scripted queries use `podman exec rpm-mcp-postgres psql -U rpm_mcp -d rpm_mcp -tAc "..."`.
- **`asyncio.gather(return_exceptions=True)`** returns `BaseException`, not `Exception` — mypy won't narrow `isinstance(res, Exception)`. Use `isinstance(res, BaseException)`.

## Verified hot-path latencies
- Semantic search (1 package warm, ~80 entries): p50 35ms, p95 82ms, p99 82ms, max 219ms (first call cold).
- Full ingest of `vim` (cold, RPM source, 80 entries + embeddings): 15.7s.

## Decisions locked
- Single Postgres container (no compose). Data dir: `infra/pg_data`. DB/user/password all `rpm_mcp`.
- One `Database` class owns all SQL. No other module talks to Postgres.
- Content-addressed dedup: `uuid5(NAMESPACE_OID, package||content)` per changelog entry.
- Spec fetchers (OBS, Pagure) are plain async functions, not full `Source` ABCs — only two sources, ABC overhead not justified.
- News + openQA fetchers same — plain functions returning model records.
