"""Unit tests for src/testcatalog_client.py -- mock aiohttp, no network."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import BugReference, OpenQATest
from src.testcatalog_client import TestCatalogClient


def _resp_ctx(status: int, payload: object | None = None) -> MagicMock:
    import json as _json

    body = _json.dumps(payload) if payload is not None else "[]"
    resp = MagicMock()
    resp.status = status
    resp.raise_for_status = MagicMock(return_value=None)
    resp.json = AsyncMock(return_value=payload if payload is not None else [])
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


def _mock_session(*responses: MagicMock) -> MagicMock:
    """Each call to session.get() returns the next prepared response context."""
    session = MagicMock()
    session.get = MagicMock(side_effect=list(responses))
    return session


# ---------------------------------------------------------------------------
# get_bugs_for_package
# ---------------------------------------------------------------------------

_BUGS_PAYLOAD = {
    "total": {"value": 2, "relation": "eq"},
    "hits": [
        {
            "_id": "x1",
            "_score": 1.0,
            "_source": {
                "bugId": 1219879,
                "summary": "Remove EOL OpenSSL 1.1.1",
                "status": "RESOLVED",
                "severity": "Normal",
                "component": "Basesystem",
                "assignedTo": "openssl-maintainers@suse.de",
                "resolution": "FIXED",
            },
        },
        {
            "_id": "x2",
            "_score": 0.9,
            "_source": {
                "bugId": 1233235,
                "summary": "openssl-3 introduced a dependency on perl-base",
                "status": "RESOLVED",
                "severity": "Normal",
                "component": "Other",
                "assignedTo": "openssl-maintainers@suse.de",
                "resolution": "FIXED",
            },
        },
    ],
}


async def test_get_bugs_parses_hits() -> None:
    client = TestCatalogClient()
    with patch.object(
        client, "_get_session", AsyncMock(return_value=_mock_session(_resp_ctx(200, _BUGS_PAYLOAD)))
    ):
        bugs = await client.get_bugs_for_package("openssl")

    assert len(bugs) == 2
    assert all(isinstance(b, BugReference) for b in bugs)
    assert bugs[0].bug_id == 1219879
    assert bugs[0].summary == "Remove EOL OpenSSL 1.1.1"
    assert bugs[0].status == "RESOLVED"
    assert bugs[0].assigned_to == "openssl-maintainers@suse.de"
    assert bugs[1].bug_id == 1233235


async def test_get_bugs_404_returns_empty() -> None:
    client = TestCatalogClient()
    with patch.object(client, "_get_session", AsyncMock(return_value=_mock_session(_resp_ctx(404)))):
        bugs = await client.get_bugs_for_package("nonexistent")
    assert bugs == []


async def test_get_bugs_clamps_limit_to_100() -> None:
    client = TestCatalogClient()
    session = _mock_session(_resp_ctx(200, {"hits": []}))
    with patch.object(client, "_get_session", AsyncMock(return_value=session)):
        await client.get_bugs_for_package("openssl", limit=500)
    # The size param should be clamped to 100.
    call_kwargs = session.get.call_args.kwargs
    assert call_kwargs["params"]["size"] == "100"


async def test_get_bugs_skips_hits_without_bugid() -> None:
    payload = {
        "hits": [
            {"_source": {"summary": "no bugId field"}},  # skipped
            {"_source": {"bugId": "not-an-int", "summary": "bad type"}},  # skipped
            {"_source": {"bugId": 42, "summary": "valid"}},
        ]
    }
    client = TestCatalogClient()
    with patch.object(client, "_get_session", AsyncMock(return_value=_mock_session(_resp_ctx(200, payload)))):
        bugs = await client.get_bugs_for_package("vim")
    assert len(bugs) == 1
    assert bugs[0].bug_id == 42


async def test_get_bugs_sanitises_string_fields() -> None:
    payload = {
        "hits": [
            {
                "_source": {
                    "bugId": 1,
                    "summary": "clean summary",
                    "status": "NEW",
                    "severity": "",
                    "component": None,
                    "assignedTo": "dev@example.com",
                    "resolution": "",
                }
            }
        ]
    }
    client = TestCatalogClient()
    with patch.object(client, "_get_session", AsyncMock(return_value=_mock_session(_resp_ctx(200, payload)))):
        bugs = await client.get_bugs_for_package("vim")
    assert len(bugs) == 1
    bug = bugs[0]
    assert bug.severity is None  # empty string -> None
    assert bug.component is None  # null -> None
    assert bug.resolution is None  # empty -> None
    assert bug.assigned_to == "dev@example.com"


# ---------------------------------------------------------------------------
# get_tests_for_package (regression: existing path still works)
# ---------------------------------------------------------------------------


async def test_get_tests_filters_by_package_header() -> None:
    payload = [
        {
            "sourcePath": "tests/console/vim.pm",
            "comments": "# Package: vim\n# Summary: vim editor test",
        },
        {
            "sourcePath": "tests/console/emacs.pm",
            "comments": "# Package: emacs\n# Summary: not vim",
        },
        {
            "sourcePath": "tests/console/multi.pm",
            "comments": "# Package: vim vim-data\n# Summary: multi-pkg",
        },
    ]
    client = TestCatalogClient()
    with patch.object(client, "_get_session", AsyncMock(return_value=_mock_session(_resp_ctx(200, payload)))):
        tests = await client.get_tests_for_package("vim")

    assert len(tests) == 2
    assert all(isinstance(t, OpenQATest) for t in tests)
    assert {t.test_path for t in tests} == {"tests/console/vim.pm", "tests/console/multi.pm"}


# Ensure pytest-asyncio decorates these
pytestmark = pytest.mark.asyncio
