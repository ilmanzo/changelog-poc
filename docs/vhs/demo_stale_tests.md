# Demo: Stale test cleanup -- dropped features

When packages drop or remove features, the openQA tests that cover those features become
dead weight. This demo finds packages with recently removed functionality, cross-references
their test coverage, and produces a prioritised list of test cases that are candidates for removal.

<!-- prompt:demo_stale_tests -->
**Prompt:** *"For openSUSE Leap 16 and Tumbleweed, find packages whose recent changelogs mention dropped or removed features. For each one, show the test coverage -- are any tests now covering functionality that no longer exists? Give me a prioritised cleanup list."*
<!-- /prompt:demo_stale_tests -->

## Session output

<!-- demo-output:demo_stale_tests -->
```console
$ gemini -y -p "For openSUSE Leap 16 and Tumbleweed, find packages whose recent changelogs mention dropped or removed features. For each one, show the test coverage -- are any tests now covering functionality that no longer exists? Give me a prioritised cleanup list."

Based on recent changelog analysis for **openSUSE Tumbleweed** and **Leap 16** (including ALP-based
SLE 16 shared components), I have identified several packages that have recently dropped or removed
features. Below is a prioritized cleanup list focusing on tests that may now be covering
non-existent functionality.

### 1. systemd / libsystemd0 (CRITICAL)
*   **Dropped Features:**
    *   **`libidn`** support has been completely removed and replaced by `libidn2`.
    *   **`libiptc`** (legacy `iptables` API) support in `systemd-networkd` and `systemd-nspawn` has
    been dropped; only `nftables` is now supported for NAT rules.
    *   **Meson options** `-Dlibidn` and `-Dlibiptc` are now ignored/deprecated.
*   **Potentially Stale Tests:**
    *   `tests/network/wireguard_nm.pm`
    *   `tests/network/wireguard.pm`
    *   `tests/autoyast/wicked.pm`
*   **Cleanup Goal:** Audit these network tests to ensure they are not relying on `iptables` for NAT
configuration or checking for `libidn` link-time dependencies. Migrate logic to `nftables`.

### 2. GDM / GNOME Session (HIGH)
*   **Dropped Features:**
    *   **X11 Session Support:** GDM has entirely removed X11 support except for a legacy launch
    path; `gnome-session` has similarly dropped X11 session support.
    *   **`xdm-integration`** and `update-alternatives` for display managers are being removed in
    SLE 16.1 / Leap 16.
    *   **`udev-dir`** meson option entirely removed.
*   **Potentially Stale Tests:**
    *   `tests/x11/gdm_session_switch.pm`
    *   `tests/x11/gnomecase/gnome_classic_switch.pm`
    *   `tests/x11/keyboard_layout_gdm.pm`
*   **Cleanup Goal:** Verify if "Gnome Classic" or other X11-specific session tests are still
viable. Tests attempting to use `update-alternatives` for display manager selection on Leap 16 will
fail.

### 3. Shadow / Account Management (MEDIUM)
*   **Dropped Features:**
    *   **`chpasswd(8)`** and **`chgpasswd(8)`** are deprecated and slated for removal.
    *   **`--with-sha-crypt`** build flag removed.
    *   Support for several "deprecated for decades" hashing algorithms is being phased out.
*   **Potentially Stale Tests:**
    *   `tests/console/verify_shadow.pm`
    *   `tests/console/sshd.pm`
*   **Cleanup Goal:** Update scripts relying on `chpasswd` to use `usermod` or `passwd --stdin`
(where supported). Ensure account creation tests don't specify removed SHA-crypt variants.

### 4. libxslt1 (MEDIUM)
*   **Dropped Features:**
    *   **EXSLT crypto extensions** and support for dynamically loaded plugins are now disabled by
    default and marked for removal.
    *   **`nanoftp.h`** and **`nanohttp.h`** removed.
*   **Potentially Stale Tests:**
    *   Upstream-integrated tests in `lib/services/nginx.pm` or other XML processing layers.
*   **Cleanup Goal:** If any tests exercise specific EXSLT crypto functions or rely on `libxslt` to
fetch remote resources via FTP/HTTP, they must be refactored or removed.

### 5. Permissions / chkstat (LOW)
*   **Dropped Features:**
    *   Legacy **`CHECK_PERMISSIONS`** logic and deprecated capability checks in `chkstat` have been
    removed.
*   **Potentially Stale Tests:**
    *   Security auditing tests (none directly mapped in openQA DB, but often part of `cc_audit`
    suites).
*   **Cleanup Goal:** Ensure security hardening tests are not checking for old `chkstat` behavior or
variables that are now ignored.

### Summary of Coverage Risks
| Package | Removed Feature | openQA Test Risk | Priority |
| :--- | :--- | :--- | :--- |
| **systemd** | `iptables` / `libidn` | Network & NAT tests | **Critical** |
| **gdm** | X11 Support / `xdm` | X11 Session switching | **High** |
| **shadow** | `chpasswd` / old SHA | User management tests | **Medium** |
| **libxslt1** | EXSLT Crypto / FTP | XML/Web service tests | **Medium** |
| **ca-certs** | `SwissSign` CA | SSL/Certificate validation | **Low** |
```
<!-- /demo-output:demo_stale_tests -->

![Stale test cleanup](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_stale_tests.gif)
