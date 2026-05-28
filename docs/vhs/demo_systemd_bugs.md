# Demo: QA triage -- systemd

Full QA triage workflow for systemd in a single prompt: Bugzilla bugs from the
TestCatalog analytics API, openQA test coverage, and recent changelog entries.
The MCP client correlates all three sources and identifies open bugs that may
lack test coverage.

**Prompt:** *"Show me Bugzilla bugs filed for systemd, the openQA tests that cover systemd, and the most recent changelog entries. Are there any open bugs about features that lack test coverage? Summarise the QA triage status."*

## Session output

<!-- demo-output:demo_bugs -->
```console
$ gemini -y -p "Show me Bugzilla bugs filed for systemd, the openQA tests that cover systemd, and
the most recent changelog entries. Are there any open bugs about features that lack test coverage?
Summarise the QA triage status."

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
*   **Dropped Support:** System V service scripts and ancient obsoletes (predating 2020) were
removed.
*   **Subpackage Splits:** `systemd-boot` (now on loongarch64) and `systemd-container` were split
into dedicated packages.
*   **Security:** Fixed `machined` unprivileged shell access (**CVE-2026-40224**).

### 4. Test Coverage Gaps & Triage Summary
**Are there open bugs about features that lack test coverage?**
**Yes.** There is a significant gap between the indexed bugs/recent features and the current openQA
coverage:
*   **Installation Regressions:** The open bug **bsc#1259761** (scriptlet failures) lacks a
dedicated package-update/scriptlet validation test in the openQA list.
*   **Feature Gaps:** Despite the recent v260 upgrade, there are no explicit test modules for:
    *   `systemd-boot` (recent split-off)
    *   `systemd-resolved` (recent fixes mentioned in changelog)
    *   `systemd-homed` and `systemd-userdb` (moved from experimental to main)
    *   `systemd-timedated` (subject of recently resolved bsc#1253281)

**QA Triage Summary:**
The triage status is **Active but Narrow**. While core components (Journal, Udev, Network) are
well-covered by openQA, the transition to **v260.1** and the recent regressions in **v257.x**
scriptlets reveal a need for more granular testing of sub-packages and installation triggers. Triage
efforts should focus on verifying the `bsc#1259761` fix and expanding the `testcatalog` mapping to
include `boot` and `resolved` modules.
```
<!-- /demo-output:demo_bugs -->

![QA triage -- systemd](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_systemd_bugs.gif)

<!-- demo-output:demo_systemd_bugs -->
```console
$ gemini -y -p "Show me Bugzilla bugs filed for systemd, the openQA tests that cover systemd, and the most recent changelog entries. Are there any open bugs about features that lack test coverage? Summarise the QA triage status."

For **systemd**, here is the current QA status based on Bugzilla, openQA coverage, and recent
activity:

### 1. Bugzilla Bugs (Active)
There is **one critical active bug** that requires immediate attention:
*   **[NEW] bsc#1259761**: Scriptlets failure when installing `systemd-257.10-160000.1.1`. This
indicates a regression in the package installation logic.

Several recent bugs have been resolved, including fixes for:
*   **bsc#1261978 (CVE-2026-40224)**: Unprivileged shell access to root namespace via `machined`.
*   **bsc#1263117**: Segfault in `cryptsetup` when using keyfiles with TPM devices.
*   **bsc#1259418 (CVE-2026-29111)**: Improper cgroup path validation in `core`.

### 2. openQA Test Coverage
The current suite includes **14 tests** exercising:
*   **Core Services**: `journalctl` (levels and basic), `verify_default_target`.
*   **Storage/Security**: `cryptsetup`, `udev_no_symlink`.
*   **Network**: `wireguard`, `wireguard_nm`, `wicked`.
*   **Installation**: `autoyast_smoke`, `autoyast/repos`.

### 3. Recent Changelog Highlights
*   **May 2026**: Added weak runtime dependency on `libtss2-tcti-device0` for `udev` and fixed
`vconsole-setup` font issues (**bsc#1212970**).
*   **March 2026**: Upgraded to **v260.1**, which officially **dropped support for SysV service
scripts**.
*   **March 2026**: Fixed `systemd-update-helper` to prevent incorrect skipping of `systemctl
disable` during package removal (**bsc#1245551**).

### 4. Gaps in Coverage & Triage Status

| Feature | Recent Activity / Bug | Test Coverage Status |
| :--- | :--- | :--- |
| **Package Scriptlets** | **bsc#1259761 (NEW)** | **GAP**: Basic `autoyast_smoke` failed to prevent
this install-time regression. |
| **Machined / Nspawn** | **CVE-2026-40224**, **CVE-2026-4105** | **GAP**: No direct tests for
`machined` or `nspawn` validation in the systemd suite. |
| **vconsole-setup** | bsc#1212970 (Recent fix) | **GAP**: Lacks dedicated functional tests for
console font setting. |
| **SysV Migration** | Support dropped in v260.1 | **GAP**: No explicit test verifying the absence
of SysV side-effects. |
| **Cryptsetup / TPM** | bsc#1263117 (Fixed) | **COVERED**: Validated by
`tests/console/cryptsetup.pm`. |

### Summary QA Triage
The **QA status is at risk** due to the active scriptlet failure (**bsc#1259761**), which suggests
that existing installation tests are not granular enough to catch macro/scriptlet regressions.

While core features like the **Journal** and **Cryptsetup** have solid coverage, there is a
**significant validation gap** around **container-related security fixes** (`machined`, `nspawn`)
and **system maintenance utilities** (`systemd-update-helper`). Triage efforts should prioritize
adding regression tests for the installation scriptlets and the recently patched security
vulnerabilities in `machined`.

Strategic Intent: Investigate systemd QA status: bugs, test coverage, and recent changes.
Summary: I gathered Bugzilla bugs, openQA test coverage, and recent changelog entries for 'systemd'.
I identified a critical NEW bug (bsc#1259761) regarding scriptlet failures and noted coverage gaps
in machined, nspawn, and vconsole-setup despite recent security fixes and architectural changes
(SysV removal). Final report provided a detailed triage status.
title: Investigating systemd QA Status
```
<!-- /demo-output:demo_systemd_bugs -->
