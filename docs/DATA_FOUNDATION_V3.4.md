# Recruiter-Na v3.4 Data Foundation

## Authoritative sources

### Standard position profiles

Production standard profiles are rebuilt from the 14 original PDF files under
`data/source/profiles/originals/`. The old mixed 40-profile JSON is quarantined
and is not a production source.

1. 二代测序检验技术员（全流程岗）.pdf
2. 二代测序检验技术员（提取岗）.pdf
3. 医学联络官.pdf
4. 客户运营代表.pdf
5. 微生物检验技术员.pdf
6. 微生物检验技术员（前处理岗）.pdf
7. 检验技术员（应届）.pdf
8. 检验技术员（社招）.pdf
9. 生信工程师.pdf
10. 病理医生（应届）.pdf
11. 病理医生（社招）.pdf
12. 病理技术员（应届）.pdf
13. 病理技术员（社招）.pdf
14. 配送专员.pdf

`data/manifests/profile_sources.json` records the SHA-256 hash and stable
`profile_id` for each source file.

### JD templates

`data/source/jd/岗位清单及JD库.xlsx` is the source workbook. It contains 45 JD
records. Generated records retain source workbook, sheet, and row provenance.

The workbook does not contain an exact `医学联络官` JD template. Related results
must remain explicitly marked `related`; they must not be promoted to an Exact
match.

### Resumes

Production resume authority is MySQL through `ResumeRepository`.

The migration source pack contains 75 candidate-source documents, but it is not
claimed to be a complete backup of the production MySQL database. The live
production count must always be queried dynamically; it is never hardcoded.

## Rebuild

```bash
python scripts/build-data-foundation.py
python scripts/audit-data-foundation.py
```

The generated profile/JD libraries are reproducible from the source assets.

## Production boundaries

- Profile = standard position profile.
- JD template = reusable JD source record.
- Requisition = a currently open hiring request under `workspace/jobs/`.
- Resume = candidate data in the production MySQL repository.

None of these entities may silently substitute for another.
