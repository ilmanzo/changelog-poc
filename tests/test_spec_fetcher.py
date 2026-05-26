"""Unit tests for src/sources/spec_sources.py — mock httpx.AsyncClient via _client."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.sources.spec_sources import (
    ObsSpecSource,
    PagureSpecSource,
    fetch_any_spec,
)


def _mock_client(*responses: tuple[int, str | dict]) -> MagicMock:
    """Build a mock httpx.AsyncClient async context manager.

    Each response is (status_code, body): body is str for .text, dict for .json().
    """
    resp_mocks = []
    for status, body in responses:
        resp = MagicMock()
        resp.status_code = status
        if isinstance(body, dict):
            resp.text = ""
            resp.json.return_value = body
        else:
            resp.text = body
        resp_mocks.append(resp)

    client = AsyncMock()
    client.get = AsyncMock(side_effect=resp_mocks)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# ObsSpecSource
# ---------------------------------------------------------------------------
async def test_obs_fetch_success() -> None:
    spec_text = "Name: vim\nVersion: 9.0\n"
    mock = _mock_client((200, spec_text))
    with patch("src.sources.spec_sources._client", return_value=mock):
        text, url = await ObsSpecSource().fetch_spec("vim")
    assert text == spec_text
    assert url is not None and "vim" in url


async def test_obs_fetch_404_returns_none() -> None:
    mock = _mock_client((404, ""))
    with patch("src.sources.spec_sources._client", return_value=mock):
        text, url = await ObsSpecSource().fetch_spec("nonexistent_pkg")
    assert text is None
    assert url is None


async def test_obs_fetch_empty_body_returns_none() -> None:
    mock = _mock_client((200, ""))
    with patch("src.sources.spec_sources._client", return_value=mock):
        text, url = await ObsSpecSource().fetch_spec("emptypkg")
    assert text is None


async def test_obs_fetch_exception_returns_none() -> None:
    client = AsyncMock()
    client.get = AsyncMock(side_effect=Exception("network error"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("src.sources.spec_sources._client", return_value=client):
        text, url = await ObsSpecSource().fetch_spec("vim")
    assert text is None
    assert url is None


# ---------------------------------------------------------------------------
# PagureSpecSource
# ---------------------------------------------------------------------------
async def test_pagure_fetch_success() -> None:
    meta = {"default_branch": "rawhide"}
    spec_text = "Name: vim\nVersion: 9.1\n"
    mock = _mock_client((200, meta), (200, spec_text))
    with patch("src.sources.spec_sources._client", return_value=mock):
        text, url = await PagureSpecSource().fetch_spec("vim")
    assert text == spec_text
    assert url is not None


async def test_pagure_meta_404_returns_none() -> None:
    mock = _mock_client((404, ""))
    with patch("src.sources.spec_sources._client", return_value=mock):
        text, url = await PagureSpecSource().fetch_spec("nope")
    assert text is None
    assert url is None


async def test_pagure_spec_file_404_returns_none() -> None:
    meta = {"default_branch": "main"}
    mock = _mock_client((200, meta), (404, ""))
    with patch("src.sources.spec_sources._client", return_value=mock):
        text, url = await PagureSpecSource().fetch_spec("missing_spec")
    assert text is None


# ---------------------------------------------------------------------------
# fetch_any_spec — waterfall: OBS first, then Pagure
# ---------------------------------------------------------------------------
async def test_fetch_any_obs_wins() -> None:
    spec_text = "Name: curl\n"

    async def _ok(self, pkg: str):  # type: ignore[no-untyped-def]
        return spec_text, f"https://obs/{pkg}.spec"

    async def _none(self, pkg: str):  # type: ignore[no-untyped-def]
        return None, None

    with patch.object(ObsSpecSource, "fetch_spec", _ok), \
         patch.object(PagureSpecSource, "fetch_spec", _none):
        text, _url, source = await fetch_any_spec("curl")

    assert text == spec_text
    assert source == "opensuse"


async def test_fetch_any_obs_fails_pagure_wins() -> None:
    spec_text = "Name: curl\n"

    async def _none(self, pkg: str):  # type: ignore[no-untyped-def]
        return None, None

    async def _ok(self, pkg: str):  # type: ignore[no-untyped-def]
        return spec_text, f"https://pagure/{pkg}.spec"

    with patch.object(ObsSpecSource, "fetch_spec", _none), \
         patch.object(PagureSpecSource, "fetch_spec", _ok):
        text, _url, source = await fetch_any_spec("curl")

    assert text == spec_text
    assert source == "fedora"


async def test_fetch_any_both_fail_returns_none_triple() -> None:
    async def _none(self, pkg: str):  # type: ignore[no-untyped-def]
        return None, None

    with patch.object(ObsSpecSource, "fetch_spec", _none), \
         patch.object(PagureSpecSource, "fetch_spec", _none):
        text, url, source = await fetch_any_spec("ghost")

    assert text is None
    assert url is None
    assert source is None
