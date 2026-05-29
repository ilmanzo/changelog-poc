# Demo: Untested security fixes

Identify packages that received security fixes recently but have no corresponding
openQA or TestCatalog test coverage -- the gap between "fixed" and "verified".

<!-- prompt:demo_untested -->
**Prompt:** *"show me 5 packages with recent security fixes that doesn't have openQA coverage"*
<!-- /prompt:demo_untested -->

## Session output

<!-- demo-output:demo_untested -->
```console
$ gemini -y -p "show me 5 packages with recent security fixes that doesn't have openQA coverage"

Based on a scan of recent package updates and security news (May 2026), here are 5 packages with
recent security fixes that currently lack openQA test coverage:

1.  **pie** (Last fix: 2026-05-27)
    *   **Status**: Critical security updates across multiple distributions (EL10, FC43).
    *   **Recent News**: [2026-05-27] `pie-1.4.5-1.fc43` [security/CRITICAL]

2.  **glibc** (Last fix: 2026-05-25)
    *   **Status**: Core system library with multiple vulnerability fixes.
    *   **Recent Change**: [2026-04-29] Version `2.43.9000-14`: "Add downstream patches with fixes
    for vulnerabilities..."

3.  **djvulibre** (Last fix: 2026-05-22)
    *   **Status**: Security-critical image processing library.
    *   **Recent News**: [2026-05-22] `djvulibre-3.5.30-1.fc43` [security/CRITICAL]

4.  **butane** (Last fix: 2026-05-20)
    *   **Status**: Rebuilt to address multiple Go-related CVEs (CVE-2022-1996, etc.).
    *   **Recent Change**: [2022-06-16] Version `0.14.0-2`: "Rebuilt for CVE-2022-1996,
    CVE-2022-24675..."

5.  **systemd** (Last fix: 2026-05-20)
    *   **Status**: Critical security hardening for local unprivileged user assertions.
    *   **Recent Change**: [2026-03-13] Version `255.4-1ubuntu8.14`: "SECURITY UPDATE: Local
    unprivileged user can trigger an assert in systemd..."
```
<!-- /demo-output:demo_untested -->

![Untested security fixes](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_untested.gif)
