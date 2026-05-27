"""Changelog source: Ubuntu/Debian changelogs.ubuntu.com.

Uses the simple URL pattern that serves the latest changelog for any
source package. No version resolution needed — the endpoint always
returns the current changelog.
"""
from __future__ import annotations

from async_lru import alru_cache

from .base import FetchResult
from .http_source import HttpSource
from ..debian_parser import parse_debian_changelog


class UbuntuSource(HttpSource):
    """Fetches changelogs from changelogs.ubuntu.com by source package name.

    "Easy to find" heuristic: try the same name as the RPM package.
    If 404, HttpSource._fetch_text raises SourceNotFound and the
    registry moves on.
    """

    name = "ubuntu"
    _BASE_URL = "https://changelogs.ubuntu.com/changelogs/binary"

    @alru_cache(maxsize=128)
    async def fetch(self, package: str) -> FetchResult:
        url = f"{self._BASE_URL}/{package}/changelog"
        text = await self._fetch_text(url)
        entries = parse_debian_changelog(
            text, package=package, source=self.name,
        )
        return FetchResult(entries=entries, source_name=self.name, distro="ubuntu")
