"""Shared aiohttp session helpers used by network ``Source`` implementations."""

from __future__ import annotations

import asyncio

import aiohttp

from .config import settings

HTTP_TIMEOUT = aiohttp.ClientTimeout(
    total=settings.obs_timeout_total,
    connect=settings.obs_timeout_connect,
)


def make_client_session(*, headers: dict[str, str] | None = None) -> aiohttp.ClientSession:
    """Return a new aiohttp.ClientSession with the project-wide timeout."""
    return aiohttp.ClientSession(timeout=HTTP_TIMEOUT, headers=headers)


async def refresh_session(
    session: aiohttp.ClientSession | None,
    *,
    headers: dict[str, str] | None = None,
) -> aiohttp.ClientSession:
    """Return *session* if still usable, otherwise close it and make a fresh one.

    Handles three failure modes: ``None``, already-closed, or bound to a
    different event loop (e.g. after a loop restart in tests).
    """
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    stale = session is None or session.closed or (loop is not None and session._loop is not loop)
    if not stale:
        assert session is not None
        return session

    if session is not None and not session.closed:
        await session.close()
    return make_client_session(headers=headers)
