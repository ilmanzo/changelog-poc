"""Integration tests for src/db.py — require a running PostgreSQL with pgvector.

These tests use testcontainers and are marked @pytest.mark.e2e so they are
deselected by default (addopts = "-m 'not e2e'" in pyproject.toml).

Run with:
    export DOCKER_HOST=unix:///run/user/$UID/podman/podman.sock
    PYTHONPATH=. uv run pytest tests/test_db.py -v -m e2e
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer

from src.db import Database
from src.models import ChangelogEntry

pytestmark = pytest.mark.asyncio(loop_scope="module")

PG_IMAGE = "pgvector/pgvector:pg17"
_ZERO_VEC = [0.0] * 384
_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
_OLD = datetime(2022, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Session-scoped container + database
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def db_dsn():
    with PostgresContainer(PG_IMAGE) as container:
        yield (
            f"postgresql://{container.username}:{container.password}"
            f"@{container.get_container_host_ip()}"
            f":{container.get_exposed_port(5432)}"
            f"/{container.dbname}"
        )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database(db_dsn: str):
    db = Database(dsn=db_dsn)
    await db.connect()
    yield db
    await db.close()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _entry(content: str, version: str = "1.0", date: datetime = _NOW) -> ChangelogEntry:
    return ChangelogEntry(version=version, author="tester", date=date, content=content)


# ---------------------------------------------------------------------------
# packages
# ---------------------------------------------------------------------------
@pytest.mark.e2e
async def test_upsert_and_get_package(database: Database) -> None:
    pkg_id = await database.upsert_package("test-vim", distro="opensuse")
    assert isinstance(pkg_id, int) and pkg_id > 0

    got = await database.get_package_id("test-vim")
    assert got == pkg_id


@pytest.mark.e2e
async def test_upsert_package_idempotent(database: Database) -> None:
    id1 = await database.upsert_package("test-idempotent", distro="opensuse")
    id2 = await database.upsert_package("test-idempotent", distro="opensuse")
    assert id1 == id2


@pytest.mark.e2e
async def test_get_package_id_missing_returns_none(database: Database) -> None:
    result = await database.get_package_id("nonexistent-xyzzy-abc")
    assert result is None


# ---------------------------------------------------------------------------
# changelog_entries — dedup
# ---------------------------------------------------------------------------
@pytest.mark.e2e
async def test_upsert_entries_dedup(database: Database) -> None:
    pkg_id = await database.upsert_package("test-dedup")
    entry = _entry("Fix security issue CVE-2024-9999")
    # Insert same entry twice → should not duplicate
    await database.upsert_changelog_entries("test-dedup", pkg_id, [entry], [_ZERO_VEC], "rpm")
    await database.upsert_changelog_entries("test-dedup", pkg_id, [entry], [_ZERO_VEC], "rpm")

    rows = await database.fetch_entries(pkg_id)
    assert len(rows) == 1


@pytest.mark.e2e
async def test_fetch_entries_ordered_by_date(database: Database) -> None:
    pkg_id = await database.upsert_package("test-order")
    old = _entry("Old fix", date=_OLD)
    new = _entry("New fix", date=_NOW)
    await database.upsert_changelog_entries("test-order", pkg_id, [old, new], [_ZERO_VEC, _ZERO_VEC], "rpm")

    rows = await database.fetch_entries(pkg_id)
    # Should be newest first
    assert rows[0]["entry_date"] >= rows[-1]["entry_date"]


# ---------------------------------------------------------------------------
# FTS search
# ---------------------------------------------------------------------------
@pytest.mark.e2e
async def test_fts_search_finds_matching_entry(database: Database) -> None:
    pkg_id = await database.upsert_package("test-fts")
    entry = _entry("Critical security vulnerability fixed in TLS handshake")
    await database.upsert_changelog_entries("test-fts", pkg_id, [entry], [_ZERO_VEC], "rpm")

    rows = await database.fts_search("security vulnerability", limit=10)
    contents = [r["content"] for r in rows]
    assert any("TLS" in c for c in contents)


@pytest.mark.e2e
async def test_fts_search_with_since_filter(database: Database) -> None:
    pkg_id = await database.upsert_package("test-fts-since")
    recent = _entry("Recent memory leak fix", date=_NOW)
    old = _entry("Old memory corruption bug", date=_OLD)
    await database.upsert_changelog_entries(
        "test-fts-since", pkg_id, [recent, old], [_ZERO_VEC, _ZERO_VEC], "rpm"
    )

    cutoff = datetime(2023, 1, 1, tzinfo=timezone.utc)
    rows = await database.fts_search("memory", since=cutoff, limit=10)

    # Only the 2024 entry should appear (old 2022 entry excluded)
    for r in rows:
        if r["package"] == "test-fts-since":
            assert r["entry_date"].year >= 2023


# ---------------------------------------------------------------------------
# CVE / bug queries
# ---------------------------------------------------------------------------
@pytest.mark.e2e
async def test_list_package_cves_finds_cve_entry(database: Database) -> None:
    pkg_id = await database.upsert_package("test-cve-list")
    cve_entry = _entry("Fix CVE-2024-1234 buffer overflow in parser")
    clean_entry = _entry("Routine dependency update")
    await database.upsert_changelog_entries(
        "test-cve-list", pkg_id, [cve_entry, clean_entry], [_ZERO_VEC, _ZERO_VEC], "rpm"
    )

    rows = await database.list_package_cves("test-cve-list")
    assert len(rows) == 1
    assert "CVE-2024-1234" in rows[0]["content"]


@pytest.mark.e2e
async def test_list_package_bugs_finds_bsc_entry(database: Database) -> None:
    pkg_id = await database.upsert_package("test-bsc-list")
    bsc_entry = _entry("Fix bsc#1260905 crash on startup")
    clean_entry = _entry("Minor code cleanup")
    await database.upsert_changelog_entries(
        "test-bsc-list", pkg_id, [bsc_entry, clean_entry], [_ZERO_VEC, _ZERO_VEC], "rpm"
    )

    rows = await database.list_package_bugs("test-bsc-list")
    assert len(rows) == 1
    assert "bsc#1260905" in rows[0]["content"]


@pytest.mark.e2e
async def test_find_bug_specific(database: Database) -> None:
    pkg_id = await database.upsert_package("test-find-bug")
    entry = _entry("Fix boo#9876543 race condition in scheduler")
    await database.upsert_changelog_entries(
        "test-find-bug", pkg_id, [entry], [_ZERO_VEC], "rpm"
    )

    rows = await database.find_bug("boo#9876543")
    assert any("boo#9876543" in r["content"] for r in rows)


@pytest.mark.e2e
async def test_find_bug_absent_returns_empty(database: Database) -> None:
    rows = await database.find_bug("bsc#0000000")
    assert rows == []


# ---------------------------------------------------------------------------
# Manifest / freshness
# ---------------------------------------------------------------------------
@pytest.mark.e2e
async def test_touch_manifest_and_is_fresh(database: Database) -> None:
    pkg_id = await database.upsert_package("test-manifest")
    await database.touch_manifest(pkg_id)
    assert await database.is_fresh(pkg_id, ttl_seconds=3600)


@pytest.mark.e2e
async def test_is_fresh_false_without_manifest(database: Database) -> None:
    pkg_id = await database.upsert_package("test-no-manifest")
    assert not await database.is_fresh(pkg_id, ttl_seconds=3600)


@pytest.mark.e2e
async def test_get_synced_at_returns_timestamp(database: Database) -> None:
    pkg_id = await database.upsert_package("test-synced-at")
    await database.touch_manifest(pkg_id)
    ts = await database.get_synced_at(pkg_id)
    assert ts is not None
    assert ts.tzinfo is not None


@pytest.mark.e2e
async def test_get_synced_at_missing_returns_none(database: Database) -> None:
    pkg_id = await database.upsert_package("test-no-synced-at")
    assert await database.get_synced_at(pkg_id) is None


# ---------------------------------------------------------------------------
# Semantic search (zero vector — just validates the query runs)
# ---------------------------------------------------------------------------
@pytest.mark.e2e
async def test_semantic_search_runs_without_error(database: Database) -> None:
    pkg_id = await database.upsert_package("test-semantic")
    entry = _entry("OpenSSL TLS handshake fix")
    await database.upsert_changelog_entries(
        "test-semantic", pkg_id, [entry], [_ZERO_VEC], "rpm"
    )

    rows = await database.semantic_search(_ZERO_VEC, limit=5)
    # Zero vector → results may be arbitrary, but query should succeed
    assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# get_upstream_url
# ---------------------------------------------------------------------------
@pytest.mark.e2e
async def test_get_upstream_url_returns_url(database: Database) -> None:
    await database.upsert_package(
        "test-upstream", upstream_url="https://github.com/vim/vim"
    )
    url = await database.get_upstream_url("test-upstream")
    assert url == "https://github.com/vim/vim"


@pytest.mark.e2e
async def test_get_upstream_url_missing_returns_none(database: Database) -> None:
    url = await database.get_upstream_url("nonexistent-pkg-xyzzy")
    assert url is None
