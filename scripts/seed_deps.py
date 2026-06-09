#!/usr/bin/env python
"""Bootstrap the `deps` table from the local rpmdb.

    uv run scripts/seed_deps.py                    # all installed packages
    uv run scripts/seed_deps.py systemd chrony     # specific packages
    uv run scripts/seed_deps.py -c 16              # bump concurrency

Walks `rpm -qa`, resolves each package's requires via `rpm -qR + --whatprovides`
(already done by `RPMManager.get_dependencies`), upserts the package row in the
`opensuse` distro, then writes deps with `kind='requires'`. Existing rows for
the package are replaced atomically.

Skip silently when a package is not installed (RuntimeError from `-qR`).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

import structlog

from src.config import settings
from src.db import Database
from src.logging_config import configure_logging
from src.rpm_manager import RPMManager


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("packages", nargs="*", help="Package names (default: rpm -qa)")
    p.add_argument("--concurrency", "-c", type=int, default=8, help="Max concurrent rpm queries")
    p.add_argument("--debug", action="store_true", help="Verbose logging")
    return p.parse_args()


async def _seed_one(
    pkg: str,
    rpm_mgr: RPMManager,
    db: Database,
    log: structlog.stdlib.BoundLogger,
) -> int:
    try:
        deps = await rpm_mgr.get_dependencies(pkg)
    except RuntimeError:
        return 0
    pkg_id = await db.upsert_package(name=pkg, distro="opensuse")
    await db.replace_deps(pkg_id, deps, kind="requires")
    log.debug("seeded", package=pkg, deps=len(deps))
    return len(deps)


async def _run(packages: list[str], concurrency: int) -> tuple[int, int, int]:
    db = Database()
    await db.connect()
    rpm_mgr = RPMManager()
    log = structlog.get_logger("seed-deps")

    if not packages:
        packages = await rpm_mgr.list_installed_packages()
        log.info("loaded_local", count=len(packages))

    sem = asyncio.Semaphore(concurrency)
    done = 0
    total = len(packages)
    t0 = time.perf_counter()

    async def _one(pkg: str) -> int:
        nonlocal done
        async with sem:
            n = await _seed_one(pkg, rpm_mgr, db, log)
            done += 1
            if done % 250 == 0:
                log.info("progress", done=done, total=total, elapsed_s=round(time.perf_counter() - t0, 1))
            return n

    try:
        results = await asyncio.gather(*(_one(p) for p in packages), return_exceptions=True)
    finally:
        await db.close()

    seeded = sum(1 for r in results if isinstance(r, int) and r > 0)
    skipped = sum(1 for r in results if isinstance(r, int) and r == 0)
    errors = sum(1 for r in results if isinstance(r, BaseException))
    return seeded, skipped, errors


def main() -> int:
    args = _parse_args()
    configure_logging(
        level=logging.DEBUG if args.debug else logging.INFO,
        json_logs=(settings.log_format == "json"),
    )
    log = structlog.get_logger("seed-deps")

    seeded, skipped, errors = asyncio.run(_run(args.packages, args.concurrency))
    log.info("done", seeded=seeded, skipped_not_installed=skipped, errors=errors)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
