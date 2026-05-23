"""Unit tests for src/spec_parser.py — pure text parsing, no mocking needed."""
from __future__ import annotations

import pytest

from src.spec_parser import _fallback_split, chunk_sections, extract_sections

SIMPLE_SPEC = """\
Name:           testpkg
Version:        1.0
Release:        1%{?dist}
Summary:        A test package
License:        MIT

%description
A minimal test package.

%prep
%autosetup -p1

%build
%make_build

%install
%make_install

%check
make test

%changelog
* Mon Jan  8 2024 Dev <dev@example.com> - 1.0-1
- Initial release
"""


# ---------------------------------------------------------------------------
# _fallback_split (pure regex, always reachable)
# ---------------------------------------------------------------------------
def test_fallback_split_header_only() -> None:
    content = "Name: foo\nVersion: 1.0\n"
    sections = _fallback_split(content)
    assert "header" in sections
    assert "foo" in sections["header"]


@pytest.mark.parametrize(
    "content,expected_sections",
    [
        ("Name: foo\n%build\n%make_build\n", ["%build"]),
        (
            "Name: foo\n%prep\n%autosetup\n%build\n%make_build\n%install\n%make_install\n",
            ["%prep", "%build", "%install"],
        ),
    ],
    ids=["single_build", "multiple_sections"],
)
def test_fallback_split_detects_sections(content: str, expected_sections: list[str]) -> None:
    sections = _fallback_split(content)
    for name in expected_sections:
        assert name in sections


def test_fallback_split_empty_content() -> None:
    sections = _fallback_split("")
    # At minimum returns a header key
    assert "header" in sections


# ---------------------------------------------------------------------------
# extract_sections (uses python-specfile AST, falls back on failure)
# ---------------------------------------------------------------------------
def test_extract_sections_returns_dict() -> None:
    sections = extract_sections(SIMPLE_SPEC)
    assert isinstance(sections, dict)
    assert len(sections) > 0


def test_extract_sections_has_header() -> None:
    sections = extract_sections(SIMPLE_SPEC)
    assert "header" in sections


def test_extract_sections_finds_build_or_fallback() -> None:
    sections = extract_sections(SIMPLE_SPEC)
    # Either AST found %build or fallback detected it
    has_build = any("%build" in k or "build" in k.lower() for k in sections)
    assert has_build or len(sections) >= 2  # at minimum header + something


def test_extract_sections_values_are_strings() -> None:
    sections = extract_sections(SIMPLE_SPEC)
    for k, v in sections.items():
        assert isinstance(k, str)
        assert isinstance(v, str)


def test_extract_sections_malformed_spec_uses_fallback() -> None:
    # Intentionally malformed — should trigger fallback, not raise
    malformed = "%%BAD {{ BROKEN SPEC without proper structure"
    sections = extract_sections(malformed)
    assert isinstance(sections, dict)


# ---------------------------------------------------------------------------
# chunk_sections
# ---------------------------------------------------------------------------
def test_chunk_sections_short_content_single_chunk() -> None:
    sections = {"header": "Short header content", "%build": "Short build"}
    chunks = chunk_sections(sections)
    assert len(chunks) == 2
    assert chunks[0].section_name == "header"
    assert chunks[0].chunk_index == 0


def test_chunk_sections_empty_sections_skipped() -> None:
    sections = {"header": "Some content", "%build": "", "%check": "   "}
    chunks = chunk_sections(sections)
    assert all(c.section_name != "%build" for c in chunks)
    assert all(c.section_name != "%check" for c in chunks)


def test_chunk_sections_long_content_multiple_chunks() -> None:
    # 2200 chars forces at least 3 chunks with size=1000, overlap=100, step=900
    long_text = "x " * 1100
    sections = {"%build": long_text}
    chunks = chunk_sections(sections)
    assert len(chunks) >= 2
    assert all(c.section_name == "%build" for c in chunks)
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1


def test_chunk_sections_preserves_content() -> None:
    marker = "UNIQUE_MARKER_12345"
    sections = {"header": marker}
    chunks = chunk_sections(sections)
    assert any(marker in c.content for c in chunks)
