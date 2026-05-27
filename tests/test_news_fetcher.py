"""Unit tests for src/news_fetcher.py — mock aiohttp via make_client_session."""

from __future__ import annotations

from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from src.news_fetcher import (
    _classify_bodhi,
    _pkg_from_title,
    fetch_all_news,
    fetch_bodhi,
    fetch_opensuse_news,
)


def _resp_ctx(status: int = 200, text: str = "", json_body: Any = None) -> MagicMock:
    """Build the async-context-manager mock returned by session.get(url)."""
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.json = AsyncMock(return_value=json_body if json_body is not None else {})

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _session_ctx(get_return: MagicMock | None = None, get_side_effect: Exception | None = None) -> MagicMock:
    """Mock the `async with make_client_session() as session` context."""
    session = MagicMock()
    if get_side_effect is not None:
        session.get = MagicMock(side_effect=get_side_effect)
    else:
        session.get = MagicMock(return_value=get_return)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "update,expected",
    [
        ({"type": "security"}, "CRITICAL"),
        ({"critpath": True}, "CRITICAL"),
        ({"type": "bugfix"}, "Routine"),
    ],
    ids=["security", "critpath", "routine_bugfix"],
)
def test_classify_bodhi(update: dict, expected: str) -> None:
    assert _classify_bodhi(update) == expected


def test_pkg_from_title_with_dash() -> None:
    assert _pkg_from_title("vim-9.0.0") == "vim"


def test_pkg_from_title_no_dash() -> None:
    assert _pkg_from_title("vim") == "vim"


# ---------------------------------------------------------------------------
# fetch_bodhi
# ---------------------------------------------------------------------------
async def test_fetch_bodhi_returns_news_items() -> None:
    body = {
        "updates": [
            {"title": "vim-9.0", "type": "bugfix", "notes": "Fix crash", "url": "https://bodhi.fp.o/1"},
            {"title": "curl-8.0", "type": "security", "critpath": False, "notes": "CVE fix", "url": None},
        ]
    }
    ctx = _session_ctx(get_return=_resp_ctx(200, json_body=body))
    with patch("src.news_fetcher.make_client_session", return_value=ctx):
        result = await fetch_bodhi(limit=5)
    assert len(result) == 2
    assert result[0].source == "bodhi"
    assert result[0].package_name == "vim"
    assert result[1].importance == "CRITICAL"


async def test_fetch_bodhi_skips_empty_title() -> None:
    ctx = _session_ctx(get_return=_resp_ctx(200, json_body={"updates": [{"title": "", "type": "bugfix"}]}))
    with patch("src.news_fetcher.make_client_session", return_value=ctx):
        result = await fetch_bodhi()
    assert result == []


@pytest.mark.parametrize(
    "case",
    ["http_503", "network"],
    ids=["http_error", "network_error"],
)
async def test_fetch_bodhi_error_returns_empty(case: str) -> None:
    if case == "http_503":
        ctx = _session_ctx(get_return=_resp_ctx(503))
    else:
        ctx = _session_ctx(get_side_effect=aiohttp.ClientConnectionError("refused"))
    with patch("src.news_fetcher.make_client_session", return_value=ctx):
        result = await fetch_bodhi()
    assert result == []


# ---------------------------------------------------------------------------
# fetch_opensuse_news
# ---------------------------------------------------------------------------
_RSS = """\
<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Tumbleweed Snapshot 20240501</title>
  <link>https://news.opensuse.org/2024/05/01/snapshot</link>
  <description>Updates in this snapshot include kernel and mesa.</description>
</item>
<item>
  <title>Conference Recap</title>
  <link>https://news.opensuse.org/2024/04/30/conf</link>
  <description>Highlights from the openSUSE conference.</description>
</item>
</channel></rss>
"""


async def test_fetch_opensuse_news_returns_items() -> None:
    ctx = _session_ctx(get_return=_resp_ctx(200, text=_RSS))
    with patch("src.news_fetcher.make_client_session", return_value=ctx):
        result = await fetch_opensuse_news(limit=10)
    assert len(result) == 2
    assert result[0].source == "opensuse-rss"


async def test_fetch_opensuse_news_tumbleweed_critical() -> None:
    ctx = _session_ctx(get_return=_resp_ctx(200, text=_RSS))
    with patch("src.news_fetcher.make_client_session", return_value=ctx):
        result = await fetch_opensuse_news()
    tumbleweed = next(r for r in result if "Tumbleweed" in r.title)
    assert tumbleweed.importance == "CRITICAL"
    assert tumbleweed.package_name == "Tumbleweed"


@pytest.mark.parametrize(
    "case",
    ["http_404", "network"],
    ids=["http_error", "network_error"],
)
async def test_fetch_opensuse_news_error_returns_empty(case: str) -> None:
    if case == "http_404":
        ctx = _session_ctx(get_return=_resp_ctx(404))
    else:
        ctx = _session_ctx(get_side_effect=aiohttp.ClientConnectionError("refused"))
    with patch("src.news_fetcher.make_client_session", return_value=ctx):
        result = await fetch_opensuse_news()
    assert result == []


_XXE_RSS = """<?xml version="1.0"?>
<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<rss><channel>
<item>
  <title>Pwned &xxe;</title>
  <link>https://x</link>
  <description>boom</description>
</item>
</channel></rss>
"""


async def test_fetch_opensuse_news_rejects_xxe() -> None:
    """defusedxml must refuse to resolve external entities; result is empty."""
    ctx = _session_ctx(get_return=_resp_ctx(200, text=_XXE_RSS))
    with patch("src.news_fetcher.make_client_session", return_value=ctx):
        result = await fetch_opensuse_news()
    assert result == []


# ---------------------------------------------------------------------------
# fetch_all_news
# ---------------------------------------------------------------------------
async def test_fetch_all_news_concatenates_both_feeds() -> None:
    from datetime import datetime

    from src.models import NewsItem

    def _item(source: str) -> NewsItem:
        return NewsItem(
            title="t",
            source=source,
            item_type="bugfix",
            importance="Routine",
            content=None,
            url=None,
            date=datetime.now(UTC),
        )

    with (
        patch("src.news_fetcher.fetch_bodhi", new=AsyncMock(return_value=[_item("bodhi")])),
        patch("src.news_fetcher.fetch_opensuse_news", new=AsyncMock(return_value=[_item("opensuse-rss")])),
    ):
        result = await fetch_all_news(limit=5)

    assert len(result) == 2
    sources = {r.source for r in result}
    assert "bodhi" in sources
    assert "opensuse-rss" in sources
