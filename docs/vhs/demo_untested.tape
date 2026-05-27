# Demo: finding packages with changes but no test coverage
# Prerequisites: PostgreSQL running, rpm-mcp database populated, test repo ingested

Output docs/vhs/demo_untested.gif
Set FontSize 14
Set Width 1200
Set Height 600
Set Theme "Monokai"

Type "gemini -p 'show me 5 packages with important changes in the last 3 months that dont have test covering those new features' --allowed-mcp-server-names=rpm-mcp"
Enter
Sleep 15s
