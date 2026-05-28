#!/bin/bash
# Build a staging directory of wiki pages from docs/ + repo-root markdown files.
# Pages get a header notice and cross-doc markdown links rewritten to wiki-page names.
#
# Usage: scripts/sync-docs-to-wiki.sh <out-dir>

set -euo pipefail

OUT="${1:?missing output dir}"
REPO_BASE="https://github.com/ilmanzo/changelog-poc/blob/main"

mkdir -p "$OUT"

# Home.md is curated by hand; copy as-is.
cp wiki/Home.md "$OUT/Home.md"

# Source-to-wiki-page map. Wiki page names use Title-Case with dashes.
declare -A MAP=(
    [docs/user-guide.md]=User-Guide.md
    [docs/developer-guide.md]=Developer-Guide.md
    [docs/architecture.md]=Architecture.md
    [docs/schema.md]=Schema.md
    [docs/THREAT_MODEL.md]=Threat-Model.md
    [docs/dev-diary.md]=Development-Diary.md
    [CHANGELOG.md]=Changelog.md
    [docs/vhs/demo_changelog.md]=Demo-Changelog.md
    [docs/vhs/demo_untested.md]=Demo-Untested.md
    [docs/vhs/demo_cve_timeline.md]=Demo-CVE-Timeline.md
    [docs/vhs/demo_search.md]=Demo-Search.md
    [docs/vhs/demo_cross_distro.md]=Demo-Cross-Distro.md
    [docs/vhs/demo_systemd_bugs.md]=Demo-Systemd-Bugs.md
    [docs/vhs/demo_openssl_bugs.md]=Demo-Openssl-Bugs.md
)

for src in "${!MAP[@]}"; do
    dst="$OUT/${MAP[$src]}"
    {
        echo "> _This page is auto-generated from [\`${src}\`](${REPO_BASE}/${src}) on every push to \`main\`. Do not edit the wiki directly -- edit the source file._"
        echo
        cat "$src"
    } > "$dst"

    # Rewrite cross-doc markdown links so they target wiki page names instead of .md files.
    # Handles both bare relative links (foo.md) and docs/foo.md prefixed ones.
    sed -i \
        -e 's|](user-guide\.md)|](User-Guide)|g' \
        -e 's|](developer-guide\.md)|](Developer-Guide)|g' \
        -e 's|](architecture\.md)|](Architecture)|g' \
        -e 's|](schema\.md)|](Schema)|g' \
        -e 's|](THREAT_MODEL\.md)|](Threat-Model)|g' \
        -e 's|](dev-diary\.md)|](Development-Diary)|g' \
        -e 's|](docs/user-guide\.md)|](User-Guide)|g' \
        -e 's|](docs/developer-guide\.md)|](Developer-Guide)|g' \
        -e 's|](docs/architecture\.md)|](Architecture)|g' \
        -e 's|](docs/schema\.md)|](Schema)|g' \
        -e 's|](docs/THREAT_MODEL\.md)|](Threat-Model)|g' \
        -e 's|](docs/dev-diary\.md)|](Development-Diary)|g' \
        -e 's|](CHANGELOG\.md)|](Changelog)|g' \
        "$dst"
done

echo "Built ${#MAP[@]} wiki pages + Home.md in $OUT"
ls -la "$OUT"
