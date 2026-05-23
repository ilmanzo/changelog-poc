"""Unit tests for src/openqa_fetcher.py — filesystem-based, no mocking needed."""
from __future__ import annotations

import pytest

from src.openqa_fetcher import scan_tests


def _setup_missing_dir(tmp_path):
    return tmp_path / "nonexistent"


def _setup_no_pm_files(tmp_path):
    (tmp_path / "tests").mkdir()
    return tmp_path


def _setup_no_package_header(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "no_pkg.pm").write_text("# Summary: just a summary\nsome perl code\n")
    return tmp_path


@pytest.mark.parametrize(
    "setup",
    [_setup_missing_dir, _setup_no_pm_files, _setup_no_package_header],
    ids=["missing_dir", "no_pm_files", "no_package_header"],
)
def test_scan_tests_empty_cases(tmp_path, setup) -> None:
    scan_root = setup(tmp_path)
    assert scan_tests(scan_root) == []


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
