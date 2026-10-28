# Selected Resume Delivery and Evidence Summary Design

## Goal

When a hiring manager selects a candidate from a recommendation batch using a
documented command or natural ordinal (for example, `第一个`), Recruiter-Na
must: (1) preserve the owner-scoped selection rule, (2) send that selected
candidate's original resume as a DingTalk file to the same conversation, and
(3) record the outcome without returning a server path or a public link.

Candidate recommendation cards will also show the recruiter-requested tag
count in the form `符合标签 X/Y`. `Y` is every applicable label in the active
policy for this request; `X` is the subset with explicit, grounded resume
evidence that satisfies its label. This is not a candidate score and never
changes Candidate Decision eligibility or ordering.

## Scope and Non-goals

- Support only v5's existing `CANDIDATES_READY` / `REVIEWING_CANDIDATES`
  selection flow.
- Do not expose full-library browsing, a file URL, a filesystem path, database
  credentials, a numeric candidate score, or internal decision modes.
- Do not infer a candidate's file from name or raw text. The file must resolve
  through the selected `candidate_id` and `ResumeRepository` record.
- Direct DingTalk file delivery is the first choice. If the media upload or
  file-message call fails, a later controlled URL-delivery implementation may
  be invoked; it must be signed, short-lived, owner-bound, and audited. Until
  that URL implementation exists, retain the selected state and report a
  recoverable delivery failure.

## Data and Authorization

`HiringRequestRepository.select_candidate(owner, request_id, candidate_id)`
remains the selection authority. A delivery may proceed only after the
candidate is in the selecting owner's previously shown IDs. The workflow passes
the selected candidate ID—not a filename or client-provided path—to a
`SelectedResumeDeliveryService`.

The service asks `ResumeRepository` for that record, takes only its stored
original-file reference and safe display filename, and rejects a missing,
non-regular, unreadable, oversize, or unapproved-root file. The production
source record currently contains readable original-file references for all
five resumes; this observation is not a contract that paths will always be
valid.

The permitted source roots and maximum file size are explicit deployment
configuration. Path validation resolves symlinks before comparison. File bytes
are read only for the authorized, selected record and are never persisted in
SQLite, logs, traces, or response text.

## DingTalk Delivery

The Node DingTalk adapter gains a narrow file-delivery capability:

1. The Python workflow returns a `selected_resume_delivery` descriptor after a
   successful selection: request ID, candidate ID, filename, and a one-time
   local delivery token. It never returns the source path to DingTalk text.
2. The bridge resolves the descriptor using the host-side delivery command,
   uploads the validated bytes using the DingTalk media API, then posts a
   `file` message through the originating session webhook.
3. A delivery result is passed back to the Python host to append an
   owner-scoped audit event containing request ID, candidate ID, message ID,
   sanitized filename, outcome, and DingTalk media/message reference. It does
   not store media URLs, tokens, or the local source path.

The initial production proof uses a controlled test resume. The direct-file
method is accepted only if upload and file-message delivery both return a
successful DingTalk response. A platform permission or unsupported-message
failure leaves the selection valid, records `RESUME_DELIVERY_FAILED`, and
gives the manager a concise retry-safe message; it does not silently downgrade
to a URL.

## Idempotency and Errors

Each delivery key is `(request_id, candidate_id, original_file_fingerprint)`.
The repository records the key before a retryable attempt and a terminal
success only after DingTalk confirms delivery. Repeated selection messages or
bridge retries return the existing successful result rather than transmitting
the file again. An earlier failed attempt can be retried deliberately without
reselecting a different candidate.

File validation failure, unavailable source, upload error, unsupported file
message, and audit-write failure all receive distinct internal codes. The
hiring manager sees only a safe result such as “已选中该候选人，但简历文件暂未
送达，请稍后重试。”

## Matched Tag Summary in Candidate Cards

`screening_v5` derives these immutable display fields from the already
grounded policy evaluation:

- `applicable_tag_count`: count of all user-visible, active policy labels for
  this request (for example, seven labels render as the denominator `7`).
- `matched_tag_count`: count of those labels for which the grounded assessment
  says `satisfied` with `confirmed` evidence. A partial or unknown assertion
  does not add to this count.

The card renders `符合标签 X/Y` next to its existing recommendation context.
`unknown` remains a verification item, never a fail; the values are not
labeled a score and are not used by recommendation ordering.

## Tests and Acceptance Criteria

Automated tests must prove:

1. `第一个`, `选第一个`, and `选第1个` select only a previously shown first
   candidate; cross-owner and unshown candidates still fail.
2. A selected candidate yields a file-delivery request without exposing a
   filesystem path; a missing or unsafe source does not send a file.
3. The bridge sends a DingTalk `file` message only after media upload succeeds;
   upload or send failure is auditable and retry-safe, not duplicated.
4. Candidate cards render the coverage summary using grounded counts and do
   not contain scoring fields or internal decision-mode names.
5. Existing Top-3, next-batch, pause/resume, unsupported-policy, and
   owner-isolation tests remain green.
6. Production rollout verifies service isolation, file upload, and receipt of
   one controlled resume by an authorized manager before enabling broader use.
