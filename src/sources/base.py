"""Abstract base for changelog data sources."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import ChangelogEntry


class SourceNotFound(Exception):
    """Source definitively does not carry this package (HTTP 404 / not installed).
    Caller should move on to the next source rather than treating as an error.
    """


class SourceError(Exception):
    """Source temporarily unavailable (5xx, connection timeout). Raised after
    all tenacity retries are exhausted. Caller should log a warning and skip.
    """


@dataclass
class FetchResult:
    """Outcome of a single ``ChangelogSource.fetch`` call (or a registry run)."""

    entries: list[ChangelogEntry]
    upstream_url: str | None = None   # populated only by RpmSource
    source_name: str = ""
    distro: str = "opensuse"          # overridden by cross-distro sources
    fetch_failed: bool = False        # set by registry when ≥1 source raised SourceError

    @property
    def is_empty(self) -> bool:
        return not self.entries


class ChangelogSource(ABC):
    """Common interface every changelog data source must implement."""

    name: str = ""
    distro: str = "opensuse"
    is_local: bool = False  # True → tried first in parallel strategy

    @abstractmethod
    async def fetch(self, package: str) -> FetchResult:
        ...

    async def close(self) -> None:
        """Release held resources (HTTP sessions). Default no-op."""
