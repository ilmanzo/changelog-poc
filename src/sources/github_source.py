"""Changelog source: GitHub release notes via the REST API."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import aiohttp
import structlog

from .base import ChangelogSource, FetchResult, SourceError, SourceNotFound
from ..config import settings
from ..models import ChangelogEntry
from ..sanitize import scrub_external

_logger = structlog.get_logger("rpm-mcp.github")

_GITHUB_REPO_RE = re.compile(
    r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
)


def parse_github_repo(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL, or None."""
    m = _GITHUB_REPO_RE.match(url.rstrip("/"))
    return (m.group(1), m.group(2)) if m else None


class GitHubSource(ChangelogSource):
    """Fetches release notes from the GitHub Releases API.

    Requires an upstream GitHub URL (resolved by spec_url_extractor or
    service_file_parser). Auth via optional ``GITHUB_TOKEN`` env var.
    """

    name = "github_release"

    def __init__(self, upstream_url: str) -> None:
        self._upstream_url = upstream_url
        parsed = parse_github_repo(upstream_url)
        if not parsed:
            raise ValueError(f"not a GitHub repo URL: {upstream_url}")
        self._owner, self._repo = parsed

    async def fetch(self, package: str) -> FetchResult:
        token = settings.github_token
        headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        api_url = (
            f"https://api.github.com/repos/{self._owner}/{self._repo}"
            f"/releases?per_page=100"
        )

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(api_url) as resp:
                    if resp.status == 404:
                        raise SourceNotFound(
                            f"no releases at {self._owner}/{self._repo}"
                        )
                    if resp.status == 403:
                        _logger.warning(
                            "github_rate_limited",
                            owner=self._owner,
                            repo=self._repo,
                        )
                        raise SourceError("GitHub API rate limit exceeded")
                    if resp.status != 200:
                        raise SourceError(
                            f"GitHub API returned {resp.status}"
                        )
                    releases = await resp.json()
        except aiohttp.ClientError as exc:
            raise SourceError(str(exc)) from exc

        entries: list[ChangelogEntry] = []
        for rel in releases:
            if rel.get("draft"):
                continue
            tag = rel.get("tag_name", "unknown")
            body = rel.get("body") or ""
            body = scrub_external(body, package=package, source=self.name)
            published = rel.get("published_at") or rel.get("created_at", "")
            try:
                dt = datetime.fromisoformat(
                    published.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                dt = datetime.min.replace(tzinfo=timezone.utc)

            version = tag.lstrip("vV")
            entries.append(ChangelogEntry(
                version=version,
                author=rel.get("author", {}).get("login", "unknown"),
                date=dt,
                content=body,
            ))

        return FetchResult(entries=entries, source_name=self.name)
