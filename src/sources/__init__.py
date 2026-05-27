"""Pluggable changelog/spec/news/test sources."""
from __future__ import annotations

from .base import ChangelogSource, FetchResult, SourceError, SourceNotFound
from .fedora_source import FedoraSource
from .gitea_source import GiteaSource
from .obs_source import ObsSource
from .registry import FetchStrategy, SourceRegistry
from .rpm_source import RpmSource

__all__ = [
    "ChangelogSource",
    "FedoraSource",
    "FetchResult",
    "FetchStrategy",
    "GiteaSource",
    "ObsSource",
    "RpmSource",
    "SourceError",
    "SourceNotFound",
    "SourceRegistry",
]
