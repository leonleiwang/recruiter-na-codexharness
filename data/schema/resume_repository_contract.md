# Resume Repository Contract

Production resume authority is MySQL. Recruiter-Na reads the existing tables
used by `runtime/rn/resume_screening/parsing/resume_mysql.py`, including `candidate_resume` and its
associated candidate-information / mail-metadata tables when available.

Connection values are injected only through environment variables:

- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DATABASE`

No credentials belong in source code, manifests, release ZIPs, or Skills.

`data/dev/resume_db.fixture.json` is development-only. When `APP_ENV=production`,
selecting a JSON resume backend must raise `PRODUCTION_JSON_RESUME_DB_FORBIDDEN`.
If MySQL is unavailable, screening must return `RESUME_BACKEND_UNAVAILABLE` and
must not fall back to a local JSON file.
