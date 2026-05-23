"""Raw .spec fetchers for Fedora Pagure and openSUSE OBS.

Lightweight async helpers — no per-source ABC since both sources expose the
same trivial interface (give-me-the-spec-text). Caller decides which to try.
"""
from __future__ import annotations

import httpx
import structlog

from .config import settings

_logger = structlog.get_logger("rpm-mcp.spec_fetcher")

PAGURE_BASE = "https://src.fedoraproject.org"
OBS_BASE = "https://build.opensuse.org/public/source/openSUSE:Factory"

_TIMEOUT = httpx.Timeout(settings.obs_timeout_total, connect=settings.obs_timeout_connect)


async def fetch_pagure_spec(package: str) -> tuple[str | None, str | None]:
    """Return (spec_text, source_url) or (None, None) if not found."""
    api_url = f"{PAGURE_BASE}/api/0/rpms/{package}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            meta = await client.get(api_url)
            if meta.status_code != 200:
                return None, None
            branch = meta.json().get("default_branch", "main")
            raw_url = f"{PAGURE_BASE}/rpms/{package}/raw/{branch}/f/{package}.spec"
            spec = await client.get(raw_url)
            if spec.status_code == 200 and spec.text:
                return spec.text, raw_url
    except Exception as e:
        _logger.warning("pagure_fetch_failed", package=package, error=str(e))
    return None, None


async def fetch_obs_spec(package: str) -> tuple[str | None, str | None]:
    """Return (spec_text, source_url) or (None, None) if not found."""
    url = f"{OBS_BASE}/{package}/{package}.spec"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200 and resp.text:
                return resp.text, url
    except Exception as e:
        _logger.warning("obs_spec_fetch_failed", package=package, error=str(e))
    return None, None


async def fetch_any_spec(package: str) -> tuple[str | None, str | None, str | None]:
    """Try OBS first then Pagure. Returns (spec_text, source_url, source_name)."""
    for fn, name in ((fetch_obs_spec, "opensuse"), (fetch_pagure_spec, "fedora")):
        text, url = await fn(package)
        if text:
            return text, url, name
    return None, None, None
