"""Process-wide singletons shared by every tool module and the CLI.

Centralising them here lets `src/tools/*` import without touching
``mcp_server`` (which would create a cycle, since the server itself
imports the tool modules).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog

from .config import settings
from .db import Database
from .git_manager import GitManager
from .ingest import IngestService
from .rpm_manager import RPMManager
from .sources import (
    FedoraSource,
    FetchStrategy,
    GiteaSource,
    ObsSource,
    RpmSource,
    SourceRegistry,
    UbuntuSource,
)

_logger = structlog.get_logger("rpm-mcp.server")

db = Database()
rpm_mgr = RPMManager()
git_mgr = GitManager()
source_registry = SourceRegistry(
    sources=[RpmSource(rpm_mgr), ObsSource(), GiteaSource(), FedoraSource(), UbuntuSource()],
    strategy=FetchStrategy(settings.fetch_strategy),
)
ingest_service = IngestService(source_registry, db)


@asynccontextmanager
async def lifespan(_server: object) -> AsyncIterator[None]:
    await db.connect()
    _logger.info("server_started")
    try:
        yield
    finally:
        # Why: a tool may have just dispatched a fire-and-forget ingest via
        # IngestService.schedule(); without an explicit drain, asyncio.run
        # finalisation cancels those tasks mid-flight and the CLI/MCP caller
        # is left with a "queued" promise that never lands.
        await ingest_service.drain_pending(timeout_s=30.0)
        await source_registry.close()
        await db.close()
        _logger.info("server_stopped")
