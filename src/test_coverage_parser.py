"""Parse os-autoinst-distri-opensuse .pm test files for package references.

Extracts package names from zypper_install / ensure_installed /
install_package calls and from test module path heuristics.
"""

from __future__ import annotations

import re
from pathlib import Path

_INSTALL_CALL_RE = re.compile(
    r"""(?:zypper_call|zypper_install|ensure_installed|install_package)\s*\(\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)

_ZYPPER_SUBCMDS = frozenset(
    {
        "install",
        "in",
        "remove",
        "rm",
        "update",
        "up",
        "patch",
        "search",
        "se",
        "info",
        "if",
        "repos",
        "lr",
        "addrepo",
        "ar",
        "refresh",
        "ref",
    }
)

_PACKAGE_HEADER_RE = re.compile(
    r"""^\s*#\s*Package:\s*(.+)$""",
    re.MULTILINE,
)


def extract_package_refs(pm_content: str) -> set[str]:
    """Return package names referenced in a .pm test file."""
    packages: set[str] = set()

    for m in _INSTALL_CALL_RE.finditer(pm_content):
        for pkg in re.split(r"[\s,]+", m.group(1)):
            pkg = pkg.strip()
            if pkg and not pkg.startswith("-") and pkg not in _ZYPPER_SUBCMDS:
                packages.add(pkg)

    for m in _PACKAGE_HEADER_RE.finditer(pm_content):
        for pkg in re.split(r"[\s,]+", m.group(1)):
            pkg = pkg.strip()
            if pkg:
                packages.add(pkg)

    return packages


def extract_package_from_path(test_path: str) -> str | None:
    """Heuristic: ``tests/installation/install_vim.pm`` -> ``vim``."""
    stem = Path(test_path).stem
    for prefix in ("install_", "update_", "test_", "verify_"):
        if stem.startswith(prefix):
            return stem[len(prefix) :]
    return None


def scan_test_directory(repo_path: Path) -> dict[str, set[str]]:
    """Scan all .pm files under *repo_path* and return {test_path: {packages}}.

    Returns only entries that reference at least one package.
    """
    results: dict[str, set[str]] = {}
    for pm_file in repo_path.rglob("*.pm"):
        rel = str(pm_file.relative_to(repo_path))
        try:
            content = pm_file.read_text(errors="replace")
        except OSError:
            continue
        packages = extract_package_refs(content)
        path_pkg = extract_package_from_path(rel)
        if path_pkg:
            packages.add(path_pkg)
        if packages:
            results[rel] = packages
    return results
