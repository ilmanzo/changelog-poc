"""
E2E tests for rpm-mcp MCP server using opencode CLI as the test client.

Design: each test submits a natural-language prompt to `opencode run --format json`
and asserts that the expected MCP tool was actually invoked — not on the LLM's text
output, which is non-deterministic. Tool invocations are detected from the NDJSON
event stream (event["type"] == "tool_use", event["part"]["tool"]).

Provider: Ollama (SUSE) — internal SUSE endpoint, model default:latest.
Requires network access to coding.op-prg2-gpu-1-ingress.op.suse.org (10.144.114.244).

Run:
    export DOCKER_HOST=unix:///run/user/$UID/podman/podman.sock
    PYTHONPATH=. uv run pytest tests/test_e2e_opencode.py -v -m e2e

Skip-network tests only (no Ollama required):
    # (currently all tests require network; marker is set at module level)
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer

PROJECT_DIR = Path(__file__).parent.parent.resolve()
OPENCODE_BIN = Path.home() / ".opencode" / "bin" / "opencode"
MCP_SERVER_NAME = "rpm-mcp-e2e"
PG_IMAGE = "pgvector/pgvector:pg17"
# Internal SUSE Ollama endpoint.
# coding.op-prg2-gpu-1-ingress.op.suse.org -> nginx-op-prg2-gpu-1.openplatform.suse.com -> 10.144.114.244
# Hostname only resolves via the SUSE internal DNS; OLLAMA_IP is used as fallback.
OLLAMA_HOSTNAME = "coding.op-prg2-gpu-1-ingress.op.suse.org"
OLLAMA_IP = "10.144.114.244"
OLLAMA_BASE_URL = f"http://{OLLAMA_HOSTNAME}/v1"

# ---------------------------------------------------------------------------
# Probe test cases: (prompt, expected_tool_keywords, timeout_s)
#
# expected_tool_keywords: any tool name *containing* one of these strings
# counts as a match — covers both the MCP direct path
# (e.g. "rpm_mcp_e2e_sync_package") and the bash-CLI fallback path
# (e.g. "mcp_server.py sync-package").
#
# timeout_s: per-test subprocess timeout.  Simple DB-read tools usually
# finish in <60 s; multi-step reasoning (untested_changes) needs more.
# ---------------------------------------------------------------------------
PROBE_CASES = [
    pytest.param(
        f"Call sync_package from the {MCP_SERVER_NAME} MCP server with package='vim'",
        {"sync_package", "sync-package"},
        120,
        id="sync",
    ),
    pytest.param(
        f"Using the {MCP_SERVER_NAME} MCP server, list the 3 most recent vim releases",
        {"get_recent_releases", "get-recent-releases"},
        120,
        id="recent_releases",
    ),
    pytest.param(
        f"Using the {MCP_SERVER_NAME} MCP server, what changed in vim between version 9.1 and 9.2?",
        {"analyze_package_diff", "analyze-package-diff", "compare_versions", "compare-versions"},
        120,
        id="diff",
    ),
    pytest.param(
        f"Using the {MCP_SERVER_NAME} MCP server, list all CVEs fixed in vim",
        {"list_cves", "list-cves", "find_cve", "find-cve"},
        120,
        id="cves",
    ),
    pytest.param(
        f"Using the {MCP_SERVER_NAME} MCP server, find bsc# bug references in curl",
        {"list_bugs", "list-bugs", "find_bug", "find-bug"},
        120,
        id="bugs",
    ),
    pytest.param(
        f"Using the {MCP_SERVER_NAME} MCP server, full-text search for TLS certificate fixes",
        {"fts_search", "fts-search", "semantic_search", "semantic-search"},
        120,
        id="search",
    ),
    pytest.param(
        f"Using the {MCP_SERVER_NAME} MCP server, what packages does vim depend on?",
        {"get_dependencies", "get-dependencies"},
        120,
        id="deps",
    ),
    pytest.param(
        f"Using the {MCP_SERVER_NAME} MCP server, which packages depend on openssl?",
        {"get_reverse_dependencies", "get-reverse-dependencies"},
        120,
        id="revdeps",
    ),
    pytest.param(
        f"Call find_untested_changes from the {MCP_SERVER_NAME} MCP server to find packages with security changes but no openQA test coverage",
        {"find_untested_changes", "find-untested-changes", "get_test_coverage", "get-test-coverage"},
        300,  # multi-step reasoning: needs extra time
        id="untested",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_ollama_url() -> str:
    """Return the best reachable base URL for the Ollama endpoint.

    Tries the hostname first (requires SUSE internal DNS); falls back to the
    raw IP with a Host header workaround embedded in the URL.  opencode/Node
    doesn't support per-request Host overrides, so we just use the IP directly
    and accept that TLS SNI won't apply (the endpoint is plain HTTP anyway).
    """
    import socket
    try:
        socket.getaddrinfo(OLLAMA_HOSTNAME, 80, proto=socket.IPPROTO_TCP)
        return OLLAMA_BASE_URL
    except OSError:
        return f"http://{OLLAMA_IP}/v1"


def _opencode_config(pg_dsn: str, ollama_url: str) -> str:
    """Inline opencode.json injected via OPENCODE_CONFIG_CONTENT env var."""
    cfg = {
        "model": "ollama/default:latest",
        "provider": {
            "ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama (SUSE)",
                "options": {"baseURL": ollama_url},
                "models": {"default:latest": {"name": "default:latest"}},
            }
        },
        "mcp": {
            MCP_SERVER_NAME: {
                "type": "local",
                "command": [
                    "uv", "run", "--directory", str(PROJECT_DIR),
                    "python", "mcp_server.py",
                ],
                "environment": {"DATABASE_URL": pg_dsn},
            }
        },
    }
    return json.dumps(cfg)


def _run(prompt: str, env: dict[str, str], timeout: int = 180) -> list[dict]:
    """
    Run `opencode run --format json` and return parsed NDJSON events.

    --pure: suppress user plugins (e.g. opencode-gemini-auth).
    --dangerously-skip-permissions: non-interactive auto-approval.
    --format json: emit one JSON object per line (event stream).
    """
    result = subprocess.run(
        [
            str(OPENCODE_BIN),
            "run",
            "--format", "json",
            "--dangerously-skip-permissions",
            "--pure",
            prompt,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_DIR),
        env=env,
    )
    events: list[dict] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def extract_tool_calls(events: list[dict]) -> list[str]:
    """
    Extract every tool invocation from the opencode JSON event stream.

    Two paths are handled:
      1. Direct MCP tool call: event["part"]["tool"] = "rpm_mcp_e2e_sync_package"
      2. Bash fallback: event["part"]["tool"] = "bash",
         event["part"]["state"]["input"]["command"] contains "mcp_server.py sync-package"
    """
    calls: list[str] = []
    for ev in events:
        if ev.get("type") != "tool_use":
            continue
        part = ev.get("part", {})
        tool = part.get("tool", "")
        if not tool:
            continue
        if tool == "bash":
            # Bash fallback: LLM called the CLI instead of the MCP protocol.
            # Extract the tool name from the command string.
            cmd: str = part.get("state", {}).get("input", {}).get("command", "")
            if "mcp_server.py" in cmd:
                # "uv run mcp_server.py get-recent-releases vim --n 2" -> "get-recent-releases"
                after = cmd.split("mcp_server.py")[-1].strip()
                subcommand = after.split()[0] if after.split() else ""
                if subcommand:
                    calls.append(subcommand)
        else:
            calls.append(tool)
    return calls


class AllOf(tuple):  # type: ignore[type-arg]
    """Sentinel wrapper meaning *every* group must match at least one call.

    Use in EDGE_CASES when a prompt must trigger a chain of tools, e.g.
        AllOf({"sync_package", "sync-package"}, {"list_cves", "list-cves"})
    asserts that both a sync_package call AND a list_cves call appear.
    """

    def __new__(cls, *groups: set[str]) -> "AllOf":
        return super().__new__(cls, groups)


def assert_tool_called(calls: list[str], expected: set[str] | AllOf) -> None:
    """Fail with a clear message if the expected calls did not occur.

    - set[str]: any one keyword matching any call passes.
    - AllOf(set, set, ...): each group must independently match some call.
    """
    if isinstance(expected, AllOf):
        missing = [grp for grp in expected if not _any_call_matches(calls, grp)]
        if missing:
            pytest.fail(
                f"Expected all of {[sorted(g) for g in expected]!r} to be invoked.\n"
                f"Missing groups: {[sorted(g) for g in missing]!r}\n"
                f"Actual tool calls: {calls or ['(none)']}"
            )
        return
    if _any_call_matches(calls, expected):
        return
    pytest.fail(
        f"Expected one of {sorted(expected)!r} to be invoked.\n"
        f"Actual tool calls: {calls or ['(none)']}"
    )


def _any_call_matches(calls: list[str], keywords: set[str]) -> bool:
    return any(any(kw in call for kw in keywords) for call in calls)


def assert_mcp_server_used(calls: list[str]) -> None:
    """Assert the MCP server was reached — directly or via the bash-CLI fallback.

    Fails when the model answers from training data (no tools) or fetches
    external web pages instead of querying our server.
    """
    if not calls:
        pytest.fail("No tools invoked at all — model answered from training data (hallucination risk).")
    mcp_calls = [c for c in calls if MCP_SERVER_NAME in c or "mcp_server" in c]
    if not mcp_calls:
        pytest.fail(
            f"MCP server was never reached.\n"
            f"Actual tool calls: {calls}\n"
            f"Model bypassed the server (webfetch, bash without mcp_server.py, etc.)."
        )


# ---------------------------------------------------------------------------
# Edge-case prompts: natural language, no explicit tool names.
#
# These test that the model reaches for the MCP server even when the prompt
# is phrased as a general question and the answer *could* come from training
# data or a web search.
#
# Each entry: (prompt, expected_tools, timeout_s)
#   expected_tools: specific acceptable calls.
#                   Use the sentinel ANY_MCP_TOOL to mean
#                   "just assert the MCP server was reached."
# ---------------------------------------------------------------------------
ANY_MCP_TOOL: set[str] = set()  # sentinel — checked by assert_mcp_server_used

EDGE_CASES = [
    # -- General-knowledge traps: model knows these from training data --------
    pytest.param(
        "Does vim have any security vulnerabilities?",
        {"list_cves", "list-cves", "find_cve", "find-cve"},
        120,
        id="edge_security_general_question",
    ),
    pytest.param(
        "Tell me about CVE-2023-4738",
        {"find_cve", "find-cve", "list_cves", "list-cves"},
        120,
        id="edge_named_cve_general",
    ),
    pytest.param(
        "Any CVEs fixed in openssl?",
        {"list_cves", "list-cves", "find_cve", "find-cve"},
        120,
        id="edge_cve_no_tool_name",
    ),

    # -- Natural phrasing without mentioning MCP or tool names ---------------
    pytest.param(
        "Show me what changed in curl last month",
        {"get_changes_in_range", "get-changes-in-range", "get_recent_releases", "get-recent-releases"},
        120,
        id="edge_temporal_relative",
    ),
    pytest.param(
        "What packages were recently updated?",
        {"get_news", "get-news", "get_recent_releases", "get-recent-releases", "get_sync_status", "get-sync-status"},
        120,
        id="edge_recent_updates_no_package",
    ),
    pytest.param(
        "Is openssl data up to date in the database?",
        {"get_sync_status", "get-sync-status"},
        120,
        id="edge_staleness_check",
    ),
    pytest.param(
        "Search for memory leak fixes",
        {"fts_search", "fts-search", "semantic_search", "semantic-search"},
        120,
        id="edge_natural_search",
    ),
    pytest.param(
        "What does openssl depend on?",
        {"get_dependencies", "get-dependencies"},
        120,
        id="edge_deps_no_tool",
    ),
    pytest.param(
        "Which packages have the most reverse dependencies?",
        {"find_core_packages", "find-core-packages", "get_reverse_dependencies", "get-reverse-dependencies"},
        120,
        id="edge_core_packages_indirect",
    ),
    pytest.param(
        "Are there openQA tests for vim?",
        {"get_test_coverage", "get-test-coverage"},
        180,
        id="edge_test_coverage_natural",
    ),

    # -- Terse / telegram-style prompts --------------------------------------
    pytest.param(
        "vim CVE list",
        {"list_cves", "list-cves", "find_cve", "find-cve"},
        180,
        id="edge_terse_cve",
    ),
    pytest.param(
        "curl changelog",
        {"get_recent_releases", "get-recent-releases", "get_changes_in_range", "get-changes-in-range",
         "analyze_package_diff", "analyze-package-diff"},
        120,
        id="edge_terse_changelog",
    ),
    pytest.param(
        "Find bsc#1234567",
        {"find_bug", "find-bug"},
        120,
        id="edge_bare_bug_id",
    ),

    # -- Tool-disambiguation: multiple valid tools exist ---------------------
    pytest.param(
        "Compare vim 9.0 with 9.1",
        {"analyze_package_diff", "analyze-package-diff", "compare_versions", "compare-versions"},
        180,
        id="edge_version_compare_ambiguous",
    ),
    pytest.param(
        "Find all bugs in vim",
        {"list_bugs", "list-bugs", "find_bug", "find-bug", "fts_search", "fts-search"},
        180,
        id="edge_bugs_tool_disambiguation",
    ),
    pytest.param(
        "Get news for curl",
        {"get_news", "get-news"},
        120,
        id="edge_news_terse",
    ),

    # -- Non-existent data: model should still call the tool, not give up ---
    pytest.param(
        "Get recent releases for python3-setuptools",
        {"get_recent_releases", "get-recent-releases", "sync_package", "sync-package"},
        240,
        id="edge_package_not_indexed",
    ),
    pytest.param(
        "Find CVE-9999-00001 in vim",
        {"find_cve", "find-cve"},
        120,
        id="edge_nonexistent_cve",
    ),

    # -- Spec file (requires network fetch) ----------------------------------
    pytest.param(
        "What's the spec file for vim?",
        {"get_spec_details", "get-spec-details"},
        120,
        id="edge_spec_natural",
    ),

    # -- Cascade / multi-step ------------------------------------------------
    pytest.param(
        "If curl gets a security patch, which other packages should I review?",
        {"get_reverse_dependencies", "get-reverse-dependencies", "get_dependency_changes", "get-dependency-changes"},
        180,
        id="edge_blast_radius_indirect",
    ),
    pytest.param(
        "ingest the bash package",
        {"sync_package", "sync-package", "sync_all_distros", "sync-all-distros"},
        180,
        id="edge_ingest_imperative",
    ),

    # -- Bash-bypass guard: LLM tempted to run `rpm -q` instead of MCP --------
    pytest.param(
        "Check in the package database what version of vim was last synced",
        {"get_recent_releases", "get-recent-releases", "get_sync_status", "get-sync-status"},
        120,
        id="edge_bash_bypass_rpm_q",
    ),

    # -- Webfetch guard: LLM tempted to scrape upstream --------------------
    pytest.param(
        "Latest curl release notes",
        {"get_recent_releases", "get-recent-releases", "get_news", "get-news"},
        120,
        id="edge_webfetch_release_notes",
    ),

    # -- Adversarial: wrong assumption the model should verify via MCP -----
    pytest.param(
        "I'm pretty sure vim's latest version is 8.0 — can you confirm that's accurate?",
        {"get_recent_releases", "get-recent-releases"},
        120,
        id="edge_adversarial_confirm_assumption",
    ),

    # -- Multi-tool chain: both calls must appear --------------------------
    pytest.param(
        "Sync curl then list its CVEs",
        AllOf(
            {"sync_package", "sync-package", "sync_all_distros", "sync-all-distros"},
            {"list_cves", "list-cves"},
        ),
        180,
        id="edge_multi_tool_chain",
    ),

    # -- Identifier normalization: spaces in CVE ID ------------------------
    pytest.param(
        "Find CVE 2023 4738 in vim",
        {"find_cve", "find-cve", "list_cves", "list-cves"},
        120,
        id="edge_cve_spaces",
    ),

    # -- Subpackage name normalization -------------------------------------
    pytest.param(
        "Recent releases of libcurl4",
        {"get_recent_releases", "get-recent-releases", "sync_package", "sync-package"},
        120,
        id="edge_subpackage_name",
    ),

    # -- Bug-ref alias (boo# vs bsc#) --------------------------------------
    pytest.param(
        "Find boo#1234567",
        {"find_bug", "find-bug"},
        120,
        id="edge_bug_alias_boo",
    ),

    # -- Dateparser stress: relative day -----------------------------------
    pytest.param(
        "What changed yesterday in vim?",
        {"get_changes_in_range", "get-changes-in-range", "get_recent_releases", "get-recent-releases"},
        120,
        id="edge_temporal_yesterday",
    ),

    # -- Advisory framing: LLM tempted to opine instead of querying --------
    pytest.param(
        "Should I upgrade vim?",
        {"get_recent_releases", "get-recent-releases", "list_cves", "list-cves",
         "analyze_package_diff", "analyze-package-diff"},
        120,
        id="edge_advisory_framing",
    ),
]

# ---------------------------------------------------------------------------
# Session-scoped infrastructure fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def _ollama_reachable() -> None:
    """Skip the entire session if the SUSE Ollama endpoint is unreachable.

    Uses urllib (stdlib) to avoid adding a new dependency.  Tries the
    hostname first, then the known IP directly, so the check works even
    when the internal SUSE DNS is not configured on the host.
    """
    urls = [
        f"{OLLAMA_BASE_URL}/models",
        f"http://{OLLAMA_IP}/v1/models",
    ]
    last_exc: Exception = Exception("no attempt made")
    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                headers={"Host": "coding.op-prg2-gpu-1-ingress.op.suse.org"},
            )
            with urllib.request.urlopen(req, timeout=5):
                return  # reachable
        except Exception as exc:
            last_exc = exc
    pytest.skip(
        f"SUSE Ollama endpoint unreachable ({OLLAMA_HOSTNAME}): {last_exc}\n"
        "  -> Make sure the SUSE VPN is active before running this suite."
    )


@pytest.fixture(scope="session")
def pg_dsn(_ollama_reachable: None) -> str:  # type: ignore[type-arg]
    """Isolated pgvector Postgres for the e2e session (testcontainers)."""
    with PostgresContainer(PG_IMAGE) as container:
        yield (
            f"postgresql://{container.username}:{container.password}"
            f"@{container.get_container_host_ip()}"
            f":{container.get_exposed_port(5432)}"
            f"/{container.dbname}"
        )


@pytest.fixture(scope="session")
def opencode_env(pg_dsn: str) -> dict[str, str]:
    """Environment dict for all opencode subprocess calls."""
    ollama_url = _resolve_ollama_url()
    env = os.environ.copy()
    env["OPENCODE_CONFIG_CONTENT"] = _opencode_config(pg_dsn, ollama_url)
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
    return env


@pytest.fixture(scope="session")
def vim_ingested(opencode_env: dict[str, str]) -> str:
    """Pre-ingest vim once; used as a dependency by tests that query vim data."""
    out_events = _run(
        f"Call sync_package from the {MCP_SERVER_NAME} MCP server with package='vim'.",
        env=opencode_env,
        timeout=300,
    )
    raw = " ".join(
        ev.get("part", {}).get("text", "")
        or str(ev.get("part", {}).get("state", {}).get("output", ""))
        for ev in out_events
    )
    assert "vim" in raw.lower() or "indexed" in raw.lower() or extract_tool_calls(out_events), (
        f"sync_package('vim') produced no tool call and no success text:\n{raw}"
    )
    return raw


@pytest.fixture(scope="session")
def curl_ingested(opencode_env: dict[str, str]) -> str:
    """Pre-ingest curl once."""
    out_events = _run(
        f"Call sync_package from the {MCP_SERVER_NAME} MCP server with package='curl'.",
        env=opencode_env,
        timeout=300,
    )
    raw = " ".join(
        ev.get("part", {}).get("text", "")
        or str(ev.get("part", {}).get("state", {}).get("output", ""))
        for ev in out_events
    )
    assert "curl" in raw.lower() or "indexed" in raw.lower() or extract_tool_calls(out_events), (
        f"sync_package('curl') produced no tool call and no success text:\n{raw}"
    )
    return raw


@pytest.fixture(scope="session")
def packages_ingested(vim_ingested: str, curl_ingested: str) -> dict[str, str]:
    return {"vim": vim_ingested, "curl": curl_ingested}


# ---------------------------------------------------------------------------
# Parametrized probe tests
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.network
@pytest.mark.parametrize("prompt,expected_tools,timeout_s", PROBE_CASES)
def test_tool_invoked(
    packages_ingested: dict[str, str],
    opencode_env: dict[str, str],
    prompt: str,
    expected_tools: set[str],
    timeout_s: int,
) -> None:
    """Submit *prompt* to opencode and assert the expected MCP tool was called."""
    events = _run(prompt, env=opencode_env, timeout=timeout_s)
    calls = extract_tool_calls(events)
    assert_tool_called(calls, expected_tools)


@pytest.mark.e2e
@pytest.mark.network
@pytest.mark.parametrize("prompt,expected_tools,timeout_s", EDGE_CASES)
def test_mcp_reached_on_natural_query(
    packages_ingested: dict[str, str],
    opencode_env: dict[str, str],
    prompt: str,
    expected_tools: set[str],
    timeout_s: int,
) -> None:
    """Natural-language prompts with no explicit tool or MCP server name.

    Two-tier assertion:
      1. Always: the MCP server was reached (not pure LLM or webfetch).
      2. When expected_tools is non-empty: a specific tool (or one from a set)
         was called — verifying the model picked the right tool for the task.
    """
    events = _run(prompt, env=opencode_env, timeout=timeout_s)
    calls = extract_tool_calls(events)
    assert_mcp_server_used(calls)
    if expected_tools:
        assert_tool_called(calls, expected_tools)
