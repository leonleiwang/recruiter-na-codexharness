# Recruiter-Na v5 Candidate Decision Contract

## 1. Purpose

v5.0.1 removes the active numerical candidate-scoring contract and integrates the recruiter-authored `Yushilong_allprocess_v2.3` business judgment model for the first supported role: **检验技术员**.

The v2.3 package is treated as recruiter expertise, not as the hiring-manager UI. Recruiter-Na owns the product adapter: hiring managers describe the role and request-specific delta in natural language; internal fields such as `decision_mode` and `evidence_hints` remain hidden.

## 2. Product flow

```text
Hiring manager request
  -> semantic standard-profile match
  -> reduced intake when matched / full intake when unmatched
  -> request-specific JD
  -> user confirms 开始筛选
  -> role-policy scope gate
  -> resume facts
  -> LLM decision evidence
  -> evidence grounding
  -> deterministic Candidate Decision
  -> recommendation-order snapshot
  -> Top 3 / 换一批 / 选择 / WAITING_HR_SOURCING
```

The existing v4 hiring-request ownership boundary and batch state machine are retained.

## 3. Supported decision scope

v5.0.1 Candidate Decision is enabled only for:

- `PROFILE-91603AA55F6E` — 检验技术员（应届）
- `PROFILE-412167F2937C` — 检验技术员（社招）
- exact normalized title `检验技术员`, `检验技术员（应届）`, or `检验技术员（社招）`

Microbiology, NGS and other specialized roles do not inherit this policy merely because their title contains “检验技术员”. Unsupported roles may still create a request and JD, but candidate screening returns `SCREENING_POLICY_UNSUPPORTED_ROLE`; there is no legacy-score fallback.

## 4. Business-policy authority

Canonical integration policy:

`knowledge/recruiting/policies/inspection_technician_v1.json`

Provenance:

- Recruiter business baseline: `Yushilong_allprocess_v2.3`
- v2.3 source ZIP SHA-256: `9189bb0428742ce2492b9b9403e99c4b4879a35e0f5fef063f0698548f564245`
- v4.0.0 source project ZIP SHA-256: `e79be874016b023dcb9fb795425134349a342e3cc0680f54c10e3fc7bb857921`
- Accepted for integration: 2026-09-11

The verified 14-profile repository remains a separate authority for profile matching. The Decision Policy does not replace those source PDFs.

## 5. Actual Hiring Criteria adapter

`criteria_adapter_v5.py` merges the accepted policy with the request delta.

Hiring-manager deltas such as:

> 必须做过罗氏免疫平台

are translated internally into an additional `hard_gate` criterion. The manager is never asked to select `hard_gate`, `resume_evidence`, `interview_verify`, or `preference` directly.

## 6. LLM contract

The LLM may only classify facts and return grounded evidence.

Hard checks:

- education: `matched | not_matched | unknown`
- certification: `valid | pending_exam | expired | none | unknown`
- location: `match | willing | unknown | refused`
- red line: `hit | clear | unknown`

Each criterion has two independent axes:

- `criterion_result = satisfied | not_satisfied | unknown`
- `evidence_status = confirmed | partial | none`

`not_satisfied` means the resume contains evidence that the criterion is not met. It is not a synonym for weak/missing evidence. Missing information is `unknown`.

The LLM is forbidden to produce a numerical candidate score or the final pass/fail decision.

## 7. Grounding contract

Definitive positive or negative claims must quote evidence that can be grounded in normalized resume facts. A fabricated or ungrounded negative fact becomes a **technical evaluation failure**, never an automatic candidate rejection.

## 8. Deterministic decision table

Eligibility checks:

| Item | Explicit fail | Unknown |
|---|---|---|
| Education | `not_matched` -> FAIL | verify |
| Certification | `expired` / `none` -> FAIL | verify |
| Location | `refused` -> FAIL | verify |
| Red line | `hit` -> FAIL | verify |

Criteria:

| decision_mode | satisfied | not_satisfied | unknown |
|---|---|---|---|
| `hard_gate` | pass | veto | verify |
| `resume_evidence` | pass | verify | verify |
| `interview_verify` | pass | interview focus | interview focus |
| `preference` | preference signal | no effect | no effect |

Top-level `decision=fail` is allowed only by an explicit eligibility failure or `hard_gate + not_satisfied` after grounding.

## 9. Recommendation order

There is no formal talent score. Only `decision=pass` candidates enter the recommendation list.

Stable internal order:

1. no verification needed before verification needed;
2. more confirmed preference signals;
3. more confirmed resume-evidence criteria;
4. fewer verification items;
5. original candidate-pool order.

This is a **view/recommendation order**, not an objective ranking or percentage score.

## 10. v4 -> v5 persistence compatibility

The SQLite `ranking_json` column is retained to avoid a destructive schema migration, but v5 stores recommendation-order entries in it.

On repository open, v5 detects an in-progress v4 candidate snapshot that contains old score rows. For requests in `CANDIDATES_READY` / `REVIEWING_CANDIDATES` (including paused requests whose previous state was candidate review), v5:

- preserves request details and JD;
- clears the incompatible candidate snapshot and old seen list;
- resets to `JD_READY`;
- records `V5_LEGACY_RECOMMENDATION_INVALIDATED`;
- requires a fresh “开始筛选” under Candidate Decision.

`SELECTED` history is preserved unchanged.

## 11. Trace artifacts

Each v5 screening run writes:

- `data_sources.json`
- `request_snapshot.json`
- `decision_policy.json`
- `actual_criteria.json`
- per candidate: `resume_facts.json`, `decision_evidence.json`, `decision_evidence_validation.json`, `candidate_decision.json`
- `decision_snapshot.json`

Technical evaluator/grounding failures are isolated per candidate and do not become business FAIL decisions.
