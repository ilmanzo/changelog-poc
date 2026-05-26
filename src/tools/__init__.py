"""Tool modules. Each `*.py` exports `register(mcp)` that binds its tools."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import changelog, deps, news, spec


def register_all(mcp: FastMCP) -> None:
    """Register every tool with the given FastMCP server."""
    changelog.register(mcp)
    deps.register(mcp)
    spec.register(mcp)
    news.register(mcp)


ALL_CLI_TOOLS = (
    *changelog.CLI_TOOLS,
    *deps.CLI_TOOLS,
    *spec.CLI_TOOLS,
    *news.CLI_TOOLS,
)
