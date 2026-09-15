# Recruiter-Na v5.0.0 Candidate Decision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace the active v4 score-based screening path with the accepted v2.3 inspection-technician Candidate Decision contract while preserving hiring-manager intake and recommendation state behavior.

**Architecture:** Keep the existing request/JD and Top-3 state machine, add a versioned inspection-technician policy, an Actual Criteria adapter, a strict semantic decision-evidence evaluator, deterministic decision engine and v5 screening service. Old score modules remain non-active and no unsupported role may fall back to them.

**Tech Stack:** Python 3, SQLite, pytest, existing OpenAI-compatible HTTP LLM configuration, existing repositories.

**Spec:** `docs/superpowers/specs/2026-09-11-recruiter-na-v5-decision-design.md`

## Global Constraints

- Version is exactly `5.0.0`.
- Screening policy v1 supports only 检验技术员 / 检验技术员（应届） / 检验技术员（社招） and the two verified profile IDs defined in the spec.
- No active screening result contains formal numerical candidate score fields.
- UNKNOWN is never an automatic fail.
- Final pass/fail is deterministic Python, not LLM output.
- Existing SQLite schema and Top-3 cursor persistence stay compatible.
- The source archive contains no `.git`; execution uses an isolated extracted workspace and cannot make task commits.

---

### Task 1: Freeze v2.3 policy and criteria adapter

**Files:**
- Create: `knowledge/recruiting/policies/inspection_technician_v1.json`
- Create: `runtime/rn/criteria_adapter_v5.py`
- Create: `tests/test_criteria_adapter_v5.py`

**Interfaces:**
- Produces: `load_inspection_technician_policy() -> dict`
- Produces: `supports_inspection_technician(request, profile=None) -> bool`
- Produces: `build_actual_criteria(request, profile=None) -> dict`

- [x] Write failing tests for supported/unsupported profile scope, request location override, and extra hard requirements becoming `hard_gate` criteria.
- [x] Run the focused test and verify it fails because the v5 adapter does not exist.
- [x] Add the accepted v2.3 policy JSON and minimal adapter implementation.
- [x] Run the focused tests and verify green.

### Task 2: Add strict v5 evidence contract and grounding

**Files:**
- Create: `runtime/rn/decision_evidence_evaluator.py`
- Modify: `runtime/rn/evidence_grounding.py`
- Create: `tests/test_decision_evidence_v5.py`

**Interfaces:**
- Produces: `DecisionEvidenceEvaluator.evaluate(criteria, resume_facts) -> dict`
- Produces: `validate_decision_grounding(resume_facts, assessment) -> dict`

- [x] Write failing tests covering strict enum validation, unknown-without-evidence, explicit negative evidence requirements, and grounding failure.
- [x] Run the focused tests and verify red.
- [x] Implement the strict JSON contract/evaluator and v5 grounding validator without score fields.
- [x] Run the focused tests and verify green.

### Task 3: Implement deterministic Candidate Decision

**Files:**
- Create: `runtime/rn/candidate_decision_v5.py`
- Create: `tests/test_candidate_decision_v5.py`

**Interfaces:**
- Produces: `decide_candidate(criteria, assessment, resume_facts=None) -> dict`
- Produces: `recommendation_order_key(decision, pool_index) -> tuple`

- [x] Write failing tests for hard-check fail, hard-check UNKNOWN, hard-gate veto, resume-evidence not-satisfied -> verify, interview-verify -> focus, preference signals, and stable recommendation ordering.
- [x] Run focused tests and verify red.
- [x] Implement deterministic decision table and ordering key.
- [x] Run focused tests and verify green.

### Task 4: Replace active screening service

**Files:**
- Create: `runtime/rn/screening_v5.py`
- Create: `tests/test_screening_v5.py`

**Interfaces:**
- Produces: `evaluate_candidate_v5(resume, request, profile, evaluator, trace_dir=None) -> dict`
- Produces: `HiringDecisionScreeningService.screen_request(request) -> dict`

- [x] Write failing tests proving full-pool evaluation, no score fields, unsupported-role refusal, pass-only recommendations, trace output, and technical-failure isolation.
- [x] Run focused tests and verify red.
- [x] Implement v5 screening using normalize_resume_facts -> evidence evaluator -> grounding -> deterministic decision.
- [x] Run focused tests and verify green.

### Task 5: Integrate v5 hiring-manager workflow and host entry point

**Files:**
- Create: `runtime/rn/hiring_workflow_v5.py`
- Modify: `runtime/rn/host_tool.py`
- Modify: `tests/test_hiring_workflow_v4.py` or replace with `tests/test_hiring_workflow_v5.py`
- Modify: `tests/test_hiring_requests_v4.py` only if assertion names require recommendation-order wording.

**Interfaces:**
- `HiringManagerWorkflow` remains the host-facing class name, imported from `hiring_workflow_v5`.
- `HiringDecisionScreeningService.screen_request()` replaces active `rank_request()`.

- [x] Write/update failing workflow tests: candidate cards contain no scores; Top-3/next-batch no-repeat survives; unsupported role does not enter SCREENING permanently; selection and owner isolation survive.
- [x] Run focused tests and verify red.
- [x] Implement v5 workflow rendering and unsupported-policy handling; wire host tool to v5.
- [x] Run focused tests and verify green.

### Task 6: Remove scoring from active CLI/docs/skills and version package

**Files:**
- Modify: `runtime/rn/cli.py`
- Modify: `.agents/skills/hiring-request/SKILL.md`
- Modify: `README.md`
- Modify: `USAGE.md`
- Modify: `AGENTS.md`
- Modify: `CHANGELOG.md`
- Modify: `VERSION`
- Create: `docs/RECRUITMENT_DECISION_V5.md`
- Create: `deploy/upgrade-v5.0.0.sh`
- Create/modify: `tests/test_no_active_scoring_v5.py`, `tests/test_codex_packaging.py`

- [x] Write failing static/packaging tests asserting active v5 modules and user-facing skill/docs do not reference formal score formulas and version is 5.0.0.
- [x] Run focused tests and verify red.
- [x] Update CLI/docs/skill/deploy/version and mark old scoring path legacy/non-active.
- [x] Run focused tests and verify green.

### Task 7: Full regression and package verification

**Files:**
- Modify only if a regression exposes a v5 compatibility defect.
- Create: `docs/V5_VERIFICATION_REPORT.md`

- [x] Run `PYTHONPATH=runtime python -m pytest -q` and record exact result.
- [x] Run syntax compilation over `runtime/rn`.
- [x] Run grep/static scan proving active v5 modules have no RQ/JM/CS score output.
- [x] Run a deterministic local fake-evaluator smoke flow for Top-3 and next-batch.
- [x] Write verification report with commands and results.
- [x] Remove cache/OS metadata and build `recruiter-na-codexharness-v5.0.0.zip`.
- [x] Extract the final ZIP to a clean directory and rerun the full automated suite against the packaged artifact.
