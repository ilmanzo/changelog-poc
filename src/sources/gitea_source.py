"""Changelog source: src.opensuse.org (Gitea)."""
from __future__ import annotations

from async_lru import alru_cache

from .base import FetchResult
from .http_source import HttpSource
from ..obs_parser import parse_obs_changes


class GiteaSource(HttpSource):
    """Fetches .changes files from src.opensuse.org (the Gitea mirror).
    Fallback when the OBS Factory API is unavailable.
    """

    name = "gitea"
    _BASE_URL = "https://src.opensuse.org/openSUSE"

    @alru_cache(maxsize=128)
    async def fetch(self, package: str) -> FetchResult:
        url = f"{self._BASE_URL}/{package}/raw/branch/master/{package}.changes"
        text = await self._fetch_text(url)
        return FetchResult(
            entries=parse_obs_changes(text),
            source_name=self.name,
        )
