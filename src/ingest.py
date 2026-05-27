"""Ingestion service — fetches a package's changelog and writes it to Postgres.

Pure application logic; no MCP or CLI concerns. Both the ``sync_package`` MCP
tool and ``scripts/ingest.py`` delegate here so the same code path is exercised
by on-demand and offline batch ingestion.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime
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
    STALE = "stale"   # fetch failed, serving previously-cached rows
    ERROR = "error"   # unexpected exception during a background ingest


@dataclass(frozen=True)
class IngestResult:
    package: str
    status: IngestStatus
    entries: int = 0
    source: str = ""
    elapsed_s: float = 0.0
    error: str = ""
    synced_at: datetime | None = None   # populated when status is STALE


class IngestService:
    """Coordinates ``SourceRegistry`` → ``Database`` for a single package.

    In-flight ingests are coalesced via ``_pending`` so that repeated calls
    for the same ``(package, distro)`` share a single task. Use ``schedule``
    for fire-and-forget background dispatch (see DD10 fast-fail UX).
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
        self._pending: dict[tuple[str, str], asyncio.Task[IngestResult]] = {}

    # ------------------------------------------------------------------
    # Public entrypoints
    # ------------------------------------------------------------------
    async def ingest(self, package: str, distro: str = "opensuse") -> IngestResult:
        """Coalesced sync-style ingest — awaits the result."""
        return await self._get_or_start(package, distro)

    def schedule(
        self, package: str, distro: str = "opensuse"
    ) -> asyncio.Task[IngestResult]:
        """Fire-and-forget. Returns the in-flight task (for tests/diagnostics).

        Production callers ignore the return value. Exceptions raised inside
        the task are captured by ``_ingest_one`` and converted to
        ``IngestStatus.ERROR`` so unawaited tasks do not log warnings.
        """
        return self._get_or_start(package, distro)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _get_or_start(
        self, package: str, distro: str
    ) -> asyncio.Task[IngestResult]:
        key = (package, distro)
        task = self._pending.get(key)
        if task is not None and not task.done():
            return task
        task = asyncio.create_task(self._ingest_one(package, distro))
        self._pending[key] = task

        def _cleanup(_t: asyncio.Task[IngestResult], k: tuple[str, str] = key) -> None:
            self._pending.pop(k, None)

        task.add_done_callback(_cleanup)
        return task

    async def _ingest_one(self, package: str, distro: str) -> IngestResult:
        try:
            return await self._do_ingest(package, distro)
        except Exception as exc:  # noqa: BLE001 — background safety net
            self._log.exception("ingest_failed", package=package)
            return IngestResult(
                package=package,
                status=IngestStatus.ERROR,
                error=str(exc),
            )

    async def _do_ingest(self, package: str, distro: str) -> IngestResult:
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
            if result.fetch_failed:
                stale = await self._stale_fallback(package)
                if stale is not None:
                    cached_count, synced_at = stale
                    log.warning(
                        "serving_stale",
                        cached_entries=cached_count,
                        synced_at=synced_at.isoformat() if synced_at else None,
                        elapsed_s=elapsed,
                    )
                    return IngestResult(
                        package=package,
                        status=IngestStatus.STALE,
                        entries=cached_count,
                        elapsed_s=elapsed,
                        synced_at=synced_at,
                    )
            log.warning("no_entries_found", elapsed_s=elapsed)
            return IngestResult(
                package=package,
                status=IngestStatus.EMPTY,
                elapsed_s=elapsed,
            )

        effective_distro = result.distro or distro
        package_id = await self._db.upsert_package(
            name=package,
            distro=effective_distro,
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
        await self._db.touch_manifest(package_id, kind="changelog")

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

    async def _stale_fallback(
        self, package: str
    ) -> tuple[int, datetime | None] | None:
        """If we have cached rows for *package*, return (count, synced_at).

        Returns ``None`` when no cache exists (caller should report EMPTY).
        """
        pkg_id = await self._db.get_package_id(package)
        if pkg_id is None:
            return None
        rows = await self._db.fetch_entries(pkg_id)
        if not rows:
            return None
        synced_at = await self._db.get_synced_at(pkg_id)
        return len(rows), synced_at
