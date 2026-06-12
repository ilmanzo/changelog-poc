#!/usr/bin/env bash
# Usage:
#   ./scripts/test.sh [unit|e2e|e2e-db|e2e-gemini|e2e-opencode|e2e-edge|all] [pytest args...]
#
# e2e-edge: opencode edge-case prompts only (subset of e2e-opencode).
#   ./scripts/test.sh e2e-edge                       # all edge_* cases
#   ./scripts/test.sh e2e-edge bash_bypass           # filter by substring
#   ./scripts/test.sh e2e-edge multi_tool_chain -x   # filter + pytest args
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:-unit}"
shift || true

UNIT_ARGS=(
    tests/
    --ignore=tests/test_e2e_gemini.py
    --ignore=tests/test_e2e_opencode.py
    --ignore=tests/test_db.py
    -m "not e2e"
    --cov=src
    --cov-report=term-missing
)

E2E_DB_ARGS=(
    tests/test_db.py
    -m e2e
    -v
)

E2E_GEMINI_ARGS=(
    tests/test_e2e_gemini.py
    -m e2e
    -v
)

E2E_OPENCODE_ARGS=(
    tests/test_e2e_opencode.py
    -m e2e
    -v
)

run_unit() {
    echo "=== Unit tests ==="
    PYTHONPATH=. uv run pytest "${UNIT_ARGS[@]}" "$@"
}

run_e2e_db() {
    echo "=== DB integration tests ==="
    export DOCKER_HOST="unix:///run/user/${UID}/podman/podman.sock"
    export TESTCONTAINERS_RYUK_DISABLED=true
    PYTHONPATH=. uv run pytest "${E2E_DB_ARGS[@]}" "$@"
}

run_e2e_gemini() {
    echo "=== Gemini e2e tests ==="
    export DOCKER_HOST="unix:///run/user/${UID}/podman/podman.sock"
    export TESTCONTAINERS_RYUK_DISABLED=true
    PYTHONPATH=. uv run pytest "${E2E_GEMINI_ARGS[@]}" "$@"
}

run_e2e_opencode() {
    echo "=== OpenCode e2e tests ==="
    export DOCKER_HOST="unix:///run/user/${UID}/podman/podman.sock"
    export TESTCONTAINERS_RYUK_DISABLED=true
    PYTHONPATH=. uv run pytest "${E2E_OPENCODE_ARGS[@]}" "$@"
}

run_e2e_edge() {
    echo "=== OpenCode edge-case prompts ==="
    local filter="edge_"
    if [[ $# -gt 0 && "$1" != -* ]]; then
        filter="edge_${1#edge_}"
        shift
    fi
    export DOCKER_HOST="unix:///run/user/${UID}/podman/podman.sock"
    export TESTCONTAINERS_RYUK_DISABLED=true
    PYTHONPATH=. uv run pytest "${E2E_OPENCODE_ARGS[@]}" -k "$filter" "$@"
}

case "$MODE" in
    unit)
        run_unit "$@"
        ;;
    e2e)
        run_e2e_db "$@"
        run_e2e_gemini "$@"
        run_e2e_opencode "$@"
        ;;
    e2e-db)
        run_e2e_db "$@"
        ;;
    e2e-gemini)
        run_e2e_gemini "$@"
        ;;
    e2e-opencode)
        run_e2e_opencode "$@"
        ;;
    e2e-edge)
        run_e2e_edge "$@"
        ;;
    all)
        run_unit "$@"
        run_e2e_db "$@"
        run_e2e_gemini "$@"
        run_e2e_opencode "$@"
        ;;
    *)
        echo "Usage: $0 [unit|e2e|e2e-db|e2e-gemini|e2e-opencode|e2e-edge|all] [pytest args...]" >&2
        exit 1
        ;;
esac
