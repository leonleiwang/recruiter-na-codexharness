#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install-codex-apparmor-profile.sh" >&2
  exit 2
fi
if ! command -v apparmor_parser >/dev/null 2>&1; then
  echo "apparmor_parser is not installed; install apparmor-utils/apparmor first." >&2
  exit 3
fi

CODEX_CMD="${CODEX_BIN:-codex}"
WRAPPER="$(command -v "$CODEX_CMD" 2>/dev/null || true)"
if [[ -z "$WRAPPER" ]]; then
  echo "Codex CLI not found on PATH." >&2
  exit 4
fi

find_native_codex() {
  local wrapper="$1" root="" candidate=""
  if file -L "$wrapper" 2>/dev/null | grep -q 'ELF '; then
    realpath "$wrapper"
    return 0
  fi
  if command -v npm >/dev/null 2>&1; then
    root="$(npm root -g 2>/dev/null || true)"
    if [[ -n "$root" && -d "$root/@openai/codex" ]]; then
      candidate="$(find "$root/@openai/codex" -type f -path '*/vendor/*/codex/codex' -perm -111 -print -quit 2>/dev/null || true)"
      if [[ -z "$candidate" ]]; then
        candidate="$(find "$root/@openai" -type f -path '*/codex/*/codex' -perm -111 -print -quit 2>/dev/null || true)"
      fi
    fi
  fi
  if [[ -n "$candidate" ]]; then
    realpath "$candidate"
    return 0
  fi
  return 1
}

NATIVE="$(find_native_codex "$WRAPPER" || true)"
if [[ -z "$NATIVE" || ! -x "$NATIVE" ]]; then
  echo "Could not locate the native Codex Linux executable behind: $WRAPPER" >&2
  echo "Do not disable AppArmor globally. Locate the native binary, then adapt this profile manually." >&2
  exit 5
fi

echo "Codex wrapper: $WRAPPER"
echo "Native Codex:  $NATIVE"
file -L "$NATIVE"

PROFILE=/etc/apparmor.d/codex-native
STAMP="$(date +%Y%m%d-%H%M%S)"
if [[ -f "$PROFILE" ]]; then
  cp -a "$PROFILE" "${PROFILE}.backup-${STAMP}"
fi

cat > "$PROFILE" <<EOF
abi <abi/4.0>,
include <tunables/global>

@{codex_bin} = ${NATIVE}

# Targeted exception for Codex's own Bubblewrap/user-namespace sandbox.
# Global Ubuntu/AppArmor restrictions remain enabled.
profile codex-native @{codex_bin} flags=(unconfined) {
  userns,
  @{codex_bin} mr,
  include if exists <local/codex-native>
}
EOF

apparmor_parser -r "$PROFILE"

echo
echo "=== Global restrictions after profile install ==="
for key in kernel.apparmor_restrict_unprivileged_userns kernel.apparmor_restrict_unprivileged_unconfined; do
  printf '%s = ' "$key"
  sysctl -n "$key" 2>/dev/null || echo "n/a"
done

echo
echo "=== Re-test as recruiter-na when available ==="
if id recruiter-na >/dev/null 2>&1; then
  if sudo -u recruiter-na -H env CODEX_HOME=/opt/recruiter-na/codex-home "$WRAPPER" sandbox /bin/true; then
    echo "SANDBOX_PROBE=PASS"
  else
    echo "SANDBOX_PROBE=FAIL -- inspect scripts/diagnose-codex-sandbox.sh output before changing any global security control." >&2
    exit 6
  fi
else
  "$WRAPPER" sandbox /bin/true
fi
