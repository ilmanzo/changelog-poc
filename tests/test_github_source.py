"""Unit tests for src/sources/github_source.py -- mock HTTP, no network."""
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
    with pytest.raises(ValueError, match="not a github_release repo URL"):
        GitHubSource("https://gitlab.com/a/b")


# ---------------------------------------------------------------------------
# GitHubSource.fetch (mocked HTTP)
# ---------------------------------------------------------------------------

RELEASES_JSON: list[dict[str, object]] = [
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


def _resp_ctx(status: int, payload: object | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=json.dumps(payload) if payload is not None else "")
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _mock_session(status: int, payload: object | None = None) -> MagicMock:
    session = MagicMock()
    session.get = MagicMock(return_value=_resp_ctx(status, payload))
    return session


async def test_fetch_parses_releases() -> None:
    source = GitHubSource("https://github.com/vim/vim")
    with patch.object(source, "_get_session", AsyncMock(return_value=_mock_session(200, RELEASES_JSON))):
        result = await source.fetch("vim")

    assert result.source_name == "github_release"
    assert len(result.entries) == 2
    assert result.entries[0].version == "9.1.0"
    assert result.entries[0].author == "maintainer"
    assert "CVE-2024-1234" in result.entries[0].content
    assert result.entries[0].date.year == 2024


async def test_fetch_skips_drafts() -> None:
    source = GitHubSource("https://github.com/vim/vim")
    with patch.object(source, "_get_session", AsyncMock(return_value=_mock_session(200, RELEASES_JSON))):
        result = await source.fetch("vim")

    versions = [e.version for e in result.entries]
    assert "9.2.0-beta" not in versions


async def test_fetch_404_raises_not_found() -> None:
    source = GitHubSource("https://github.com/vim/vim")
    with patch.object(source, "_get_session", AsyncMock(return_value=_mock_session(404))):
        with pytest.raises(SourceNotFound):
            await source.fetch("vim")


async def test_fetch_403_raises_source_error(monkeypatch) -> None:
    # Ensure no retries inflate the test runtime.
    monkeypatch.setattr("src.sources.http_source.settings.obs_max_retries", 1)
    source = GitHubSource("https://github.com/vim/vim")
    with patch.object(source, "_get_session", AsyncMock(return_value=_mock_session(403))):
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
    with patch.object(source, "_get_session", AsyncMock(return_value=_mock_session(200, releases))):
        result = await source.fetch("b")

    assert result.entries[0].version == "2.0"


def test_strips_only_one_v_prefix() -> None:
    # Regression: tag.lstrip("vV") would strip ALL leading v's;
    # removeprefix variant must strip at most one.
    releases = [
        {
            "tag_name": "vvv1.0",
            "body": "x",
            "published_at": "2024-01-01T00:00:00Z",
            "draft": False,
            "author": {"login": "d"},
        },
    ]
    source = GitHubSource("https://github.com/a/b")
    entry = source._build_entry(releases[0], "b")
    assert entry.version == "vv1.0"
