# Changelog

## Unreleased — DingTalk Reply Routing & Resume Delivery Fixes

- Candidate Decision evidence is now evaluated twice per resume (`RECRUITER_DECISION_CONSISTENCY=strict`, the default) and every criterion whose two conclusions disagree falls back to `unknown`/待核实 instead of being decided by sampling noise. Measured on the 112-resume pool with the same policy: decisions that flipped between two identical runs dropped from 7/106 to 1/106, identical matched-tag counts rose from 80% to 88%, and the Top-3 overlap went from 1/3 to 2/3, at the cost of ~2.4x runtime per pool run (60s to 148s). `RECRUITER_DECISION_CONSISTENCY=off` restores single-pass evaluation.
- Each merged evaluation records a `consistency` block with the disagreement list, and a failed second pass logs `[decision] consistency_second_pass_failed code=...` while keeping the first result.
- Candidate criteria now follow the verified profiles instead of one social-hire-flavored set. The policy gained the 2026.09.20 profile items (`试剂耗材管理能力`, `专业理论知识`, `专业背景与项目经验`, `持续学习`), criterion ids are declared in the policy itself (C01-C12 keep their meaning, new items are C13-C16), and `profile_criterion_overrides` may mark an item `applicable: false`, so each profile is evaluated only against its own items and the tag denominator matches it: 社招 13 criteria / 16 tags, 应届 11 criteria / 14 tags. Previously a 应届 candidate was measured against 社招 abilities such as 试剂耗材管理能力, and both profiles reported the same denominator.
- Each screening run records the skipped items in `actual_criteria.json` (`profile_criterion_exclusions`), and `rn policy` reports `criteria_by_profile`. `tests/test_policy_profile_coverage.py` fails whenever a profile item has no matching policy criterion, so the next profile update cannot silently drift again.
- Added `scripts/update-profile-from-docx.py` to rebuild one profile entry from a 岗位画像 DOCX table.
- Stopped showing trait items (抗压 / 细心 / 责任心 / 积极主动 / 持续学习) as 推荐依据 to the hiring manager: trait conclusions still flow into 面试重点/待核实 but do not justify a recommendation. Recommended pool ordering and matched tag counts are unchanged; on the 112-person pool, 6 people lost the only line of 推荐依据 that came from a trait.

- Fixed the SELECTED takeover: a hiring manager's earlier selected request can no longer intercept every later message. Active requests keep priority, and resume/contact intents only apply when the message actually refers to the selected request. This restores field intake ("紧急") and stops unrelated replies from being read as contact consent.
- Fixed ordinal selection ("选第N个") so it applies to the request that displayed the batch, instead of an older SELECTED request; the selection message is now the first-selection wording.
- Stopped promising the enterprise cloud contact-center (Tencent CCC) call flow: selecting a candidate no longer asks about it, and an explicit contact request now states that the capability is not integrated yet and that no call or recording will be placed. See docs/reports/TENCENT_CCC_CONTACT_IMPLEMENTATION_HANDOFF_20260921.md.
- Resume delivery now fails with an actionable reason (`RESUME_SOURCE_ROOTS_NOT_CONFIGURED`) when `RECRUITER_RESUME_SOURCE_ROOTS` is unset, instead of reporting only "not ready".
- Added host diagnostics for intent-classifier failures and resume-delivery failures; the DingTalk bridge forwards child-process diagnostics to journalctl.
- Documented `RECRUITER_RESUME_SOURCE_ROOTS`, `RECRUITER_RESUME_MAX_BYTES`, `RECRUITER_RESUME_URL_BASE`, `RECRUITER_RESUME_URL_DIR`, `DINGTALK_AI_CARD_TEMPLATE_ID`, `DINGTALK_PROGRESS_CARD`, and `DINGTALK_PROGRESS_THROTTLE_MS`; deployment scripts create `workspace/resume-url` and exclude `*.backup-*` from the release sync.
- Moved stale `runtime/rn/*.backup-*` recovery copies out of the deploy path and refreshed the stale `/new` router expectation in `im/router-v34.test.mjs` (release verification gate).
- Fixed duplicate hiring requests: the intent classifier now receives the pending intake fields (`当前待补字段`), and the prompt boundary states that supplying those field values continues the current draft even when the message reads like a full hiring description. Previously each field answer created a new request.
- Stopped leaking backend errors to hiring managers: a screening backend failure (e.g. missing MySQL configuration) now keeps the request retryable at `JD_READY` with a safe message, while the real reason is logged as `[workflow] screening_failed request=… error=…`; the DingTalk bridge no longer appends `result.error` to the user reply.
- Fixed duplicate DingTalk Stream consumers: starting a second local bridge while a stale instance stayed connected made DingTalk route robot messages to a dead connection, so the hiring manager saw the platform error card (`INTERNAL_UNKNOWN`). `scripts/start-local.sh` now refuses to start when an instance is already running, and `scripts/stop-local.sh` / `stop-local.cmd` stop every instance.
- DingTalk inbound messages are no longer dropped silently: payload parse failures and unnormalizable messages (missing `senderStaffId`/`msgId`) are logged with a field-presence summary. `DINGTALK_STREAM_DEBUG=true` enables the SDK raw frame log for troubleshooting (off by default because frames contain the session webhook).
- Fixed garbled DingTalk replies: the bridge now spawns the Python host with `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`. On Windows Python encodes stdout with the console code page (GBK) while the bridge decodes it as UTF-8, which turned every reply into mojibake. `scripts/start-local.sh`, `deploy/recruiter-na.service` and the env examples set the same variables.
- Fixed empty bodies for markdown DingTalk messages: the adapter now reads `text.content`, `markdown.text` or `content`, logs an empty body instead of failing silently, and the field summary reports markdown presence. Previously such a message fell through to the Codex fallback and failed with `spawn codex ENOENT`.
- Candidate Decision now vetoes a required criterion that the resume explicitly disproves: `level=必须` + `decision_mode=resume_evidence` + `criterion_result=not_satisfied` + `evidence_status=confirmed` fails the candidate (`rule=resume_evidence_confirmed_not_satisfied`). `unknown` and `partial` evidence still only produce verification items. On the labelled regression set this moves a candidate the business rejected from `pass` to `fail` while leaving the two accepted candidates unchanged.
- Candidate Decision is now deterministic: the evidence request sets `temperature=0`. Previously the same resume could yield different criterion verdicts between runs, which decision rules then amplified into wrong rejections.
- Contract violations in the evidence document are repaired instead of aborting the candidate: a definitive result without a verbatim quote is downgraded to `unknown` (hard checks and red lines to `unknown`, criteria to `unknown`/`none`), `none` with evidence becomes `partial`, and out-of-order assessments are re-sorted. Repairs are recorded in `contract_repairs`. Previously one ungrounded quote raised `EvidenceEvaluationError` and the candidate silently disappeared as a technical failure.
- Added `scripts/eval-resumes.py`: a MySQL-free evaluation regression that turns resume PDFs into resume facts, runs the semantic evidence chain plus the deterministic decision, and compares the result with business labels.
- Criteria now follow the matched profile instead of the shared policy alone. The policy supports both 检验技术员（应届）and 检验技术员（社招）but described only the experienced bar (≥1年 inspection experience, level=必须), so a fresh-graduate requisition evaluated interns against it and vetoed every one of them. `inspection_technician_v1.json` gained `profile_criterion_overrides` (fresh graduates: experience is a preference, internships suffice and a candidate who needs training is not vetoed; experienced hires: ≥1年 stays required) and the adapter applies them per matched profile, recording `profile_criterion_overrides_applied` in the criteria contract. `scripts/eval-resumes.py` gained `--profile-id` so a regression can run under either bar.

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
