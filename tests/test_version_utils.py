"""Unit tests for src/version_utils.py — pure functions, no mocking needed."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.models import ChangelogEntry
from src.version_utils import BSC_RE, CVE_RE, clean_version, content_matches, parse_when


# ---------------------------------------------------------------------------
# clean_version
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("9.2",        "9.2"),
    ("9.2.0447",   "9.2.0447"),
    ("9.2p1",      "9.2"),          # strip from 'p' onward
    ("9.2+git.123", "9.2"),         # strip from '+' onward
    ("1:9.2",      "1:9.2"),        # epoch prefix NOT stripped
    ("0",          "0"),
])
def test_clean_version_strips_suffixes(raw: str, expected: str) -> None:
    assert clean_version(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "unknown", "p1", ""])
def test_clean_version_returns_none_for_non_numeric(raw: str) -> None:
    assert clean_version(raw) is None


# ---------------------------------------------------------------------------
# content_matches
# ---------------------------------------------------------------------------
def _entry(content: str) -> ChangelogEntry:
    return ChangelogEntry(version="1.0", author="test", date=datetime.min, content=content)


def test_content_matches_single_term() -> None:
    e = _entry("Fix CVE-2023-4738 buffer overflow")
    assert content_matches(e, "CVE-2023-4738")


def test_content_matches_multiple_terms_any() -> None:
    e = _entry("Fix CVE-2024-1234 in vim")
    assert content_matches(e, "openssl", "vim")


def test_content_matches_no_match() -> None:
    e = _entry("Routine maintenance")
    assert not content_matches(e, "CVE", "security")


# ---------------------------------------------------------------------------
# parse_when
# ---------------------------------------------------------------------------
def test_parse_when_iso8601_date_only() -> None:
    dt = parse_when("2024-01-15")
    assert dt is not None
    assert dt.year == 2024 and dt.month == 1 and dt.day == 15
    assert dt.tzinfo is not None  # must be tz-aware


def test_parse_when_iso8601_with_tz() -> None:
    dt = parse_when("2024-06-01T12:00:00+00:00")
    assert dt is not None
    assert dt.year == 2024
    assert dt.tzinfo is not None


def test_parse_when_natural_language() -> None:
    dt = parse_when("1 year ago")
    assert dt is not None
    assert dt.year <= datetime.now(UTC).year - 1


def test_parse_when_invalid_returns_none() -> None:
    assert parse_when("not a date at all xyz") is None


def test_parse_when_empty_returns_none() -> None:
    assert parse_when("") is None


def test_parse_when_whitespace_returns_none() -> None:
    assert parse_when("   ") is None


# ---------------------------------------------------------------------------
# CVE_RE
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cve_id", [
    "CVE-2023-4738",
    "CVE-2024-12345",
    "CVE-2023-1234567",
    "cve-2023-4738",    # case-insensitive
])
def test_cve_re_valid(cve_id: str) -> None:
    assert CVE_RE.match(cve_id), f"Expected {cve_id!r} to match CVE_RE"


@pytest.mark.parametrize("cve_id", [
    "CVE-2023-123",             # too few digits (< 4)
    "CVE-2023-12345678",        # too many digits (> 7)
    "CVE-23-4738",              # year not 4 digits
    "CWE-2023-4738",            # wrong prefix
    "CVE-2023-4738 extra",      # trailing content
    "prefix CVE-2023-4738",     # leading content
    "",
])
def test_cve_re_invalid(cve_id: str) -> None:
    assert not CVE_RE.match(cve_id), f"Expected {cve_id!r} NOT to match CVE_RE"


# ---------------------------------------------------------------------------
# BSC_RE
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bug_id", [
    "bsc#1260905",
    "boo#1234567",
    "bnc#999999",
    "BSC#1260905",      # case-insensitive
    "bsc#1",            # any number of digits
])
def test_bsc_re_valid(bug_id: str) -> None:
    assert BSC_RE.match(bug_id), f"Expected {bug_id!r} to match BSC_RE"


@pytest.mark.parametrize("bug_id", [
    "bsc#abc",          # non-numeric
    "bug#1234567",      # wrong prefix
    "bsc-1234567",      # wrong separator
    "bsc#",             # no digits
    "bsc#1234567 extra",
    "",
])
def test_bsc_re_invalid(bug_id: str) -> None:
    assert not BSC_RE.match(bug_id), f"Expected {bug_id!r} NOT to match BSC_RE"
