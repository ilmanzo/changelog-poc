"""SourceRegistry: orchestrates fetching across multiple ChangelogSources."""
from __future__ import annotations

import asyncio
from enum import Enum

import structlog

from .base import ChangelogSource, FetchResult, SourceError, SourceNotFound

logger = structlog.get_logger("rpm-mcp.sources")


class FetchStrategy(str, Enum):
    WATERFALL = "waterfall"
    PARALLEL = "parallel"


class SourceRegistry:
    """Holds an ordered list of sources and applies a fetch strategy."""

    def __init__(
        self,
        sources: list[ChangelogSource],
        strategy: FetchStrategy = FetchStrategy.WATERFALL,
    ) -> None:
        self._sources = sources
        self._strategy = strategy
        self._local = [s for s in sources if s.is_local]
        self._network = [s for s in sources if not s.is_local]

    @property
    def known_distros(self) -> list[str]:
        seen: dict[str, None] = {}
        for s in self._sources:
            seen.setdefault(s.distro, None)
        return list(seen)

    def _filter(self, distro: str | None) -> list[ChangelogSource]:
        if distro is None:
            return self._sources
        return [s for s in self._sources if s.distro == distro]

    async def fetch(self, package: str, *, distro: str | None = None) -> FetchResult:
        sources = self._filter(distro)
        if not sources:
            return FetchResult(entries=[], source_name="none")
        if self._strategy == FetchStrategy.PARALLEL:
            return await self._fetch_parallel(package, sources)
        return await self._fetch_waterfall(package, sources)

    async def close(self) -> None:
        for source in self._sources:
            await source.close()

    async def _fetch_waterfall(
        self, package: str, sources: list[ChangelogSource],
    ) -> FetchResult:
        any_error = False
        for source in sources:
            try:
                result = await source.fetch(package)
                if not result.is_empty:
                    logger.info("source_hit",
                                package=package, source=source.name,
                                entries=len(result.entries), strategy="waterfall")
                    return result
                logger.info("source_empty", package=package, source=source.name)
            except SourceNotFound:
                logger.info("source_miss",
                            package=package, source=source.name, reason="not_found")
            except SourceError as exc:
                any_error = True
                logger.warning("source_error",
                               package=package, source=source.name, error=str(exc))

        return FetchResult(entries=[], source_name="none", fetch_failed=any_error)

    async def _fetch_parallel(
        self, package: str, sources: list[ChangelogSource],
    ) -> FetchResult:
        local = [s for s in sources if s.is_local]
        network = [s for s in sources if not s.is_local]
        any_error = False
        for source in local:
            try:
                result = await source.fetch(package)
                if not result.is_empty:
                    logger.info("source_hit",
                                package=package, source=source.name,
                                entries=len(result.entries), strategy="parallel_local")
                    return result
            except SourceNotFound:
                logger.info("source_miss",
                            package=package, source=source.name, reason="not_found")
            except SourceError as exc:
                any_error = True
                logger.warning("source_error",
                               package=package, source=source.name, error=str(exc))

        if not network:
            return FetchResult(entries=[], source_name="none", fetch_failed=any_error)

        raw = await asyncio.gather(
            *[s.fetch(package) for s in network],
            return_exceptions=True,
        )

        valid: list[FetchResult] = []
        for source, outcome in zip(network, raw):
            if isinstance(outcome, SourceNotFound):
                logger.info("source_miss",
                            package=package, source=source.name, reason="not_found")
            elif isinstance(outcome, Exception):
                any_error = True
                logger.warning("source_error",
                               package=package, source=source.name, error=str(outcome))
            elif isinstance(outcome, FetchResult) and not outcome.is_empty:
                valid.append(outcome)

        if not valid:
            return FetchResult(entries=[], source_name="none", fetch_failed=any_error)

        # Pick the source that returned the most entries: in practice OBS/Gitea
        # mirrors lag and truncate, so "most rows" is a reasonable proxy for
        # "freshest". Content-addressed UUIDs make later merging idempotent if
        # we ever want to union instead.
        best = max(valid, key=lambda r: len(r.entries))
        logger.info("source_hit",
                    package=package, source=best.source_name,
                    entries=len(best.entries), strategy="parallel_network")
        return best
