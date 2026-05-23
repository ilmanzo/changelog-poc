"""Frozen domain dataclasses shared across modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ChangelogEntry:
    """A single dated entry parsed from a package changelog."""

    version: str
    author: str
    date: datetime
    content: str


@dataclass(frozen=True)
class PackageMetadata:
    """Identity + changelog for an installed/queried package."""

    name: str
    version: str
    release: str
    url: str | None
    changelog: list[ChangelogEntry]
    distro: str = "opensuse"

    @property
    def full_version(self) -> str:
        return f"{self.version}-{self.release}"


@dataclass(frozen=True)
class SpecSection:
    """A single chunked section of a parsed .spec file."""

    section_name: str        # 'header' | '%prep' | '%build' | '%install' | '%check' | '%changelog'
    chunk_index: int
    content: str


@dataclass(frozen=True)
class NewsItem:
    """An item from Fedora Bodhi, openSUSE news RSS, or similar."""

    title: str
    source: str              # 'bodhi' | 'opensuse-rss'
    item_type: str | None
    importance: str | None
    content: str | None
    url: str | None
    date: datetime
    package_name: str | None = None


@dataclass(frozen=True)
class OpenQATest:
    """Mapping from a package to an openQA test file."""

    package_name: str
    test_path: str
    summary: str | None


@dataclass(frozen=True)
class CVEMention:
    """A CVE reference found inside a changelog entry."""

    cve_id: str
    package_name: str
    version: str
    entry_date: datetime
    excerpt: str


@dataclass(frozen=True)
class Dependency:
    """A single edge of the dependency graph."""

    package_name: str
    dep_name: str
    kind: str                # 'requires' | 'provides'
