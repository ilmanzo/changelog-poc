"""Process-wide singletons shared by every tool module and the CLI.

Centralising them here lets `src/tools/*` import without touching
``mcp_server`` (which would create a cycle, since the server itself
imports the tool modules).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog

from src.config import settings
from src.db import Database
from src.git_manager import GitManager
from src.ingest import IngestService
from src.rpm_manager import RPMManager
from src.test_repo_manager import TestRepoManager
from src.sources import (
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
test_repo_mgr = TestRepoManager()


@asynccontextmanager
async def lifespan(_server: object) -> AsyncIterator[None]:
    await db.connect()
    _logger.info("server_started")
    try:
        yield
    finally:
        await source_registry.close()
        await db.close()
        _logger.info("server_stopped")
