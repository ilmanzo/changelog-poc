"""Unit tests for src/spec_url_extractor.py — pure text, no network."""
from __future__ import annotations

import pytest

from src.spec_url_extractor import extract_upstream_urls

SPEC_GITHUB = """\
Name:           vim
Version:        9.1
URL:            https://github.com/vim/vim
Source0:        https://github.com/vim/vim/archive/v%{version}.tar.gz
"""

SPEC_GITLAB = """\
Name:           glib2
Version:        2.80
URL:            https://gitlab.gnome.org/GNOME/glib
Source:         https://gitlab.gnome.org/GNOME/glib/-/archive/%{version}/glib-%{version}.tar.bz2
"""

SPEC_NO_FORGE = """\
Name:           coreutils
Version:        9.4
URL:            https://www.gnu.org/software/coreutils/
Source0:        https://ftp.gnu.org/gnu/coreutils/coreutils-%{version}.tar.xz
"""

SPEC_MACROS_IN_SOURCE = """\
Name:           example
URL:            https://github.com/owner/repo
Source0:        https://github.com/%{name}/%{name}/archive/v%{version}.tar.gz
"""


def test_github_url_and_source() -> None:
    urls = extract_upstream_urls(SPEC_GITHUB)
    assert len(urls) == 1
    assert urls[0] == "https://github.com/vim/vim"


def test_gitlab_gnome() -> None:
    urls = extract_upstream_urls(SPEC_GITLAB)
    assert len(urls) == 1
    assert urls[0] == "https://gitlab.gnome.org/GNOME/glib"


def test_no_forge_returns_empty() -> None:
    assert extract_upstream_urls(SPEC_NO_FORGE) == []


def test_deduplication() -> None:
    urls = extract_upstream_urls(SPEC_MACROS_IN_SOURCE)
    assert len(urls) == 1
    assert urls[0] == "https://github.com/owner/repo"


def test_empty_spec() -> None:
    assert extract_upstream_urls("") == []


def test_url_tag_case_insensitive() -> None:
    spec = "url:   https://github.com/foo/bar\n"
    urls = extract_upstream_urls(spec)
    assert len(urls) == 1
    assert "github.com/foo/bar" in urls[0]


def test_codeberg_recognised() -> None:
    spec = "URL:   https://codeberg.org/user/repo\n"
    urls = extract_upstream_urls(spec)
    assert len(urls) == 1


def test_multiple_sources() -> None:
    spec = (
        "URL:   https://github.com/a/b\n"
        "Source0: https://gitlab.com/c/d/archive/v1.tar.gz\n"
    )
    urls = extract_upstream_urls(spec)
    assert len(urls) == 2
    assert "github.com/a/b" in urls[0]
    assert "gitlab.com/c/d" in urls[1]
