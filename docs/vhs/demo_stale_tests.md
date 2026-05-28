# Demo: Stale test cleanup -- dropped features

When packages drop or remove features, the openQA tests that cover those features become
dead weight. This demo finds packages with recently removed functionality, cross-references
their test coverage, and produces a prioritised list of tests that are candidates for removal.

**Prompt:** *"Find packages whose recent changelogs mention dropped or removed features. For each one, show the openQA test coverage -- are any tests now covering functionality that no longer exists? Give me a prioritised cleanup list."*

## Session output

<!-- demo-output:demo_stale_tests -->
```console
$ gemini -y -p "Find packages whose recent changelogs mention dropped or removed features. For each one, show the openQA test coverage -- are any tests now covering functionality that no longer exists? Give me a prioritised cleanup list."

Based on recent changelog activity (last 90–180 days) and openQA test coverage, here is a
prioritised cleanup list for packages with dropped or removed features that may have obsolete tests.

### **1. GDM / gnome-session (Priority: High)**
*   **Dropped Features:** **Entirely removed X11 support** (except for per-user X server for other
desktops). Features like **XDCMP** and **system-wide Xserver** are gone.
*   **Obsolete/Potentially Broken Tests:**
    *   `tests/x11/gdm_session_switch.pm`: Likely obsolete if it tests switching to system-wide X11
    sessions or XDCMP.
    *   `tests/x11/gnomecase/gnome_classic_switch.pm`: May be obsolete if it relies on the old X11
    session launch mechanism.
    *   `tests/x11/keyboard_layout_gdm.pm`: Potentially obsolete if it targets X11-specific layout
    handling in the greeter.
*   **Cleanup Action:** Audit these modules to remove XDCMP/system-Xserver test cases.

### **2. curl (Priority: High)**
*   **Dropped Features:** **RTMP support** entirely removed. **SMB support** made opt-in (previously
default). **OpenSSL-QUIC** support dropped.
*   **Obsolete/Potentially Broken Tests:**
    *   `tests/network/curl_client.pm`: Potentially broken if it uses `rtmp://` or `smb://` URLs
    (unless the latter is explicitly enabled).
    *   `tests/network/curl_server.pm`: May be obsolete if it provides RTMP/SMB backend services for
    the client tests.
*   **Cleanup Action:** Remove RTMP test cases; update SMB tests to ensure the feature is explicitly
enabled in the build under test.

### **3. python313-cryptography (Priority: Medium)**
*   **Dropped Features:** **Binary elliptic curves (SECT* classes)** removed. **OpenSSL 1.1.x
support** removed (OpenSSL 3.0+ now required).
*   **Obsolete/Potentially Broken Tests:**
    *   Internal package tests or `testcatalog` entries using SECT curves.
    *   Tests specifically verifying interoperability with OpenSSL 1.1.
*   **Cleanup Action:** Remove tests for SECT curves and OpenSSL 1.1-specific logic.

### **4. systemd (Priority: Medium)**
*   **Dropped Features:** `libidn` support (replaced by `libidn2`). `libiptc` support removed for
**NAT rules** in `systemd-networkd` and `systemd-nspawn` (only `nftables` supported now).
*   **Obsolete/Potentially Broken Tests:**
    *   `tests/network/wireguard.pm`: If it verifies NAT functionality via the `iptables` backend.
    *   Tests involving `systemd-nspawn` with NAT configurations.
*   **Cleanup Action:** Update network tests to use `nftables` or remove obsolete `iptables`
verification.

### **5. openssh (Priority: Low)**
*   **Dropped Features:** Bug compatibility for implementations that don't support **rekeying**.
Empty principals in certificates no longer act as wildcards.
*   **Obsolete/Potentially Broken Tests:**
    *   `tests/console/sshd.pm`: If it tests interoperability with legacy, non-rekeying SSH
    servers/clients.
*   **Cleanup Action:** Remove legacy bug-compatibility tests.

### **Summary Table**

| Package | Feature Dropped | Impacted Test Module | Priority |
| :--- | :--- | :--- | :--- |
| **GDM** | XDCMP / System-X11 | `gdm_session_switch.pm` | High |
| **curl** | RTMP / Default SMB | `curl_client.pm` | High |
| **systemd** | iptables NAT | `network/wireguard.pm` | Medium |
| **cryptography**| SECT Curves | (Internal/TestCatalog) | Medium |
| **openssh** | Rekeying Compat | `tests/console/sshd.pm` | Low |
```
<!-- /demo-output:demo_stale_tests -->

![Stale test cleanup](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_stale_tests.gif)
