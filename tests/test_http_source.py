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
    """Return an async-context-manager mock for session.get(url).

    Sets the fields ``read_bounded_text`` looks at: ``content_length=None``
    skips the up-front size check, and ``content.iter_chunked`` yields the
    body in one chunk.
    """
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.charset = "utf-8"
    resp.content_length = None
    body_bytes = text.encode("utf-8")

    async def _iter(_chunk_size: int) -> object:
        yield body_bytes

    resp.content = MagicMock()
    resp.content.iter_chunked = _iter
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
# close() — no-op now that sessions are shared via http_utils
# ---------------------------------------------------------------------------
async def test_close_is_noop() -> None:
    """HttpClient.close() no longer owns a session; the shared one is closed
    centrally during shutdown via http_utils.close_shared_session.
    """
    src = _ConcreteSource()
    await src.close()  # no exception, no session touched
    await src.close()  # idempotent


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
