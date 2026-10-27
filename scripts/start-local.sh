#!/usr/bin/env bash
# 本地联调启动脚本：加载 .env.local，推导路径类变量，前台运行钉钉桥接。
# 用法：bash scripts/start-local.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${RECRUITER_ENV_FILE:-$ROOT/.env.local}"
if [[ ! -f "$ENV_FILE" ]]; then echo "缺少 $ENV_FILE" >&2; exit 2; fi

# 同一个钉钉机器人只允许一个 Stream 连接持有者：多实例会互相抢消息，
# 且旧实例的日志可能写进已删除的文件，导致"日志空白"的假象。
#
# 闸门用 PID 锁文件而不是进程查询：钉钉 Stream 是**出站**连接，没有可探测的
# 监听端口；唯一能定位进程的是 Get-CimInstance Win32_Process，而它在非管理员
# 会话里返回"拒绝访问"——曾把三个启动脚本全部锁死(exit 5)。锁文件用
# `kill -0` 判活，不需要任何特权，进程已死的陈旧锁会被回收。
LOCK_FILE="$ROOT/workspace/bridge.lock"
LOCK_HELD=0
if [[ "${RECRUITER_ALLOW_MULTI:-0}" != "1" ]]; then
  if [[ -f "$LOCK_FILE" ]]; then
    # [[:space:]]*$ also tolerates a CRLF lock written by the PowerShell starter.
    lock_pid="$(sed -n 's/^PID=\([0-9]\{1,\}\)[[:space:]]*$/\1/p' "$LOCK_FILE" | head -1)"
    if [[ -z "$lock_pid" ]]; then
      echo "锁文件 $LOCK_FILE 无法解析（内容损坏或无权限），为避免重复启动，拒绝继续。" >&2
      echo "删除该文件或设 RECRUITER_ALLOW_MULTI=1 重试。" >&2
      exit 5
    fi
    if kill -0 "$lock_pid" 2>/dev/null; then
      echo "已有桥接实例在运行（pid=$lock_pid），拒绝重复启动。" >&2
      echo "先执行：bash scripts/stop-local.sh   （或设 RECRUITER_ALLOW_MULTI=1 跳过检查）" >&2
      exit 4
    fi
    echo "回收陈旧锁 $LOCK_FILE（原 pid=$lock_pid 已不存在）" >&2
    rm -f "$LOCK_FILE"
  fi
  mkdir -p "$(dirname "$LOCK_FILE")"
  if ! ( set -o noclobber; printf 'PID=%s\n' "$$" > "$LOCK_FILE" ) 2>/dev/null; then
    echo "锁文件 $LOCK_FILE 被并发创建，拒绝重复启动。" >&2
    exit 4
  fi
  LOCK_HELD=1
  trap 'if [[ "$LOCK_HELD" == "1" ]]; then rm -f "$LOCK_FILE"; fi' EXIT
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
# Python 子进程按 UTF-8 输出，避免 Windows 控制台代码页把回复变成乱码。
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

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

# 不要用 exec：exec 会把本进程替换掉，EXIT trap 永远不触发，锁文件会一直留在
# 磁盘上（虽然下次启动会回收，但会误报"回收陈旧锁"）。直接调用即可：set -e
# 保证非零退出码原样传出，trap 也会在 node 退出时清掉锁。
node "$ROOT/im/recruiter-na.mjs"
