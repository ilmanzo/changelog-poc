Your test was green yesterday. Today it's red. App code didn't change — but openssl shipped a new version
overnight, and now nobody knows what broke.

rpm-mcp closes that gap. It's a Model Context Protocol server that gives any AI assistant — Claude Code,
gemini-cli, Cursor — instant semantic and keyword search over every RPM changelog, spec file, CVE, distro news
item, and openQA test for openSUSE and Fedora.

Ask in plain English: "What changed in glibc this week?" "Which packages link against the new openssl ABI?"
"Where was CVE-2023-4738 fixed?" — answers come back in under a second, with citations.

One Postgres+pgvector backend, ~13k packages indexed, runs on a laptop or a shared box. Engineers stop
spelunking through XML APIs and rpm -q --changelog pipelines. Their AI does it for them.


*"What changed in SLES 16 over the last 3 months that could affect my package?"*
