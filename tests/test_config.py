"""Unit tests for src/config.py settings loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings


def test_defaults_load_when_no_env_no_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("DATABASE_URL", "LOG_FORMAT", "FETCH_STRATEGY", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    s = Settings()
    assert s.database_url == "postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp"
    assert s.log_format == "text"
    assert s.fetch_strategy == "waterfall"
    assert s.github_token == ""


def test_dotenv_overrides_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://x:y@db/test\n"
        "LOG_FORMAT=json\n"
        "FETCH_STRATEGY=parallel\n"
    )
    monkeypatch.chdir(tmp_path)
    for key in ("DATABASE_URL", "LOG_FORMAT", "FETCH_STRATEGY"):
        monkeypatch.delenv(key, raising=False)

    s = Settings()
    assert s.database_url == "postgresql://x:y@db/test"
    assert s.log_format == "json"
    assert s.fetch_strategy == "parallel"


def test_os_env_wins_over_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LOG_FORMAT=json\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_FORMAT", "text")

    assert Settings().log_format == "text"


def test_unknown_keys_in_dotenv_are_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("UNRELATED_TOOL_KEY=whatever\nLOG_FORMAT=json\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOG_FORMAT", raising=False)

    # Should not raise despite the unknown key.
    assert Settings().log_format == "json"
