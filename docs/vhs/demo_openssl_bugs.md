# Demo: QA triage -- openssl

Same QA triage workflow applied to openssl: a security-critical library with a rich
history of CVEs and Bugzilla activity. Combines `find_bugs_in_tests`, `get_test_coverage`,
and `get_recent_releases` to give a complete triage picture.

**Prompt:** *"Show me Bugzilla bugs filed for openssl, the openQA tests that cover openssl, and the most recent changelog entries. Are there any open bugs about features that lack test coverage? Summarise the QA triage status."*

## Session output

<!-- demo-output:demo_openssl_bugs -->
<!-- /demo-output:demo_openssl_bugs -->

![QA triage -- openssl](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_openssl_bugs.gif)
