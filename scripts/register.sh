#!/usr/bin/env bash
# Usage: ./scripts/register.sh {add|remove|status} [claude|gemini|all]
#
# Registers the rpm-mcp MCP server with Claude Code and/or Gemini CLI.
# Resolves the repo root automatically. Set DATABASE_URL in the environment
# to override the default DSN baked into the registration.
set -euo pipefail

SERVER_NAME="rpm-mcp"
REPO_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
GEMINI_SETTINGS="${HOME}/.gemini/settings.json"
DB_URL="${DATABASE_URL:-postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp}"

usage() {
    echo "Usage: $0 {add|remove|status} [claude|gemini|all]" >&2
    exit 2
}

die() { echo "error: $*" >&2; exit 1; }

require() { command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"; }

# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------
claude_add() {
    require claude
    if claude mcp list 2>/dev/null | grep -q "^${SERVER_NAME}\b"; then
        echo "claude: ${SERVER_NAME} already registered — removing first"
        claude mcp remove "${SERVER_NAME}" --scope user >/dev/null 2>&1 || true
    fi
    claude mcp add "${SERVER_NAME}" \
        --scope user \
        --env "DATABASE_URL=${DB_URL}" \
        -- uv run --directory "${REPO_DIR}" python mcp_server.py
    echo "claude: registered ${SERVER_NAME}"
}

claude_remove() {
    require claude
    claude mcp remove "${SERVER_NAME}" --scope user 2>/dev/null \
        && echo "claude: removed ${SERVER_NAME}" \
        || echo "claude: ${SERVER_NAME} was not registered"
}

claude_status() {
    if ! command -v claude >/dev/null 2>&1; then
        echo "claude: CLI not installed"
        return
    fi
    if claude mcp list 2>/dev/null | grep -q "^${SERVER_NAME}\b"; then
        claude mcp list | grep "^${SERVER_NAME}\b"
    else
        echo "claude: ${SERVER_NAME} not registered"
    fi
}

# ---------------------------------------------------------------------------
# Gemini CLI
# ---------------------------------------------------------------------------
gemini_add() {
    require jq
    mkdir -p "$(dirname "${GEMINI_SETTINGS}")"
    [ -f "${GEMINI_SETTINGS}" ] || echo '{}' > "${GEMINI_SETTINGS}"
    cp "${GEMINI_SETTINGS}" "${GEMINI_SETTINGS}.bak.$(date +%s)"

    jq --arg name "${SERVER_NAME}" \
       --arg cwd "${REPO_DIR}" \
       --arg dsn "${DB_URL}" \
       '.mcpServers[$name] = {
           command: "uv",
           args: ["run", "python", "mcp_server.py"],
           cwd: $cwd,
           env: { DATABASE_URL: $dsn }
        }' "${GEMINI_SETTINGS}" > "${GEMINI_SETTINGS}.tmp"
    mv "${GEMINI_SETTINGS}.tmp" "${GEMINI_SETTINGS}"
    echo "gemini: registered ${SERVER_NAME} in ${GEMINI_SETTINGS}"
}

gemini_remove() {
    require jq
    if [ ! -f "${GEMINI_SETTINGS}" ]; then
        echo "gemini: ${GEMINI_SETTINGS} does not exist"
        return
    fi
    if ! jq -e --arg name "${SERVER_NAME}" '.mcpServers[$name]' \
            "${GEMINI_SETTINGS}" >/dev/null 2>&1; then
        echo "gemini: ${SERVER_NAME} not registered"
        return
    fi
    cp "${GEMINI_SETTINGS}" "${GEMINI_SETTINGS}.bak.$(date +%s)"
    jq --arg name "${SERVER_NAME}" 'del(.mcpServers[$name])' \
        "${GEMINI_SETTINGS}" > "${GEMINI_SETTINGS}.tmp"
    mv "${GEMINI_SETTINGS}.tmp" "${GEMINI_SETTINGS}"
    echo "gemini: removed ${SERVER_NAME}"
}

gemini_status() {
    if [ ! -f "${GEMINI_SETTINGS}" ]; then
        echo "gemini: ${GEMINI_SETTINGS} does not exist"
        return
    fi
    require jq
    if jq -e --arg name "${SERVER_NAME}" '.mcpServers[$name]' \
            "${GEMINI_SETTINGS}" >/dev/null 2>&1; then
        echo "gemini: ${SERVER_NAME} registered (cwd: $(jq -r --arg n "${SERVER_NAME}" '.mcpServers[$n].cwd' "${GEMINI_SETTINGS}"))"
    else
        echo "gemini: ${SERVER_NAME} not registered"
    fi
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
ACTION="${1:-}"
TARGET="${2:-all}"
[ -z "${ACTION}" ] && usage

case "${TARGET}" in
    claude|gemini|all) ;;
    *) usage ;;
esac

do_claude=false; do_gemini=false
case "${TARGET}" in
    claude) do_claude=true ;;
    gemini) do_gemini=true ;;
    all)    do_claude=true; do_gemini=true ;;
esac

case "${ACTION}" in
    add)
        ${do_claude} && claude_add
        ${do_gemini} && gemini_add
        ;;
    remove)
        ${do_claude} && claude_remove
        ${do_gemini} && gemini_remove
        ;;
    status)
        ${do_claude} && claude_status
        ${do_gemini} && gemini_status
        ;;
    *)
        usage
        ;;
esac
