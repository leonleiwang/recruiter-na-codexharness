# Recruiting knowledge runtime notes

Production data libraries are no longer stored here.

- Verified standard profiles are generated at `data/generated/profiles/profile_library.json`
  from immutable PDFs under `data/source/profiles/originals/`.
- Verified JD templates are generated at `data/generated/jd/jd_library.json`
  from `data/source/jd/岗位清单及JD库.xlsx`.
- Production resumes are authoritative in MySQL through `ResumeRepository`.

`dictionaries.md` remains a terminology/reference asset. Do not count files in
this directory to determine repository size.
