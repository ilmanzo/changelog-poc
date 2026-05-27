"""Unit tests for src/sources/fedora_source.py — mock HTTP, no network."""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.sources.fedora_source import FedoraSource, extract_changelog_section

# ---------------------------------------------------------------------------
# extract_changelog_section (pure)
# ---------------------------------------------------------------------------

SPEC_WITH_CHANGELOG = """\
Name:           vim
Version:        9.1
Release:        1.fc40
Summary:        The VIM editor

%description
Vi IMproved.

%changelog
-------------------------------------------------------------------
Thu Jan  4 10:30:00 UTC 2024 - maintainer@fedoraproject.org

- Update to version 9.1:
  * Fix CVE-2024-1234

-------------------------------------------------------------------
Wed Dec  6 08:15:00 UTC 2023 - maintainer@fedoraproject.org

- Security fixes for version 9.0
"""

SPEC_NO_CHANGELOG = """\
Name:           vim
Version:        9.1
Release:        1.fc40

%description
Vi IMproved.

%build
make
"""


def test_extract_changelog_present() -> None:
    result = extract_changelog_section(SPEC_WITH_CHANGELOG)
    assert result is not None
    assert "Update to version 9.1" in result
    assert "CVE-2024-1234" in result


def test_extract_changelog_absent() -> None:
    assert extract_changelog_section(SPEC_NO_CHANGELOG) is None


def test_extract_changelog_empty_string() -> None:
    assert extract_changelog_section("") is None


def test_extract_changelog_case_insensitive() -> None:
    spec = "Name: foo\n%CHANGELOG\n- entry one\n"
    result = extract_changelog_section(spec)
    assert result is not None
    assert "entry one" in result


# ---------------------------------------------------------------------------
# FedoraSource.fetch (mocked HTTP)
# ---------------------------------------------------------------------------

PAGURE_META = json.dumps({"default_branch": "rawhide"})


@pytest.fixture
def source() -> FedoraSource:
    s = FedoraSource()
    s.fetch.cache_clear()
    return s


async def test_fetch_parses_changelog(source: FedoraSource) -> None:
    with patch.object(source, "_fetch_text", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = [PAGURE_META, SPEC_WITH_CHANGELOG]
        result = await source.fetch("vim")

    assert len(result.entries) == 2
    assert result.source_name == "fedora"
    assert result.entries[0].version == "9.1"
    assert "CVE-2024-1234" in result.entries[0].content


async def test_fetch_no_changelog_raises_not_found(source: FedoraSource) -> None:
    from src.sources.base import SourceNotFound

    with patch.object(source, "_fetch_text", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = [PAGURE_META, SPEC_NO_CHANGELOG]
        with pytest.raises(SourceNotFound):
            await source.fetch("vim")


async def test_fetch_uses_default_branch(source: FedoraSource) -> None:
    meta = json.dumps({"default_branch": "f40"})
    with patch.object(source, "_fetch_text", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = [meta, SPEC_WITH_CHANGELOG]
        await source.fetch("vim")

    calls = mock_fetch.call_args_list
    assert "/raw/f40/f/" in calls[1][0][0]


async def test_fetch_404_propagates(source: FedoraSource) -> None:
    from src.sources.base import SourceNotFound

    with patch.object(source, "_fetch_text", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = SourceNotFound("vim")
        with pytest.raises(SourceNotFound):
            await source.fetch("vim")
