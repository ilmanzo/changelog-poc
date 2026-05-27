"""Pluggable changelog/spec/news/test sources."""
from __future__ import annotations

from .base import ChangelogSource, FetchResult, SourceError, SourceNotFound
from .fedora_source import FedoraSource
from .gitea_source import GiteaSource
from .github_source import GitHubSource
from .gitlab_source import GitLabSource
from .obs_source import ObsSource
from .registry import FetchStrategy, SourceRegistry
from .rpm_source import RpmSource
from .ubuntu_source import UbuntuSource

__all__ = [
    "ChangelogSource",
    "FedoraSource",
    "FetchResult",
    "FetchStrategy",
    "GitHubSource",
    "GitLabSource",
    "GiteaSource",
    "ObsSource",
    "RpmSource",
    "SourceError",
    "SourceNotFound",
    "SourceRegistry",
    "UbuntuSource",
]
