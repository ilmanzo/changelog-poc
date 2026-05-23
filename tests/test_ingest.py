"""Unit tests for src/ingest.py — mock registry, db, and embedder."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.ingest import IngestResult, IngestService, IngestStatus, validate_package_name
from src.models import ChangelogEntry
from src.sources.base import FetchResult, SourceError

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)

_ENTRIES = [
    ChangelogEntry(version="1.0", author="dev", date=_NOW, content="Fix CVE-2024-1234"),
    ChangelogEntry(version="1.0", author="dev", date=_NOW, content="Add feature X"),
    ChangelogEntry(version="0.9", author="dev", date=_NOW, content="Initial release"),
]

_RESULT = FetchResult(entries=_ENTRIES, source_name="rpm", upstream_url=None)
_EMPTY_RESULT = FetchResult(entries=[], source_name="rpm")


def _make_registry(result: FetchResult | Exception) -> AsyncMock:
    reg = AsyncMock()
    if isinstance(result, Exception):
        reg.fetch = AsyncMock(side_effect=result)
    else:
        reg.fetch = AsyncMock(return_value=result)
    return reg


def _make_db(upsert_returns: int = 3) -> AsyncMock:
    db = AsyncMock()
    db.upsert_package = AsyncMock(return_value=1)
    db.upsert_changelog_entries = AsyncMock(return_value=upsert_returns)
    db.touch_manifest = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# validate_package_name
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", [
    "vim", "curl", "python3-setuptools", "lib64openssl1_0_0", "gcc.c++",
    "python3+", "libfoo.bar",
])
def test_validate_package_name_valid(name: str) -> None:
    validate_package_name(name)  # should not raise


@pytest.mark.parametrize("name", [
    "../etc/passwd",
    "; rm -rf /",
    "foo bar",
    "foo!bar",
    "",
])
def test_validate_package_name_invalid(name: str) -> None:
    with pytest.raises(ValueError):
        validate_package_name(name)


# ---------------------------------------------------------------------------
# IngestService.ingest — happy path
# ---------------------------------------------------------------------------
async def test_ingest_success_returns_indexed() -> None:
    reg = _make_registry(_RESULT)
    db = _make_db(upsert_returns=3)

    with patch("src.ingest.embedder.embed_batch", new=AsyncMock(return_value=[[0.1] * 384] * 3)):
        svc = IngestService(reg, db)
        result = await svc.ingest("vim")

    assert result.status is IngestStatus.INDEXED
    assert result.package == "vim"
    assert result.source == "rpm"
    assert result.entries == 3


async def test_ingest_calls_touch_manifest() -> None:
    reg = _make_registry(_RESULT)
    db = _make_db()

    with patch("src.ingest.embedder.embed_batch", new=AsyncMock(return_value=[[]] * 3)):
        svc = IngestService(reg, db)
        await svc.ingest("vim")

    db.touch_manifest.assert_awaited_once_with(1)  # package_id=1


async def test_ingest_embed_failure_falls_back_to_empty_vectors() -> None:
    """If embed_batch returns [] (failure), ingest still proceeds with null embeddings."""
    reg = _make_registry(_RESULT)
    db = _make_db()

    with patch("src.ingest.embedder.embed_batch", new=AsyncMock(return_value=[])):
        svc = IngestService(reg, db)
        result = await svc.ingest("vim")

    # Should still index with empty embeddings
    assert result.status is IngestStatus.INDEXED


# ---------------------------------------------------------------------------
# IngestService.ingest — empty result
# ---------------------------------------------------------------------------
async def test_ingest_empty_source_returns_empty() -> None:
    reg = _make_registry(_EMPTY_RESULT)
    db = _make_db()

    svc = IngestService(reg, db)
    result = await svc.ingest("nonexistent_xyzzy")

    assert result.status is IngestStatus.EMPTY
    db.upsert_package.assert_not_awaited()


# ---------------------------------------------------------------------------
# IngestService.ingest — invalid package name
# ---------------------------------------------------------------------------
async def test_ingest_invalid_name_returns_invalid() -> None:
    reg = _make_registry(_RESULT)
    db = _make_db()

    svc = IngestService(reg, db)
    result = await svc.ingest("../bad/path")

    assert result.status is IngestStatus.INVALID
    reg.fetch.assert_not_awaited()


# ---------------------------------------------------------------------------
# IngestService.ingest — elapsed time recorded
# ---------------------------------------------------------------------------
async def test_ingest_records_elapsed_time() -> None:
    reg = _make_registry(_RESULT)
    db = _make_db()

    with patch("src.ingest.embedder.embed_batch", new=AsyncMock(return_value=[[]] * 3)):
        svc = IngestService(reg, db)
        result = await svc.ingest("vim")

    assert result.elapsed_s >= 0.0
