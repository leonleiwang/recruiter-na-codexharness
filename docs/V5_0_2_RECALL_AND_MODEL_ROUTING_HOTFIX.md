# Recruiter-Na v5.0.2 Recall & Model Routing Hotfix

## Production symptoms

1. The inspection-technician candidate pool contained only resumes whose JSON/text literally included `检验技术员`. MinerU-ingested resumes such as `医学检验`, `检验科`, or `医学检验技术` were filtered out by the MySQL lexical post-filter.
2. Candidate Decision calls still timed out on the DeepSeek route. v5.0.1 bounded the timeout but did not guarantee that the interactive path selected the approved Qwen models already present in the operator fallback list.

## Fix

### Candidate recall

The accepted inspection-technician Policy now declares internal retrieval aliases:

- `检验技术员`
- `医学检验`
- `检验科`
- `检验`

`HiringDecisionScreeningService._pool()` queries these aliases in stable order, deduplicates by candidate id, and stops at `RECRUITER_SCREEN_POOL_LIMIT`. This keeps ingestion-format differences out of business logic: Alibaba and MinerU rows can be recalled from their actual resume text without injecting a fake job title into `raw_json`.

### Interactive model routing

When there is no explicit `RECRUITER_DECISION_MODEL`, Candidate Decision prefers models already present in the approved legacy fallback list in this order:

1. `qwen3.6-flash`
2. `qwen3.7-max`

An explicit custom decision model still wins.

The v5.0.2 upgrade script additionally handles the common v5.0.1 production state where the upgrade auto-generated `RECRUITER_DECISION_MODEL=deepseek-v4-flash`: if both Qwen models are already present in `RECRUITER_LLM_FALLBACK_MODELS`, the script migrates only the Candidate Decision route to `qwen3.6-flash` with `qwen3.7-max` fallback. Global LLM settings are not changed.

The interactive budget remains:

- 15 seconds per model
- one attempt per model
- at most two models

No unconditional 60-second sleep is added for generic read timeouts. A read timeout is not proof of TPM throttling, and sleeping in the synchronous DingTalk request would reintroduce minute-level latency.

## Non-goals

v5.0.2 does not change:

- Candidate Decision Policy semantics
- PASS / VERIFY / FAIL rules
- database schema
- resume ingestion
- PulseHR projects
- Top 3 / next-batch state machine
