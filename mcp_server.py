"""FastMCP entrypoint for rpm-mcp.

Tool bodies live in ``src/tools/*``; each module exports ``register(mcp)``.
Singletons (DB, source registry, ingest service) live in ``src/runtime``.
This file wires them together.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from src.cli import run_cli
from src.config import settings
from src.logging_config import configure_logging
from src.runtime import lifespan
from src.tools import register_all

configure_logging(
    level=logging.INFO,
    json_logs=settings.log_format.lower() == "json",
)

mcp = FastMCP("rpm", lifespan=lifespan)
register_all(mcp)


if __name__ == "__main__":
    run_cli(serve=mcp.run)
