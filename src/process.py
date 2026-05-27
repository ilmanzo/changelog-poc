"""Shared async subprocess runner used by RPMManager and GitManager."""
from __future__ import annotations

import asyncio
from pathlib import Path

from .config import settings


class SubprocessTimeout(Exception):
    """Raised when a subprocess exceeds ``settings.subprocess_timeout_s``."""


async def run_subprocess(
    binary: str,
    *args: str,
    cwd: Path | str | None = None,
    timeout_s: float | None = None,
) -> tuple[str, str, int]:
    """Run ``binary args...`` and return ``(stdout, stderr, returncode)``.

    Streams are decoded as UTF-8 with error-replacement and stripped. Raises
    ``SubprocessTimeout`` if the process exceeds *timeout_s* (defaults to
    ``settings.subprocess_timeout_s``); the child is killed before the
    exception is raised.
    """
    proc = await asyncio.create_subprocess_exec(
        binary,
        *args,
        cwd=str(cwd) if cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timeout = timeout_s if timeout_s is not None else settings.subprocess_timeout_s
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise SubprocessTimeout(
            f"{binary} {' '.join(args)} exceeded {timeout}s timeout"
        ) from exc
    if proc.returncode is None:  # pragma: no cover -- communicate() awaited
        raise RuntimeError(f"{binary} returned no exit code")
    return (
        stdout.decode("utf-8", errors="ignore").strip(),
        stderr.decode("utf-8", errors="ignore").strip(),
        proc.returncode,
    )
