"""Unit tests for src/debian_parser.py — pure text parsing, no mocking needed."""
from __future__ import annotations

import pytest

from src.debian_parser import parse_debian_changelog

SINGLE_ENTRY = """\
vim (2:9.1.0016-1ubuntu1) noble; urgency=medium

  * d/p/debian/use-lua5.4.diff: Refreshed for new upstream release.
  * Security fix for CVE-2024-22667.

 -- James McCoy <jamessan@debian.org>  Mon, 15 Jan 2024 07:11:08 -0500
"""

TWO_ENTRIES = """\
openssl (3.0.13-0ubuntu3) noble; urgency=medium

  * Fix regression in FIPS mode.

 -- William Grant <wgrant@ubuntu.com>  Thu, 14 Mar 2024 11:04:29 +1100

openssl (3.0.13-0ubuntu2) noble; urgency=medium

  * Backport upstream fix for CVE-2024-0727.

 -- Marc Deslauriers <marc.deslauriers@ubuntu.com>  Tue, 06 Feb 2024 09:18:55 -0500
"""

NO_CONTENT_ENTRY = """\
emptypackage (1.0-1) unstable; urgency=low

 -- Nobody <nobody@example.com>  Mon, 01 Jan 2024 00:00:00 +0000
"""


def test_parse_empty_returns_empty() -> None:
    assert parse_debian_changelog("") == []


def test_parse_single_entry() -> None:
    entries = parse_debian_changelog(SINGLE_ENTRY)
    assert len(entries) == 1
    e = entries[0]
    assert e.version == "2:9.1.0016-1ubuntu1"
    assert "James McCoy" in e.author
    assert e.date.year == 2024
    assert e.date.month == 1
    assert "CVE-2024-22667" in e.content


def test_parse_two_entries() -> None:
    entries = parse_debian_changelog(TWO_ENTRIES)
    assert len(entries) == 2
    assert entries[0].version == "3.0.13-0ubuntu3"
    assert entries[1].version == "3.0.13-0ubuntu2"
    assert entries[0].date > entries[1].date


def test_parse_authors() -> None:
    entries = parse_debian_changelog(TWO_ENTRIES)
    assert "William Grant" in entries[0].author
    assert "Marc Deslauriers" in entries[1].author


def test_parse_content_includes_changes() -> None:
    entries = parse_debian_changelog(TWO_ENTRIES)
    assert "FIPS" in entries[0].content
    assert "CVE-2024-0727" in entries[1].content


def test_no_content_entry_skipped() -> None:
    entries = parse_debian_changelog(NO_CONTENT_ENTRY)
    assert len(entries) == 0


def test_parse_junk_returns_empty() -> None:
    assert parse_debian_changelog("random text\nno structure here\n") == []


@pytest.mark.parametrize(
    "urgency",
    ["low", "medium", "high", "emergency", "critical"],
    ids=["low", "medium", "high", "emergency", "critical"],
)
def test_all_urgency_levels_accepted(urgency: str) -> None:
    text = (
        f"pkg (1.0-1) stable; urgency={urgency}\n"
        "\n"
        "  * A change.\n"
        "\n"
        f" -- Dev <dev@example.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
    )
    entries = parse_debian_changelog(text)
    assert len(entries) == 1
    assert entries[0].version == "1.0-1"
