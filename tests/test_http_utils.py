"""Unit tests for src/http_utils.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import aiohttp

from src.http_utils import make_client_session, refresh_session


# ---------------------------------------------------------------------------
# make_client_session
# ---------------------------------------------------------------------------
async def test_make_client_session_returns_open_session() -> None:
    session = make_client_session()
    assert isinstance(session, aiohttp.ClientSession)
    assert not session.closed
    await session.close()


# ---------------------------------------------------------------------------
# refresh_session — None input
# ---------------------------------------------------------------------------
async def test_refresh_session_none_creates_new() -> None:
    session = await refresh_session(None)
    assert isinstance(session, aiohttp.ClientSession)
    assert not session.closed
    await session.close()


# ---------------------------------------------------------------------------
# refresh_session — closed session input
# ---------------------------------------------------------------------------
async def test_refresh_session_closed_creates_new() -> None:
    old = make_client_session()
    await old.close()
    assert old.closed

    new = await refresh_session(old)
    assert not new.closed
    assert new is not old
    await new.close()


# ---------------------------------------------------------------------------
# refresh_session — valid open session is returned as-is
# ---------------------------------------------------------------------------
async def test_refresh_session_valid_returns_same() -> None:
    session = make_client_session()
    returned = await refresh_session(session)
    assert returned is session
    await session.close()


# ---------------------------------------------------------------------------
# refresh_session — session bound to a different loop is replaced
# ---------------------------------------------------------------------------
async def test_refresh_session_stale_loop_creates_new() -> None:

    stale = MagicMock(spec=aiohttp.ClientSession)
    stale.closed = False
    # Simulate a loop mismatch by pointing _loop to a different object
    stale._loop = object()

    new = await refresh_session(stale)  # type: ignore[arg-type]
    # Should not return the stale mock since loop differs
    assert new is not stale
    await new.close()
