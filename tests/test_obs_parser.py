"""Unit tests for src/obs_parser.py — pure text parsing, no mocking needed."""
from __future__ import annotations

from datetime import datetime

import pytest

from src.obs_parser import parse_obs_changes

MINIMAL_ENTRY = """\
-------------------------------------------------------------------
Thu Jan  4 10:30:00 UTC 2024 - user@example.com

- Update to version 9.2.0100:
  * Fix CVE-2024-1234
  * Performance improvements

"""

TWO_ENTRIES = """\
-------------------------------------------------------------------
Thu Jan  4 10:30:00 UTC 2024 - user@example.com

- Update to version 9.2.0100:
  * Fix CVE-2024-1234 (bsc#1234567)

-------------------------------------------------------------------
Wed Dec  6 08:15:00 UTC 2023 - maintainer@example.com

- Security fixes for version 9.1.123

"""

NO_VERSION_ENTRY = """\
-------------------------------------------------------------------
Thu Jan  4 10:30:00 UTC 2024 - user@example.com

- Routine maintenance tasks
- No version information here

"""


def test_parse_empty_returns_empty() -> None:
    assert parse_obs_changes("") == []


def test_parse_minimal_entry_count() -> None:
    entries = parse_obs_changes(MINIMAL_ENTRY)
    assert len(entries) == 1


def test_parse_minimal_entry_author() -> None:
    entries = parse_obs_changes(MINIMAL_ENTRY)
    assert "user@example.com" in entries[0].author


def test_parse_minimal_entry_date() -> None:
    entries = parse_obs_changes(MINIMAL_ENTRY)
    assert entries[0].date != datetime.min
    assert entries[0].date.year == 2024
    assert entries[0].date.month == 1


def test_parse_minimal_entry_version_detected() -> None:
    entries = parse_obs_changes(MINIMAL_ENTRY)
    # "Update to version 9.2.0100" should be detected
    assert entries[0].version == "9.2.0100"


def test_parse_two_entries_count() -> None:
    entries = parse_obs_changes(TWO_ENTRIES)
    assert len(entries) == 2


def test_parse_two_entries_ordered_newest_first() -> None:
    entries = parse_obs_changes(TWO_ENTRIES)
    assert entries[0].date > entries[1].date


def test_parse_two_entries_authors() -> None:
    entries = parse_obs_changes(TWO_ENTRIES)
    assert "user@example.com" in entries[0].author
    assert "maintainer@example.com" in entries[1].author


def test_parse_entry_content_stripped() -> None:
    entries = parse_obs_changes(MINIMAL_ENTRY)
    # Content should not have leading/trailing whitespace
    assert entries[0].content == entries[0].content.strip()


def test_parse_version_fallback_to_unknown() -> None:
    entries = parse_obs_changes(NO_VERSION_ENTRY)
    assert len(entries) == 1
    assert entries[0].version == "unknown"


def test_parse_content_preserved() -> None:
    entries = parse_obs_changes(MINIMAL_ENTRY)
    assert "CVE-2024-1234" in entries[0].content


def test_parse_junk_only_returns_empty() -> None:
    # Content that has a separator but no valid header → parser skips block
    result = parse_obs_changes("-------------------------------------------------------------------\njust junk text with no date header\n")
    assert result == []
