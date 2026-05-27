# Demo: semantic search across package changelogs
# Prerequisites: PostgreSQL running, rpm-mcp database populated

Output docs/vhs/demo_search.gif
Set FontSize 15
Set Width 1024
Set Height 1024
Set Theme "Molokai"

Type `gemini -y -p "find network related packages whose changelog entries mention new command line flags in the last 2 months" 2>/dev/null`
Enter
Sleep 2m
