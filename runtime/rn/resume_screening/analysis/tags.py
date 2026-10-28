"""简历 → 能力 / 经验 / 特质 三维度标签（带逐字证据）。

输入是 ``analysis.fields.extract_resume_fields`` 产出的字段 dict，输出是三维标签
文档。标签不是「结论」，而是**可回溯的简历事实单元**：

- 每个标签都带 ``evidence``（原文片段），便于 HR 一眼核对；
- ``confidence`` 只表达「这段文字对该维度的支持强度」（强线索命中 0.9，弱线索
  多条共现 0.6-0.7），**不是**对候选人的评分；
- 标签只做二维陈述（维度 + 名称 + 证据），匹配度在 ``matching.py`` 里计算，
  正式录用决策仍由 ``decision/`` 的 v5 链路负责。
"""
from __future__ import annotations

from typing import Any, Iterable

from rn.resume_screening.analysis import cues
from rn.resume_screening.analysis.textutil import compact_text, evidence_snippet

SCHEMA_VERSION = "resume-tags/1.0"

MAX_EVIDENCE_PER_TAG = 3


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def resume_corpus(fields: dict[str, Any]) -> str:
    """Build the text corpus used for tagging.

    ``raw_text`` 是首选（包含全部原始表述）；若调用方只给了结构化字段
    （例如从数据库读出来的记录没有原文），则把结构化字段拼回可扫描文本，
    保证标签仍能基于真实事实生成。
    """
    raw = str((fields or {}).get("raw_text") or "").strip()
    if raw:
        return raw
    chunks: list[str] = []
    for key in ("name", "current_position", "current_company", "expected_position", "self_evaluation"):
        value = str(fields.get(key) or "").strip()
        if value:
            chunks.append(value)
    skills = _string_list(fields.get("skills"))
    if skills:
        chunks.append("技能：" + "、".join(skills))
    certifications = _string_list(fields.get("certifications"))
    if certifications:
        chunks.append("证书：" + "、".join(certifications))
    for item in _as_list(fields.get("work_experiences")):
        if not isinstance(item, dict):
            continue
        parts = [str(item.get(key) or "").strip() for key in ("company", "position", "description")]
        line = " ".join(part for part in parts if part)
        if line:
            chunks.append(line)
    for item in _as_list(fields.get("projects")):
        if isinstance(item, dict):
            line = " ".join(str(item.get(key) or "").strip() for key in ("name", "description"))
            if line.strip():
                chunks.append(line)
    education = fields.get("education") if isinstance(fields.get("education"), dict) else {}
    edu_line = " ".join(str(education.get(key) or "").strip() for key in ("school", "major", "level"))
    if edu_line.strip():
        chunks.append(edu_line)
    return "\n".join(chunks)


def summarize_resume(fields: dict[str, Any]) -> dict[str, Any]:
    """Small human-facing digest used in reports (no judgement involved)."""
    education = fields.get("education") if isinstance(fields.get("education"), dict) else {}
    experiences = [item for item in _as_list(fields.get("work_experiences")) if isinstance(item, dict)]
    return {
        "name": str(fields.get("name") or "").strip(),
        "total_work_years": fields.get("total_work_years"),
        "education_level": str(education.get("level") or "").strip(),
        "school": str(education.get("school") or "").strip(),
        "major": str(education.get("major") or "").strip(),
        "location": str(fields.get("location") or "").strip(),
        "current_company": str(fields.get("current_company") or "").strip(),
        "current_position": str(fields.get("current_position") or "").strip(),
        "expected_position": str(fields.get("expected_position") or "").strip(),
        "expected_city": str(fields.get("expected_city") or "").strip(),
        "expected_salary": str(fields.get("expected_salary") or "").strip(),
        "skills": _string_list(fields.get("skills")),
        "certifications": _string_list(fields.get("certifications")),
        "experience_count": len(experiences),
        "latest_experience": (
            {
                "company": str(experiences[0].get("company") or "").strip(),
                "position": str(experiences[0].get("position") or "").strip(),
                "start_date": str(experiences[0].get("start_date") or "").strip(),
                "end_date": str(experiences[0].get("end_date") or "").strip(),
            }
            if experiences
            else {}
        ),
    }


def _confidence(strong_count: int, weak_count: int) -> float:
    if strong_count:
        return round(min(0.95, 0.88 + 0.02 * (strong_count - 1)), 3)
    if weak_count >= 3:
        return 0.7
    if weak_count == 2:
        return 0.6
    if weak_count == 1:
        return 0.45
    return 0.0


def _tag(
    *,
    tag_id: str,
    dimension: str,
    name: str,
    canonical_name: str,
    evidence: str,
    keywords: list[str],
    strong: list[str],
    confidence: float,
    source: str,
) -> dict[str, Any]:
    return {
        "tag_id": tag_id,
        "dimension": dimension,
        "name": name,
        "canonical_name": canonical_name,
        "evidence": evidence,
        "keywords": keywords,
        "strong": strong,
        "confidence": confidence,
        "source": source,
    }


def extract_resume_tags(
    fields: dict[str, Any],
    *,
    dimensions: Iterable[str] = cues.DIMENSIONS,
) -> dict[str, Any]:
    """Derive three-dimension tags from extracted resume fields."""
    fields = fields if isinstance(fields, dict) else {}
    raw_corpus = resume_corpus(fields)
    # OCR 逐字空格的简历（「姓 名 ： 张 晓 明」）若直接做关键词匹配会全部漏掉，
    # 所以标签侧必须用归一化后的语料（与 matching 侧同一口径）。
    corpus = compact_text(raw_corpus)
    wanted = tuple(dim for dim in dimensions if dim in cues.DIMENSIONS) or cues.DIMENSIONS
    hits = cues.match_cues(corpus, dimensions=wanted)
    hits.sort(key=lambda row: (-row["score"], row["name"]))

    tags: dict[str, list[dict[str, Any]]] = {dim: [] for dim in wanted}
    counters = {dim: 0 for dim in wanted}
    for hit in hits:
        dim = hit["dimension"]
        if dim not in tags:
            continue
        counters[dim] += 1
        weak_only = list(hit.get("weak_only") or [])
        # 弱证据词（参加学习培训/学习适应…）不能撑起置信度：证据里可以展示，但不计入
        # 「多处弱线索」的升级计数，避免「参加过培训」被当成「学得快」。
        scoring_weak = [kw for kw in hit["keywords"] if kw not in weak_only]
        snippets: list[str] = []
        for keyword in hit["strong"] + scoring_weak:
            snippet = evidence_snippet(corpus, keyword)
            if snippet and snippet not in snippets:
                snippets.append(snippet)
            if len(snippets) >= MAX_EVIDENCE_PER_TAG:
                break
        tags[dim].append(_tag(
            tag_id=f"T-{dim[:2].upper()}-{counters[dim]:02d}",
            dimension=dim,
            name=hit["name"],
            canonical_name=hit["name"],
            evidence=" / ".join(snippets),
            keywords=list(hit["keywords"]),
            strong=list(hit["strong"]),
            confidence=_confidence(len(hit["strong"]), len(scoring_weak)),
            source="resume_text",
        ))

    stats: dict[str, dict[str, Any]] = {}
    for dim in wanted:
        rows = tags[dim]
        stats[dim] = {
            "count": len(rows),
            "strong_count": sum(1 for row in rows if row["strong"]),
            "avg_confidence": round(sum(row["confidence"] for row in rows) / len(rows), 3) if rows else 0.0,
            "names": [row["name"] for row in rows],
        }

    notes: list[str] = []
    if not normalize_probe(corpus):
        notes.append("简历文本为空：无法生成任何标签。")
    elif not hits:
        notes.append(
            "简历原文有内容但未匹配到任何三维线索：扫描件请检查 OCR 结果是否存在乱码或形近错字。"
        )
    if not tags[cues.DIMENSION_TRAIT]:
        notes.append("特质维度未发现自述线索：抗压/细心/责任心等通常需要面试核实。")
    if not tags[cues.DIMENSION_EXPERIENCE]:
        notes.append("经验维度未发现可识别的工作/实习经历线索（应届或无经历简历常见）。")

    return {
        "schema_version": SCHEMA_VERSION,
        "dimensions": list(wanted),
        "dimension_labels": {dim: cues.DIMENSION_LABELS[dim] for dim in wanted},
        "tags": tags,
        "dimension_stats": stats,
        "summary": summarize_resume(fields),
        "structured": {
            "skills": _string_list(fields.get("skills")),
            "certifications": _string_list(fields.get("certifications")),
            "industry_tags": _string_list(fields.get("industry_tags")),
            "projects": len(_as_list(fields.get("projects"))),
        },
        "notes": notes,
    }


def normalize_probe(text: str) -> str:
    """Small helper so tests/CLI can tell whether a corpus is effectively empty."""
    return compact_text(text)


__all__ = [
    "SCHEMA_VERSION",
    "extract_resume_tags",
    "resume_corpus",
    "summarize_resume",
]
