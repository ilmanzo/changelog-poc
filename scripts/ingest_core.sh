#!/usr/bin/env bash
# Pre-ingest changelogs for the N most important core packages.
# Skips packages whose data is fresher than THRESHOLD_DAYS.
#
# Usage:
#   scripts/ingest_core.sh                       # top 100, base pattern
#   scripts/ingest_core.sh 50                    # top 50
#   scripts/ingest_core.sh 200 --seed-pattern enhanced_base
#
# Env overrides:
#   THRESHOLD_DAYS   freshness window (default: 7)
#   PARALLEL         concurrent ingest workers (default: 2)
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."

N="${1:-100}"
shift || true
THRESHOLD_DAYS="${THRESHOLD_DAYS:-7}"
PARALLEL="${PARALLEL:-2}"

echo "=== Resolving top $N core packages ==="
mapfile -t PACKAGES < <(
    ./rpm-mcp find-core-packages --n "$N" "$@" 2>/dev/null \
    | awk '/^\s+[0-9]+\./ {print $2}'
)

if [[ ${#PACKAGES[@]} -eq 0 ]]; then
    echo "No packages found. Is the seed pattern installed?" >&2
    exit 1
fi
echo "Resolved ${#PACKAGES[@]} packages."

echo ""
echo "=== Checking staleness (threshold: ${THRESHOLD_DAYS}d) ==="
SYNC_STATUS=$(./rpm-mcp get-sync-status --threshold-days "$THRESHOLD_DAYS" 2>/dev/null || true)
mapfile -t FRESH_PKGS < <(echo "$SYNC_STATUS" | awk '/^\s+\[ok\]/ {print $2}')

STALE=()
for pkg in "${PACKAGES[@]}"; do
    if ! printf '%s\n' "${FRESH_PKGS[@]+"${FRESH_PKGS[@]}"}" | grep -qx "$pkg"; then
        STALE+=("$pkg")
    fi
done

if [[ ${#STALE[@]} -eq 0 ]]; then
    echo "All ${#PACKAGES[@]} core packages are fresh. Nothing to do."
    exit 0
fi

echo "Stale or unsynced: ${#STALE[@]} / ${#PACKAGES[@]}"
printf '  %s\n' "${STALE[@]}"

echo ""
echo "=== Ingesting (parallelism: $PARALLEL) ==="
printf '%s\n' "${STALE[@]}" | xargs -P "$PARALLEL" -I{} ./rpm-mcp sync-package {}
echo ""
echo "Done."
