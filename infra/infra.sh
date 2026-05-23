#!/bin/bash
# Manage the rpm-mcp infrastructure: PostgreSQL + pgvector (single container).
# Uses `podman` directly — no compose plugin required.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="rpm-mcp-postgres"
IMAGE="pgvector/pgvector:pg17"
DATA_DIR="$DIR/pg_data"
PORT="5432"

function start() {
    mkdir -p "$DATA_DIR"
    if podman container exists "$NAME"; then
        echo "Container $NAME already exists — starting it..."
        podman start "$NAME"
    else
        echo "Creating + starting $NAME..."
        podman run -d \
            --name "$NAME" \
            -e POSTGRES_DB=rpm_mcp \
            -e POSTGRES_USER=rpm_mcp \
            -e POSTGRES_PASSWORD=rpm_mcp \
            -p "${PORT}:5432" \
            -v "${DATA_DIR}:/var/lib/postgresql/data:Z" \
            --restart=unless-stopped \
            "$IMAGE"
    fi
    echo "Waiting for Postgres to accept connections..."
    for _ in $(seq 1 30); do
        if podman exec "$NAME" pg_isready -U rpm_mcp -d rpm_mcp >/dev/null 2>&1; then
            echo "ready."
            return
        fi
        sleep 1
    done
    echo "TIMEOUT: Postgres did not become ready in 30s" >&2
    exit 1
}

function stop() {
    echo "Stopping $NAME..."
    podman stop "$NAME" 2>/dev/null || true
}

function rm_container() {
    stop
    echo "Removing $NAME..."
    podman rm "$NAME" 2>/dev/null || true
}

function status() {
    podman ps -a --filter "name=$NAME"
}

function logs() {
    podman logs -f "$NAME"
}

function psql_shell() {
    podman exec -it "$NAME" psql -U rpm_mcp -d rpm_mcp
}

case "${1:-}" in
    start)  start ;;
    stop)   stop ;;
    rm)     rm_container ;;
    status) status ;;
    logs)   logs ;;
    psql)   psql_shell ;;
    *)
        echo "Usage: $0 {start|stop|rm|status|logs|psql}"
        exit 1
        ;;
esac
