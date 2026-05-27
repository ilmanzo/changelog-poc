# Threat Model

## Deployment topology

```
User workstation                     Shared host
+-----------------+                  +------------------+
| MCP client      |  stdio (local)   | PostgreSQL       |
| (Claude/gemini) | <--------------> | + pgvector       |
| + mcp_server.py |                  | (rpm_mcp DB)     |
+-----------------+                  +------------------+
       |
       | HTTP (outbound only)
       v
  OBS API, Gitea, Pagure, GitHub/GitLab APIs,
  Ubuntu changelogs, openQA, Bodhi
```

Each user runs `mcp_server.py` locally via stdio. No network listener, no
auth layer, no TLS termination. The server connects to a shared PostgreSQL
instance (credential in `DATABASE_URL` env var).

## Data sources and trust boundaries

| Source | Transport | Trust level | Notes |
|---|---|---|---|
| OBS API (`api.opensuse.org`) | HTTPS | High | Official openSUSE build service |
| Gitea (`src.opensuse.org`) | HTTPS | High | Official openSUSE source mirror |
| Pagure (`src.fedoraproject.org`) | HTTPS | High | Official Fedora source host |
| GitHub Releases API | HTTPS | Medium | URL resolved from spec; repo content is user-contributed |
| GitLab Releases API | HTTPS | Medium | Same as GitHub |
| Ubuntu Changelogs | HTTPS | High | Official Ubuntu archive |
| Bodhi (`bodhi.fedoraproject.org`) | HTTPS | High | Official Fedora update system |
| openQA (`openqa.opensuse.org`) | HTTPS | High | Official openSUSE QA system |
| Local RPM database | `rpm -q` | High | Local system packages |
| Local git clone | `git log` | Medium | Shallow clone of upstream or test repo |
| PostgreSQL | TCP (DSN) | Trusted | Shared backing store |

## Mitigations in place

### Input validation

- **Package names**: `validate_package_name()` rejects traversal (`../`),
  shell metacharacters, and empty strings via `^[a-zA-Z0-9_\-\.+]+$` regex.
  Every ingest path calls this before any I/O.

- **CVE/bug IDs**: `_validate_cve_id()` and `_validate_bug_id()` enforce
  strict format (`CVE-YYYY-NNNN+`, `bsc#NNNNNN`) before DB queries.

- **SQL injection**: All queries use parameterised placeholders (`$1`, `$2`).
  Dynamic WHERE clauses in `_fetch_text_search` are validated against a
  whitelist regex (`_SAFE_WHERE_CLAUSE`) that only permits
  `column OP $N` form. No string interpolation of user values into SQL.

- **XML parsing**: OBS `_service` files parsed with `defusedxml` (no entity
  expansion, no external entities, no DTD processing).

- **Subprocess execution**: `run_subprocess()` uses
  `asyncio.create_subprocess_exec` (argv list, no shell). Package names
  cannot inject arguments because they are validated first.

### Network security

- **No inbound listener**: stdio transport only. No HTTP server, no SSE, no
  WebSocket. Attack surface is limited to the MCP client process.

- **Outbound HTTPS only**: All source fetches use HTTPS. No plain HTTP
  connections to external services.

- **Token handling**: `GITHUB_TOKEN` / `GITLAB_TOKEN` are read from env vars
  (not config files), passed as HTTP headers, never logged or stored in DB.

### Data integrity

- **Content-addressed dedup**: `uuid5(NAMESPACE, package||content)` ensures
  the same changelog entry from different sources converges to one row.
  Prevents duplication but also means a corrupted source that produces
  identical content won't create duplicate alerts.

- **Idempotent migrations**: Applied on every startup via `Database.connect()`.
  Each migration is guarded by `IF NOT EXISTS` / `CREATE INDEX CONCURRENTLY`
  patterns.

## Accepted risks

### R1: Malicious upstream maintainer (MEDIUM)

A maintainer who controls a package's changelog, spec file, or GitHub
releases can inject arbitrary text into the changelog database. This text is
returned verbatim to the MCP client's LLM, which may interpret it as
instructions (indirect prompt injection).

**Impact**: Degraded LLM answer quality for that specific package. The LLM
might produce misleading summaries or recommendations based on poisoned
changelog content.

**Why accepted**: Changelog content is public, auditable data. The server is
a data layer, not a decision layer -- the LLM client is responsible for
critical reasoning. Filtering or sanitising changelog text would reduce
utility.

### R2: Shared PostgreSQL without row-level security (LOW)

All users read/write the same tables. A compromised `mcp_server.py` process
could read or modify any package's data.

**Why accepted**: The data is public (distro changelogs, specs, news). There
is no user-private data in the schema. Write operations are idempotent
upserts keyed on content hashes -- corruption self-heals on next ingest.

### R3: DATABASE_URL credential in environment (LOW)

The PostgreSQL DSN (including password) lives in `DATABASE_URL`. A local
process with access to the user's environment can read it.

**Why accepted**: Standard practice for 12-factor apps. The database holds
only public distro metadata. Rotating the credential is straightforward.

### R4: GitHub/GitLab anonymous rate limiting (LOW)

Without `GITHUB_TOKEN`, the GitHub API allows 60 requests/hour. A bulk
ingest of many packages with upstream GitHub URLs could hit this limit,
causing enrichment to silently skip packages.

**Why accepted**: Enrichment is best-effort. Core changelog data comes from
OBS/Gitea (no rate limit). Setting `GITHUB_TOKEN` lifts the limit to
5000 req/hr.

### R5: Upstream URL resolution from spec files (LOW)

`_resolve_upstream_url` fetches raw `.spec` and `_service` files from OBS
and extracts URLs via regex/XML parsing. A spec file could contain a
URL pointing to a non-forge host that happens to match the forge host
list.

**Why accepted**: The forge host allowlist (`_FORGE_HOSTS`) is small and
curated. URLs are only used to fetch release notes via GitHub/GitLab
REST APIs -- no arbitrary HTTP fetches. A false positive would at worst
produce a 404 or unrelated release notes, caught by the `SourceNotFound`
handler.

### R6: Indirect prompt injection via changelog content (MEDIUM)

Changelog entries are returned as tool results to the MCP client. A
carefully crafted changelog entry could contain text designed to influence
the LLM's behavior (e.g., "ignore previous instructions and...").

**Why accepted**: This is inherent to any tool that returns untrusted text
to an LLM. The MCP server cannot solve this -- it is the MCP client's
responsibility to handle tool results safely. The server returns raw data
without interpretation.

## Out of scope

- **Authentication/authorisation**: No user identity. Stdio transport means
  the OS process model provides isolation.
- **TLS termination**: PostgreSQL connection uses whatever `DATABASE_URL`
  specifies (`sslmode=require` recommended for production).
- **DDoS / rate limiting**: No inbound network surface to attack.
- **Supply chain (Python dependencies)**: Managed by `uv.lock` pinning.
  Not unique to this project.
