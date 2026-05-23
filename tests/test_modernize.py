"""Unit tests for src/modernize.py — regex-based deprecated macro detection."""
from __future__ import annotations

import pytest

from src.modernize import MODERN_MACROS, Suggestion, check_modernization

# Map each MODERN_MACROS pattern to a spec line that triggers it.
# The last entry (%clean\n…) is a multi-line pattern that cannot match in the
# current single-line-per-iteration implementation — marked xfail.
_TRIGGER_CASES: list[tuple[str, str, bool]] = [
    ("%{__make} all",                          r"%{__make}",                   False),
    ("make %{_smp_mflags}",                    r"make\s+%{?_smp_mflags}",      False),
    ("%{__cmake} -DCMAKE_BUILD_TYPE=Release",  r"%{__cmake}",                  False),
    ("cmake ..",                               r"cmake\s+\.\.",                 False),
    ("%{__python3} setup.py build",            r"%{__python3}",                 False),
    ("%{__install} -m 0644 file.txt /dest/",   r"%{__install}",                 False),
    ("%{__rm} -rf /tmp/build",                 r"%{__rm}",                      False),
    ("%{buildroot}/usr/bin/foo",               r"%{buildroot}",                 False),
    ("BuildRoot: %{_tmppath}/%{name}",         r"BuildRoot:\s+.*",              False),
    # Multi-line pattern: single-line scanning can never match it.
    ("%clean\nrm -rf %{buildroot}",            r"%clean\nrm -rf %{buildroot}",  True),
]


# ---------------------------------------------------------------------------
# Per-pattern detection (parametrized)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "spec_line,pattern,xfail_reason",
    _TRIGGER_CASES,
    ids=[case[1][:30] for case in _TRIGGER_CASES],
)
def test_each_deprecated_pattern_detected(spec_line: str, pattern: str, xfail_reason: bool) -> None:
    if xfail_reason:
        pytest.xfail("multi-line pattern cannot match in single-line check_modernization")
    suggestions = check_modernization(spec_line)
    assert any(s.pattern == pattern for s in suggestions), (
        f"Pattern {pattern!r} not detected in {spec_line!r}\n"
        f"Got: {[s.pattern for s in suggestions]}"
    )


# ---------------------------------------------------------------------------
# Clean spec → no suggestions
# ---------------------------------------------------------------------------
def test_modern_spec_returns_no_suggestions() -> None:
    modern_spec = """\
Name:    foo
Version: 1.0

%build
%cmake
%cmake_build

%install
%cmake_install
"""
    assert check_modernization(modern_spec) == []


# ---------------------------------------------------------------------------
# Multiple patterns in one spec
# ---------------------------------------------------------------------------
def test_multiple_patterns_detected() -> None:
    spec = "%{__make} all\nmake %{_smp_mflags}\n"
    suggestions = check_modernization(spec)
    assert len(suggestions) >= 2


# ---------------------------------------------------------------------------
# Suggestion fields
# ---------------------------------------------------------------------------
def test_suggestion_has_correct_fields() -> None:
    spec = "%{__make} all\n"
    suggestions = check_modernization(spec)
    assert suggestions
    s = suggestions[0]
    assert isinstance(s, Suggestion)
    assert s.line == 1
    assert "%{__make}" in s.content
    assert s.description


def test_suggestion_line_numbers_correct() -> None:
    spec = "Name: foo\n%{__make} all\nmake %{_smp_mflags}\n"
    suggestions = check_modernization(spec)
    line_nums = {s.line for s in suggestions}
    assert 2 in line_nums  # %{__make} is on line 2
    assert 3 in line_nums  # make %{_smp_mflags} is on line 3


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------
def test_empty_spec_returns_empty() -> None:
    assert check_modernization("") == []
