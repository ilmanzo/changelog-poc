"""Shared HTTP plumbing for network-backed changelog sources."""
from __future__ import annotations

import aiohttp
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .base import ChangelogSource, SourceError, SourceNotFound
from ..config import settings
from ..http_utils import refresh_session


class HttpSource(ChangelogSource):
    """Base class for HTTP-backed sources.

    - Session lifecycle via ``refresh_session``
    - Exponential-backoff retries on transient errors
    - HTTP 404 → SourceNotFound, 5xx → SourceError
    """

    is_local = False

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        self._session = await refresh_session(self._session)
        return self._session

    async def _fetch_text(self, url: str) -> str:
        """GET *url* with retries. SourceNotFound on 404, SourceError on 5xx."""
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(
                (aiohttp.ClientError, aiohttp.ServerConnectionError)
            ),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            stop=stop_after_attempt(settings.obs_max_retries),
            reraise=True,
        ):
            with attempt:
                session = await self._get_session()
                async with session.get(url) as resp:
                    if resp.status == 404:
                        raise SourceNotFound(url)
                    if resp.status != 200:
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history, status=resp.status
                        )
                    return await resp.text()

        raise SourceError(f"All retries exhausted for {url}")
