"""rpm-mcp doctor -- health check + opt-in auto-repair.

Runs 4 check bundles (core, client, runtime, network) and prints OK/WARN/FAIL
status per check. With --fix, attempts safe remediation for the failures that
have known fixes.

Exit code: 0 if every check is OK or WARN; 1 if any check is FAIL.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import aiohttp
import asyncpg

# Allow `python scripts/doctor.py` to find the src/ package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import embedder
from src.config import settings
from src.db import MIGRATIONS_DIR

Status = Literal["OK", "WARN", "FAIL"]
REPO_ROOT = Path(__file__).resolve().parent.parent
CONTAINER_NAME = "rpm-mcp-postgres"


@dataclass
class Check:
    name: str
    status: Status
    message: str
    fix: Callable[[], Awaitable[None]] | None = None
    fix_label: str = ""


def _label(status: Status) -> str:
    return {"OK": "[OK]   ", "WARN": "[WARN] ", "FAIL": "[FAIL] "}[status]


# ---------------------------------------------------------------------------
# Bundle: Core (container + DB + migrations)
# ---------------------------------------------------------------------------
async def check_container() -> Check:
    r = await asyncio.to_thread(
        subprocess.run,
        ["podman", "ps", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Status}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    out = r.stdout.strip()
    if "Up" in out:
        return Check("container", "OK", f"{CONTAINER_NAME} up ({out})")

    async def fix_start() -> None:
        await asyncio.to_thread(
            subprocess.run,
            [str(REPO_ROOT / "infra" / "infra.sh"), "start"],
            check=True,
        )

    return Check(
        "container",
        "FAIL",
        f"{CONTAINER_NAME} not running",
        fix=fix_start,
        fix_label="start container",
    )


async def check_db() -> Check:
    try:
        conn = await asyncpg.connect(settings.database_url, timeout=5)
        await conn.close()
    except Exception as e:
        # Strip DSN from error
        msg = str(e).split("dsn=")[0].strip()
        return Check("db_connect", "FAIL", f"cannot connect: {msg or type(e).__name__}")
    return Check("db_connect", "OK", "Postgres reachable")


async def check_migrations() -> Check:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    expected = {f.name for f in files}
    try:
        conn = await asyncpg.connect(settings.database_url, timeout=5)
        try:
            rows = await conn.fetch("SELECT version FROM schema_migrations")
        finally:
            await conn.close()
    except asyncpg.UndefinedTableError:
        return Check(
            "migrations",
            "FAIL",
            f"schema_migrations table missing (expected {len(expected)} files)",
        )
    except Exception as e:
        return Check("migrations", "FAIL", f"cannot query: {type(e).__name__}")

    applied = {r["version"] for r in rows}
    missing = expected - applied
    if missing:
        return Check(
            "migrations",
            "FAIL",
            f"{len(missing)} unapplied: {', '.join(sorted(missing))}",
        )
    return Check("migrations", "OK", f"{len(expected)} migrations applied")


# ---------------------------------------------------------------------------
# Bundle: Client (MCP registration)
# ---------------------------------------------------------------------------
async def check_clients() -> Check:
    r = await asyncio.to_thread(
        subprocess.run,
        [str(REPO_ROOT / "scripts" / "register.sh"), "status", "all"],
        capture_output=True,
        text=True,
        check=False,
    )
    out = r.stdout.strip()
    # A line counts as a positive registration if it contains "registered" but NOT "not registered".
    registered_lines = [
        line for line in out.splitlines() if "registered" in line and "not registered" not in line
    ]
    summary = " | ".join(line.strip() for line in out.splitlines() if line.strip())[:200]

    if registered_lines:
        return Check("mcp_clients", "OK", summary or "client(s) registered")

    async def fix_register() -> None:
        await asyncio.to_thread(
            subprocess.run,
            [str(REPO_ROOT / "scripts" / "register.sh"), "add", "all"],
            check=False,  # any of the two might not be installed
        )

    return Check(
        "mcp_clients",
        "WARN",
        "no MCP client registered (Claude or gemini-cli)",
        fix=fix_register,
        fix_label="register with all available clients",
    )


# ---------------------------------------------------------------------------
# Bundle: Runtime (model cache + ingested data)
# ---------------------------------------------------------------------------
def _fastembed_cache_dir() -> Path:
    return Path(os.environ.get("FASTEMBED_CACHE_DIR", Path.home() / ".cache" / "fastembed"))


async def check_fastembed_model() -> Check:
    cache = _fastembed_cache_dir()
    if cache.exists() and any(cache.rglob("*.onnx")):
        return Check("fastembed_model", "OK", f"model cached under {cache}")

    async def fix_warmup() -> None:
        # Calling embed_one triggers download into the default cache.
        await embedder.embed_one("warmup")

    return Check(
        "fastembed_model",
        "WARN",
        "fastembed model not yet cached (will download on first semantic_search)",
        fix=fix_warmup,
        fix_label="pre-download fastembed model",
    )


async def check_packages_populated() -> Check:
    try:
        conn = await asyncpg.connect(settings.database_url, timeout=5)
        try:
            n = await conn.fetchval("SELECT COUNT(*) FROM packages")
        finally:
            await conn.close()
    except Exception as e:
        return Check("packages_populated", "FAIL", f"cannot query: {type(e).__name__}")
    if n and n > 0:
        return Check("packages_populated", "OK", f"{n} packages ingested")
    return Check(
        "packages_populated",
        "WARN",
        "DB is empty; run `uv run scripts/ingest.py vim curl openssl` to seed it",
    )


# ---------------------------------------------------------------------------
# Bundle: Network (external sources)
# ---------------------------------------------------------------------------
async def _probe(url: str, session: aiohttp.ClientSession) -> tuple[bool, str]:
    try:
        async with session.get(url, allow_redirects=True) as resp:
            return resp.status < 500, f"HTTP {resp.status}"
    except Exception as e:
        return False, type(e).__name__


async def check_network() -> Check:
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        results = await asyncio.gather(
            _probe(settings.testcatalog_url, session),
            _probe("https://api.opensuse.org/public/", session),
            return_exceptions=False,
        )
    tc_ok, tc_msg = results[0]
    obs_ok, obs_msg = results[1]
    summary = f"TestCatalog: {tc_msg} | OBS: {obs_msg}"
    if tc_ok and obs_ok:
        return Check("network", "OK", summary)
    return Check("network", "WARN", summary)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
async def run_checks() -> list[Check]:
    bundles = [
        ("Core", [check_container(), check_db(), check_migrations()]),
        ("Client", [check_clients()]),
        ("Runtime", [check_fastembed_model(), check_packages_populated()]),
        ("Network", [check_network()]),
    ]
    out: list[Check] = []
    for label, coros in bundles:
        print(f"\n== {label} ==")
        results = await asyncio.gather(*coros, return_exceptions=False)
        for c in results:
            print(f"  {_label(c.status)} {c.name:<22} {c.message}")
            out.append(c)
    return out


async def main_async(do_fix: bool) -> int:
    checks = await run_checks()

    if do_fix:
        fixable = [c for c in checks if c.status in ("FAIL", "WARN") and c.fix is not None]
        if not fixable:
            print("\nNothing to fix.")
        else:
            print(f"\n== Fixing {len(fixable)} issue(s) ==")
            for c in fixable:
                print(f"  -> {c.fix_label} ({c.name})...")
                try:
                    assert c.fix is not None
                    await c.fix()
                except Exception as e:
                    print(f"     fix failed: {e}")
            print("\n== Re-running checks ==")
            checks = await run_checks()

    failed = sum(1 for c in checks if c.status == "FAIL")
    warned = sum(1 for c in checks if c.status == "WARN")
    okayed = sum(1 for c in checks if c.status == "OK")
    print(f"\nSummary: {okayed} OK, {warned} WARN, {failed} FAIL")
    return 1 if failed > 0 else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="rpm-mcp doctor",
        description="Health check for the rpm-mcp deployment.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Attempt safe auto-repairs for failing checks (start container, register clients, "
        "pre-download fastembed model).",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(do_fix=args.fix)))


if __name__ == "__main__":
    main()
