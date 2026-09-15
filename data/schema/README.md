# Production resume schema

The exact production MySQL DDL is intentionally not invented from application
code. On the production host, export the authoritative schema with:

```bash
bash scripts/export-resume-schema.sh
```

The generated `resume_mysql_schema.production.sql` can be stored in the private
repository / encrypted backup alongside migrations. Do not include credentials
or real candidate rows in source control.
