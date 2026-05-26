# rpm-mcp CLI demo -- regenerate with:  vhs docs/demo/cli.tape
#
# Prereqs (run once before recording):
#   infra/infra.sh start
#   scripts/ingest_core.sh 20        # seeds vim, curl, bash, openssl, kernel-default ...
#   uv run scripts/worker.py --news  # populate the news table
#
# Pre-warm each query so on-camera latency is realistic, not cold-cache:
#   for cmd in "find-cve CVE-2023-4738 vim" \
#              "semantic-search 'heap buffer overflow regex'" \
#              "analyze-package-diff vim 9.0.2127 9.1.0" \
#              "find-bug bsc#1213018 vim" \
#              "get-recent-releases bash --n 5" \
#              "get-dependency-changes curl --n 5 --depth 2" \
#              "get-news --limit 5" \
#              "get-sync-status --threshold-days 7"; do
#     eval "uv run mcp_server.py $cmd" > /dev/null 2>&1
#   done

Output docs/demo/cli.gif
Output docs/demo/cli.mp4

Set Shell "bash"
Set FontSize 16
Set Width 1400
Set Height 820
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set PlaybackSpeed 1.0

# --- Hidden setup: cd into the repo, clear screen, tighten prompt -----------
Hide
Type "cd ~/projects/changelog_mcp/rpm-mcp"
Enter
Type "export PS1='$ '"
Enter
Type "alias mcp='uv run mcp_server.py'"
Enter
Type "clear"
Enter
Sleep 500ms
Show

# --- 1. Practical CVE lookup -------------------------------------------------
Type "# 1. Look up a CVE in a real package changelog"
Enter
Sleep 1500ms
Type "mcp find-cve CVE-2023-4738 vim 2>/dev/null"
Enter
Sleep 5s

# --- 2. Semantic search finds the same vuln WITHOUT the CVE ID --------------
Type "clear"
Enter
Type "# 2. pgvector semantic search -- same hit, no literal CVE id"
Enter
Sleep 1500ms
Type "mcp semantic-search 'heap buffer overflow regex engine' --limit 3 2>/dev/null"
Enter
Sleep 6s

# --- 3. Version-range diff ---------------------------------------------------
Type "clear"
Enter
Type "# 3. Diff between two versions -- semver-aware filter"
Enter
Sleep 1500ms
Type "mcp analyze-package-diff vim 9.0.2127 9.1.0 2>/dev/null | head -40"
Enter
Sleep 6s

# --- 4. openSUSE bugzilla lookup --------------------------------------------
Type "clear"
Enter
Type "# 4. openSUSE bug reference (bsc#) lookup"
Enter
Sleep 1500ms
Type "mcp find-bug bsc#1213018 vim 2>/dev/null"
Enter
Sleep 5s

# --- 5. Cross-package dependency reach --------------------------------------
Type "clear"
Enter
Type "# 5. BFS over deps -- 'what changed in everything curl pulls in?'"
Enter
Sleep 1500ms
Type "mcp get-dependency-changes curl --n 3 --depth 2 2>/dev/null | head -25"
Enter
Sleep 6s

# --- 6. Recent releases grouped by version ----------------------------------
Type "clear"
Enter
Type "# 6. Recent releases, grouped by version"
Enter
Sleep 1500ms
Type "mcp get-recent-releases bash --n 5 2>/dev/null"
Enter
Sleep 5s

# --- 7. News (Bodhi + openSUSE RSS) -----------------------------------------
Type "clear"
Enter
Type "# 7. Daily news roll-up from Bodhi + openSUSE RSS"
Enter
Sleep 1500ms
Type "mcp get-news --limit 5 2>/dev/null"
Enter
Sleep 5s

# --- 8. Ops view: cache freshness -------------------------------------------
Type "clear"
Enter
Type "# 8. Ops view -- which packages need a re-sync?"
Enter
Sleep 1500ms
Type "mcp get-sync-status --threshold-days 7 2>/dev/null | head -20"
Enter
Sleep 6s

# --- Outro ------------------------------------------------------------------
Type "clear"
Enter
Type "# rpm-mcp: ask any MCP client (Claude Code, Zed, Cursor) the same"
Enter
Type "# questions in plain English -- the model calls these tools for you."
Enter
Sleep 4s
