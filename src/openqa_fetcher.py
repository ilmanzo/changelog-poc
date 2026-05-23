"""openQA test→package mapping extractor.

Scans a checkout of ``os-autoinst-distri-opensuse`` for ``.pm`` test files,
extracting `# Package:` and `# Summary:` headers into ``OpenQATest`` records.
The repo path is expected to already exist on disk (caller clones or updates).
"""
from __future__ import annotations

import re
from pathlib import Path

import structlog

from .models import OpenQATest

_logger = structlog.get_logger("rpm-mcp.openqa")

_PKG_RE = re.compile(r"^# Package:\s*(.*)$", re.MULTILINE)
_SUMMARY_RE = re.compile(r"^# Summary:\s*(.*)$", re.MULTILINE)


def scan_tests(repo_path: str | Path) -> list[OpenQATest]:
    """Walk ``<repo_path>/tests/**/*.pm`` and return one OpenQATest per (pkg, file)."""
    repo = Path(repo_path)
    tests_dir = repo / "tests"
    if not tests_dir.exists():
        _logger.error("tests_dir_missing", path=str(tests_dir))
        return []

    out: list[OpenQATest] = []
    for pm in tests_dir.rglob("*.pm"):
        try:
            content = pm.read_text(errors="ignore")
        except Exception as e:
            _logger.warning("pm_read_failed", path=str(pm), error=str(e))
            continue

        pkg_m = _PKG_RE.search(content)
        if not pkg_m:
            continue
        summary_m = _SUMMARY_RE.search(content)
        summary = summary_m.group(1).strip() if summary_m else None
        rel_path = str(pm.relative_to(repo))

        for raw in pkg_m.group(1).split():
            pkg = raw.strip().strip(",")
            if pkg:
                out.append(OpenQATest(
                    package_name=pkg, test_path=rel_path, summary=summary,
                ))
    return out
