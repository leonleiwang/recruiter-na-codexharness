# Recruiter-Na v4 Hiring Manager Workflow Implementation Plan

**Goal:** Reframe Recruiter-Na as a DingTalk digital recruiter for hiring managers with exactly two public capabilities: start a hiring request and view the caller's own hiring progress.

**Architecture:** Keep verified Profile/JD sources, MySQL resumes, semantic evidence, grounding, and codexharness-im. Add an owner-scoped SQLite hiring-request store and host-side workflow service. Matching/intake/JD/ranking execute host-side; candidate scoring uses separate Profile and JD semantic contracts and deterministic 40/60 blending. The DingTalk router never exposes raw repository browsing to hiring managers.

**Tech Stack:** Python 3.10+, SQLite, MySQL/PyMySQL, existing semantic evaluator, Node.js DingTalk bridge, codexharness-im 0.2.2.

## Global Constraints
- Profile match is semantic-only; no keyword fallback may decide MATCHED/NO_MATCH.
- Profile+JD match blend is 40%/60%; no-profile requests use JDMatch 100%.
- Outer score remains RQ 30% + JM 70%.
- Missing hard-gate information is UNKNOWN / needs_verification, never automatic FAIL.
- Hiring-manager data access is owner-scoped by normalized DingTalk senderId.
- Public workflow cannot browse all profiles, JDs, resumes, or other owners' requests.
- Candidate ranking is computed once for a pool; batches of three never repeat previously shown candidates.
