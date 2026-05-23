#!/usr/bin/env python
"""Latency benchmark for the two hot paths: ingest and semantic search.

Real packages, real Postgres+pgvector. End-to-end wall time.

    uv run scripts/bench.py ingest
    uv run scripts/bench.py search --concurrency 8
    uv run scripts/bench.py both
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
import sys
import time
from dataclasses import dataclass

import structlog

from src import embedder
from src.config import settings
from src.db import Database
from src.ingest import IngestService
from src.logging_config import configure_logging
from src.rpm_manager import RPMManager
from src.sources import (
    FetchStrategy,
    GiteaSource,
    ObsSource,
    RpmSource,
    SourceRegistry,
)

DEFAULT_INGEST_PACKAGES = [
    "chrony", "openssh", "systemd", "bash", "curl",
    "vim", "nginx", "postgresql", "git", "kernel-default",
]

DEFAULT_SEARCH_QUERIES = [
    "security vulnerability fix",
    "CVE buffer overflow",
    "TLS protocol upgrade",
    "performance regression",
    "memory leak fix",
    "IPv6 networking change",
    "deprecation removal",
    "API breaking change",
    "concurrency race condition",
    "build system update",
]


@dataclass
class Sample:
    label: str
    elapsed_ms: float
    ok: bool
    note: str = ""


def _percentiles(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"n": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "mean": 0.0}
    s = sorted(samples)
    return {
        "n": len(s),
        "mean": statistics.fmean(s),
        "p50": statistics.median(s),
        "p95": s[max(0, int(len(s) * 0.95) - 1)],
        "p99": s[max(0, int(len(s) * 0.99) - 1)],
        "max": s[-1],
    }


def _print_report(title: str, samples: list[Sample]) -> None:
    ok = [s for s in samples if s.ok]
    fail = [s for s in samples if not s.ok]
    pct = _percentiles([s.elapsed_ms for s in ok])

    print(f"\n=== {title} ===")
    print(f"  n={pct['n']}/{len(samples)}  failed={len(fail)}")
    if pct["n"]:
        for k in ("mean", "p50", "p95", "p99", "max"):
            print(f"  {k:<4} = {pct[k]:8.1f} ms")
    print()
    print(f"  {'label':<30} {'ms':>10}  status")
    print(f"  {'-' * 30} {'-' * 10}  {'-' * 20}")
    for s in samples:
        status = "OK" if s.ok else f"FAIL: {s.note}"
        print(f"  {s.label:<30} {s.elapsed_ms:>10.1f}  {status}")


async def bench_ingest(
    service: IngestService, packages: list[str], concurrency: int
) -> list[Sample]:
    sem = asyncio.Semaphore(concurrency)

    async def _one(pkg: str) -> Sample:
        async with sem:
            t0 = time.perf_counter()
            try:
                result = await service.ingest(pkg)
                dt = (time.perf_counter() - t0) * 1000
                if result.status.value == "indexed":
                    return Sample(pkg, dt, True)
                return Sample(pkg, dt, False, note=result.status.value)
            except Exception as exc:
                dt = (time.perf_counter() - t0) * 1000
                return Sample(pkg, dt, False, note=type(exc).__name__)

    return await asyncio.gather(*(_one(p) for p in packages))


async def bench_search(db: Database, queries: list[str], concurrency: int) -> list[Sample]:
    sem = asyncio.Semaphore(concurrency)

    async def _one(q: str) -> Sample:
        async with sem:
            t0 = time.perf_counter()
            try:
                emb = await embedder.embed_one(q)
                hits = await db.semantic_search(emb, limit=5) if emb else []
                dt = (time.perf_counter() - t0) * 1000
                label = q if len(q) <= 28 else q[:25] + "..."
                return Sample(label, dt, True, note=f"{len(hits)} hits")
            except Exception as exc:
                dt = (time.perf_counter() - t0) * 1000
                return Sample(q[:28], dt, False, note=type(exc).__name__)

    return await asyncio.gather(*(_one(q) for q in queries))


async def _run(args: argparse.Namespace) -> None:
    db = Database()
    await db.connect()
    rpm_mgr = RPMManager()
    registry = SourceRegistry(
        sources=[RpmSource(rpm_mgr), ObsSource(), GiteaSource()],
        strategy=FetchStrategy(settings.fetch_strategy),
    )
    service = IngestService(registry, db)
    try:
        if args.mode in ("ingest", "both"):
            samples = await bench_ingest(service, args.packages, args.concurrency)
            _print_report("INGEST  (cold + cache)", samples)
        if args.mode in ("search", "both"):
            samples = await bench_search(db, args.queries, args.concurrency)
            _print_report("SEARCH  (warm)", samples)
    finally:
        await registry.close()
        await db.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("mode", choices=["ingest", "search", "both"])
    p.add_argument("--packages", nargs="*", default=DEFAULT_INGEST_PACKAGES)
    p.add_argument("--queries", nargs="*", default=DEFAULT_SEARCH_QUERIES)
    p.add_argument("--concurrency", "-c", type=int, default=2)
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    configure_logging(
        level=logging.DEBUG if args.debug else logging.WARNING,
        json_logs=False,
    )
    log = structlog.get_logger("bench")
    log.warning("starting", mode=args.mode, concurrency=args.concurrency)
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
