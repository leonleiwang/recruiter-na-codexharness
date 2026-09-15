# Changelog

## 0.1.0 — Public Source Release

- Published the Recruiter-Na v5.0.2 source, safe example configuration, deployment scripts, and verification suite under Apache-2.0.
- Excluded credentials, tenant configuration, runtime state, candidate resumes, internal position/JD libraries, request databases, historical internal materials, and the separately sourced `codexharness-im` adapter.
- Documented the public product boundary: owner-scoped hiring requests and approved-policy Candidate Decision only; no public HR-admin data browsing or numerical candidate scoring.

## 5.0.2 — Candidate Recall & Qwen Decision Routing Hotfix

- Fixed inspection-technician candidate recall for Chinese resumes that contain `医学检验` / `检验科` / `检验` but not the exact title `检验技术员`; recall aliases are now part of the internal Policy rather than ingestion-specific raw JSON.
- Candidate Decision now prefers the approved interactive chain `qwen3.6-flash → qwen3.7-max` when no explicit decision model is configured.
- Added v5.0.2 env migration that replaces the v5.0.1 auto-generated DeepSeek decision primary with Qwen only when those Qwen models are already present in the operator-approved legacy fallback list; explicit custom decision models remain untouched.
- Kept the 15s/model, one-attempt, two-model interactive budget; no default one-minute sleep is added for generic read timeouts.

## 5.0.1 — Candidate Decision Latency & Grounding Hotfix

- Fixed production grounding false failures: an exact resume quote with a wrong model-authored `source_path` is now canonicalized to the authoritative path.
- Added fail-safe grounding repair: ungrounded definitive evidence is downgraded to `UNKNOWN`/`none`; it can never support rejection or preference, and a single bad quote no longer crashes the whole candidate evaluation.
- Added decision-specific interactive LLM controls: 15s timeout, 1 request attempt per model, and at most 2 models by default.
- When no dedicated decision fallback chain is configured, existing `flash`/`fast` fallback models are preferred for the Candidate Decision path.
- Added per-candidate `timing.json` plus aggregate screening performance metadata and a regression test proving candidate workers execute concurrently.
- Added `upgrade-v5.0.1.sh`; it preserves global LLM configuration and only appends missing decision-specific latency settings.

## 5.0.0 — Candidate Decision Migration

- Replaced the active RQ/JM/CS candidate-scoring path with evidence-grounded Candidate Decision.
- Accepted recruiter workflow v2.3 as the business Decision Policy baseline for the inspection-technician MVP.
- Added separate `criterion_result` and `evidence_status` contracts; UNKNOWN never becomes an automatic rejection.
- Added deterministic hard-check / decision-mode rules and grounded veto requirements.
- Added `criteria_adapter_v5.py`, `decision_evidence_evaluator.py`, `candidate_decision_v5.py`, `screening_v5.py`, and `hiring_workflow_v5.py`.
- Preserved v4 hiring-manager intake, request-specific JD, owner isolation, Top 3 / next batch, selection, pause/resume and HR-sourcing continuation.
- Kept the existing `ranking_json` database column for migration safety; v5 stores recommendation order, not a numeric candidate score.
- Added explicit role-policy scope gate: unsupported roles never fall back to the legacy scorer.
- Moved scoring v3/v4 modules to legacy/non-active status without deleting them from the source bundle.

## 4.0.0 — Hiring Manager Recruitment Workflow

- Repositioned Recruiter-Na as a DingTalk digital recruiter for hiring managers, not an HR/admin database bot.
- Public capabilities reduced to: start/continue own hiring requests; view own hiring progress.
- Added owner-scoped SQLite Recruitment Request store keyed by normalized DingTalk senderId.
- Added semantic-only standard-profile matching. No keyword fallback may decide profile membership.
- Matched profiles reduce intake fields; unmatched roles collect complete hiring information.
- Added request-specific JD generation; related JD templates are wording references only.
- Added v4 dual semantic scoring: ProfileMatch 40% + JDMatch 60%, or JDMatch 100% without profile; outer RQ 30% + JM 70% retained.
- Fixed old top-3 logic: v4 scores the retrieved candidate pool first, persists one ranking snapshot, and serves non-repeating batches of three.
- Added candidate selection, pause/resume, WAITING_HR_SOURCING, and new-resume continuation semantics.
- Removed profile/JD/resume full-library browsing from the hiring-manager product path.
- Archived v3.x public/admin-oriented Skills outside `.agents/skills`; only v4 hiring-request and my-hiring-progress remain discoverable.
