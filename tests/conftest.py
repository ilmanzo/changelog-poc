"""Shared fixtures for rpm-mcp unit tests."""
from __future__ import annotations

import pytest


@pytest.fixture
def sample_changes_text() -> str:
    """Minimal realistic .changes file with two entries (OBS format)."""
    return """\
-------------------------------------------------------------------
Thu Jan  4 10:30:00 UTC 2024 - user@example.com

- Update to version 9.2.0100:
  * Fix CVE-2024-1234 (bsc#1234567)
  * Performance improvements

-------------------------------------------------------------------
Wed Dec  6 08:15:00 UTC 2023 - maintainer@example.com

- Security fixes for 9.1.123
  * bsc#1111111 - Fix memory corruption

"""


@pytest.fixture
def sample_rpm_changelog_text() -> str:
    """Sample rpm --changelog output with two entries."""
    return """\
* Mon Jan  8 2024 Some Packager <packager@example.com> - 9.2.0100
- Fix buffer overflow (CVE-2024-1234, bsc#1234567)
- Performance improvements

* Thu Dec  7 2023 Another Packager <other@example.com> - 9.1.123
- Update to version 9.1.123
- Security update
"""


@pytest.fixture
def sample_spec_text() -> str:
    """Minimal valid spec file for parser tests."""
    return """\
Name:           testpkg
Version:        1.0
Release:        1%{?dist}
Summary:        A test package
License:        MIT

%description
A minimal test package for unit testing.

%prep
%autosetup -p1

%build
%make_build

%install
%make_install

%check
make test

%changelog
* Mon Jan  8 2024 Packager <packager@example.com> - 1.0-1
- Initial release
"""
