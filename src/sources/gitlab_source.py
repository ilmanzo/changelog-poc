"""Changelog source: GitLab release notes via the REST API."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

import aiohttp
import structlog

from .base import ChangelogSource, FetchResult, SourceError, SourceNotFound
from ..models import ChangelogEntry
from ..sanitize import scrub_external

_logger = structlog.get_logger("rpm-mcp.gitlab")

_GITLAB_REPO_RE = re.compile(
    r"https?://([^/]+)/(.+?)(?:\.git)?/?$"
)


def parse_gitlab_repo(url: str) -> tuple[str, str] | None:
    """Extract (host, project_path) from a GitLab URL, or None."""
    m = _GITLAB_REPO_RE.match(url.rstrip("/"))
    if not m:
        return None
    host = m.group(1)
    path = m.group(2).rstrip("/")
    if path.count("/") < 1:
        return None
    return host, path


class GitLabSource(ChangelogSource):
    """Fetches release notes from a GitLab Releases API endpoint.

    Requires an upstream GitLab URL. Auth via optional ``GITLAB_TOKEN``
    env var.
    """

    name = "gitlab_release"

    def __init__(self, upstream_url: str) -> None:
        self._upstream_url = upstream_url
        parsed = parse_gitlab_repo(upstream_url)
        if not parsed:
            raise ValueError(f"not a GitLab repo URL: {upstream_url}")
        self._host, self._project_path = parsed

    async def fetch(self, package: str) -> FetchResult:
        token = os.environ.get("GITLAB_TOKEN", "")
        headers: dict[str, str] = {}
        if token:
            headers["PRIVATE-TOKEN"] = token

        encoded = quote_plus(self._project_path)
        api_url = (
            f"https://{self._host}/api/v4/projects/{encoded}"
            f"/releases?per_page=100"
        )

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(api_url) as resp:
                    if resp.status == 404:
                        raise SourceNotFound(
                            f"no releases at {self._project_path}"
                        )
                    if resp.status != 200:
                        raise SourceError(
                            f"GitLab API returned {resp.status}"
                        )
                    releases = await resp.json()
        except aiohttp.ClientError as exc:
            raise SourceError(str(exc)) from exc

        entries: list[ChangelogEntry] = []
        for rel in releases:
            tag = rel.get("tag_name", "unknown")
            body = rel.get("description") or ""
            body = scrub_external(body, package=package, source=self.name)
            released_at = rel.get("released_at") or rel.get("created_at", "")
            try:
                dt = datetime.fromisoformat(
                    released_at.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                dt = datetime.min.replace(tzinfo=timezone.utc)

            version = tag.lstrip("vV")
            entries.append(ChangelogEntry(
                version=version,
                author=rel.get("author", {}).get("username", "unknown")
                        if isinstance(rel.get("author"), dict) else "unknown",
                date=dt,
                content=body,
            ))

        return FetchResult(entries=entries, source_name=self.name)
