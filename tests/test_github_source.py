"""Unit tests for src/sources/github_source.py — mock HTTP, no network."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.sources.base import SourceError, SourceNotFound
from src.sources.github_source import GitHubSource, parse_github_repo

# ---------------------------------------------------------------------------
# parse_github_repo (pure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/vim/vim", ("vim", "vim")),
        ("https://github.com/vim/vim/", ("vim", "vim")),
        ("https://github.com/vim/vim.git", ("vim", "vim")),
        ("http://github.com/owner/repo", ("owner", "repo")),
    ],
    ids=["plain", "trailing_slash", "dot_git", "http"],
)
def test_parse_github_repo(url: str, expected: tuple[str, str]) -> None:
    assert parse_github_repo(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/a/b",
        "not a url",
        "https://github.com/",
        "",
    ],
    ids=["gitlab", "junk", "incomplete", "empty"],
)
def test_parse_github_repo_none(url: str) -> None:
    assert parse_github_repo(url) is None


def test_invalid_url_raises() -> None:
    with pytest.raises(ValueError, match="not a GitHub repo URL"):
        GitHubSource("https://gitlab.com/a/b")


# ---------------------------------------------------------------------------
# GitHubSource.fetch (mocked aiohttp)
# ---------------------------------------------------------------------------

RELEASES_JSON = [
    {
        "tag_name": "v9.1.0",
        "body": "## What's new\n- Fix CVE-2024-1234",
        "published_at": "2024-01-15T10:00:00Z",
        "draft": False,
        "author": {"login": "maintainer"},
    },
    {
        "tag_name": "v9.0.0",
        "body": "Initial release",
        "published_at": "2023-06-01T08:00:00Z",
        "draft": False,
        "author": {"login": "maintainer"},
    },
    {
        "tag_name": "v9.2.0-beta",
        "body": "Draft release",
        "published_at": "2024-02-01T00:00:00Z",
        "draft": True,
        "author": {"login": "maintainer"},
    },
]


def _mock_response(status: int, payload: object = None):
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=payload)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)
    return resp


def _mock_session(response):
    session = AsyncMock()
    session.get = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


async def test_fetch_parses_releases() -> None:
    source = GitHubSource("https://github.com/vim/vim")
    resp = _mock_response(200, RELEASES_JSON)
    session = _mock_session(resp)

    with patch("src.sources.github_source.aiohttp.ClientSession", return_value=session):
        result = await source.fetch("vim")

    assert result.source_name == "github_release"
    assert len(result.entries) == 2
    assert result.entries[0].version == "9.1.0"
    assert result.entries[0].author == "maintainer"
    assert "CVE-2024-1234" in result.entries[0].content
    assert result.entries[0].date.year == 2024


async def test_fetch_skips_drafts() -> None:
    source = GitHubSource("https://github.com/vim/vim")
    resp = _mock_response(200, RELEASES_JSON)
    session = _mock_session(resp)

    with patch("src.sources.github_source.aiohttp.ClientSession", return_value=session):
        result = await source.fetch("vim")

    versions = [e.version for e in result.entries]
    assert "9.2.0-beta" not in versions


async def test_fetch_404_raises_not_found() -> None:
    source = GitHubSource("https://github.com/vim/vim")
    resp = _mock_response(404)
    session = _mock_session(resp)

    with patch("src.sources.github_source.aiohttp.ClientSession", return_value=session):
        with pytest.raises(SourceNotFound):
            await source.fetch("vim")


async def test_fetch_403_raises_source_error() -> None:
    source = GitHubSource("https://github.com/vim/vim")
    resp = _mock_response(403)
    session = _mock_session(resp)

    with patch("src.sources.github_source.aiohttp.ClientSession", return_value=session):
        with pytest.raises(SourceError, match="rate limit"):
            await source.fetch("vim")


async def test_fetch_strips_v_prefix() -> None:
    releases = [
        {
            "tag_name": "V2.0",
            "body": "release",
            "published_at": "2024-01-01T00:00:00Z",
            "draft": False,
            "author": {"login": "dev"},
        },
    ]
    source = GitHubSource("https://github.com/a/b")
    resp = _mock_response(200, releases)
    session = _mock_session(resp)

    with patch("src.sources.github_source.aiohttp.ClientSession", return_value=session):
        result = await source.fetch("b")

    assert result.entries[0].version == "2.0"
