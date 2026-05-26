"""News ingestion for Fedora Bodhi + openSUSE RSS.

Returns NewsItem records suitable for ``Database.upsert_news``. No LLM call
inside the fetcher — classification is heuristic; LLM summarisation lives in
its own tool layer if/when needed.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import structlog

from .config import settings
from .models import NewsItem
from .sanitize import scrub_external

_logger = structlog.get_logger("rpm-mcp.news")

BODHI_URL = "https://bodhi.fedoraproject.org/updates"
OPENSUSE_NEWS_URL = "https://news.opensuse.org/feed.xml"

_TIMEOUT = httpx.Timeout(settings.obs_timeout_total, connect=settings.obs_timeout_connect)


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
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                _logger.warning("bodhi_http", status=resp.status_code)
                return items
            for u in resp.json().get("updates", []):
                title = scrub_external(u.get("title") or "")
                if not title:
                    continue
                items.append(NewsItem(
                    title=title,
                    source="bodhi",
                    item_type=u.get("type"),
                    importance=_classify_bodhi(u),
                    content=scrub_external(u.get("notes") or "") or None,
                    url=u.get("url"),
                    date=datetime.now(UTC),
                    package_name=_pkg_from_title(title),
                ))
    except Exception as e:
        _logger.warning("bodhi_error", error=str(e))
    return items


_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.DOTALL)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_LINK_RE = re.compile(r"<link>(.*?)</link>", re.DOTALL)
_DESC_RE = re.compile(r"<description>(.*?)</description>", re.DOTALL)


async def fetch_opensuse_news(limit: int = 20) -> list[NewsItem]:
    items: list[NewsItem] = []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(OPENSUSE_NEWS_URL)
            if resp.status_code != 200:
                _logger.warning("opensuse_news_http", status=resp.status_code)
                return items
            for raw in _ITEM_RE.findall(resp.text)[:limit]:
                title_m = _TITLE_RE.search(raw)
                link_m = _LINK_RE.search(raw)
                desc_m = _DESC_RE.search(raw)
                if not title_m:
                    continue
                title = scrub_external(title_m.group(1).strip())
                pkg = "Tumbleweed" if ("Tumbleweed" in title or "Snapshot" in title) else None
                importance = "CRITICAL" if pkg == "Tumbleweed" else "Routine"
                items.append(NewsItem(
                    title=title,
                    source="opensuse-rss",
                    item_type="snapshot" if pkg else "news",
                    importance=importance,
                    content=scrub_external(desc_m.group(1).strip()) if desc_m else None,
                    url=link_m.group(1).strip() if link_m else None,
                    date=datetime.now(UTC),
                    package_name=pkg,
                ))
    except Exception as e:
        _logger.warning("opensuse_news_error", error=str(e))
    return items


async def fetch_all_news(limit: int = 20) -> list[NewsItem]:
    """Convenience: fetch both feeds and concatenate."""
    bodhi = await fetch_bodhi(limit)
    opensuse = await fetch_opensuse_news(limit)
    return [*opensuse, *bodhi]
