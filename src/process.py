"""Shared async subprocess runner used by RPMManager and GitManager."""
from __future__ import annotations

import asyncio
from pathlib import Path


async def run_subprocess(
    binary: str, *args: str, cwd: Path | str | None = None
) -> tuple[str, str, int]:
    """Run ``binary args...`` and return ``(stdout, stderr, returncode)``.

    Streams are decoded as UTF-8 with error-replacement and stripped.
    ``returncode`` is normalised to 0 when the process is still running.
    """
    proc = await asyncio.create_subprocess_exec(
        binary,
        *args,
        cwd=str(cwd) if cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        stdout.decode("utf-8", errors="ignore").strip(),
        stderr.decode("utf-8", errors="ignore").strip(),
        proc.returncode or 0,
    )
