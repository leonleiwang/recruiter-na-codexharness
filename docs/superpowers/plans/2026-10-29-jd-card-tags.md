# JD tag card integration

User-approved scope: card labels come directly from the existing resume_screening JD rule engine; v5 retains eligibility, final decisions, recommendation ordering, verification and interview focus. Fix both reviewed presentation defects and merge PR #4 with its original author commit intact.

- [x] Add regression tests for eligible/unregistered certificates, pending exams, willing locations, JD-derived labels, differing rule/v5 outcomes and empty JD labels.
- [x] Add a narrow adapter using requirements_from_jd, field/tag extraction and match_requirements. Export only tag counts/names for cards; keep an auditable JD matching artifact.
- [x] Generate the pre-screening tag list through the same JD parser. Keep v5 decision fields independent of display fields; no auxiliary recommendation/eligibility output enters decisions.
- [x] Run targeted and complete Python suites, compilation, secret scan and relevant IM checks. Identify environment-only failures explicitly.
- [ ] Append changes to the existing PR branch; verify exact head, mergeability and author ancestry, then merge while preserving history. No deployment or external messages.

## Verification

- New regressions were observed failing before implementation. Targeted suites: 149 passed.
- Full Python suite with locally available verified libraries: 511 passed, 5 failed, 6 skipped. Exact original PR head with the same assets: 501 passed, the same 5 failed, 6 skipped.
- Pre-existing failures: missing `data/dev/resume_db.fixture.json`; three profile/policy consistency cases (freshgrad `专业知识` naming and social `试剂耗材管理能力` drift); no available OCR engine for a disguised PNG. No policy or source asset was changed to bypass these failures.
- IM tests: 12 passed. Compilation, secret scan, and diff whitespace checks passed.
- All new card labels are JD rule results; the internal v5 sort fields remain separate. Empty JD tags do not fall back to policy counts.
- Historical recommendation snapshots retain their saved display counts until re-screened. No deployment or production data access.
