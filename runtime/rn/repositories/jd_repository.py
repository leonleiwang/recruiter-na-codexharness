"""Verified JD-template repository rebuilt from the source workbook."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rn.repositories.models import DataSourceHealth, JDRecord

ROOT = Path(__file__).resolve().parents[3]
_JD_LIBRARY_PATH = ROOT / "data" / "generated" / "jd" / "jd_library.json"


class JDRepository:
    """Read-only access to verified JD templates.

    Exact matches and related matches are deliberately separate.  Active
    requisitions are not part of this repository.
    """

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else _JD_LIBRARY_PATH
        self._jds: list[JDRecord] = []
        self._by_id: dict[str, JDRecord] = {}
        self._loaded = False
        self._doc: dict[str, Any] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        if not self._path.exists():
            raise FileNotFoundError(f"JD library not found: {self._path}")
        data = json.loads(self._path.read_text(encoding="utf-8"))
        self._doc = data
        items: list[dict[str, Any]] = data.get("entries") or []
        for item in items:
            if item.get("source_status") != "VERIFIED_SOURCE":
                raise ValueError(f"UNVERIFIED_JD_IN_PRODUCTION_LIBRARY: {item.get('title')}")
            jd_id = str(item.get("jd_id", "")).strip()
            title = str(item.get("title", "")).strip()
            if not jd_id or not title:
                continue
            record = JDRecord(
                jd_id=jd_id,
                title=title,
                company_type=str(item.get("company_type", "")),
                sub_category=str(item.get("sub_category", "")),
                core_responsibilities=str(item.get("core_responsibilities", "")),
                requirements_text=str(item.get("requirements_text", "")),
                source_file=str(item.get("source_file", "")),
                source_sheet=str(item.get("source_sheet", "")),
                source_row=int(item.get("source_row", 0)),
            )
            self._jds.append(record)
            self._by_id[jd_id] = record
        self._loaded = True

    def health(self) -> DataSourceHealth:
        try:
            self._load()
            expected = int(self._doc.get("total", 0))
            if expected != len(self._jds):
                return DataSourceHealth(
                    status="degraded", backend="verified_xlsx_json", count=len(self._jds),
                    error=f"total={expected} != actual={len(self._jds)}",
                )
            return DataSourceHealth(status="ok", backend="verified_xlsx_json", count=len(self._jds))
        except Exception as exc:
            return DataSourceHealth(status="unavailable", backend="verified_xlsx_json", error=str(exc))

    def count(self) -> int:
        self._load()
        return len(self._jds)

    def search_exact(self, title: str, limit: int = 20) -> list[JDRecord]:
        self._load()
        q = title.strip().casefold()
        if not q:
            return []
        return [jd for jd in self._jds if jd.title.strip().casefold() == q][:limit]

    def search(self, query: str = "", limit: int = 20) -> list[JDRecord]:
        """Loose JD search, still limited to JDRepository records."""
        self._load()
        q = query.strip().casefold()
        if not q:
            return self._jds[:limit]
        exact = self.search_exact(query, limit=limit)
        if exact:
            return exact
        matches = []
        for jd in self._jds:
            hay = " ".join([jd.title, jd.sub_category, jd.core_responsibilities, jd.requirements_text]).casefold()
            if q in hay:
                matches.append(jd)
        return matches[:limit]

    def get(self, jd_id: str) -> JDRecord | None:
        self._load()
        return self._by_id.get(jd_id)

    def list_all(self) -> list[JDRecord]:
        self._load()
        return list(self._jds)

    @staticmethod
    def _domain_terms(query: str) -> set[str]:
        q = query.casefold()
        terms: set[str] = set()
        maps = {
            "医学": {"医学", "医药", "医疗", "临床", "学术", "kol", "专家"},
            "联络": {"沟通", "拜访", "交流", "维护", "联络"},
            "官": {"专员", "经理", "顾问"},
            "肿瘤": {"肿瘤", "oncology", "癌"},
            "病理": {"病理", "阅片", "形态"},
            "检验": {"检验", "检测", "实验室"},
        }
        for trigger, vals in maps.items():
            if trigger in q:
                terms.update(vals)
        return terms

    def search_related(self, query: str, limit: int = 10) -> list[JDRecord]:
        """Rank related JDs without pretending that related == exact."""
        self._load()
        q = query.strip().casefold()
        if not q or self.search_exact(query):
            return []
        q_chars = {c for c in re.sub(r"\s+", "", q) if "\u4e00" <= c <= "\u9fff"}
        domain_terms = self._domain_terms(q)
        scored: list[tuple[float, str, JDRecord]] = []
        for jd in self._jds:
            title = jd.title.casefold()
            body = " ".join([jd.title, jd.core_responsibilities, jd.requirements_text, jd.sub_category]).casefold()
            score = 0.0
            overlap = len(q_chars & {c for c in title if "\u4e00" <= c <= "\u9fff"})
            score += overlap * 1.5
            # Domain evidence in the JD body matters more than title-character accidents.
            for term in domain_terms:
                if term.casefold() in body:
                    score += 1.2
                if term.casefold() in title:
                    score += 1.2
            if "kol" in body or "学术" in body:
                if "医学" in q or "联络" in q:
                    score += 2.0
            if "销售" in title and "销售" not in q:
                score -= 2.5
            if score >= 3.0:
                scored.append((score, jd.title, jd))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [r[2] for r in scored[:limit]]
