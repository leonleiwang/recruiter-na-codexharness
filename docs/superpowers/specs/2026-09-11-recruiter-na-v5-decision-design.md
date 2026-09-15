# Recruiter-Na v5.0.0 Candidate Decision Architecture

## Goal

Replace the active v4 numerical candidate scoring path with the accepted Yushilong all-process v2.3 Candidate Decision contract for the inspection-technician MVP, while preserving the existing hiring-manager intake/JD workflow, owner isolation, Top-3/next-batch state machine, resume source, and DingTalk host entry point.

## Product boundary

Recruiter-Na remains a hiring-manager product. Hiring managers do not see or edit `decision_mode`, evidence hints, profile-internal screening modes, or the recruiter-authored full profile workflow. The standard profile/policy is a backend completion and decision asset. The hiring manager only supplies the hiring request and request-specific delta such as location, headcount, salary, urgency, and explicit extra must-have conditions.

## Supported screening scope

v5.0.0 enables the new decision engine only for:

- standard profile `PROFILE-91603AA55F6E` — 检验技术员（应届）
- standard profile `PROFILE-412167F2937C` — 检验技术员（社招）
- exact normalized request title `检验技术员` / `检验技术员（应届）` / `检验技术员（社招）`

Other jobs may still create hiring requests and JD content, but candidate screening returns an explicit unsupported-policy response. It MUST NOT fall back to v4 RQ/JM/CS scoring.

## Architecture

### 1. Hiring-manager workflow

Reuse the v4 intake semantics: semantic profile matching, matched-profile reduced intake, no-match full intake, request-specific JD generation, per-owner state, pause/resume, selection, Top-3 and next-batch behavior.

Create `hiring_workflow_v5.py` for the v5 active flow. It invokes `screen_request()` rather than `rank_request()` and renders decision-oriented candidate cards without numeric score fields.

### 2. Business decision policy

Store the accepted v2.3 inspection-technician judgment contract as a versioned JSON policy under `knowledge/recruiting/policies/inspection_technician_v1.json`.

The policy contains:

- provenance and supported profile IDs/titles;
- hard filters for education/certification/location/red lines;
- capabilities, experiences, traits;
- each criterion's `level`, `decision_mode`, definition and evidence hints.

The policy is independent from the 14 verified standard-profile source PDFs. The verified profile library continues to drive semantic profile matching; the policy drives candidate decision for the supported role.

### 3. Actual criteria adapter

`criteria_adapter_v5.py` loads the policy and builds an Actual Hiring Criteria contract from:

- the accepted inspection-technician policy;
- request-specific job location;
- request metadata;
- explicit `extra_hard_requirements` supplied by the hiring manager.

Extra must-have requirements become additional `hard_gate` criteria. The adapter does not ask the manager to choose internal decision modes.

### 4. Semantic evidence evaluator

`decision_evidence_evaluator.py` asks the configured LLM to emit only semantic facts and evidence:

- hard-check statuses:
  - education: `matched | not_matched | unknown`
  - certification: `valid | pending_exam | expired | none | unknown`
  - location: `match | willing | unknown | refused`
  - each red line: `hit | clear | unknown`
- each criterion:
  - `criterion_result = satisfied | not_satisfied | unknown`
  - `evidence_status = confirmed | partial | none`
  - grounded evidence quotes and source paths.

The evaluator MUST NOT output scores, ranking numbers, recommendation probabilities, or the final pass/fail decision.

Missing information is `unknown`, never automatic failure. `not_satisfied`, `none`, `refused`, `expired`, and red-line `hit` require explicit resume evidence.

### 5. Grounding

Extend evidence grounding with `validate_decision_grounding()`.

Any definitive positive/negative hard check or definitive criterion result must be traceable to authoritative normalized resume facts. Ungrounded or structurally invalid LLM output fails candidate evaluation; it does not silently become a rejection.

### 6. Deterministic Candidate Decision engine

`candidate_decision_v5.py` is the only component allowed to produce the final decision.

Hard checks:

- education `not_matched` -> fail; `unknown` -> needs verification
- certification `expired | none` -> fail; `unknown` -> needs verification; `valid | pending_exam` -> pass
- location `refused` -> fail; `unknown` -> needs verification
- any red-line `hit` -> fail; `unknown` -> needs verification

Criterion table:

| decision_mode | satisfied | not_satisfied | unknown |
|---|---|---|---|
| hard_gate | pass | veto | needs_verification |
| resume_evidence | pass | needs_verification | needs_verification |
| interview_verify | pass | interview_focus | interview_focus |
| preference | preference_signal | no effect | no effect |

Top-level `decision = fail` only when a hard check fails or `hard_gate + not_satisfied` occurs. All other cases remain `pass`, possibly with verification or interview-focus items.

### 7. Recommendation ordering

There is no formal candidate score.

Only candidates with `decision=pass` enter the recommendation list. Stable ordering is:

1. pass with no `needs_verification` before pass with verification;
2. more confirmed `preference_signals` first;
3. more confirmed satisfied resume-evidence criteria first;
4. fewer verification items first;
5. original candidate-pool order as deterministic final tie-break.

The stored order is a recommendation-view order, not an objective talent ranking. No RQ/JM/CS fields are emitted.

### 8. Persistence compatibility

Keep the existing SQLite `ranking_json` column and Top-3 cursor fields to avoid a destructive database migration. In v5, `ranking_json` stores recommendation-order entries rather than score rows. `next_batch()` and selected-candidate ownership behavior remain unchanged.

### 9. Traces

Each screening run writes:

- `decision_policy.json`
- `actual_criteria.json`
- request snapshot and source snapshot
- per candidate:
  - `resume_facts.json`
  - `decision_evidence.json`
  - `decision_evidence_validation.json`
  - `candidate_decision.json`
- `decision_snapshot.json`

Legacy score trace names are not used by the active v5 path.

### 10. Legacy code

Old scoring modules may remain in the package for historical compatibility, but active v5 host/workflow/screening code must not import or invoke `scoring_v4`, `scoring_engine_v3`, or `screening_v4`. Documentation must describe them as legacy/non-active if retained.

## Error handling

- Unsupported role policy -> explicit user message; request remains JD-ready and no score fallback occurs.
- LLM configuration/evaluation failure -> candidate goes to technical failure list, not pass/fail.
- Grounding failure -> candidate goes to technical failure list, not automatic rejection.
- Empty eligible list -> Top-3 state machine transitions to waiting-for-HR-sourcing as before.

## Acceptance criteria

1. No active v5 result or user-facing candidate card contains `RQ`, `JM`, `CS`, ProfileMatch/JDMatch score, or numerical candidate score.
2. `UNKNOWN` hard-check information never causes automatic rejection.
3. Only grounded explicit negative facts can trigger a hard-check fail or hard-gate veto.
4. `criterion_result` and `evidence_status` remain independent fields.
5. Top-3, next-batch, no-repeat, selection, pause/resume and owner isolation continue to work.
6. Unsupported roles never fall back to old scoring.
7. Existing data repositories and DB schema remain usable without destructive migration.
8. Full automated suite passes from a clean extracted package.
