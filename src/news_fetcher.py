"""News ingestion for Fedora Bodhi + openSUSE RSS.

Returns NewsItem records suitable for ``Database.upsert_news``. No LLM call
inside the fetcher — classification is heuristic; LLM summarisation lives in
its own tool layer if/when needed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import structlog
from defusedxml import ElementTree as DefusedET

from .http_utils import MAX_REDIRECTS, get_shared_session, read_bounded_text
from .models import NewsItem
from .sanitize import scrub_external

_logger = structlog.get_logger("rpm-mcp.news")

BODHI_URL = "https://bodhi.fedoraproject.org/updates"
OPENSUSE_NEWS_URL = "https://news.opensuse.org/feed.xml"


def _classify_bodhi(u: dict) -> str:
    if u.get("critpath") or u.get("type") == "security":
        return "CRITICAL"
    return "Routine"


def _pkg_from_title(title: str) -> str:
    return title.split("-", 1)[0] if "-" in title else title


async def fetch_bodhi(limit: int = 20) -> list[NewsItem]:
    items: list[NewsItem] = []
    url = f"{BODHI_URL}/?rows_per_page={limit}&status=testing"
    try:
        session = get_shared_session()
        async with session.get(url, max_redirects=MAX_REDIRECTS) as resp:
            if resp.status != 200:
                _logger.warning("bodhi_http", status=resp.status)
                return items
            data = json.loads(await read_bounded_text(resp))
        for u in data.get("updates", []):
            raw_title = u.get("title") or ""
            pkg = _pkg_from_title(raw_title) if raw_title else None
            title = scrub_external(raw_title, source="bodhi", package=pkg)
            if not title:
                continue
            items.append(
                NewsItem(
                    title=title,
                    source="bodhi",
                    item_type=u.get("type"),
                    importance=_classify_bodhi(u),
                    content=scrub_external(u.get("notes") or "", source="bodhi", package=pkg) or None,
                    url=u.get("url"),
                    date=datetime.now(UTC),
                    package_name=pkg,
                )
            )
    except Exception as e:
        _logger.warning("bodhi_error", error=str(e))
    return items


def _child_text(item: object, tag: str) -> str | None:
    """Return the stripped text of *item*'s first child named *tag*, or None.

    Tolerates namespaces by matching on local name suffix; RSS 2.0 uses bare
    element names but openSUSE's feed may carry atom: prefixes.
    """
    for child in list(item):  # type: ignore[call-overload]
        local = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
        if local == tag and child.text:
            return child.text.strip()
    return None


async def fetch_opensuse_news(limit: int = 20) -> list[NewsItem]:
    items: list[NewsItem] = []
    try:
        session = get_shared_session()
        async with session.get(OPENSUSE_NEWS_URL, max_redirects=MAX_REDIRECTS) as resp:
            if resp.status != 200:
                _logger.warning("opensuse_news_http", status=resp.status)
                return items
            text = await read_bounded_text(resp)
        root = DefusedET.fromstring(text)
        for entry in root.iter("item"):
            if len(items) >= limit:
                break
            title_raw = _child_text(entry, "title")
            if not title_raw:
                continue
            pkg = "Tumbleweed" if ("Tumbleweed" in title_raw or "Snapshot" in title_raw) else None
            title = scrub_external(title_raw, source="opensuse-rss", package=pkg)
            desc_raw = _child_text(entry, "description")
            link_raw = _child_text(entry, "link")
            importance = "CRITICAL" if pkg == "Tumbleweed" else "Routine"
            items.append(
                NewsItem(
                    title=title,
                    source="opensuse-rss",
                    item_type="snapshot" if pkg else "news",
                    importance=importance,
                    content=scrub_external(desc_raw, source="opensuse-rss", package=pkg)
                    if desc_raw
                    else None,
                    url=link_raw,
                    date=datetime.now(UTC),
                    package_name=pkg,
                )
            )
    except Exception as e:
        _logger.warning("opensuse_news_error", error=str(e))
    return items


async def fetch_all_news(limit: int = 20) -> list[NewsItem]:
    """Convenience: fetch both feeds and concatenate."""
    bodhi = await fetch_bodhi(limit)
    opensuse = await fetch_opensuse_news(limit)
    return [*opensuse, *bodhi]
