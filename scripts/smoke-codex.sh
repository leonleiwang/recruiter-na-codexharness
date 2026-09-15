#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${CODEX_HOME:?Set CODEX_HOME to Recruiter-Na isolated Codex config directory}"
PINNED="$(cat "$ROOT/config/CODEX_VERSION")"
CURRENT="$(codex --version | awk '{print $NF}')"
[[ "$CURRENT" == "$PINNED" ]] || { echo "Codex mismatch current=$CURRENT pinned=$PINNED" >&2; exit 2; }

echo 'Read AGENTS.md. Do not call network or business tools. Reply exactly: RECRUITER_NA_CODEX_READY' \
  | codex exec --ephemeral --skip-git-repo-check --sandbox read-only --color never -C "$ROOT" -
