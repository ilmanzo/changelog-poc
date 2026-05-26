"""Spec-file sources: a minimal ABC + OBS/Pagure implementations.

Mirrors the `ChangelogSource` ABC pattern but for raw `.spec` text. Sources
return ``(text, source_url)`` on success and ``(None, None)`` when the
package is unavailable on that origin.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
import structlog

from ..config import settings

_logger = structlog.get_logger("rpm-mcp.spec_source")
_TIMEOUT = httpx.Timeout(settings.obs_timeout_total, connect=settings.obs_timeout_connect)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)


class SpecSource(ABC):
    """Optional `fetch_spec` capability on the source ABC family (DD4)."""

    name: str = ""

    @abstractmethod
    async def fetch_spec(self, package: str) -> tuple[str | None, str | None]:
        ...


class ObsSpecSource(SpecSource):
    name = "opensuse"
    _BASE = "https://build.opensuse.org/public/source/openSUSE:Factory"

    async def fetch_spec(self, package: str) -> tuple[str | None, str | None]:
        url = f"{self._BASE}/{package}/{package}.spec"
        try:
            async with _client() as cli:
                resp = await cli.get(url)
                if resp.status_code == 200 and resp.text:
                    return resp.text, url
        except Exception as e:
            _logger.warning("obs_spec_fetch_failed", package=package, error=str(e))
        return None, None


class PagureSpecSource(SpecSource):
    name = "fedora"
    _BASE = "https://src.fedoraproject.org"

    async def fetch_spec(self, package: str) -> tuple[str | None, str | None]:
        api_url = f"{self._BASE}/api/0/rpms/{package}"
        try:
            async with _client() as cli:
                meta = await cli.get(api_url)
                if meta.status_code != 200:
                    return None, None
                branch = meta.json().get("default_branch", "main")
                raw_url = f"{self._BASE}/rpms/{package}/raw/{branch}/f/{package}.spec"
                spec = await cli.get(raw_url)
                if spec.status_code == 200 and spec.text:
                    return spec.text, raw_url
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
