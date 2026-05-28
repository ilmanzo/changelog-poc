# Demo: Untested security fixes

Identify packages that received security fixes recently but have no corresponding
openQA or TestCatalog test coverage -- the gap between "fixed" and "verified".

**Prompt:** *"show me 5 packages with recent security fixes that don't have openQA coverage"*

## Session output

<!-- demo-output:demo_untested -->
```console
$ gemini -y -p "show me 5 packages with recent security fixes that doesn't have openQA coverage"

Based on the recent news and changelog data from the database, here are 5 packages with recent
security fixes that currently lack openQA test coverage:

1.  **pie** (Last fix: 2026-05-27)
    *   **Details**: Several security updates marked as **CRITICAL** for Fedora and EL10 (e.g.,
    `pie-1.4.5-1.fc43`).
    *   **Status**: No recorded openQA tests or TestCatalog entries.

2.  **glibc** (Last fix: 2026-05-25)
    *   **Details**: Recent changelog entries include downstream patches specifically for
    **vulnerability fixes** and resolving issues like `ungetwc` operating on byte streams.
    *   **Status**: No openQA coverage found despite being a core system library.

3.  **djvulibre** (Last fix: 2026-05-22)
    *   **Details**: Security updates (`djvulibre-3.5.30-1`) released for Fedora 42, 43, and 44,
    flagged as **security/CRITICAL**.
    *   **Status**: No associated openQA tests.

4.  **butane** (Last fix: 2026-05-20)
    *   **Details**: Recently rebuilt to address multiple vulnerabilities including
    **CVE-2022-1996**, **CVE-2022-24675**, **CVE-2022-28327**, and **CVE-2022-27191**.
    *   **Status**: No test coverage recorded in the system.

5.  **systemd** (Last fix: 2026-05-20)
    *   **Details**: Recent **SECURITY UPDATE** addressing a local unprivileged user's ability to
    trigger an assertion failure (DoS).
    *   **Status**: While a critical component, no specific openQA test mapping is currently indexed
    for this package in the database.

These packages represent significant coverage gaps where recent security-relevant changes have been
made without corresponding automated verification in the linked openQA or TestCatalog suites.
```
<!-- /demo-output:demo_untested -->

![Untested security fixes](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_untested.gif)
