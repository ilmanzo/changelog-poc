"""Unit tests for src/sources/rpm_source.py."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from src.models import ChangelogEntry, PackageMetadata
from src.sources.base import SourceError, SourceNotFound
from src.sources.rpm_source import RpmSource

_ENTRY = ChangelogEntry(
    version="9.2",
    author="Test Packager <test@example.com>",
    date=datetime(2024, 6, 1),
    content="- Fix CVE-2024-1234",
)

_META = PackageMetadata(
    name="vim",
    version="9.2",
    release="1.1",
    url="https://www.vim.org",
    changelog=[_ENTRY],
)

_META_EMPTY = PackageMetadata(
    name="minipkg",
    version="1.0",
    release="1",
    url=None,
    changelog=[],
)


def _src_with_mock(metadata: PackageMetadata | None = None, exc: Exception | None = None) -> RpmSource:
    mgr = AsyncMock()
    if exc:
        mgr.get_metadata = AsyncMock(side_effect=exc)
    else:
        mgr.get_metadata = AsyncMock(return_value=metadata)
    return RpmSource(rpm_manager=mgr)


# ---------------------------------------------------------------------------
# fetch — success with entries
# ---------------------------------------------------------------------------
async def test_fetch_success_returns_entries() -> None:
    src = _src_with_mock(_META)
    result = await src.fetch("vim")
    assert len(result.entries) == 1
    assert result.entries[0].version == "9.2"
    assert result.upstream_url == "https://www.vim.org"
    assert result.source_name == "rpm"


# ---------------------------------------------------------------------------
# fetch — success with empty changelog (not an error)
# ---------------------------------------------------------------------------
async def test_fetch_empty_changelog_returns_empty_result() -> None:
    src = _src_with_mock(_META_EMPTY)
    result = await src.fetch("minipkg")
    assert result.entries == []
    assert result.is_empty


# ---------------------------------------------------------------------------
# fetch — "not found" / "no package" RuntimeError → SourceNotFound
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("msg", [
    "Package 'ghost' not found: error",
    "no package ghost installed",
], ids=["not_found", "no_package"])
async def test_fetch_not_found_raises_source_not_found(msg: str) -> None:
    src = _src_with_mock(exc=RuntimeError(msg))
    with pytest.raises(SourceNotFound):
        await src.fetch("ghost")


# ---------------------------------------------------------------------------
# fetch — unexpected RuntimeError → SourceError
# ---------------------------------------------------------------------------
async def test_fetch_unexpected_error_raises_source_error() -> None:
    src = _src_with_mock(exc=RuntimeError("rpm database corrupt"))
    with pytest.raises(SourceError):
        await src.fetch("vim")
