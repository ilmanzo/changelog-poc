"""Changelog source: OBS Factory API (api.opensuse.org)."""

from __future__ import annotations

from ..obs_parser import parse_obs_changes
from ..service_file_parser import extract_urls_from_service
from ..spec_url_extractor import extract_upstream_urls
from .base import FetchResult, SourceError, SourceNotFound
from .http_source import HttpSource


class ObsSource(HttpSource):
    """Fetches .changes files from the openSUSE Build Service Factory API."""

    name = "obs"
    _BASE_URL = "https://api.opensuse.org/public/source/openSUSE:Factory"

    async def fetch(self, package: str) -> FetchResult:
        url = f"{self._BASE_URL}/{package}/{package}.changes"
        text = await self._fetch_text(url)
        return FetchResult(
            entries=parse_obs_changes(text, package=package, source=self.name),
            source_name=self.name,
        )

    async def resolve_upstream_url(self, package: str) -> str | None:
        """Try the OBS spec header and _service file to find a forge URL.

        Returns the first match or None. OBS-specific; sister sources
        (Fedora, Ubuntu) must implement their own resolver when needed --
        their upstream URL typically comes from the spec/control file
        already returned by the changelog ``fetch``.
        """
        for suffix, parser in (
            (f"/{package}/{package}.spec", extract_upstream_urls),
            (f"/{package}/_service", extract_urls_from_service),
        ):
            try:
                text = await self._fetch_text(self._BASE_URL + suffix)
            except (SourceNotFound, SourceError):
                continue
            urls = parser(text)
            if urls:
                return urls[0]
        return None
