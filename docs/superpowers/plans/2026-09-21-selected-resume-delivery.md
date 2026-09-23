# Selected Resume Delivery Implementation Plan

> Execute in order. Preserve the existing uncommitted intake-loop repair; do
> not reset, clean, stash, or rewrite unrelated workspace state.

## Files and Responsibilities

- `runtime/rn/candidate_decision_v5.py`: derive matched/applicable tag counts
  from grounded decision data without affecting ordering.
- `runtime/rn/screening_v5.py`: persist those counts on recommendation rows.
- `runtime/rn/hiring_workflow_v5.py`: recognize natural ordinals and return a
  safe post-selection delivery request.
- `runtime/rn/resume_delivery.py` (new): resolve selected candidate originals,
  enforce configured source roots/type/size limits, and prepare opaque delivery
  data without exposing a path to manager-facing text.
- `runtime/rn/hiring_requests.py`: store owner-scoped, idempotent delivery
  audit events and delivery status.
- `runtime/rn/host_tool.py`: add trusted bridge-only commands for delivery
  preparation and result acknowledgement.
- `im/recruiter-na.mjs`: process a successful selection's descriptor and send
  its file before finalizing the card/reply.
- `vendor/codexharness-im/src/adapters/dingtalk-stream-bot.mjs`: upload media
  and post a session-webhook DingTalk file message.
- Python and Node tests: reproduce all boundaries with fake repositories and
  fake DingTalk HTTP responses.

## Task 1: Grounded matched-tag counts

1. Add a failing `tests/test_candidate_decision_v5.py` test with a hand-built
   assessment: seven user-visible labels, six `satisfied + confirmed`, and one
   unknown. Assert `matched_tag_count == 6`, `applicable_tag_count == 7`, and
   that the decision/order are otherwise unchanged.
2. Run that one test; confirm it fails because the fields do not exist.
3. Add a small pure helper in `candidate_decision_v5.py` that counts the three
   visible hard-filter labels, red-line labels, and policy criteria according
   to their grounded assessment. Deduplicate only by stable label ID/name.
4. Include the two count fields in the decision object, then copy them into
   v5 recommendation snapshots in `screening_v5.py`.
5. Update `_render_candidates()` to show `符合标签 X/Y`; retain individual
   verification/interview text and never show a score or decision mode.
6. Run the focused decision, screening, and workflow tests.

## Task 2: Natural selection and safe original-file preparation

1. Add failing workflow tests for `第一个`, `选第一个`, and `选第1个`; each
   must select the current displayed first candidate, while cross-owner and
   non-recommended candidates remain rejected.
2. Add failing unit tests for `SelectedResumeDeliveryService` with a fake
   `ResumeRepository`: valid source returns an internal opaque payload; missing
   file, symlink escape, unsupported extension, oversize file, and missing
   original reference all fail with stable safe codes.
3. Implement ordinal parsing without weakening existing name/number selection.
4. Implement the delivery service using candidate ID lookup only. Resolve its
   `source_file_path` against configured canonical roots and read no bytes
   during preparation. Return an internal descriptor with request/candidate
   identifiers, sanitized filename, SHA-256 fingerprint, and an opaque local
   file handle/path for the trusted bridge only.
5. Extend the SQLite repository with delivery-attempt/success event helpers.
   Use `(request_id, candidate_id, fingerprint)` as idempotency key and never
   store a source path or payload bytes.
6. Have successful selection return a descriptor only after owner-scoped
   selection succeeds. A file-preparation failure returns a safe message while
   retaining `SELECTED`.
7. Run focused Python tests and `git diff --check`.

## Task 3: DingTalk direct file adapter and bridge

1. Add failing Node tests with a fake `fetch`: media upload uses multipart
   body and the session webhook receives exactly `{msgtype:'file', ...}` only
   after upload success. Assert upload/send HTTP errors reject without logging
   media bytes or a path.
2. Implement adapter media upload separately from JSON `openApi`; use a
   bounded stream/file object and the current app access token. Implement
   session-webhook file delivery using DingTalk's returned media ID.
3. Add a bridge test where a workflow result contains a descriptor: it asks the
   trusted host command for prepared delivery material, sends the file, then
   acknowledges success. A failed direct delivery finalizes the existing
   selection with a safe retry message and no URL fallback.
4. Ensure final card/text says “已选中并已发送简历文件” only after DingTalk
   confirms the file message; selection alone never makes that claim.
5. Run `npm test` and `npm run check` from `vendor/codexharness-im`.

## Task 4: Full verification and controlled rollout readiness

1. Run the complete Python suite with `PYTHONPATH=runtime .venv/bin/pytest -q`.
2. Run Node adapter tests/checks and secret scan; inspect the diff for paths,
   media tokens, credentials, or PII leakage.
3. Confirm the production service's protected co-resident services are active
   before any deployment. Transfer only reviewed files, verify hashes, and
   restart only `recruiter-na.service`.
4. With a pre-approved controlled resume and manager, prove upload plus file
   receipt. Record sanitized delivery evidence and service state. If the
   direct-file call fails, stop before adding another delivery channel and
   report the returned DingTalk failure evidence.
