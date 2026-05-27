"""Changelog source: Fedora via Pagure dist-git.

Tries the standalone ``changelog`` file first (rpmautospec), then falls
back to extracting ``%changelog`` from the spec file. Both use RPM
changelog format (``* Day Mon DD YYYY Author - version``).
"""
from __future__ import annotations

import re

from async_lru import alru_cache

from .base import FetchResult, SourceNotFound
from .http_source import HttpSource
from ..obs_parser import parse_obs_changes
from ..rpm_manager import RPMManager

_CHANGELOG_SPLIT = re.compile(r"^%changelog\s*$", re.MULTILINE | re.IGNORECASE)
_RPM_HEADER = re.compile(r"^\* [A-Z][a-z]{2} [A-Z][a-z]{2}")


def extract_changelog_section(spec_text: str) -> str | None:
    """Return everything after the first ``%changelog`` directive, or None."""
    parts = _CHANGELOG_SPLIT.split(spec_text, maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip()


def _parse_changelog(text: str, package: str) -> list:
    """Parse changelog text, auto-detecting RPM vs OBS format."""
    if _RPM_HEADER.search(text):
        return RPMManager.parse_changelog(text, package=package)
    return parse_obs_changes(text, package=package, source="fedora")


class FedoraSource(HttpSource):
    """Fetches changelogs from Fedora Pagure dist-git."""

    name = "fedora"
    distro = "fedora"
    _API_BASE = "https://src.fedoraproject.org"

    @alru_cache(maxsize=128)
    async def fetch(self, package: str) -> FetchResult:
        meta_url = f"{self._API_BASE}/api/0/rpms/{package}"
        meta_text = await self._fetch_text(meta_url)

        import json
        meta = json.loads(meta_text)
        branch = meta.get("default_branch", "rawhide")

        # rpmautospec: try standalone changelog file first
        changelog_url = f"{self._API_BASE}/rpms/{package}/raw/{branch}/f/changelog"
        try:
            changelog_text = await self._fetch_text(changelog_url)
            entries = _parse_changelog(changelog_text, package)
            if entries:
                return FetchResult(entries=entries, source_name=self.name, distro="fedora")
        except SourceNotFound:
            pass

        # Fallback: extract %changelog from spec
        spec_url = f"{self._API_BASE}/rpms/{package}/raw/{branch}/f/{package}.spec"
        spec_text = await self._fetch_text(spec_url)

        section = extract_changelog_section(spec_text)
        if not section:
            raise SourceNotFound(f"no changelog for {package} in Fedora dist-git")

        entries = _parse_changelog(section, package)
        return FetchResult(entries=entries, source_name=self.name, distro="fedora")
