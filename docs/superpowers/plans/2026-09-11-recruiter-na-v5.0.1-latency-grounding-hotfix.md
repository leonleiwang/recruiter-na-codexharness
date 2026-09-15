# Recruiter-Na v5.0.1 Latency & Grounding Hotfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Turn the production 3-candidate, ~9-minute grounding-failure path into a bounded-latency, fail-safe Candidate Decision flow without changing recruitment policy semantics.

**Architecture:** Preserve v5 Candidate Decision. Add deterministic grounding repair that only canonicalizes exact grounded quotes or downgrades ungrounded outcomes to UNKNOWN. Give the decision evaluator its own fail-fast timeout/retry/model-chain policy and add latency observability plus a concurrency regression test.

**Tech Stack:** Python 3, pytest, requests, ThreadPoolExecutor, Bash/systemd deployment.

**Spec:** `docs/superpowers/specs/2026-09-11-recruiter-na-v5.0.1-latency-grounding-hotfix-design.md`

## Global Constraints

- No numeric candidate score may re-enter the active v5 path.
- UNKNOWN never becomes an automatic rejection.
- Ungrounded evidence may not support a negative or positive definitive decision.
- Do not modify unrelated PulseHR or other `/opt` projects in deployment scripts.
- Existing MySQL and hiring-request SQLite data are preserved.

---

### Task 1: Grounding canonicalization and fail-safe repair

**Files:**
- Modify: `runtime/rn/evidence_grounding.py`
- Modify: `runtime/rn/screening_v5.py`
- Test: `tests/test_grounding_repair_v501.py`

- [x] Write failing tests for wrong source-path repair and ungrounded definitive-result downgrade.
- [x] Run the focused tests and verify RED.
- [x] Implement canonical path resolution and deterministic downgrade repair.
- [x] Re-run focused tests and existing grounding/decision tests.

### Task 2: Bounded decision LLM runtime

**Files:**
- Modify: `runtime/rn/decision_evidence_evaluator.py`
- Test: `tests/test_decision_latency_v501.py`
- Modify: `.env.example`
- Modify: `deploy/recruiter-na.env.example`

- [x] Write failing tests for 15s decision timeout, one request attempt, two-model maximum, and flash-fallback preference.
- [x] Run focused tests and verify RED.
- [x] Implement decision-specific request/model policy without changing intake/profile matching defaults.
- [x] Re-run focused and evaluator tests.

### Task 3: Concurrency and latency observability

**Files:**
- Modify: `runtime/rn/screening_v5.py`
- Test: `tests/test_screening_v5.py`

- [x] Write failing concurrency-barrier and timing-trace tests.
- [x] Run focused tests and verify RED.
- [x] Add per-candidate and aggregate timing traces.
- [x] Re-run screening tests.

### Task 4: Release wiring and safe upgrade

**Files:**
- Create: `deploy/upgrade-v5.0.1.sh`
- Modify: `VERSION`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `USAGE.md`
- Modify: `AGENTS.md`
- Modify: `im/recruiter-na.mjs`
- Modify: release wiring tests
- Create: `docs/V5_0_1_PERFORMANCE_HOTFIX.md`

- [x] Write/update release wiring tests for exact 5.0.1 identity and safe env migration.
- [x] Verify RED.
- [x] Implement release metadata, docs, and upgrade script.
- [x] Verify release wiring tests and shell syntax.

### Task 5: Full verification and packaging

- [x] Run `bash scripts/verify.sh` and require `VERIFY_PASS`.
- [x] Build a clean `recruiter-na-codexharness-v5.0.1.zip` excluding runtime caches/local DB/secrets.
- [x] Extract the final ZIP into a fresh directory and run the same verification again.
- [x] Compute SHA-256 and write `docs/V5_0_1_VERIFICATION_REPORT.md`.
