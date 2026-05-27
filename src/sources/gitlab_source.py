"""Changelog source: GitLab release notes via the REST API."""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import quote_plus, urlparse

from ..config import settings
from .release_source import ReleaseProvider, ReleaseSource

# Hardcoded allowlist of known GitLab instances. The regex on its own
# (host + 2-segment path) would accept arbitrary hosts, which is an
# SSRF surface when the URL comes from an untrusted spec file.
_GITLAB_HOSTS: frozenset[str] = frozenset(
    {
        "gitlab.com",
        "gitlab.gnome.org",
        "gitlab.freedesktop.org",
        "gitlab.xfce.org",
        "invent.kde.org",
        "salsa.debian.org",
    }
)

_GITLAB_REPO_RE = re.compile(r"https?://([^/]+)/(.+?)(?:\.git)?/?$")


def parse_gitlab_repo(url: str) -> tuple[str, str] | None:
    """Extract (host, project_path) from a GitLab URL, or None.

    Rejects hosts not in ``_GITLAB_HOSTS`` to prevent SSRF via crafted
    upstream URLs in spec files.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if host not in _GITLAB_HOSTS:
        return None

    m = _GITLAB_REPO_RE.match(url.rstrip("/"))
    if not m:
        return None
    path = m.group(2).rstrip("/")
    if path.count("/") < 1:
        return None
    return host, path


class GitLabSource(ReleaseSource):
    """Fetches release notes from a GitLab Releases API endpoint.

    Requires an upstream GitLab URL on a known instance. Auth via optional
    ``GITLAB_TOKEN`` env var.
    """

    provider: ClassVar[ReleaseProvider] = ReleaseProvider(
        name="gitlab_release",
        auth_header="PRIVATE-TOKEN",
        auth_format="{token}",
        body_field="description",
        author_subfield="username",
        date_field="released_at",
    )

    def __init__(self, upstream_url: str) -> None:
        super().__init__(upstream_url, token=settings.gitlab_token)

    @classmethod
    def parse_url(cls, url: str) -> tuple[str, ...] | None:
        return parse_gitlab_repo(url)

    def _api_url(self) -> str:
        host, project_path = self._url_parts
        encoded = quote_plus(project_path)
        return f"https://{host}/api/v4/projects/{encoded}/releases?per_page=100"
