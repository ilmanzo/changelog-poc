---
layout: post
title: "Building an MCP server for OBS in 3 days"
description: "An AI-assisted hackathon sprint: from zero to a working changelog and spec-analysis server for openSUSE"
categories: programming
tags: [linux, opensource, python, ai, mcp, opensuse, obs, hackathon, postgresql]
author: Andrea Manzini
date: 2026-05-26
---

## The problem nobody talks about

A QA engineer files a bug. The test has been green for months. Nothing changed in the app.
But `openssl` was updated in OBS yesterday.

This is the **environment gap**: test failures that have nothing to do with application code, caused by
system-level package changes in the [Open Build Service](https://build.opensuse.org/).
Today diagnosing this means manually navigating XML API responses, clicking through the OBS web UI "Changes"
tab, or running `rpm -q --changelog` and hoping you remember what the previous version looked like.

Worse: if `libfoo` was bumped, which packages re-link against the new ABI? There's no quick answer.

The hackathon prompt was simple: build a tool that lets an LLM answer these questions from the terminal,
in under 60 seconds, without leaving your editor.

## What we built

**rpm-mcp** — a [Model Context Protocol](https://modelcontextprotocol.io/) server that gives any MCP-compatible
AI assistant unified access to:

- openSUSE OBS changelogs and `.spec` file diffs
- RPM changelog history from the local database
- Gitea / GitHub source history
- Bodhi update feeds and openQA test results
- Semantic and full-text search across all of the above

Since MCP is a standard protocol, it works out of the box with Claude Code, OpenCode, Cursor, Zed,
Continue.dev, Windsurf — any client that speaks the protocol.
For Claude Code it's three lines in `.claude/settings.json`:

{{< highlight json >}}
{
  "mcpServers": {
    "rpm-mcp": {
      "command": "uv",
      "args": ["run", "mcp_server.py"],
      "cwd": "/path/to/rpm-mcp"
    }
  }
}
{{</ highlight >}}

One important distinction: this is a **server-side** MCP tool, not a zero-setup plugin.
It requires a running PostgreSQL instance (and optionally a local LLM proxy).
That infrastructure cost is what enables the capability — semantic search, FTS, version history,
dependency graphs — none of which fit in a stateless plugin.
The upside: deploy one instance, every engineer's editor and every CI agent in the org connects to
the same data.

---

## Day 1 — From zero to working MCP server

The first design decision set the tone for everything else: **one Postgres instead of two separate stores**.

The previous proof-of-concept used Qdrant for vectors and SQLite for relational data. Replacing both with
a single PostgreSQL instance running [pgvector](https://github.com/pgvector/pgvector) and `pg_trgm`
simplified ops dramatically. One container, one connection pool, one migration file.

The data model is content-addressed: every changelog entry gets a UUID primary key derived from
`uuid5(NAMESPACE, package_name + content)`. The same `.changes` block fetched from OBS and from Gitea
converges to the same row without any field comparison. No dedup logic, no conflicts.

{{< highlight python >}}
import uuid

NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

def entry_id(package: str, content: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, package + content)
{{</ highlight >}}

Sources are pluggable via a `Source` ABC with four optional capabilities:
`fetch_changelog`, `fetch_spec`, `fetch_news`, `fetch_tests`.
Adding a new registry is ~50 lines: implement what you have, skip the rest.

The OBS integration hit the first real friction: the API returns XML where everyone else uses JSON.
The `GET /source/{project}/{package}/_history` endpoint gives revision IDs; `?view=diff` gives raw diffs.
System package diffs can be massive — a single `glibc` diff across two releases can run hundreds of kilobytes.
The server pre-processes diffs into 1000-character overlapping chunks before sending anything to an LLM.

By end of day 1 all 14 changelog tools were wired and returning real data.
The first end-to-end win: asking `find_cve("CVE-2023-4738", "vim")` and getting back the exact `.changes`
entry where the fix landed.

**What the AI co-pilot accelerated**: boilerplate — asyncpg pool setup, structlog wiring, FastMCP lifespan
management. This kind of repetitive scaffolding that normally takes half a day was done in minutes.

**Where it went wrong**: the first SQL migration wasn't idempotent. `CREATE TABLE` without `IF NOT EXISTS`,
which would break every restart after the first. Caught it in review and fixed it, but it's a reminder
that generated code still needs a human pass.

---

## Day 2 — Spec assistant, news, and openQA

Day 2 was about making the server genuinely useful for the spec-analysis use case.

`python-specfile` gives a proper AST for `.spec` parsing — section names, macros, and structure — instead
of fragile regex over raw text. Where it breaks is non-standard specs (hand-written macros, includes,
conditional blocks). For those, the fallback is a sliding-window chunker that splits on section headers
and sends chunks to the LLM with overlap to preserve context.

The modernization checker deliberately avoids the LLM: 10 regex patterns for deprecated macros
(`%{make_jobs}`, `%makeinstall`, `%configure`, and friends), deterministic output, no network call.
Fast enough to run on every spec fetch.

{{< highlight python >}}
MODERN_MACROS = [
    (r"%makeinstall\b", "%make_install"),
    (r"%{make_jobs}", "%{?_smp_mflags}"),
    # ... 8 more
]
{{</ highlight >}}

The LLM tools (`explain_build`, `analyze_package`, `modernize_package`) use a local proxy at
`LLM_BASE_URL` — an OpenAI-compatible `/v1/chat/completions` endpoint, no cloud dependency, runs
entirely on-premises. Important for anything touching private OBS projects or internal package trees.

Bodhi and openQA sources use a **parallel fetch strategy** for local sources (fast, no network)
and **waterfall** for remote: try the fastest authoritative source first, fall back on failure.

What got cut: Podman macro expansion (`get_expanded_spec` from the original prototype).
The cost/complexity ratio was wrong for a three-day sprint. Deferred.

---

## Day 3 — 0 to 181 tests

The repo started day 3 with 35 end-to-end tests driven by gemini-cli — slow, fragile, and useless
for CI without the full stack running. Zero unit tests.

The strategy: pure functions first. `version_utils`, `obs_parser`, `spec_parser`, `modernize` —
no mocks, no fixtures, just input/output assertions. Instant confidence, runs in milliseconds.
Then work outward: mock `asyncio.create_subprocess_exec` for subprocess-backed managers,
mock `httpx.AsyncClient` for HTTP sources, testcontainers for the database layer.

The refactoring ran in parallel. A `_tool_wrapper` decorator collapsed repeated boilerplate across
all 18 MCP tools — timing, structured logging, error formatting — into a single 80-line decorator:

{{< highlight python >}}
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
{{</ highlight >}}

`@pytest.mark.parametrize` collapsed 30+ near-identical test functions.
Before:

{{< highlight python >}}
async def test_fetch_bodhi_error_404(): ...
async def test_fetch_bodhi_error_500(): ...
async def test_fetch_bodhi_error_timeout(): ...
{{</ highlight >}}

After:

{{< highlight python >}}
@pytest.mark.parametrize("case", ["404", "500", "timeout"], ids=["404", "500", "timeout"])
async def test_fetch_bodhi_error(case: str) -> None: ...
{{</ highlight >}}

End state: **257 unit tests, full suite runs in under 3 seconds**.
The DB integration tests run separately via testcontainers + Podman — 19 tests, all green, ~7 seconds.

---

## After the sprint — production hardening

The three days got the surface area right; the week that followed made it boring enough to run
unattended. The interesting decisions:

**Stale-data is a feature, not a failure.** When OBS or Pagure is unreachable, the tool no longer
returns an error — it serves the previously-cached data and prepends a banner:
`WARNING: source fetch failed; serving cached data from 2026-05-23T14:02Z`. The MCP client sees
the warning, the user knows the timestamp, and the agent can decide whether to trust it. Hard
failures are reserved for things that genuinely cannot be answered.

**Tiered cache by source.** News feeds, package changelogs, and spec files all change on
different timescales, so they shouldn't share a TTL. The `manifest` table grew a `kind`
discriminator — news every 24h, changelogs every 24h, specs every 7d — driven by a per-kind
sweep in the worker. Saves bandwidth and respects upstream.

**Worker as a systemd timer, not a daemon.** The ingestion worker runs once per hour as a
`Type=oneshot` user unit (`packaging/systemd/rpm-mcp-worker.{service,timer}`). No long-lived
process, no PID file, no supervisor — `systemctl --user status` and `journalctl --user -u
rpm-mcp-worker` are the entire operations interface. `RandomizedDelaySec=15min` smooths the
load on shared infra.

**Fast-fail for unindexed packages.** First query on a never-seen package used to block for
5–35 seconds while ingest ran inline. Now the tool returns *"package not yet indexed; ingestion
queued"* immediately and dispatches the ingest as a background task with in-process
coalescing (`_pending: dict[str, asyncio.Task]`). Second query within a second or two reuses
the same task. Cross-process races are absorbed by `ON CONFLICT DO NOTHING` upserts —
no advisory locks needed.

**Security cleanup pass.** Dropped `http://` from allowed git URL schemes (with a structlog
warning if `git://` is used), wired `validate_package_name` into `GitManager._safe_repo_path`
so embedded slashes are rejected before resolving paths, replaced the regex-based RSS parser
with `defusedxml.ElementTree` to neutralise XXE, and replaced load-bearing `assert`s with
explicit `raise` so `python -O` doesn't silently strip safety checks.

What still isn't done: tenacity retry on LLM calls, the full prompt-injection hardening
batch (nonce-fenced context blocks, scrubbed control characters, a written threat model),
and a versioned migrations table. The roadmap in `plan.md` is the source of truth.

---

## What's next

The longer-term vision is more interesting than the bug list. Once the server is stable it
can be exposed as a shared service — imagine a SLES installation where any engineer (or any
CI job) can ask:

> *"What changed in SLES 16 over the last 3 months that could affect my package?"*

That's a single query to this server. The packaging process becomes observable and explainable
to anyone with an MCP-capable tool, without needing to know the OBS API, XML parsing, or
where changelogs live. Reliability and transparency as a product feature, not an afterthought.

The code is at [github.com/ilmanzo/rpm-mcp](https://github.com/ilmanzo/rpm-mcp) — contributions welcome.
