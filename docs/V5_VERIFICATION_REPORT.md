# Recruiter-Na v5.0.0 Verification Report

Date: 2026-09-11

## Release inputs

- Project source ZIP: `recruiter-na-codexharness(2).zip`
  - SHA-256: `e79be874016b023dcb9fb795425134349a342e3cc0680f54c10e3fc7bb857921`
- Recruiter business baseline: `Yushilong_allprocess_v2.3.zip`
  - SHA-256: `9189bb0428742ce2492b9b9403e99c4b4879a35e0f5fef063f0698548f564245`
- Release version: `5.0.0`
- The supplied project archive had no `.git` metadata; verification is artifact-based rather than commit-based.

## Source-tree release verification

Command:

```bash
bash scripts/verify.sh
```

Result: `VERIFY_PASS`

Observed gates:

- Data foundation: `DATA_FOUNDATION_PASS`
  - 14 verified profile PDFs
  - 45 verified JD entries
  - production resume authority remains MySQL
- Python tests: **112 passed, 0 failed**
- Python syntax compile: PASS
- codexharness-im: **21 passed, 0 failed** + Node syntax check PASS
- Recruiter IM: **4 passed, 0 failed** + bridge/router syntax PASS
- Product scope / owner boundary: PASS
- v5 active path / no-formal-score guard: PASS
- Obsolete active path scan: PASS
- deployment/shell syntax: PASS
- secret scan: `SECRET_SCAN_CLEAN`

## v5-focused acceptance coverage

Automated tests cover:

- policy scope does not leak to microbiology/NGS roles;
- hiring-manager request delta becomes internal criteria without exposing `decision_mode`;
- `criterion_result` is independent from `evidence_status`;
- explicit negative claims require evidence and grounding;
- UNKNOWN education/certification/location never auto-rejects;
- only explicit hard-check failure / grounded hard-gate not-satisfied can veto;
- preference/interview signals do not become numeric scores;
- full candidate pool is evaluated and technical failures are isolated;
- candidate cards contain no RQ/JM/CS score display;
- Top 3 / next batch / no-repeat / selection are retained;
- unsupported role screening never falls back to the old scorer;
- owner-scoped requests remain isolated;
- in-progress v4 score snapshots are invalidated to `JD_READY` on v5 repository open;
- selected historical requests are preserved;
- v5 recommendation snapshots survive repository reopen;
- DingTalk bridge, router, systemd metadata and install scripts identify v5;
- upgrade script migrates the worker-concurrency setting name.

## Persistence migration behavior

No destructive SQL schema migration is used. The old `ranking_json` column is retained as a storage column for v5 recommendation-order entries.

When v5 detects an in-progress v4 score snapshot in candidate-review state, it clears that incompatible snapshot and resets the request to `JD_READY` so a fresh Candidate Decision run is required. `SELECTED` history is not rewritten.

## Packaging hygiene

The release ZIP excludes:

- `.venv/`
- `.pytest_cache/`
- Python `__pycache__/` / `*.pyc`
- `.DS_Store`
- runtime `workspace/runs`, reports, logs and local request SQLite files
- `data/source/resumes/private/`
- real `.env` files

The vendored IM `node_modules` is retained so the release can run the same offline Node verification before production copy; the deploy script still excludes it from the app copy and installs production dependencies normally.

## Final package re-verification

A release ZIP was built from a clean staging tree, extracted into a new directory, and verified using:

```bash
bash scripts/verify.sh
```

Clean-extract result: **`VERIFY_PASS`**

- Python: **112 passed, 0 failed**
- codexharness-im: **21 passed, 0 failed**
- Recruiter IM: **4 passed, 0 failed**
- Data foundation: PASS
- v5 no-formal-score active-path guard: PASS
- deployment syntax: PASS
- secret scan: CLEAN

After this report/documentation-only update, the release is rebuilt and the same clean-extract verification is run once more against the final bytes before delivery.
