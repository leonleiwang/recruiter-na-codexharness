#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then PY=python3; fi
export PYTHONPATH="$ROOT/runtime"

echo "[1/10] Data foundation audit"
"$PY" "$ROOT/scripts/audit-data-foundation.py"

echo "[2/10] Python tests"
"$PY" -m pytest "$ROOT/tests" -q

echo "[3/10] Python syntax"
"$PY" -m compileall -q "$ROOT/runtime" "$ROOT/scripts"

echo "[4/10] codexharness-im tests"
(cd "$ROOT/vendor/codexharness-im" && npm test && npm run check)

echo "[5/10] Recruiter IM tests + syntax"
node --test "$ROOT/im"/*test.mjs
node --check "$ROOT/im/recruiter-na.mjs"
node --check "$ROOT/im/router.mjs"

echo "[6/10] Product scope / owner-boundary scan"
SKILLS="$(find "$ROOT/.agents/skills" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort | tr '\n' ' ')"
if [[ "$SKILLS" != "hiring-request my-hiring-progress " ]]; then
  echo "Unexpected public skills: $SKILLS" >&2; exit 5
fi
if grep -RIn --exclude='*.test.mjs' -E 'resume\.count|profile\.count|jd\.count|requisition\.list|screen\.prepare|screen\.run' "$ROOT/im" "$ROOT/.agents/skills"; then
  echo "Admin/library route leaked into hiring-manager public path" >&2; exit 6
fi
if ! grep -q "owner_staff_id" "$ROOT/runtime/rn/hiring_requests.py"; then
  echo "Owner-scoped request store missing" >&2; exit 7
fi

echo "[7/10] v5 active-path / no-formal-score guard"
ACTIVE=(
  "$ROOT/runtime/rn/host_tool.py"
  "$ROOT/runtime/rn/hiring_workflow_v5.py"
  "$ROOT/runtime/rn/screening_v5.py"
  "$ROOT/runtime/rn/candidate_decision_v5.py"
  "$ROOT/runtime/rn/criteria_adapter_v5.py"
  "$ROOT/runtime/rn/decision_evidence_evaluator.py"
  "$ROOT/.agents/skills/hiring-request/SKILL.md"
  "$ROOT/README.md" "$ROOT/AGENTS.md" "$ROOT/USAGE.md"
)
if grep -nHE 'scoring_v4|scoring_engine_v3|screening_v4|screening_pipeline_v3|ProfileMatch ×|JDMatch ×|RQ ×|JM =|CS =' "${ACTIVE[@]}"; then
  echo "Formal scoring contract/import leaked into v5 active path" >&2; exit 8
fi
if ! grep -q 'from rn.hiring_workflow_v5 import HiringManagerWorkflow' "$ROOT/runtime/rn/host_tool.py"; then
  echo "Host is not using v5 workflow" >&2; exit 9
fi
if ! grep -q 'from rn.screening_v5 import HiringDecisionScreeningService' "$ROOT/runtime/rn/host_tool.py"; then
  echo "Host is not using v5 Candidate Decision screening" >&2; exit 10
fi

echo "[8/10] Obsolete active path scan"
if grep -RIn --exclude='*.pyc' -E 'scoring_v22|evaluate_hr_skill_v22|_match_tag|hr-skill-v2\.2|简历筛选规则_v2\.2|workspace/resume_db\.json|data-gateway\.sock' "$ROOT/runtime" "$ROOT/im" "$ROOT/.agents/skills" "$ROOT/AGENTS.md"; then
  echo "Obsolete active path reference found" >&2; exit 11
fi

echo "[9/10] Deployment script syntax"
while IFS= read -r -d '' f; do bash -n "$f"; done < <(find "$ROOT/deploy" "$ROOT/scripts" -type f -name '*.sh' -print0)

echo "[10/10] Obvious secret scan"
"$PY" "$ROOT/scripts/secret_scan.py"

echo "VERIFY_PASS"
