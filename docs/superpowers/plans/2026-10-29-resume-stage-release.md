# v0.2.1 implementation and release plan

User scope: resume-stage denominators reflect achievable pre-interview labels, JD rules keep matching raw text, v5 retains decisions. Interview focus becomes question direction (label). Chinese-default docs and engineering files follow local Huoshui conventions. Publish a release branch/tag/Release and deploy only the personal Tencent Recruiter-Na test instance.

- [x] Confirm PR #4 merge and inspect current runtime, reference docs, data/config boundaries.
- [x] Observe failing stage/format regressions, implement JD stage projection and interview direction rendering without editing policy.
- [x] Prepare Chinese/default and English docs, release notes, changelog and text/privacy conventions.
- [ ] Run focused/full verification and independent read-only review. Compare any environmental failures to baseline.
- [ ] Publish release branch, tag and source-only GitHub Release with exact checksums.
- [ ] Deploy isolated release with verified file manifest, retained request state and rollback; confirm Stream startup and unchanged protected services.
