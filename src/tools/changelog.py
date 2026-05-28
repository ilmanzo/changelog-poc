"""Changelog tools: diff, releases, range, CVE/bug search, semantic + FTS, sync."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal

from mcp.server.fastmcp import FastMCP

from packaging import version as pkg_version

from .. import embedder
from ..ingest import IngestStatus, validate_package_name
from ..models import ChangelogEntry
from ..runtime import db, git_mgr, ingest_service
from ..version_utils import clean_version, content_matches, parse_when
from ._helpers import (
    BSC_CONTENT_RE,
    CVE_CONTENT_RE,
    MSG_PKG_NOT_FOUND,
    MSG_PKG_NOT_FOUND_SHORT,
    MSG_PKG_QUEUED,
    MSG_UNTIL_UNPARSEABLE,
    ReleaseGroup,
    _ensure_and_load_entries,
    _format_date,
    _format_listing_rows,
    _format_match_rows,
    _Readiness,
    _records_to_entries,
    _validate_bug_id,
    _validate_cve_id,
    parse_when_or_msg,
    queued_msg_or_none,
)
from ._wrap import _tlog, _tool_wrapper


# ---------------------------------------------------------------------------
# Tool bodies
# ---------------------------------------------------------------------------
@_tool_wrapper("analyze_package_diff", untrusted_sources=("rpm", "obs", "gitea"), category="search")
async def analyze_package_diff(
    package: str,
    version_start: str,
    version_end: str,
    deep: bool = False,
    refresh: bool = False,
) -> str:
    """Retrieve changelog entries between two versions of *package*.

    Filter priority: semver range -> fuzzy content match -> version-string match.
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

    git_logs = await _fetch_git_logs(package, version_start, version_end, relevant) if deep else ""

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


@_tool_wrapper("get_recent_releases", untrusted_sources=("rpm", "obs", "gitea"), category="fast")
async def get_recent_releases(package: str, n: int = 3, refresh: bool = False) -> str:
    """Last *n* distinct releases of *package*, grouped by version."""
    validate_package_name(package)
    groups = await _fetch_recent_releases(package, n, refresh)
    if groups is _Readiness.QUEUED:
        return MSG_PKG_QUEUED.format(package)
    if groups is None:
        return MSG_PKG_NOT_FOUND_SHORT.format(package)
    _tlog(releases=len(groups))

    lines = [f"Package: {package} -- last {len(groups)} release(s)"]
    for ver, newest_dt, items in groups:
        lines.append(f"\n=== {ver} ({_format_date(newest_dt)}) ===")
        for e in items:
            lines.append(f"--- {_format_date(e.date)} ({e.author}) ---\n{e.content}")
    return "\n".join(lines)


@_tool_wrapper("get_changes_in_range", untrusted_sources=("rpm", "obs", "gitea"), category="fast")
async def get_changes_in_range(
    package: str, since: str, until: str | None = None, refresh: bool = False
) -> str:
    """Changelog entries within ``[since, until]`` (ISO 8601 or natural language)."""
    validate_package_name(package)
    since_dt, err = parse_when_or_msg(since)
    if err or since_dt is None:
        return err or "Missing 'since' value."
    until_dt = parse_when(until) if until else datetime.now(UTC)
    if until_dt is None:
        return MSG_UNTIL_UNPARSEABLE.format(until)
    if since_dt >= until_dt:
        return (
            f"Invalid range: 'since' ({since_dt.isoformat()}) is not before 'until' ({until_dt.isoformat()})."
        )
    if (msg := await queued_msg_or_none(package, refresh)) is not None:
        return msg

    pkg_id = await db.get_package_id(package)
    if pkg_id is None:
        raise RuntimeError(f"internal: package row missing for {package!r} after readiness probe")
    rows = await db.fetch_entries_in_range(pkg_id, since_dt, until_dt)
    entries = _records_to_entries(rows)
    _tlog(entries=len(entries))

    header = (
        f"Package: {package} -- changes between "
        f"{_format_date(since_dt)} and {_format_date(until_dt)} ({len(entries)} entries)"
    )
    if not entries:
        return header + "\n(no entries in this window)"
    lines = [header]
    for e in entries:
        lines.append(f"\n--- {_format_date(e.date)} ({e.version}, {e.author}) ---\n{e.content}")
    return "\n".join(lines)


@_tool_wrapper("find_cve", untrusted_sources=("rpm", "obs", "gitea"), category="fast")
async def find_cve(cve_id: str, package: str | None = None) -> str:
    """Case-insensitive substring search for a CVE ID across cached changelogs."""
    try:
        cve_id = _validate_cve_id(cve_id)
    except ValueError as e:
        return str(e)
    if package:
        validate_package_name(package)
        if (msg := await queued_msg_or_none(package)) is not None:
            return msg

    matches = await db.find_cve(cve_id, package_name=package)
    _tlog(matches=len(matches))
    if not matches:
        scope = f"package '{package}'" if package else "any cached package"
        return f"No mentions of {cve_id} found in {scope}."
    return _format_match_rows(matches, f"Found {len(matches)} entries mentioning {cve_id}:")


@_tool_wrapper("list_cves", untrusted_sources=("rpm", "obs", "gitea"), category="fast")
async def list_cves(package: str, since: str | None = None) -> str:
    """List all CVE IDs mentioned in *package*'s changelog.

    ``since`` accepts ISO 8601 or natural language (e.g. "2024-01-01", "1 year ago").
    """
    validate_package_name(package)
    since_dt, err = parse_when_or_msg(since)
    if err:
        return err
    if (msg := await queued_msg_or_none(package)) is not None:
        return msg
    rows = await db.list_package_cves(package, since=since_dt)
    _tlog(entries=len(rows))
    since_tag = f" (since {_format_date(since_dt)})" if since_dt else ""
    if not rows:
        return f"No CVE mentions found in '{package}' changelog{since_tag}."
    header = f"CVE entries for {package}{since_tag} -- {len(rows)} matching changelog entries:"
    return _format_listing_rows(rows, header, CVE_CONTENT_RE)


@_tool_wrapper("find_bug", untrusted_sources=("rpm", "obs", "gitea"), category="fast")
async def find_bug(bug_id: str, package: str | None = None) -> str:
    """Case-insensitive search for a SUSE/openSUSE bugzilla reference across cached changelogs.

    *bug_id* accepts ``bsc#1234567``, ``boo#1234567``, or ``bnc#1234567``.
    """
    try:
        bug_id = _validate_bug_id(bug_id)
    except ValueError as e:
        return str(e)
    if package:
        validate_package_name(package)
        if (msg := await queued_msg_or_none(package)) is not None:
            return msg

    matches = await db.find_bug(bug_id, package_name=package)
    _tlog(matches=len(matches))
    if not matches:
        scope = f"package '{package}'" if package else "any cached package"
        return f"No mentions of {bug_id} found in {scope}."
    return _format_match_rows(matches, f"Found {len(matches)} entries mentioning {bug_id}:")


@_tool_wrapper("list_bugs", untrusted_sources=("rpm", "obs", "gitea"), category="fast")
async def list_bugs(package: str, since: str | None = None) -> str:
    """List all SUSE/openSUSE bugzilla references (bsc#, boo#, bnc#) in *package*'s changelog.

    ``since`` accepts ISO 8601 or natural language (e.g. "2024-01-01", "1 year ago").
    """
    validate_package_name(package)
    since_dt, err = parse_when_or_msg(since)
    if err:
        return err
    if (msg := await queued_msg_or_none(package)) is not None:
        return msg
    rows = await db.list_package_bugs(package, since=since_dt)
    _tlog(entries=len(rows))
    since_tag = f" (since {_format_date(since_dt)})" if since_dt else ""
    if not rows:
        return f"No bug references found in '{package}' changelog{since_tag}."
    header = f"Bug references for {package}{since_tag} -- {len(rows)} matching changelog entries:"
    return _format_listing_rows(rows, header, BSC_CONTENT_RE)


@_tool_wrapper("semantic_search", untrusted_sources=("rpm", "obs", "gitea"), category="search")
async def semantic_search(query: str, limit: int = 5) -> str:
    """Natural-language search across indexed changelogs via pgvector cosine distance."""
    emb = await embedder.embed_one(query)
    if not emb:
        return "Embedding failed -- semantic search unavailable."
    rows = await db.semantic_search(emb, limit=limit)
    _tlog(results=len(rows))
    if not rows:
        return "No relevant entries found."
    lines = [f"Semantic search results for: '{query}'"]
    for r in rows:
        lines.append(f"\n--- {r['package']} ({r['version']}, {_format_date(r['entry_date'])}) ---")
        lines.append(r["content"])
    return "\n".join(lines)


@_tool_wrapper("fts_search", untrusted_sources=("rpm", "obs", "gitea"), category="search")
async def fts_search(query: str, limit: int = 10, since: str | None = None) -> str:
    """Keyword / full-text search via tsvector over changelog content.

    ``since`` accepts ISO 8601 or natural language (e.g. "2024-01-01", "1 year ago").
    """
    since_dt, err = parse_when_or_msg(since)
    if err:
        return err
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


@_tool_wrapper("compare_versions", category="fast")
async def compare_versions(package: str, refresh: bool = False) -> str:
    """Compare latest changelog versions of *package* across distros (openSUSE, Fedora, Ubuntu).

    If ``refresh=True``, re-ingests from all distros before comparing.
    """
    validate_package_name(package)
    if refresh:
        await ingest_service.ingest_all_distros(package)
    rows = await db.compare_versions(package)
    _tlog(distros=len(rows), refresh=refresh)
    if not rows:
        return f"No changelog data for '{package}' in any distro. Try sync_package first."
    lines = [f"Version comparison for {package}:"]
    for r in rows:
        lines.append(f"  {r['distro']:12s}  {r['version']:30s}  ({_format_date(r['entry_date'])})")
    return "\n".join(lines)


@_tool_wrapper("sync_package")
async def sync_package(package: str, distro: str = "opensuse") -> str:
    """Force-ingest *package* from a single *distro* -- fetch + embed + upsert.

    Use ``compare_versions`` afterwards to see cross-distro differences.
    """
    result = await ingest_service.ingest(package, distro)
    _tlog(status=result.status.value, entries=result.entries, distro=distro)
    if result.status is IngestStatus.INDEXED:
        return f"Indexed {result.entries} entries for {package} [{distro}] (source: {result.source})."
    if result.status is IngestStatus.EMPTY:
        return f"No changelog found for {package} [{distro}]."
    return f"Sync failed for {package} [{distro}]: {result.error}"


@_tool_wrapper("sync_all_distros")
async def sync_all_distros(package: str) -> str:
    """Ingest *package* from every known distro (openSUSE, Fedora, Ubuntu) in parallel."""
    validate_package_name(package)
    results = await ingest_service.ingest_all_distros(package)
    lines = [f"Cross-distro sync for {package}:"]
    for r in results:
        tag = f"[{r.source or '?'}]"
        if r.status is IngestStatus.INDEXED:
            lines.append(f"  {tag:20s} {r.entries} entries indexed")
        elif r.status is IngestStatus.EMPTY:
            lines.append(f"  {tag:20s} not found")
        else:
            lines.append(f"  {tag:20s} {r.status.value}: {r.error}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _filter_entries_by_version(
    entries: list[ChangelogEntry], version_start: str, version_end: str
) -> tuple[list[ChangelogEntry], str]:
    """Apply semver -> fuzzy -> version-string fallback. Returns (matches, strategy_name)."""
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
    except (pkg_version.InvalidVersion, ValueError, TypeError):
        relevant = [e for e in entries if content_matches(e, version_start, version_end)]
        if relevant:
            return relevant, "fuzzy_fallback"

    fallback = [e for e in entries if version_end in str(e.version) or content_matches(e, version_end)]
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


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
CLI_TOOLS = (
    analyze_package_diff,
    get_recent_releases,
    get_changes_in_range,
    find_cve,
    list_cves,
    find_bug,
    list_bugs,
    semantic_search,
    fts_search,
    compare_versions,
    sync_package,
    sync_all_distros,
)


def register(mcp: FastMCP) -> None:
    for fn in CLI_TOOLS:
        mcp.tool()(fn)
