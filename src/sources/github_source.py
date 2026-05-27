"""Changelog source: GitHub release notes via the REST API."""
from __future__ import annotations

import re
from typing import ClassVar

from ..config import settings
from .release_source import ReleaseProvider, ReleaseSource


_GITHUB_REPO_RE = re.compile(
    r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
)


def parse_github_repo(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL, or None."""
    m = _GITHUB_REPO_RE.match(url.rstrip("/"))
    return (m.group(1), m.group(2)) if m else None


class GitHubSource(ReleaseSource):
    """Fetches release notes from the GitHub Releases API.

    Requires an upstream GitHub URL (resolved by spec_url_extractor or
    service_file_parser). Auth via optional ``GITHUB_TOKEN`` env var.
    """

    provider: ClassVar[ReleaseProvider] = ReleaseProvider(
        name="github_release",
        auth_header="Authorization",
        auth_format="Bearer {token}",
        body_field="body",
        author_subfield="login",
        date_field="published_at",
    )

    # GitHub returns 403 for rate-limit (anonymous: 60/hr, token: 5000/hr).
    # Surface a clearer message instead of the generic "HTTP 403".
    _STATUS_ERROR_MESSAGES: ClassVar[dict[int, str]] = {
        403: "GitHub API rate limit exceeded",
    }

    def __init__(self, upstream_url: str) -> None:
        super().__init__(upstream_url, token=settings.github_token)

    @classmethod
    def parse_url(cls, url: str) -> tuple[str, ...] | None:
        return parse_github_repo(url)

    def _api_url(self) -> str:
        owner, repo = self._url_parts
        return (
            f"https://api.github.com/repos/{owner}/{repo}"
            f"/releases?per_page=100"
        )
