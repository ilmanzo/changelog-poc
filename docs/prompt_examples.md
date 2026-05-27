# Prompt Examples for rpm-mcp

This file contains example prompts that demonstrate the capabilities of the `rpm-mcp` server.

## Changelog & Version Queries

- "What are the changes in `vim` between version 9.0 and 9.1?"
- "Show me the last 5 releases of `kernel-default`."
- "Which packages do have new command line switches in the last 3 months?"
- "List all changes for `openssl` since 2024-01-01."

## Security & Bug Tracking

- "Are there any mentions of CVE-2024-1234 in `curl`?"
- "List all CVEs fixed in `glibc` in the last 6 months."
- "Find all bugzilla references for `systemd` (bsc#123456)."
- "Which packages mentioned 'privilege escalation' in their changelogs recently?"

## Discovery & Semantic Search

- "Find packages related to 'quantum resistant cryptography'."
- "What's new in the openSUSE ecosystem regarding 'system extensions'?"
- "Search for 'performance improvements' in core system utilities."

## Dependencies & Spec Files

- "What are the runtime dependencies of `zypper`?"
- "Which installed packages depend on `libopenssl3`?"
- "Show me the `%prep` section of the `bash` spec file."

## News & Quality Assurance

- "What's the latest news for `tumbleweed`?"
- "Which openQA tests cover the `openssh` package?"
- Test improvement: "Show me 5 packages that do have an openQA test, received new features in the last 3 months but those features aren't yet covered in OpenQA tests" 
