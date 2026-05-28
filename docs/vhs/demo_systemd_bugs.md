# Demo: QA triage -- systemd

Full QA triage workflow for systemd in a single prompt: Bugzilla bugs from the
TestCatalog analytics API, openQA test coverage, and recent changelog entries.
The MCP client correlates all three sources and identifies open bugs that may
lack test coverage.

**Prompt:** *"Show me Bugzilla bugs filed for systemd, the openQA tests that cover systemd, and the most recent changelog entries. Are there any open bugs about features that lack test coverage? Summarise the QA triage status."*

![QA triage -- systemd](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_systemd_bugs.gif)

## Session output

<!-- demo-output:demo_bugs -->
```console
$ gemini -y -p "Show me Bugzilla bugs filed for systemd, the openQA tests that cover systemd, and the most recent changelog entries. Are there any open bugs about features that lack test coverage? Summarise the QA triage status."

Here is the QA triage and test coverage summary for **systemd**:

### 1. Bugzilla Status (SUSE TestCatalog Analytics)
There are **10** primary bugs indexed for `systemd` in the triage backlog.
*   **Open Bugs (NEW):**
    *   **bsc#1259761**: Scriptlets failure when installing `systemd-257.10` (Normal severity).
*   **Recently Addressed (RESOLVED/Changelog):**
    *   **bsc#1260357 / bsc#1264224**: Added weak dependency on `libtss2-tcti-device0` for udev.
    *   **bsc#1212970**: Fixed `vconsole-setup` font issues when console driver lacks support.
    *   **bsc#1263117**: Fixed `cryptsetup` segfault with TPM devices and keyfiles.
    *   **bsc#1253281**: `timedated` access to `adjtime` (Resolved).
    *   **bsc#1220495**: `systemd-user-runtime-dir` error triggers (Resolved).

### 2. openQA Test Coverage
There are **14** test modules currently mapping to `systemd` coverage:
*   **Core Systems:** `journalctl.pm`, `journalctlLevels.pm`, `verify_default_target.pm`.
*   **Hardware/Kernel:** `udev_no_symlink.pm`, `cryptsetup.pm`, `kernel_kexec.pm`.
*   **Network:** `wireguard.pm`, `wireguard_nm.pm`.
*   **Installation/YaST:** `autoyast_smoke.pm`, `repos.pm`, `wicked.pm`.
*   **UI/Misc:** `window_system.pm`, `screensharing_available.pm`.

### 3. Recent Changelog Highlights (v260.1 Upgrade)
The package was recently upgraded to **v260.1**, introducing several structural changes:
*   **Dropped Support:** System V service scripts and ancient obsoletes (predating 2020) were removed.
*   **Subpackage Splits:** `systemd-boot` (now on loongarch64) and `systemd-container` were split into dedicated packages.
*   **Security:** Fixed `machined` unprivileged shell access (**CVE-2026-40224**).

### 4. Test Coverage Gaps & Triage Summary
**Are there open bugs about features that lack test coverage?**
**Yes.** There is a significant gap between the indexed bugs/recent features and the current openQA coverage:
*   **Installation Regressions:** The open bug **bsc#1259761** (scriptlet failures) lacks a dedicated package-update/scriptlet validation test in the openQA list.
*   **Feature Gaps:** Despite the recent v260 upgrade, there are no explicit test modules for:
    *   `systemd-boot` (recent split-off)
    *   `systemd-resolved` (recent fixes mentioned in changelog)
    *   `systemd-homed` and `systemd-userdb` (moved from experimental to main)
    *   `systemd-timedated` (subject of recently resolved bsc#1253281)

**QA Triage Summary:**
The triage status is **Active but Narrow**. While core components (Journal, Udev, Network) are well-covered by openQA, the transition to **v260.1** and the recent regressions in **v257.x** scriptlets reveal a need for more granular testing of sub-packages and installation triggers. Triage efforts should focus on verifying the `bsc#1259761` fix and expanding the `testcatalog` mapping to include `boot` and `resolved` modules.
```
<!-- /demo-output:demo_bugs -->
