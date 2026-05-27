"""Unit tests for src/git_manager.py — mock _exec and filesystem."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.git_manager import GitManager


def _gm(tmp_path: Path) -> GitManager:
    return GitManager(cache_dir=tmp_path)


# ---------------------------------------------------------------------------
# _validate_url — sync, pure
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://github.com/foo/bar",
    "git://github.com/foo/bar",
], ids=["https", "git"])
def test_validate_url_valid(url: str, tmp_path: Path) -> None:
    _gm(tmp_path)._validate_url(url)


@pytest.mark.parametrize("url", [
    "http://gitea.example.com/repo",
    "ftp://example.com/repo",
], ids=["http", "ftp"])
def test_validate_url_invalid_scheme_raises(url: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        _gm(tmp_path)._validate_url(url)


# ---------------------------------------------------------------------------
# _safe_repo_path — sync, pure
# ---------------------------------------------------------------------------
def test_safe_repo_path_valid(tmp_path: Path) -> None:
    path = _gm(tmp_path)._safe_repo_path("mypkg")
    assert path == tmp_path / "mypkg"


@pytest.mark.parametrize(
    "name", ["../evil", "../../etc/passwd", ".", "foo/bar", "evil;rm", "x y"],
    ids=["dotdot", "deep", "dot", "slash", "semicolon", "space"],
)
def test_safe_repo_path_traversal_raises(name: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"traversal|Path|Invalid package"):
        _gm(tmp_path)._safe_repo_path(name)


# ---------------------------------------------------------------------------
# get_logs_between_timestamps — mock _exec
# ---------------------------------------------------------------------------
async def test_get_logs_between_timestamps_returns_stdout(tmp_path: Path) -> None:
    gm = _gm(tmp_path)
    repo = tmp_path / "repo"
    with patch.object(gm, "_exec", new=AsyncMock(return_value=("commit A\ncommit B", "", 0))):
        result = await gm.get_logs_between_timestamps(
            repo, datetime(2024, 1, 1), datetime(2024, 12, 31)
        )
    assert result == "commit A\ncommit B"


async def test_get_logs_between_timestamps_empty(tmp_path: Path) -> None:
    gm = _gm(tmp_path)
    repo = tmp_path / "repo"
    with patch.object(gm, "_exec", new=AsyncMock(return_value=("", "", 0))):
        result = await gm.get_logs_between_timestamps(
            repo, datetime(2024, 1, 1), datetime(2024, 1, 2)
        )
    assert result == ""


# ---------------------------------------------------------------------------
# get_logs_between_tags — mock _exec
# ---------------------------------------------------------------------------
async def test_get_logs_between_tags_returns_stdout(tmp_path: Path) -> None:
    gm = _gm(tmp_path)
    repo = tmp_path / "repo"
    with patch.object(gm, "_exec", new=AsyncMock(return_value=("fix: thing\nfeat: other", "", 0))):
        result = await gm.get_logs_between_tags(repo, "v1.0", "v1.1")
    assert "fix: thing" in result


async def test_get_logs_between_tags_empty(tmp_path: Path) -> None:
    gm = _gm(tmp_path)
    repo = tmp_path / "repo"
    with patch.object(gm, "_exec", new=AsyncMock(return_value=("", "", 0))):
        result = await gm.get_logs_between_tags(repo, "v1.0", "v1.0")
    assert result == ""


# ---------------------------------------------------------------------------
# find_tag — mock _exec
# ---------------------------------------------------------------------------
async def test_find_tag_found_on_first_pattern(tmp_path: Path) -> None:
    gm = _gm(tmp_path)
    repo = tmp_path / "repo"
    # First call: tag -l → returns tag; second: cat-file → rc=0
    mock_exec = AsyncMock(side_effect=[
        ("1.2.3", "", 0),   # tag -l with plain version
        ("commit", "", 0),  # cat-file -t
    ])
    with patch.object(gm, "_exec", new=mock_exec):
        tag = await gm.find_tag(repo, "1.2.3")
    assert tag == "1.2.3"


async def test_find_tag_found_with_v_prefix(tmp_path: Path) -> None:
    gm = _gm(tmp_path)
    repo = tmp_path / "repo"
    mock_exec = AsyncMock(side_effect=[
        ("", "", 0),        # tag -l "1.2.3" → not found
        ("v1.2.3", "", 0),  # tag -l "v1.2.3" → found
        ("commit", "", 0),  # cat-file -t → valid
    ])
    with patch.object(gm, "_exec", new=mock_exec):
        tag = await gm.find_tag(repo, "1.2.3")
    assert tag == "v1.2.3"


async def test_find_tag_not_found_returns_none(tmp_path: Path) -> None:
    gm = _gm(tmp_path)
    repo = tmp_path / "repo"
    with patch.object(gm, "_exec", new=AsyncMock(return_value=("", "", 0))):
        tag = await gm.find_tag(repo, "9.9.9")
    assert tag is None


# ---------------------------------------------------------------------------
# _evict_cache_if_needed — uses real filesystem via tmp_path
# ---------------------------------------------------------------------------
async def test_evict_removes_oldest_when_over_limit(tmp_path: Path) -> None:
    gm = _gm(tmp_path)
    gm.cache_dir = tmp_path

    # Create 3 dirs with staggered mtimes (settings default is 50, patch to 2)
    dirs = []
    for name in ("old", "middle", "new"):
        d = tmp_path / name
        d.mkdir()
        dirs.append(d)
        time.sleep(0.01)  # ensure distinct mtime

    with patch("src.git_manager.settings") as mock_settings:
        mock_settings.git_cache_max_entries = 2
        await gm._evict_cache_if_needed()

    assert not (tmp_path / "old").exists()
    assert (tmp_path / "middle").exists()
    assert (tmp_path / "new").exists()


async def test_evict_no_op_when_under_limit(tmp_path: Path) -> None:
    gm = _gm(tmp_path)
    (tmp_path / "pkg1").mkdir()

    with patch("src.git_manager.settings") as mock_settings:
        mock_settings.git_cache_max_entries = 50
        await gm._evict_cache_if_needed()

    assert (tmp_path / "pkg1").exists()
