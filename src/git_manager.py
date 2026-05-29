"""Shallow clones + tag/log extraction from upstream git repos."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import structlog
from async_lru import alru_cache

from .config import settings
from .ingest import validate_package_name
from .process import run_subprocess

# Why: git:// has no transport encryption or auth and the upstream URLs we
# clone come from spec headers (untrusted). https only.
_ALLOWED_SCHEMES = {"https"}
_logger = structlog.get_logger("rpm-mcp.git")


class GitManager:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or Path.home() / ".cache" / "rpm-mcp"

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise ValueError(f"Unsupported URL scheme {parsed.scheme!r}: only {_ALLOWED_SCHEMES} allowed")

    def _safe_repo_path(self, package_name: str) -> Path:
        validate_package_name(package_name)
        repo_path = (self.cache_dir / package_name).resolve()
        cache_root = self.cache_dir.resolve()
        if not repo_path.is_relative_to(cache_root) or repo_path == cache_root:
            raise ValueError(f"Path traversal detected in package name: {package_name!r}")
        return repo_path

    async def _ensure_cache_dir(self) -> None:
        await asyncio.to_thread(self.cache_dir.mkdir, parents=True, exist_ok=True)

    async def _evict_cache_if_needed(self) -> None:
        def _evict() -> None:
            try:
                entries = [p for p in self.cache_dir.iterdir() if p.is_dir()]
            except FileNotFoundError:
                return
            if len(entries) <= settings.git_cache_max_entries:
                return
            entries.sort(key=lambda p: p.stat().st_mtime)
            to_remove = entries[: len(entries) - settings.git_cache_max_entries]
            for path in to_remove:
                shutil.rmtree(path, ignore_errors=True)

        await asyncio.to_thread(_evict)

    async def _exec(self, cwd: Path, *args: str) -> tuple[str, str, int]:
        return await run_subprocess("git", *args, cwd=cwd)

    async def ensure_repo(self, url: str, package_name: str) -> Path:
        self._validate_url(url)
        await self._ensure_cache_dir()
        repo_path = self._safe_repo_path(package_name)

        path_exists = await asyncio.to_thread(repo_path.exists)
        if path_exists:
            _, _, rc = await self._exec(repo_path, "fetch", "--depth", "50", "--tags")
            if rc != 0:
                await asyncio.to_thread(shutil.rmtree, repo_path)
                await self._clone(url, repo_path)
        else:
            await self._evict_cache_if_needed()
            await self._clone(url, repo_path)
        return repo_path

    async def _clone(self, url: str, path: Path) -> None:
        _, stderr, rc = await run_subprocess(
            "git", "clone", "--depth", "50", "--no-single-branch", url, str(path)
        )
        if rc != 0:
            raise RuntimeError(f"Git clone failed for {url}: {stderr}")

    @alru_cache(maxsize=256)
    async def get_logs_between_timestamps(self, repo_path: Path, start: datetime, end: datetime) -> str:
        after = start.strftime("%Y-%m-%d %H:%M:%S")
        before = end.strftime("%Y-%m-%d %H:%M:%S")
        stdout, _, _ = await self._exec(
            repo_path, "log", f"--after={after}", f"--before={before}", "--format=%s"
        )
        return stdout

    @alru_cache(maxsize=256)
    async def get_logs_between_tags(self, repo_path: Path, tag_start: str, tag_end: str) -> str:
        stdout, _, _ = await self._exec(repo_path, "log", f"{tag_start}..{tag_end}", "--format=%s")
        return stdout

    @alru_cache(maxsize=256)
    async def find_tag(self, repo_path: Path, version: str) -> str | None:
        patterns = [version, f"v{version}", f"*{version}*"]
        for pattern in patterns:
            stdout, _, rc = await self._exec(repo_path, "tag", "-l", "--sort=-version:refname", pattern)
            if rc == 0 and stdout:
                tag = stdout.splitlines()[0]
                _, _, vrc = await self._exec(repo_path, "cat-file", "-t", tag)
                if vrc == 0:
                    return tag
        return None
