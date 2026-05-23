"""Unit tests for src/news_fetcher.py — mock httpx.AsyncClient."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.news_fetcher import (
    _classify_bodhi,
    _pkg_from_title,
    fetch_bodhi,
    fetch_opensuse_news,
    fetch_all_news,
)


def _mock_client(status_code: int = 200, text: str = "", json_body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_body is not None:
        resp.json.return_value = json_body

    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_classify_bodhi_security() -> None:
    assert _classify_bodhi({"type": "security"}) == "CRITICAL"


def test_classify_bodhi_critpath() -> None:
    assert _classify_bodhi({"critpath": True}) == "CRITICAL"


def test_classify_bodhi_routine() -> None:
    assert _classify_bodhi({"type": "bugfix"}) == "Routine"


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
    mock = _mock_client(200, json_body=body)
    with patch("src.news_fetcher.httpx.AsyncClient", return_value=mock):
        result = await fetch_bodhi(limit=5)
    assert len(result) == 2
    assert result[0].source == "bodhi"
    assert result[0].package_name == "vim"
    assert result[1].importance == "CRITICAL"


async def test_fetch_bodhi_skips_empty_title() -> None:
    body = {"updates": [{"title": "", "type": "bugfix"}]}
    mock = _mock_client(200, json_body=body)
    with patch("src.news_fetcher.httpx.AsyncClient", return_value=mock):
        result = await fetch_bodhi()
    assert result == []


async def test_fetch_bodhi_http_error_returns_empty() -> None:
    mock = _mock_client(status_code=503)
    with patch("src.news_fetcher.httpx.AsyncClient", return_value=mock):
        result = await fetch_bodhi()
    assert result == []


async def test_fetch_bodhi_network_error_returns_empty() -> None:
    import httpx
    mock = _mock_client()
    mock.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("src.news_fetcher.httpx.AsyncClient", return_value=mock):
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
    mock = _mock_client(200, text=_RSS)
    with patch("src.news_fetcher.httpx.AsyncClient", return_value=mock):
        result = await fetch_opensuse_news(limit=10)
    assert len(result) == 2
    assert result[0].source == "opensuse-rss"


async def test_fetch_opensuse_news_tumbleweed_critical() -> None:
    mock = _mock_client(200, text=_RSS)
    with patch("src.news_fetcher.httpx.AsyncClient", return_value=mock):
        result = await fetch_opensuse_news()
    tumbleweed = next(r for r in result if "Tumbleweed" in r.title)
    assert tumbleweed.importance == "CRITICAL"
    assert tumbleweed.package_name == "Tumbleweed"


async def test_fetch_opensuse_news_http_error_returns_empty() -> None:
    mock = _mock_client(status_code=404)
    with patch("src.news_fetcher.httpx.AsyncClient", return_value=mock):
        result = await fetch_opensuse_news()
    assert result == []


async def test_fetch_opensuse_news_network_error_returns_empty() -> None:
    import httpx
    mock = _mock_client()
    mock.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("src.news_fetcher.httpx.AsyncClient", return_value=mock):
        result = await fetch_opensuse_news()
    assert result == []


# ---------------------------------------------------------------------------
# fetch_all_news
# ---------------------------------------------------------------------------
async def test_fetch_all_news_concatenates_both_feeds() -> None:
    from src.models import NewsItem
    from datetime import datetime, timezone

    def _item(source: str) -> NewsItem:
        return NewsItem(title="t", source=source, item_type="bugfix", importance="Routine", content=None, url=None, date=datetime.now(timezone.utc))

    with (
        patch("src.news_fetcher.fetch_bodhi", new=AsyncMock(return_value=[_item("bodhi")])),
        patch("src.news_fetcher.fetch_opensuse_news", new=AsyncMock(return_value=[_item("opensuse-rss")])),
    ):
        result = await fetch_all_news(limit=5)

    assert len(result) == 2
    sources = {r.source for r in result}
    assert "bodhi" in sources
    assert "opensuse-rss" in sources
