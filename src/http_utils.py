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

# Defense-in-depth caps for any HTTP fetch the project makes.
# Why MAX_REDIRECTS: a compromised upstream mirror can 302-loop into an
# internal host (SSRF-lite); aiohttp's default of 10 is too generous given
# we only validate the *initial* URL via safe_upstream_url. Three hops
# covers legitimate canonicalisation (http->https, www, trailing-slash).
# Why MAX_RESPONSE_BYTES: resp.text()/json() buffer the whole body; a
# malicious or broken upstream returning GBs would OOM the process. 10MB
# is well above the largest legitimate changelog/spec we have observed.
MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 10 * 1024 * 1024

_SHARED_SESSION: aiohttp.ClientSession | None = None


class ResponseTooLargeError(aiohttp.ClientError):
    """HTTP response body exceeded the configured cap.

    Inherits from ``aiohttp.ClientError`` so existing
    ``except aiohttp.ClientError`` handlers (news_fetcher, news.py)
    fall back to cache instead of crashing.
    """


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


async def read_bounded(
    resp: aiohttp.ClientResponse,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> bytes:
    """Read *resp*'s body, raising ResponseTooLargeError if it exceeds *max_bytes*.

    Honest upstreams that advertise Content-Length are rejected up front;
    streaming reads abort as soon as the running total crosses the cap so a
    lying Content-Length cannot trick us into buffering an unbounded body.
    """
    cl = resp.content_length
    if cl is not None and cl > max_bytes:
        raise ResponseTooLargeError(f"response too large: declared {cl} > {max_bytes} bytes")
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError(f"response too large: streamed > {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def read_bounded_text(
    resp: aiohttp.ClientResponse,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> str:
    """Read *resp* as text, capped at *max_bytes*. Uses the response's
    declared charset, falling back to UTF-8 with replacement on decode error.
    """
    raw = await read_bounded(resp, max_bytes)
    encoding = resp.charset or "utf-8"
    return raw.decode(encoding, errors="replace")
