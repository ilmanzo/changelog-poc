# Demo: semantic search across package changelogs
# Prerequisites: PostgreSQL running, rpm-mcp database populated

Output docs/vhs/demo_search.gif
Set FontSize 16
Set Width 800
Set Height 600
Set Theme "Molokai"

Type "gemini -y"
Enter
Sleep 10s
Type "find changelog entries mentioning CVE fixes for network packages in the last 6 months"
Enter
Sleep 120s
