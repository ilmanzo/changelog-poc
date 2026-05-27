"""Parser for the Debian/Ubuntu changelog format.

Format reference: https://www.debian.org/doc/debian-policy/ch-source.html#debian-changelog-debian-changelog

Each entry looks like::

    package (version) distribution; urgency=level

      * change description

     -- maintainer <email>  date

Entries are separated by blank lines between the trailer and the next header.
"""
from __future__ import annotations

import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from .models import ChangelogEntry
from .sanitize import scrub_external

_HEADER_RE = re.compile(
    r"^(\S+)\s+\(([^)]+)\)\s+([^;]+);\s+urgency=(\S+)"
)

_TRAILER_RE = re.compile(
    r"^\s+--\s+(.+?)\s{2,}(.+)$"
)


def parse_debian_changelog(
    raw_text: str,
    *,
    package: str | None = None,
    source: str | None = None,
) -> list[ChangelogEntry]:
    """Parse a Debian changelog into a list of ChangelogEntry objects."""
    raw_text = scrub_external(raw_text, package=package, source=source)
    entries: list[ChangelogEntry] = []

    current_version = "unknown"
    current_lines: list[str] = []
    in_entry = False

    for line in raw_text.splitlines():
        header_match = _HEADER_RE.match(line)
        if header_match:
            current_version = header_match.group(2)
            current_lines = []
            in_entry = True
            continue

        trailer_match = _TRAILER_RE.match(line)
        if trailer_match and in_entry:
            author = trailer_match.group(1)
            date_str = trailer_match.group(2).strip()
            try:
                dt = parsedate_to_datetime(date_str)
            except (ValueError, TypeError):
                dt = datetime.min

            content = "\n".join(current_lines).strip()
            if content:
                entries.append(ChangelogEntry(
                    version=current_version,
                    author=author,
                    date=dt,
                    content=content,
                ))
            in_entry = False
            continue

        if in_entry:
            current_lines.append(line)

    return entries
