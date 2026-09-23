#!/usr/bin/env bash
set -euo pipefail
if [[ "${EUID}" -ne 0 ]]; then echo "Run as root: sudo bash deploy/install-cloud.sh" >&2; exit 2; fi
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP=/opt/recruiter-na/app
HOME_DIR=/opt/recruiter-na/codex-home
CODEX_VERSION="$(cat "$SRC/config/CODEX_VERSION")"
PROVIDER="${CODEX_PROVIDER:-deepseek}"

apt-get update
apt-get install -y python3 python3-venv python3-pip curl ca-certificates rsync
if ! command -v node >/dev/null 2>&1 || [[ "$(node -p 'Number(process.versions.node.split(`.`)[0])' 2>/dev/null || echo 0)" -lt 20 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
npm install -g "@openai/codex@${CODEX_VERSION}"
INSTALLED_CODEX_VERSION="$(codex --version | awk '{print $NF}')"
if [[ "$INSTALLED_CODEX_VERSION" != "$CODEX_VERSION" ]]; then
  echo "Codex install verification failed: expected=$CODEX_VERSION actual=$INSTALLED_CODEX_VERSION" >&2
  exit 4
fi

if ! id recruiter-na >/dev/null 2>&1; then useradd --system --home /opt/recruiter-na --shell /usr/sbin/nologin recruiter-na; fi
mkdir -p "$APP" "$HOME_DIR" /etc/recruiter-na
rsync -a --delete --exclude '.venv' --exclude 'node_modules' --exclude '.env' --exclude '*.backup-*' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude 'data/source/resumes/private' --exclude 'data/quarantine' --exclude 'workspace/hiring_manager_requests.sqlite3*' "$SRC/" "$APP/"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
(cd "$APP/vendor/codexharness-im" && npm install --omit=dev)

if [[ ! -f "$HOME_DIR/config.toml" ]]; then
  case "$PROVIDER" in
    deepseek) cp "$APP/config/codex/codex.deepseek.toml.example" "$HOME_DIR/config.toml" ;;
    qwen) cp "$APP/config/codex/codex.qwen-modelstudio.toml.example" "$HOME_DIR/config.toml" ;;
    ollama) cp "$APP/config/codex/codex.ollama.toml.example" "$HOME_DIR/config.toml" ;;
    *) echo "Unknown CODEX_PROVIDER=$PROVIDER" >&2; exit 3 ;;
  esac
fi
if [[ ! -f /etc/recruiter-na/recruiter-na.env ]]; then
  cp "$APP/deploy/recruiter-na.env.example" /etc/recruiter-na/recruiter-na.env
  chmod 600 /etc/recruiter-na/recruiter-na.env
fi
cp "$APP/deploy/recruiter-na.service" /etc/systemd/system/recruiter-na.service
mkdir -p "$APP/workspace/runs" "$APP/workspace/reports" "$APP/workspace/.host-progress" "$APP/workspace/resume-url"
chown -R recruiter-na:recruiter-na /opt/recruiter-na
systemctl daemon-reload

echo "Installed Recruiter-Na v5 app and Codex CLI ${CODEX_VERSION}."
echo "DO NOT start yet. Edit: /etc/recruiter-na/recruiter-na.env"
echo "Required for resume delivery: RECRUITER_RESUME_SOURCE_ROOTS (empty value disables delivery)"
echo "Optional: RECRUITER_RESUME_URL_BASE (served from workspace/resume-url), DINGTALK_AI_CARD_TEMPLATE_ID"
echo "Also edit model/provider placeholders in: $HOME_DIR/config.toml"
echo "Then run offline verification: sudo -u recruiter-na $APP/scripts/verify.sh"
echo "Check Codex sandbox: sudo -u recruiter-na $APP/scripts/diagnose-codex-sandbox.sh"
echo "Finally: systemctl enable --now recruiter-na"
