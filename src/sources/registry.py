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

    async def fetch(self, package: str) -> FetchResult:
        if self._strategy == FetchStrategy.PARALLEL:
            return await self._fetch_parallel(package)
        return await self._fetch_waterfall(package)

    async def close(self) -> None:
        for source in self._sources:
            await source.close()

    async def _fetch_waterfall(self, package: str) -> FetchResult:
        for source in self._sources:
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
                logger.warning("source_error",
                               package=package, source=source.name, error=str(exc))

        return FetchResult(entries=[], source_name="none")

    async def _fetch_parallel(self, package: str) -> FetchResult:
        for source in self._local:
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
                logger.warning("source_error",
                               package=package, source=source.name, error=str(exc))

        if not self._network:
            return FetchResult(entries=[], source_name="none")

        raw = await asyncio.gather(
            *[s.fetch(package) for s in self._network],
            return_exceptions=True,
        )

        valid: list[FetchResult] = []
        for source, outcome in zip(self._network, raw):
            if isinstance(outcome, SourceNotFound):
                logger.info("source_miss",
                            package=package, source=source.name, reason="not_found")
            elif isinstance(outcome, (SourceError, Exception)):
                logger.warning("source_error",
                               package=package, source=source.name, error=str(outcome))
            elif isinstance(outcome, FetchResult) and not outcome.is_empty:
                valid.append(outcome)

        if not valid:
            return FetchResult(entries=[], source_name="none")

        best = max(valid, key=lambda r: len(r.entries))
        logger.info("source_hit",
                    package=package, source=best.source_name,
                    entries=len(best.entries), strategy="parallel_network")
        return best
