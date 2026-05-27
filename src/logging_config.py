"""Structlog configuration.

Set ``LOG_FORMAT=json`` for newline-delimited JSON (production / log aggregators).
Default is human-readable coloured output for local development.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(level: int = logging.INFO, json_logs: bool = False) -> None:
    """Configure structlog. Call once at process startup."""

    shared_processors: list[Any] = [
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: Any
    if json_logs:
        renderer = structlog.processors.JSONRenderer()
        shared_processors.append(structlog.processors.format_exc_info)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
    )
