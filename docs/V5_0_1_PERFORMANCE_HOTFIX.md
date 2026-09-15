# Recruiter-Na v5.0.1 Performance Hotfix

## Production evidence

The first v5.0.0 live smoke test fetched three resume facts in milliseconds but produced candidate completions at roughly 3, 6 and 9 minutes. All three ended in `DECISION_EVIDENCE_GROUNDING_FAILED`. The deployed environment used a 60-second generic LLM timeout and a long fallback chain.

## What v5.0.1 changes

1. Candidate Decision has a separate interactive LLM budget: `RECRUITER_DECISION_TIMEOUT_SECONDS=15`, `RECRUITER_DECISION_MAX_ATTEMPTS=1`, `RECRUITER_DECISION_MAX_MODELS=2`.
2. `RECRUITER_DECISION_MODEL` and `RECRUITER_DECISION_FALLBACK_MODELS` can be set independently. If they are absent, the general configuration remains the source, with flash/fast fallbacks preferred.
3. Grounding resolves exact quotes globally when the model supplied the wrong path.
4. Quotes that still cannot be grounded are discarded. A definitive hard check/criterion/red-line hit that loses all grounded support is downgraded to UNKNOWN. This is deliberately fail-safe: ungrounded model output cannot reject a candidate.
5. Each candidate emits `timing.json`; the decision snapshot includes aggregate `performance` metadata.

## Expected behavior

With three decision workers and healthy low-latency models, three-candidate screening should complete concurrently rather than linearly. Under full network timeouts, the default two-model budget is bounded to roughly 30 seconds plus small switching overhead per candidate, instead of multi-minute retry chains.

## Deployment

```bash
sudo bash deploy/upgrade-v5.0.1.sh
```

The script only updates `/opt/recruiter-na`, backs up the current app/env, preserves MySQL and request SQLite data, and does not touch PulseHR or other `/opt` projects.
