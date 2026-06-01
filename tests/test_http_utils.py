"""Unit tests for src/http_utils.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import aiohttp
import pytest

from src import http_utils
from src.http_utils import (
    MAX_RESPONSE_BYTES,
    ResponseTooLargeError,
    close_shared_session,
    get_shared_session,
    read_bounded,
    read_bounded_text,
)


async def test_get_shared_session_returns_open_session() -> None:
    await close_shared_session()
    session = get_shared_session()
    try:
        assert isinstance(session, aiohttp.ClientSession)
        assert not session.closed
    finally:
        await close_shared_session()


async def test_get_shared_session_is_idempotent_within_loop() -> None:
    await close_shared_session()
    first = get_shared_session()
    second = get_shared_session()
    try:
        assert first is second
    finally:
        await close_shared_session()


async def test_get_shared_session_recreated_after_close() -> None:
    await close_shared_session()
    first = get_shared_session()
    await close_shared_session()
    assert first.closed

    second = get_shared_session()
    try:
        assert second is not first
        assert not second.closed
    finally:
        await close_shared_session()


async def test_close_shared_session_is_idempotent() -> None:
    await close_shared_session()
    get_shared_session()
    await close_shared_session()
    await close_shared_session()  # second call must not error
    assert http_utils._SHARED_SESSION is None


# ---------------------------------------------------------------------------
# read_bounded / read_bounded_text
# ---------------------------------------------------------------------------
def _mock_response(body: bytes, *, charset: str = "utf-8", content_length: int | None = None) -> MagicMock:
    resp = MagicMock()
    resp.charset = charset
    resp.content_length = content_length

    async def _iter(_chunk_size: int) -> object:
        yield body

    resp.content = MagicMock()
    resp.content.iter_chunked = _iter
    return resp


async def test_read_bounded_returns_full_body_under_cap() -> None:
    resp = _mock_response(b"hello world")
    assert await read_bounded(resp, max_bytes=1024) == b"hello world"


async def test_read_bounded_raises_when_declared_size_exceeds_cap() -> None:
    resp = _mock_response(b"x", content_length=99_999)
    with pytest.raises(ResponseTooLargeError):
        await read_bounded(resp, max_bytes=1024)


async def test_read_bounded_raises_when_streamed_size_exceeds_cap() -> None:
    # Lying Content-Length: declares 1 byte, streams 2000.
    resp = _mock_response(b"x" * 2000, content_length=1)
    # Declared-size check comes first; since 1 < 1024 it would pass, so we
    # need a case where declared is honest-or-None but streamed exceeds.
    resp.content_length = None

    async def _iter(_chunk_size: int) -> object:
        yield b"x" * 600
        yield b"y" * 600

    resp.content.iter_chunked = _iter
    with pytest.raises(ResponseTooLargeError):
        await read_bounded(resp, max_bytes=1024)


async def test_read_bounded_text_decodes_with_charset() -> None:
    resp = _mock_response("café".encode("latin-1"), charset="latin-1")
    assert await read_bounded_text(resp, max_bytes=1024) == "café"


async def test_response_too_large_is_aiohttp_client_error() -> None:
    """Subclass relationship lets news_fetcher's `except aiohttp.ClientError`
    catch it and fall back to cache instead of crashing.
    """
    assert issubclass(ResponseTooLargeError, aiohttp.ClientError)


async def test_max_response_bytes_is_10_mib() -> None:
    assert MAX_RESPONSE_BYTES == 10 * 1024 * 1024
