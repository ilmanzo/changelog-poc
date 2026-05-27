"""Dependency tools: forward/reverse deps, transitive dep changes, core packages."""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from src.config import settings
from src.ingest import validate_package_name
from src.runtime import rpm_mgr
from src.tools._helpers import _format_date, _Readiness
from src.tools._wrap import _tlog, _tool_wrapper
from src.tools.changelog import _fetch_recent_releases


@_tool_wrapper("get_dependencies")
async def get_dependencies(package: str) -> str:
    """Direct runtime deps of *package* from the local RPM database."""
    validate_package_name(package)
    try:
        deps = await rpm_mgr.get_dependencies(package)
    except RuntimeError as e:
        return f"Package '{package}' not installed locally: {e}"
    _tlog(count=len(deps))
    if not deps:
        return f"No dependencies resolved for '{package}'."
    return f"Dependencies of {package} ({len(deps)}):\n" + "\n".join(sorted(deps))


@_tool_wrapper("get_reverse_dependencies")
async def get_reverse_dependencies(package: str) -> str:
    """Installed packages that depend on *package* (local RPM database)."""
    validate_package_name(package)
    try:
        rdeps = await rpm_mgr.get_reverse_dependencies(package)
    except RuntimeError as e:
        return f"Package '{package}' not installed locally: {e}"
    _tlog(count=len(rdeps))
    if not rdeps:
        return f"No installed packages depend on '{package}'."
    return f"Packages depending on {package} ({len(rdeps)}):\n" + "\n".join(sorted(rdeps))


@_tool_wrapper("get_dependency_changes")
async def get_dependency_changes(package: str, n: int = 3, depth: int = 1, refresh: bool = False) -> str:
    """For each (transitive) dependency of *package*, return its last *n* releases."""
    validate_package_name(package)
    depth = max(1, min(depth, 2))
    n = max(1, min(n, 20))

    deps_list = await _collect_transitive_deps(package, depth)
    if not deps_list:
        return f"No dependencies resolved for '{package}' (is it installed locally?)."

    results = await asyncio.gather(
        *(_fetch_recent_releases(d, n, refresh) for d in deps_list),
        return_exceptions=True,
    )

    lines = [
        f"Dependencies of {package} (depth={depth}, {len(deps_list)} packages, last {n} release(s) each):"
    ]
    ok = err = missing = queued = 0
    for dep, res in zip(deps_list, results, strict=True):
        if isinstance(res, BaseException):
            if not isinstance(res, Exception):
                raise res
            err += 1
            lines.append(f"\n## {dep}: error -- {res}")
            continue
        if res is None:
            missing += 1
            lines.append(f"\n## {dep}: no changelog found in any source")
            continue
        if res is _Readiness.QUEUED:
            queued += 1
            lines.append(f"\n## {dep}: queued -- retry in a few seconds")
            continue
        ok += 1
        lines.append(f"\n## {dep} -- last {len(res)} release(s)")
        for ver, newest_dt, items in res:
            lines.append(f"  === {ver} ({_format_date(newest_dt)}) ===")
            for e in items:
                lines.append(f"  {e.content}")
    _tlog(ok=ok, missing=missing, errors=err, queued=queued)
    return "\n".join(lines)


@_tool_wrapper("find_core_packages")
async def find_core_packages(
    n: int = 50,
    seed_pattern: str = "base",
    expand: bool = True,
) -> str:
    """Identify the *n* most important distro packages by reverse-dependency count.

    Seeds from the given pattern (default: base), optionally expands one hop of
    transitive deps to surface hidden-but-fundamental packages (e.g. glibc), then
    ranks the candidate pool by how many installed packages require each one.
    """
    n = max(1, min(n, 200))

    seed_pkgs = await rpm_mgr.find_pattern_packages(seed_pattern)
    if not seed_pkgs:
        return (
            f"Pattern '{seed_pattern}' not found in the local RPM database. "
            "Try 'base', 'enhanced_base', or check `rpm -q --whatprovides 'pattern():<name>'`."
        )
    _tlog(seed_count=len(seed_pkgs))

    candidates: set[str] = set(seed_pkgs)
    if expand:
        dep_results = await asyncio.gather(
            *(rpm_mgr.get_dependencies(p) for p in seed_pkgs),
            return_exceptions=True,
        )
        for res in dep_results:
            if isinstance(res, frozenset):
                candidates.update(res)

    candidates = {p for p in candidates if not p.startswith("patterns-")}
    _tlog(candidate_count=len(candidates))

    rdep_results = await asyncio.gather(
        *(rpm_mgr.get_reverse_dependencies(p) for p in candidates),
        return_exceptions=True,
    )
    scored: list[tuple[int, str]] = []
    for pkg, res in zip(candidates, rdep_results, strict=True):
        if isinstance(res, frozenset):
            scored.append((len(res), pkg))

    scored.sort(reverse=True)
    top = scored[:n]
    _tlog(result_count=len(top))

    width = len(str(len(top)))
    lines = [
        f"Core packages seeded from '{seed_pattern}' pattern"
        f" (top {len(top)} of {len(scored)} candidates by reverse-dep count):",
        "",
    ]
    for rank, (count, pkg) in enumerate(top, 1):
        lines.append(f"  {rank:{width}}. {pkg:<40} (required by {count} packages)")
    return "\n".join(lines)


async def _collect_transitive_deps(root: str, depth: int) -> list[str]:
    """BFS up to *depth* hops from *root*, capped by ``settings.f4_max_packages``."""
    visited: set[str] = {root}
    deps: set[str] = set()
    frontier: set[str] = {root}
    for _ in range(depth):
        new_frontier: set[str] = set()
        for pkg in frontier:
            try:
                pkg_deps = await rpm_mgr.get_dependencies(pkg)
            except RuntimeError:
                continue
            for d in pkg_deps:
                if d not in visited:
                    new_frontier.add(d)
                    visited.add(d)
        deps.update(new_frontier)
        frontier = new_frontier
        if len(deps) >= settings.f4_max_packages:
            break
    return sorted(deps)[: settings.f4_max_packages]


CLI_TOOLS = (
    get_dependencies,
    get_reverse_dependencies,
    get_dependency_changes,
    find_core_packages,
)


def register(mcp: FastMCP) -> None:
    for fn in CLI_TOOLS:
        mcp.tool()(fn)
