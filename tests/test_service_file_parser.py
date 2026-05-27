"""Unit tests for src/service_file_parser.py — pure XML parsing."""
from __future__ import annotations

from src.service_file_parser import extract_urls_from_service

OBS_SCM_SERVICE = """\
<services>
  <service name="obs_scm">
    <param name="url">https://github.com/openSUSE/libzypp.git</param>
    <param name="scm">git</param>
    <param name="revision">master</param>
  </service>
</services>
"""

TAR_SCM_SERVICE = """\
<services>
  <service name="tar_scm">
    <param name="url">https://gitlab.com/procps-ng/procps</param>
    <param name="scm">git</param>
    <param name="versionformat">@PARENT_TAG@</param>
  </service>
  <service name="recompress">
    <param name="compression">xz</param>
  </service>
</services>
"""

NO_URL_SERVICE = """\
<services>
  <service name="set_version">
    <param name="basename">somepackage</param>
  </service>
</services>
"""

MALFORMED_XML = "this is not xml <at all"

MULTIPLE_SCM_SERVICES = """\
<services>
  <service name="obs_scm">
    <param name="url">https://github.com/a/b</param>
    <param name="scm">git</param>
  </service>
  <service name="tar_scm">
    <param name="url">https://gitlab.com/c/d</param>
    <param name="scm">git</param>
  </service>
</services>
"""


def test_obs_scm_extracts_url() -> None:
    urls = extract_urls_from_service(OBS_SCM_SERVICE)
    assert urls == ["https://github.com/openSUSE/libzypp.git"]


def test_tar_scm_extracts_url() -> None:
    urls = extract_urls_from_service(TAR_SCM_SERVICE)
    assert urls == ["https://gitlab.com/procps-ng/procps"]


def test_no_scm_service_returns_empty() -> None:
    assert extract_urls_from_service(NO_URL_SERVICE) == []


def test_malformed_xml_returns_empty() -> None:
    assert extract_urls_from_service(MALFORMED_XML) == []


def test_empty_string_returns_empty() -> None:
    assert extract_urls_from_service("") == []


def test_multiple_scm_services() -> None:
    urls = extract_urls_from_service(MULTIPLE_SCM_SERVICES)
    assert len(urls) == 2
    assert "github.com" in urls[0]
    assert "gitlab.com" in urls[1]
