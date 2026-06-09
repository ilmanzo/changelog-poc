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
from enum import StrEnum
from urllib.parse import urlparse

import structlog

from . import embedder
from .db import Database
from .errors import ValidationError
from .rpm_manager import RPMManager
from .sources import SourceRegistry
from .sources.base import SourceError, SourceNotFound
from .sources.url_router import parse_upstream_url

_PACKAGE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.+]+$")
_SAFE_UPSTREAM_SCHEMES = {"https"}


def validate_package_name(package: str) -> None:
    if not _PACKAGE_NAME_RE.match(package):
        raise ValidationError(f"Invalid package name: {package!r}")


def safe_upstream_url(url: str | None) -> str | None:
    """Sanitise an upstream URL before persisting / fetching from it.

    Returns the URL when it parses as https with a non-empty host; otherwise
    None. RPM ``URL:`` fields, spec headers, and OBS ``_service`` files are
    all untrusted -- a malicious entry like ``file:///etc/passwd`` or
    ``http://internal/api`` must not be stored or fetched.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in _SAFE_UPSTREAM_SCHEMES or not parsed.hostname:
        return None
    return url


class IngestStatus(StrEnum):
    INDEXED = "indexed"
    EMPTY = "empty"
    INVALID = "invalid"
    STALE = "stale"  # fetch failed, serving previously-cached rows
    ERROR = "error"  # unexpected exception during a background ingest


@dataclass(frozen=True)
class IngestResult:
    package: str
    status: IngestStatus
    entries: int = 0
    source: str = ""
    elapsed_s: float = 0.0
    error: str = ""
    synced_at: datetime | None = None  # populated when status is STALE


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
        rpm_mgr: RPMManager | None = None,
        logger_name: str = "rpm-mcp.ingest",
    ) -> None:
        self._sources = source_registry
        self._db = db
        self._rpm_mgr = rpm_mgr
        self._log = structlog.get_logger(logger_name)
        self._pending: dict[tuple[str, str], asyncio.Task[IngestResult]] = {}

    # ------------------------------------------------------------------
    # Public entrypoints
    # ------------------------------------------------------------------
    async def ingest(self, package: str, distro: str = "opensuse") -> IngestResult:
        """Coalesced sync-style ingest — awaits the result."""
        return await self._get_or_start(package, distro)

    def schedule(self, package: str, distro: str = "opensuse") -> asyncio.Task[IngestResult]:
        """Fire-and-forget. Returns the in-flight task (for tests/diagnostics).

        Production callers ignore the return value. Exceptions raised inside
        the task are captured by ``_ingest_one`` and converted to
        ``IngestStatus.ERROR`` so unawaited tasks do not log warnings.
        """
        return self._get_or_start(package, distro)

    async def ingest_all_distros(self, package: str) -> list[IngestResult]:
        """Ingest *package* from every registered distro, capped at
        ``worker_concurrency`` concurrent fetches so we don't DOS upstreams
        as the distro count grows.
        """
        distros = self._sources.known_distros
        # Defensive lower bound: at least one in-flight, regardless of config.
        from .config import settings as _settings
        sem = asyncio.Semaphore(max(1, _settings.worker_concurrency))

        async def _bounded(d: str) -> IngestResult:
            async with sem:
                return await self.ingest(package, d)

        return list(await asyncio.gather(*(_bounded(d) for d in distros)))

    async def drain_pending(self, timeout_s: float | None = None) -> int:
        """Await any in-flight tasks scheduled via ``schedule``.

        Called from the lifespan ``__aexit__`` so a fire-and-forget ingest
        triggered just before shutdown (e.g. in CLI one-shot mode) is allowed
        to finish instead of being cancelled by ``asyncio.run`` finalisation.
        Returns the number of tasks awaited. Times out silently — the caller
        is already on the shutdown path.
        """
        tasks = [t for t in self._pending.values() if not t.done()]
        if not tasks:
            return 0
        self._log.info("draining_pending_ingests", count=len(tasks))
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout_s,
            )
        except TimeoutError:
            self._log.warning("drain_timeout", remaining=sum(1 for t in tasks if not t.done()))
        return len(tasks)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _get_or_start(self, package: str, distro: str) -> asyncio.Task[IngestResult]:
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
        except Exception as exc:
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
        except ValidationError as exc:
            log.warning("invalid_package", error=str(exc))
            return IngestResult(
                package=package,
                status=IngestStatus.INVALID,
                error=str(exc),
                elapsed_s=round(time.perf_counter() - t0, 3),
            )

        result = await self._sources.fetch(package, distro=distro)
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
            upstream_url=safe_upstream_url(result.upstream_url),
        )

        embeddings = await embedder.embed_batch(e.content for e in result.entries)
        inserted = await self._db.upsert_changelog_entries(
            package_name=package,
            package_id=package_id,
            entries=result.entries,
            embeddings=embeddings,
            source_name=result.source_name,
        )
        await self._db.touch_manifest(package_id, kind="changelog")

        await self._populate_deps(package, package_id, effective_distro, log)

        upstream_extra = await self._enrich_upstream(
            package,
            package_id,
            result.upstream_url,
            log,
        )
        total = inserted + upstream_extra

        elapsed = round(time.perf_counter() - t0, 3)
        log.info(
            "indexed",
            entries=total,
            source=result.source_name,
            upstream_extra=upstream_extra,
            elapsed_s=elapsed,
        )
        return IngestResult(
            package=package,
            status=IngestStatus.INDEXED,
            entries=total,
            source=result.source_name,
            elapsed_s=elapsed,
        )

    async def _populate_deps(
        self,
        package: str,
        package_id: int,
        distro: str,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        """Best-effort: refresh `deps` table from the local rpmdb.

        No-op when the rpm manager is absent or the package is not installed
        locally (`rpm -qR` failure). Local-only because `rpm -qR` queries the
        host rpmdb.
        """
        if self._rpm_mgr is None or distro != "opensuse":
            return
        try:
            deps = await self._rpm_mgr.get_dependencies(package)
        except RuntimeError:
            return
        await self._db.replace_deps(package_id, deps, kind="requires")
        log.debug("deps_populated", count=len(deps))

    async def _enrich_upstream(
        self,
        package: str,
        package_id: int,
        upstream_url: str | None,
        log: structlog.stdlib.BoundLogger,
    ) -> int:
        """Best-effort: fetch release notes from GitHub/GitLab if URL resolves."""
        url = safe_upstream_url(upstream_url) or await self._db.get_upstream_url(package)
        if not url:
            url = safe_upstream_url(await self._resolve_upstream_url(package))
            if url:
                await self._db.upsert_package(
                    name=package,
                    upstream_url=url,
                )
        if not url:
            return 0

        source = parse_upstream_url(url)
        if source is None:
            return 0

        try:
            result = await source.fetch(package)
        except (SourceNotFound, SourceError) as exc:
            log.info("upstream_skip", url=url, reason=str(exc))
            return 0

        if result.is_empty:
            return 0

        embs = await embedder.embed_batch(e.content for e in result.entries)
        inserted = await self._db.upsert_changelog_entries(
            package_name=package,
            package_id=package_id,
            entries=result.entries,
            embeddings=embs,
            source_name=result.source_name,
        )
        log.info(
            "upstream_enriched",
            source=result.source_name,
            entries=inserted,
            url=url,
        )
        return inserted

    async def _resolve_upstream_url(self, package: str) -> str | None:
        """Ask each source that knows how to find its own upstream URL.

        Today only ``ObsSource`` implements this -- the resolver is delegated
        to whichever source carries the knowledge so the ingest layer stays
        distro-agnostic.
        """
        obs = self._sources.get_by_name("obs")
        if obs is not None and hasattr(obs, "resolve_upstream_url"):
            return await obs.resolve_upstream_url(package)
        return None

    async def _stale_fallback(self, package: str) -> tuple[int, datetime | None] | None:
        """If we have cached rows for *package*, return (count, synced_at).

        Returns ``None`` when no cache exists (caller should report EMPTY).
        Uses ``count_entries`` instead of ``fetch_entries`` so a package with
        100k rows doesn't materialise everything just to report a count.
        """
        pkg_id = await self._db.get_package_id(package)
        if pkg_id is None:
            return None
        count = await self._db.count_entries(pkg_id)
        if count == 0:
            return None
        synced_at = await self._db.get_synced_at(pkg_id)
        return count, synced_at
