"""S7b: untrusted-data envelope on MCP path, suppressed on CLI path."""
from __future__ import annotations

import pytest

from src.tools._wrap import (
    _mark_stale,
    _suppress_envelope,
    _tool_wrapper,
    suppress_untrusted_envelope,
)


@pytest.fixture(autouse=True)
def _reset_suppress() -> None:
    # Each test starts with the envelope enabled (MCP default).
    _suppress_envelope.set(False)


@_tool_wrapper("t_with_sources", untrusted_sources=("rpm", "obs"))
async def _tool_with_sources(package: str) -> str:
    return "BODY"


@_tool_wrapper("t_no_sources")
async def _tool_no_sources(package: str) -> str:
    return "BODY"


@pytest.mark.asyncio
async def test_envelope_wraps_when_sources_listed() -> None:
    out = await _tool_with_sources(package="vim")
    assert out == (
        '<rpm-mcp:untrusted-data sources="rpm,obs">\n'
        "BODY\n"
        "</rpm-mcp:untrusted-data>"
    )


@pytest.mark.asyncio
async def test_envelope_absent_when_no_sources() -> None:
    out = await _tool_no_sources(package="vim")
    assert out == "BODY"


@pytest.mark.asyncio
async def test_envelope_suppressed_for_cli() -> None:
    suppress_untrusted_envelope()
    out = await _tool_with_sources(package="vim")
    assert out == "BODY"


@pytest.mark.asyncio
async def test_stale_banner_stays_outside_envelope() -> None:
    from datetime import UTC, datetime

    synced_at = datetime(2026, 1, 1, tzinfo=UTC)

    @_tool_wrapper("t_stale", untrusted_sources=("rpm",))
    async def _t(package: str) -> str:
        _mark_stale(synced_at)
        return "BODY"

    out = await _t(package="vim")
    assert out.startswith("WARNING: source fetch failed")
    assert "<rpm-mcp:untrusted-data" in out
    # Banner is emitted before the opening tag, not inside it.
    banner_end = out.index("<rpm-mcp:untrusted-data")
    assert "BODY" not in out[:banner_end]
