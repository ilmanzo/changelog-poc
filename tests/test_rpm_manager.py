"""Unit tests for src/rpm_manager.py.

parse_changelog() is a pure static method — tested directly.
get_dependencies() / get_reverse_dependencies() patch self._exec.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.rpm_manager import RPMManager


# ---------------------------------------------------------------------------
# parse_changelog — static method, no mocking needed
# ---------------------------------------------------------------------------
# rpm --changelog uses the format "* Mon Jan 15 2024 Author - version"
# The parser regex requires \d{2} for the day, so days must be two digits.
SAMPLE_CHANGELOG = """\
* Mon Jan 15 2024 Some Packager <packager@example.com> - 9.2.0100
- Fix buffer overflow (CVE-2024-1234, bsc#1234567)
- Performance improvements

* Thu Dec 07 2023 Another Packager <other@example.com> - 9.1.123
- Update to version 9.1.123
- Security update

* Fri Oct 13 2023 Third Dev <dev@example.com>
- Routine maintenance (no version tag in header)
"""


def test_parse_changelog_entry_count() -> None:
    entries = RPMManager.parse_changelog(SAMPLE_CHANGELOG)
    assert len(entries) == 3


def test_parse_changelog_versions_parsed() -> None:
    entries = RPMManager.parse_changelog(SAMPLE_CHANGELOG)
    assert entries[0].version == "9.2.0100"
    assert entries[1].version == "9.1.123"


def test_parse_changelog_dates_parsed() -> None:
    entries = RPMManager.parse_changelog(SAMPLE_CHANGELOG)
    assert entries[0].date.year == 2024
    assert entries[1].date.year == 2023


def test_parse_changelog_author_parsed() -> None:
    entries = RPMManager.parse_changelog(SAMPLE_CHANGELOG)
    assert "Some Packager" in entries[0].author


def test_parse_changelog_content_preserved() -> None:
    entries = RPMManager.parse_changelog(SAMPLE_CHANGELOG)
    assert "CVE-2024-1234" in entries[0].content
    assert "bsc#1234567" in entries[0].content


def test_parse_changelog_version_extracted_from_content() -> None:
    # When header has no "- VERSION" part, version should come from content
    entries = RPMManager.parse_changelog(SAMPLE_CHANGELOG)
    # Entry 3 has no version in header; content has "- Routine maintenance"
    # The version extraction regex looks for "Update|Upgrade to version X.Y.Z"
    # "Routine maintenance" doesn't match → stays "unknown"
    assert entries[2].version == "unknown"


def test_parse_changelog_version_from_content_line() -> None:
    changelog = """\
* Mon Jan 15 2024 Dev <dev@example.com>
- Update to version 1.2.3
- Some other fix
"""
    entries = RPMManager.parse_changelog(changelog)
    assert entries[0].version == "1.2.3"


def test_parse_changelog_empty_returns_empty() -> None:
    assert RPMManager.parse_changelog("") == []


def test_parse_changelog_no_valid_headers_returns_empty() -> None:
    assert RPMManager.parse_changelog("just some random text\nno headers here\n") == []


def test_parse_changelog_entries_ordered_newest_first() -> None:
    entries = RPMManager.parse_changelog(SAMPLE_CHANGELOG)
    for a, b in zip(entries, entries[1:]):
        assert a.date >= b.date


# ---------------------------------------------------------------------------
# get_dependencies — patch _exec
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "exec_responses,package,expected_check",
    [
        (
            [("libm.so.6\nglibc\n", "", 0), ("glibc\nlibc6\n", "", 0)],
            "vim",
            lambda d: "glibc" in d or "libc6" in d,
        ),
        (
            [("rpmlib(PayloadFilesHavePrefix)\nconfig(foo)\n", "", 0), ("", "", 0)],
            "minimal_pkg",
            lambda d: d == frozenset(),
        ),
    ],
    ids=["parses_packages", "empty_deps"],
)
async def test_get_dependencies(exec_responses, package, expected_check) -> None:
    mgr = RPMManager()
    mgr.get_dependencies.cache_clear()  # type: ignore[attr-defined]
    with patch.object(mgr, "_exec", new=AsyncMock()) as mock_exec:
        mock_exec.side_effect = exec_responses
        deps = await mgr.get_dependencies(package)
    assert expected_check(deps)


async def test_get_dependencies_not_installed_raises() -> None:
    mgr = RPMManager()
    mgr.get_dependencies.cache_clear()  # type: ignore[attr-defined]
    with patch.object(mgr, "_exec", new=AsyncMock()) as mock_exec:
        mock_exec.return_value = ("", "package vim not installed", 1)
        with pytest.raises(RuntimeError, match="not found"):
            await mgr.get_dependencies("vim_nonexistent_xyzzy")


# ---------------------------------------------------------------------------
# get_reverse_dependencies — patch _exec
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "exec_responses,expected_check",
    [
        (
            [("openssl = 3.1\nlibssl.so.3\n", "", 0), ("curl\nwget\n", "", 0)],
            lambda r: "curl" in r or "wget" in r,
        ),
        (
            [("openssl = 3.1\n", "", 0), ("openssl\ncurl\n", "", 0)],
            lambda r: "openssl" not in r,
        ),
    ],
    ids=["returns_packages", "excludes_self"],
)
async def test_get_reverse_deps(exec_responses, expected_check) -> None:
    mgr = RPMManager()
    mgr.get_reverse_dependencies.cache_clear()  # type: ignore[attr-defined]
    with patch.object(mgr, "_exec", new=AsyncMock()) as mock_exec:
        mock_exec.side_effect = exec_responses
        rdeps = await mgr.get_reverse_dependencies("openssl")
    assert expected_check(rdeps)
