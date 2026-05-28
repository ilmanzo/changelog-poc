# Changelog

All notable changes to rpm-mcp are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [CalVer](https://calver.org/): `YYYY.MM.N`.

---

## [Unreleased]

### Added
- TestCatalog integration: `get_test_coverage(package, source=)` queries live SUSE TestCatalog API
  alongside the existing openQA local-scan data; TTL-gated with stale-data WARNING banner.
- Migration `004_testcatalog.sql`: adds `source` column to `openqa_tests`; updates UNIQUE constraint.
- Typed error hierarchy (`src/errors.py`): `RPMMcpError`, `ValidationError`, `DBError`;
  `SourceError`/`SourceNotFound` inherit from base; `_tool_wrapper` dispatches per type.
- Per-category tool timeouts: `TOOL_TIMEOUT_FAST_S` (default 10s) and `TOOL_TIMEOUT_SEARCH_S`
  (default 30s) env vars; sync/ingest tools have no cap.
- Nightly database backup: `scripts/backup.sh` + `packaging/systemd/rpm-mcp-backup.{service,timer}`.
- Versioned migration tracking: `schema_migrations` table records which `.sql` files have been applied.
- `duration_ms` and `category` fields added to every `tool_done` structured log record.
- TestCatalog `TESTCATALOG_URL` and `TESTCATALOG_API_KEY` config settings.

### Changed
- `get_openqa_tests` retired; replaced by unified `get_test_coverage(package, source=None)`.
- `find_untested_changes` now checks both openQA and TestCatalog sources.
- `upsert_openqa` and `get_openqa_tests` accept `source` parameter.

---

## [2026.05.0] -- 2026-05-27

Initial release after the three-day hackathon sprint.

### Added
- 18 MCP tools across 4 modules: changelog, deps, spec, news/test-coverage.
- PostgreSQL + pgvector backing store; content-addressed UUID dedup.
- 5 changelog sources: RpmSource, ObsSource, GiteaSource, FedoraSource, UbuntuSource.
- Upstream enrichment via GitHub/GitLab release notes.
- Cross-distro ingestion and version comparison.
- Full-text search (GIN/tsvector) and semantic search (HNSW/pgvector).
- openQA test-coverage tracking from local os-autoinst-distri-opensuse clone.
- TestCatalog integration (initial).
- Typed error hierarchy and per-tool timeout categories.
- 353 unit tests; mypy clean on 48 source files.
- `scripts/worker.py` for batch ingestion with `--testcatalog` flag.
- Systemd user units for worker and backup.
- Architecture diagrams in `docs/diagrams/`.
- Security: prompt-injection sanitization, output envelope (S7b), threat model.
