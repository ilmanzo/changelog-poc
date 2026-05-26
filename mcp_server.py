"""FastMCP entrypoint for rpm-mcp.

All MCP tool functions live here. Each tool is wrapped by ``_tool_wrapper``,
which standardises timing + structured logging + exception → user-facing error
string. Inside a tool body, call ``_tlog(field=value)`` to attach extra fields
to the wrapper's terminal ``tool_done`` / ``tool_error`` log record.
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import functools
import inspect
import logging
import os
import re
import sys
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, Mapping, TypeAlias

import structlog
from mcp.server.fastmcp import FastMCP
from packaging import version as pkg_version

from src import embedder
from src.config import settings
from src.db import Database
from src.git_manager import GitManager
from src.ingest import IngestService, IngestStatus, validate_package_name
from src.logging_config import configure_logging
from src.models import ChangelogEntry
from src.news_fetcher import fetch_all_news
from src.openqa_fetcher import scan_tests  # noqa: F401  (re-exported for worker use)
from src.rpm_manager import RPMManager
from src.sources import (
    FetchStrategy,
    GiteaSource,
    ObsSource,
    RpmSource,
    SourceRegistry,
)
from src.spec_fetcher import fetch_obs_spec, fetch_pagure_spec
from src.spec_parser import chunk_sections, extract_sections
from src.version_utils import BSC_RE, CVE_RE, clean_version, content_matches, parse_when

configure_logging(
    level=logging.INFO,
    json_logs=settings.log_format.lower() == "json",
)
_logger = structlog.get_logger("rpm-mcp.server")

# Why: _CVE_CONTENT_RE / _BSC_CONTENT_RE scan freely inside changelog bodies,
# unlike CVE_RE / BSC_RE which anchor a full ID string for input validation.
_CVE_CONTENT_RE = re.compile(r"CVE-\d{4}-\d+", re.IGNORECASE)
_BSC_CONTENT_RE = re.compile(r"\b(?:bsc|boo|bnc)#\d+\b", re.IGNORECASE)


# Singletons — bound at module level so tests can monkey-patch independently.
db = Database()
rpm_mgr = RPMManager()
git_mgr = GitManager()
source_registry = SourceRegistry(
    sources=[RpmSource(rpm_mgr), ObsSource(), GiteaSource()],
    strategy=FetchStrategy(settings.fetch_strategy),
)
ingest_service = IngestService(source_registry, db)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    await db.connect()
    _logger.info("server_started")
    try:
        yield
    finally:
        await source_registry.close()
        await db.close()
        _logger.info("server_stopped")


mcp = FastMCP("rpm-mcp", lifespan=lifespan)


# ---------------------------------------------------------------------------
# User-facing message templates
# ---------------------------------------------------------------------------
MSG_PKG_NOT_FOUND = "Package '{}' not found in any source (local RPM, OBS, src.opensuse.org)."
MSG_PKG_NOT_FOUND_SHORT = "Package '{}' not found in any source."
MSG_PKG_QUEUED = (
    "Package '{}' is not yet indexed; ingestion has been queued. "
    "Retry this call in a few seconds."
)
MSG_INVALID_CVE = "Invalid CVE ID '{}'. Expected CVE-YYYY-NNNN(NNN)."
MSG_INVALID_BUG = "Invalid bug ID '{}'. Expected bsc#NNNNNN, boo#NNNNNN, or bnc#NNNNNN."
MSG_SINCE_UNPARSEABLE = "Could not parse 'since' value: {!r}."
MSG_UNTIL_UNPARSEABLE = "Could not parse 'until' value: {!r}."
MSG_UNKNOWN_SPEC_SOURCE = "Unknown source {!r}. Use 'opensuse' or 'fedora'."

ReleaseGroup: TypeAlias = tuple[str, datetime, list[ChangelogEntry]]
SqlRow: TypeAlias = Mapping[str, Any]


# ---------------------------------------------------------------------------
# Tool wrapper: timing + logging + exception → user-facing string
# ---------------------------------------------------------------------------
_log_extras: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "_log_extras", default={}
)

# Why: per-task flag set when _ensure_or_queue fell back to stale cached data.
# _tool_wrapper reads it post-call and prepends a one-line ⚠ banner.
_stale_state: contextvars.ContextVar[datetime | None] = contextvars.ContextVar(
    "_stale_state", default=None
)


def _tlog(**fields: Any) -> None:
    """Add structured fields to the wrapping tool's terminal log record."""
    _log_extras.set({**_log_extras.get(), **fields})


def _mark_stale(synced_at: datetime | None) -> None:
    """Flag the current tool call as serving stale data."""
    _stale_state.set(synced_at)


def _stale_banner(synced_at: datetime | None) -> str:
    ts = synced_at.isoformat() if synced_at else "unknown timestamp"
    return f"⚠ Source fetch failed; serving cached data from {ts}\n\n"


def _tool_wrapper(tool_name: str) -> Callable[[Callable[..., Awaitable[str]]], Callable[..., Awaitable[str]]]:
    """Wrap a tool body with timing, structured logging, and uniform error formatting."""
    def decorator(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> str:
            token = _log_extras.set({})
            stale_token = _stale_state.set(None)
            t0 = time.perf_counter()
            bound: Mapping[str, Any]
            try:
                bound = sig.bind(*args, **kwargs).arguments
            except TypeError:
                bound = kwargs
            log = _logger.bind(
                tool=tool_name,
                **{k: v for k, v in bound.items() if isinstance(v, (str, int, bool))},
            )
            try:
                result = await fn(*args, **kwargs)
                stale_at = _stale_state.get()
                log.info(
                    "tool_done",
                    elapsed_s=round(time.perf_counter() - t0, 3),
                    stale=stale_at is not None,
                    **_log_extras.get(),
                )
                if stale_at is not None:
                    return _stale_banner(stale_at) + result
                return result
            except Exception as e:
                log.exception(
                    "tool_error",
                    elapsed_s=round(time.perf_counter() - t0, 3),
                    **_log_extras.get(),
                )
                return f"Error in {tool_name} for {bound.get('package', '?')}: {e}"
            finally:
                _log_extras.reset(token)
                _stale_state.reset(stale_token)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _format_date(dt: datetime | None) -> str:
    return dt.date().isoformat() if dt else "?"


def _validate_cve_id(cve_id: str) -> str:
    if not CVE_RE.match(cve_id):
        raise ValueError(MSG_INVALID_CVE.format(cve_id))
    return cve_id.upper()


def _validate_bug_id(bug_id: str) -> str:
    if not BSC_RE.match(bug_id):
        raise ValueError(MSG_INVALID_BUG.format(bug_id))
    return bug_id.lower()


class _Readiness(Enum):
    READY = "ready"     # caller may read cached rows
    QUEUED = "queued"   # never-indexed package — background ingest dispatched


async def _ensure_or_queue(package: str, refresh: bool = False) -> _Readiness:
    """Fast-fail readiness check (DD10).

    Never blocks on a fresh ingest for a brand-new package: schedules a
    background task and returns ``QUEUED`` immediately so the caller can
    return a "queued; retry" message. For packages already in the cache,
    falls back to the synchronous refresh path (preserving the STALE banner).
    """
    pkg_id = await db.get_package_id(package)
    if pkg_id is None:
        ingest_service.schedule(package)
        return _Readiness.QUEUED
    if not refresh and await db.is_fresh(pkg_id, settings.cache_ttl_seconds):
        return _Readiness.READY
    res = await ingest_service.ingest(package)
    if res.status is IngestStatus.STALE:
        _mark_stale(res.synced_at)
    return _Readiness.READY


def _records_to_entries(rows: list[SqlRow]) -> list[ChangelogEntry]:
    return [
        ChangelogEntry(
            version=r["version"],
            author=r["author"] or "",
            date=r["entry_date"] or datetime.min,
            content=r["content"],
        )
        for r in rows
    ]


async def _load_entries(package: str) -> list[ChangelogEntry]:
    pkg_id = await db.get_package_id(package)
    if pkg_id is None:
        return []
    rows = await db.fetch_entries(pkg_id, limit=settings.cache_max_entries)
    return _records_to_entries(rows)


async def _ensure_and_load_entries(
    package: str, refresh: bool = False
) -> list[ChangelogEntry] | Literal[_Readiness.QUEUED] | None:
    """Fast-fail entry loader (DD10).

    Returns:
        - non-empty ``list[ChangelogEntry]`` on cache hit
        - ``_Readiness.QUEUED`` when the package has never been indexed
          (ingestion was scheduled in the background)
        - ``None`` when the package row exists but holds zero entries
    """
    state = await _ensure_or_queue(package, refresh)
    if state is _Readiness.QUEUED:
        return state
    entries = await _load_entries(package)
    return entries or None


def _format_match_rows(rows: list[SqlRow], header: str) -> str:
    """Format find_cve / find_bug rows: package version (date) + content body."""
    lines = [header]
    for r in rows:
        lines.append(
            f"\n--- {r['package']} {r['version']} "
            f"({_format_date(r['entry_date'])}) ---\n{r['content']}"
        )
    return "\n".join(lines)


def _format_listing_rows(
    rows: list[SqlRow], header: str, pattern: re.Pattern[str]
) -> str:
    """Format list_cves / list_bugs rows: version (date) — extracted IDs + 400-char preview."""
    lines = [header]
    for r in rows:
        ids = ", ".join(sorted(set(pattern.findall(r["content"]))))
        lines.append(
            f"\n--- {r['version']} ({_format_date(r['entry_date'])}) — {ids} ---\n"
            f"{r['content'].strip()[:400]}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# changelog tools
# ---------------------------------------------------------------------------
@mcp.tool()
@_tool_wrapper("analyze_package_diff")
async def analyze_package_diff(
    package: str,
    version_start: str,
    version_end: str,
    deep: bool = False,
    refresh: bool = False,
) -> str:
    """Retrieve changelog entries between two versions of *package*.

    Filter priority: semver range → fuzzy content match → version-string match.
    ``deep=True`` shallow-clones upstream and adds `git log v_start..v_end`.
    ``refresh=True`` forces re-ingest.
    """
    validate_package_name(package)
    entries = await _ensure_and_load_entries(package, refresh)
    if entries is _Readiness.QUEUED:
        return MSG_PKG_QUEUED.format(package)
    if entries is None:
        return MSG_PKG_NOT_FOUND.format(package)

    relevant, strategy = _filter_entries_by_version(entries, version_start, version_end)
    _tlog(filter_strategy=strategy, result_entries=len(relevant), total=len(entries))

    if not relevant:
        return (
            f"No changelog entries found for '{package}' between versions "
            f"{version_start} and {version_end}. "
            f"Package exists but the version range may be incorrect or too narrow. "
            f"Available entries span {len(entries)} versions."
        )

    git_logs = ""
    if deep:
        git_logs = await _fetch_git_logs(package, version_start, version_end, relevant)

    lines = [
        f"Package: {package} Diff ({version_start} -> {version_end})",
        "\nCHANGELOG ENTRIES:",
    ]
    for e in relevant:
        lines.append(f"--- {_format_date(e.date)} ({e.version}) ---\n{e.content}")
    if git_logs:
        lines.append("\nUPSTREAM GIT COMMITS:")
        lines.append(git_logs)
    return "\n".join(lines)


def _filter_entries_by_version(
    entries: list[ChangelogEntry], version_start: str, version_end: str
) -> tuple[list[ChangelogEntry], str]:
    """Apply semver → fuzzy → version-string fallback. Returns (matches, strategy_name)."""
    try:
        v_start = pkg_version.parse(clean_version(version_start) or "0")
        v_end = pkg_version.parse(clean_version(version_end) or "9999")
        relevant: list[ChangelogEntry] = []
        for e in entries:
            cv = clean_version(e.version)
            if not cv or cv == "unknown":
                if content_matches(e, version_start, version_end):
                    relevant.append(e)
                continue
            try:
                if v_start <= pkg_version.parse(cv) <= v_end:
                    relevant.append(e)
            except (pkg_version.InvalidVersion, ValueError):
                if content_matches(e, version_start, version_end):
                    relevant.append(e)
        if relevant:
            return relevant, "semver"
    except Exception:
        relevant = [e for e in entries if content_matches(e, version_start, version_end)]
        if relevant:
            return relevant, "fuzzy_fallback"

    # Last-ditch: substring match on version_end
    fallback = [
        e for e in entries
        if version_end in str(e.version) or content_matches(e, version_end)
    ]
    return (fallback, "version_string_match" if fallback else "none")


async def _fetch_git_logs(
    package: str,
    version_start: str,
    version_end: str,
    relevant: list[ChangelogEntry],
) -> str:
    upstream = await db.get_upstream_url(package)
    if not upstream:
        return ""
    repo_path = await git_mgr.ensure_repo(upstream, package)
    tag_a, tag_b = await asyncio.gather(
        git_mgr.find_tag(repo_path, version_start),
        git_mgr.find_tag(repo_path, version_end),
    )
    if tag_a and tag_b:
        return await git_mgr.get_logs_between_tags(repo_path, tag_a, tag_b)
    start_date = relevant[-1].date
    end_date = relevant[0].date
    if start_date and end_date:
        return await git_mgr.get_logs_between_timestamps(repo_path, start_date, end_date)
    return ""


async def _fetch_recent_releases(
    package: str, n: int, refresh: bool
) -> list[ReleaseGroup] | Literal[_Readiness.QUEUED] | None:
    n = max(1, min(n, 50))
    entries = await _ensure_and_load_entries(package, refresh)
    if entries is _Readiness.QUEUED:
        return entries
    if entries is None:
        return None

    groups: dict[str, list[ChangelogEntry]] = defaultdict(list)
    for e in entries:
        key = clean_version(e.version) or str(e.version) or "unknown"
        groups[key].append(e)

    ordered = sorted(
        groups.items(),
        key=lambda kv: max(x.date for x in kv[1]),
        reverse=True,
    )[:n]
    return [
        (ver, max(x.date for x in items), sorted(items, key=lambda x: x.date, reverse=True))
        for ver, items in ordered
    ]


@mcp.tool()
@_tool_wrapper("get_recent_releases")
async def get_recent_releases(package: str, n: int = 3, refresh: bool = False) -> str:
    """Last *n* distinct releases of *package*, grouped by version."""
    validate_package_name(package)
    groups = await _fetch_recent_releases(package, n, refresh)
    if groups is _Readiness.QUEUED:
        return MSG_PKG_QUEUED.format(package)
    if groups is None:
        return MSG_PKG_NOT_FOUND_SHORT.format(package)
    _tlog(releases=len(groups))

    lines = [f"Package: {package} — last {len(groups)} release(s)"]
    for ver, newest_dt, items in groups:
        lines.append(f"\n=== {ver} ({_format_date(newest_dt)}) ===")
        for e in items:
            lines.append(f"--- {_format_date(e.date)} ({e.author}) ---\n{e.content}")
    return "\n".join(lines)


@mcp.tool()
@_tool_wrapper("get_changes_in_range")
async def get_changes_in_range(
    package: str, since: str, until: str | None = None, refresh: bool = False
) -> str:
    """Changelog entries within ``[since, until]`` (ISO 8601 or natural language)."""
    validate_package_name(package)
    since_dt = parse_when(since)
    if since_dt is None:
        return MSG_SINCE_UNPARSEABLE.format(since)
    until_dt = parse_when(until) if until else datetime.now(UTC)
    if until_dt is None:
        return MSG_UNTIL_UNPARSEABLE.format(until)
    if since_dt >= until_dt:
        return (
            f"Invalid range: 'since' ({since_dt.isoformat()}) is not before "
            f"'until' ({until_dt.isoformat()})."
        )

    if await _ensure_or_queue(package, refresh) is _Readiness.QUEUED:
        return MSG_PKG_QUEUED.format(package)

    pkg_id = await db.get_package_id(package)
    # Why: _ensure_or_queue returned READY, so the package row exists in `packages`.
    assert pkg_id is not None
    rows = await db.fetch_entries_in_range(pkg_id, since_dt, until_dt)
    entries = _records_to_entries(rows)
    _tlog(entries=len(entries))

    header = (
        f"Package: {package} — changes between "
        f"{_format_date(since_dt)} and {_format_date(until_dt)} ({len(entries)} entries)"
    )
    if not entries:
        return header + "\n(no entries in this window)"
    lines = [header]
    for e in entries:
        lines.append(
            f"\n--- {_format_date(e.date)} ({e.version}, {e.author}) ---\n{e.content}"
        )
    return "\n".join(lines)


@mcp.tool()
@_tool_wrapper("get_dependencies")
async def get_dependencies(package: str) -> str:
    """Direct runtime deps of *package* from the local RPM database."""
    validate_package_name(package)
    try:
        deps = await rpm_mgr.get_dependencies(package)
    except RuntimeError as e:
        return f"Package '{package}' not installed locally: {e}"
    _tlog(count=len(deps))
    if not deps:
        return f"No dependencies resolved for '{package}'."
    return f"Dependencies of {package} ({len(deps)}):\n" + "\n".join(sorted(deps))


@mcp.tool()
@_tool_wrapper("get_reverse_dependencies")
async def get_reverse_dependencies(package: str) -> str:
    """Installed packages that depend on *package* (local RPM database)."""
    validate_package_name(package)
    try:
        rdeps = await rpm_mgr.get_reverse_dependencies(package)
    except RuntimeError as e:
        return f"Package '{package}' not installed locally: {e}"
    _tlog(count=len(rdeps))
    if not rdeps:
        return f"No installed packages depend on '{package}'."
    return f"Packages depending on {package} ({len(rdeps)}):\n" + "\n".join(sorted(rdeps))


@mcp.tool()
@_tool_wrapper("find_cve")
async def find_cve(cve_id: str, package: str | None = None) -> str:
    """Case-insensitive substring search for a CVE ID across cached changelogs."""
    try:
        cve_id = _validate_cve_id(cve_id)
    except ValueError as e:
        return str(e)
    if package:
        validate_package_name(package)
        if await _ensure_or_queue(package) is _Readiness.QUEUED:
            return MSG_PKG_QUEUED.format(package)

    matches = await db.find_cve(cve_id, package_name=package)
    _tlog(matches=len(matches))
    if not matches:
        scope = f"package '{package}'" if package else "any cached package"
        return f"No mentions of {cve_id} found in {scope}."
    return _format_match_rows(matches, f"Found {len(matches)} entries mentioning {cve_id}:")


@mcp.tool()
@_tool_wrapper("get_dependency_changes")
async def get_dependency_changes(
    package: str, n: int = 3, depth: int = 1, refresh: bool = False
) -> str:
    """For each (transitive) dependency of *package*, return its last *n* releases."""
    validate_package_name(package)
    depth = max(1, min(depth, 2))
    n = max(1, min(n, 20))

    deps_list = await _collect_transitive_deps(package, depth)
    if not deps_list:
        return f"No dependencies resolved for '{package}' (is it installed locally?)."

    results = await asyncio.gather(
        *(_fetch_recent_releases(d, n, refresh) for d in deps_list),
        return_exceptions=True,
    )

    lines = [
        f"Dependencies of {package} "
        f"(depth={depth}, {len(deps_list)} packages, last {n} release(s) each):"
    ]
    ok = err = missing = queued = 0
    for dep, res in zip(deps_list, results):
        if isinstance(res, BaseException):
            err += 1
            lines.append(f"\n## {dep}: error — {res}")
            continue
        if res is None:
            missing += 1
            lines.append(f"\n## {dep}: no changelog found in any source")
            continue
        if res is _Readiness.QUEUED:
            queued += 1
            lines.append(f"\n## {dep}: queued — retry in a few seconds")
            continue
        ok += 1
        lines.append(f"\n## {dep} — last {len(res)} release(s)")
        for ver, newest_dt, items in res:
            lines.append(f"  === {ver} ({_format_date(newest_dt)}) ===")
            for e in items:
                lines.append(f"  {e.content}")
    _tlog(ok=ok, missing=missing, errors=err, queued=queued)
    return "\n".join(lines)


async def _collect_transitive_deps(root: str, depth: int) -> list[str]:
    """BFS up to *depth* hops from *root*, capped by settings.f4_max_packages."""
    visited: set[str] = {root}
    deps: set[str] = set()
    frontier: set[str] = {root}
    for _ in range(depth):
        new_frontier: set[str] = set()
        for pkg in frontier:
            try:
                pkg_deps = await rpm_mgr.get_dependencies(pkg)
            except Exception as ex:
                _logger.warning("rpm_deps_failed", package=pkg, error=str(ex))
                continue
            for d in pkg_deps:
                if d not in visited:
                    new_frontier.add(d)
                    visited.add(d)
        deps.update(new_frontier)
        frontier = new_frontier
        if len(deps) >= settings.f4_max_packages:
            break
    return sorted(deps)[: settings.f4_max_packages]


@mcp.tool()
@_tool_wrapper("sync_package")
async def sync_package(package: str) -> str:
    """Force-ingest *package* — fetch + embed + upsert. Thin wrapper over IngestService."""
    result = await ingest_service.ingest(package)
    _tlog(status=result.status.value, entries=result.entries)
    if result.status is IngestStatus.INDEXED:
        return f"Successfully indexed {result.entries} entries for {package} (source: {result.source})."
    if result.status is IngestStatus.EMPTY:
        return f"No changelog found for {package} in any source."
    return f"Sync failed for {package}: {result.error}"


@mcp.tool()
@_tool_wrapper("semantic_search")
async def semantic_search(query: str, limit: int = 5) -> str:
    """Natural-language search across indexed changelogs via pgvector cosine distance."""
    emb = await embedder.embed_one(query)
    if not emb:
        return "Embedding failed — semantic search unavailable."
    rows = await db.semantic_search(emb, limit=limit)
    _tlog(results=len(rows))
    if not rows:
        return "No relevant entries found."
    lines = [f"Semantic search results for: '{query}'"]
    for r in rows:
        lines.append(
            f"\n--- {r['package']} ({r['version']}, {_format_date(r['entry_date'])}) ---"
        )
        lines.append(r["content"])
    return "\n".join(lines)


@mcp.tool()
@_tool_wrapper("fts_search")
async def fts_search(query: str, limit: int = 10, since: str | None = None) -> str:
    """Keyword / full-text search via tsvector over changelog content.

    ``since`` accepts ISO 8601 or natural language (e.g. "2024-01-01", "1 year ago").
    """
    since_dt = parse_when(since) if since else None
    if since and since_dt is None:
        return MSG_SINCE_UNPARSEABLE.format(since)
    rows = await db.fts_search(query, limit=limit, since=since_dt)
    _tlog(results=len(rows))
    since_tag = f" (since {_format_date(since_dt)})" if since_dt else ""
    if not rows:
        return f"No FTS matches for: '{query}'{since_tag}."
    lines = [f"FTS results for: '{query}'{since_tag}"]
    for r in rows:
        lines.append(
            f"\n--- {r['package']} ({r['version']}, {_format_date(r['entry_date'])}, "
            f"rank={r['rank']:.3f}) ---"
        )
        lines.append(r["content"])
    return "\n".join(lines)


@mcp.tool()
@_tool_wrapper("list_cves")
async def list_cves(package: str, since: str | None = None) -> str:
    """List all CVE IDs mentioned in *package*'s changelog, optionally filtered to entries
    from ``since`` onward (ISO 8601 or natural language, e.g. "2024-01-01", "1 year ago").
    """
    validate_package_name(package)
    since_dt = parse_when(since) if since else None
    if since and since_dt is None:
        return MSG_SINCE_UNPARSEABLE.format(since)
    if await _ensure_or_queue(package) is _Readiness.QUEUED:
        return MSG_PKG_QUEUED.format(package)
    rows = await db.list_package_cves(package, since=since_dt)
    _tlog(entries=len(rows))
    since_tag = f" (since {_format_date(since_dt)})" if since_dt else ""
    if not rows:
        return f"No CVE mentions found in '{package}' changelog{since_tag}."
    header = (
        f"CVE entries for {package}{since_tag} — "
        f"{len(rows)} matching changelog entries:"
    )
    return _format_listing_rows(rows, header, _CVE_CONTENT_RE)


@mcp.tool()
@_tool_wrapper("find_bug")
async def find_bug(bug_id: str, package: str | None = None) -> str:
    """Case-insensitive search for a SUSE/openSUSE bugzilla reference across cached changelogs.

    *bug_id* accepts ``bsc#1234567``, ``boo#1234567``, or ``bnc#1234567``.
    Optionally scope to a single *package*.
    """
    try:
        bug_id = _validate_bug_id(bug_id)
    except ValueError as e:
        return str(e)
    if package:
        validate_package_name(package)
        if await _ensure_or_queue(package) is _Readiness.QUEUED:
            return MSG_PKG_QUEUED.format(package)

    matches = await db.find_bug(bug_id, package_name=package)
    _tlog(matches=len(matches))
    if not matches:
        scope = f"package '{package}'" if package else "any cached package"
        return f"No mentions of {bug_id} found in {scope}."
    return _format_match_rows(matches, f"Found {len(matches)} entries mentioning {bug_id}:")


@mcp.tool()
@_tool_wrapper("list_bugs")
async def list_bugs(package: str, since: str | None = None) -> str:
    """List all SUSE/openSUSE bugzilla references (bsc#, boo#, bnc#) in *package*'s changelog.

    ``since`` accepts ISO 8601 or natural language (e.g. "2024-01-01", "1 year ago").
    """
    validate_package_name(package)
    since_dt = parse_when(since) if since else None
    if since and since_dt is None:
        return MSG_SINCE_UNPARSEABLE.format(since)
    if await _ensure_or_queue(package) is _Readiness.QUEUED:
        return MSG_PKG_QUEUED.format(package)
    rows = await db.list_package_bugs(package, since=since_dt)
    _tlog(entries=len(rows))
    since_tag = f" (since {_format_date(since_dt)})" if since_dt else ""
    if not rows:
        return f"No bug references found in '{package}' changelog{since_tag}."
    header = (
        f"Bug references for {package}{since_tag} — "
        f"{len(rows)} matching changelog entries:"
    )
    return _format_listing_rows(rows, header, _BSC_CONTENT_RE)


# ---------------------------------------------------------------------------
# Phase 2: spec tools
# ---------------------------------------------------------------------------
_SPEC_SOURCES = {
    "opensuse": fetch_obs_spec,
    "fedora": fetch_pagure_spec,
}


async def _ensure_spec(package: str, source: str = "opensuse") -> tuple[int, int, str, str] | None:
    """Fetch + persist spec if not cached. Returns (package_id, spec_id, content, source_url)
    or None if no source has it.
    """
    pkg_id = await db.get_package_id(package)
    if pkg_id is not None:
        cached = await db.get_spec(pkg_id, source)
        if cached:
            return pkg_id, int(cached["id"]), cached["content"], ""

    fetcher = _SPEC_SOURCES.get(source)
    if not fetcher:
        return None
    text, url = await fetcher(package)
    if not text:
        return None

    pkg_id = await db.upsert_package(package)
    spec_id = await db.upsert_spec(pkg_id, source, version=None, content=text)
    sections = chunk_sections(extract_sections(text))
    if sections:
        embeddings = await embedder.embed_batch(s.content for s in sections)
        if not embeddings:
            embeddings = [[] for _ in sections]
        await db.replace_spec_sections(spec_id, sections, embeddings)
    return pkg_id, spec_id, text, url or ""


@mcp.tool()
@_tool_wrapper("get_spec_details")
async def get_spec_details(package: str, source: str = "opensuse") -> str:
    """Return the parsed AST sections of *package*'s .spec from *source*
    (``opensuse`` or ``fedora``). Fetched on cache miss.
    """
    validate_package_name(package)
    if source not in _SPEC_SOURCES:
        return MSG_UNKNOWN_SPEC_SOURCE.format(source)
    out = await _ensure_spec(package, source)
    if out is None:
        return f"No {source} spec found for {package}."
    _, _, content, _ = out
    sections = extract_sections(content)
    _tlog(sections=len(sections))
    lines = [f"Package: {package} (source: {source})  —  {len(sections)} sections"]
    for name, body in sections.items():
        body = body.strip()
        if not body:
            continue
        lines.append(f"\n## {name}\n{body}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 3: news + openQA tools
# ---------------------------------------------------------------------------
@mcp.tool()
@_tool_wrapper("get_news")
async def get_news(package: str | None = None, limit: int = 10, refresh: bool = False) -> str:
    """Recent Fedora Bodhi + openSUSE news items, optionally scoped to *package*.

    Set ``refresh=True`` to re-pull both feeds before querying.
    """
    if package:
        validate_package_name(package)
    if refresh:
        items = await fetch_all_news(limit=max(limit, 20))
        inserted = await db.upsert_news(items)
        _tlog(fetched=len(items), inserted=inserted)

    rows = await db.get_news(package_name=package, limit=limit)
    _tlog(results=len(rows))
    if not rows:
        scope = f"package '{package}'" if package else "any package"
        return f"No news items for {scope}. Try refresh=True to pull latest feeds."

    lines = [f"News items ({len(rows)}{' for ' + package if package else ''}):"]
    for r in rows:
        lines.append(
            f"\n[{r['importance'] or '-'}] {r['title']}\n"
            f"  source={r['source']} type={r['item_type'] or '-'} date={_format_date(r['item_date'])}\n"
            f"  url={r['url'] or '-'}\n"
            f"  {(r['content'] or '').strip()[:300]}"
        )
    return "\n".join(lines)


@mcp.tool()
@_tool_wrapper("get_openqa_tests")
async def get_openqa_tests(package: str) -> str:
    """List openQA tests that exercise *package*. Sourced from a pre-ingested
    ``os-autoinst-distri-opensuse`` checkout — refresh via ``ingest_openqa_repo``
    in scripts/worker.py.
    """
    validate_package_name(package)
    rows = await db.get_openqa_tests(package)
    _tlog(tests=len(rows))
    if not rows:
        return (
            f"No openQA tests recorded for '{package}'. "
            "Run the openQA ingest in the worker if the local DB is empty."
        )
    lines = [f"openQA tests for {package} ({len(rows)}):"]
    for r in rows:
        summary = f" — {r['summary']}" if r["summary"] else ""
        lines.append(f"  {r['test_path']}{summary}")
    return "\n".join(lines)


@mcp.tool()
@_tool_wrapper("get_sync_status")
async def get_sync_status(
    package: str | None = None,
    threshold_days: int = 7,
) -> str:
    """Show sync age for indexed packages; flag those older than *threshold_days*.

    If *package* is given, report only that package. Otherwise reports all
    packages in the manifest, sorted oldest-first.
    """
    names = [package] if package else None
    rows = await db.get_sync_ages(names)
    _tlog(count=len(rows))

    if not rows:
        target = f"'{package}'" if package else "any package"
        return f"No sync record found for {target}. Run sync-package first."

    threshold_s = threshold_days * 86400
    stale = [r for r in rows if int(r["age_seconds"]) >= threshold_s]
    fresh = [r for r in rows if int(r["age_seconds"]) < threshold_s]

    def _fmt_age(seconds: Any) -> str:
        s = int(seconds)
        if s < 3600:
            return f"{s // 60}m ago"
        if s < 86400:
            return f"{s // 3600}h ago"
        return f"{s // 86400}d ago"

    lines = [
        f"Sync status — threshold: {threshold_days}d"
        f" | total: {len(rows)} | fresh: {len(fresh)} | stale: {len(stale)}",
        "",
    ]
    if stale:
        lines.append(f"  STALE (>{threshold_days}d):")
        for r in stale:
            lines.append(f"    ⚠  {r['name']:<40} {_fmt_age(r['age_seconds'])}")
        lines.append("")
    if fresh:
        lines.append(f"  FRESH (<{threshold_days}d):")
        for r in fresh:
            lines.append(f"    ✓  {r['name']:<40} {_fmt_age(r['age_seconds'])}")

    return "\n".join(lines)


@mcp.tool()
@_tool_wrapper("find_core_packages")
async def find_core_packages(
    n: int = 50,
    seed_pattern: str = "base",
    expand: bool = True,
) -> str:
    """Identify the *n* most important distro packages by reverse-dependency count.

    Seeds from the given pattern (default: base), optionally expands one hop of
    transitive deps to surface hidden-but-fundamental packages (e.g. glibc), then
    ranks the candidate pool by how many installed packages require each one.
    Pattern meta-packages (patterns-*) are excluded from results.
    """
    n = max(1, min(n, 200))

    seed_pkgs = await rpm_mgr.find_pattern_packages(seed_pattern)
    if not seed_pkgs:
        return (
            f"Pattern '{seed_pattern}' not found in the local RPM database. "
            "Try 'base', 'enhanced_base', or check `rpm -q --whatprovides 'pattern():<name>'`."
        )
    _tlog(seed_count=len(seed_pkgs))

    candidates: set[str] = set(seed_pkgs)
    if expand:
        dep_results = await asyncio.gather(
            *(rpm_mgr.get_dependencies(p) for p in seed_pkgs),
            return_exceptions=True,
        )
        for res in dep_results:
            if isinstance(res, frozenset):
                candidates.update(res)

    candidates = {p for p in candidates if not p.startswith("patterns-")}
    _tlog(candidate_count=len(candidates))

    rdep_results = await asyncio.gather(
        *(rpm_mgr.get_reverse_dependencies(p) for p in candidates),
        return_exceptions=True,
    )
    scored: list[tuple[int, str]] = []
    for pkg, res in zip(candidates, rdep_results):
        if isinstance(res, frozenset):
            scored.append((len(res), pkg))

    scored.sort(reverse=True)
    top = scored[:n]
    _tlog(result_count=len(top))

    width = len(str(len(top)))
    lines = [
        f"Core packages seeded from '{seed_pattern}' pattern"
        f" (top {len(top)} of {len(scored)} candidates by reverse-dep count):",
        "",
    ]
    for rank, (count, pkg) in enumerate(top, 1):
        lines.append(f"  {rank:{width}}. {pkg:<40} (required by {count} packages)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

_CLI_TOOL_FUNCS: list[Callable[..., Awaitable[str]]] = [
    analyze_package_diff,
    get_recent_releases,
    get_changes_in_range,
    get_dependencies,
    get_reverse_dependencies,
    find_cve,
    get_dependency_changes,
    sync_package,
    semantic_search,
    fts_search,
    list_cves,
    find_bug,
    list_bugs,
    get_spec_details,
    get_news,
    get_openqa_tests,
    find_core_packages,
    get_sync_status,
]


def _resolve_param_type(annotation: str) -> tuple[type, bool]:
    """Parse annotation string → (python_type, is_optional)."""
    s = annotation.strip()
    is_optional = "None" in s
    s = s.replace("| None", "").replace("None |", "").replace("Optional[", "").rstrip("]").strip()
    return {"str": str, "int": int, "float": float, "bool": bool}.get(s, str), is_optional


def _build_cli_parser() -> tuple[argparse.ArgumentParser, dict[str, Callable[..., Awaitable[str]]]]:
    parser = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        description="rpm-mcp — invoke a tool directly (CLI) or run as MCP server (default).",
    )
    subparsers = parser.add_subparsers(dest="tool", metavar="TOOL")
    subparsers.add_parser("serve", help="Run as MCP stdio server (same as invoking with no subcommand).")

    func_map: dict[str, Callable[..., Awaitable[str]]] = {}
    for fn in _CLI_TOOL_FUNCS:
        cmd = fn.__name__.replace("_", "-")
        func_map[cmd] = fn
        sig = inspect.signature(fn)
        doc_line = (fn.__doc__ or "").strip().splitlines()[0].replace("%", "%%") if fn.__doc__ else ""
        sub = subparsers.add_parser(cmd, help=doc_line)

        for pname, param in sig.parameters.items():
            ann = str(param.annotation)
            py_type, is_optional = _resolve_param_type(ann)
            has_default = param.default is not inspect.Parameter.empty
            flag = f"--{pname.replace('_', '-')}"

            if py_type is bool:
                default_bool = param.default if has_default else False
                if default_bool:
                    sub.add_argument(
                        f"--no-{pname.replace('_', '-')}",
                        dest=pname,
                        action="store_false",
                        default=True,
                    )
                else:
                    sub.add_argument(flag, action="store_true", default=False)
            elif has_default or is_optional:
                kw: dict[str, Any] = {"default": param.default if has_default else None}
                if py_type in (str, int, float):
                    kw["type"] = py_type
                sub.add_argument(flag, **kw)
            else:
                kw = {}
                if py_type in (str, int, float):
                    kw["type"] = py_type
                sub.add_argument(pname, **kw)

    return parser, func_map


if __name__ == "__main__":
    parser, func_map = _build_cli_parser()
    ns = parser.parse_args()

    if ns.tool is None or ns.tool == "serve":
        transport = os.environ.get("MCP_TRANSPORT", "stdio")
        if transport == "sse":
            mcp.run(transport="sse")
        else:
            mcp.run()
    else:
        async def _run_cli() -> None:
            db_connected = False
            try:
                await db.connect()
                db_connected = True
            except Exception as exc:
                _logger.warning("db_unavailable_cli", error=str(exc))
            try:
                kwargs = {
                    k.replace("-", "_"): v
                    for k, v in vars(ns).items()
                    if k != "tool"
                }
                result = await func_map[ns.tool](**kwargs)
                print(result)
            finally:
                await source_registry.close()
                if db_connected:
                    await db.close()

        asyncio.run(_run_cli())
