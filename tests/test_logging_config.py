"""Unit tests for src/logging_config.py."""
from __future__ import annotations

import logging

import pytest
import structlog

from src.logging_config import configure_logging


@pytest.mark.parametrize(
    "kwargs",
    [
        {"level": logging.DEBUG, "json_logs": False},
        {"level": logging.WARNING, "json_logs": True},
        {},  # defaults
    ],
    ids=["console_mode", "json_mode", "defaults"],
)
def test_configure_logging_sets_wrapper(kwargs: dict) -> None:
    configure_logging(**kwargs)
    config = structlog.get_config()
    assert config["wrapper_class"] is not None
    assert config["logger_factory"] is not None


def test_configure_logging_idempotent() -> None:
    configure_logging()
    configure_logging()
