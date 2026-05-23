"""Regex-based detection of deprecated RPM macros and patterns."""
from __future__ import annotations

import re
from dataclasses import dataclass

MODERN_MACROS: list[tuple[str, str | None, str]] = [
    (r"%{__make}", r"%make_build",
     "Use %make_build instead of %{__make} for parallel builds."),
    (r"make\s+%{?_smp_mflags}", r"%make_build",
     "Use %make_build macro instead of manual make with flags."),
    (r"%{__cmake}", r"%cmake",
     "Use %cmake macro instead of %{__cmake}."),
    (r"cmake\s+\.\.", r"%cmake",
     "Use %cmake macro for standard build configuration."),
    (r"%{__python3}", r"%python3",
     "Use %python3 instead of %{__python3}."),
    (r"%{__install}", "install",
     "Avoid %{__install}; use literal 'install' (Fedora/openSUSE preference)."),
    (r"%{__rm}", "rm",
     "Avoid %{__rm}; use literal 'rm'."),
    (r"%{buildroot}", r"%{buildroot}",
     "Verify %{buildroot} usage follows modern guidelines (not in %prep)."),
    (r"BuildRoot:\s+.*", None,
     "Legacy BuildRoot tag is no longer needed."),
    (r"%clean\nrm -rf %{buildroot}", None,
     "Legacy %clean section with rm -rf is no longer needed."),
]


@dataclass(frozen=True)
class Suggestion:
    line: int
    content: str
    pattern: str
    replacement: str | None
    description: str


def check_modernization(content: str) -> list[Suggestion]:
    suggestions: list[Suggestion] = []
    for i, line in enumerate(content.splitlines(), start=1):
        for old, new, desc in MODERN_MACROS:
            if re.search(old, line):
                suggestions.append(Suggestion(
                    line=i, content=line.strip(),
                    pattern=old, replacement=new, description=desc,
                ))
    return suggestions
