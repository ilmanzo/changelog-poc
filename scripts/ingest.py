#!/usr/bin/env python
"""Offline batch ingestion CLI.

    uv run scripts/ingest.py systemd chrony openssh
    uv run scripts/ingest.py --file packages.txt
    uv run scripts/ingest.py --concurrency 4 systemd chrony

Exit codes: 0 if every package INDEXED, 1 if any EMPTY/INVALID, 2 on unhandled error.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import structlog

from src.config import settings
from src.db import Database
from src.ingest import IngestResult, IngestService, IngestStatus
from src.logging_config import configure_logging
from src.rpm_manager import RPMManager
from src.sources import (
    FetchStrategy,
    GiteaSource,
    ObsSource,
    RpmSource,
    SourceRegistry,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("packages", nargs="*", help="Package names to ingest")
    p.add_argument(
        "--file", "-f", type=Path, help="Read package names from a file (one per line, # comments OK)"
    )
    p.add_argument("--concurrency", "-c", type=int, default=2, help="Max concurrent ingestions (default: 2)")
    p.add_argument("--debug", action="store_true", help="Verbose logging")
    return p.parse_args()


def _read_package_file(path: Path) -> list[str]:
    out: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _collect_packages(args: argparse.Namespace) -> list[str]:
    pkgs = list(args.packages)
    if args.file:
        pkgs.extend(_read_package_file(args.file))
    seen: set[str] = set()
    deduped: list[str] = []
    for p in pkgs:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


async def _run(packages: list[str], concurrency: int) -> list[IngestResult]:
    db = Database()
    await db.connect()
    rpm_mgr = RPMManager()
    registry = SourceRegistry(
        sources=[RpmSource(rpm_mgr), ObsSource(), GiteaSource()],
        strategy=FetchStrategy(settings.fetch_strategy),
    )
    service = IngestService(registry, db, rpm_mgr=rpm_mgr)
    sem = asyncio.Semaphore(concurrency)

    async def _one(pkg: str) -> IngestResult:
        async with sem:
            return await service.ingest(pkg)

    try:
        return await asyncio.gather(*(_one(p) for p in packages))
    finally:
        await registry.close()
        await db.close()


def _exit_code(results: list[IngestResult]) -> int:
    return 0 if all(r.status is IngestStatus.INDEXED for r in results) else 1


def main() -> int:
    args = _parse_args()
    configure_logging(
        level=logging.DEBUG if args.debug else logging.INFO,
        json_logs=(settings.log_format == "json"),
    )
    log = structlog.get_logger("ingest-cli")

    packages = _collect_packages(args)
    if not packages:
        log.error("no_packages", msg="give names on CLI or via --file")
        return 2

    log.info("starting", count=len(packages), concurrency=args.concurrency)
    results = asyncio.run(_run(packages, args.concurrency))

    indexed = sum(1 for r in results if r.status is IngestStatus.INDEXED)
    empty = sum(1 for r in results if r.status is IngestStatus.EMPTY)
    invalid = sum(1 for r in results if r.status is IngestStatus.INVALID)
    log.info("done", indexed=indexed, empty=empty, invalid=invalid)
    return _exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
