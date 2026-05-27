#!/usr/bin/env bash
# Render all docs/diagrams/src/*.mmd into docs/diagrams/*.svg via the
# official mermaid-cli container. Requires podman (or set RUNTIME=docker).
#
# No host install of node/npm/mermaid is needed.
#
# Usage:  ./scripts/render_diagrams.sh
#         RUNTIME=docker ./scripts/render_diagrams.sh
#         FORMAT=png ./scripts/render_diagrams.sh

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SRC_DIR="${REPO_ROOT}/docs/diagrams/src"
readonly OUT_DIR="${REPO_ROOT}/docs/diagrams"
readonly IMAGE="${IMAGE:-ghcr.io/mermaid-js/mermaid-cli/mermaid-cli:latest}"
readonly RUNTIME="${RUNTIME:-podman}"
readonly FORMAT="${FORMAT:-svg}"

if ! command -v "${RUNTIME}" >/dev/null 2>&1; then
    echo "error: '${RUNTIME}' not found. Install it or set RUNTIME=docker." >&2
    exit 1
fi

shopt -s nullglob
sources=("${SRC_DIR}"/*.mmd)
if [[ ${#sources[@]} -eq 0 ]]; then
    echo "no .mmd files in ${SRC_DIR}" >&2
    exit 0
fi

# Mount the diagrams dir into the container; mermaid-cli reads/writes relative
# to /data. The :z label keeps SELinux happy on rootful podman.
mount_opts="-v ${OUT_DIR}:/data"
if [[ "${RUNTIME}" == "podman" ]]; then
    mount_opts="${mount_opts}:z"
fi

echo "Rendering ${#sources[@]} diagram(s) via ${RUNTIME} (${IMAGE})..."
for src in "${sources[@]}"; do
    name="$(basename "${src}" .mmd)"
    rel_in="src/${name}.mmd"
    rel_out="${name}.${FORMAT}"
    echo "  ${rel_in} -> ${rel_out}"
    # shellcheck disable=SC2086 # word-splitting on mount_opts is intentional
    "${RUNTIME}" run --rm \
        --userns keep-id \
        --user "$(id -u):$(id -g)" \
        ${mount_opts} \
        "${IMAGE}" \
        -i "${rel_in}" \
        -o "${rel_out}" \
        --backgroundColor white \
        >/dev/null
done

echo "Done. Outputs in ${OUT_DIR}/"
