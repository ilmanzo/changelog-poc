"""Unit tests for src/test_coverage_parser.py — pure text parsing."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from src.test_coverage_parser import (
    extract_package_from_path,
    extract_package_refs,
    scan_test_directory,
)


ZYPPER_INSTALL = dedent("""\
    sub run {
        zypper_call("install vim");
        zypper_install("wget");
    }
""")

ENSURE_INSTALLED = dedent("""\
    sub run {
        ensure_installed("openssl curl");
    }
""")

PACKAGE_HEADER = dedent("""\
    # Summary: Install and verify Apache
    # Package: apache2
    # Package: apache2-utils
    sub run {
        install_package("apache2");
    }
""")

NO_PACKAGES = dedent("""\
    sub run {
        my $result = script_run("ls -la");
    }
""")


def test_zypper_call() -> None:
    refs = extract_package_refs(ZYPPER_INSTALL)
    assert "vim" in refs
    assert "wget" in refs


def test_ensure_installed_splits_space_separated() -> None:
    refs = extract_package_refs(ENSURE_INSTALLED)
    assert "openssl" in refs
    assert "curl" in refs


def test_package_header() -> None:
    refs = extract_package_refs(PACKAGE_HEADER)
    assert "apache2" in refs
    assert "apache2-utils" in refs


def test_install_package_also_matched() -> None:
    refs = extract_package_refs(PACKAGE_HEADER)
    assert "apache2" in refs


def test_no_packages_returns_empty() -> None:
    assert extract_package_refs(NO_PACKAGES) == set()


def test_ignores_flags() -> None:
    text = 'zypper_call("install -y --force")\n'
    refs = extract_package_refs(text)
    assert refs == set()


# ---------------------------------------------------------------------------
# extract_package_from_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("tests/installation/install_vim.pm", "vim"),
        ("tests/update_openssl.pm", "openssl"),
        ("tests/verify_apache2.pm", "apache2"),
        ("tests/test_curl.pm", "curl"),
        ("tests/random_module.pm", None),
    ],
    ids=["install", "update", "verify", "test", "no_prefix"],
)
def test_extract_package_from_path(path: str, expected: str | None) -> None:
    assert extract_package_from_path(path) == expected


# ---------------------------------------------------------------------------
# scan_test_directory
# ---------------------------------------------------------------------------


def test_scan_test_directory(tmp_path: Path) -> None:
    test_dir = tmp_path / "tests" / "installation"
    test_dir.mkdir(parents=True)
    (test_dir / "install_vim.pm").write_text(
        'sub run { zypper_install("vim"); }\n'
    )
    (test_dir / "generic.pm").write_text(
        "sub run { }\n"
    )

    results = scan_test_directory(tmp_path)
    assert len(results) == 1
    key = "tests/installation/install_vim.pm"
    assert key in results
    assert "vim" in results[key]
