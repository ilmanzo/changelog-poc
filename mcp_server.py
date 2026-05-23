"""FastMCP entrypoint for rpm-mcp.

Phase 1: changelog tools (parity with changelog-poc) wired to Postgres+pgvector.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import AsyncIterator

import structlog
from mcp.server.fastmcp import FastMCP
from packaging import version as pkg_version

from src import embedder
from src.config import settings
from src.db import Database
from src.git_manager import GitManager
from src.ingest import IngestService, IngestStatus, validate_package_name
from src.llm import ask_llm
from src.logging_config import configure_logging
from src.models import ChangelogEntry
from src.modernize import check_modernization
from src.news_fetcher import fetch_all_news
from src.openqa_fetcher import scan_tests
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

# Matches CVE/BSC IDs embedded anywhere in changelog text (not anchored, unlike CVE_RE/BSC_RE).
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
# helpers
# ---------------------------------------------------------------------------
async def _ensure_fresh(package: str, refresh: bool = False) -> bool:
    """True if cache fresh OR a triggered ingest succeeded."""
    pkg_id = await db.get_package_id(package)
    if not refresh and pkg_id is not None and await db.is_fresh(pkg_id, settings.cache_ttl_seconds):
        return True
    res = await ingest_service.ingest(package)
    return res.status is IngestStatus.INDEXED


def _records_to_entries(rows: list) -> list[ChangelogEntry]:
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


# ---------------------------------------------------------------------------
# changelog tools
# ---------------------------------------------------------------------------
@mcp.tool()
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
    t0 = time.perf_counter()
    log = _logger.bind(tool="analyze_package_diff", package=package,
                       version_start=version_start, version_end=version_end, refresh=refresh)
    try:
        validate_package_name(package)
        if not await _ensure_fresh(package, refresh):
            return f"Package '{package}' not found in any source (local RPM, OBS, src.opensuse.org)."

        entries = await _load_entries(package)
        if not entries:
            return f"Package '{package}' not found in any source (local RPM, OBS, src.opensuse.org)."

        relevant: list[ChangelogEntry] = []
        strategy = "none"
        try:
            v_start = pkg_version.parse(clean_version(version_start) or "0")
            v_end = pkg_version.parse(clean_version(version_end) or "9999")
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
                strategy = "semver"
        except Exception as ve:
            log.warning("version_filter_failed", error=str(ve))
            relevant = [e for e in entries if content_matches(e, version_start, version_end)]
            if relevant:
                strategy = "fuzzy_fallback"

        if not relevant:
            relevant = [e for e in entries
                        if version_end in str(e.version) or content_matches(e, version_end)]
            if relevant:
                strategy = "version_string_match"

        if not relevant:
            log.warning("version_range_not_found", total=len(entries),
                        elapsed_s=round(time.perf_counter() - t0, 3))
            return (
                f"No changelog entries found for '{package}' between versions "
                f"{version_start} and {version_end}. "
                f"Package exists but the version range may be incorrect or too narrow. "
                f"Available entries span {len(entries)} versions."
            )

        git_logs = ""
        if deep:
            upstream = await db.get_upstream_url(package)
            if upstream:
                repo_path = await git_mgr.ensure_repo(upstream, package)
                tag_a, tag_b = await asyncio.gather(
                    git_mgr.find_tag(repo_path, version_start),
                    git_mgr.find_tag(repo_path, version_end),
                )
                if tag_a and tag_b:
                    git_logs = await git_mgr.get_logs_between_tags(repo_path, tag_a, tag_b)
                else:
                    start_date = relevant[-1].date
                    end_date = relevant[0].date
                    if start_date and end_date:
                        git_logs = await git_mgr.get_logs_between_timestamps(
                            repo_path, start_date, end_date
                        )

        lines = [f"Package: {package} Diff ({version_start} -> {version_end})",
                 "\nCHANGELOG ENTRIES:"]
        for e in relevant:
            lines.append(f"--- {e.date.date()} ({e.version}) ---\n{e.content}")
        if git_logs:
            lines.append("\nUPSTREAM GIT COMMITS:")
            lines.append(git_logs)

        log.info("tool_done", filter_strategy=strategy, result_entries=len(relevant),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error analyzing {package}: {e}"


async def _fetch_recent_releases(
    package: str, n: int, refresh: bool
) -> list[tuple[str, datetime, list[ChangelogEntry]]] | None:
    n = max(1, min(n, 50))
    if not await _ensure_fresh(package, refresh):
        return None
    entries = await _load_entries(package)
    if not entries:
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
async def get_recent_releases(package: str, n: int = 3, refresh: bool = False) -> str:
    """Last *n* distinct releases of *package*, grouped by version."""
    t0 = time.perf_counter()
    log = _logger.bind(tool="get_recent_releases", package=package, n=n, refresh=refresh)
    try:
        validate_package_name(package)
        groups = await _fetch_recent_releases(package, n, refresh)
        if groups is None:
            return f"Package '{package}' not found in any source."

        lines = [f"Package: {package} — last {len(groups)} release(s)"]
        for ver, newest_dt, items in groups:
            lines.append(f"\n=== {ver} ({newest_dt.date()}) ===")
            for e in items:
                lines.append(f"--- {e.date.date()} ({e.author}) ---\n{e.content}")

        log.info("tool_done", releases=len(groups),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error fetching recent releases for {package}: {e}"


@mcp.tool()
async def get_changes_in_range(
    package: str, since: str, until: str | None = None, refresh: bool = False
) -> str:
    """Changelog entries within ``[since, until]`` (ISO 8601 or natural language)."""
    t0 = time.perf_counter()
    log = _logger.bind(tool="get_changes_in_range", package=package, since=since,
                       until=until, refresh=refresh)
    try:
        validate_package_name(package)
        since_dt = parse_when(since)
        if since_dt is None:
            return f"Could not parse 'since' value: {since!r}."
        until_dt = parse_when(until) if until else datetime.now(UTC)
        if until_dt is None:
            return f"Could not parse 'until' value: {until!r}."
        if since_dt >= until_dt:
            return (
                f"Invalid range: 'since' ({since_dt.isoformat()}) is not before "
                f"'until' ({until_dt.isoformat()})."
            )

        if not await _ensure_fresh(package, refresh):
            return f"Package '{package}' not found in any source."

        pkg_id = await db.get_package_id(package)
        assert pkg_id is not None
        rows = await db.fetch_entries_in_range(pkg_id, since_dt, until_dt)
        entries = _records_to_entries(rows)

        header = (
            f"Package: {package} — changes between "
            f"{since_dt.date()} and {until_dt.date()} ({len(entries)} entries)"
        )
        if not entries:
            return header + "\n(no entries in this window)"
        lines = [header]
        for e in entries:
            lines.append(f"\n--- {e.date.date()} ({e.version}, {e.author}) ---\n{e.content}")
        log.info("tool_done", entries=len(entries),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error fetching changes for {package}: {e}"


@mcp.tool()
async def get_dependencies(package: str) -> str:
    """Direct runtime deps of *package* from the local RPM database."""
    t0 = time.perf_counter()
    log = _logger.bind(tool="get_dependencies", package=package)
    try:
        validate_package_name(package)
        deps = await rpm_mgr.get_dependencies(package)
        log.info("tool_done", count=len(deps),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        if not deps:
            return f"No dependencies resolved for '{package}'."
        return f"Dependencies of {package} ({len(deps)}):\n" + "\n".join(sorted(deps))
    except RuntimeError as e:
        return f"Package '{package}' not installed locally: {e}"
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error fetching dependencies for {package}: {e}"


@mcp.tool()
async def get_reverse_dependencies(package: str) -> str:
    """Installed packages that depend on *package* (local RPM database)."""
    t0 = time.perf_counter()
    log = _logger.bind(tool="get_reverse_dependencies", package=package)
    try:
        validate_package_name(package)
        rdeps = await rpm_mgr.get_reverse_dependencies(package)
        log.info("tool_done", count=len(rdeps),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        if not rdeps:
            return f"No installed packages depend on '{package}'."
        return f"Packages depending on {package} ({len(rdeps)}):\n" + "\n".join(sorted(rdeps))
    except RuntimeError as e:
        return f"Package '{package}' not installed locally: {e}"
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error fetching reverse dependencies for {package}: {e}"


@mcp.tool()
async def find_cve(cve_id: str, package: str | None = None) -> str:
    """Case-insensitive substring search for a CVE ID across cached changelogs."""
    t0 = time.perf_counter()
    log = _logger.bind(tool="find_cve", cve_id=cve_id, package=package)
    try:
        if not CVE_RE.match(cve_id):
            return f"Invalid CVE ID '{cve_id}'. Expected CVE-YYYY-NNNN(NNN)."
        cve_id = cve_id.upper()
        if package:
            validate_package_name(package)
            if not await _ensure_fresh(package):
                return f"Package '{package}' not found in any source."

        matches = await db.find_cve(cve_id, package_name=package)
        if not matches:
            scope = f"package '{package}'" if package else "any cached package"
            return f"No mentions of {cve_id} found in {scope}."

        lines = [f"Found {len(matches)} entries mentioning {cve_id}:"]
        for r in matches:
            lines.append(
                f"\n--- {r['package']} {r['version']} "
                f"({r['entry_date'].date() if r['entry_date'] else '?'}) ---\n{r['content']}"
            )
        log.info("tool_done", matches=len(matches),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error searching for {cve_id}: {e}"


@mcp.tool()
async def get_dependency_changes(
    package: str, n: int = 3, depth: int = 1, refresh: bool = False
) -> str:
    """For each (transitive) dependency of *package*, return its last *n* releases."""
    t0 = time.perf_counter()
    log = _logger.bind(tool="get_dependency_changes",
                       package=package, n=n, depth=depth, refresh=refresh)
    try:
        validate_package_name(package)
        depth = max(1, min(depth, 2))
        n = max(1, min(n, 20))

        visited: set[str] = {package}
        deps: set[str] = set()
        frontier: set[str] = {package}
        for _ in range(depth):
            new_frontier: set[str] = set()
            for pkg in frontier:
                try:
                    pkg_deps = await rpm_mgr.get_dependencies(pkg)
                except Exception as ex:
                    log.warning("rpm_deps_failed", package=pkg, error=str(ex))
                    continue
                for d in pkg_deps:
                    if d not in visited:
                        new_frontier.add(d)
                        visited.add(d)
            deps.update(new_frontier)
            frontier = new_frontier
            if len(deps) >= settings.f4_max_packages:
                break

        if not deps:
            return f"No dependencies resolved for '{package}' (is it installed locally?)."

        deps_list = sorted(deps)[: settings.f4_max_packages]
        results = await asyncio.gather(
            *(_fetch_recent_releases(d, n, refresh) for d in deps_list),
            return_exceptions=True,
        )

        lines = [
            f"Dependencies of {package} "
            f"(depth={depth}, {len(deps_list)} packages, last {n} release(s) each):"
        ]
        ok = err = missing = 0
        for dep, res in zip(deps_list, results):
            if isinstance(res, BaseException):
                err += 1
                lines.append(f"\n## {dep}: error — {res}")
                continue
            if res is None:
                missing += 1
                lines.append(f"\n## {dep}: no changelog found in any source")
                continue
            ok += 1
            lines.append(f"\n## {dep} — last {len(res)} release(s)")
            for ver, newest_dt, items in res:
                lines.append(f"  === {ver} ({newest_dt.date()}) ===")
                for e in items:
                    lines.append(f"  {e.content}")
        log.info("tool_done", ok=ok, missing=missing, errors=err,
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error fetching dependency changes for {package}: {e}"


@mcp.tool()
async def sync_package(package: str) -> str:
    """Force-ingest *package* — fetch + embed + upsert. Thin wrapper over IngestService."""
    log = _logger.bind(tool="sync_package", package=package)
    try:
        result = await ingest_service.ingest(package)
    except Exception as e:
        log.exception("tool_error")
        return f"Sync failed for {package}: {e}"
    if result.status is IngestStatus.INDEXED:
        return f"Successfully indexed {result.entries} entries for {package} (source: {result.source})."
    if result.status is IngestStatus.EMPTY:
        return f"No changelog found for {package} in any source."
    return f"Sync failed for {package}: {result.error}"


@mcp.tool()
async def semantic_search(query: str, limit: int = 5) -> str:
    """Natural-language search across indexed changelogs via pgvector cosine distance."""
    t0 = time.perf_counter()
    log = _logger.bind(tool="semantic_search", query=query, limit=limit)
    try:
        emb = await embedder.embed_one(query)
        if not emb:
            return "Embedding failed — semantic search unavailable."
        rows = await db.semantic_search(emb, limit=limit)
        if not rows:
            return "No relevant entries found."
        lines = [f"Semantic search results for: '{query}'"]
        for r in rows:
            d = r["entry_date"].date() if r["entry_date"] else "?"
            lines.append(f"\n--- {r['package']} ({r['version']}, {d}) ---")
            lines.append(r["content"])
        log.info("tool_done", results=len(rows),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Search failed: {e}"


@mcp.tool()
async def fts_search(query: str, limit: int = 10, since: str | None = None) -> str:
    """Keyword / full-text search via tsvector over changelog content.

    ``since`` accepts ISO 8601 or natural language (e.g. "2024-01-01", "1 year ago").
    """
    t0 = time.perf_counter()
    log = _logger.bind(tool="fts_search", query=query, limit=limit, since=since)
    try:
        since_dt = parse_when(since) if since else None
        if since and since_dt is None:
            return f"Could not parse 'since' value: {since!r}."
        rows = await db.fts_search(query, limit=limit, since=since_dt)
        if not rows:
            scope = f" since {since_dt.date()}" if since_dt else ""
            return f"No FTS matches for: '{query}'{scope}."
        lines = [f"FTS results for: '{query}'" + (f" (since {since_dt.date()})" if since_dt else "")]
        for r in rows:
            d = r["entry_date"].date() if r["entry_date"] else "?"
            lines.append(f"\n--- {r['package']} ({r['version']}, {d}, rank={r['rank']:.3f}) ---")
            lines.append(r["content"])
        log.info("tool_done", results=len(rows),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"FTS search failed: {e}"


@mcp.tool()
async def list_cves(package: str, since: str | None = None) -> str:
    """List all CVE IDs mentioned in *package*'s changelog, optionally filtered to entries
    from ``since`` onward (ISO 8601 or natural language, e.g. "2024-01-01", "1 year ago").
    """
    t0 = time.perf_counter()
    log = _logger.bind(tool="list_cves", package=package, since=since)
    try:
        validate_package_name(package)
        since_dt = parse_when(since) if since else None
        if since and since_dt is None:
            return f"Could not parse 'since' value: {since!r}."
        if not await _ensure_fresh(package):
            return f"Package '{package}' not found in any source."
        rows = await db.list_package_cves(package, since=since_dt)
        if not rows:
            scope = f" since {since_dt.date()}" if since_dt else ""
            return f"No CVE mentions found in '{package}' changelog{scope}."
        since_tag = f" (since {since_dt.date()})" if since_dt else ""
        lines = [f"CVE entries for {package}{since_tag} — {len(rows)} matching changelog entries:"]
        for r in rows:
            d = r["entry_date"].date() if r["entry_date"] else "?"
            cves = ", ".join(sorted(set(_CVE_CONTENT_RE.findall(r["content"]))))
            lines.append(f"\n--- {r['version']} ({d}) — {cves} ---\n{r['content'].strip()[:400]}")
        log.info("tool_done", entries=len(rows),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error listing CVEs for {package}: {e}"


@mcp.tool()
async def find_bug(bug_id: str, package: str | None = None) -> str:
    """Case-insensitive search for a SUSE/openSUSE bugzilla reference across cached changelogs.

    *bug_id* accepts ``bsc#1234567``, ``boo#1234567``, or ``bnc#1234567``.
    Optionally scope to a single *package*.
    """
    t0 = time.perf_counter()
    log = _logger.bind(tool="find_bug", bug_id=bug_id, package=package)
    try:
        if not BSC_RE.match(bug_id):
            return f"Invalid bug ID '{bug_id}'. Expected bsc#NNNNNN, boo#NNNNNN, or bnc#NNNNNN."
        bug_id = bug_id.lower()
        if package:
            validate_package_name(package)
            if not await _ensure_fresh(package):
                return f"Package '{package}' not found in any source."

        matches = await db.find_bug(bug_id, package_name=package)
        if not matches:
            scope = f"package '{package}'" if package else "any cached package"
            return f"No mentions of {bug_id} found in {scope}."

        lines = [f"Found {len(matches)} entries mentioning {bug_id}:"]
        for r in matches:
            lines.append(
                f"\n--- {r['package']} {r['version']} "
                f"({r['entry_date'].date() if r['entry_date'] else '?'}) ---\n{r['content']}"
            )
        log.info("tool_done", matches=len(matches),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error searching for {bug_id}: {e}"


@mcp.tool()
async def list_bugs(package: str, since: str | None = None) -> str:
    """List all SUSE/openSUSE bugzilla references (bsc#, boo#, bnc#) in *package*'s changelog.

    ``since`` accepts ISO 8601 or natural language (e.g. "2024-01-01", "1 year ago").
    """
    t0 = time.perf_counter()
    log = _logger.bind(tool="list_bugs", package=package, since=since)
    try:
        validate_package_name(package)
        since_dt = parse_when(since) if since else None
        if since and since_dt is None:
            return f"Could not parse 'since' value: {since!r}."
        if not await _ensure_fresh(package):
            return f"Package '{package}' not found in any source."
        rows = await db.list_package_bugs(package, since=since_dt)
        if not rows:
            scope = f" since {since_dt.date()}" if since_dt else ""
            return f"No bug references found in '{package}' changelog{scope}."
        since_tag = f" (since {since_dt.date()})" if since_dt else ""
        lines = [f"Bug references for {package}{since_tag} — {len(rows)} matching changelog entries:"]
        for r in rows:
            d = r["entry_date"].date() if r["entry_date"] else "?"
            bugs = ", ".join(sorted(set(_BSC_CONTENT_RE.findall(r["content"]))))
            lines.append(f"\n--- {r['version']} ({d}) — {bugs} ---\n{r['content'].strip()[:400]}")
        log.info("tool_done", entries=len(rows),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error listing bugs for {package}: {e}"


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
async def get_spec_details(package: str, source: str = "opensuse") -> str:
    """Return the parsed AST sections of *package*'s .spec from *source*
    (``opensuse`` or ``fedora``). Fetched on cache miss.
    """
    t0 = time.perf_counter()
    log = _logger.bind(tool="get_spec_details", package=package, source=source)
    try:
        validate_package_name(package)
        if source not in _SPEC_SOURCES:
            return f"Unknown source {source!r}. Use 'opensuse' or 'fedora'."
        out = await _ensure_spec(package, source)
        if out is None:
            return f"No {source} spec found for {package}."
        _, _, content, _ = out
        sections = extract_sections(content)
        lines = [f"Package: {package} (source: {source})  —  {len(sections)} sections"]
        for name, body in sections.items():
            body = body.strip()
            if not body:
                continue
            lines.append(f"\n## {name}\n{body}")
        log.info("tool_done", sections=len(sections),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error fetching spec for {package}: {e}"


@mcp.tool()
async def modernize_package(package: str, source: str = "opensuse") -> str:
    """Scan *package*'s .spec for deprecated macros and ask the LLM for a refactor."""
    t0 = time.perf_counter()
    log = _logger.bind(tool="modernize_package", package=package, source=source)
    try:
        validate_package_name(package)
        if source not in _SPEC_SOURCES:
            return f"Unknown source {source!r}. Use 'opensuse' or 'fedora'."
        out = await _ensure_spec(package, source)
        if out is None:
            return f"No {source} spec found for {package}."
        _, _, content, _ = out

        suggestions = check_modernization(content)
        if not suggestions:
            return f"No deprecated macros found in {package} ({source})."

        header = [f"Found {len(suggestions)} suggestion(s) for {package}:"]
        for s in suggestions:
            replacement = s.replacement if s.replacement is not None else "(remove)"
            header.append(
                f"  L{s.line}: {s.content}\n"
                f"      pattern  : {s.pattern}\n"
                f"      replace  : {replacement}\n"
                f"      reason   : {s.description}"
            )

        context = (
            f"Spec file for {package}:\n```\n{content}\n```\n\n"
            "Findings:\n" + "\n".join(
                f"- L{s.line} `{s.content}` — {s.description}" for s in suggestions
            )
        )
        answer = await ask_llm(
            "Rewrite this spec file applying the suggested modernizations. "
            "Show only the changed sections.",
            context,
        )
        log.info("tool_done", suggestions=len(suggestions),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(header) + "\n\n--- LLM rewrite ---\n" + answer
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error modernizing {package}: {e}"


@mcp.tool()
async def explain_build(package: str, source: str = "opensuse") -> str:
    """LLM walk-through of the %prep / %build / %install / %check sections."""
    t0 = time.perf_counter()
    log = _logger.bind(tool="explain_build", package=package, source=source)
    try:
        validate_package_name(package)
        if source not in _SPEC_SOURCES:
            return f"Unknown source {source!r}. Use 'opensuse' or 'fedora'."
        out = await _ensure_spec(package, source)
        if out is None:
            return f"No {source} spec found for {package}."
        _, _, content, _ = out

        sections = extract_sections(content)
        wanted = {"%prep", "%build", "%install", "%check"}
        relevant = {k: v for k, v in sections.items() if k in wanted and v.strip()}
        if not relevant:
            return f"No build sections found in {package} spec."

        context = "\n\n".join(f"## {name}\n{body}" for name, body in relevant.items())
        answer = await ask_llm(
            "Explain step-by-step what this package's build pipeline does. "
            "Cover %prep, %build, %install, %check.",
            context,
        )
        log.info("tool_done", sections=len(relevant),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Build walkthrough for {package} ({source}):\n\n{answer}"
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error explaining build for {package}: {e}"


@mcp.tool()
async def analyze_package(question: str, package: str, source: str = "opensuse") -> str:
    """LLM Q&A grounded on *package*'s stored spec (any source)."""
    t0 = time.perf_counter()
    log = _logger.bind(tool="analyze_package", package=package, source=source)
    try:
        validate_package_name(package)
        if source not in _SPEC_SOURCES:
            return f"Unknown source {source!r}. Use 'opensuse' or 'fedora'."
        out = await _ensure_spec(package, source)
        if out is None:
            return f"No {source} spec found for {package}."
        _, _, content, _ = out
        answer = await ask_llm(question, f"Spec for {package}:\n{content}")
        log.info("tool_done", q_chars=len(question),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return answer
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error analyzing {package}: {e}"


# ---------------------------------------------------------------------------
# Phase 3: news + openQA tools
# ---------------------------------------------------------------------------
@mcp.tool()
async def get_news(package: str | None = None, limit: int = 10, refresh: bool = False) -> str:
    """Recent Fedora Bodhi + openSUSE news items, optionally scoped to *package*.

    Set ``refresh=True`` to re-pull both feeds before querying.
    """
    t0 = time.perf_counter()
    log = _logger.bind(tool="get_news", package=package, limit=limit, refresh=refresh)
    try:
        if package:
            validate_package_name(package)
        if refresh:
            items = await fetch_all_news(limit=max(limit, 20))
            inserted = await db.upsert_news(items)
            log.info("news_refreshed", fetched=len(items), inserted=inserted)

        rows = await db.get_news(package_name=package, limit=limit)
        if not rows:
            scope = f"package '{package}'" if package else "any package"
            return f"No news items for {scope}. Try refresh=True to pull latest feeds."

        lines = [
            f"News items ({len(rows)}{' for ' + package if package else ''}):",
        ]
        for r in rows:
            d = r["item_date"].date() if r["item_date"] else "?"
            lines.append(
                f"\n[{r['importance'] or '-'}] {r['title']}\n"
                f"  source={r['source']} type={r['item_type'] or '-'} date={d}\n"
                f"  url={r['url'] or '-'}\n"
                f"  {(r['content'] or '').strip()[:300]}"
            )
        log.info("tool_done", results=len(rows),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error fetching news: {e}"


@mcp.tool()
async def get_openqa_tests(package: str) -> str:
    """List openQA tests that exercise *package*. Sourced from a pre-ingested
    ``os-autoinst-distri-opensuse`` checkout — refresh via ``ingest_openqa_repo``
    in scripts/worker.py.
    """
    t0 = time.perf_counter()
    log = _logger.bind(tool="get_openqa_tests", package=package)
    try:
        validate_package_name(package)
        rows = await db.get_openqa_tests(package)
        if not rows:
            return (
                f"No openQA tests recorded for '{package}'. "
                "Run the openQA ingest in the worker if the local DB is empty."
            )
        lines = [f"openQA tests for {package} ({len(rows)}):"]
        for r in rows:
            summary = f" — {r['summary']}" if r["summary"] else ""
            lines.append(f"  {r['test_path']}{summary}")
        log.info("tool_done", tests=len(rows),
                 elapsed_s=round(time.perf_counter() - t0, 3))
        return "\n".join(lines)
    except Exception as e:
        log.exception("tool_error", elapsed_s=round(time.perf_counter() - t0, 3))
        return f"Error fetching openQA tests for {package}: {e}"


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "sse":
        mcp.run(transport="sse")
    else:
        mcp.run()
