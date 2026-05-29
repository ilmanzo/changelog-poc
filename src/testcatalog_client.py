"""HTTP client for the SUSE TestCatalog API.

Base URL: http://testcatalog.qa.suse.de:3001
Spec:     GET /api/v1/tests?q=<pkg>&limit=200  -> list[Test]

Read endpoints are public (no token required).
Set TESTCATALOG_API_KEY for write operations (summary reviews).

Each Test object has:
  sourcePath  -- relative path, e.g. "tests/console/vim.pm"
  comments    -- raw header block containing "# Package:" and "# Summary:" lines
  fullPath    -- GitHub URL to the file
  testName    -- short name
  tags        -- list of strings (tool type, area, ...)
"""

from __future__ import annotations

import re

import aiohttp
import structlog

from .config import settings
from .http_utils import refresh_session
from .models import BugReference, OpenQATest
from .openqa_fetcher import _PKG_RE, _SUMMARY_RE
from .sanitize import scrub_external

_logger = structlog.get_logger("rpm-mcp.testcatalog")

_PAGE_SIZE = 200
_SOURCE_TAG = "testcatalog"

# Matches "Package: vim" lines (multi-package: space/comma separated values)
_PKG_SPLIT_RE = re.compile(r"[\s,]+")


class TestCatalogClient:
    """Async client for GET /api/v1/tests."""

    def __init__(self) -> None:
        self._base = settings.testcatalog_url.rstrip("/")
        self._key = settings.testcatalog_api_key or ""
        # Why: a Bearer token sent over plain HTTP is harvestable from any
        # transit hop. Fail fast at construction rather than leaking on first
        # request. Anonymous (no key) HTTP is still allowed for public reads.
        if self._key and not self._base.lower().startswith("https://"):
            raise ValueError(
                "testcatalog_api_key requires https:// testcatalog_url to avoid "
                f"Bearer token leakage; got scheme in {self._base!r}"
            )
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        headers: dict[str, str] = {}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        self._session = await refresh_session(self._session, headers=headers)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def get_tests_for_package(self, package: str) -> list[OpenQATest]:
        """Return OpenQATest records from TestCatalog that map to *package*.

        Searches full-text with q=<package>, then filters by the
        '# Package: <package>' header in the comments block -- same logic
        used by openqa_fetcher.scan_tests() on local .pm files.
        """
        session = await self._get_session()
        out: list[OpenQATest] = []
        skip = 0

        while True:
            url = f"{self._base}/api/v1/tests"
            params = {"q": package, "limit": str(_PAGE_SIZE), "skip": str(skip)}
            async with session.get(url, params=params) as resp:
                if resp.status == 404:
                    break
                resp.raise_for_status()
                page: list[dict] = await resp.json()

            if not page:
                break

            for item in page:
                comments = item.get("comments") or ""
                pkg_m = _PKG_RE.search(comments)
                if not pkg_m:
                    continue

                # A single .pm can declare multiple packages ("Package: vim vim-data")
                raw_pkgs = _PKG_SPLIT_RE.split(pkg_m.group(1).strip())
                if package not in raw_pkgs:
                    continue

                summary_m = _SUMMARY_RE.search(comments)
                raw_summary = summary_m.group(1).strip() if summary_m else None

                raw_path = item.get("sourcePath") or item.get("fullPath") or ""
                if not raw_path:
                    continue

                out.append(
                    OpenQATest(
                        package_name=package,
                        test_path=scrub_external(raw_path, package=package, source=_SOURCE_TAG),
                        summary=scrub_external(raw_summary, package=package, source=_SOURCE_TAG)
                        if raw_summary
                        else None,
                    )
                )

            if len(page) < _PAGE_SIZE:
                break
            skip += _PAGE_SIZE

        _logger.info("testcatalog_fetched", package=package, count=len(out))
        return out

    async def get_bugs_for_package(self, package: str, limit: int = 10) -> list[BugReference]:
        """Return Bugzilla bugs mentioning *package* from the TestCatalog analytics API.

        Endpoint: GET /api/v1/analytics/search?q=<pkg>&scope=bugs&size=<limit>.
        Response envelope: ``{hits: [{_source: {bugId, summary, status, ...}}, ...]}``.
        """
        session = await self._get_session()
        url = f"{self._base}/api/v1/analytics/search"
        params = {"q": package, "scope": "bugs", "size": str(max(1, min(limit, 100)))}
        async with session.get(url, params=params) as resp:
            if resp.status == 404:
                return []
            resp.raise_for_status()
            payload = await resp.json()

        hits = payload.get("hits") or []
        out: list[BugReference] = []
        for hit in hits:
            src = hit.get("_source") or {}
            bug_id = src.get("bugId")
            if not isinstance(bug_id, int):
                continue

            def _clean(field: str, _src: dict = src) -> str | None:
                raw = _src.get(field)
                if raw is None or raw == "":
                    return None
                return scrub_external(str(raw), package=package, source=_SOURCE_TAG) or None

            out.append(
                BugReference(
                    package_name=package,
                    bug_id=bug_id,
                    summary=_clean("summary"),
                    status=_clean("status"),
                    severity=_clean("severity"),
                    component=_clean("component"),
                    assigned_to=_clean("assignedTo"),
                    resolution=_clean("resolution"),
                )
            )

        _logger.info("testcatalog_bugs_fetched", package=package, count=len(out))
        return out
