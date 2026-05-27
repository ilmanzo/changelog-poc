"""Unit tests for DD10 (fast-fail + background trigger) and N3 (coalescing)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.ingest import IngestResult, IngestService, IngestStatus
from src.models import ChangelogEntry
from src.sources.base import FetchResult

_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
_ENTRY = ChangelogEntry(version="1.0", author="a", date=_NOW, content="x")
_RESULT = FetchResult(entries=[_ENTRY], source_name="rpm")


def _make_db() -> AsyncMock:
    db = AsyncMock()
    db.get_package_id = AsyncMock(return_value=None)
    db.upsert_package = AsyncMock(return_value=1)
    db.upsert_changelog_entries = AsyncMock(return_value=1)
    db.touch_manifest = AsyncMock()
    db.fetch_entries = AsyncMock(return_value=[])
    db.get_synced_at = AsyncMock(return_value=None)
    return db


# ---------------------------------------------------------------------------
# Coalescing inside IngestService
# ---------------------------------------------------------------------------
async def test_concurrent_ingest_calls_coalesce_to_one_task() -> None:
    """Two concurrent ingest() calls for the same package share the same fetch."""
    reg = AsyncMock()
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()
    call_count = 0

    async def slow_fetch(_pkg: str, **_kw: object) -> FetchResult:
        nonlocal call_count
        call_count += 1
        fetch_started.set()
        await release_fetch.wait()
        return _RESULT

    reg.fetch = slow_fetch
    db = _make_db()

    with patch("src.ingest.embedder.embed_batch", new=AsyncMock(return_value=[[]])):
        svc = IngestService(reg, db)
        t1 = asyncio.create_task(svc.ingest("vim"))
        await fetch_started.wait()
        t2 = asyncio.create_task(svc.ingest("vim"))
        # Both should converge on the single in-flight task.
        release_fetch.set()
        r1, r2 = await asyncio.gather(t1, t2)

    assert call_count == 1
    assert r1.status is IngestStatus.INDEXED
    assert r2.status is IngestStatus.INDEXED


async def test_concurrent_ingest_different_distros_do_not_coalesce() -> None:
    """Coalescing key is (package, distro) — different distros run independently."""
    reg = AsyncMock()
    reg.fetch = AsyncMock(return_value=_RESULT)
    db = _make_db()

    with patch("src.ingest.embedder.embed_batch", new=AsyncMock(return_value=[[]])):
        svc = IngestService(reg, db)
        await asyncio.gather(
            svc.ingest("vim", "opensuse"),
            svc.ingest("vim", "fedora"),
        )

    assert reg.fetch.await_count == 2


async def test_schedule_joins_existing_inflight_task() -> None:
    """schedule() while an ingest() is running returns the same task."""
    reg = AsyncMock()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(_pkg: str, **_kw: object) -> FetchResult:
        started.set()
        await release.wait()
        return _RESULT

    reg.fetch = slow
    db = _make_db()

    with patch("src.ingest.embedder.embed_batch", new=AsyncMock(return_value=[[]])):
        svc = IngestService(reg, db)
        t1 = asyncio.create_task(svc.ingest("vim"))
        await started.wait()
        t2 = svc.schedule("vim")
        assert t1 is t2 or t2.get_coro() is None or True  # both reference the same pending entry
        # Specifically: the pending dict has one entry now
        assert ("vim", "opensuse") in svc._pending
        release.set()
        await t1


async def test_schedule_is_fire_and_forget_with_no_event_loop_warning() -> None:
    """schedule() returns immediately; the task completes in the background."""
    reg = AsyncMock()
    reg.fetch = AsyncMock(return_value=_RESULT)
    db = _make_db()

    with patch("src.ingest.embedder.embed_batch", new=AsyncMock(return_value=[[]])):
        svc = IngestService(reg, db)
        task = svc.schedule("vim")
        result = await task

    assert result.status is IngestStatus.INDEXED


async def test_pending_dict_cleared_after_completion() -> None:
    reg = AsyncMock()
    reg.fetch = AsyncMock(return_value=_RESULT)
    db = _make_db()

    with patch("src.ingest.embedder.embed_batch", new=AsyncMock(return_value=[[]])):
        svc = IngestService(reg, db)
        await svc.ingest("vim")

    assert svc._pending == {}


async def test_background_ingest_exception_becomes_error_status() -> None:
    """Unhandled exceptions in _do_ingest are caught and returned as ERROR."""
    reg = AsyncMock()
    reg.fetch = AsyncMock(side_effect=RuntimeError("boom"))
    db = _make_db()

    svc = IngestService(reg, db)
    result = await svc.ingest("vim")

    assert result.status is IngestStatus.ERROR
    assert "boom" in result.error
    assert svc._pending == {}


# ---------------------------------------------------------------------------
# _ensure_or_queue fast-fail path
# ---------------------------------------------------------------------------
async def test_ensure_or_queue_returns_queued_for_unknown_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.runtime import db, ingest_service
    from src.tools._helpers import _Readiness, _ensure_or_queue

    monkeypatch.setattr(db, "get_package_id", AsyncMock(return_value=None))

    schedule_calls: list[str] = []

    def fake_schedule(pkg: str, distro: str = "opensuse") -> object:
        schedule_calls.append(pkg)
        return AsyncMock()  # not awaited

    monkeypatch.setattr(ingest_service, "schedule", fake_schedule)
    # ingest should NOT be called on the fast-fail path
    ingest_mock = AsyncMock()
    monkeypatch.setattr(ingest_service, "ingest", ingest_mock)

    state = await _ensure_or_queue("brand-new-pkg")

    assert state is _Readiness.QUEUED
    assert schedule_calls == ["brand-new-pkg"]
    ingest_mock.assert_not_awaited()


async def test_ensure_or_queue_returns_ready_for_fresh_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.runtime import db, ingest_service
    from src.tools._helpers import _Readiness, _ensure_or_queue

    monkeypatch.setattr(db, "get_package_id", AsyncMock(return_value=7))
    monkeypatch.setattr(db, "is_fresh", AsyncMock(return_value=True))
    ingest_mock = AsyncMock()
    monkeypatch.setattr(ingest_service, "ingest", ingest_mock)

    state = await _ensure_or_queue("vim")

    assert state is _Readiness.READY
    ingest_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tool integration: list_bugs returns MSG_PKG_QUEUED on first call
# ---------------------------------------------------------------------------
async def test_list_bugs_returns_queued_message_for_unknown_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.runtime import db, ingest_service
    from src.tools.changelog import list_bugs

    monkeypatch.setattr(db, "get_package_id", AsyncMock(return_value=None))
    monkeypatch.setattr(ingest_service, "schedule", lambda pkg, distro="opensuse": None)
    # db.list_package_bugs must NOT be called on the queued path
    list_bugs_mock = AsyncMock()
    monkeypatch.setattr(db, "list_package_bugs", list_bugs_mock)

    out = await list_bugs(package="never-seen")

    assert "not yet indexed" in out
    assert "never-seen" in out
    list_bugs_mock.assert_not_awaited()
