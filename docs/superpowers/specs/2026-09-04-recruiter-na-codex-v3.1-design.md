# Recruiter-Na Codex v3.1 Design

## Goal
Migrate Recruiter-Na from DSH to Codex CLI while making semantic evidence the sole authority for job-match interpretation. Deterministic Python computes scores only after complete, grounded LLM evidence exists.

## Runtime chain
`job source -> JobContract v3 -> ResumeFacts -> LLM SemanticEvidence -> grounding validation -> deterministic scoring_engine_v3 -> ranking/report`.

## Non-negotiable rules
- Keywords may retrieve candidates or passages, but may not set evidence levels, requirement status, JM, CS, or veto.
- No semantic evidence, incomplete evidence, LLM timeout, invalid JSON, or ungrounded quote means no formal JM/CS.
- `UNKNOWN` on any hard gate means `needs_verification`, never veto.
- Only an explicit `HARD_GATE + FAIL` may veto.
- The LLM never emits total scores. Python never performs semantic matching.
- Positive evidence must be grounded to exact resume facts/text.
- Active runtime contains no v2.2 scorer or silent keyword fallback.
- Secrets are injected only from environment variables.

## Deployment
Codex CLI is an external host runtime pinned to 0.153.2. The release ZIP contains Recruiter-Na source, skills, Python runtime, deployment scripts, and vendored codexharness-im, but not Codex binaries, virtualenvs, node_modules, secrets, real resumes, or historical reports.
