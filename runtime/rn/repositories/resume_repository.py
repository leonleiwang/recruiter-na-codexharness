"""Resume repository (简历数据库).

This repository wraps the MySQL backend with production fail-closed semantics.
In production, it MUST NOT silently fall back to JSON resume DB.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from rn.repositories.models import DataSourceHealth, ResumeRecord
from rn.resume_mysql import ResumeMySQLBackend


ROOT = Path(__file__).resolve().parents[3]
DEV_FIXTURE = ROOT / "data/dev/resume_db.fixture.json"  # development only


class ResumeRepository:
    """Read-only access to the resume database with production guardrails."""

    def __init__(self, backend: str | None = None):
        self._backend_name = backend or os.environ.get("RN_BACKEND", "mysql")
        self._allow_json_dev = os.environ.get("RN_ALLOW_JSON_DEV", "false").lower() == "true"
        self._app_env = os.environ.get("APP_ENV", os.environ.get("RN_BACKEND", "production"))
        self._mysql: ResumeMySQLBackend | None = None

    def _get_mysql(self) -> ResumeMySQLBackend:
        if self._mysql is None:
            self._mysql = ResumeMySQLBackend()
        return self._mysql

    def health(self) -> DataSourceHealth:
        if self._backend_name != "mysql":
            return DataSourceHealth(status="degraded", backend=self._backend_name,
                                    error="MySQL is the only supported production backend")
        try:
            be = self._get_mysql()
            conn = be._connect()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS cnt FROM candidate_resume")
            count = cur.fetchone()["cnt"]
            cur.close()
            return DataSourceHealth(status="ok", backend="mysql", count=count)
        except Exception as exc:
            return DataSourceHealth(status="unavailable", backend="mysql", error=str(exc))

    def count(self) -> int:
        h = self.health()
        if h.status == "ok" and h.count is not None:
            return h.count
        if h.status == "unavailable":
            raise RuntimeError("RESUME_BACKEND_UNAVAILABLE: " + (h.error or "unknown error"))
        return 0

    def search(self, query: str = "", limit: int = 20, **filters: Any) -> list[ResumeRecord]:
        if self._backend_name != "mysql":
            if self._allow_json_dev and self._app_env != "production":
                return self._search_json(query, limit, **filters)
            raise RuntimeError("PRODUCTION_JSON_RESUME_DB_FORBIDDEN: use MySQL in production")
        try:
            be = self._get_mysql()
            raw_list = be.search(query, limit=limit, **filters)
            return [ResumeRecord(resume_id=r.get("resume_id", ""), name=r.get("name", ""), raw=r) for r in raw_list]
        except Exception as exc:
            if "RESUME_BACKEND_UNAVAILABLE" in str(exc) or "PRODUCTION_JSON" in str(exc):
                raise
            raise RuntimeError(f"RESUME_BACKEND_UNAVAILABLE: {exc}") from exc

    def get(self, resume_id: str) -> ResumeRecord | None:
        results = self.search(query=resume_id, limit=1)
        return results[0] if results else None

    def get_candidate(self, identifier: str) -> dict[str, Any] | None:
        """Get a full candidate resume dict by identifier (name, phone, or resume_id)."""
        if self._backend_name != "mysql":
            if self._allow_json_dev and self._app_env != "production":
                return self._get_candidate_json(identifier)
            raise RuntimeError("PRODUCTION_JSON_RESUME_DB_FORBIDDEN")
        try:
            be = self._get_mysql()
            results = be.search(query=identifier, limit=1)
            return results[0] if results else None
        except Exception as exc:
            raise RuntimeError(f"RESUME_BACKEND_UNAVAILABLE: {exc}") from exc

    def search_candidates(self, query: str = "", limit: int = 50, **filters: Any) -> list[dict[str, Any]]:
        """Search and return full candidate resume dicts for screening."""
        if self._backend_name != "mysql":
            if self._allow_json_dev and self._app_env != "production":
                return self._search_candidates_json(query, limit, **filters)
            raise RuntimeError("PRODUCTION_JSON_RESUME_DB_FORBIDDEN")
        try:
            be = self._get_mysql()
            return be.search(query, limit=limit, **filters)
        except Exception as exc:
            raise RuntimeError(f"RESUME_BACKEND_UNAVAILABLE: {exc}") from exc

    def _search_json(self, query: str = "", limit: int = 20, **filters: Any) -> list[ResumeRecord]:
        """Development-only JSON fallback. Must not be used in production."""
        from rn.resume_db import JsonResumeStore
        db = JsonResumeStore(DEV_FIXTURE)
        results = db.search(query, limit=limit, **filters)
        return [ResumeRecord(resume_id=r.get("resume_id", ""), name=r.get("name", ""), raw=r) for r in results]

    def _get_candidate_json(self, identifier: str) -> dict[str, Any] | None:
        from rn.resume_db import JsonResumeStore
        db = JsonResumeStore(DEV_FIXTURE)
        results = db.search(query=identifier, limit=1)
        return results[0] if results else None

    def _search_candidates_json(self, query: str = "", limit: int = 50, **filters: Any) -> list[dict[str, Any]]:
        from rn.resume_db import JsonResumeStore
        db = JsonResumeStore(DEV_FIXTURE)
        return db.search(query, limit=limit, **filters)