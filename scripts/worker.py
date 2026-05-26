#!/usr/bin/env python
"""Centralised ingestion daemon — run hourly via systemd timer / cron.

End users run only the per-user MCP server; this script handles bulk refresh.

    uv run scripts/worker.py                              # locally installed pkgs
    uv run scripts/worker.py --file packages.txt          # explicit allow-list
    uv run scripts/worker.py --news                       # refresh news feeds only
    uv run scripts/worker.py --openqa /path/to/openqa     # ingest openQA repo
    uv run scripts/worker.py --all --file packages.txt    # full sweep

Exits 0 unless an unrecoverable error occurs (e.g. DB unreachable).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import structlog

from src.config import settings
from src.ingest import IngestResult, IngestService, IngestStatus
from src.logging_config import configure_logging
from src.news_fetcher import fetch_all_news
from src.openqa_fetcher import scan_tests
from src.process import run_subprocess
from src.runtime import db, ingest_service, lifespan, rpm_mgr


async def _load_packages(args: argparse.Namespace) -> list[str]:
    if args.file:
        pkgs = []
        for raw in args.file.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                pkgs.append(line)
        return pkgs
    stdout, _, _ = await run_subprocess(rpm_mgr.rpm_binary, "-qa", "--qf", "%{NAME}\n")
    return sorted({line.strip() for line in stdout.splitlines() if line.strip()})


async def _ingest_batch(
    service: IngestService, packages: list[str], concurrency: int
) -> list[IngestResult]:
    sem = asyncio.Semaphore(concurrency)

    async def _one(pkg: str) -> IngestResult:
        async with sem:
            return await service.ingest(pkg)

    return await asyncio.gather(*(_one(p) for p in packages))


async def _run(args: argparse.Namespace) -> int:
    log = structlog.get_logger("worker")
    async with lifespan(None):
        if args.sweep:
            evicted = await db.evict_stale(settings.cache_ttl_seconds)
            log.info("ttl_sweep", evicted=len(evicted))

        if args.news or args.all:
            items = await fetch_all_news(limit=50)
            inserted = await db.upsert_news(items)
            log.info("news_refreshed", inserted=inserted)

        if args.openqa:
            tests = scan_tests(args.openqa)
            inserted = await db.upsert_openqa(tests)
            log.info("openqa_refreshed", inserted=inserted)

        if args.packages_path_provided or args.all:
            pkgs = await _load_packages(args)
            log.info("ingest_start", count=len(pkgs), concurrency=args.concurrency)
            results = await _ingest_batch(ingest_service, pkgs, args.concurrency)
            indexed = sum(1 for r in results if r.status is IngestStatus.INDEXED)
            empty = sum(1 for r in results if r.status is IngestStatus.EMPTY)
            invalid = sum(1 for r in results if r.status is IngestStatus.INVALID)
            log.info("ingest_done", indexed=indexed, empty=empty, invalid=invalid)
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--file", "-f", type=Path,
                   help="Allow-list of packages to ingest (one per line, # comments OK)")
    p.add_argument("--concurrency", "-c", type=int, default=settings.worker_concurrency)
    p.add_argument("--news", action="store_true", help="Refresh news feeds")
    p.add_argument("--openqa", type=Path,
                   help="Path to a checked-out os-autoinst-distri-opensuse repo to scan")
    p.add_argument("--sweep", action="store_true",
                   help="Evict packages whose manifest is older than CACHE_TTL_SECONDS")
    p.add_argument("--all", action="store_true",
                   help="Run sweep + news + ingest (uses --file if given, else rpm -qa)")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    args.packages_path_provided = bool(args.file)
    return args


def main() -> int:
    args = _parse_args()
    configure_logging(
        level=logging.DEBUG if args.debug else logging.INFO,
        json_logs=(settings.log_format == "json"),
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
