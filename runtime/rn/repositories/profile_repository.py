"""Verified standard-position profile repository.

Production profiles are generated only from immutable source PDFs under
``data/source/profiles/originals``.  The mixed legacy profile library is not a
runtime source.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rn.repositories.models import DataSourceHealth, ProfileRecord

ROOT = Path(__file__).resolve().parents[3]
_PROFILE_LIBRARY_PATH = ROOT / "data" / "generated" / "profiles" / "profile_library.json"


class ProfileRepository:
    """Read-only access to verified standard position profiles."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else _PROFILE_LIBRARY_PATH
        self._profiles: list[ProfileRecord] = []
        self._by_name: dict[str, ProfileRecord] = {}
        self._by_id: dict[str, ProfileRecord] = {}
        self._loaded = False
        self._meta: dict[str, Any] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        if not self._path.exists():
            raise FileNotFoundError(f"Profile library not found: {self._path}")
        data = json.loads(self._path.read_text(encoding="utf-8"))
        self._meta = data.get("meta") or {}
        profiles: list[dict[str, Any]] = data.get("profiles") or []
        for p in profiles:
            name = str(p.get("name", "")).strip()
            if not name:
                continue
            if p.get("source_status") != "VERIFIED_SOURCE":
                raise ValueError(f"UNVERIFIED_PROFILE_IN_PRODUCTION_LIBRARY: {name}")
            if not p.get("source_file") or not p.get("source_sha256"):
                raise ValueError(f"PROFILE_PROVENANCE_MISSING: {name}")
            profile_id = str(p.get("profile_id") or "").strip() or _derive_profile_id(name)
            aliases = [str(a).strip() for a in (p.get("aliases") or []) if str(a).strip()]
            record = ProfileRecord(
                profile_id=profile_id,
                name=name,
                aliases=aliases,
                category=str(p.get("category", "")),
                department=str(p.get("department", "")),
                capabilities=p.get("key_abilities") or p.get("capabilities") or [],
                requirements=p.get("key_experiences") or p.get("requirements") or [],
                raw=p,
            )
            self._profiles.append(record)
            self._by_name[name] = record
            self._by_id[profile_id] = record
            for alias in aliases:
                self._by_name.setdefault(alias, record)
        self._loaded = True

    def health(self) -> DataSourceHealth:
        try:
            self._load()
            meta_total = int(self._meta.get("total", 0))
            authority = self._meta.get("authority")
            if meta_total != len(self._profiles):
                return DataSourceHealth(
                    status="degraded", backend="verified_pdf_json", count=len(self._profiles),
                    error=f"meta.total={meta_total} != actual={len(self._profiles)}",
                )
            if authority != "VERIFIED_SOURCE_PDF":
                return DataSourceHealth(
                    status="degraded", backend="verified_pdf_json", count=len(self._profiles),
                    error=f"unexpected authority={authority}",
                )
            return DataSourceHealth(status="ok", backend="verified_pdf_json", count=len(self._profiles))
        except Exception as exc:
            return DataSourceHealth(status="unavailable", backend="verified_pdf_json", error=str(exc))

    def count(self) -> int:
        self._load()
        return len(self._profiles)

    def search(self, query: str = "", limit: int = 20) -> list[ProfileRecord]:
        self._load()
        q = query.strip().lower()
        if not q:
            return self._profiles[:limit]
        exact = []
        related = []
        for p in self._profiles:
            names = [p.name, *p.aliases]
            if any(q == name.lower() for name in names):
                exact.append(p)
            elif any(q in name.lower() or name.lower() in q for name in names):
                related.append(p)
        return (exact + related)[:limit]

    def get(self, profile_id_or_name: str) -> ProfileRecord | None:
        self._load()
        return self._by_id.get(profile_id_or_name) or self._by_name.get(profile_id_or_name)

    def list_all(self) -> list[ProfileRecord]:
        self._load()
        return list(self._profiles)


def _derive_profile_id(name: str) -> str:
    import hashlib
    return f"PROFILE-{hashlib.sha256(name.encode()).hexdigest()[:12].upper()}"
