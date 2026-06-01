"""Shared HTTP plumbing for network-backed sources (changelog + spec)."""

from __future__ import annotations

import json
from typing import Any, ClassVar

import aiohttp
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import settings
from ..http_utils import MAX_REDIRECTS, get_shared_session, read_bounded_text
from .base import ChangelogSource, SourceError, SourceNotFound


class HttpClient:
    """Reusable HTTP plumbing -- not a Source on its own.

    Both ``HttpSource`` (changelog ABC) and the spec-source classes mix this
    in so they share session lifecycle, retry policy, and error taxonomy
    without one inheriting the other's abstract ``fetch`` contract.

    All instances share the process-wide aiohttp session from
    ``http_utils.get_shared_session``; per-instance auth / forge headers are
    merged into each request, not baked into the session.
    """

    # Subclasses can map specific 4xx statuses to custom error messages
    # (e.g. GitHubSource: 403 -> "GitHub API rate limit exceeded").
    _STATUS_ERROR_MESSAGES: ClassVar[dict[int, str]] = {}

    def __init__(
        self,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._extra_headers = extra_headers or None

    async def close(self) -> None:
        """No-op: the shared session is owned by ``http_utils``.

        Kept so ``SourceRegistry.close()`` can fan out uniformly without
        special-casing legacy per-instance sessions.
        """
        return None

    async def _get_session(self) -> aiohttp.ClientSession:
        return get_shared_session()

    async def _fetch_text(self, url: str) -> str:
        """GET *url* with retries. SourceNotFound on 404, SourceError on 4xx/5xx."""
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((aiohttp.ServerConnectionError, aiohttp.ClientConnectionError)),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            stop=stop_after_attempt(settings.obs_max_retries),
            reraise=True,
        ):
            with attempt:
                session = await self._get_session()
                async with session.get(
                    url,
                    headers=self._extra_headers,
                    max_redirects=MAX_REDIRECTS,
                ) as resp:
                    if resp.status == 404:
                        raise SourceNotFound(url)
                    if 400 <= resp.status < 500:
                        msg = self._STATUS_ERROR_MESSAGES.get(resp.status, f"HTTP {resp.status} for {url}")
                        raise SourceError(msg)
                    if 500 <= resp.status < 600:
                        # Retried by tenacity until exhausted.
                        raise aiohttp.ClientConnectionError(f"HTTP {resp.status} for {url}")
                    if resp.status != 200:
                        raise SourceError(f"HTTP {resp.status} for {url}")
                    return await read_bounded_text(resp)

        raise SourceError(f"All retries exhausted for {url}")

    async def _fetch_json(self, url: str) -> Any:
        """GET *url* and parse the response body as JSON."""
        return json.loads(await self._fetch_text(url))


# MRO order matters: HttpClient first so its __init__ is picked up when
# subclasses don't define their own (e.g. `RpmSource(HttpSource)`).
class HttpSource(HttpClient, ChangelogSource):
    """Changelog source that fetches over HTTP. Combines the ChangelogSource
    ABC contract with the shared HttpClient plumbing.
    """
