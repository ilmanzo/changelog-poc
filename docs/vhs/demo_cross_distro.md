# Demo: Cross-distro dependency blast radius

When a core library is updated, which packages depend on it -- and did their changelogs
acknowledge the change? Then compare the library version across distributions in one shot.
Demonstrates `get_reverse_dependencies`, `get_dependency_changes`, and `compare_versions`
working together without naming any tool in the prompt.

**Prompt:** *"openssl was updated last week. Which packages in my system depend on it, and did their changelogs mention that update? Give me a cross-distro status comparison between OpenSUSE, Ubuntu and Fedora. Summarise all findings."*

## Session output

<!-- demo-output:demo_cross_distro -->
```console
$ gemini -y -p "openssl was updated last week. Which packages in my system depend on it, and did their changelogs mention that update? Give me a cross-distro status comparison between OpenSUSE, Ubuntu and Fedora. Summarise all findings."

OpenSSL and its ecosystem have seen significant activity recently, particularly with the emergence
of **OpenSSL 4.0.0** and subsequent compatibility updates in major system packages.

### 1. OpenSSL Update Status (Cross-Distro)

The latest status across the requested distributions as of late May 2026:

| Distribution | Version | Latest Update | Key Changes |
| :--- | :--- | :--- | :--- |
| **OpenSUSE** | `openssl-3` (3.5.3) | **May 12, 2026** | **Performance**: Optimized MLDSA NTT for
POWER (p8+) architectures; security fixes for CVE-2026-28387 and others in March. |
| **Ubuntu** | `3.5.5-1ubuntu3` | **April 7, 2026** | **Security**: Fixed TLS 1.3 server key
agreement issue (unexpected key selection) and NULL pointer dereferences in OCSP. |
| **Fedora** | `1:3.5.6-1` | **April 19, 2026** | **Security**: Rebase to 3.5.6 resolving
CVE-2026-2673 and CVE-2026-28387. A revert of a premature OpenSSL 4 update was noted in mid-April
for compatibility. |

### 2. System Dependencies
A total of **111 packages** on your system depend directly on `libopenssl3`. The most critical ones
include:
*   **Core Services**: `systemd`, `openssh-server`, `dbus`, `krb5`.
*   **Networking/Tools**: `curl`, `wget`, `rsync`, `git-core`, `sudo`, `bind-utils`.
*   **Languages/Runtimes**: `python313-base`, `nodejs24`, `ruby4.0`, `perl-Net-SSLeay`.

### 3. Impact on Dependent Packages
Several major dependents mentioned the OpenSSL updates in their recent changelogs:

*   **`curl` (8.20.0, April 28 - May 4, 2026)**:
    *   Added fixes specifically for **OpenSSL 4.0.0-beta1** compatibility.
    *   Improved OpenSSL `channel_binding` lookups.
    *   Addressed multiple CVEs (CVE-2026-4873, etc.) related to connection reuse and TLS
    requirements.
*   **`python313-cryptography` (48.0.0, May 9, 2026)**:
    *   Added support for **Post-Quantum algorithms** (ML-KEM, ML-DSA) when used with **OpenSSL
    3.5.0 or later**.
    *   Updated wheels to be compiled with **OpenSSL 4.0.0**.
    *   Dropped support for OpenSSL < 3.0.0.
*   **`python313-certifi` (May 19, 2026)**:
    *   Added a new build requirement for `openssl` to handle the `/etc/ssl/ca-bundle.pem`
    generation.
*   **`systemd` (May 11-17, 2026)**:
    *   While not explicitly citing the OpenSSL version bump in the last week, it received fixes for
    `cryptsetup` TPM device handling and `udev` dependencies which are closely tied to the
    system's cryptographic stack.

### Summary
The update you noticed "last week" likely refers to the **OpenSUSE performance update (May 12)** or
the ripple effect of **OpenSSL 4.0.0 compatibility fixes** hitting stable repositories for `curl`
and `python-cryptography` during the first half of May. Your system is largely transitioned to the
3.5.x branch, with 4.0.0 support already being staged in core networking and development tools.

**Recommendation**: Ensure `libopenssl3` and `curl` are updated together, as recent `curl` releases
specifically address security regressions when reusing TLS connections with the newer OpenSSL
backends.
```
<!-- /demo-output:demo_cross_distro -->

![Cross-distro blast radius](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_cross_distro.gif)
