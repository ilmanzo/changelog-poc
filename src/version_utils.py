"""Pure helpers for version-string normalization and date parsing."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import dateparser

from .models import ChangelogEntry

CLEAN_RE = re.compile(r"[\+p].*$")
HAS_DIGIT_RE = re.compile(r"\d")
CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,7}$", re.IGNORECASE)
# Matches bsc#, boo#, bnc# with an optional leading URL prefix.
BSC_RE = re.compile(r"^(?:bsc|boo|bnc)#\d+$", re.IGNORECASE)

DATEPARSER_SETTINGS = {
    "PREFER_DATES_FROM": "past",
    "RETURN_AS_TIMEZONE_AWARE": True,
    "TIMEZONE": "UTC",
}


def clean_version(v_str: str) -> str | None:
    v = CLEAN_RE.sub("", str(v_str))
    return v if HAS_DIGIT_RE.search(v) else None


def content_matches(entry: ChangelogEntry, *versions: str) -> bool:
    return any(v in entry.content for v in versions)


def parse_when(text: str) -> datetime | None:
    """ISO 8601 first (cheap, strict), then dateparser. UTC-aware or None."""
    text = text.strip() if text else ""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        pass
    dt = dateparser.parse(text, settings=DATEPARSER_SETTINGS)
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
