"""Extract upstream repository URLs from RPM spec file text.

Looks for forge URLs (GitHub, GitLab, Codeberg, etc.) in the ``URL:``
and ``Source0:``/``Source:`` header fields. Returns a de-duped, ordered
list of candidate URLs.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_URL_TAG_RE = re.compile(r"^URL:\s+(\S+)", re.MULTILINE | re.IGNORECASE)
_SOURCE_TAG_RE = re.compile(
    r"^Source0?:\s+(\S+)",
    re.MULTILINE | re.IGNORECASE,
)

_FORGE_HOSTS = frozenset(
    {
        "github.com",
        "gitlab.com",
        "codeberg.org",
        "gitlab.gnome.org",
        "gitlab.freedesktop.org",
        "gitlab.xfce.org",
        "invent.kde.org",
        "git.savannah.gnu.org",
        "gitea.com",
        "sr.ht",
    }
)


def _is_forge_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    return host in _FORGE_HOSTS


def _normalise_forge_url(url: str) -> str:
    """Strip archive suffixes and path fragments to get a repo base URL.

    Example: ``https://github.com/vim/vim/archive/v9.1.tar.gz``
           -> ``https://github.com/vim/vim``
    """
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    clean: list[str] = []
    for part in parts:
        if part in ("archive", "releases", "tarball", "zipball", "-"):
            break
        if re.match(r"^v?\d", part) and clean:
            break
        if part.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".zip", ".tgz")):
            break
        clean.append(part)
    path = "/".join(clean[:2]) if len(clean) >= 2 else "/".join(clean)
    return f"{parsed.scheme}://{parsed.hostname}/{path}"


def extract_upstream_urls(spec_text: str) -> list[str]:
    """Return de-duped forge URLs found in a spec file's header fields."""
    seen: dict[str, None] = {}

    for match in _URL_TAG_RE.finditer(spec_text):
        url = match.group(1).rstrip("/")
        if _is_forge_url(url):
            norm = _normalise_forge_url(url)
            seen.setdefault(norm, None)

    for match in _SOURCE_TAG_RE.finditer(spec_text):
        raw = match.group(1).rstrip("/")
        expanded = re.sub(r"%\{[^}]+\}", "", raw)
        if not _is_forge_url(expanded):
            continue
        norm = _normalise_forge_url(expanded)
        parsed = urlparse(norm)
        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) < 2:
            continue
        seen.setdefault(norm, None)

    return list(seen)
