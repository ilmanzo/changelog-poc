# Demo: QA triage -- openssl

Same QA triage workflow applied to openssl: a security-critical library with a rich
history of CVEs and Bugzilla activity. Combines `find_bugs_in_tests`, `get_test_coverage`,
and `get_recent_releases` to give a complete triage picture.

**Prompt:** *"Show me Bugzilla bugs filed for openssl, the openQA tests that cover openssl, and the most recent changelog entries. Are there any open bugs about features that lack test coverage? Summarise the QA triage status."*

## Session output

<!-- demo-output:demo_openssl_bugs -->
```console
$ gemini -y -p "Show me Bugzilla bugs filed for openssl, the openQA tests that cover openssl, and the most recent changelog entries. Are there any open bugs about features that lack test coverage? Summarise the QA triage status."

The following is the QA triage status for the **openssl** package, based on recent changelogs,
Bugzilla reports, and openQA test coverage.

### 1. Most Recent Changelog Entries
The package is currently on the **3.5.x** branch, with frequent updates tracking upstream:
*   **3.5.3 (2025-09-16):** Current stable version.
*   **3.5.2 (2025-08-04):** Maintenance update.
*   **3.5.1 (2025-07-06):** Maintenance update.
*   **3.5.0 (2025-04-03):** Initial 3.5 release, which introduced new features and triggered several
QA regressions.

### 2. Bugzilla Status (TestCatalog Analytics)
A total of 10 relevant bugs were identified. Most critical issues found during the 3.5 upgrade cycle
have been resolved:
*   **bsc#1243431 (RESOLVED):** Critical issue where `sss_ssh_authorizedkeys` failed to retrieve
public keys with the latest OpenSSL.
*   **bsc#1242046 (RESOLVED):** FIPS regression where openQA reported an "invalid algorithm found"
in OpenSSL 3.5.
*   **bsc#1240378 (RESOLVED):** Security fix for the "Minerva" side-channel vulnerability in P-384.
*   **bsc#1242787 (RESOLVED):** Improvements for "Secure Execution" (s390x) environments.
*   **bsc#760296 (NEW):** A legacy feature request for TLSv1 `EMPTY_FRAGMENTS` (workaround for
BEAST) remains open but is largely obsolete for modern deployments.

### 3. openQA Test Coverage
There are **26 test modules** actively exercising `openssl`, showing a robust testing suite:
*   **FIPS Compliance:** Extensive coverage including `openssl_fips_alglist`, `openssl_fips_cipher`,
`openssl_fips_hash`, and public key checks (RSA/DSA).
*   **Modern Protocols:** Tests for **TLS 1.3**, **ALPN**, and **Post-Quantum Cryptography (PQC)**
via `openssl_pqc` and `oqs_provider_openssl`.
*   **Service Integration:** Verified against major consumers like **NGINX**, **OpenVPN**, **389
Directory Server**, **Valkey**, and **rsyslog**.

### 4. QA Triage Analysis & Gaps
*   **Regression Detection:** The system is highly effective. **bsc#1242046** proves that openQA is
actively catching breaking changes in FIPS providers before they reach stable users.
*   **Coverage Gaps:** 
    *   **QUIC:** While OpenSSL 3.x introduced native QUIC support, there is no dedicated QUIC test
    module listed in the current coverage.
    *   **Secure Execution:** Although the bug for Secure Execution improvements is resolved, there
    isn't a clearly named openQA module targeting this specific platform feature in the
    `openssl` scope.
*   **SSH/SSSD Integration:** The recent failure in `sss_ssh_authorizedkeys` suggests that while
service integration is broad, the specific edge cases in public key parsing for SSH authentication
were a blind spot that may require more focused integration tests.

### Summary QA Triage Status
**STATUS: HEALTHY.** The `openssl` package has one of the most comprehensive test suites in the
distribution. The triage process is proactive, with openQA successfully identifying regressions in
new major releases (3.5.0). Most reported bugs are related to specific security certifications or
deep-stack integration issues (SSSD/SSH) rather than core library instability.

**Recommendation:** Consider adding a dedicated test for **QUIC connectivity** and expanding sssd
integration tests to prevent a recurrence of the SSH public key retrieval issue.
```
<!-- /demo-output:demo_openssl_bugs -->

![QA triage -- openssl](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_openssl_bugs.gif)
