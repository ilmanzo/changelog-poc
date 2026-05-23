"""Spec-file AST parsing via python-specfile.

Produces SpecSection chunks ready for embedding. Falls back to manual `%`-based
splitting if the AST parser rejects the input (common with malformed specs).
"""
from __future__ import annotations

import structlog
from specfile import Specfile

from .embedder import chunk_text
from .models import SpecSection

_logger = structlog.get_logger("rpm-mcp.spec_parser")

_SECTION_TAGS = (
    "%prep", "%build", "%install", "%check", "%files",
    "%changelog", "%package", "%description",
)


def extract_sections(content: str) -> dict[str, str]:
    """Section name → raw section content. Includes a synthetic 'header' preamble."""
    try:
        spec = Specfile(content=content, sourcedir="/tmp")
        sections: dict[str, str] = {}
        with spec.sections() as sc:
            for section in sc:
                sections[section.name] = "".join(section.data)
        header_lines: list[str] = []
        for line in content.splitlines():
            if line.startswith("%"):
                break
            header_lines.append(line)
        sections["header"] = "\n".join(header_lines)
        return sections
    except Exception as e:
        _logger.warning("ast_parse_failed_fallback", error=str(e))
        return _fallback_split(content)


def _fallback_split(content: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in content.splitlines():
        if line.startswith("%") and any(tag in line for tag in _SECTION_TAGS):
            current = line.strip()
            sections[current] = []
        sections[current].append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def chunk_sections(sections: dict[str, str]) -> list[SpecSection]:
    """Slide-window-chunk each section into SpecSection records ready for embedding."""
    out: list[SpecSection] = []
    for name, content in sections.items():
        content = content.strip()
        if not content:
            continue
        for idx, chunk in enumerate(chunk_text(content)):
            out.append(SpecSection(
                section_name=name,
                chunk_index=idx,
                content=chunk,
            ))
    return out
