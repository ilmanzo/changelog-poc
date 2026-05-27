#!/usr/bin/env bash
# Record all VHS demo GIFs in one shot.
#
# Usage:
#   scripts/record_demos.sh              # record all tapes
#   scripts/record_demos.sh demo_search  # record a single tape (no extension)
#
# What it does:
#   1. Ensures infra (PostgreSQL) is running
#   2. Registers the MCP server with gemini-cli
#   3. Records each .tape file into a .gif with the same base name
#   4. Removes the gemini registration on exit
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."

TAPE_DIR="docs/vhs"

die() { echo "error: $*" >&2; exit 1; }

command -v vhs  >/dev/null 2>&1 || die "vhs not found -- install from https://github.com/charmbracelet/vhs"
command -v gemini >/dev/null 2>&1 || die "gemini CLI not found"

# --- infra ---
echo "=== Ensuring infrastructure is running ==="
infra/infra.sh start

# --- register MCP server with gemini ---
echo "=== Registering MCP server with gemini ==="
scripts/register.sh add gemini

cleanup() {
    echo "=== Removing gemini MCP registration ==="
    scripts/register.sh remove gemini
}
trap cleanup EXIT

# --- determine which tapes to record ---
if [[ $# -gt 0 ]]; then
    TAPES=()
    for name in "$@"; do
        tape="${TAPE_DIR}/${name%.tape}.tape"
        [[ -f "$tape" ]] || die "tape not found: $tape"
        TAPES+=("$tape")
    done
else
    mapfile -t TAPES < <(find "$TAPE_DIR" -name '*.tape' | sort)
fi

[[ ${#TAPES[@]} -gt 0 ]] || die "no .tape files found in $TAPE_DIR"

HAS_FFMPEG=false
command -v ffmpeg >/dev/null 2>&1 && HAS_FFMPEG=true

HAS_GIFSICLE=false
command -v gifsicle >/dev/null 2>&1 && HAS_GIFSICLE=true

# --- record ---
for tape in "${TAPES[@]}"; do
    base="$(basename "${tape%.tape}")"
    gif="${TAPE_DIR}/${base}.gif"
    echo ""
    echo "=== Recording ${base} ==="
    vhs "$tape"

    if $HAS_FFMPEG; then
        tmp="${gif%.gif}_dedup.gif"
        # mpdecimate: drop near-duplicate frames (cuts the long pre-output pause).
        ffmpeg -loglevel error -i "$gif" \
            -filter_complex "[0:v]mpdecimate,setpts=N/FRAME_RATE/TB,split=2[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer" \
            -y "$tmp" \
            && mv "$tmp" "$gif"
        echo "    removed static frames"
    fi

    if $HAS_GIFSICLE; then
        orig_size=$(stat -c%s "$gif")
        # --no-loopcount stops the gif on its last frame (no loop, no need to pause).
        # tpad-based end-frame hold was attempted but paletteuse collapses identical
        # trailing frames; gifsicle has no built-in way to scope --delay to one frame
        # without filtering the others away, so we just drop the loop instead.
        gifsicle --batch --no-loopcount --optimize=3 --lossy=80 "$gif"
        new_size=$(stat -c%s "$gif")
        saved=$(( (orig_size - new_size) * 100 / orig_size ))
        echo "    optimized: ${saved}% smaller, loop disabled"
    fi

    echo "    -> ${gif}"
done

echo ""
echo "Done. Recorded ${#TAPES[@]} demo(s)."
if ! $HAS_FFMPEG; then
    echo "Tip: install ffmpeg to auto-remove static frames from GIFs (zypper in ffmpeg)"
fi
if ! $HAS_GIFSICLE; then
    echo "Tip: install gifsicle for automatic GIF optimization (zypper in gifsicle)"
fi
