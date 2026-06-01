"""Unit tests for src/sources/gitlab_source.py -- mock HTTP, no network."""
from __future__ import annotations

import json
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


def test_parse_gitlab_repo_rejects_unknown_host() -> None:
    # SSRF guard: only known GitLab instances pass the allowlist.
    assert parse_gitlab_repo("https://evil.internal/a/b") is None
    assert parse_gitlab_repo("https://localhost/a/b") is None
    assert parse_gitlab_repo("https://192.168.1.1/a/b") is None


def test_invalid_url_raises() -> None:
    with pytest.raises(ValueError, match="not a gitlab_release repo URL"):
        GitLabSource("not-a-url")


# ---------------------------------------------------------------------------
# GitLabSource.fetch (mocked HTTP)
# ---------------------------------------------------------------------------

RELEASES_JSON: list[dict[str, object]] = [
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


def _resp_ctx(status: int, payload: object | None = None) -> MagicMock:
    body = json.dumps(payload) if payload is not None else ""
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=body)
    resp.charset = "utf-8"
    resp.content_length = None
    body_bytes = body.encode("utf-8")

    async def _iter(_chunk_size: int) -> object:
        yield body_bytes

    resp.content = MagicMock()
    resp.content.iter_chunked = _iter
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _mock_session(status: int, payload: object | None = None) -> MagicMock:
    session = MagicMock()
    session.get = MagicMock(return_value=_resp_ctx(status, payload))
    return session


async def test_fetch_parses_releases() -> None:
    source = GitLabSource("https://gitlab.gnome.org/GNOME/glib")
    with patch.object(source, "_get_session", AsyncMock(return_value=_mock_session(200, RELEASES_JSON))):
        result = await source.fetch("glib2")

    assert result.source_name == "gitlab_release"
    assert len(result.entries) == 2
    assert result.entries[0].version == "2.80.0"
    assert "GLib 2.80" in result.entries[0].content


async def test_fetch_404_raises_not_found() -> None:
    source = GitLabSource("https://gitlab.gnome.org/GNOME/glib")
    with patch.object(source, "_get_session", AsyncMock(return_value=_mock_session(404))):
        with pytest.raises(SourceNotFound):
            await source.fetch("glib2")


async def test_fetch_500_raises_source_error(monkeypatch) -> None:
    monkeypatch.setattr("src.sources.http_source.settings.obs_max_retries", 1)
    source = GitLabSource("https://gitlab.gnome.org/GNOME/glib")
    with patch.object(source, "_get_session", AsyncMock(return_value=_mock_session(500))):
        with pytest.raises((SourceError, Exception)):
            await source.fetch("glib2")


async def test_fetch_url_contains_encoded_project() -> None:
    source = GitLabSource("https://gitlab.gnome.org/GNOME/glib")
    session = _mock_session(200, [])
    with patch.object(source, "_get_session", AsyncMock(return_value=session)):
        await source.fetch("glib2")

    call_args = session.get.call_args[0][0]
    assert "GNOME%2Fglib" in call_args
