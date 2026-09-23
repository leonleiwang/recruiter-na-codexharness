#!/usr/bin/env bash
# 停止全部本地桥接实例（Windows/Git Bash）。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pids="$(powershell -NoProfile -ExecutionPolicy Bypass -File "$ROOT/scripts/list-bridges.ps1" 2>/dev/null | sed -n 's/^PID=\([0-9]*\).*/\1/p' || true)"
if [[ -z "$pids" ]]; then echo "没有在运行的桥接实例。"; exit 0; fi
for p in $pids; do
  echo "停止桥接 PID $p"
  taskkill //PID "$p" //F >/dev/null 2>&1 || true
done
sleep 1
left="$(powershell -NoProfile -ExecutionPolicy Bypass -File "$ROOT/scripts/list-bridges.ps1" 2>/dev/null | grep -c '^PID=' || true)"
echo "剩余实例: ${left:-0}"
