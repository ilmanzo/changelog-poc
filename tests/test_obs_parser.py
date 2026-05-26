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


@pytest.mark.parametrize(
    "field,check",
    [
        ("count", lambda e: len(e) == 1),
        ("author", lambda e: "user@example.com" in e[0].author),
        ("date_not_min", lambda e: e[0].date != datetime.min),
        ("date_year", lambda e: e[0].date.year == 2024),
        ("date_month", lambda e: e[0].date.month == 1),
        ("version_detected", lambda e: e[0].version == "9.2.0100"),
    ],
    ids=["count", "author", "date_not_min", "date_year", "date_month", "version_detected"],
)
def test_parse_minimal_entry_fields(field: str, check) -> None:
    entries = parse_obs_changes(MINIMAL_ENTRY)
    assert check(entries), f"field check failed: {field}"


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


# --- B1: 4-5 char timezone abbreviations must be accepted ---


_TZ_HEADER = (
    "-------------------------------------------------------------------\n"
    "{header}\n"
    "\n"
    "- Update to version 1.2.3\n"
    "\n"
)


@pytest.mark.parametrize(
    "header",
    [
        "Thu Jan  4 10:30:00 UTC 2024 - alice@example.com",   # 3 chars (regression)
        "Thu Jan  4 10:30:00 CEST 2024 - bob@example.com",    # 4 chars
        "Thu Jan  4 10:30:00 AEST 2024 - carol@example.com",  # 4 chars
        "Thu Jan  4 10:30:00 BRST 2024 - dave@example.com",   # 4 chars
        "Thu Jan  4 10:30:00 NZDST 2024 - erin@example.com",  # 5 chars
    ],
    ids=["UTC_3", "CEST_4", "AEST_4", "BRST_4", "NZDST_5"],
)
def test_parse_accepts_3_to_5_char_timezones(header: str) -> None:
    entries = parse_obs_changes(_TZ_HEADER.format(header=header))
    assert len(entries) == 1, f"entry dropped for header: {header!r}"
    assert entries[0].version == "1.2.3"
