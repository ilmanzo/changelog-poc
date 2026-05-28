-- 006_testcatalog_bugs.sql
-- Cache table for Bugzilla bugs fetched from the TestCatalog analytics API
-- (GET /api/v1/analytics/search?scope=bugs). Populated lazily by the
-- find_bugs_in_tests MCP tool with a 24h TTL (kind='testcatalog_bugs' in
-- manifest).

CREATE TABLE IF NOT EXISTS testcatalog_bugs (
    id           BIGSERIAL PRIMARY KEY,
    package_id   BIGINT NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    bug_id       BIGINT NOT NULL,
    summary      TEXT,
    status       TEXT,
    severity     TEXT,
    component    TEXT,
    assigned_to  TEXT,
    resolution   TEXT,
    UNIQUE (package_id, bug_id)
);

CREATE INDEX IF NOT EXISTS testcatalog_bugs_pkg_idx ON testcatalog_bugs (package_id);
