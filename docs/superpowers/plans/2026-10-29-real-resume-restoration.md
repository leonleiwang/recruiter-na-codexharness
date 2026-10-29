# Real resume restoration implementation plan

**Goal:** Restore the previously configured real resume source and remove synthetic runtime recommendations while preserving hiring requests.

**Architecture:** Repair deployment configuration rather than copying resumes into source. Use the existing MySQL repository and Candidate Decision chain. A small display fix maps unknown eligibility IDs to approved business text without changing decisions or ranking.

**Execution:** Inline; no additional review agents. Source changes stay on `release/v0.2.3`.

- [x] Verify original credentials through the repository, inspect only aggregate health and bounded inspection-technician retrieval.
- [x] Back up state, quarantine synthetic files, retain nine requests/JDs and invalidate seven synthetic recommendation snapshots with audit events.
- [x] Run actual model screening against six real candidates and inspect the first three manager-facing recommendations; keep technical failures separate from rejection.
- [x] Reproduce internal red-line ID leakage with failing tests, map business labels, run regressions.
- [ ] Publish v0.2.3 branch/tag/Release, verify public CI, deploy while retaining the real-source configuration and rollback.
- [ ] Verify live MySQL source, file references, Stream connection, state integrity and unchanged PulseHR units; user performs business acceptance.
