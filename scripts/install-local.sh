#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PINNED="$(cat "$ROOT/config/CODEX_VERSION")"

if ! command -v codex >/dev/null 2>&1; then
  echo "Codex CLI not found. Install pinned version: npm install -g @openai/codex@$PINNED" >&2
  exit 2
fi
CURRENT="$(codex --version | awk '{print $NF}')"
if [[ "$CURRENT" != "$PINNED" && "${ALLOW_CODEX_VERSION_MISMATCH:-0}" != "1" ]]; then
  echo "Codex version mismatch: current=$CURRENT pinned=$PINNED" >&2
  echo "Set ALLOW_CODEX_VERSION_MISMATCH=1 only for intentional compatibility testing." >&2
  exit 2
fi

python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt"
(
  cd "$ROOT/vendor/codexharness-im"
  npm install --omit=dev
)
mkdir -p "$ROOT/workspace/runs" "$ROOT/workspace/reports"
# Production resumes are MySQL-authoritative. Development JSON data lives only
# in data/dev/resume_db.fixture.json and is never created under workspace/.

echo "Recruiter-Na v5 local dependencies installed."
echo "Next: create an isolated CODEX_HOME/config.toml from config/codex/*.example, export provider/API env vars, then run scripts/verify.sh and scripts/smoke-codex.sh."
