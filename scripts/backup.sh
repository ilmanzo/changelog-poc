#!/bin/bash
# rpm-mcp database backup -- pg_dump -Fc + 7-day retention.
# Run daily via the rpm-mcp-backup.timer systemd user unit.
#
# Env vars:
#   DATABASE_URL       PostgreSQL DSN (falls back to the default rpm_mcp DSN)
#   RPM_MCP_BACKUP_DIR Backup directory (default: ~/rpm-mcp-backup)

set -euo pipefail

# Why: dumps contain every changelog/spec/news row plus connection metadata
# in -Fc headers. Default umask (022) leaves them world-readable -- a
# co-tenant on the same host can `cat` the dump. 077 = owner-only.
umask 077

# Scrub password from the DSN before any log/error path can echo it.
# DSN itself stays in $DSN for pg_dump; $DSN_SAFE goes into messages.
_scrub_dsn() {
    # Match :PASSWORD@HOST and replace PASSWORD with ***
    sed -E 's#://([^:/]+):[^@]+@#://\1:***@#'
}

DSN="${DATABASE_URL:-postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp}"
DSN_SAFE="$(printf '%s' "$DSN" | _scrub_dsn)"
DIR="${RPM_MCP_BACKUP_DIR:-$HOME/rpm-mcp-backup}"

mkdir -p "$DIR"
# Tighten dir too in case it pre-existed with looser perms.
chmod 700 "$DIR"

STAMP="$(date +%Y%m%d-%H%M)"
OUTFILE="$DIR/rpm-mcp-${STAMP}.pgdump"

# On error, the trap echoes only the scrubbed DSN -- so systemd journal
# (and any redirected log) never sees the password.
trap 'echo "ERROR: pg_dump failed for $DSN_SAFE" >&2' ERR

pg_dump -Fc "$DSN" -f "$OUTFILE"
echo "Backup written: $OUTFILE ($(du -sh "$OUTFILE" | cut -f1))"

# Prune backups older than 7 days
find "$DIR" -name "rpm-mcp-*.pgdump" -mtime +7 -delete
REMAINING=$(find "$DIR" -name "rpm-mcp-*.pgdump" | wc -l)
echo "Retention: ${REMAINING} backup(s) kept in $DIR"
