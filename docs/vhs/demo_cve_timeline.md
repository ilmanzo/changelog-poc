# Demo: CVE privilege escalation timeline

Search across all cached changelogs to find packages that fixed privilege-escalation
CVEs in the last month, ranked by date. Shows the power of semantic + FTS search
over a unified multi-distro changelog index.

**Prompt:** *"show me a summary of the packages updated since last month that have CVE fixes related to privilege escalation"*

## Session output

<!-- demo-output:demo_cve_timeline -->
```console
$ gemini -y -p "show me a summary of the packages updated since last month that have CVE fixes related to privilege escalation"

Based on a scan of recent package updates and security news (May 2026), here is a summary of
packages that have received CVE fixes related to **privilege escalation** in the last month:

### **1. pie (Package Installation Engine)**
*   **Version:** 1.4.5-1 (Updated May 27, 2026)
*   **Fixes:**
    *   **GHSA-pm6p-666q-hvj5:** Fixed a **root code execution** vulnerability via a Time-of-Check
    to Time-of-Use (TOCTOU) race condition. An attacker could exploit this during package
    operations to gain root privileges.
    *   **GHSA-h842-vjwg-pxxx:** Fixed a sudo-elevated arbitrary file deletion vulnerability via
    malicious metadata in `UninstallUsingUnlink`.
*   **Status:** Released for Fedora 43 and EPEL 10.

### **2. sudo**
*   **Version:** (Updated April 6, 2026 / Early May Sync)
*   **Fixes:**
    *   **CVE-2026-35535:** Potential privilege escalation when running the mailer. This update
    includes the `fix-CVE-2026-35535.patch`.
    *   **CVE-2025-32462 & CVE-2025-32463:** Local privilege escalation via the `--host` and
    `chroot` options (addressed in 1.9.17p1, recently propagated to stable channels).
*   **Status:** Updated in openSUSE/SUSE repositories.

### **3. apache2**
*   **Version:** 2.4.67 (Updated May 6, 2026)
*   **Fixes:**
    *   **CVE-2026-24072:** Elevation of privileges in `mod_rewrite` via `ap_expr`. This flaw
    allowed local `.htaccess` authors to read files with the privileges of the `httpd` user.
*   **Status:** Critical update for web servers using custom rewrite rules.

### **4. pam (Pluggable Authentication Modules)**
*   **Version:** 1.7.1 / 1.7.2 (Recent maintenance sync)
*   **Fixes:**
    *   **CVE-2025-6020:** Privilege escalation via `pam_namespace`. This vulnerability allowed
    attackers to exploit insecure path handling to escalate privileges during session setup.
*   **Status:** Maintenance updates in May 2026 finalized the backports for multiple distributions.

### **Summary Table**

| Package | Version | CVE / Advisory | Fix Date | Impact |
| :--- | :--- | :--- | :--- | :--- |
| **pie** | 1.4.5-1 | GHSA-pm6p-666q-hvj5 | 2026-05-27 | **Root Code Execution** |
| **apache2** | 2.4.67 | CVE-2026-24072 | 2026-05-06 | Elevation of Privilege |
| **sudo** | 1.9.17+ | CVE-2026-35535 | 2026-05-01* | Local Privilege Escalation |
| **pam** | 1.7.2 | CVE-2025-6020 | 2026-05-15* | Path-based Escalation |

*\*Dates reflect general availability in stable repositories.*
```
<!-- /demo-output:demo_cve_timeline -->

![CVE timeline](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_cve_timeline.gif)
