#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/upgrade-v5.0.2.sh" >&2
  exit 2
fi
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP=/opt/recruiter-na/app
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/opt/recruiter-na/backups/app-before-v5.0.2-${STAMP}"

# Verify release before touching production.
if [[ -x "$APP/.venv/bin/python" ]]; then
  PYTHON_BIN="$APP/.venv/bin/python" bash "$SRC/scripts/verify.sh"
else
  bash "$SRC/scripts/verify.sh"
fi

mkdir -p "$(dirname "$BACKUP")"
if [[ -d "$APP" ]]; then
  mkdir -p "$BACKUP"
  rsync -a \
    --exclude '.venv' --exclude 'node_modules' \
    --exclude 'workspace/runs' --exclude 'workspace/reports' \
    --exclude 'workspace/hiring_manager_requests.sqlite3*' \
    --exclude 'workspace/.im-dedupe' --exclude 'workspace/.progress' --exclude 'workspace/.host-progress' \
    "$APP/" "$BACKUP/"
fi
if [[ -f /etc/recruiter-na/recruiter-na.env ]]; then
  cp -a /etc/recruiter-na/recruiter-na.env "/etc/recruiter-na/recruiter-na.env.backup-${STAMP}"
  # v5 renamed the concurrency knob because formal candidate scoring is gone.
  # Preserve the operator's previous concurrency value when present.
  if ! grep -q '^RECRUITER_DECISION_WORKERS=' /etc/recruiter-na/recruiter-na.env; then
    OLD_WORKERS="$(sed -n 's/^RECRUITER_SCORING_WORKERS=//p' /etc/recruiter-na/recruiter-na.env | tail -n 1)"
    printf '\nRECRUITER_DECISION_WORKERS=%s\n' "${OLD_WORKERS:-3}" >> /etc/recruiter-na/recruiter-na.env
  fi

  # v5.0.2 keeps Candidate Decision interactive and migrates only the
  # v5.0.1 auto-generated DeepSeek decision route. Global LLM settings stay untouched.
  ensure_env() {
    local key="$1" value="$2"
    if ! grep -q "^${key}=" /etc/recruiter-na/recruiter-na.env; then
      printf '%s=%s\n' "$key" "$value" >> /etc/recruiter-na/recruiter-na.env
    fi
  }
  set_env_value() {
    local key="$1" value="$2"
    if grep -q "^${key}=" /etc/recruiter-na/recruiter-na.env; then
      sed -i "s|^${key}=.*|${key}=${value}|" /etc/recruiter-na/recruiter-na.env
    else
      printf '%s=%s\n' "$key" "$value" >> /etc/recruiter-na/recruiter-na.env
    fi
  }

  LEGACY_FALLBACKS="$(sed -n 's/^RECRUITER_LLM_FALLBACK_MODELS=//p' /etc/recruiter-na/recruiter-na.env | tail -n 1)"
  CURRENT_DECISION_MODEL="$(sed -n 's/^RECRUITER_DECISION_MODEL=//p' /etc/recruiter-na/recruiter-na.env | tail -n 1)"
  CURRENT_DECISION_FALLBACKS="$(sed -n 's/^RECRUITER_DECISION_FALLBACK_MODELS=//p' /etc/recruiter-na/recruiter-na.env | tail -n 1)"

  has_legacy_model() {
    local needle="$1"
    printf '%s' "$LEGACY_FALLBACKS" | tr ',' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | grep -Fxq "$needle"
  }

  # v5.0.1 may have auto-selected deepseek-v4-flash. Replace only that generated
  # route (or an empty/general DeepSeek route) when the operator already approved
  # both Qwen models in RECRUITER_LLM_FALLBACK_MODELS. Custom decision models win.
  if has_legacy_model qwen3.6-flash && has_legacy_model qwen3.7-max; then
    case "$CURRENT_DECISION_MODEL" in
      ""|deepseek-v4-pro|deepseek-v4-flash)
        set_env_value RECRUITER_DECISION_MODEL qwen3.6-flash
        ;;
    esac
    if [[ -z "$CURRENT_DECISION_FALLBACKS" || "$CURRENT_DECISION_FALLBACKS" == "deepseek-v4-flash" ]]; then
      set_env_value RECRUITER_DECISION_FALLBACK_MODELS qwen3.7-max
    fi
  fi

  ensure_env RECRUITER_DECISION_TIMEOUT_SECONDS 15
  ensure_env RECRUITER_DECISION_MAX_ATTEMPTS 1
  ensure_env RECRUITER_DECISION_MAX_MODELS 2
fi

systemctl stop recruiter-na 2>/dev/null || true
# Backup the owner-scoped request DB after the writer is stopped. The v5 schema
# intentionally keeps ranking_json for recommendation-order compatibility.
if [[ -f "$APP/workspace/hiring_manager_requests.sqlite3" ]]; then
  mkdir -p "$BACKUP/workspace"
  python3 - "$APP/workspace/hiring_manager_requests.sqlite3" "$BACKUP/workspace/hiring_manager_requests.sqlite3" <<'PYBACKUP'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
    s.backup(d)
PYBACKUP
fi

# Obsolete v3 unix-socket service stays disabled.
systemctl disable --now recruiter-na-data 2>/dev/null || true
rm -f /run/recruiter-na/data-gateway.sock 2>/dev/null || true

mkdir -p "$APP"
rsync -a --delete \
  --exclude '.venv' --exclude 'node_modules' --exclude '.env' --exclude '*.backup-*' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude 'data/source/resumes/private' --exclude 'data/quarantine' \
  --exclude 'workspace/runs' --exclude 'workspace/reports' \
  --exclude 'workspace/hiring_manager_requests.sqlite3*' \
  --exclude 'workspace/.im-dedupe' --exclude 'workspace/.progress' --exclude 'workspace/.host-progress' \
  "$SRC/" "$APP/"

if [[ ! -x "$APP/.venv/bin/python" ]]; then python3 -m venv "$APP/.venv"; fi
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
(cd "$APP/vendor/codexharness-im" && npm install --omit=dev)

cp "$APP/deploy/recruiter-na.service" /etc/systemd/system/recruiter-na.service
mkdir -p "$APP/workspace/runs" "$APP/workspace/reports" "$APP/workspace/.host-progress" "$APP/workspace/resume-url"
rm -f "$APP/workspace/resume_db.json" "$APP/workspace/resume_db.example.json"
chown -R recruiter-na:recruiter-na /opt/recruiter-na

systemctl daemon-reload
systemctl enable recruiter-na >/dev/null
systemctl restart recruiter-na

printf '\n=== Recruiter-Na v5.0.2 upgrade complete ===\n'
systemctl --no-pager --full status recruiter-na | sed -n '1,20p' || true
printf '\nBackup: %s\n' "$BACKUP"
printf 'Preserved secrets: /etc/recruiter-na/recruiter-na.env\n'
printf 'Preserved hiring requests: workspace/hiring_manager_requests.sqlite3*\n'
printf 'Obsolete recruiter-na-data.service: disabled\n'
printf '\nProduct gates:\n'
printf '  1) /new → exactly one reply\n'
printf '  2) 我要招一个检验技术员，杭州，1个人 → profile semantic match + reduced intake\n'
printf '  3) 补充本次特殊要求 → internal criteria adapter; decision_mode remains hidden\n'
printf '  4) 完成缺项 → request-specific JD\n'
printf '  5) 开始筛选 → Candidate Decision + first 3; grounding mismatch downgrades to VERIFY, no numeric score\n'
printf '  6) 换一批 → next 3, no duplicates\n'
printf '  7) unsupported role screening → explicit unsupported policy, no legacy scorer fallback\n'
printf '  8) 检查 candidates/*/timing.json → interactive latency trace available\n'
printf '  9) 查看我的招聘进展 → current sender only\n'
