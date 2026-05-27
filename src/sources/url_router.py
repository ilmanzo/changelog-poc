"""Dispatch an upstream URL to the matching ReleaseSource subclass.

Keeps IngestService decoupled from individual release providers: adding a
new forge means registering it here, not editing ingest.py.
"""
from __future__ import annotations

from collections.abc import Callable

from .github_source import GitHubSource, parse_github_repo
from .gitlab_source import GitLabSource, parse_gitlab_repo
from .release_source import ReleaseSource

_DISPATCH: tuple[tuple[Callable[[str], object], type[ReleaseSource]], ...] = (
    (parse_github_repo, GitHubSource),
    (parse_gitlab_repo, GitLabSource),
)


def parse_upstream_url(url: str) -> ReleaseSource | None:
    """Return an instantiated ReleaseSource for *url*, or None if no forge matches."""
    for parser, source_cls in _DISPATCH:
        if parser(url) is None:
            continue
        try:
            return source_cls(url)
        except ValueError:
            return None
    return None
