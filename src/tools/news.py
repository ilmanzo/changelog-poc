"""News / test-coverage / sync-status tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..config import settings
from ..ingest import validate_package_name
from ..runtime import db
from ._helpers import _format_date
from ._wrap import _mark_stale, _tlog, _tool_wrapper


@_tool_wrapper("get_news", untrusted_sources=("bodhi", "opensuse-rss"), category="fast")
async def get_news(package: str | None = None, limit: int = 10) -> str:
    """Recent Fedora Bodhi + openSUSE news items, optionally scoped to *package*.

    Read-only against the ``news`` table; the worker daemon owns ingestion.
    """
    if package:
        validate_package_name(package)

    rows = await db.get_news(package_name=package, limit=limit)
    _tlog(results=len(rows))
    if not rows:
        scope = f"package '{package}'" if package else "any package"
        return f"No news items for {scope}. The worker must populate the news table."

    lines = [f"News items ({len(rows)}{' for ' + package if package else ''}):"]
    for r in rows:
        lines.append(
            f"\n[{r['importance'] or '-'}] {r['title']}\n"
            f"  source={r['source']} type={r['item_type'] or '-'} date={_format_date(r['item_date'])}\n"
            f"  url={r['url'] or '-'}\n"
            f"  {(r['content'] or '').strip()[:300]}"
        )
    return "\n".join(lines)


async def _refresh_testcatalog(package: str, pkg_id: int | None) -> None:
    """Query live TestCatalog API, write results to DB, touch manifest.

    When the live query fails and stale cached rows exist, sets the stale
    banner via ``_mark_stale`` so the tool wrapper prepends a WARNING.
    """
    from ..testcatalog_client import TestCatalogClient

    client = TestCatalogClient()
    live_tests = []
    api_ok = False
    try:
        live_tests = await client.get_tests_for_package(package)
        api_ok = True
    except Exception as exc:
        _tlog(testcatalog_live_failed=str(exc))
    finally:
        await client.close()

    if api_ok:
        if live_tests:
            await db.upsert_openqa(live_tests, source="testcatalog")
        # always touch manifest so we don't hammer the API on empty results
        new_id = pkg_id or await db.get_package_id(package)
        if new_id:
            await db.touch_manifest(new_id, kind="testcatalog")
    elif pkg_id is not None:
        # live query failed -- check if stale cache exists to show banner
        synced_at: datetime | None = await db.get_synced_at(pkg_id, kind="testcatalog")
        if synced_at is not None:
            _mark_stale(synced_at)


@_tool_wrapper("get_test_coverage", untrusted_sources=("openqa", "testcatalog"), category="search")
async def get_test_coverage(package: str, source: str | None = None) -> str:
    """List test modules that exercise *package* from all available sources.

    *source* filters results: ``'openqa'`` (local repo scan, DB only),
    ``'testcatalog'`` (live API with 24h DB cache), or ``None`` for both.

    TestCatalog is queried live when the cache is stale; cached rows are served
    with a WARNING banner if the live API is unreachable.
    """
    validate_package_name(package)

    want_openqa = source is None or source == "openqa"
    want_testcatalog = source is None or source == "testcatalog"

    # openQA: always served from DB (populated by worker --openqa / --test-repo)
    rows_openqa = await db.get_openqa_tests(package, source="openqa") if want_openqa else []

    # TestCatalog: TTL-gated live query with DB cache fallback
    if want_testcatalog:
        pkg_id = await db.get_package_id(package)
        is_fresh = pkg_id is not None and await db.is_fresh(
            pkg_id, settings.cache_ttl_changelog_s, kind="testcatalog"
        )
        if not is_fresh:
            await _refresh_testcatalog(package, pkg_id)
        rows_testcatalog = await db.get_openqa_tests(package, source="testcatalog")
    else:
        rows_testcatalog = []

    total = len(rows_openqa) + len(rows_testcatalog)
    _tlog(openqa=len(rows_openqa), testcatalog=len(rows_testcatalog))

    if not rows_openqa and not rows_testcatalog:
        scope = f" (source={source})" if source else ""
        return (
            f"No test coverage recorded for '{package}'{scope}. "
            "Run the worker (--openqa / --test-repo / --testcatalog) to populate the DB."
        )

    lines = [f"Test coverage for {package} ({total} total):"]
    for r in rows_openqa:
        summary = f" -- {r['summary']}" if r["summary"] else ""
        lines.append(f"  [openqa]      {r['test_path']}{summary}")
    for r in rows_testcatalog:
        summary = f" -- {r['summary']}" if r["summary"] else ""
        lines.append(f"  [testcatalog] {r['test_path']}{summary}")
    return "\n".join(lines)


@_tool_wrapper("get_sync_status", category="fast")
async def get_sync_status(
    package: str | None = None,
    threshold_days: int = 7,
) -> str:
    """Show sync age for indexed packages; flag those older than *threshold_days*."""
    names = [package] if package else None
    rows = await db.get_sync_ages(names)
    _tlog(count=len(rows))

    if not rows:
        target = f"'{package}'" if package else "any package"
        return f"No sync record found for {target}. Run sync-package first."

    threshold_s = threshold_days * 86400
    stale = [r for r in rows if int(r["age_seconds"]) >= threshold_s]
    fresh = [r for r in rows if int(r["age_seconds"]) < threshold_s]

    lines = [
        f"Sync status -- threshold: {threshold_days}d"
        f" | total: {len(rows)} | fresh: {len(fresh)} | stale: {len(stale)}",
        "",
    ]
    _append_status_block(lines, "STALE", f">{threshold_days}d", "[stale]", stale)
    _append_status_block(lines, "FRESH", f"<{threshold_days}d", "[ok]   ", fresh)
    return "\n".join(lines)


def _fmt_age(seconds: Any) -> str:
    s = int(seconds)
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _append_status_block(
    lines: list[str],
    label: str,
    bound: str,
    marker: str,
    rows: list[Any],
) -> None:
    if not rows:
        return
    lines.append(f"  {label} ({bound}):")
    for r in rows:
        lines.append(f"    {marker} {r['name']:<40} {_fmt_age(r['age_seconds'])}")
    lines.append("")


@_tool_wrapper("find_untested_changes", category="fast")
async def find_untested_changes(days: int = 90, limit: int = 5) -> str:
    """Find packages with recent changelog activity but no recorded test coverage.

    Checks both openQA (local repo scan) and TestCatalog sources.
    Useful for identifying coverage gaps after upstream bumps.
    """
    rows = await db.find_untested_packages(days=days, limit=limit)
    _tlog(results=len(rows))
    if not rows:
        return (
            f"All packages with changelog entries in the last {days} days "
            "have at least one recorded test (openQA or TestCatalog)."
        )
    lines = [
        f"Packages with changes in the last {days}d but no test coverage"
        f" (openQA or TestCatalog) ({len(rows)}):"
    ]
    for r in rows:
        lines.append(
            f"\n  {r['name']}"
            f"  (latest change: {_format_date(r['latest_change'])},"
            f" {r['change_count']} entries)"
        )
    return "\n".join(lines)


async def _refresh_testcatalog_bugs(package: str, pkg_id: int | None, limit: int) -> None:
    """Query live TestCatalog bugs analytics, write results to DB, touch manifest.

    On live failure with cached rows present, sets the stale banner so the
    wrapper prepends a WARNING.
    """
    from ..testcatalog_client import TestCatalogClient

    client = TestCatalogClient()
    live_bugs: list = []
    api_ok = False
    try:
        live_bugs = await client.get_bugs_for_package(package, limit=limit)
        api_ok = True
    except Exception as exc:
        _tlog(testcatalog_bugs_live_failed=str(exc))
    finally:
        await client.close()

    if api_ok:
        if live_bugs:
            await db.upsert_testcatalog_bugs(live_bugs)
        new_id = pkg_id or await db.get_package_id(package)
        if new_id:
            await db.touch_manifest(new_id, kind="testcatalog_bugs")
    elif pkg_id is not None:
        synced_at: datetime | None = await db.get_synced_at(pkg_id, kind="testcatalog_bugs")
        if synced_at is not None:
            _mark_stale(synced_at)


@_tool_wrapper("find_bugs_in_tests", untrusted_sources=("testcatalog",), category="search")
async def find_bugs_in_tests(package: str, limit: int = 10) -> str:
    """Bugzilla bugs filed for *package* from the SUSE TestCatalog analytics API.

    Queries `/api/v1/analytics/search?scope=bugs`; results are cached in the
    `testcatalog_bugs` table with a 24h TTL. Returns up to *limit* bugs
    (clamped to 100, the API's max page size).
    """
    validate_package_name(package)

    pkg_id = await db.get_package_id(package)
    is_fresh = pkg_id is not None and await db.is_fresh(
        pkg_id, settings.cache_ttl_changelog_s, kind="testcatalog_bugs"
    )
    if not is_fresh:
        await _refresh_testcatalog_bugs(package, pkg_id, limit)

    rows = await db.get_testcatalog_bugs(package)
    _tlog(bugs=len(rows))
    if not rows:
        return (
            f"No Bugzilla bugs found for '{package}' in TestCatalog. "
            "The analytics index may not include this package, or the API is unreachable."
        )

    lines = [f"Bugs for {package} ({len(rows)} results):"]
    for r in rows[:limit]:
        status = (r["status"] or "?").upper()
        owner = r["assigned_to"] or "?"
        component = r["component"] or "?"
        severity = r["severity"] or "?"
        summary = (r["summary"] or "").strip()
        lines.append(f"  [{status:<10}] bsc#{r['bug_id']} -- {owner}")
        lines.append(f"    [{component}, {severity}] {summary}")
    return "\n".join(lines)


CLI_TOOLS = (
    get_news,
    get_test_coverage,
    find_bugs_in_tests,
    get_sync_status,
    find_untested_changes,
)


def register(mcp: FastMCP) -> None:
    for fn in CLI_TOOLS:
        mcp.tool()(fn)
