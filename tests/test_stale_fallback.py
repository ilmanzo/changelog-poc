"""Unit tests for P2+DD3: stale-data fallback + ⚠ warning banner."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.ingest import IngestService, IngestStatus
from src.models import ChangelogEntry
from src.sources.base import FetchResult

_SYNCED_AT = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
_CACHED_ROWS = [
    {"version": "1.0", "author": "dev", "entry_date": _SYNCED_AT, "content": "cached entry"},
    {"version": "0.9", "author": "dev", "entry_date": _SYNCED_AT, "content": "old entry"},
]


def _registry_returns(result: FetchResult) -> AsyncMock:
    reg = AsyncMock()
    reg.fetch = AsyncMock(return_value=result)
    return reg


def _db_with_cache(rows: list[dict] | None) -> AsyncMock:
    db = AsyncMock()
    db.get_package_id = AsyncMock(return_value=42 if rows else None)
    db.fetch_entries = AsyncMock(return_value=rows or [])
    db.get_synced_at = AsyncMock(return_value=_SYNCED_AT if rows else None)
    db.upsert_package = AsyncMock(return_value=42)
    db.upsert_changelog_entries = AsyncMock(return_value=0)
    db.touch_manifest = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# IngestService — STALE branch
# ---------------------------------------------------------------------------
async def test_ingest_fetch_failed_with_cache_returns_stale() -> None:
    reg = _registry_returns(FetchResult(entries=[], source_name="none", fetch_failed=True))
    db = _db_with_cache(_CACHED_ROWS)

    svc = IngestService(reg, db)
    result = await svc.ingest("vim")

    assert result.status is IngestStatus.STALE
    assert result.entries == 2
    assert result.synced_at == _SYNCED_AT
    db.upsert_package.assert_not_awaited()
    db.touch_manifest.assert_not_awaited()


async def test_ingest_fetch_failed_no_cache_returns_empty() -> None:
    reg = _registry_returns(FetchResult(entries=[], source_name="none", fetch_failed=True))
    db = _db_with_cache(None)

    svc = IngestService(reg, db)
    result = await svc.ingest("nonexistent")

    assert result.status is IngestStatus.EMPTY
    assert result.synced_at is None


async def test_ingest_empty_without_fetch_failure_does_not_fall_back() -> None:
    """Genuine empty (no error) must remain EMPTY — no stale probe."""
    reg = _registry_returns(FetchResult(entries=[], source_name="rpm", fetch_failed=False))
    db = _db_with_cache(_CACHED_ROWS)

    svc = IngestService(reg, db)
    result = await svc.ingest("vim")

    assert result.status is IngestStatus.EMPTY
    db.get_synced_at.assert_not_awaited()


async def test_ingest_indexed_does_not_consult_cache() -> None:
    entry = ChangelogEntry(version="2.0", author="x", date=_SYNCED_AT, content="new")
    reg = _registry_returns(FetchResult(entries=[entry], source_name="rpm"))
    db = _db_with_cache(_CACHED_ROWS)
    db.upsert_changelog_entries = AsyncMock(return_value=1)

    svc = IngestService(reg, db)
    from unittest.mock import patch
    with patch("src.ingest.embedder.embed_batch", new=AsyncMock(return_value=[[]])):
        result = await svc.ingest("vim")

    assert result.status is IngestStatus.INDEXED
    db.get_synced_at.assert_not_awaited()


# ---------------------------------------------------------------------------
# mcp_server — banner plumbing
# ---------------------------------------------------------------------------
def test_stale_banner_includes_timestamp() -> None:
    from mcp_server import _stale_banner

    msg = _stale_banner(_SYNCED_AT)

    assert msg.startswith("⚠")
    assert "2024-01-15" in msg
    assert msg.endswith("\n\n")


def test_stale_banner_handles_missing_timestamp() -> None:
    from mcp_server import _stale_banner

    msg = _stale_banner(None)

    assert "unknown timestamp" in msg


async def test_tool_wrapper_prepends_banner_when_marked_stale() -> None:
    from mcp_server import _mark_stale, _tool_wrapper

    @_tool_wrapper("dummy")
    async def tool(package: str) -> str:
        _mark_stale(_SYNCED_AT)
        return "body"

    out = await tool(package="vim")

    assert out.startswith("⚠")
    assert out.endswith("body")


async def test_tool_wrapper_no_banner_when_not_stale() -> None:
    from mcp_server import _tool_wrapper

    @_tool_wrapper("dummy")
    async def tool(package: str) -> str:
        return "body"

    out = await tool(package="vim")

    assert out == "body"


async def test_tool_wrapper_isolates_stale_state_between_calls() -> None:
    """A stale call must not leak its banner into a later clean call."""
    from mcp_server import _mark_stale, _tool_wrapper

    @_tool_wrapper("dummy")
    async def stale_tool(package: str) -> str:
        _mark_stale(_SYNCED_AT)
        return "body"

    @_tool_wrapper("dummy")
    async def clean_tool(package: str) -> str:
        return "body"

    first = await stale_tool(package="vim")
    second = await clean_tool(package="vim")

    assert first.startswith("⚠")
    assert second == "body"


# ---------------------------------------------------------------------------
# mcp_server._ensure_fresh — marks stale on IngestStatus.STALE
# ---------------------------------------------------------------------------
async def test_ensure_fresh_marks_stale_on_stale_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.ingest import IngestResult
    import mcp_server

    monkeypatch.setattr(mcp_server.db, "get_package_id", AsyncMock(return_value=None))
    monkeypatch.setattr(
        mcp_server.ingest_service,
        "ingest",
        AsyncMock(
            return_value=IngestResult(
                package="vim",
                status=IngestStatus.STALE,
                entries=2,
                synced_at=_SYNCED_AT,
            )
        ),
    )

    captured: dict = {}

    def fake_mark(ts: datetime | None) -> None:
        captured["ts"] = ts

    monkeypatch.setattr(mcp_server, "_mark_stale", fake_mark)

    ok = await mcp_server._ensure_fresh("vim")

    assert ok is True
    assert captured["ts"] == _SYNCED_AT


async def test_ensure_fresh_does_not_mark_on_indexed(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.ingest import IngestResult
    import mcp_server

    monkeypatch.setattr(mcp_server.db, "get_package_id", AsyncMock(return_value=None))
    monkeypatch.setattr(
        mcp_server.ingest_service,
        "ingest",
        AsyncMock(
            return_value=IngestResult(
                package="vim",
                status=IngestStatus.INDEXED,
                entries=3,
            )
        ),
    )

    called = False

    def fake_mark(ts: datetime | None) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(mcp_server, "_mark_stale", fake_mark)

    ok = await mcp_server._ensure_fresh("vim")

    assert ok is True
    assert called is False
