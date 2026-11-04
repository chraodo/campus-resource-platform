#!/usr/bin/env bash
set -euo pipefail

backup_file="${1:-}"
if [[ -z "$backup_file" || ! -f "$backup_file" ]]; then
  echo "Usage: CAMPUS_CONFIRM_DATABASE_RESTORE=RESTORE $0 /absolute/path/to/backup.sql.gz" >&2
  exit 2
fi
if [[ "${CAMPUS_CONFIRM_DATABASE_RESTORE:-}" != "RESTORE" ]]; then
  echo "Database restore replaces all current campus data." >&2
  echo "Set CAMPUS_CONFIRM_DATABASE_RESTORE=RESTORE after a human approves this backup." >&2
  exit 2
fi
gzip -t "$backup_file"

compose=(docker compose -f compose.yaml -f compose.prod.yaml)
"${compose[@]}" stop api worker resource
"${compose[@]}" exec -T db psql -U campus -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS campus WITH (FORCE);" \
  -c "CREATE DATABASE campus OWNER campus;"
gzip -dc "$backup_file" | "${compose[@]}" exec -T db psql -U campus -d campus -v ON_ERROR_STOP=1
"${compose[@]}" up --detach resource api worker
echo "Database restore completed. Verify API health and business data before reopening traffic."
