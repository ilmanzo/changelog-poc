# Demo: semantic search across package changelogs
# Prerequisites: PostgreSQL running, rpm-mcp database populated

Output docs/vhs/demo_search.gif
Set FontSize 14
Set Width 1200
Set Height 600
Set Theme "Monokai"

Type "gemini -p 'find changelog entries mentioning CVE fixes for network packages in the last 6 months' --allowed-mcp-server-names=rpm-mcp"
Enter
Sleep 15s
