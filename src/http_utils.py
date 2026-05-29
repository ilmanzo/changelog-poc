"""Shared aiohttp session helpers used by network ``Source`` implementations."""

from __future__ import annotations

import asyncio
from importlib.metadata import PackageNotFoundError, version

import aiohttp

from .config import settings

HTTP_TIMEOUT = aiohttp.ClientTimeout(
    total=settings.obs_timeout_total,
    connect=settings.obs_timeout_connect,
)

try:
    _PKG_VERSION = version("rpm-mcp")
except PackageNotFoundError:
    _PKG_VERSION = "dev"

# Why: anonymous requests are subject to per-host UA quotas (GitHub, Pagure,
# OBS); identifiable traffic also lets upstream operators reach out before
# blocking. Caller-provided headers always win.
USER_AGENT = f"rpm-mcp/{_PKG_VERSION} (+https://github.com/ilmanzo/changelog-poc)"


def make_client_session(*, headers: dict[str, str] | None = None) -> aiohttp.ClientSession:
    """Return a new aiohttp.ClientSession with the project-wide timeout."""
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    return aiohttp.ClientSession(timeout=HTTP_TIMEOUT, headers=merged)


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
