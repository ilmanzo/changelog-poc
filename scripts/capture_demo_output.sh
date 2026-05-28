#!/usr/bin/env bash
# Run each demo prompt through gemini-cli and embed the clean output into
# docs/dev-diary.md immediately after each GIF embed.
#
# Output blocks in the diary are wrapped in:
#   <!-- demo-output:NAME -->
#   ...
#   <!-- /demo-output:NAME -->
# Re-running the script replaces existing blocks idempotently.
#
# Usage:
#   scripts/capture_demo_output.sh               # all tapes
#   scripts/capture_demo_output.sh demo_bugs     # single tape (no .tape extension)

set -euo pipefail

REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
TAPE_DIR="$REPO/docs/vhs"
DIARY="$REPO/docs/dev-diary.md"
BASE_URL="https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs"

strip_ansi() { sed 's/\x1b\[[0-9;]*[mKJH]//g; s/\r//g'; }

# Extract gemini prompt from a tape file
extract_prompt() {
    local tape="$1"
    grep '^Type' "$tape" \
        | sed "s/^Type \`gemini -y -p \"//; s/\" 2>\/dev\/null\`$//" \
        | head -1
}

# Run one capture: returns the clean output text
run_prompt() {
    local prompt="$1"
    gemini -y -p "$prompt" 2>/dev/null | strip_ansi
}

# Build the replacement block for the diary
build_block() {
    local name="$1" prompt="$2" output="$3"
    printf '<!-- demo-output:%s -->\n' "$name"
    printf '```console\n'
    printf '$ gemini -y -p "%s"\n\n' "$prompt"
    printf '%s\n' "$output"
    printf '```\n'
    printf '<!-- /demo-output:%s -->' "$name"
}

# Inject/replace the output block in the diary, right after the GIF embed line.
inject_into_diary() {
    local name="$1" prompt="$2" output="$3"
    local gif_pattern="${BASE_URL}/${name}.gif"
    local start_marker="<!-- demo-output:${name} -->"
    local end_marker="<!-- /demo-output:${name} -->"
    local block
    block="$(build_block "$name" "$prompt" "$output")"

    python3 - "$DIARY" "$gif_pattern" "$start_marker" "$end_marker" "$block" <<'PY'
import sys
from pathlib import Path

diary_path, gif_pattern, start, end, block = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
text = Path(diary_path).read_text()

# Remove any existing block for this demo
while start in text and end in text:
    s = text.index(start)
    e = text.index(end) + len(end)
    # also eat the trailing newline if present
    if e < len(text) and text[e] == '\n':
        e += 1
    text = text[:s] + text[e:]

# Find the GIF embed line and inject the block after it
lines = text.splitlines(keepends=True)
out = []
for i, line in enumerate(lines):
    out.append(line)
    if gif_pattern in line:
        # Insert blank line + block after the GIF line
        if out[-1].rstrip('\n'):  # gif line has content
            out.append('\n')
        out.append(block + '\n')
Path(diary_path).write_text(''.join(out))
print(f"  injected after: {line.strip()[:80]}")
PY
}

# Determine which tapes to process
if [[ $# -gt 0 ]]; then
    TAPES=()
    for name in "$@"; do
        tape="${TAPE_DIR}/${name%.tape}.tape"
        [[ -f "$tape" ]] || { echo "tape not found: $tape" >&2; exit 1; }
        TAPES+=("$tape")
    done
else
    mapfile -t TAPES < <(find "$TAPE_DIR" -name 'demo_*.tape' | sort)
fi

for tape in "${TAPES[@]}"; do
    name="$(basename "${tape%.tape}")"
    prompt="$(extract_prompt "$tape")"
    if [[ -z "$prompt" ]]; then
        echo "SKIP $name (no Type line found in tape)"
        continue
    fi

    echo ""
    echo "==> $name"
    echo "    prompt: ${prompt:0:80}..."
    echo "    running gemini..."

    output="$(run_prompt "$prompt")"
    inject_into_diary "$name" "$prompt" "$output"
    echo "    diary: updated"
done

echo ""
echo "Done. Commit and push -- the GitHub Action publishes to the wiki automatically:"
echo "  git add docs/dev-diary.md scripts/capture_demo_output.sh"
echo "  git commit -m 'docs(diary): embed captured gemini output in demo sections'"
echo "  git push origin main"
