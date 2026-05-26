"""Spec tools: AST-parsed sections of a package's .spec file."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from src import embedder
from src.config import settings
from src.ingest import validate_package_name
from src.runtime import db
from src.sources.spec_sources import SPEC_SOURCES
from src.spec_parser import chunk_sections, extract_sections
from src.tools._helpers import MSG_UNKNOWN_SPEC_SOURCE
from src.tools._wrap import _tlog, _tool_wrapper


async def _ensure_spec(
    package: str, source: str = "opensuse"
) -> tuple[int, int, str, str] | None:
    """Fetch + persist spec if missing or older than ``cache_ttl_spec_s``.
    Returns ``(package_id, spec_id, content, url)`` or ``None`` if no source has it.
    """
    pkg_id = await db.get_package_id(package)
    if pkg_id is not None and await db.is_fresh(
        pkg_id, settings.cache_ttl_spec_s, kind="spec"
    ):
        cached = await db.get_spec(pkg_id, source)
        if cached:
            return pkg_id, int(cached["id"]), cached["content"], ""

    spec_source = SPEC_SOURCES.get(source)
    if spec_source is None:
        return None
    text, url = await spec_source.fetch_spec(package)
    if not text:
        return None

    pkg_id = await db.upsert_package(package)
    spec_id = await db.upsert_spec(pkg_id, source, version=None, content=text)
    sections = chunk_sections(extract_sections(text, package=package, source=source))
    if sections:
        embeddings = await embedder.embed_batch(s.content for s in sections)
        if not embeddings:
            embeddings = [[] for _ in sections]
        await db.replace_spec_sections(spec_id, sections, embeddings)
    await db.touch_manifest(pkg_id, kind="spec")
    return pkg_id, spec_id, text, url or ""


@_tool_wrapper("get_spec_details")
async def get_spec_details(package: str, source: str = "opensuse") -> str:
    """Return the parsed AST sections of *package*'s .spec from *source*
    (``opensuse`` or ``fedora``). Fetched on cache miss.
    """
    validate_package_name(package)
    if source not in SPEC_SOURCES:
        return MSG_UNKNOWN_SPEC_SOURCE.format(source)
    out = await _ensure_spec(package, source)
    if out is None:
        return f"No {source} spec found for {package}."
    _, _, content, _ = out
    sections = extract_sections(content, package=package, source=source)
    _tlog(sections=len(sections))
    lines = [f"Package: {package} (source: {source}) -- {len(sections)} sections"]
    for name, body in sections.items():
        body = body.strip()
        if not body:
            continue
        lines.append(f"\n## {name}\n{body}")
    return "\n".join(lines)


CLI_TOOLS = (get_spec_details,)


def register(mcp: FastMCP) -> None:
    for fn in CLI_TOOLS:
        mcp.tool()(fn)
