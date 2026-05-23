"""Unit tests for src/openqa_fetcher.py — filesystem-based, no mocking needed."""
from __future__ import annotations

import pytest

from src.openqa_fetcher import scan_tests


def test_scan_tests_missing_dir(tmp_path) -> None:
    result = scan_tests(tmp_path / "nonexistent")
    assert result == []


def test_scan_tests_no_pm_files(tmp_path) -> None:
    (tmp_path / "tests").mkdir()
    result = scan_tests(tmp_path)
    assert result == []


def test_scan_tests_pm_without_package_header(tmp_path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "no_pkg.pm").write_text("# Summary: just a summary\nsome perl code\n")
    result = scan_tests(tmp_path)
    assert result == []


def test_scan_tests_single_package(tmp_path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "vim_test.pm").write_text(
        "# Package: vim\n# Summary: Vim editor smoke test\nuse strict;\n"
    )
    result = scan_tests(tmp_path)
    assert len(result) == 1
    assert result[0].package_name == "vim"
    assert result[0].summary == "Vim editor smoke test"
    assert "vim_test.pm" in result[0].test_path


def test_scan_tests_multiple_packages_on_one_line(tmp_path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "editors.pm").write_text(
        "# Package: vim, nano, emacs\n# Summary: Editor tests\n"
    )
    result = scan_tests(tmp_path)
    pkg_names = {r.package_name for r in result}
    assert {"vim", "nano", "emacs"} == pkg_names
    assert all(r.summary == "Editor tests" for r in result)


def test_scan_tests_no_summary(tmp_path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "nosummary.pm").write_text("# Package: curl\nuse strict;\n")
    result = scan_tests(tmp_path)
    assert len(result) == 1
    assert result[0].summary is None


def test_scan_tests_recursive(tmp_path) -> None:
    nested = tmp_path / "tests" / "network"
    nested.mkdir(parents=True)
    (nested / "wget_test.pm").write_text(
        "# Package: wget\n# Summary: wget download test\n"
    )
    result = scan_tests(tmp_path)
    assert len(result) == 1
    assert "network" in result[0].test_path


def test_scan_tests_relative_path_in_result(tmp_path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "foo.pm").write_text("# Package: foo\n")
    result = scan_tests(tmp_path)
    # path should be relative to repo root, not absolute
    assert not result[0].test_path.startswith("/")
