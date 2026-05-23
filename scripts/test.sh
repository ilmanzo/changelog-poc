#!/usr/bin/env bash
# Usage: ./scripts/test.sh [unit|e2e|all] [extra pytest args...]
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:-unit}"
shift || true

UNIT_ARGS=(
    tests/
    --ignore=tests/test_e2e_gemini.py
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

case "$MODE" in
    unit)
        run_unit "$@"
        ;;
    e2e)
        run_e2e_db "$@"
        run_e2e_gemini "$@"
        ;;
    e2e-db)
        run_e2e_db "$@"
        ;;
    e2e-gemini)
        run_e2e_gemini "$@"
        ;;
    all)
        run_unit "$@"
        run_e2e_db "$@"
        run_e2e_gemini "$@"
        ;;
    *)
        echo "Usage: $0 [unit|e2e|e2e-db|e2e-gemini|all] [extra pytest args...]" >&2
        exit 1
        ;;
esac
