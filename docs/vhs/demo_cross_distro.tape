# Demo: cross-distro dependency blast-radius + changelog correlation
# Prerequisites: PostgreSQL running, openssl + its dependents ingested for opensuse/ubuntu/fedora
#   uv run scripts/ingest.py openssl curl wget libcurl4 --file /dev/null
#   uv run scripts/worker.py --all

Output docs/vhs/demo_cross_distro.gif
Set FontSize 15
Set Width 1024
Set Height 1024
Set Theme "Molokai"

Type `gemini -y -p "openssl was updated last week. Which packages in my system depend on it, and did their changelogs mention that update? Give me a cross-distro status comparison between OpenSUSE, Ubuntu and Fedora. Summarise all findings." 2>/dev/null`
Enter
Sleep 3m
