#!/usr/bin/env bash
# Run before `vhs docs/demo/cli.tape` so the recording shows warm-cache latency,
# not cold fetches. Each query is the same one cli.tape runs on-camera.
set -euo pipefail
cd "$(dirname "$0")/../.."

queries=(
    "find-cve CVE-2023-4738 vim"
    "semantic-search 'heap buffer overflow regex engine' --limit 3"
    "analyze-package-diff vim 9.0.2127 9.1.0"
    "find-bug bsc#1213018 vim"
    "get-dependency-changes curl --n 3 --depth 2"
    "get-recent-releases bash --n 5"
    "get-news --limit 5"
    "get-sync-status --threshold-days 7"
)

for q in "${queries[@]}"; do
    echo "prewarm: $q"
    eval "uv run mcp_server.py $q" > /dev/null 2>&1 || echo "  (failed -- ok if data not seeded)"
done
echo "done."
