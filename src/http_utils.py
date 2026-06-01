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

# Tuned for the ~100-concurrent-user target: total cap of 100 sockets,
# at most 10 to any single upstream (OBS/Gitea/GitHub/...). Per-source
# auth lives in per-request headers, not session state, so one shared
# session safely serves every source.
_TCP_LIMIT_TOTAL = 100
_TCP_LIMIT_PER_HOST = 10

_SHARED_SESSION: aiohttp.ClientSession | None = None


def _create_session() -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(
        limit=_TCP_LIMIT_TOTAL,
        limit_per_host=_TCP_LIMIT_PER_HOST,
    )
    return aiohttp.ClientSession(
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        connector=connector,
    )


def get_shared_session() -> aiohttp.ClientSession:
    """Return the process-wide aiohttp session.

    Lazy-initialised on first call so the connector binds to whatever event
    loop is running at the time. Auto-recreated if the previous session was
    closed or is bound to a different loop (tests restart the loop between
    cases). Must be torn down via ``close_shared_session`` during shutdown.

    Auth/User-Agent diversity is handled by passing ``headers=`` to each
    ``session.get(...)`` call instead of baking them into the session.
    """
    global _SHARED_SESSION
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    sess = _SHARED_SESSION
    stale = (
        sess is None
        or sess.closed
        or (loop is not None and sess._loop is not loop)
    )
    if stale:
        _SHARED_SESSION = _create_session()
    assert _SHARED_SESSION is not None
    return _SHARED_SESSION


async def close_shared_session() -> None:
    """Close the process-wide aiohttp session. Idempotent."""
    global _SHARED_SESSION
    sess = _SHARED_SESSION
    _SHARED_SESSION = None
    if sess is not None and not sess.closed:
        await sess.close()
