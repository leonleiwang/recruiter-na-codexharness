# Recruiter-Na v5.0.1 Latency & Grounding Hotfix Design

## Scope

This is a bounded production hotfix on the accepted v5 Candidate Decision architecture. It does not change the recruiter v2.3 business policy, the hiring-manager intake/JD flow, or PASS/VERIFY/FAIL semantics.

## Observed production failure

A 3-candidate smoke test wrote all `resume_facts.json` files within milliseconds, then completed candidate failures at roughly 3, 6, and 9 minutes. All three failures were `DECISION_EVIDENCE_GROUNDING_FAILED`; MySQL was not the bottleneck. Production was configured with a 60s LLM timeout, one primary plus four fallbacks, and the generic HTTP helper retried up to five times.

## Design

1. **Safe grounding repair before failure**
   - Evidence text must still be grounded in authoritative `resume_facts`.
   - A wrong `source_path` no longer invalidates an otherwise exact quote; the validator resolves the quote globally and canonicalizes the path.
   - If a supplied quote cannot be grounded, the affected semantic outcome is deterministically downgraded to `unknown`/`none` rather than being allowed to support PASS, FAIL, red-line veto, or preference.
   - The repaired contract is revalidated by the strict grounding validator before Candidate Decision.
   - Grounding therefore becomes fail-safe: ungrounded model text can never reject a candidate, but a single bad quote does not turn the whole candidate into a technical failure.

2. **Interactive decision LLM budget**
   - Candidate Decision receives dedicated runtime knobs independent of slower intake/profile matching:
     - `RECRUITER_DECISION_TIMEOUT_SECONDS=15`
     - `RECRUITER_DECISION_MAX_ATTEMPTS=1`
     - `RECRUITER_DECISION_MAX_MODELS=2`
     - optional `RECRUITER_DECISION_MODEL`
     - optional `RECRUITER_DECISION_FALLBACK_MODELS`
   - When dedicated fallbacks are not configured, the legacy fallback list is reused but latency-oriented `*flash*` models are preferred.
   - The primary model plus at most one fallback bounds one candidate's network wait to approximately 31 seconds under two full timeouts instead of minutes.

3. **Concurrency proof and latency traces**
   - Keep the existing `ThreadPoolExecutor` candidate-level concurrency.
   - Add a regression test using a synchronization barrier to prove three candidate evaluations are concurrently in flight.
   - Write per-candidate `timing.json` and aggregate screening timing metadata so future production diagnosis can distinguish LLM, contract, grounding, and deterministic decision latency without SSH guesswork.

4. **Deployment safety**
   - v5.0.1 upgrade only touches `/opt/recruiter-na` and preserves existing secrets, MySQL data, request SQLite, run traces, and unrelated services/projects.
   - Existing global LLM settings are preserved. New decision-specific settings are appended only when absent.
