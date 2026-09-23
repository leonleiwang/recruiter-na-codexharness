#!/usr/bin/env bash
# 本地联调启动脚本：加载 .env.local，推导路径类变量，前台运行钉钉桥接。
# 用法：bash scripts/start-local.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${RECRUITER_ENV_FILE:-$ROOT/.env.local}"
if [[ ! -f "$ENV_FILE" ]]; then echo "缺少 $ENV_FILE" >&2; exit 2; fi

# 同一个钉钉机器人只允许一个 Stream 连接持有者：多实例会互相抢消息，
# 且旧实例的日志可能写进已删除的文件，导致"日志空白"的假象。
if [[ "${RECRUITER_ALLOW_MULTI:-0}" != "1" ]]; then
  existing="$(powershell -NoProfile -ExecutionPolicy Bypass -File "$ROOT/scripts/list-bridges.ps1" 2>/dev/null | grep -c '^PID=' || true)"
  if [[ "${existing:-0}" -gt 0 ]]; then
    echo "已有 ${existing} 个桥接实例在运行，拒绝重复启动。" >&2
    echo "先执行：bash scripts/stop-local.sh   （或设 RECRUITER_ALLOW_MULTI=1 跳过检查）" >&2
    exit 4
  fi
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

export RECRUITER_REQUEST_DB="${RECRUITER_REQUEST_DB:-$ROOT/workspace/hiring_manager_requests.sqlite3}"
export CODEX_HOME="${CODEX_HOME:-$ROOT/workspace/codex-home}"
export CODEX_WORKDIR="${CODEX_WORKDIR:-$ROOT}"
export RECRUITER_RESUME_URL_DIR="${RECRUITER_RESUME_URL_DIR:-$ROOT/workspace/resume-url}"
export RECRUITER_DEDUPE_DIR="${RECRUITER_DEDUPE_DIR:-$ROOT/workspace/.im-dedupe}"
export RECRUITER_PROGRESS_DIR="${RECRUITER_PROGRESS_DIR:-$ROOT/workspace/.progress}"
export PYTHONPATH="$ROOT/runtime"

mkdir -p "$ROOT/workspace" "$CODEX_HOME" "$RECRUITER_RESUME_URL_DIR"

# 钉钉凭证缺失则无法启动；其余（LLM/MySQL/简历原件）缺失只告警，便于先验证连通性。
fatal=()
[[ -n "${DINGTALK_CLIENT_ID:-}" ]] || fatal+=("DINGTALK_CLIENT_ID")
[[ -n "${DINGTALK_CLIENT_SECRET:-}" ]] || fatal+=("DINGTALK_CLIENT_SECRET")
if (( ${#fatal[@]} > 0 )); then
  echo "缺少必需变量（${fatal[*]}），请补齐 $ENV_FILE" >&2
  exit 3
fi

warn=()
[[ -n "${RECRUITER_LLM_BASE_URL:-}" && -n "${RECRUITER_LLM_MODEL:-}" ]] || warn+=("RECRUITER_LLM_BASE_URL / RECRUITER_LLM_MODEL")
[[ -n "${DEEPSEEK_API_KEY:-}${DASHSCOPE_API_KEY:-}${RECRUITER_LLM_API_KEY:-}" ]] || warn+=("LLM API Key")
[[ -n "${MYSQL_HOST:-}" ]] || warn+=("MYSQL_HOST（未配置则无法筛选候选人）")
[[ -n "${RECRUITER_RESUME_SOURCE_ROOTS:-}" ]] || warn+=("RECRUITER_RESUME_SOURCE_ROOTS（未配置则无法投递简历原件）")

echo "启动 Recruiter-Na 桥接"
echo "  root=$ROOT"
echo "  codex_home=$CODEX_HOME"
echo "  python=${RECRUITER_PYTHON_BIN:-python}"
echo "  request_db=$RECRUITER_REQUEST_DB"
if (( ${#warn[@]} > 0 )); then
  echo "  未配置（功能受限）:" >&2
  printf '    - %s\n' "${warn[@]}" >&2
fi

exec node "$ROOT/im/recruiter-na.mjs"
