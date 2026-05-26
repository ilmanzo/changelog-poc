"""Changelog source: OBS Factory API (api.opensuse.org)."""
from __future__ import annotations

from async_lru import alru_cache

from .base import FetchResult
from .http_source import HttpSource
from ..obs_parser import parse_obs_changes


class ObsSource(HttpSource):
    """Fetches .changes files from the openSUSE Build Service Factory API."""

    name = "obs"
    _BASE_URL = "https://api.opensuse.org/public/source/openSUSE:Factory"

    @alru_cache(maxsize=128)
    async def fetch(self, package: str) -> FetchResult:
        url = f"{self._BASE_URL}/{package}/{package}.changes"
        text = await self._fetch_text(url)
        return FetchResult(
            entries=parse_obs_changes(text, package=package, source=self.name),
            source_name=self.name,
        )
