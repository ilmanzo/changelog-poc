"""News / openQA / sync-status tools."""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from src.ingest import validate_package_name
from src.runtime import db
from src.tools._helpers import _format_date
from src.tools._wrap import _tlog, _tool_wrapper


@_tool_wrapper("get_news", untrusted_sources=("bodhi", "opensuse-rss"))
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


@_tool_wrapper("get_openqa_tests", untrusted_sources=("openqa",))
async def get_openqa_tests(package: str) -> str:
    """List openQA tests that exercise *package*."""
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
        summary = f" -- {r['summary']}" if r["summary"] else ""
        lines.append(f"  {r['test_path']}{summary}")
    return "\n".join(lines)


@_tool_wrapper("get_sync_status")
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


CLI_TOOLS = (
    get_news,
    get_openqa_tests,
    get_sync_status,
)


def register(mcp: FastMCP) -> None:
    for fn in CLI_TOOLS:
        mcp.tool()(fn)
