"""Shared helpers reused by multiple tool modules.

User-facing error templates, package readiness probe, formatters for
match/listing tables, and the `_Readiness` enum returned by
`_ensure_or_queue` (DD10 fast-fail path).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from ..config import settings
from ..ingest import IngestStatus
from ..models import ChangelogEntry
from ..runtime import db, ingest_service
from ..version_utils import BSC_RE, CVE_RE, clean_version, parse_when
from ._wrap import _mark_stale

# ---------------------------------------------------------------------------
# User-facing message templates
# ---------------------------------------------------------------------------
MSG_PKG_NOT_FOUND = "Package '{}' not found in any source (local RPM, OBS, src.opensuse.org)."
MSG_PKG_NOT_FOUND_SHORT = "Package '{}' not found in any source."
MSG_PKG_QUEUED = (
    "Package '{}' is not yet indexed; ingestion has been queued. Retry this call in a few seconds."
)
MSG_INVALID_CVE = "Invalid CVE ID '{}'. Expected CVE-YYYY-NNNN(NNN)."
MSG_INVALID_BUG = "Invalid bug ID '{}'. Expected bsc#NNNNNN, boo#NNNNNN, or bnc#NNNNNN."
MSG_SINCE_UNPARSEABLE = "Could not parse 'since' value: {!r}."
MSG_UNTIL_UNPARSEABLE = "Could not parse 'until' value: {!r}."
MSG_UNKNOWN_SPEC_SOURCE = "Unknown source {!r}. Use 'opensuse' or 'fedora'."

# Scan freely inside changelog bodies (unlike CVE_RE / BSC_RE which anchor a full ID).
CVE_CONTENT_RE = re.compile(r"CVE-\d{4}-\d+", re.IGNORECASE)
BSC_CONTENT_RE = re.compile(r"\b(?:bsc|boo|bnc)#\d+\b", re.IGNORECASE)

type ReleaseGroup = tuple[str, datetime, list[ChangelogEntry]]
type SqlRow = Mapping[str, Any]


# ---------------------------------------------------------------------------
# Pure formatters
# ---------------------------------------------------------------------------
def _format_date(dt: datetime | None) -> str:
    return dt.date().isoformat() if dt else "?"


def _validate_with_regex(
    value: str, pattern: re.Pattern[str], err_template: str, normalize: Callable[[str], str]
) -> str:
    if not pattern.match(value):
        raise ValueError(err_template.format(value))
    return normalize(value)


def _validate_cve_id(cve_id: str) -> str:
    return _validate_with_regex(cve_id, CVE_RE, MSG_INVALID_CVE, str.upper)


def _validate_bug_id(bug_id: str) -> str:
    return _validate_with_regex(bug_id, BSC_RE, MSG_INVALID_BUG, str.lower)


def parse_when_or_msg(value: str | None, *, kind: str = "since") -> tuple[datetime | None, str | None]:
    """Parse a since/until string. Returns ``(datetime, None)`` on success,
    ``(None, error_message)`` on failure, or ``(None, None)`` when ``value`` is empty.
    """
    if not value:
        return None, None
    dt = parse_when(value)
    if dt is None:
        template = MSG_UNTIL_UNPARSEABLE if kind == "until" else MSG_SINCE_UNPARSEABLE
        return None, template.format(value)
    return dt, None


_EPOCH_MIN = datetime.min.replace(tzinfo=UTC)


def _records_to_entries(rows: list[SqlRow]) -> list[ChangelogEntry]:
    # Why: DB returns tz-aware timestamps; downstream comparisons against
    # datetime.now(UTC) raise TypeError on mixed aware/naive datetimes.
    return [
        ChangelogEntry(
            version=r["version"],
            author=r["author"] or "",
            date=r["entry_date"] or _EPOCH_MIN,
            content=r["content"],
        )
        for r in rows
    ]


def _format_match_rows(rows: list[SqlRow], header: str) -> str:
    """find_cve / find_bug: package version (date) + content body."""
    lines = [header]
    for r in rows:
        lines.append(
            f"\n--- {r['package']} {r['version']} ({_format_date(r['entry_date'])}) ---\n{r['content']}"
        )
    return "\n".join(lines)


def _format_listing_rows(rows: list[SqlRow], header: str, pattern: re.Pattern[str]) -> str:
    """list_cves / list_bugs: version (date) — extracted IDs + 400-char preview."""
    lines = [header]
    for r in rows:
        ids = ", ".join(sorted(set(pattern.findall(r["content"]))))
        lines.append(
            f"\n--- {r['version']} ({_format_date(r['entry_date'])}) — {ids} ---\n"
            f"{r['content'].strip()[:400]}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Readiness probe (DD10 fast-fail)
# ---------------------------------------------------------------------------
class _Readiness(Enum):
    READY = "ready"  # caller may read cached rows
    QUEUED = "queued"  # never-indexed package — background ingest dispatched


async def _ensure_or_queue(package: str, refresh: bool = False) -> _Readiness:
    """Two-mode readiness probe (DD10):

    * **Never indexed** -- schedule a background ingest, return ``QUEUED``
      immediately so the caller can return a "queued, retry shortly" message
      without blocking the client.
    * **Already in cache** -- return ``READY`` if fresh; otherwise block on
      a synchronous refresh. When the refresh degrades to stale-cache
      (source failed), mark the call via ``_mark_stale`` so the tool wrapper
      prepends a WARNING banner.

    Callers always get ``READY`` or ``QUEUED`` -- the blocking path is opt-in
    for stale data and never visible at the call site.
    """
    pkg_id = await db.get_package_id(package)
    if pkg_id is None:
        ingest_service.schedule(package)
        return _Readiness.QUEUED
    if not refresh and await db.is_fresh(pkg_id, settings.cache_ttl_changelog_s, kind="changelog"):
        return _Readiness.READY
    res = await ingest_service.ingest(package)
    if res.status is IngestStatus.STALE:
        _mark_stale(res.synced_at)
    return _Readiness.READY


async def queued_msg_or_none(package: str, refresh: bool = False) -> str | None:
    """Return ``MSG_PKG_QUEUED`` when the package is not yet indexed, else ``None``.

    Tools use it as an early-return guard:
    ``if (msg := await queued_msg_or_none(pkg)) is not None: return msg``.
    """
    if await _ensure_or_queue(package, refresh) is _Readiness.QUEUED:
        return MSG_PKG_QUEUED.format(package)
    return None


async def _load_entries(package: str) -> list[ChangelogEntry]:
    pkg_id = await db.get_package_id(package)
    if pkg_id is None:
        return []
    rows = await db.fetch_entries(pkg_id, limit=settings.cache_max_entries)
    return _records_to_entries(rows)


async def _ensure_and_load_entries(
    package: str, refresh: bool = False
) -> list[ChangelogEntry] | Literal[_Readiness.QUEUED] | None:
    """Returns entries, ``QUEUED`` for never-indexed packages, or ``None``
    when the package row exists but holds zero entries.
    """
    state = await _ensure_or_queue(package, refresh)
    if state is _Readiness.QUEUED:
        return state
    entries = await _load_entries(package)
    return entries or None


async def fetch_recent_releases(
    package: str, n: int, refresh: bool
) -> list[ReleaseGroup] | Literal[_Readiness.QUEUED] | None:
    """Group cached entries by cleaned version; return the latest *n* groups.

    Shared between ``changelog.get_recent_releases`` and ``deps.get_dependency_changes``
    -- the deps tool fans this out per-dependency for the BFS aggregation.
    """
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
