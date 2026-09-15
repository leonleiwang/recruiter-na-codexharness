# Recruiter-Na v5.0.1 Verification Report

Date: 2026-09-11

## Scope

Production hotfix for the v5 Candidate Decision path after the first Tencent Cloud smoke test showed ~3/6/9 minute candidate completion times and three `DECISION_EVIDENCE_GROUNDING_FAILED` results.

## Release-tree verification

Command:

```bash
bash scripts/verify.sh
```

Result before packaging:

- Data foundation audit: PASS
- Python tests: **125 passed / 0 failed**
- Python compile: PASS
- Codex Harness IM tests: **21 passed / 0 failed**
- Recruiter IM tests: **4 passed / 0 failed**
- Product scope / owner boundary: PASS
- Active v5 no-formal-score guard: PASS
- Obsolete-path scan: PASS
- Deployment shell syntax: PASS
- Secret scan: CLEAN
- Final result: **VERIFY_PASS**

## Hotfix-specific regression coverage

- exact resume quote + wrong `source_path` → canonical source path, still grounded;
- ungrounded definitive criterion → `unknown` + `evidence_status=none`, never candidate rejection;
- ungrounded negative hard check → `unknown`, never rejection;
- production regression: bad criterion quote no longer creates `technical_failure`; candidate becomes PASS + needs verification;
- Candidate Decision defaults are independent of global 60s LLM timeout: 15s / 1 attempt / max 2 models;
- current production-style fallback list prefers `deepseek-v4-flash` over slower non-flash fallbacks;
- three candidate workers are proven concurrently in flight with a barrier regression test;
- per-candidate and aggregate latency traces are emitted.

## Deployment safety

`deploy/upgrade-v5.0.1.sh` is scoped to `/opt/recruiter-na`. Static release tests assert that it does not name or touch PulseHR project paths. It backs up the Recruiter-Na app/env and preserves MySQL configuration, request SQLite, Codex Home and workspace run/report data.

## Clean-extract verification

The packaged ZIP was extracted into a fresh directory and the same verification command was run again.

- Data foundation audit: PASS
- Python tests: **125 passed / 0 failed**
- Codex Harness IM tests: **21 passed / 0 failed**
- Recruiter IM tests: **4 passed / 0 failed**
- Secret scan: CLEAN
- Final result: **VERIFY_PASS**

The release is therefore verified both from the working release tree and from a clean extraction of the packaged artifact.
