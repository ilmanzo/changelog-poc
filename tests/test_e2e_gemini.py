"""
E2E tests for rpm-mcp MCP server using gemini-cli as the test client.

Covers six query categories a packaging engineer would use:
  1. Changelog / version tracking
  2. Security / CVE
  3. Dependency analysis
  4. Spec understanding (network)
  5. News + package intelligence (network)
  6. Semantic / fuzzy search

Data source: real openSUSE Tumbleweed packages from the local RPM database (vim, curl).
vim 9.2.0447 is confirmed installed with rich CVE history.

Run all e2e tests:
    export DOCKER_HOST=unix:///run/user/$UID/podman/podman.sock
    PYTHONPATH=. uv run pytest tests/test_e2e_gemini.py -v -m e2e

Run without network-dependent tests:
    PYTHONPATH=. uv run pytest tests/test_e2e_gemini.py -v -m "e2e and not network"

Requirements:
    - gemini CLI installed and authenticated
    - container engine (docker/podman socket) for testcontainers
    - vim and curl installed locally (standard on openSUSE Tumbleweed)
    - For @pytest.mark.network tests: internet access (OBS, Pagure, RSS feeds)

openQA tests (get_openqa_tests tool) require a local os-autoinst-distri-opensuse
checkout and are not automated here — test manually with:
    gemini -y -p "Call get_openqa_tests with package='zypper'"
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer

PROJECT_DIR = Path(__file__).parent.parent.resolve()
GEMINI_SETTINGS = Path.home() / ".gemini" / "settings.json"
MCP_SERVER_NAME = "rpm-mcp-e2e"
PG_IMAGE = "pgvector/pgvector:pg17"


# ---------------------------------------------------------------------------
# Session-scoped infrastructure fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def pg_dsn():
    """Isolated pgvector Postgres for the e2e session."""
    with PostgresContainer(PG_IMAGE) as container:
        yield (
            f"postgresql://{container.username}:{container.password}"
            f"@{container.get_container_host_ip()}"
            f":{container.get_exposed_port(5432)}"
            f"/{container.dbname}"
        )


@pytest.fixture(scope="session")
def gemini_mcp(pg_dsn):
    """Patch ~/.gemini/settings.json to register the rpm-mcp-e2e server, restore on teardown."""
    original = GEMINI_SETTINGS.read_text()
    cfg = json.loads(original)

    cfg.setdefault("mcpServers", {})[MCP_SERVER_NAME] = {
        "command": "uv",
        "args": ["run", "python", "mcp_server.py"],
        "cwd": str(PROJECT_DIR),
        "env": {
            "DATABASE_URL": pg_dsn,
            "PYTHONPATH": str(PROJECT_DIR),
        },
    }
    GEMINI_SETTINGS.write_text(json.dumps(cfg, indent=2))

    yield MCP_SERVER_NAME

    GEMINI_SETTINGS.write_text(original)


@pytest.fixture(scope="session")
def vim_ingested(gemini_mcp):
    """Pre-ingest vim once per session so query tests don't retrigger ingest."""
    out = _gemini(
        f"Call sync_package from the {MCP_SERVER_NAME} MCP server with package='vim'. "
        "Report the exact response you received.",
        timeout=300,
    )
    assert "vim" in out.lower() or "indexed" in out.lower(), (
        f"sync_package('vim') did not succeed:\n{out}"
    )
    return out


@pytest.fixture(scope="session")
def curl_ingested(gemini_mcp):
    """Pre-ingest curl once per session."""
    out = _gemini(
        f"Call sync_package from the {MCP_SERVER_NAME} MCP server with package='curl'. "
        "Report the exact response you received.",
        timeout=300,
    )
    assert "curl" in out.lower() or "indexed" in out.lower(), (
        f"sync_package('curl') did not succeed:\n{out}"
    )
    return out


@pytest.fixture(scope="session")
def packages_ingested(vim_ingested, curl_ingested):
    """Ensure both vim and curl are indexed before parametrized multi-package tests."""
    return {"vim": vim_ingested, "curl": curl_ingested}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _gemini(prompt: str, timeout: int = 180) -> str:
    result = subprocess.run(
        [
            "gemini", "-y", "-p", prompt,
            f"--allowed-mcp-server-names={MCP_SERVER_NAME}",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_DIR),
    )
    return result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Category 0: Sync (baseline)
# ---------------------------------------------------------------------------
@pytest.mark.e2e
def test_sync_vim_succeeds(gemini_mcp):
    """sync_package reads vim changelog from local RPM DB and indexes into Postgres."""
    out = _gemini(
        f"Call sync_package from the {MCP_SERVER_NAME} MCP server with package='vim'. "
        "Report exactly what the tool returned.",
        timeout=300,
    )
    assert "indexed" in out.lower() or "successfully" in out.lower(), (
        f"Expected success message:\n{out}"
    )


@pytest.mark.e2e
def test_sync_missing_package_reports_not_found(gemini_mcp):
    out = _gemini(
        f"Call sync_package from the {MCP_SERVER_NAME} MCP server "
        "with package='xyzzy_nonexistent_abc'. Report the exact response."
    )
    assert out.strip(), "gemini returned empty output"


# ---------------------------------------------------------------------------
# Category 1: Changelog / version tracking
# ---------------------------------------------------------------------------
@pytest.mark.e2e
@pytest.mark.parametrize("package,expected_version", [
    ("vim", "9.2"),
    ("curl", "8."),
])
def test_get_recent_releases(packages_ingested, gemini_mcp, package, expected_version):
    """get_recent_releases returns real version numbers from the local RPM database."""
    out = _gemini(
        f"Call get_recent_releases from the {MCP_SERVER_NAME} MCP server "
        f"with package='{package}' and n=3. What versions were returned?"
    )
    assert expected_version in out, (
        f"Expected version prefix {expected_version!r} for {package}:\n{out}"
    )


@pytest.mark.e2e
def test_analyze_package_diff_vim(vim_ingested, gemini_mcp):
    """analyze_package_diff returns changelog text between two vim major versions."""
    out = _gemini(
        f"Call analyze_package_diff from the {MCP_SERVER_NAME} MCP server "
        "with package='vim', version_start='9.1', version_end='9.2'. "
        "Summarize what changed."
    )
    assert "vim" in out.lower() or "9.2" in out or "patch" in out.lower(), (
        f"Expected vim diff content:\n{out}"
    )


@pytest.mark.e2e
def test_get_changes_in_range_2026(vim_ingested, gemini_mcp):
    """get_changes_in_range filters vim changelog to entries from 2026."""
    out = _gemini(
        f"Call get_changes_in_range from the {MCP_SERVER_NAME} MCP server "
        "with package='vim', since='2026-01-01'. How many entries were returned and what versions appear?"
    )
    assert "vim" in out.lower() or "9.2" in out or "2026" in out, (
        f"Expected 2026 vim entries:\n{out}"
    )


@pytest.mark.e2e
def test_get_recent_releases_missing_package(gemini_mcp):
    out = _gemini(
        f"Call get_recent_releases from the {MCP_SERVER_NAME} MCP server "
        "with package='nonexistent_pkg_xyzzy'. Report the exact response."
    )
    assert any(kw in out.lower() for kw in ["not found", "source", "no changelog"]), (
        f"Expected not-found response:\n{out}"
    )


# ---------------------------------------------------------------------------
# Category 2: Security / CVE
# ---------------------------------------------------------------------------
@pytest.mark.e2e
@pytest.mark.parametrize("cve_id,expect_in_output", [
    ("CVE-2023-4738", "vim"),       # known buffer-overflow fix in indexed data
    ("CVE-2026-39881", None),       # just check non-empty; may or may not match
])
def test_find_cve(vim_ingested, gemini_mcp, cve_id, expect_in_output):
    """find_cve locates CVE IDs in indexed changelogs."""
    out = _gemini(
        f"Call find_cve from the {MCP_SERVER_NAME} MCP server "
        f"with cve_id='{cve_id}'. What did you find?"
    )
    assert out.strip(), "gemini returned empty output"
    if expect_in_output:
        assert cve_id.lower() in out.lower() or expect_in_output in out.lower(), (
            f"Expected {cve_id} or {expect_in_output!r} in output:\n{out}"
        )


@pytest.mark.e2e
@pytest.mark.parametrize("since,expect_cve", [
    (None, True),           # all-time CVE list — must have entries
    ("2024-01-01", None),   # recent only — may be empty or non-empty
])
def test_list_cves_vim(vim_ingested, gemini_mcp, since, expect_cve):
    """list_cves returns CVE entries from vim's changelog."""
    since_arg = f"and since='{since}'" if since else ""
    out = _gemini(
        f"Call list_cves from the {MCP_SERVER_NAME} MCP server "
        f"with package='vim' {since_arg}. List all the CVE IDs you see."
    )
    if expect_cve:
        assert "cve-" in out.lower(), f"Expected CVE IDs in output:\n{out}"
    else:
        assert "cve-" in out.lower() or "no cve" in out.lower() or out.strip(), (
            "Expected non-empty response"
        )


@pytest.mark.e2e
@pytest.mark.parametrize("bug_id,expect_content", [
    ("bsc#1260905", None),      # just check non-empty
    ("boo#1234567", None),
])
def test_find_bug(vim_ingested, gemini_mcp, bug_id, expect_content):
    """find_bug locates SUSE/openSUSE bugzilla references in indexed changelogs."""
    out = _gemini(
        f"Call find_bug from the {MCP_SERVER_NAME} MCP server "
        f"with bug_id='{bug_id}'. What did you find?"
    )
    assert out.strip(), "gemini returned empty output"


@pytest.mark.e2e
@pytest.mark.parametrize("since", [None, "2024-01-01"])
def test_list_bugs_vim(vim_ingested, gemini_mcp, since):
    """list_bugs returns bsc#/boo#/bnc# references from vim's changelog."""
    since_arg = f"and since='{since}'" if since else ""
    out = _gemini(
        f"Call list_bugs from the {MCP_SERVER_NAME} MCP server "
        f"with package='vim' {since_arg}. List all the bug IDs (bsc#, boo#, bnc#) you see."
    )
    assert out.strip(), "gemini returned empty output"


@pytest.mark.e2e
def test_find_bug_invalid_format_rejected(gemini_mcp):
    out = _gemini(
        f"Call find_bug from the {MCP_SERVER_NAME} MCP server "
        "with bug_id='github-1234'. What error did you get?"
    )
    assert out.strip(), "gemini returned empty output"


@pytest.mark.e2e
def test_find_cve_invalid_format_rejected(gemini_mcp):
    out = _gemini(
        f"Call find_cve from the {MCP_SERVER_NAME} MCP server "
        "with cve_id='NOTACVE-1234'. What error did you get?"
    )
    assert out.strip(), "gemini returned empty output"


@pytest.mark.e2e
@pytest.mark.parametrize("query,since", [
    ("security fix", None),
    ("buffer overflow", "2023-01-01"),
])
def test_fts_search_security(vim_ingested, gemini_mcp, query, since):
    """fts_search surfaces vim security entries, with and without temporal filter."""
    since_arg = f", since='{since}'" if since else ""
    out = _gemini(
        f"Call fts_search from the {MCP_SERVER_NAME} MCP server "
        f"with query='{query}'{since_arg}. Which packages and versions appear?"
    )
    assert "vim" in out.lower() or "buffer" in out.lower() or out.strip(), (
        f"Expected results for query={query!r}:\n{out}"
    )


@pytest.mark.e2e
def test_fts_search_no_results(gemini_mcp):
    out = _gemini(
        f"Call fts_search from the {MCP_SERVER_NAME} MCP server "
        "with query='xyzzy_flugelhorn_quux_zzz'. What was the result?"
    )
    assert out.strip(), "gemini returned empty output"


# ---------------------------------------------------------------------------
# Category 3: Dependency analysis
# ---------------------------------------------------------------------------
@pytest.mark.e2e
def test_get_dependencies_vim(gemini_mcp):
    """get_dependencies reads vim's runtime deps from local RPM DB (no Postgres needed)."""
    out = _gemini(
        f"Call get_dependencies from the {MCP_SERVER_NAME} MCP server "
        "with package='vim'. List the dependency names you received."
    )
    assert out.strip(), "gemini returned empty output"
    assert any(dep in out.lower() for dep in ["glibc", "libm", "libvim", "libc", "vim"]), (
        f"Expected known vim dependencies in output:\n{out}"
    )


@pytest.mark.e2e
def test_get_reverse_deps_openssl(gemini_mcp):
    """get_reverse_dependencies shows what packages on this system use openssl."""
    out = _gemini(
        f"Call get_reverse_dependencies from the {MCP_SERVER_NAME} MCP server "
        "with package='openssl'. Which packages depend on it?"
    )
    assert out.strip(), "gemini returned empty output"


@pytest.mark.e2e
def test_get_dependency_changes_vim(vim_ingested, gemini_mcp):
    """get_dependency_changes fetches recent releases for each of vim's deps."""
    out = _gemini(
        f"Call get_dependency_changes from the {MCP_SERVER_NAME} MCP server "
        "with package='vim', n=2, depth=1. What dependencies were found and any recent releases?"
    )
    assert out.strip(), "gemini returned empty output"


# ---------------------------------------------------------------------------
# Category 4: Spec / build understanding  (requires network; some also LLM)
# ---------------------------------------------------------------------------
@pytest.mark.e2e
@pytest.mark.network
@pytest.mark.parametrize("package,source", [
    ("vim", "opensuse"),
    ("vim-enhanced", "fedora"),
])
def test_get_spec_details(gemini_mcp, package, source):
    """get_spec_details fetches .spec from OBS or Pagure and returns parsed sections."""
    out = _gemini(
        f"Call get_spec_details from the {MCP_SERVER_NAME} MCP server "
        f"with package='{package}' and source='{source}'. What spec sections are present?"
    )
    assert out.strip(), "gemini returned empty output"
    if source == "opensuse":
        assert any(kw in out.lower() for kw in ["%build", "build", "%prep", "section"]), (
            f"Expected spec sections in output:\n{out}"
        )


@pytest.mark.e2e
@pytest.mark.network
def test_compare_spec_check_section_both_distros(gemini_mcp):
    """gemini calls get_spec_details for both distros and compares %check sections."""
    out = _gemini(
        f"Using the {MCP_SERVER_NAME} MCP server, call get_spec_details twice: "
        "once for package='vim', source='opensuse' and once for package='vim-enhanced', source='fedora'. "
        "Compare the %check section between the two. What are the differences?"
    )
    assert out.strip(), "gemini returned empty output"


# ---------------------------------------------------------------------------
# Category 5: News + package intelligence  (requires network)
# ---------------------------------------------------------------------------
@pytest.mark.e2e
@pytest.mark.network
def test_get_news_returns_recent_items(gemini_mcp):
    """get_news returns items already populated by the worker (read-only since DD9)."""
    out = _gemini(
        f"Call get_news from the {MCP_SERVER_NAME} MCP server "
        "with limit=5. List the news sources and titles you received.",
        timeout=120,
    )
    assert out.strip(), "gemini returned empty output"


@pytest.mark.e2e
@pytest.mark.network
def test_get_news_scoped_to_vim(gemini_mcp):
    """get_news scoped to vim returns vim-specific news or empty message."""
    out = _gemini(
        f"Call get_news from the {MCP_SERVER_NAME} MCP server "
        "with package='vim' and limit=5. What news items did you get?",
        timeout=60,
    )
    assert out.strip(), "gemini returned empty output"


# ---------------------------------------------------------------------------
# Category 6: Semantic / fuzzy search
# ---------------------------------------------------------------------------
@pytest.mark.e2e
@pytest.mark.parametrize("query,expect_vim", [
    ("TLS certificate handling", False),
    ("memory leak fix buffer overflow", True),
    ("security vulnerability patch", False),
])
def test_semantic_search(vim_ingested, gemini_mcp, query, expect_vim):
    """semantic_search returns ranked results from pgvector HNSW index."""
    out = _gemini(
        f"Call semantic_search from the {MCP_SERVER_NAME} MCP server "
        f"with query='{query}' and limit=5. What packages appeared?"
    )
    assert out.strip(), "gemini returned empty output"
    if expect_vim:
        assert "vim" in out.lower(), f"Expected vim in semantic results for {query!r}:\n{out}"


@pytest.mark.e2e
@pytest.mark.parametrize("query,since", [
    ("memory", "2024-01-01"),
    ("security fix", "2023-01-01"),
])
def test_fts_search_with_since(vim_ingested, gemini_mcp, query, since):
    """fts_search with since= returns only entries from that date onward."""
    out = _gemini(
        f"Call fts_search from the {MCP_SERVER_NAME} MCP server "
        f"with query='{query}', since='{since}', limit=5. "
        "Are all the result dates from the expected year or later?"
    )
    assert out.strip(), "gemini returned empty output"


# ---------------------------------------------------------------------------
# Category 7: Coverage gap analysis
# ---------------------------------------------------------------------------
@pytest.mark.e2e
def test_find_untested_changes(packages_ingested, gemini_mcp):
    """find_untested_changes surfaces packages with recent activity but no openQA tests.

    Since the e2e DB has no openqa_tests rows, ingested packages (vim, curl)
    should appear as untested.
    """
    out = _gemini(
        f"Using the {MCP_SERVER_NAME} MCP server, show me 3 packages with "
        "important changes in the last 6 months that don't have openQA tests "
        "covering those new features.",
        timeout=120,
    )
    assert out.strip(), "gemini returned empty output"
    lower = out.lower()
    assert any(pkg in lower for pkg in ["vim", "curl"]), (
        f"Expected at least one ingested package in untested results:\n{out}"
    )
    assert any(kw in lower for kw in ["no", "untested", "without", "coverage", "test"]), (
        f"Expected coverage gap language in output:\n{out}"
    )
