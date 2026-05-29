"""Unit tests for src/sources/spec_sources.py.

The spec sources now inherit ``HttpClient`` plumbing, so we mock the helper
methods (``_fetch_text`` / ``_fetch_json``) rather than ``aiohttp`` directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from src.sources.base import SourceError, SourceNotFound
from src.sources.spec_sources import ObsSpecSource, PagureSpecSource


# ---------------------------------------------------------------------------
# ObsSpecSource
# ---------------------------------------------------------------------------
async def test_obs_fetch_success() -> None:
    spec_text = "Name: vim\nVersion: 9.0\n"
    with patch.object(ObsSpecSource, "_fetch_text", new=AsyncMock(return_value=spec_text)):
        text, url = await ObsSpecSource().fetch_spec("vim")
    assert text == spec_text
    assert url is not None and "vim" in url


async def test_obs_fetch_404_returns_none() -> None:
    with patch.object(ObsSpecSource, "_fetch_text", new=AsyncMock(side_effect=SourceNotFound("404"))):
        text, url = await ObsSpecSource().fetch_spec("nonexistent_pkg")
    assert text is None
    assert url is None


async def test_obs_fetch_empty_body_returns_none() -> None:
    with patch.object(ObsSpecSource, "_fetch_text", new=AsyncMock(return_value="")):
        text, _url = await ObsSpecSource().fetch_spec("emptypkg")
    assert text is None


async def test_obs_fetch_source_error_returns_none() -> None:
    with patch.object(ObsSpecSource, "_fetch_text", new=AsyncMock(side_effect=SourceError("5xx"))):
        text, url = await ObsSpecSource().fetch_spec("vim")
    assert text is None
    assert url is None


# ---------------------------------------------------------------------------
# PagureSpecSource
# ---------------------------------------------------------------------------
async def test_pagure_fetch_success() -> None:
    spec_text = "Name: vim\nVersion: 9.1\n"
    with (
        patch.object(PagureSpecSource, "_fetch_json", new=AsyncMock(return_value={"default_branch": "rawhide"})),
        patch.object(PagureSpecSource, "_fetch_text", new=AsyncMock(return_value=spec_text)),
    ):
        text, url = await PagureSpecSource().fetch_spec("vim")
    assert text == spec_text
    assert url is not None and "rawhide" in url


async def test_pagure_meta_404_returns_none() -> None:
    with patch.object(PagureSpecSource, "_fetch_json", new=AsyncMock(side_effect=SourceNotFound("404"))):
        text, url = await PagureSpecSource().fetch_spec("nope")
    assert text is None
    assert url is None


async def test_pagure_spec_file_404_returns_none() -> None:
    with (
        patch.object(PagureSpecSource, "_fetch_json", new=AsyncMock(return_value={"default_branch": "main"})),
        patch.object(PagureSpecSource, "_fetch_text", new=AsyncMock(side_effect=SourceNotFound("404"))),
    ):
        text, _url = await PagureSpecSource().fetch_spec("missing_spec")
    assert text is None
