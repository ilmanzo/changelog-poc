"""Unit tests for src/http_utils.py."""
from __future__ import annotations

import aiohttp

from src import http_utils
from src.http_utils import close_shared_session, get_shared_session


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
