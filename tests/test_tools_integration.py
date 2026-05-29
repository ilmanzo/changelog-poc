"""DB integration tests for MCP tool functions.

Spins up a real Postgres via testcontainers, patches module-level ``db`` singletons
in each tool module, and calls tool functions directly.  ``ingest_service`` is mocked
so no network fetches happen; seeded packages are marked fresh so the fast-fail
probe never triggers ingestion.

Run with:
    ./scripts/test.sh e2e-db
    PYTHONPATH=. uv run pytest tests/test_tools_integration.py -v -m e2e
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer

from src.db import Database
from src.models import ChangelogEntry, NewsItem, OpenQATest

PG_IMAGE = "pgvector/pgvector:pg17"
_ZERO_VEC = [0.0] * 384
_NOW = datetime(2024, 6, 1, tzinfo=UTC)
_OLD = datetime(2022, 1, 1, tzinfo=UTC)

pytestmark = pytest.mark.asyncio(loop_scope="module")


# ---------------------------------------------------------------------------
# Infrastructure fixtures
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
async def real_db(db_dsn: str):
    db = Database(dsn=db_dsn)
    await db.connect()
    yield db
    await db.close()


def _mock_ingest_service() -> MagicMock:
    svc = MagicMock()
    svc.schedule = MagicMock()
    svc.ingest = AsyncMock(return_value=MagicMock(status=None, synced_at=None))
    return svc


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def seeded_db(real_db: Database):
    """Seed packages and entries used across all tool tests."""
    # curl: CVEs, bugs, changelog entries
    curl_id = await real_db.upsert_package("curl", distro="opensuse")
    curl_entries = [
        ChangelogEntry(version="8.5.0", author="packager", date=_NOW,
                       content="- Fix CVE-2024-1234 buffer overflow in HTTP/2 parser\n- Fix bsc#1220000 TLS handshake crash"),
        ChangelogEntry(version="8.4.0", author="packager", date=_OLD,
                       content="- Fix CVE-2023-9999 use-after-free in DNS resolver"),
        ChangelogEntry(version="8.3.0", author="packager", date=_OLD,
                       content="- Minor performance improvements\n- Update bundled zlib to 1.3.1"),
    ]
    await real_db.upsert_changelog_entries("curl", curl_id, curl_entries, [_ZERO_VEC] * 3, "rpm")
    await real_db.touch_manifest(curl_id, kind="changelog")

    # vim: single entry with a bug ref only
    vim_id = await real_db.upsert_package("vim", distro="opensuse")
    vim_entries = [
        ChangelogEntry(version="9.1.0", author="packager", date=_NOW,
                       content="- Fix boo#1230001 crash when opening certain files"),
    ]
    await real_db.upsert_changelog_entries("vim", vim_id, vim_entries, [_ZERO_VEC], "rpm")
    await real_db.touch_manifest(vim_id, kind="changelog")

    # openssl: has news and openqa tests
    ssl_id = await real_db.upsert_package("openssl", distro="opensuse")
    ssl_entries = [
        ChangelogEntry(version="3.1.5", author="packager", date=_NOW,
                       content="- Fix CVE-2024-5678 memory disclosure in AES-CBC"),
    ]
    await real_db.upsert_changelog_entries("openssl", ssl_id, ssl_entries, [_ZERO_VEC], "rpm")
    await real_db.touch_manifest(ssl_id, kind="changelog")

    # News items
    news_items = [
        NewsItem(title="curl 8.5.0 released", source="opensuse-rss", item_type="release",
                 importance="moderate", content="Security update for curl", url="https://example.com/1",
                 date=_NOW, package_name="curl"),
        NewsItem(title="openssl update available", source="bodhi", item_type="update",
                 importance="critical", content="Critical OpenSSL security fix", url="https://example.com/2",
                 date=_NOW, package_name="openssl"),
    ]
    await real_db.upsert_news(news_items)

    # openQA tests
    tests = [
        OpenQATest(package_name="openssl", test_path="tests/crypto/openssl_basic.pm", summary="Basic TLS"),
        OpenQATest(package_name="openssl", test_path="tests/security/tls_handshake.pm", summary="TLS handshake"),
    ]
    await real_db.upsert_openqa(tests, source="openqa")
    await real_db.touch_manifest(ssl_id, kind="changelog")

    # Package with changes but NO test coverage (for find_untested_changes)
    # Use a recent date so the entry falls within any reasonable `days` window.
    bare_id = await real_db.upsert_package("bare-pkg", distro="opensuse")
    bare_entries = [
        ChangelogEntry(version="1.0.0", author="packager", date=datetime.now(UTC),
                       content="- Fix CVE-2024-7777 remote code execution"),
    ]
    await real_db.upsert_changelog_entries("bare-pkg", bare_id, bare_entries, [_ZERO_VEC], "rpm")
    await real_db.touch_manifest(bare_id, kind="changelog")

    yield real_db


# ---------------------------------------------------------------------------
# Patches applied to all tests in this module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def patch_singletons(seeded_db: Database):
    mock_ingest = _mock_ingest_service()
    mock_embedder = MagicMock()
    mock_embedder.embed_one = AsyncMock(return_value=_ZERO_VEC)

    with (
        patch("src.tools.changelog.db", seeded_db),
        patch("src.tools.news.db", seeded_db),
        patch("src.tools.spec.db", seeded_db),
        patch("src.tools._helpers.db", seeded_db),
        patch("src.tools._helpers.ingest_service", mock_ingest),
        patch("src.tools.changelog.embedder", mock_embedder),
    ):
        yield


# ---------------------------------------------------------------------------
# changelog tools
# ---------------------------------------------------------------------------

@pytest.mark.e2e
async def test_find_cve_match() -> None:
    from src.tools.changelog import find_cve
    result = await find_cve("CVE-2024-1234")
    assert "CVE-2024-1234" in result
    assert "curl" in result


@pytest.mark.e2e
async def test_find_cve_no_match() -> None:
    from src.tools.changelog import find_cve
    result = await find_cve("CVE-1999-0000")
    assert "No mentions" in result


@pytest.mark.e2e
async def test_find_cve_scoped_to_package() -> None:
    from src.tools.changelog import find_cve
    result = await find_cve("CVE-2024-1234", package="curl")
    assert "CVE-2024-1234" in result


@pytest.mark.e2e
async def test_find_cve_scoped_wrong_package() -> None:
    from src.tools.changelog import find_cve
    result = await find_cve("CVE-2024-1234", package="vim")
    assert "No mentions" in result


@pytest.mark.e2e
async def test_find_cve_invalid_id() -> None:
    from src.tools.changelog import find_cve
    result = await find_cve("not-a-cve")
    assert "Invalid" in result or "CVE" in result


@pytest.mark.e2e
async def test_list_cves_returns_all() -> None:
    from src.tools.changelog import list_cves
    result = await list_cves("curl")
    assert "CVE-2024-1234" in result
    assert "CVE-2023-9999" in result


@pytest.mark.e2e
async def test_list_cves_since_filter() -> None:
    from src.tools.changelog import list_cves
    result = await list_cves("curl", since="2024-01-01")
    assert "CVE-2024-1234" in result
    assert "CVE-2023-9999" not in result


@pytest.mark.e2e
async def test_list_cves_empty_package() -> None:
    from src.tools.changelog import list_cves
    result = await list_cves("vim")
    assert "No CVE" in result


@pytest.mark.e2e
async def test_find_bug_match() -> None:
    from src.tools.changelog import find_bug
    result = await find_bug("bsc#1220000")
    assert "bsc#1220000" in result


@pytest.mark.e2e
async def test_find_bug_no_match() -> None:
    from src.tools.changelog import find_bug
    result = await find_bug("bsc#9999999")
    assert "No mentions" in result


@pytest.mark.e2e
async def test_find_bug_boo_prefix() -> None:
    from src.tools.changelog import find_bug
    result = await find_bug("boo#1230001")
    assert "boo#1230001" in result
    assert "vim" in result


@pytest.mark.e2e
async def test_list_bugs_returns_entries() -> None:
    from src.tools.changelog import list_bugs
    result = await list_bugs("curl")
    assert "bsc#1220000" in result


@pytest.mark.e2e
async def test_list_bugs_no_bugs() -> None:
    from src.tools.changelog import list_bugs
    result = await list_bugs("openssl")
    assert "No bug" in result


@pytest.mark.e2e
async def test_fts_search_finds_results() -> None:
    from src.tools.changelog import fts_search
    result = await fts_search("buffer overflow")
    assert "curl" in result or "CVE" in result


@pytest.mark.e2e
async def test_fts_search_no_match() -> None:
    from src.tools.changelog import fts_search
    result = await fts_search("xyzzy_nonexistent_term_12345")
    assert "No FTS matches" in result


@pytest.mark.e2e
async def test_fts_search_with_since() -> None:
    from src.tools.changelog import fts_search
    result = await fts_search("security", since="2024-01-01")
    assert isinstance(result, str)


@pytest.mark.e2e
async def test_semantic_search_runs() -> None:
    from src.tools.changelog import semantic_search
    result = await semantic_search("TLS vulnerability fix")
    assert isinstance(result, str)


@pytest.mark.e2e
async def test_get_recent_releases_known_package() -> None:
    from src.tools.changelog import get_recent_releases
    result = await get_recent_releases("curl", n=2)
    assert "curl" in result or "8.5.0" in result or "8.4.0" in result


@pytest.mark.e2e
async def test_get_recent_releases_not_found() -> None:
    from src.tools.changelog import get_recent_releases
    result = await get_recent_releases("nonexistent-pkg-xyzzy")
    assert "queued" in result.lower() or "not found" in result.lower()


@pytest.mark.e2e
async def test_compare_versions_known_package() -> None:
    from src.tools.changelog import compare_versions
    result = await compare_versions("curl")
    assert isinstance(result, str)


@pytest.mark.e2e
async def test_get_changes_in_range() -> None:
    from src.tools.changelog import get_changes_in_range
    result = await get_changes_in_range("curl", since="2024-01-01", until="2025-01-01")
    assert "curl" in result or "8.5.0" in result


# ---------------------------------------------------------------------------
# news tools
# ---------------------------------------------------------------------------

@pytest.mark.e2e
async def test_get_news_all() -> None:
    from src.tools.news import get_news
    result = await get_news()
    assert "curl" in result or "openssl" in result


@pytest.mark.e2e
async def test_get_news_scoped_package() -> None:
    from src.tools.news import get_news
    result = await get_news(package="curl")
    assert "curl" in result


@pytest.mark.e2e
async def test_get_news_missing_package() -> None:
    from src.tools.news import get_news
    result = await get_news(package="nonexistent-pkg-xyzzy")
    assert "No news" in result


@pytest.mark.e2e
async def test_get_sync_status_known() -> None:
    from src.tools.news import get_sync_status
    result = await get_sync_status(package="curl")
    assert "curl" in result


@pytest.mark.e2e
async def test_get_sync_status_missing() -> None:
    from src.tools.news import get_sync_status
    result = await get_sync_status(package="nonexistent-pkg-xyzzy")
    assert "No sync" in result


@pytest.mark.e2e
async def test_find_untested_changes_returns_bare_pkg() -> None:
    from src.tools.news import find_untested_changes
    result = await find_untested_changes(days=180, limit=20)
    # bare-pkg has changes but no test coverage
    assert "bare-pkg" in result


@pytest.mark.e2e
async def test_find_untested_changes_openssl_excluded() -> None:
    from src.tools.news import find_untested_changes
    result = await find_untested_changes(days=180, limit=20)
    # openssl has openqa tests — should not appear
    assert "openssl" not in result
