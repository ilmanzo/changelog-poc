"""Unit tests for src/logging_config.py."""
from __future__ import annotations

import logging

import structlog

from src.logging_config import configure_logging


def test_configure_logging_console_mode() -> None:
    configure_logging(level=logging.DEBUG, json_logs=False)
    config = structlog.get_config()
    assert config["wrapper_class"] is not None


def test_configure_logging_json_mode() -> None:
    configure_logging(level=logging.WARNING, json_logs=True)
    config = structlog.get_config()
    assert config["wrapper_class"] is not None


def test_configure_logging_default_args() -> None:
    configure_logging()
    config = structlog.get_config()
    assert config["logger_factory"] is not None


def test_configure_logging_idempotent() -> None:
    configure_logging()
    configure_logging()
