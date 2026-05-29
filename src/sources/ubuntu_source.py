"""Changelog source: Ubuntu via Launchpad.

Fetches the ``+changelog`` HTML page from Launchpad and extracts
Debian-format changelog entries from ``<pre>`` blocks.
"""

from __future__ import annotations

import html
import re

from ..debian_parser import parse_debian_changelog
from .base import FetchResult
from .http_source import HttpSource

_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.DOTALL)


class UbuntuSource(HttpSource):
    """Fetches changelogs from Launchpad by source package name."""

    name = "ubuntu"
    distro = "ubuntu"
    _BASE_URL = "https://launchpad.net/ubuntu/+source"

    async def fetch(self, package: str) -> FetchResult:
        url = f"{self._BASE_URL}/{package}/+changelog"
        page = await self._fetch_text(url)
        blocks = _PRE_RE.findall(page)
        if not blocks:
            return FetchResult(entries=[], source_name=self.name, distro="ubuntu")
        changelog_text = "\n\n".join(html.unescape(b) for b in blocks)
        entries = parse_debian_changelog(
            changelog_text,
            package=package,
            source=self.name,
        )
        return FetchResult(entries=entries, source_name=self.name, distro="ubuntu")
