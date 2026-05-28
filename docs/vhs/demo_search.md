# Demo: Semantic search

Find packages by topic across all cached changelog entries using vector similarity
search (fastembed ONNX + pgvector HNSW). The query is embedded and compared against
all 384-dim entry vectors -- no keyword matching required.

**Prompt:** *"find network related packages whose changelog entries mention new command line flags in the last 2 months"*

## Session output

<!-- demo-output:demo_search -->
```console
$ gemini -y -p "find network related packages whose changelog entries mention new command line flags in the last 2 months"

Based on a search of changelog entries from the last 2 months (since March 28, 2026), several
network-related packages have introduced new command-line flags or options:

### 1. **OpenSSH** (Updated to 10.3p1 on April 22, 2026)
OpenSSH introduced several new CLI features and flags:
*   **`ssh-add -Q`**: A new flag to support querying protocol extensions via the agent "query"
extension.
*   **`ssh -Oconninfo`**: A new multiplexing command that displays detailed connection information
(similar to the `~I` escape sequence).
*   **`ssh -O channels`**: A new multiplexing command to list all currently open channels in a
running multiplexed session.
*   **`sshd_config`**: Added a `GSSAPIDelegateCredentials` option (mirroring the client-side
option).

### 2. **ethtool** (Updated to 7.0 on April 27, 2026)
The 7.0 release and the preceding 6.19 update added:
*   **`--show-mse`**: A new flag to support Maximum Squared Error (MSE) display.
*   **`tsinfo`**: Added support for PTP hardware source.
*   **`monitor`**: Added notification handling for PLCA (Physical Layer Collision Avoidance)
configuration.
*   **`rxfh`**: Added support for IPv6 Flow Label hashing.

### 3. **iproute2** (Updated to 7.0 on April 15, 2026)
The major 7.0 update introduced support for new networking technologies which include new
command-line parameters:
*   **CAN XL Support**: Initial support for CAN XL in `iplink_can`.
*   **DPLL Mode Setting**: Added support for mode setting in the `dpll` utility.
*   **Fractional Frequency Offset**: Added support for fractional frequency offset in `ppt` (likely
for `dpll`).

### 4. **Git** (Updated to 2.54.0 on April 19, 2026)
While a version control system, Git introduced several networking-related CLI improvements:
*   **`git send-email`**: Now supports passing hostname/port to the `Authen::SASL` module and
supports client-side certificates.
*   **`git rebase --trailer`**: A new option to drive the interpret-trailers machinery.
*   **`git config list --type=<X>`**: A new option to show values interpreted for a specific type.
*   **`git rev-list --maximal-only`**: A new flag to show only commits not reachable by other
commits.

### 5. **Nginx** (Updated to 1.31.0 on May 13, 2026)
*   **Forward Proxy Support**: Introduced support for HTTP forward proxying.
*   **`least_time`**: Added a new load-balancing method for both HTTP and Stream modules.

### 6. **curl** (Updated to 8.20.0 on April 28, 2026)
*   **SMB Opt-in**: SMB support is now opt-in, which may affect how the protocol is invoked via CLI
in certain builds.
*   **`--capath`**: Fixed to work in builds where proxying is disabled.
```
<!-- /demo-output:demo_search -->

![Semantic search](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_search.gif)
