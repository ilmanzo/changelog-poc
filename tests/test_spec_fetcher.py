"""Unit tests for src/sources/spec_sources.py — mock aiohttp via make_client_session."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.sources.spec_sources import (
    ObsSpecSource,
    PagureSpecSource,
    fetch_any_spec,
)


def _resp_ctx(status: int, text: str = "", json_body: Any = None) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.json = AsyncMock(return_value=json_body if json_body is not None else {})

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _session_ctx(*responses: MagicMock, get_side_effect: Exception | None = None) -> MagicMock:
    """Mock `async with make_client_session() as session`. session.get returns each *responses* in order."""
    session = MagicMock()
    if get_side_effect is not None:
        session.get = MagicMock(side_effect=get_side_effect)
    else:
        session.get = MagicMock(side_effect=list(responses))

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# ObsSpecSource
# ---------------------------------------------------------------------------
async def test_obs_fetch_success() -> None:
    spec_text = "Name: vim\nVersion: 9.0\n"
    ctx = _session_ctx(_resp_ctx(200, text=spec_text))
    with patch("src.sources.spec_sources.make_client_session", return_value=ctx):
        text, url = await ObsSpecSource().fetch_spec("vim")
    assert text == spec_text
    assert url is not None and "vim" in url


async def test_obs_fetch_404_returns_none() -> None:
    ctx = _session_ctx(_resp_ctx(404))
    with patch("src.sources.spec_sources.make_client_session", return_value=ctx):
        text, url = await ObsSpecSource().fetch_spec("nonexistent_pkg")
    assert text is None
    assert url is None


async def test_obs_fetch_empty_body_returns_none() -> None:
    ctx = _session_ctx(_resp_ctx(200, text=""))
    with patch("src.sources.spec_sources.make_client_session", return_value=ctx):
        text, _url = await ObsSpecSource().fetch_spec("emptypkg")
    assert text is None


async def test_obs_fetch_exception_returns_none() -> None:
    ctx = _session_ctx(get_side_effect=Exception("network error"))
    with patch("src.sources.spec_sources.make_client_session", return_value=ctx):
        text, url = await ObsSpecSource().fetch_spec("vim")
    assert text is None
    assert url is None


# ---------------------------------------------------------------------------
# PagureSpecSource
# ---------------------------------------------------------------------------
async def test_pagure_fetch_success() -> None:
    meta = {"default_branch": "rawhide"}
    spec_text = "Name: vim\nVersion: 9.1\n"
    ctx = _session_ctx(_resp_ctx(200, json_body=meta), _resp_ctx(200, text=spec_text))
    with patch("src.sources.spec_sources.make_client_session", return_value=ctx):
        text, url = await PagureSpecSource().fetch_spec("vim")
    assert text == spec_text
    assert url is not None


async def test_pagure_meta_404_returns_none() -> None:
    ctx = _session_ctx(_resp_ctx(404))
    with patch("src.sources.spec_sources.make_client_session", return_value=ctx):
        text, url = await PagureSpecSource().fetch_spec("nope")
    assert text is None
    assert url is None


async def test_pagure_spec_file_404_returns_none() -> None:
    meta = {"default_branch": "main"}
    ctx = _session_ctx(_resp_ctx(200, json_body=meta), _resp_ctx(404))
    with patch("src.sources.spec_sources.make_client_session", return_value=ctx):
        text, _url = await PagureSpecSource().fetch_spec("missing_spec")
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

    with (
        patch.object(ObsSpecSource, "fetch_spec", _ok),
        patch.object(PagureSpecSource, "fetch_spec", _none),
    ):
        text, _url, source = await fetch_any_spec("curl")

    assert text == spec_text
    assert source == "opensuse"


async def test_fetch_any_obs_fails_pagure_wins() -> None:
    spec_text = "Name: curl\n"

    async def _none(self, pkg: str):  # type: ignore[no-untyped-def]
        return None, None

    async def _ok(self, pkg: str):  # type: ignore[no-untyped-def]
        return spec_text, f"https://pagure/{pkg}.spec"

    with (
        patch.object(ObsSpecSource, "fetch_spec", _none),
        patch.object(PagureSpecSource, "fetch_spec", _ok),
    ):
        text, _url, source = await fetch_any_spec("curl")

    assert text == spec_text
    assert source == "fedora"


async def test_fetch_any_both_fail_returns_none_triple() -> None:
    async def _none(self, pkg: str):  # type: ignore[no-untyped-def]
        return None, None

    with (
        patch.object(ObsSpecSource, "fetch_spec", _none),
        patch.object(PagureSpecSource, "fetch_spec", _none),
    ):
        text, url, source = await fetch_any_spec("ghost")

    assert text is None
    assert url is None
    assert source is None
