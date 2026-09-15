#!/usr/bin/env bash
set -euo pipefail

echo '=== recruiter-na.service ==='
systemctl status recruiter-na --no-pager || true

echo
echo '=== all Recruiter-Na bridge processes ==='
mapfile -t PIDS < <(pgrep -f '/opt/recruiter-na/app/im/recruiter-na\.mjs' || true)
if [[ ${#PIDS[@]} -eq 0 ]]; then
  echo 'none'
else
  ps -o pid,ppid,user,lstart,cmd -p "$(IFS=,; echo "${PIDS[*]}")"
  echo
  for pid in "${PIDS[@]}"; do
    echo "--- pid=$pid ---"
    printf 'cwd: '; readlink -f "/proc/$pid/cwd" || true
    echo 'cgroup:'
    cat "/proc/$pid/cgroup" 2>/dev/null || true
  done
fi

echo
echo '=== active Codex exec processes ==='
pgrep -af '(^|/)codex( |$).*exec|codex exec' || echo 'none'

echo
echo 'Expected steady state: exactly ONE recruiter-na.mjs process, owned by recruiter-na.service.'
