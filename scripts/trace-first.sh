#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OWNER="${1:?usage: scripts/trace-first.sh OWNER_STAFF_ID REQUEST_ID}"
REQUEST_ID="${2:?usage: scripts/trace-first.sh OWNER_STAFF_ID REQUEST_ID}"
export PYTHONPATH="$ROOT/runtime"
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/trace-first-v4.py" --owner "$OWNER" --request-id "$REQUEST_ID" --pool-limit 3
