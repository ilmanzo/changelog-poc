"""Unit tests for src/sources/registry.py — mock ChangelogSource objects."""
from __future__ import annotations

from unittest.mock import AsyncMock, call

import pytest

from src.models import ChangelogEntry
from src.sources.base import (
    ChangelogSource,
    FetchResult,
    SourceError,
    SourceNotFound,
)
from src.sources.registry import FetchStrategy, SourceRegistry

from datetime import datetime, timezone

_ENTRY = ChangelogEntry(
    version="1.0", author="user",
    date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    content="Fix security issue",
)
_RESULT = FetchResult(entries=[_ENTRY], source_name="mock-source")
_EMPTY = FetchResult(entries=[], source_name="mock-source")


def _make_source(
    name: str, is_local: bool = False, distro: str = "opensuse",
) -> ChangelogSource:
    """Build a minimal ChangelogSource mock."""
    src = AsyncMock(spec=ChangelogSource)
    src.name = name
    src.distro = distro
    src.is_local = is_local
    src.close = AsyncMock(return_value=None)
    return src


# ---------------------------------------------------------------------------
# Waterfall strategy
# ---------------------------------------------------------------------------
async def test_waterfall_first_source_wins() -> None:
    s1, s2 = _make_source("s1"), _make_source("s2")
    s1.fetch = AsyncMock(return_value=_RESULT)
    s2.fetch = AsyncMock(return_value=_RESULT)

    reg = SourceRegistry([s1, s2], FetchStrategy.WATERFALL)
    result = await reg.fetch("vim")

    assert result.entries == [_ENTRY]
    s1.fetch.assert_awaited_once_with("vim")
    s2.fetch.assert_not_awaited()


async def test_waterfall_skips_not_found() -> None:
    s1, s2 = _make_source("s1"), _make_source("s2")
    s1.fetch = AsyncMock(side_effect=SourceNotFound())
    s2.fetch = AsyncMock(return_value=_RESULT)

    reg = SourceRegistry([s1, s2], FetchStrategy.WATERFALL)
    result = await reg.fetch("vim")

    assert result.entries == [_ENTRY]
    s2.fetch.assert_awaited_once()


async def test_waterfall_skips_source_error() -> None:
    s1, s2 = _make_source("s1"), _make_source("s2")
    s1.fetch = AsyncMock(side_effect=SourceError("timeout"))
    s2.fetch = AsyncMock(return_value=_RESULT)

    reg = SourceRegistry([s1, s2], FetchStrategy.WATERFALL)
    result = await reg.fetch("vim")

    assert result.source_name == "mock-source"


async def test_waterfall_skips_empty_result() -> None:
    s1, s2 = _make_source("s1"), _make_source("s2")
    s1.fetch = AsyncMock(return_value=_EMPTY)
    s2.fetch = AsyncMock(return_value=_RESULT)

    reg = SourceRegistry([s1, s2], FetchStrategy.WATERFALL)
    result = await reg.fetch("vim")

    assert result.entries == [_ENTRY]


async def test_waterfall_all_fail_returns_empty() -> None:
    s1, s2 = _make_source("s1"), _make_source("s2")
    s1.fetch = AsyncMock(side_effect=SourceNotFound())
    s2.fetch = AsyncMock(side_effect=SourceError("down"))

    reg = SourceRegistry([s1, s2], FetchStrategy.WATERFALL)
    result = await reg.fetch("vim")

    assert result.is_empty
    assert result.source_name == "none"
    assert result.fetch_failed is True


async def test_waterfall_only_not_found_does_not_flag_failure() -> None:
    """404s alone (no errors) should NOT trigger the stale fallback."""
    s1, s2 = _make_source("s1"), _make_source("s2")
    s1.fetch = AsyncMock(side_effect=SourceNotFound())
    s2.fetch = AsyncMock(side_effect=SourceNotFound())

    reg = SourceRegistry([s1, s2], FetchStrategy.WATERFALL)
    result = await reg.fetch("vim")

    assert result.is_empty
    assert result.fetch_failed is False


# ---------------------------------------------------------------------------
# Parallel strategy
# ---------------------------------------------------------------------------
async def test_parallel_local_source_wins_immediately() -> None:
    local = _make_source("local", is_local=True)
    network = _make_source("network", is_local=False)
    local.fetch = AsyncMock(return_value=_RESULT)
    network.fetch = AsyncMock(return_value=_RESULT)

    reg = SourceRegistry([local, network], FetchStrategy.PARALLEL)
    result = await reg.fetch("vim")

    assert result.entries == [_ENTRY]
    # Network source should not be called when local hits
    network.fetch.assert_not_awaited()


async def test_parallel_falls_through_to_network_on_local_miss() -> None:
    local = _make_source("local", is_local=True)
    network = _make_source("network", is_local=False)
    local.fetch = AsyncMock(side_effect=SourceNotFound())
    network.fetch = AsyncMock(return_value=_RESULT)

    reg = SourceRegistry([local, network], FetchStrategy.PARALLEL)
    result = await reg.fetch("vim")

    assert result.entries == [_ENTRY]


async def test_parallel_picks_best_network_result() -> None:
    n1, n2 = _make_source("n1"), _make_source("n2")
    # n2 returns more entries → should win
    entries_big = [_ENTRY, _ENTRY, _ENTRY]
    n1.fetch = AsyncMock(return_value=FetchResult(entries=[_ENTRY], source_name="n1"))
    n2.fetch = AsyncMock(return_value=FetchResult(entries=entries_big, source_name="n2"))

    reg = SourceRegistry([n1, n2], FetchStrategy.PARALLEL)
    result = await reg.fetch("vim")

    assert result.source_name == "n2"
    assert len(result.entries) == 3


async def test_parallel_all_network_fail_returns_empty() -> None:
    n1, n2 = _make_source("n1"), _make_source("n2")
    n1.fetch = AsyncMock(side_effect=SourceError("down"))
    n2.fetch = AsyncMock(side_effect=SourceNotFound())

    reg = SourceRegistry([n1, n2], FetchStrategy.PARALLEL)
    result = await reg.fetch("vim")

    assert result.is_empty
    assert result.fetch_failed is True


# ---------------------------------------------------------------------------
# close() — all sources closed
# ---------------------------------------------------------------------------
async def test_close_calls_all_sources() -> None:
    s1, s2 = _make_source("s1"), _make_source("s2")
    reg = SourceRegistry([s1, s2], FetchStrategy.WATERFALL)
    await reg.close()
    s1.close.assert_awaited_once()
    s2.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Distro filtering
# ---------------------------------------------------------------------------
async def test_known_distros() -> None:
    s1 = _make_source("obs", distro="opensuse")
    s2 = _make_source("fedora", distro="fedora")
    s3 = _make_source("ubuntu", distro="ubuntu")
    reg = SourceRegistry([s1, s2, s3])
    assert reg.known_distros == ["opensuse", "fedora", "ubuntu"]


async def test_waterfall_distro_filter_skips_other_distros() -> None:
    suse = _make_source("obs", distro="opensuse")
    fedora = _make_source("fedora", distro="fedora")
    suse.fetch = AsyncMock(return_value=_RESULT)
    fedora.fetch = AsyncMock(
        return_value=FetchResult(entries=[_ENTRY], source_name="fedora", distro="fedora"),
    )

    reg = SourceRegistry([suse, fedora], FetchStrategy.WATERFALL)
    result = await reg.fetch("vim", distro="fedora")

    assert result.source_name == "fedora"
    suse.fetch.assert_not_awaited()
    fedora.fetch.assert_awaited_once()


async def test_distro_filter_no_match_returns_empty() -> None:
    suse = _make_source("obs", distro="opensuse")
    suse.fetch = AsyncMock(return_value=_RESULT)

    reg = SourceRegistry([suse], FetchStrategy.WATERFALL)
    result = await reg.fetch("vim", distro="fedora")

    assert result.is_empty
    suse.fetch.assert_not_awaited()


async def test_distro_none_uses_all_sources() -> None:
    suse = _make_source("obs", distro="opensuse")
    fedora = _make_source("fedora", distro="fedora")
    suse.fetch = AsyncMock(return_value=_RESULT)
    fedora.fetch = AsyncMock(return_value=_RESULT)

    reg = SourceRegistry([suse, fedora], FetchStrategy.WATERFALL)
    result = await reg.fetch("vim", distro=None)

    assert not result.is_empty
    suse.fetch.assert_awaited_once()
