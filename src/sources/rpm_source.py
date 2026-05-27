"""Changelog source: local RPM database."""
from __future__ import annotations

from async_lru import alru_cache

from ..rpm_manager import RPMManager
from .base import ChangelogSource, FetchResult, SourceError, SourceNotFound


class RpmSource(ChangelogSource):
    """Reads changelog entries from the local RPM database.

    Fastest source (no network I/O) and the only one that can provide the
    package's upstream URL (used for git deep-research).
    """

    name = "rpm"
    is_local = True

    def __init__(self, rpm_manager: RPMManager | None = None) -> None:
        self._mgr = rpm_manager or RPMManager()

    @alru_cache(maxsize=128)
    async def fetch(self, package: str) -> FetchResult:
        try:
            metadata = await self._mgr.get_metadata(package)
            return FetchResult(
                entries=metadata.changelog,
                upstream_url=metadata.url,
                source_name=self.name,
            )
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "not found" in msg or "no package" in msg:
                raise SourceNotFound(package) from exc
            raise SourceError(str(exc)) from exc
