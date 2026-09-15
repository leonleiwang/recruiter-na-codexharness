#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${MYSQL_HOST:?MYSQL_HOST is required}"
: "${MYSQL_USER:?MYSQL_USER is required}"
: "${MYSQL_PASSWORD:?MYSQL_PASSWORD is required}"
: "${MYSQL_DATABASE:?MYSQL_DATABASE is required}"
PORT="${MYSQL_PORT:-3306}"
OUT="${1:-$ROOT/data/schema/resume_mysql_schema.production.sql}"
mkdir -p "$(dirname "$OUT")"
MYSQL_PWD="$MYSQL_PASSWORD" mysqldump \
  -h "$MYSQL_HOST" -P "$PORT" -u "$MYSQL_USER" \
  --no-data --skip-comments "$MYSQL_DATABASE" \
  candidate_resume candidate_info mail_receive_log > "$OUT"
sha256sum "$OUT" > "$OUT.sha256"
printf '%s\n' "$OUT"
