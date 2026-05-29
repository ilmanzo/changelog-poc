"""Spec-file sources: a minimal ABC + OBS/Pagure implementations.

Each source returns ``(text, source_url)`` on success and ``(None, None)``
when the package is unavailable on that origin -- transient HTTP errors
(retried by the shared ``HttpClient`` machinery) are converted to that same
"missing" tuple so the caller in ``src/tools/spec.py`` doesn't need to
distinguish "404" from "service degraded".
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import structlog

from .base import SourceError, SourceNotFound
from .http_source import HttpClient

_logger = structlog.get_logger("rpm-mcp.spec_source")


class SpecSource(ABC):
    """Optional `fetch_spec` capability on the source ABC family (DD4)."""

    name: str = ""

    @abstractmethod
    async def fetch_spec(self, package: str) -> tuple[str | None, str | None]: ...


class ObsSpecSource(SpecSource, HttpClient):
    name = "opensuse"
    _BASE = "https://build.opensuse.org/public/source/openSUSE:Factory"

    async def fetch_spec(self, package: str) -> tuple[str | None, str | None]:
        url = f"{self._BASE}/{package}/{package}.spec"
        try:
            text = await self._fetch_text(url)
        except SourceNotFound:
            return None, None
        except SourceError as exc:
            _logger.warning("obs_spec_fetch_failed", package=package, error=str(exc))
            return None, None
        return (text, url) if text else (None, None)


class PagureSpecSource(SpecSource, HttpClient):
    name = "fedora"
    _BASE = "https://src.fedoraproject.org"

    async def fetch_spec(self, package: str) -> tuple[str | None, str | None]:
        api_url = f"{self._BASE}/api/0/rpms/{package}"
        try:
            meta = await self._fetch_json(api_url)
            branch = meta.get("default_branch", "main")
            raw_url = f"{self._BASE}/rpms/{package}/raw/{branch}/f/{package}.spec"
            text = await self._fetch_text(raw_url)
        except SourceNotFound:
            return None, None
        except SourceError as exc:
            _logger.warning("pagure_fetch_failed", package=package, error=str(exc))
            return None, None
        return (text, raw_url) if text else (None, None)


SPEC_SOURCES: dict[str, SpecSource] = {
    s.name: s for s in (ObsSpecSource(), PagureSpecSource())
}
