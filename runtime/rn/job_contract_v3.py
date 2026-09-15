"""Adapters for Recruiter-Na Job Contract v3 and Resume Facts.

This module converts the legacy business JSON shape into the v3 contract. It
never performs candidate matching or scoring.
"""
from __future__ import annotations

import copy
import re
from typing import Any

SCHEMA_VERSION = "3.0"

_SENSITIVE_PATTERNS = [
    re.compile(r"(?im)(性别|年龄|出生日期|出生年月|生日|婚姻状况|婚育状况|民族|籍贯|户籍|政治面貌|宗教|健康状况|身份证号?)\s*[:：]\s*[^\n|,，;；]+"),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
]


def _redact_sensitive_text(text: str) -> str:
    out = str(text or "")
    for pattern in _SENSITIVE_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


def _definition_map(profile: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(profile, dict):
        return {}
    result: dict[str, str] = {}
    for key in ("key_abilities", "key_experiences"):
        for item in profile.get(key) or []:
            if isinstance(item, dict) and item.get("name"):
                result[str(item["name"])] = str(item.get("def") or item["name"])
    for item in profile.get("key_traits") or []:
        if isinstance(item, str):
            result[item] = item
        elif isinstance(item, dict) and item.get("name"):
            result[str(item["name"])] = str(item.get("def") or item["name"])
    return result


def _flatten_legacy_tags(job: dict[str, Any], profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    defs = _definition_map(profile)
    raw: list[dict[str, Any]] = []
    for group in job.get("portrait_tags") or []:
        category = str(group.get("category") or "other")
        for item in group.get("tags") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            raw.append({
                "name": str(item["name"]).strip(),
                "category": category,
                "weight": float(item.get("weight") or 0),
                "definition": str(item.get("definition") or defs.get(str(item["name"])) or item["name"]).strip(),
            })

    if raw:
        return raw

    # Fallback for a confirmed standard profile that has not yet been written
    # back into legacy portrait_tags. This creates semantic tags, never scores.
    if isinstance(profile, dict):
        groups = [
            ("capability", profile.get("key_abilities") or [], 0.50),
            ("experience", profile.get("key_experiences") or [], 0.35),
            ("traits", profile.get("key_traits") or [], 0.15),
        ]
        for category, items, group_weight in groups:
            valid = [x for x in items if (isinstance(x, str) and x.strip()) or (isinstance(x, dict) and x.get("name"))]
            if not valid:
                continue
            weight = group_weight / len(valid)
            for item in valid:
                if isinstance(item, str):
                    name, definition = item, defs.get(item, item)
                else:
                    name = str(item["name"])
                    definition = str(item.get("def") or defs.get(name) or name)
                raw.append({"name": name, "category": category, "weight": weight, "definition": definition})
    return raw


def _normalize_weights(tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not tags:
        raise ValueError("JOB_CONTRACT_TAGS_MISSING")
    total = sum(max(0.0, float(t.get("weight") or 0)) for t in tags)
    if total <= 0:
        raise ValueError("JOB_CONTRACT_TAG_WEIGHT_INVALID")
    result = copy.deepcopy(tags)
    running = 0.0
    for index, tag in enumerate(result):
        if index == len(result) - 1:
            weight = round(1.0 - running, 6)
        else:
            weight = round(max(0.0, float(tag.get("weight") or 0)) / total, 6)
            running += weight
        tag["weight"] = weight
    return result


def _requirement_type(name: str) -> str:
    lowered = name.lower()
    if "地点" in name or "城市" in name or "location" in lowered or "办公" in name:
        return "location"
    if "学历" in name or "degree" in lowered:
        return "degree"
    if "证" in name or "资格" in name or "cert" in lowered or "license" in lowered:
        return "credential"
    if "工龄" in name or "年限" in name or "经验" in name:
        return "experience"
    return "other"


def _decision_policy(req_type: str) -> str:
    if req_type == "location":
        return "当前城市不同、期望城市不同或简历未写工作意愿时一律 UNKNOWN；只有候选人明确表示不接受目标地点/现场办公等与要求直接冲突的事实，才可 FAIL。"
    if req_type == "degree":
        return "只有简历明确给出学历且可确定低于岗位硬门槛时才可 FAIL；缺失或表述无法比较时 UNKNOWN。"
    if req_type == "credential":
        return "只有明确写明不具备/已失效等直接冲突事实才可 FAIL；未提及证书时 UNKNOWN。"
    return "仅以简历中的明确事实判断 PASS/FAIL；信息缺失、语义不足或无法确定时必须 UNKNOWN。"


def build_job_contract(job: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a v3 contract from a legacy/current job document."""
    if str(job.get("schema_version")) == SCHEMA_VERSION and job.get("job_id") and isinstance(job.get("tags"), list):
        return copy.deepcopy(job)

    job_id = str(job.get("job_id") or job.get("id") or "").strip()
    if not job_id:
        raise ValueError("JOB_ID_MISSING")
    tags = _normalize_weights(_flatten_legacy_tags(job, profile))
    converted_tags = []
    for idx, tag in enumerate(tags, 1):
        converted_tags.append({
            "tag_id": f"T{idx:02d}",
            "name": tag["name"],
            "category": tag["category"],
            "weight": tag["weight"],
            "definition": tag["definition"],
            "positive_evidence_guide": "优先寻找候选人亲自执行的具体行为、项目、结果；有量化结果可判 QUANTIFIED_CASE，有完整行为案例可判 CASE。",
            "boundary": "仅出现能力名、技术名或自我评价而无具体行为时最多 WEAK；不存在支持事实则 NO_EVIDENCE。",
        })

    requirements = []
    for idx, req in enumerate(job.get("hard_requirements") or [], 1):
        if not isinstance(req, dict) or not req.get("name"):
            continue
        name = str(req["name"]).strip()
        req_type = _requirement_type(name)
        requirements.append({
            "requirement_id": f"R{idx:02d}",
            "name": name,
            "value": req.get("value"),
            "gate_type": "HARD_GATE" if str(req.get("verify_type", "soft")).lower() == "hard" else "VERIFY_ONLY",
            "requirement_type": req_type,
            "elasticity": req.get("elasticity"),
            "decision_policy": _decision_policy(req_type),
        })

    detail = job.get("description_detail") if isinstance(job.get("description_detail"), dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "job_name": str(job.get("job_name") or job.get("title") or "").strip(),
        "job_context": {
            "department": job.get("department"),
            "location": job.get("location"),
            "description": job.get("description"),
            "responsibilities": detail.get("responsibilities", []),
            "requirements_text": detail.get("requirements", []),
            "relevant_titles": job.get("relevant_titles", []),
            "relevant_majors": job.get("relevant_majors", []),
        },
        "tags": converted_tags,
        "requirements": requirements,
    }


def normalize_resume_facts(resume: dict[str, Any]) -> dict[str, Any]:
    """Create the role-neutral facts contract consumed by semantic evaluation.

    AI-derived `_mysql_*` annotations and sensitive structured attributes are
    intentionally excluded from the semantic contract.
    """
    candidate_id = str(resume.get("candidate_id") or resume.get("resume_id") or "").strip()
    if not candidate_id:
        raise ValueError("CANDIDATE_ID_MISSING")
    education = copy.deepcopy(resume.get("education") or {}) if isinstance(resume.get("education"), dict) else {}
    degree = str(resume.get("degree") or education.get("level") or "").strip()
    raw_text = _redact_sensitive_text(str(resume.get("raw_text") or ""))
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "name": str(resume.get("name") or "").strip(),
        "degree": degree,
        "education": education,
        "location": str(resume.get("location") or "").strip(),
        "expected_location": str(resume.get("expected_location") or "").strip(),
        "total_work_years": resume.get("total_work_years"),
        "current_company": resume.get("current_company"),
        "current_position": resume.get("current_position"),
        "work_experiences": copy.deepcopy(resume.get("work_experiences") or []),
        "projects": copy.deepcopy(resume.get("projects") or []),
        "certifications": copy.deepcopy(resume.get("certifications") or []),
        "skills": copy.deepcopy(resume.get("skills") or []),
        "industry_tags": copy.deepcopy(resume.get("industry_tags") or []),
        "raw_text": raw_text,
        "resume_created_at": resume.get("resume_created_at") or resume.get("created_at"),
        "resume_source": resume.get("resume_source") or resume.get("source"),
        "source_file": (resume.get("meta") or {}).get("filename") if isinstance(resume.get("meta"), dict) else resume.get("file"),
    }


def profile_to_weighted_tags(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Return legacy-compatible portrait_tags generated from a standard profile.

    This is a job-definition helper only; it never inspects a candidate.
    """
    raw = _normalize_weights(_flatten_legacy_tags({}, profile))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in raw:
        grouped.setdefault(item["category"], []).append({"name": item["name"], "weight": item["weight"]})
    return [{"category": category, "tags": items, "weight": round(sum(i["weight"] for i in items), 6)} for category, items in grouped.items()]
