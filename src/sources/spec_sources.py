"""Spec-file sources: a minimal ABC + OBS/Pagure implementations.

Mirrors the `ChangelogSource` ABC pattern but for raw `.spec` text. Sources
return ``(text, source_url)`` on success and ``(None, None)`` when the
package is unavailable on that origin.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import structlog

from ..http_utils import make_client_session

_logger = structlog.get_logger("rpm-mcp.spec_source")


class SpecSource(ABC):
    """Optional `fetch_spec` capability on the source ABC family (DD4)."""

    name: str = ""

    @abstractmethod
    async def fetch_spec(self, package: str) -> tuple[str | None, str | None]: ...


class ObsSpecSource(SpecSource):
    name = "opensuse"
    _BASE = "https://build.opensuse.org/public/source/openSUSE:Factory"

    async def fetch_spec(self, package: str) -> tuple[str | None, str | None]:
        url = f"{self._BASE}/{package}/{package}.spec"
        try:
            async with make_client_session() as session, session.get(url) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    if text:
                        return text, url
        except Exception as e:
            _logger.warning("obs_spec_fetch_failed", package=package, error=str(e))
        return None, None


class PagureSpecSource(SpecSource):
    name = "fedora"
    _BASE = "https://src.fedoraproject.org"

    async def fetch_spec(self, package: str) -> tuple[str | None, str | None]:
        api_url = f"{self._BASE}/api/0/rpms/{package}"
        try:
            async with make_client_session() as session:
                async with session.get(api_url) as meta:
                    if meta.status != 200:
                        return None, None
                    branch = (await meta.json()).get("default_branch", "main")
                raw_url = f"{self._BASE}/rpms/{package}/raw/{branch}/f/{package}.spec"
                async with session.get(raw_url) as spec:
                    if spec.status == 200:
                        text = await spec.text()
                        if text:
                            return text, raw_url
        except Exception as e:
            _logger.warning("pagure_fetch_failed", package=package, error=str(e))
        return None, None


_WATERFALL: tuple[SpecSource, ...] = (ObsSpecSource(), PagureSpecSource())
SPEC_SOURCES: dict[str, SpecSource] = {s.name: s for s in _WATERFALL}


async def fetch_any_spec(package: str) -> tuple[str | None, str | None, str | None]:
    """Try sources in waterfall order. Returns (text, url, source_name)."""
    for src in _WATERFALL:
        text, url = await src.fetch_spec(package)
        if text:
            return text, url, src.name
    return None, None, None
