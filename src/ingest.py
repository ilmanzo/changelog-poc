"""Ingestion service — fetches a package's changelog and writes it to Postgres.

Pure application logic; no MCP or CLI concerns. Both the ``sync_package`` MCP
tool and ``scripts/ingest.py`` delegate here so the same code path is exercised
by on-demand and offline batch ingestion.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import Enum

import structlog

from . import embedder
from .db import Database
from .sources import SourceRegistry

_PACKAGE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.+]+$")


def validate_package_name(package: str) -> None:
    if not _PACKAGE_NAME_RE.match(package):
        raise ValueError(f"Invalid package name: {package!r}")


class IngestStatus(str, Enum):
    INDEXED = "indexed"
    EMPTY = "empty"
    INVALID = "invalid"


@dataclass(frozen=True)
class IngestResult:
    package: str
    status: IngestStatus
    entries: int = 0
    source: str = ""
    elapsed_s: float = 0.0
    error: str = ""


class IngestService:
    """Coordinates ``SourceRegistry`` → ``Database`` for a single package.

    Stateless. Safe to construct once and reuse across requests / batch runs.
    """

    def __init__(
        self,
        source_registry: SourceRegistry,
        db: Database,
        logger_name: str = "rpm-mcp.ingest",
    ) -> None:
        self._sources = source_registry
        self._db = db
        self._log = structlog.get_logger(logger_name)

    async def ingest(self, package: str, distro: str = "opensuse") -> IngestResult:
        t0 = time.perf_counter()
        log = self._log.bind(package=package)

        try:
            validate_package_name(package)
        except ValueError as exc:
            log.warning("invalid_package", error=str(exc))
            return IngestResult(
                package=package,
                status=IngestStatus.INVALID,
                error=str(exc),
                elapsed_s=round(time.perf_counter() - t0, 3),
            )

        result = await self._sources.fetch(package)
        if result.is_empty:
            elapsed = round(time.perf_counter() - t0, 3)
            log.warning("no_entries_found", elapsed_s=elapsed)
            return IngestResult(
                package=package,
                status=IngestStatus.EMPTY,
                elapsed_s=elapsed,
            )

        package_id = await self._db.upsert_package(
            name=package,
            distro=distro,
            upstream_url=result.upstream_url,
        )

        embeddings = await embedder.embed_batch(e.content for e in result.entries)
        if not embeddings:
            embeddings = [[] for _ in result.entries]

        inserted = await self._db.upsert_changelog_entries(
            package_name=package,
            package_id=package_id,
            entries=result.entries,
            embeddings=embeddings,
            source_name=result.source_name,
        )
        await self._db.touch_manifest(package_id)

        elapsed = round(time.perf_counter() - t0, 3)
        log.info(
            "indexed",
            entries=inserted,
            source=result.source_name,
            elapsed_s=elapsed,
        )
        return IngestResult(
            package=package,
            status=IngestStatus.INDEXED,
            entries=inserted,
            source=result.source_name,
            elapsed_s=elapsed,
        )
