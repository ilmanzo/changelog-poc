"""Base class for forge release-notes sources (GitHub, GitLab)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

from ..models import ChangelogEntry
from ..sanitize import scrub_external
from .base import FetchResult
from .http_source import HttpSource


@dataclass(frozen=True)
class ReleaseProvider:
    """Per-forge configuration shared by all instances of a ReleaseSource."""

    name: str
    auth_header: str | None  # "Authorization" (GH) or "PRIVATE-TOKEN" (GL)
    auth_format: str  # "Bearer {token}" (GH) or "{token}" (GL)
    body_field: str  # "body" (GH) / "description" (GL)
    author_subfield: str  # "login" (GH) / "username" (GL)
    date_field: str  # "published_at" (GH) / "released_at" (GL)
    date_fallback: str = "created_at"


class ReleaseSource(HttpSource):
    """Common implementation for GitHub/GitLab release-notes APIs.

    Concrete subclasses set ``provider`` and implement ``parse_url`` +
    ``_api_url``. Both forges return the same JSON shape for releases,
    differing only in field names captured by ``ReleaseProvider``.
    """

    provider: ClassVar[ReleaseProvider]

    def __init__(self, upstream_url: str, token: str | None = None) -> None:
        parts = self.parse_url(upstream_url)
        if parts is None:
            raise ValueError(f"not a {self.provider.name} repo URL: {upstream_url}")
        headers: dict[str, str] = {}
        if token and self.provider.auth_header:
            headers[self.provider.auth_header] = self.provider.auth_format.format(token=token)
        super().__init__(extra_headers=headers or None)
        self._upstream_url = upstream_url
        self._url_parts = parts

    # ------------------------------------------------------------------
    # ChangelogSource interface
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:  # type: ignore[override]
        return self.provider.name

    # ------------------------------------------------------------------
    # Subclass extension points
    # ------------------------------------------------------------------
    @classmethod
    def parse_url(cls, url: str) -> tuple[str, ...] | None:
        raise NotImplementedError

    def _api_url(self) -> str:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    async def fetch(self, package: str) -> FetchResult:
        try:
            releases = await self._fetch_json(self._api_url())
        finally:
            await self.close()

        entries: list[ChangelogEntry] = []
        for rel in releases:
            if rel.get("draft"):
                continue
            entries.append(self._build_entry(rel, package))
        return FetchResult(entries=entries, source_name=self.name)

    def _build_entry(self, rel: dict[str, Any], package: str) -> ChangelogEntry:
        tag = rel.get("tag_name", "unknown")
        body = rel.get(self.provider.body_field) or ""
        body = scrub_external(body, package=package, source=self.name)

        raw_date = rel.get(self.provider.date_field) or rel.get(self.provider.date_fallback, "")
        try:
            dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            dt = datetime.min.replace(tzinfo=UTC)

        author_obj = rel.get("author")
        author = "unknown"
        if isinstance(author_obj, dict):
            author = author_obj.get(self.provider.author_subfield) or "unknown"

        version = tag.removeprefix("v").removeprefix("V")
        return ChangelogEntry(version=version, author=author, date=dt, content=body)
