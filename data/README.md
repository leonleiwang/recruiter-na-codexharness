# Recruiter-Na Data Foundation v3.4

The three production data domains are versioned and governed separately.

## 1. Standard position profiles

- Immutable source: `data/source/profiles/originals/*.pdf`
- Generated runtime library: `data/generated/profiles/profile_library.json`
- Source manifest: `data/manifests/profile_sources.json`
- Production authority: **verified source PDFs**

The generated library is rebuilt from the source PDFs. Legacy/mixed generated
profile files are kept only under `data/quarantine/` for migration audit.

## 2. JD template library

- Immutable source: `data/source/jd/岗位清单及JD库.xlsx`
- Generated runtime library: `data/generated/jd/jd_library.json`
- Source manifest: `data/manifests/jd_source.json`
- Production authority: **verified source workbook**

JD templates and active requisitions are different entities. Files under
`workspace/jobs/job-*.json` are current requisitions, not JD templates.

## 3. Resume repository

- Production authority: **MySQL** through `ResumeRepository`
- Private source archive / re-import input: `data/source/resumes/private/`
- Source-pack manifest: `data/manifests/resume_sources.json`
- Development-only fixture: `data/dev/resume_db.fixture.json`

The private source pack is **not** declared to be a complete backup of the
production MySQL database. Runtime code must obtain the live count from MySQL.
Never commit real candidate resumes to a public repository.

## Rebuild

```bash
python scripts/build-data-foundation.py
python scripts/audit-data-foundation.py
```

Generated libraries should be reproducible from the immutable profile/JD
sources. Production screening must fail closed when MySQL is unavailable.
