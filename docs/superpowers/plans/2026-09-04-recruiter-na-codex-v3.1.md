# Recruiter-Na Codex v3.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a deployable Codex-based Recruiter-Na with evidence-grounded semantic scoring and no active v2.2 fallback.

**Architecture:** A hard-coded pipeline orchestrates semantic evaluation before deterministic scoring. Codex is the outer agent runtime; the evaluator calls the configured DeepSeek/Qwen Responses-compatible API directly so the scoring contract cannot be skipped by agent behavior.

**Tech Stack:** Python 3.10+, requests, pytest, PyMySQL, pypdf/python-docx, Node 20+, Codex CLI 0.153.2, codexharness-im 0.1.0.

**Spec:** `docs/superpowers/specs/2026-09-04-recruiter-na-codex-v3.1-design.md`

## Global Constraints
- No active import or call of `scoring_v22` or `_match_tag`.
- LLM failures never fall back to lexical scoring.
- Evidence quotes must ground to resume content before scoring.
- Only explicit hard-gate FAIL vetoes; UNKNOWN never vetoes.
- No hard-coded credentials.

---

### Task 1: Semantic contracts and anti-regression tests
- [ ] Add failing tests for v3 contract conversion, grounding, evaluator failure behavior, pipeline state/trace, and v2.2 absence.
- [ ] Run tests and verify RED for missing v3 runtime modules.
- [ ] Implement minimum modules to make tests GREEN.

### Task 2: Integrate v3 deterministic engine and CLI
- [ ] Promote the v3 scorer to `runtime/rn/scoring_engine_v3.py` as the only canonical scorer.
- [ ] Replace CLI `match` and `rq` paths with v3 pipeline/RQ.
- [ ] Remove v2.2 scorer/tests/reference from active release.

### Task 3: Migrate skills and persona to Codex
- [ ] Add root `AGENTS.md` with v3.1 authority boundaries.
- [ ] Convert/update all Skill metadata and all v2.2 wording to v3.1 semantics.
- [ ] Add IM bridge entrypoint using vendored codexharness-im.

### Task 4: Secrets and deployment
- [ ] Remove database credential defaults and read only environment settings.
- [ ] Add `.env.example`, requirements, local install/verify scripts, Codex smoke test, systemd unit, and cloud installer pinned to Codex 0.153.2.

### Task 5: Release verification
- [ ] Run Python tests, syntax compile, Node tests/checks, banned-pattern scan, secret scan, and release ZIP re-extraction verification.
- [ ] Produce SHA256.
