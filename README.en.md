# Recruiter-Na

[中文](README.md) | **English**

Recruiter-Na is an open-source DingTalk recruitment assistant for hiring managers, built on Codex Harness.

It helps a hiring manager start or continue only their own hiring request, and view progress for only their own requests. For the currently supported inspection-technician policies, its screening path is `Resume Facts → LLM Semantic Evidence → Evidence Grounding → deterministic Candidate Decision`.

Documentation: [usage guide](USAGE.md), [Candidate Decision contract](docs/RECRUITMENT_DECISION_V5.md), and [resume parsing + capability/experience/trait matching](runtime/rn/resume_screening/README.md).

## v0.2.1: resume-stage tag counts

The card counts only JD capability/experience labels that can be checked before an interview. Approved interview-only requirements are shown separately and excluded from both numerator and denominator, even when a resume contains related keywords. Three hits out of three resume-stage labels display as **3/3**; missing evidence remains in the denominator. The existing rule engine scans resume text; v5 retains eligibility, final decisions, ordering and interview focus. This is evidence coverage, not a talent score. Interview focus is shown as a question direction followed by its label, using approved criterion descriptions instead of repetitive missing-evidence text. Existing recommendation snapshots must be re-screened to adopt the new view.

The public source version is recorded in `RELEASE_VERSION` (0.2.1); `VERSION` retains the workflow contract version 5.0.2.

Release branch/tag: `release/v0.2.1` / `v0.2.1`. See [release notes](docs/releases/v0.2.1.md) for validation and limits.

## Developer preview

Recruiter-Na is a developer preview and is iterating rapidly. THERE MAY BE COMPATIBILITY-BREAKING CHANGES.

This public source release intentionally contains no production credentials, tenant configuration, candidate resumes, internal position/JD libraries, or request database. Review the example configuration before running the project.

## Run

### Run from source

Install Python 3.10+, then run the source-only verification included in this public distribution:

```bash
git clone https://github.com/leonleiwang/recruiter-na-codexharness.git
cd recruiter-na-codexharness
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/secret_scan.py
PYTHONPATH=runtime .venv/bin/python -m pytest tests/test_secrets_and_privacy.py tests/test_codex_packaging.py -q
```

The full local/production verification script, `scripts/verify.sh`, additionally requires a lawfully obtained IM adapter and authorized recruiting data. Configure a provider in an isolated `CODEX_HOME` using one of `config/codex/*.example`, then fill only the required values in your untracked `.env`. For server installation, start from `deploy/recruiter-na.env.example` and `deploy/install-cloud.sh`.

The operator must supply an authorized resume repository, verified position/JD material, and a lawfully obtained IM adapter before enabling a real bot. The omitted private runtime adapter is not part of this Apache-2.0 distribution.

## Community and support

- Submit reproducible bug reports or feature requests through [GitHub Issues](https://github.com/leonleiwang/recruiter-na-codexharness/issues).
- Use [GitHub Discussions](https://github.com/leonleiwang/recruiter-na-codexharness/discussions) for questions and design discussion.
- Do not include resumes, access tokens, tenant identifiers, or other personal data in public reports.

## Contributing

Open an issue first for a substantial change. Contributions must preserve owner-scoped request access, avoid sensitive attributes in candidate decisions, and never add secrets or real candidate data to the repository.

## Development

After supplying the authorized private dependencies described above, run the full verification suite:

```bash
bash scripts/verify.sh
```

Resume parsing and screening code is grouped in `runtime/rn/resume_screening/` (`parsing/`, `scoring/`, `decision/`; see its `README.md` for the module map). The active service uses `resume_screening/decision/screening_v5.py`; legacy scorers under `resume_screening/scoring/` remain only for regression and rollback analysis and are not loaded by the public hiring-manager path. Candidate Decision is currently supported only where an approved policy exists; unsupported roles must not fall back to numerical candidate scoring.

## Citation

```text
@software{recruiter_na_2026,
  title = {Recruiter-Na: Hiring Manager Recruitment Assistant},
  author = {Recruiter-Na Contributors},
  year = {2026},
  url = {https://github.com/leonleiwang/recruiter-na-codexharness}
}
```

## License

[Apache-2.0](LICENSE)

This license applies to the files distributed in this repository. It does not grant rights to any excluded private data, credentials, tenant configuration, or separately obtained runtime dependency.
