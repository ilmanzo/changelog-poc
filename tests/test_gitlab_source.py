"""Unit tests for src/sources/gitlab_source.py — mock HTTP, no network."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.sources.base import SourceError, SourceNotFound
from src.sources.gitlab_source import GitLabSource, parse_gitlab_repo


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://gitlab.gnome.org/GNOME/glib",
            ("gitlab.gnome.org", "GNOME/glib"),
        ),
        (
            "https://gitlab.com/procps-ng/procps",
            ("gitlab.com", "procps-ng/procps"),
        ),
        (
            "https://gitlab.freedesktop.org/mesa/mesa.git",
            ("gitlab.freedesktop.org", "mesa/mesa"),
        ),
    ],
    ids=["gnome", "gitlab_com", "freedesktop_dotgit"],
)
def test_parse_gitlab_repo(
    url: str, expected: tuple[str, str]
) -> None:
    assert parse_gitlab_repo(url) == expected


def test_parse_gitlab_repo_none() -> None:
    assert parse_gitlab_repo("not-a-url") is None


def test_parse_gitlab_repo_no_project_path() -> None:
    assert parse_gitlab_repo("https://gitlab.com/onlyone") is None


def test_invalid_url_raises() -> None:
    with pytest.raises(ValueError, match="not a GitLab repo URL"):
        GitLabSource("not-a-url")


# ---------------------------------------------------------------------------
# GitLabSource.fetch (mocked aiohttp)
# ---------------------------------------------------------------------------

RELEASES_JSON = [
    {
        "tag_name": "v2.80.0",
        "description": "GLib 2.80 stable release",
        "released_at": "2024-03-10T12:00:00Z",
        "author": {"username": "maintainer"},
    },
    {
        "tag_name": "v2.79.0",
        "description": "",
        "released_at": "2024-01-05T08:00:00Z",
        "author": {"username": "dev"},
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
    source = GitLabSource("https://gitlab.gnome.org/GNOME/glib")
    resp = _mock_response(200, RELEASES_JSON)
    session = _mock_session(resp)

    with patch("src.sources.gitlab_source.aiohttp.ClientSession", return_value=session):
        result = await source.fetch("glib2")

    assert result.source_name == "gitlab_release"
    assert len(result.entries) == 2
    assert result.entries[0].version == "2.80.0"
    assert "GLib 2.80" in result.entries[0].content


async def test_fetch_404_raises_not_found() -> None:
    source = GitLabSource("https://gitlab.gnome.org/GNOME/glib")
    resp = _mock_response(404)
    session = _mock_session(resp)

    with patch("src.sources.gitlab_source.aiohttp.ClientSession", return_value=session):
        with pytest.raises(SourceNotFound):
            await source.fetch("glib2")


async def test_fetch_500_raises_source_error() -> None:
    source = GitLabSource("https://gitlab.gnome.org/GNOME/glib")
    resp = _mock_response(500)
    session = _mock_session(resp)

    with patch("src.sources.gitlab_source.aiohttp.ClientSession", return_value=session):
        with pytest.raises(SourceError):
            await source.fetch("glib2")


async def test_fetch_url_contains_encoded_project() -> None:
    source = GitLabSource("https://gitlab.gnome.org/GNOME/glib")
    resp = _mock_response(200, [])
    session = _mock_session(resp)

    with patch("src.sources.gitlab_source.aiohttp.ClientSession", return_value=session):
        await source.fetch("glib2")

    call_args = session.get.call_args[0][0]
    assert "GNOME%2Fglib" in call_args
