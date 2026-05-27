"""Unit tests for src/sources/http_source.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from src.sources.base import FetchResult, SourceError, SourceNotFound
from src.sources.http_source import HttpSource


class _ConcreteSource(HttpSource):
    """Minimal concrete subclass so we can instantiate HttpSource."""
    name = "test"

    async def fetch(self, package: str) -> FetchResult:
        raise NotImplementedError


def _resp_ctx(status: int, text: str = "") -> MagicMock:
    """Return an async-context-manager mock for session.get(url)."""
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.request_info = MagicMock()
    resp.history = []

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _mock_session(status: int, text: str = "") -> MagicMock:
    session = MagicMock()
    session.get = MagicMock(return_value=_resp_ctx(status, text))
    return session


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------
async def test_close_open_session_calls_close() -> None:
    src = _ConcreteSource()
    mock_session = AsyncMock()
    mock_session.closed = False
    src._session = mock_session

    await src.close()
    mock_session.close.assert_awaited_once()


async def test_close_none_session_is_noop() -> None:
    src = _ConcreteSource()
    src._session = None
    await src.close()  # no exception


async def test_close_already_closed_session_is_noop() -> None:
    src = _ConcreteSource()
    mock_session = MagicMock()
    mock_session.closed = True
    src._session = mock_session
    await src.close()
    mock_session.close.assert_not_called()


# ---------------------------------------------------------------------------
# _fetch_text — 200 success
# ---------------------------------------------------------------------------
async def test_fetch_text_200_returns_body() -> None:
    src = _ConcreteSource()
    with patch.object(src, "_get_session", AsyncMock(return_value=_mock_session(200, "spec content"))):
        result = await src._fetch_text("https://example.com/pkg.spec")
    assert result == "spec content"


# ---------------------------------------------------------------------------
# _fetch_text — 404 raises SourceNotFound immediately (no retry)
# ---------------------------------------------------------------------------
async def test_fetch_text_404_raises_source_not_found() -> None:
    src = _ConcreteSource()
    with patch.object(src, "_get_session", AsyncMock(return_value=_mock_session(404))):
        with pytest.raises(SourceNotFound):
            await src._fetch_text("https://example.com/missing.spec")


# ---------------------------------------------------------------------------
# _fetch_text — 5xx exhausts retries and raises
# ---------------------------------------------------------------------------
async def test_fetch_text_5xx_raises_after_retries(monkeypatch) -> None:
    monkeypatch.setattr("src.sources.http_source.settings.obs_max_retries", 1)
    src = _ConcreteSource()
    with patch.object(src, "_get_session", AsyncMock(return_value=_mock_session(503))):
        with pytest.raises((aiohttp.ClientResponseError, SourceError, Exception)):
            await src._fetch_text("https://example.com/broken.spec")
