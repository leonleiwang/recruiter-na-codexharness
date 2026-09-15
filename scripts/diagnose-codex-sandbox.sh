#!/usr/bin/env bash
set -u

CODEX_BIN="${CODEX_BIN:-codex}"
FAIL=0

echo "=== Codex ==="
command -v "$CODEX_BIN" || true
"$CODEX_BIN" --version 2>&1 || FAIL=1

echo
echo "=== Host ==="
uname -a || true
if [[ -r /etc/os-release ]]; then cat /etc/os-release; fi
if command -v bwrap >/dev/null 2>&1; then bwrap --version || true; else echo "bwrap: not found on PATH (Codex may use a bundled helper)"; fi

echo
echo "=== User namespace / AppArmor controls ==="
for key in \
  kernel.unprivileged_userns_clone \
  user.max_user_namespaces \
  kernel.apparmor_restrict_unprivileged_userns \
  kernel.apparmor_restrict_unprivileged_unconfined; do
  printf '%s = ' "$key"
  sysctl -n "$key" 2>/dev/null || echo "n/a"
done
if command -v aa-status >/dev/null 2>&1; then aa-status --enabled >/dev/null 2>&1 && echo "AppArmor = enabled" || echo "AppArmor = installed/not enabled"; fi

echo
echo "=== Minimal Codex Linux sandbox probe ==="
TMP="$(mktemp)"
if "$CODEX_BIN" sandbox /bin/true >"$TMP" 2>&1; then
  cat "$TMP"
  rm -f "$TMP"
  echo "SANDBOX_PROBE=PASS"
  exit "$FAIL"
fi
STATUS=$?
cat "$TMP"
if grep -q 'bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted' "$TMP"; then
  echo
  echo "SANDBOX_PROBE=FAIL_APPARMOR_USERNS"
  echo "Detected the Ubuntu/AppArmor Bubblewrap loopback failure."
  echo "Use the targeted repair only after reviewing it:"
  echo "  sudo bash deploy/install-codex-apparmor-profile.sh"
  echo "This project does NOT recommend disabling AppArmor user-namespace restrictions globally."
else
  echo "SANDBOX_PROBE=FAIL_OTHER"
fi
rm -f "$TMP"
exit "$STATUS"
