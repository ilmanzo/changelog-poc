#!/usr/bin/env bash
# Usage: ./scripts/lint.sh [check|fix|format|all|ci]
#
#   check  -- ruff lint, no fixes (default)
#   fix    -- ruff lint with --fix
#   format -- ruff format (write)
#   all    -- ruff check + format + mypy
#   ci     -- ruff check + ruff format --check + mypy; non-zero on any failure
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:-check}"

TARGETS=(src mcp_server.py scripts/)

run_ruff_check() {
    echo "=== ruff check ==="
    uv run ruff check "${TARGETS[@]}"
}

run_ruff_fix() {
    echo "=== ruff check --fix ==="
    uv run ruff check --fix "${TARGETS[@]}"
}

run_ruff_format() {
    echo "=== ruff format ==="
    uv run ruff format "${TARGETS[@]}"
}

run_ruff_format_check() {
    echo "=== ruff format --check ==="
    uv run ruff format --check "${TARGETS[@]}"
}

run_mypy() {
    echo "=== mypy ==="
    PYTHONPATH=. uv run mypy src mcp_server.py
}

case "$MODE" in
    check)
        run_ruff_check
        ;;
    fix)
        run_ruff_fix
        ;;
    format)
        run_ruff_format
        ;;
    all)
        run_ruff_check
        run_ruff_format
        run_mypy
        ;;
    ci)
        run_ruff_check
        run_ruff_format_check
        run_mypy
        ;;
    *)
        echo "Usage: $0 [check|fix|format|all|ci]" >&2
        exit 1
        ;;
esac
