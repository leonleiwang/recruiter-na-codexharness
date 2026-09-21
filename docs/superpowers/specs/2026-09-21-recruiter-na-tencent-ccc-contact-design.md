# Recruiter-Na Tencent CCC Contact Loop Design

## Goal

Extend the existing owner-scoped Recruiter-Na v5 workflow after a hiring manager selects a recommended candidate. Recruiter-Na coordinates candidate availability, hiring-manager confirmation, a scheduled two-party call, and three call artifacts through Tencent Cloud CCC without changing Candidate Decision semantics.

The first delivery milestone is not complete at a mocked API response. It is complete only when the DingTalk bot recommends a pre-approved demonstration colleague whose test resume contains that colleague's controlled phone number, the hiring manager selects that person through the normal conversation, and Tencent CCC places and connects the first candidate-confirmation call to that colleague.

## Product boundary

- Candidate selection continues to come from the existing recommendation snapshot and owner-scoped `SELECTED` transition.
- Contact orchestration is a separate subsystem. It must not modify screening criteria, Candidate Decision, recommendation order, profile matching, or resume scoring.
- Only the hiring manager who owns the recruitment request can create, inspect, cancel, or continue its contact task.
- Candidate and employee phone numbers are contact data, not decision evidence. They must not enter model prompts except where CCC strictly requires a callee value, appear in user-facing responses, or be written to decision traces.
- The first real call is a controlled demonstration call, not a production candidate call.

## Delivery milestones

### Milestone 0: deterministic fake loop

Using `FakeCCCClient`, demonstrate:

`SELECTED -> CONTACT_PENDING -> CANDIDATE_CONFIRMED -> MANAGER_CONFIRMED -> SCHEDULED -> BRIDGE_CALLING -> COMPLETED`

This milestone validates persistence, state transitions, idempotency, callbacks, scheduling, and artifact association. It produces zero real telephone calls.

### Milestone 1: controlled real candidate-confirmation call

Milestone 1 is successful only when all of the following are observed end to end:

1. A dedicated demonstration resume is stored through the existing `ResumeRepository` backend and is eligible for the supported inspection-technician workflow.
2. The resume represents a named test or demonstration colleague who has explicitly agreed to receive the call and to the test recording policy.
3. Its phone number is not committed to Git, copied into a fixture, printed in logs, exposed in a candidate card, or stored in the recommendation snapshot.
4. The production DingTalk Recruiter-Na bot recommends that demonstration colleague through the normal Top-3 flow.
5. The hiring manager selects the colleague by ordinal or name through the normal bot conversation.
6. Exactly one active contact task is created from the selected request and candidate.
7. All real-call safety gates pass.
8. Tencent CCC accepts `CreateAIAgentCall`, returns a real `SessionId`, places the call to the allowlisted colleague, and the colleague answers it.
9. Recruiter-Na associates the session with `candidate_confirmation_session_id` and can retrieve or ingest its terminal call status.
10. Evidence records the request ID, candidate ID, masked callee, CCC session ID, timestamps, final call status, and recording/ASR references when available. The evidence does not contain secrets or an unmasked phone number.

A created API request, returned request ID, ringing state, or mocked callback alone is not Milestone 1 success. The call must connect to the demonstration colleague. Candidate scheduling semantics may be tested during the call, but completing manager confirmation and the bridge call belongs to later milestones.

### Milestone 2: two-sided scheduling

The AI calls the candidate, extracts an explicit time, calls the owning hiring manager, and schedules only when both parties explicitly confirm the same absolute time.

### Milestone 3: scheduled bridge and artifact closure

At the agreed time, Recruiter-Na invokes the currently supported Tencent CCC two-party call capability, verifies both participants are connected, and associates the candidate-confirmation call, manager-confirmation call, and bridge call with their CDR, recording, and ASR references.

## Architecture

### Contact task repository

Create an independent SQLite repository using the configured Recruiter-Na request database. A contact task is unique for an active `(request_id, selected_candidate_id)` pair and always carries `owner_staff_id`.

Required task fields:

- task ID, request ID, owner staff ID, candidate ID, and status;
- candidate-, manager-, and scheduled-time confirmation timestamps;
- `candidate_confirmation_session_id`, `manager_confirmation_session_id`, and `bridge_session_id`;
- recording object references and ASR/transcript references for each stage;
- retry count, last error code, lease owner/expiry, version, created time, and updated time.

Full telephone numbers, Tencent credentials, raw recording bytes, expiring public recording URLs, and unrestricted transcript text do not belong in the task row. Contact resolution remains behind dedicated resolver interfaces.

### State machine

Allowed states are:

- `CONTACT_PENDING`
- `CANDIDATE_CALLING`
- `CANDIDATE_CONFIRMED`
- `CANDIDATE_RESCHEDULE`
- `MANAGER_CALLING`
- `MANAGER_CONFIRMED`
- `SCHEDULED`
- `BRIDGE_CALLING`
- `COMPLETED`
- `DECLINED`
- `CANCELLED`
- `RETRY_PENDING`
- `FAILED`

Transitions use compare-and-swap semantics and append immutable contact events. Repeated DingTalk selection messages, scheduler ticks, and CCC callbacks return the existing task or no-op rather than creating a second call.

### Contact resolvers

`CandidateContactResolver` accepts a candidate ID and resolves the phone number from `ResumeRepository` only at call time. `ManagerContactResolver` accepts the normalized DingTalk `owner_staff_id` and resolves an employee phone from an approved company directory source. Neither interface permits enumeration or cross-owner lookup.

The demonstration resume is production-like data inserted into the configured resume backend by an explicit operator procedure. Its number is supplied outside Git and protected by the same repository access rules as other resumes. A separate allowlist stores only normalized phone hashes or a secret-managed number list.

### CCC adapter

Define a narrow `CCCClient` protocol with:

- candidate and manager AI-call creation;
- scheduled two-party call creation;
- session-detail, CDR, recording-reference, and ASR retrieval;
- callback verification and normalization.

`FakeCCCClient` is the default in tests. The production implementation uses Tencent Cloud SDK credentials injected from the environment or a secret manager. The first two calls use the current `CreateAIAgentCall` API. The bridge adapter must be verified against the current CCC application and official API before implementation; it must not simulate a bridge by placing two unrelated AI calls.

### Scheduling and recovery

A database-backed worker claims due tasks with a lease and compare-and-swap update. The scheduler stores absolute `Asia/Shanghai` timestamps plus the original spoken time. It does not use in-process timers. Restarted workers reclaim expired leases, while persisted CCC session IDs prevent repeated call creation.

### Callback and artifact ingestion

The callback boundary authenticates the sender, rejects unknown session IDs, redacts phone numbers, and treats duplicate events idempotently. Polling remains available when CCC callbacks or push delivery are unavailable.

For each call, Recruiter-Na stores CCC object identifiers and controlled storage references. It may obtain records through current equivalents of `DescribeSessionDetail`, `DescribeTelCdr`, and `DescribeTelRecordAsr`. Recording retention, access authorization, and deletion follow the company's approved recruitment-record policy; the implementation does not invent a retention period.

## Conversation behavior

After a unique selection with no active contact task, the bot says it has started contacting the selected person and will arrange a call only after both sides confirm. With an existing task, it reports the current state and does not initiate another call.

The bot supports `联系进度` and `取消联系`. Status output contains necessary progress and masked identifiers, never phone numbers, CCC credentials, internal prompts, raw callback bodies, or unrestricted transcripts.

Candidate AI calls identify the caller as Dian Diagnostics recruitment contact, explain the purpose, provide the applicable recording notice, and ask whether a call approximately thirty minutes later is convenient. The extracted result is one of `accepted`, `reschedule`, `declined`, `unreachable`, or `uncertain`, with an absolute time, `Asia/Shanghai`, confidence, and original time phrase. `uncertain` never advances automatically.

Manager AI calls state the candidate's confirmed time and request an explicit confirmation. A manager-proposed change returns to candidate confirmation; it never overwrites the candidate's agreement.

## Real-call safety gates

Every real outbound call must satisfy all gates atomically:

- `RECRUITER_CCC_ENABLED=true`;
- Tencent credentials, CCC `SdkAppId`, AI agent ID, and an approved caller number are configured and validated;
- the CCC voice-agent service and the required self-owned telephone access are active;
- the callee is in `RECRUITER_CCC_ALLOWED_CALLEES` or matches its secret-managed equivalent;
- the candidate is marked as a demonstration participant for Milestone 1;
- explicit colleague consent and recording acknowledgement are recorded outside the model output;
- the daily limit has not been reached;
- the task has no existing candidate session ID or in-flight idempotency record;
- an operator has enabled the milestone-specific real-call switch.

Failure of any gate produces no CCC call and records a redacted reason. The default configuration keeps CCC disabled. Production candidates are excluded from Milestone 1 even if their numbers happen to match an old test value.

Required configuration placeholders include:

- `RECRUITER_CCC_ENABLED=false`
- `RECRUITER_CCC_REAL_DEMO_ENABLED=false`
- `RECRUITER_CCC_ALLOWED_CALLEES=`
- `RECRUITER_CCC_DAILY_LIMIT=`
- `TENCENT_CCC_SECRET_ID=`
- `TENCENT_CCC_SECRET_KEY=`
- `TENCENT_CCC_SDK_APP_ID=`
- `TENCENT_CCC_AGENT_ID=`
- `TENCENT_CCC_CALLER_NUMBER=`
- `TENCENT_CCC_CALLBACK_SECRET=`

## Error handling

- No answer, busy, or transient CCC errors move to bounded `RETRY_PENDING`; retry policy is configurable and idempotent.
- Candidate refusal moves to `DECLINED` and never calls the manager.
- Manager refusal moves to `CANCELLED` and never schedules the bridge.
- Ambiguous speech pauses for human review.
- Missing or unhealthy contact sources fail closed without exposing the requested number.
- A callback for an unknown session is recorded as a security event and changes no task.
- A service restart resumes persisted work without issuing a second call for a stored session.

## Verification

Automated tests must cover owner isolation, unique task creation, ordinal/name selection, retries, candidate refusal/rescheduling, manager confirmation/rescheduling, scheduler leases, restart recovery, callback authentication/idempotency, session-to-stage binding, artifact references, disabled-mode zero calls, daily limits, and rejection of non-allowlisted numbers.

Before Milestone 1, the full fake flow and repository regression suite must pass. The real demonstration run then uses one pre-approved colleague and one approved caller number. Verification captures sanitized DingTalk messages, database events, CCC request/session identifiers, connected status, and artifact availability. It must state the exact commit, configuration keys present without values, whether any real call occurred, who authorized the demonstration record by internal reference, and how to disable further calls.

## Console and deployment constraints

Console inspection may determine the current SDK application ID, voice-agent status, agent ID, telephone-access status, caller-number readiness, and callback/recording configuration. It must not purchase resources, create persistent cloud credentials, delete agents or numbers, or dial a real person without the action-time confirmation required for that specific test.

Deployment changes affect only `recruiter-na.service` and its explicitly introduced worker/timer unit. PulseHR and unrelated colocated services must not be restarted or modified. Every deployment needs a rollback anchor and before/after protected-service checks.

## Acceptance criteria

1. Contact orchestration remains independent from Candidate Decision and recommendation semantics.
2. Every public operation is owner-scoped; contact data is neither enumerable nor exposed.
3. Disabled or misconfigured CCC mode generates zero real calls.
4. The Fake CCC loop reaches `COMPLETED` exactly once and associates three distinct session IDs.
5. Duplicate selection, scheduling, and callbacks never produce duplicate calls or state advancement.
6. Milestone 1 satisfies all ten controlled real-call observations, including a connected call to the consenting demonstration colleague.
7. No production candidate is called during Milestone 1.
8. No unmasked telephone number, credential, raw recording, or secret-managed allowlist is committed or logged.
9. Manager scheduling requires both parties to confirm the same absolute time.
10. The bridge milestone uses a verified current Tencent CCC two-party-call capability and records three independent call artifacts.
11. A final implementation report distinguishes automated evidence, controlled real-call evidence, console prerequisites, and anything still unverified.
