"""JD 生成器：把招聘需求字段 + 标准岗位画像 + 相关 JD 模板合并成结构化 JD。

输入只来自用户明确提供的信息与公司标准画像，不做关键词猜测；相关 JD 模板
只用于措辞参考（``related_template.usage = "wording_reference_only"``），
不得把模板内容伪装成这次招聘的专属要求。

- ``build_request_jd(request_id, fields, profile=None, related_jd=None)``
  → 结构化 JD（``schema_version = "4.0"``）。
- ``render_jd_document(jd, external=False)`` → 可直接发布的 JD 文档
  （岗位职责 + 任职要求）。画像描述只进「岗位定位」，不冒充岗位职责；用户没给
  职责时用同岗位 JD 模板的职责措辞并标注「需确认」，不凭空编造。
  ``external=True`` 是对外发布版：省略内部部门/紧急程度/岗位定位与模板编号，
  职位名称与地点去掉公司标识（撰写规范与自检见 :mod:`rn.resume_screening.jd.quality`）。
- ``render_jd_summary(jd)`` → 给用人经理看的聊天摘要（不是 JD 本体）。
"""
from __future__ import annotations

import re
from typing import Any

REQUIREMENT_CATEGORIES = ("学历", "专业", "经验", "证书", "技能", "素养", "其他")

_LABELED_REQUIREMENT_RE = re.compile(
    r"^(学历|专业|经验|技能|证书|资质|素养|知识技能|职业素养|知识|素质"
    r"|年龄|性别|工作地点|其他)\s*[:：]\s*(.+)$"
)
_REQUIREMENT_LABELS = {
    "学历": "学历",
    "专业": "专业",
    "经验": "经验",
    "技能": "技能",
    "证书": "证书",
    "资质": "证书",
    "素养": "素养",
    "素质": "素养",
    "知识技能": "技能",
    "知识": "技能",
    "职业素养": "素养",
    "年龄": "其他",
    "性别": "其他",
    "工作地点": "其他",
    "其他": "其他",
}
_BULLET_PREFIX_RE = re.compile(r"^(?:[0-9]+[.、)）]|[-*•·]|[（(][0-9]+[)）])\s*")

# 类别判定顺序有讲究：证书先于经验（「应届可接受已报名待考」的资格证条款仍算证书），
# 学历先于专业（「大专及以上学历，…专业」拆开后各归其类）。
_CATEGORY_PATTERNS = (
    ("证书", re.compile(r"资格证|执业证|上岗证|证书|执照|持证")),
    ("学历", re.compile(r"学历|本科|硕士|博士|研究生|大专|专科|中专|统招|全日制")),
    ("专业", re.compile(r"专业")),
    ("经验", re.compile(r"经验|年限|工作经历|实习|见习|届别|应届|年以上|年及以上|\d+\s*年")),
    ("技能", re.compile(r"技能|熟练|掌握|操作|能力|软件|仪器")),
    ("素养", re.compile(r"责任心|细致|耐心|严谨|抗压|主动性|职业道德|敬业|服务意识|团队意识")),
)

#: 对外发布版要去掉的公司标识（职位名称/工作地点里出现时做清理）。
_COMPANY_TERMS = ("迪安诊断", "迪安", "集团总部", "本公司", "我司", "本企业")
_PUBLIC_LOCATION_DROP_RE = re.compile(r"(?:集团总部|集团|总部|公司|本企业)")


def sanitize_title(title: str) -> str:
    """把职位名称清成行业通用称谓：去掉公司/集团标识与多余括号。"""
    text = str(title or "").strip()
    for term in _COMPANY_TERMS:
        text = text.replace(term, "")
    text = re.sub(r"[（(]\s*[)）]", "", text)
    text = re.sub(r"[（(]([^（()）]{0,16})[)）]", r"\1", text)
    text = re.sub(r"^[\s\-—_/、，,]+|[\s\-—_/、，,]+$", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def public_location(location: Any) -> str:
    """对外发布的地点去掉「集团/总部/公司」这类内部组织词。"""
    text = str(location or "").strip()
    for term in _COMPANY_TERMS:
        text = text.replace(term, "")
    return _PUBLIC_LOCATION_DROP_RE.sub("", text).strip()


def _is_sensitive_value(value: str) -> bool:
    """对外发布的要求里不能出现年龄/性别/婚育等敏感信息。"""
    from rn.resume_screening.jd.quality import sensitive_hits

    return bool(sensitive_hits(value))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [x.strip(" -•\t") for x in re.split(r"[\n；;]", text) if x.strip(" -•\t")]


def _as_duty_list(value: Any) -> list[str]:
    """JD 模板里的职责是「1. 负责…；」这类拼好的文本，去掉编号与尾标点。"""
    duties = []
    for item in _as_list(value):
        text = _BULLET_PREFIX_RE.sub("", str(item)).strip("；;。. ")
        if text:
            duties.append(text)
    return duties


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


def _requirement_has_signal(text: str) -> bool:
    if _LABELED_REQUIREMENT_RE.match(text.strip()):
        return True
    return any(pattern.search(text) for _, pattern in _CATEGORY_PATTERNS)


def _split_requirement_clauses(text: str) -> list[str]:
    """把一条硬性条件拆成可归类的短句；不带类别线索的片段并回上一句。"""
    item = _BULLET_PREFIX_RE.sub("", str(text).strip()).strip()
    if not item:
        return []
    if _LABELED_REQUIREMENT_RE.match(item):
        return [item]
    parts = [part.strip() for part in re.split(r"[；;，,]", item) if part.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and not _requirement_has_signal(part):
            merged[-1] = merged[-1].rstrip("；;，,。. ") + "，" + part
        else:
            merged.append(part)
    return merged


def _classify_requirement_clause(text: str) -> tuple[str, str]:
    item = _BULLET_PREFIX_RE.sub("", str(text).strip()).strip("；;，,。. ")
    if not item:
        return "其他", ""
    labeled = _LABELED_REQUIREMENT_RE.match(item)
    if labeled:
        category = _REQUIREMENT_LABELS.get(labeled.group(1), "其他")
        value = labeled.group(2).strip("；;，,。. ")
    else:
        category, value = "其他", item
        for candidate, pattern in _CATEGORY_PATTERNS:
            if pattern.search(item):
                category = candidate
                break
    if category == "学历":
        # 「大专及以上学历」→「大专及以上」，避免行首「学历：」重复。
        value = re.sub(r"学历$", "", value).strip() or value
    return category, value


def requirement_groups(jd: dict[str, Any]) -> dict[str, list[str]]:
    """把结构化 JD 的硬性条件 + 本次需求给出的关键能力归到 JD 常用的要求分组。

    画像继承来的能力/经验只用于派生匹配标签，不写进 JD 的任职要求：任职要求必须
    是需求方真正提出的条件，否则「快速学习能力」「检验科经验」这类标签名会被误读
    成对外 JD 的任职要求（见 ``request_key_experience_skills``）。
    """
    groups: dict[str, list[str]] = {category: [] for category in REQUIREMENT_CATEGORIES}
    seen: set[str] = set()
    for entry in jd.get("hard_requirements") or []:
        if not isinstance(entry, dict) or entry.get("gate_type") != "HARD_GATE":
            continue
        for clause in _split_requirement_clauses(entry.get("name", "")):
            category, value = _classify_requirement_clause(clause)
            if not value or value in seen:
                continue
            seen.add(value)
            groups[category].append(value)
    # 兼容手写/旧版 JD：没有 request_key_experience_skills 时退回 key_experience_skills。
    request_skills = jd.get("request_key_experience_skills")
    if request_skills is None:
        request_skills = jd.get("key_experience_skills") or []
    for skill in request_skills:
        token = str(skill).strip()
        if token and token not in seen:
            seen.add(token)
            groups["技能"].append(token)
    return groups


def build_request_jd(request_id: str, fields: dict[str, Any], profile: dict[str, Any] | None = None,
                     related_jd: Any | None = None) -> dict[str, Any]:
    matched = bool(profile)
    title = str(fields.get("position_title") or (profile or {}).get("name") or "").strip()
    responsibilities = _as_list(fields.get("core_responsibilities"))
    user_skills = _as_list(fields.get("key_experience_skills"))
    hard = _as_list(fields.get("hard_requirements")) + _as_list(fields.get("extra_hard_requirements"))
    bonus = _as_list(fields.get("bonus_points"))

    profile_context = ""
    if matched:
        profile_context = str(profile.get("description") or profile.get("business_challenge") or "").strip()
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

    related_doc: dict[str, Any] = {
        "jd_id": getattr(related_jd, "jd_id", "") if related_jd else "",
        "title": getattr(related_jd, "title", "") if related_jd else "",
        "match_type": ("exact" if title.casefold() == str(getattr(related_jd, "title", "")).strip().casefold()
                       else "related") if related_jd else "none",
        "usage": "wording_reference_only",
    }
    if related_jd is not None:
        reference = {
            "responsibilities": _as_duty_list(getattr(related_jd, "core_responsibilities", "")),
            "requirements": str(getattr(related_jd, "requirements_text", "") or "").strip(),
        }
        if reference["responsibilities"] or reference["requirements"]:
            related_doc["reference"] = reference

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
        # 岗位职责只放用户明确给出的职责；画像描述进 profile_context，不冒充职责。
        "responsibilities": responsibilities,
        "profile_context": profile_context,
        # 画像能力/经验参与「匹配标签」推导；JD 任职要求只用需求方明确给出的关键能力。
        "key_experience_skills": skills,
        "request_key_experience_skills": user_skills,
        # 画像标签的原文（能力 5 / 经验 2 之类），聊天成稿的「匹配标签」直接用它，
        # 不再逐条罗列由 JD 职责派生出来的临时标签。
        "profile_abilities": profile_abilities,
        "profile_experiences": profile_experiences,
        "hard_requirements": requirements,
        "bonus_points": bonus,
        "profile_inherited": matched,
        "profile_id": (profile or {}).get("profile_id", "") if matched else "",
        "related_template": related_doc,
    }
    return jd


def _numbered(items: list[str]) -> list[str]:
    return [f"{index}. {text}" for index, text in enumerate(items, start=1)]


def _bulleted(items: list[str]) -> list[str]:
    return [f"- {text}" for text in items]


def _meta_lines(jd: dict[str, Any], *, external: bool = False) -> list[str]:
    if external:
        # 对外发布版不暴露内部部门、紧急程度与到岗时间。
        pairs = (
            ("工作地点", public_location(jd.get("location"))),
            ("招聘人数", f"{jd['headcount']} 人" if jd.get("headcount") else ""),
            ("薪资范围", jd.get("salary_range")),
            ("工作模式", jd.get("work_mode")),
            ("用工性质", jd.get("employment_type")),
        )
    else:
        pairs = (
            ("部门", jd.get("department")),
            ("工作地点", jd.get("location")),
            ("招聘人数", f"{jd['headcount']} 人" if jd.get("headcount") else ""),
            ("薪资范围", jd.get("salary_range")),
            ("工作模式", jd.get("work_mode")),
            ("用工性质", jd.get("employment_type")),
            ("紧急程度", jd.get("urgency")),
            ("到岗时间", jd.get("onboard_date") or jd.get("start_date")),
        )
    return [f"- {label}：{value}" for label, value in pairs if str(value or "").strip()]


def render_jd_document(jd: dict[str, Any], *, external: bool = False) -> str:
    """渲染可直接发布给候选人的 JD：岗位职责 + 任职要求（而不是筛选口径）。

    ``external=True`` 时按对外发布规范裁剪：职位名称与地点去公司标识，省略内部
    部门/紧急程度/岗位定位，模板参考不暴露内部 JD 编号。
    """
    raw_title = str(jd.get("title") or "招聘岗位").strip()
    title = sanitize_title(raw_title) if external else raw_title
    lines = [f"# {title or '招聘岗位'}", ""]
    meta = _meta_lines(jd, external=external)
    if meta:
        lines.extend(meta)
        lines.append("")

    related = jd.get("related_template") or {}
    reference = related.get("reference") or {}
    duties = [str(x).strip() for x in jd.get("responsibilities") or [] if str(x).strip()]
    template_duties = [str(x).strip() for x in reference.get("responsibilities") or [] if str(x).strip()]

    lines.append("## 岗位职责")
    if duties:
        lines.extend(_numbered(duties))
    elif template_duties:
        if external:
            note = "> 以下职责措辞参考公司同岗位 JD 模板，发布前需用人部门确认。"
        else:
            label = " ".join(part for part in (str(related.get("title") or ""), str(related.get("jd_id") or "")) if part)
            note = f"> 本次需求未提供具体职责；以下职责措辞参考公司同岗位 JD 模板（{label}），发布前需用人部门确认。"
        lines.append(note)
        lines.extend(_numbered(template_duties))
    else:
        lines.append("> 本次需求未提供具体岗位职责，请用人部门补充 3–5 条后再发布。")
    lines.append("")

    context = "" if external else str(jd.get("profile_context") or "").strip()
    if context:
        lines.extend([
            "## 岗位定位",
            "> 来自公司标准岗位画像，用于说明工作场景，不替代本次岗位职责。",
            "",
            context,
            "",
        ])

    groups = requirement_groups(jd)
    lines.append("## 任职要求")
    rendered = False
    for category in REQUIREMENT_CATEGORIES:
        values = groups.get(category) or []
        if external and category == "其他":
            # 对外发布版不披露年龄/性别/婚育等内部筛选条件。
            values = [value for value in values if not _is_sensitive_value(value)]
        if not values:
            continue
        rendered = True
        if category in ("技能", "素养"):
            label = "技能与能力" if category == "技能" else "职业素养"
            lines.append(f"- **{label}**：")
            lines.extend([f"  - {value}" for value in values])
        else:
            label = "其他硬性条件" if category == "其他" else category
            lines.append(f"- **{label}**：" + "；".join(values))
    if not rendered:
        if reference.get("requirements"):
            lines.append("> 本次需求未逐条给出任职要求，可参考同岗位 JD 模板的要求措辞，发布前需用人部门确认。")
        else:
            lines.append("> 本次需求未提供任职要求，请用人部门补充学历、专业、经验、证书等条件。")
    lines.append("")

    bonus = [str(x).strip() for x in jd.get("bonus_points") or [] if str(x).strip()]
    if bonus:
        lines.append("## 加分项")
        lines.extend(_bulleted(bonus))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_jd_summary(jd: dict[str, Any]) -> str:
    """聊天摘要：告诉用人经理 JD 已生成，完整 JD 见 ``render_jd_document`` 的产物。"""
    lines = [
        "### 📄 JD 已整理完成",
        f"**{jd.get('title') or '招聘岗位'}**｜{jd.get('location') or '地点待定'}｜HC {jd.get('headcount') or '-'}｜{jd.get('salary_range') or '薪资待定'}",
    ]
    if jd.get("profile_inherited"):
        lines.append("- 已使用公司标准岗位画像补全通用能力与经验要求。")
    if jd.get("responsibilities"):
        lines.append("- 核心职责：" + "；".join(jd["responsibilities"][:3]))
    elif jd.get("profile_context"):
        context = str(jd["profile_context"]).strip()
        lines.append("- 岗位定位（来自标准画像）：" + (context[:80] + "…" if len(context) > 80 else context))
    reference = (jd.get("related_template") or {}).get("reference") or {}
    if not jd.get("responsibilities") and reference.get("responsibilities"):
        lines.append("- 岗位职责：需求未提供，已按同岗位 JD 模板给出参考措辞，需确认。")
    tags = jd.get("screening_tag_names") or []
    if tags:
        rows = ["- " + "、".join(tags[i:i + 4]) for i in range(0, len(tags), 4)]
        lines.append(f"**本次全部匹配标签（{len(tags)}项）**\n" + "\n".join(rows))
    elif jd.get("key_experience_skills"):
        lines.append("- 关键能力/经验：" + "、".join(jd["key_experience_skills"]))
    hard = [x.get("name") for x in jd.get("hard_requirements") or [] if x.get("gate_type") == "HARD_GATE"]
    if hard:
        lines.append("- 额外硬要求：" + "；".join(hard[:5]))
    lines.append("\n完整 JD（岗位职责 + 任职要求）已写入 `jd.md`，可直接复制发布。")
    lines.append("\n回复 **“开始筛选”**，我会按这次招聘需求对相关候选人完成评估并推荐前三名；需要调整 JD，直接告诉我修改内容即可。")
    return "\n\n".join(lines)
