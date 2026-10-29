# Recommendation resume downloads implementation plan

**Goal:** Add an authenticated original-resume download link after every recommended candidate, including subsequent batches.
**Architecture:** Opaque expiring links identify server-side tickets. DingTalk OAuth verifies corporate staff identity; the download server rechecks live owner-scoped requests and shown candidates, then reuses source-root validation. Link issuance never selects candidates or changes decisions.
**Stack:** Python standard-library HTTP/SQLite, requests, existing workflow/MySQL repository, dedicated loopback service behind the Recruiter-Na HTTPS route.
**Spec:** `docs/superpowers/specs/2026-10-29-recommendation-resume-download-design.md` (user approved).
**Execution:** Inline in the existing isolated worktree; do not disturb dirty primary checkout. Keep real data out of source/tests/releases.

## Review focus

Corporate links must reject anonymous users and other enterprises; optional owner mode rejects another owner; deleted requests/replaced snapshots revoke access. OAuth state must be browser-bound, expiring and single-use. Missing or changed files cannot produce misleading downloads. Session cookies and logs must not disclose identity secrets or links. Concurrent download requests must not race SQLite state consumption.

## Task 1 — Links and authorization

Files: `runtime/rn/resume_delivery.py`, new `runtime/rn/resume_downloads.py`, `tests/test_resume_downloads.py`.
- [x] Write failing tests for ticket expiry, wrong owner, unshown candidate, deletion, changed source and source-root restrictions.
- [x] Add `prepare_recommended(request, candidate_id)` without changing selected delivery semantics; `ResumeDownloadService.url_for(request, candidate_id)` and `download(ticket, authenticated_owner)` recheck authoritative repository state.
- [x] Verify security regressions and existing delivery tests.

## Task 2 — Identity and HTTP

Files: new `runtime/rn/resume_download_http.py`, `tests/test_resume_download_http.py`, dedicated systemd/NGINX examples.
- [x] Write HTTP/OAuth contract tests: login redirect, browser-bound single-use state, sessions, denial, attachment headers, safe errors.
- [x] Implement enterprise verification using OAuth openid corpid and the server token response corpId. No directory permission or trusted identity query/header. Bind the fixed enterprise to trusted Stream metadata and app identity.
- [x] Serve attachment bytes only after authentication and current permission checks. Bind listener to loopback and disable sensitive request logging.

## Task 3 — All recommendation replies

Files: `runtime/rn/hiring_workflow_v5.py`, `runtime/rn/host_tool.py`, readable-tag/workflow tests.
- [x] Write failing three-person layout test and workflow next-batch test; verify correct per-person URLs separated by blank lines.
- [x] Centralize candidate reply rendering through a workflow helper that obtains links from the service; preserve unavailable-source fallback and decision/order.
- [x] Integrate service construction only when configured; extend public CI tests.

## Task 4 — Deployment and acceptance

- [ ] Run public contracts, full baseline comparison, IM, syntax and secret scans; record actual results.
- [ ] Verify DingTalk callback configuration, TLS, loopback service, negative access tests and unchanged PulseHR services before enabling links.
- [ ] Refresh the existing recommendation display without rerunning evaluation or advancing its batch. Use desktop to click each of the three real links and verify the original file mapping. Do not select/contact anyone.
- [ ] Publish a new version if deployed; never retag v0.2.3. Preserve credentials, real MySQL, request state and rollback. Report any actual external authorization blocker explicitly.

## Verified implementation status

2026-10-29: public Python contracts 160 passed; IM 13 passed; full local suite 568 passed, 8 pre-existing private-asset/OCR failures, 10 skipped (same failure names as docs/reviews/pr6-compatibility.md). Compile, secret scan and diff checks passed. Independent read-only review found no important defects, including corporate authorization. These are synthetic contract results, not actual enterprise OAuth/download acceptance.

User revised download policy: all authenticated users of the configured enterprise with a valid recommendation link may download. No qyapi_get_member scope is required. Website OAuth callback remains a separate prerequisite. Deployment and release remain pending actual callback and real-file acceptance.
