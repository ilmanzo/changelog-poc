"""Changelog source: Fedora via Pagure spec %changelog section."""
from __future__ import annotations

import re

from async_lru import alru_cache

from .base import FetchResult, SourceNotFound
from .http_source import HttpSource
from ..obs_parser import parse_obs_changes

_CHANGELOG_SPLIT = re.compile(r"^%changelog\s*$", re.MULTILINE | re.IGNORECASE)


def extract_changelog_section(spec_text: str) -> str | None:
    """Return everything after the first ``%changelog`` directive, or None."""
    parts = _CHANGELOG_SPLIT.split(spec_text, maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip()


class FedoraSource(HttpSource):
    """Fetches the .spec file from Fedora Pagure and parses its %changelog."""

    name = "fedora"
    _API_BASE = "https://src.fedoraproject.org"

    @alru_cache(maxsize=128)
    async def fetch(self, package: str) -> FetchResult:
        meta_url = f"{self._API_BASE}/api/0/rpms/{package}"
        meta_text = await self._fetch_text(meta_url)

        import json
        meta = json.loads(meta_text)
        branch = meta.get("default_branch", "main")

        spec_url = f"{self._API_BASE}/rpms/{package}/raw/{branch}/f/{package}.spec"
        spec_text = await self._fetch_text(spec_url)

        changelog_text = extract_changelog_section(spec_text)
        if not changelog_text:
            raise SourceNotFound(f"no %changelog in {package}.spec")

        entries = parse_obs_changes(
            changelog_text, package=package, source=self.name,
        )
        return FetchResult(entries=entries, source_name=self.name, distro="fedora")
