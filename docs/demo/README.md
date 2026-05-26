# Demo recordings

Scripted CLI demo of rpm-mcp using [`vhs`](https://github.com/charmbracelet/vhs).
Produces `cli.gif` and `cli.mp4` you can embed in the blog post or README.

## Install vhs

```bash
# openSUSE Tumbleweed
sudo zypper install vhs

# or via Go
go install github.com/charmbracelet/vhs@latest
```

`vhs` also needs `ttyd` and `ffmpeg` on PATH.

## Record

From the repo root:

```bash
# one-time data seed
infra/infra.sh start
scripts/ingest_core.sh 20
uv run scripts/worker.py --news

# pre-warm caches so on-camera latency is realistic
bash docs/demo/prewarm.sh   # see header of cli.tape for the loop

# render
vhs docs/demo/cli.tape
```

Outputs land at `docs/demo/cli.gif` and `docs/demo/cli.mp4`. Re-render any
time the CLI output drifts -- the `.tape` script is deterministic.

## Editing the script

Each scene in `cli.tape` is:

1. `Type "# comment explaining the query"` + `Enter`
2. `Sleep 1500ms` so the viewer can read the comment
3. `Type "mcp <subcommand> ..."` + `Enter`
4. `Sleep 5-6s` so the viewer can read the output
5. `Type "clear"` + `Enter` before the next scene

Swap queries by editing the `Type` lines. `Set TypingSpeed`, `Set Width`,
and `Set Theme` at the top tune the look.

## MCP-client track (separate, screen-recorded)

The CLI demo shows the engine. The headline demo is an MCP client (Claude
Code, Zed, Cursor) issuing natural-language questions that the model
resolves into tool calls. That track needs a real screen recorder
(`obs-studio`, `wf-recorder` for Wayland) -- vhs only captures terminals.

Suggested script for the client demo:

1. "What CVEs have been fixed in vim recently?" -> calls `list_cves`
2. "Show me the diff between vim 9.0.2127 and 9.1.0" -> `analyze_package_diff`
3. "Search for any package that fixed a heap overflow in a regex engine"
   -> `semantic_search`
4. "Which packages depend on openssl and changed in the last month?"
   -> `get_dependency_changes` + `get_changes_in_range`
5. "Any urgent openSUSE news today?" -> `get_news`
