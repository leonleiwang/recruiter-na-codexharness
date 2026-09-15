# Recruiter-Na v5.0.2 Verification Report

Release verification executed from the release tree on 2026-09-11.

- Data foundation audit: PASS
- Python tests: 127 passed / 0 failed
- Python syntax: PASS
- Codex Harness IM tests: 21 passed / 0 failed
- Recruiter IM tests: 4 passed / 0 failed
- Product scope / owner-boundary scan: PASS
- v5 active path / no-formal-score guard: PASS
- Obsolete active path scan: PASS
- Deployment shell syntax: PASS
- Secret scan: CLEAN
- Overall: VERIFY_PASS

New regression coverage includes:

- inspection-technician recall aliases recover MinerU-style resumes that do not literally contain `检验技术员`
- Candidate Decision selects `qwen3.6-flash -> qwen3.7-max` when no explicit decision model is configured and both are in the approved fallback list
- explicit decision-model configuration still overrides automatic routing
- v5.0.2 deployment remains scoped to Recruiter-Na and does not name PulseHR project paths
