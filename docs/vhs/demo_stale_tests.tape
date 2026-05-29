# Demo: stale test cleanup -- dropped features
# Prerequisites: PostgreSQL running, rpm-mcp database populated
#   ./rpm-mcp register gemini
#   uv run scripts/ingest.py systemd curl openssl vim

Output docs/vhs/demo_stale_tests.gif
Set FontSize 15
Set Width 1024
Set Height 1024
Set Theme "Molokai"

Type `gemini -y -p "For openSUSE Leap 16 and Tumbleweed, find packages whose recent changelogs mention dropped or removed features. For each one, show the test coverage -- are any tests now covering functionality that no longer exists? Give me a prioritised cleanup list." 2>/dev/null`
Enter
Sleep 3m
