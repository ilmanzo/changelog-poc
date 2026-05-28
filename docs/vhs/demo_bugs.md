# Demo: QA triage -- systemd

Full QA triage workflow for systemd in a single prompt: Bugzilla bugs from the
TestCatalog analytics API, openQA test coverage, and recent changelog entries.
The MCP client correlates all three sources and identifies open bugs that may
lack test coverage.

**Prompt:** *"Show me Bugzilla bugs filed for systemd, the openQA tests that cover systemd, and the most recent changelog entries. Are there any open bugs about features that lack test coverage? Summarise the QA triage status."*

![QA triage -- systemd](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_bugs.gif)

## Session output

<!-- demo-output:demo_bugs -->
<!-- /demo-output:demo_bugs -->
