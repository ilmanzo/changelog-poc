# Demo: QA-engineer bug triage workflow
# Prerequisites: PostgreSQL running, openssl ingested + openQA tests scanned
#   ./rpm-mcp register gemini
#   uv run scripts/ingest.py openssl
#   uv run scripts/worker.py --test-repo

Output docs/vhs/demo_bugs.gif
Set FontSize 15
Set Width 1024
Set Height 1024
Set Theme "Molokai"

Type `gemini -y -p "Show me Bugzilla bugs filed for systemd, the openQA tests that cover systemd, and the most recent changelog entries. Are there any open bugs about features that lack test coverage? Summarise the QA triage status." 2>/dev/null`
Enter
Sleep 2m
