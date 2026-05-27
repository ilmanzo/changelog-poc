"""Manage a local shallow clone of the os-autoinst test repository.

Clones on first use, pulls on refresh. Scanning test files for
package references is delegated to ``test_coverage_parser``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from .config import settings
from .test_coverage_parser import scan_test_directory

_logger = structlog.get_logger("rpm-mcp.test_repo")


class TestRepoManager:
    """Shallow clone + pull for the openQA test repository."""

    def __init__(
        self,
        repo_url: str | None = None,
        local_path: Path | None = None,
    ) -> None:
        self.repo_url: str = repo_url or settings.test_repo_url
        self.local_path: Path = local_path or Path(settings.test_repo_path)

    async def clone_or_pull(self) -> None:
        """Ensure a fresh local copy exists."""
        if (self.local_path / ".git").is_dir():
            _logger.info("test_repo_pull", path=str(self.local_path))
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(self.local_path),
                "pull",
                "--ff-only",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                _logger.warning("test_repo_pull_failed", error=stderr.decode())
        else:
            self.local_path.parent.mkdir(parents=True, exist_ok=True)
            dest = str(self.local_path)
            _logger.info("test_repo_clone", url=self.repo_url, path=dest)
            proc = await asyncio.create_subprocess_exec(
                "git",
                "clone",
                "--depth=1",
                self.repo_url,
                dest,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"git clone failed: {stderr.decode()}")

    def scan(self) -> dict[str, set[str]]:
        """Scan .pm files for package references. Sync call (CPU-bound, fast)."""
        if not self.local_path.is_dir():
            return {}
        return scan_test_directory(self.local_path)
