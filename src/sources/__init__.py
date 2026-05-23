"""Pluggable changelog/spec/news/test sources."""
from __future__ import annotations

from .base import ChangelogSource, FetchResult, SourceError, SourceNotFound
from .gitea_source import GiteaSource
from .obs_source import ObsSource
from .registry import FetchStrategy, SourceRegistry
from .rpm_source import RpmSource

__all__ = [
    "ChangelogSource",
    "FetchResult",
    "FetchStrategy",
    "GiteaSource",
    "ObsSource",
    "RpmSource",
    "SourceError",
    "SourceNotFound",
    "SourceRegistry",
]
