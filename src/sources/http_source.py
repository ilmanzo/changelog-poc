"""Shared HTTP plumbing for network-backed changelog sources."""

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
from ..http_utils import refresh_session
from .base import ChangelogSource, SourceError, SourceNotFound


class HttpSource(ChangelogSource):
    """Base class for HTTP-backed sources.

    - Session lifecycle via ``refresh_session``
    - Exponential-backoff retries on transient (5xx, connection) errors
    - HTTP 404 -> SourceNotFound; 4xx (auth/rate-limit) -> SourceError (no retry);
      5xx -> retried then SourceError
    """

    # Subclasses can map specific 4xx statuses to custom error messages
    # (e.g. GitHubSource: 403 -> "GitHub API rate limit exceeded").
    _STATUS_ERROR_MESSAGES: ClassVar[dict[int, str]] = {}

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._session = session
        self._extra_headers = extra_headers or None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        self._session = await refresh_session(self._session, headers=self._extra_headers)
        return self._session

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
                async with session.get(url) as resp:
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
                    return await resp.text()

        raise SourceError(f"All retries exhausted for {url}")

    async def _fetch_json(self, url: str) -> Any:
        """GET *url* and parse the response body as JSON."""
        return json.loads(await self._fetch_text(url))
