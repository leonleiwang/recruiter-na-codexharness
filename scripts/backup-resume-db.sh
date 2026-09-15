#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${MYSQL_HOST:?MYSQL_HOST is required}"
: "${MYSQL_USER:?MYSQL_USER is required}"
: "${MYSQL_PASSWORD:?MYSQL_PASSWORD is required}"
: "${MYSQL_DATABASE:?MYSQL_DATABASE is required}"
PORT="${MYSQL_PORT:-3306}"
OUT_DIR="${1:-$ROOT/backups/resume-db}"
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$OUT_DIR/recruiter-resume-${STAMP}.sql.gz"
MYSQL_PWD="$MYSQL_PASSWORD" mysqldump \
  -h "$MYSQL_HOST" -P "$PORT" -u "$MYSQL_USER" \
  --single-transaction --quick "$MYSQL_DATABASE" \
  candidate_resume candidate_info mail_receive_log | gzip -9 > "$OUT"
sha256sum "$OUT" > "$OUT.sha256"
printf '%s\n' "$OUT"
