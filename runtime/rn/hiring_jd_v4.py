"""Generate request-specific structured JDs without exposing the JD library."""
from __future__ import annotations

import re
from typing import Any


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [x.strip(" -•\t") for x in re.split(r"[\n；;]", text) if x.strip(" -•\t")]


def _profile_items(profile: dict[str, Any] | None, key: str) -> list[str]:
    if not profile:
        return []
    items = []
    for item in profile.get(key) or []:
        if isinstance(item, str) and item.strip():
            items.append(item.strip())
        elif isinstance(item, dict) and item.get("name"):
            items.append(str(item["name"]).strip())
    return items


def build_request_jd(request_id: str, fields: dict[str, Any], profile: dict[str, Any] | None = None,
                     related_jd: Any | None = None) -> dict[str, Any]:
    matched = bool(profile)
    title = str(fields.get("position_title") or (profile or {}).get("name") or "").strip()
    user_responsibilities = _as_list(fields.get("core_responsibilities"))
    user_skills = _as_list(fields.get("key_experience_skills"))
    hard = _as_list(fields.get("hard_requirements")) + _as_list(fields.get("extra_hard_requirements"))
    bonus = _as_list(fields.get("bonus_points"))

    if matched:
        if not user_responsibilities:
            challenge = str(profile.get("description") or profile.get("business_challenge") or "").strip()
            if challenge:
                user_responsibilities = [challenge]
        profile_abilities = _profile_items(profile, "key_abilities")
        profile_experiences = _profile_items(profile, "key_experiences")
    else:
        profile_abilities, profile_experiences = [], []

    skills = list(dict.fromkeys([*user_skills, *profile_abilities, *profile_experiences]))
    requirements = []
    if fields.get("location"):
        requirements.append({"name": "工作地点", "value": fields["location"], "gate_type": "VERIFY_ONLY"})
    for item in hard:
        requirements.append({"name": item, "value": True, "gate_type": "HARD_GATE"})

    jd = {
        "schema_version": "4.0",
        "jd_id": f"JD-{request_id}",
        "request_id": request_id,
        "title": title,
        "department": fields.get("department", ""),
        "headcount": fields.get("headcount"),
        "location": fields.get("location", ""),
        "work_mode": fields.get("work_mode", ""),
        "salary_range": fields.get("salary_range", ""),
        "urgency": fields.get("urgency", ""),
        "employment_type": fields.get("employment_type", ""),
        "responsibilities": user_responsibilities,
        "key_experience_skills": skills,
        "hard_requirements": requirements,
        "bonus_points": bonus,
        "profile_inherited": matched,
        "profile_id": (profile or {}).get("profile_id", "") if matched else "",
        "related_template": {
            "jd_id": getattr(related_jd, "jd_id", "") if related_jd else "",
            "title": getattr(related_jd, "title", "") if related_jd else "",
            "match_type": "related" if related_jd else "none",
            "usage": "wording_reference_only",
        },
    }
    return jd


def render_jd_summary(jd: dict[str, Any]) -> str:
    lines = [
        "### 📄 JD 已整理完成",
        f"**{jd.get('title') or '招聘岗位'}**｜{jd.get('location') or '地点待定'}｜HC {jd.get('headcount') or '-'}｜{jd.get('salary_range') or '薪资待定'}",
    ]
    if jd.get("profile_inherited"):
        lines.append("- 已使用公司标准岗位画像补全通用能力与经验要求。")
    if jd.get("responsibilities"):
        lines.append("- 核心职责：" + "；".join(jd["responsibilities"][:3]))
    if jd.get("key_experience_skills"):
        lines.append("- 关键能力/经验：" + "、".join(jd["key_experience_skills"][:6]))
    hard = [x.get("name") for x in jd.get("hard_requirements") or [] if x.get("gate_type") == "HARD_GATE"]
    if hard:
        lines.append("- 额外硬要求：" + "；".join(hard[:5]))
    lines.append("\n回复 **“开始筛选”**，我会按这次招聘需求对相关候选人完成语义评分并推荐前三名；需要调整 JD，直接告诉我修改内容即可。")
    return "\n".join(lines)
