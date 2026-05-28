#!/bin/bash
# rpm-mcp database backup -- pg_dump -Fc + 7-day retention.
# Run daily via the rpm-mcp-backup.timer systemd user unit.
#
# Env vars:
#   DATABASE_URL       PostgreSQL DSN (falls back to the default rpm_mcp DSN)
#   RPM_MCP_BACKUP_DIR Backup directory (default: ~/rpm-mcp-backup)

set -euo pipefail

DSN="${DATABASE_URL:-postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp}"
DIR="${RPM_MCP_BACKUP_DIR:-$HOME/rpm-mcp-backup}"

mkdir -p "$DIR"

STAMP="$(date +%Y%m%d-%H%M)"
OUTFILE="$DIR/rpm-mcp-${STAMP}.pgdump"

pg_dump -Fc "$DSN" -f "$OUTFILE"
echo "Backup written: $OUTFILE ($(du -sh "$OUTFILE" | cut -f1))"

# Prune backups older than 7 days
find "$DIR" -name "rpm-mcp-*.pgdump" -mtime +7 -delete
REMAINING=$(find "$DIR" -name "rpm-mcp-*.pgdump" | wc -l)
echo "Retention: ${REMAINING} backup(s) kept in $DIR"
