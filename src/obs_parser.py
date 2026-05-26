"""Parser for the OBS/Gitea .changes file format.

Both ObsSource and GiteaSource share this format, so the parser lives at the
top of ``src/`` rather than being duplicated in each class.
"""
from __future__ import annotations

import re
from datetime import datetime

from .models import ChangelogEntry
from .sanitize import scrub_external

_BLOCK_SPLIT = re.compile(r"^-{67}$", re.MULTILINE)

_HEADER_RE = re.compile(
    r"^([A-Z][a-z]{2} [A-Z][a-z]{2} [\d ]\d \d{2}:\d{2}:\d{2} [A-Z]{3} \d{4}) - (.*)$"
)

# Why: precedence matters — first match wins. The explicit "update/upgrade to version X"
# patterns are checked before the looser "version X" / "for X" fallbacks so a changelog
# entry that mentions both an old reference version and a new target version returns
# the target, not the reference.
_VERSION_PATTERNS = [
    re.compile(r"[Uu]pdat(?:e|ed) to version ([\d]+(?:\.[\d]+)*)"),
    re.compile(r"[Uu]pgrad(?:e|ed) to version ([\d]+(?:\.[\d]+)*)"),
    re.compile(r"[Vv]ersion ([\d]+(?:\.[\d]+)+)"),
    re.compile(r"\bfor ([\d]+(?:\.[\d]+)+)"),
]


def parse_obs_changes(raw_text: str) -> list[ChangelogEntry]:
    """Parse an OBS .changes file into a list of ChangelogEntry objects."""
    raw_text = scrub_external(raw_text)
    entries: list[ChangelogEntry] = []

    for block in _BLOCK_SPLIT.split(raw_text):
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()
        match = _HEADER_RE.match(lines[0])
        if not match:
            continue

        date_str, author = match.groups()
        try:
            dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %Z %Y")
        except ValueError:
            dt = datetime.min

        content = "\n".join(lines[1:]).strip()

        pkg_version = "unknown"
        for pat in _VERSION_PATTERNS:
            vm = pat.search(content)
            if vm:
                pkg_version = vm.group(1)
                break

        entries.append(ChangelogEntry(
            version=pkg_version,
            author=author.strip(),
            date=dt,
            content=content,
        ))

    return entries
