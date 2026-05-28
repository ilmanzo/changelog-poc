#!/usr/bin/env bash
# Run each demo prompt through gemini-cli, save the clean output to a .txt file,
# then embed it into the corresponding docs/vhs/demo_NAME.md demo page between
# <!-- demo-output:NAME --> markers (idempotent -- safe to re-run).
#
# Usage:
#   scripts/capture_demo_output.sh               # all tapes
#   scripts/capture_demo_output.sh demo_bugs     # single tape (no extension)

set -euo pipefail

REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
TAPE_DIR="$REPO/docs/vhs"

strip_ansi() { sed 's/\x1b\[[0-9;]*[mKJH]//g; s/\r//g'; }

extract_prompt() {
    grep '^Type' "$1" \
        | sed "s/^Type \`gemini -y -p \"//; s/\" 2>\/dev\/null\`$//" \
        | head -1
}

run_prompt() {
    # Capture stdout; send stderr to /dev/null; exit 0 even if gemini fails
    # so set -e does not abort the whole script.
    gemini -y -p "$1" 2>/dev/null | strip_ansi || true
}

# Inject/replace the console block inside the demo page.
inject_into_page() {
    local page="$1" name="$2" prompt="$3" output="$4"
    local start="<!-- demo-output:${name} -->"
    local end="<!-- /demo-output:${name} -->"

    python3 - "$page" "$start" "$end" "$prompt" "$output" <<'PY'
import sys
from pathlib import Path

page_path, start, end, prompt, output = sys.argv[1:]
text = Path(page_path).read_text()

block = (
    f"{start}\n"
    f"```console\n"
    f'$ gemini -y -p "{prompt}"\n\n'
    f"{output}\n"
    f"```\n"
    f"{end}"
)

if start in text and end in text:
    s = text.index(start)
    e = text.index(end) + len(end)
    text = text[:s] + block + text[e:]
else:
    # Append at end as fallback
    text = text.rstrip('\n') + '\n\n' + block + '\n'

Path(page_path).write_text(text)
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

PAUSE_S="${CAPTURE_PAUSE:-15}"
first=1

for tape in "${TAPES[@]}"; do
    name="$(basename "${tape%.tape}")"
    page="${TAPE_DIR}/${name}.md"
    txt="${TAPE_DIR}/${name}.txt"
    prompt="$(extract_prompt "$tape")"

    if [[ -z "$prompt" ]]; then
        echo "SKIP $name (no Type line in tape)"
        continue
    fi
    if [[ ! -f "$page" ]]; then
        echo "SKIP $name (no demo page at docs/vhs/${name}.md)"
        continue
    fi
    if [[ -f "$txt" ]]; then
        echo "SKIP $name (already captured: docs/vhs/${name}.txt)"
        continue
    fi

    # Pause between requests to avoid quota exhaustion; skip before first run.
    if [[ $first -eq 1 ]]; then
        first=0
    else
        echo "    sleeping ${PAUSE_S}s before next request..."
        sleep "$PAUSE_S"
    fi

    echo ""
    echo "==> $name"
    echo "    prompt: ${prompt:0:80}..."
    echo "    running gemini..."

    output="$(run_prompt "$prompt")"

    if [[ -z "${output// }" ]]; then
        echo "    SKIP: gemini returned empty output (re-authentication may be needed)"
        continue
    fi

    # 1. Save raw .txt alongside tape and GIF
    printf '%s\n' "$output" > "$txt"
    echo "    saved: docs/vhs/${name}.txt"

    # 2. Embed into the demo page
    inject_into_page "$page" "$name" "$prompt" "$output"
    echo "    page:  docs/vhs/${name}.md updated"
done

echo ""
echo "Done. Commit and push -- the GitHub Action publishes to the wiki automatically:"
echo "  git add docs/vhs/ && git commit -m 'docs(demos): refresh captured gemini output'"
echo "  git push origin main"
