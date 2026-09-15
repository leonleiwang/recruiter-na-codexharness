# Recruiter-Na v5.0.1 Deployment and Smoke Test

## Upgrade

From the extracted v5.0.1 release root:

```bash
sudo bash deploy/upgrade-v5.0.1.sh
```

The upgrade script:

- verifies the release before modifying `/opt/recruiter-na/app`;
- backs up the current application and owner-scoped SQLite request database;
- preserves `/etc/recruiter-na/recruiter-na.env`, `codex-home`, runs/reports and request DB;
- migrates the old worker-concurrency value to `RECRUITER_DECISION_WORKERS` when the new variable is absent;
- keeps the obsolete `recruiter-na-data.service` disabled;
- restarts the single `recruiter-na` service.

No destructive SQL schema migration is required. In-progress v4 candidate-score snapshots are invalidated automatically by the v5 repository migration and return to `JD_READY`; selected historical requests are retained.

## Environment delta

Preferred v5 variable:

```text
RECRUITER_DECISION_WORKERS=3
```

`RECRUITER_SCORING_WORKERS` is no longer an active runtime setting. The v5 upgrade script only reads it once as a migration source if `RECRUITER_DECISION_WORKERS` does not yet exist.

## Product smoke

Use a real/authorized hiring-manager test account.

1. `/new` -> one reply only.
2. `我要招一个检验技术员，杭州，1个人` -> matched profile should reduce intake.
3. Complete remaining request fields. Add a delta such as `必须做过罗氏免疫平台` if you want to test the criteria adapter.
4. Confirm the generated JD.
5. Send `开始筛选`.
6. Candidate cards should show recommendation reasons / verification / interview focus, and **no numeric candidate score**.
7. Send `换一批`; no candidate should repeat within the request.
8. Select a displayed candidate; selection must remain owner-scoped.
9. Start an unsupported role and reach screening; v5 must explicitly say that the Decision Policy is unsupported and must not fall back to the old scorer.
10. `查看我的招聘进展` must show only the current sender's requests.

## Runtime diagnostics

```bash
cd /opt/recruiter-na/app
sudo -u recruiter-na bash scripts/verify.sh
sudo -u recruiter-na .venv/bin/python -m rn.data_doctor
sudo -u recruiter-na .venv/bin/python -m rn.cli --backend mysql policy
journalctl -u recruiter-na -n 200 --no-pager
```

A successful bridge startup identifies itself as `Recruiter-Na v5.0.1`.

## v5.0.1 latency / grounding checks

After `开始筛选`, inspect the newest run only if latency is abnormal:

```bash
find /opt/recruiter-na/app/workspace/runs -name timing.json -type f -printf '%T@ %p\n' | sort -nr | head
```

Expected v5.0.1 behavior:

- a wrong model-authored `source_path` with an exact resume quote is canonicalized;
- an ungrounded quote is downgraded to UNKNOWN rather than rejecting the candidate or failing the whole evaluation;
- Candidate Decision uses its dedicated 15-second / one-attempt / two-model interactive budget unless the operator overrides it;
- candidate-level `timing.json` shows LLM and grounding stage durations.
