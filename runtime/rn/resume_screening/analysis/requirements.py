"""岗位要求 → 能力 / 经验 / 特质 三维度要求清单。

三个来源：

1. **政策 JSON**（默认 ``knowledge/recruiting/policies/inspection_technician_v1.json``）：
   岗位画像已按 capabilities / experiences / traits 三维度维护，是检验技术员岗位的
   权威要求来源；支持按画像（应届 / 社招）应用 ``profile_criterion_overrides``。
2. **JD 文本**：任意岗位的招聘要求文本，按行/分句拆成要求条目，用 ``analysis.cues``
   的三维词表归类到 capability / experience / trait，无词表线索时默认算 capability。
3. **生成 JD**（``requirements_from_jd``）：用人经理需求 → 标准画像 → 生成 JD 后拆标签。
   JD 的职责/关键能力/加分项拆成能力/经验标签（加分项记为「优先」）；学历/证书/地点/
   年龄等硬性条件进 ``hard_filters``，其中**年龄是需求方显式提出的硬性条件**，简历年龄
   不符合时匹配报告直接给「不推荐」。

输出统一为 JSON 可序列化 dict（requirements 文档），字段：

``{schema_version, source, job_name, role_name, policy_id, profile_id,
dimensions, dimension_counts, requirements[], excluded[], applied_overrides[], warnings[]}``

每条 requirement：

``{requirement_id, dimension, name, canonical_name, level, level_weight,
decision_mode, description, evidence_hints, keywords, strong_keywords,
criterion_id, source, applicable}``

这里只做「岗位侧」的工作：不读简历、不打分、不做任何录用判定。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rn.resume_screening.analysis import cues
from rn.resume_screening.paths import DEFAULT_POLICY_PATH

SCHEMA_VERSION = "resume-requirements/1.0"

DIMENSION_WEIGHTS: dict[str, float] = {
    cues.DIMENSION_CAPABILITY: 0.40,
    cues.DIMENSION_EXPERIENCE: 0.35,
}
# 特质不参与打分：requirements 里仍然产出 traits 条目（面试关注点），但 matching 只对
# SCORING_DIMENSIONS（能力/经验）加权。这里不再给 trait 权重，避免被误用回打分。
LEVEL_WEIGHTS: dict[str, float] = {"必须": 2.0, "优先": 1.0}
DEFAULT_LEVEL = "必须"
DEFAULT_DIMENSION = cues.DIMENSION_CAPABILITY

_POLICY_DIMENSION_KEYS: tuple[tuple[str, str], ...] = (
    (cues.DIMENSION_CAPABILITY, "capabilities"),
    (cues.DIMENSION_EXPERIENCE, "experiences"),
    (cues.DIMENSION_TRAIT, "traits"),
)

_HEADER_RE = re.compile(
    r"^[（(【\[]?\s*(岗位职责|工作职责|职责描述|任职要求|岗位要求|职位要求|任职资格|我们希望你|你需要具备|加分项|优先条件|工作内容)\s*[）)】\]]?\s*[:：]?\s*$"
)
_BULLET_RE = re.compile(r"^\s*(?:[-*•·●◦▪]|\(?\d{1,2}[.)、]|[（(]\d{1,2}[）)]|[一二三四五六七八九十]+[、.)])\s*")
_NOISE_RE = re.compile(r"(福利|五险一金|双休|包住|餐补|薪资待遇|简历投递|联系电话|上班时间|公司地址|欢迎投递|邮箱投递)")
_PREFERENCE_RE = re.compile(r"(优先|加分|更佳|最好|有.*者优先|为佳|plus)")

# 学历 / 专业 / 证书 / 年限 / 地点属于「硬性条件」，与政策 JSON 的 hard_filters 同义，
# 不参与三维覆盖度加权，只进 hard_filter_hints 供人工核验。
_EDUCATION_RE = re.compile(
    r"(学历|学位|本科|大专|专科|硕士|博士|研究生|中专|统招|全日制|相关专业|专业要求|专业背景|及以上学历|以上学历)"
)
_CERTIFICATION_RE = re.compile(r"(资格证|资格证书|执业证|上岗证|职称|检验师|检验士|检验技师|证书)")
_EXPERIENCE_YEARS_RE = re.compile(r"(\d+\s*年(?:以上)?(?:工作|相关)?(?:经验|经历)|工作年限\s*[:：]?\s*\d+|应届|实习期满)")
_LOCATION_RE = re.compile(r"(工作地点|工作城市|常驻地|base\s*地?|办公地点|工作地址)")
_EDU_DIMENSION = cues.DIMENSION_EXPERIENCE  # 学历/专业/证书归到「经验」维度下的背景项（只在未命中硬性条件时兜底）

# 年龄条件只从「需求方显式填写的硬性条件」里解析（生成 JD 的 hard_requirements），
# 不从自由 JD 文本里扫——JD 文本中的年龄行按敏感属性整行剔除。
_AGE_BETWEEN_RE = re.compile(r"(\d{1,2})\s*(?:[-—–~～至到]|周岁(?:至|到))\s*(\d{1,2})\s*(?:岁|周岁)?")
_AGE_MAX_RE = re.compile(r"(?:不超过|不大于|最多|限|年龄)?\s*(\d{1,2})\s*(?:岁|周岁)\s*(?:以下|以内|之内|之下)")
_AGE_NOT_OVER_RE = re.compile(r"(?:不超过|不大于|最多|限)\s*(\d{1,2})\s*(?:岁|周岁)")
_AGE_MIN_RE = re.compile(r"(\d{1,2})\s*(?:岁|周岁)\s*(?:以上|及以上|起步|起)")
_AGE_CONTEXT_RE = re.compile(r"(年龄|年纪|岁|周岁)")

# 只有出现明确的「性格/态度」表述才走特质维度：否则职责句里常见的「审核」「核对」
# 会被误判成「细心」（如「负责结果审核」是职责，不是性格自述）。
# 注意「稳定」不能单独作为性格词——JD 里「保证设备稳定运行」是职责描述。
_TRAIT_MARKER_RE = re.compile(
    r"(责任心|责任感|细心|耐心|认真|严谨|细致|踏实|性格稳定|工作稳定|稳定性强|"
    r"抗压|积极主动|主动性强|自驱|学习能力强|沟通能力强|沟通表达|团队协作|"
    r"合作精神|性格|态度|皮实|吃苦|性格开朗)"
)

# 性别 / 年龄 / 婚育 / 民族 / 宗教 / 户籍 / 外貌 / 健康等属于敏感属性，依法不得作为
# 筛选依据，也不得进入匹配度或推荐顺序（AGENTS.md 硬规则）。JD 里出现时整行剔除，
# 只记录到 sensitive_excluded 供人工注意，绝不生成要求条目。
_SENSITIVE_RE = re.compile(
    r"(性别|限男|限女|男性|女性|男士|女士|年龄|岁以[下上]|\d+\s*岁|已婚|未婚|已育|未育|"
    r"婚育|生育|民族|宗教|信仰|户籍|籍贯|身高|体重|外貌|形象好|相貌|健康|体检合格|"
    r"无犯罪|犯罪记录|服刑|传染)"
)

_HARD_LABEL_RE = re.compile(
    r"^(?:工作地点|工作城市|常驻地|base\s*地?|办公地点|工作地址|上班地点)\s*[:：]\s*"
)

_JD_TOKEN_SPLIT_RE = re.compile(r"[、，,；;/｜|（）()\s]+")
_JD_LEADING_VERB_RE = re.compile(r"^(负责|熟悉|参与|具备|具有|能够|能|掌握|了解|完成|从事|优先考虑|优先|要求|至少)\s*")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_level(value: Any) -> str:
    """Map free-text level onto ``必须`` / ``优先`` (unknown → 必须)."""
    text = _clean_text(value)
    if not text:
        return DEFAULT_LEVEL
    if any(token in text for token in ("优先", "加分", "最好", "更佳", "nice to have")):
        return "优先"
    return "必须"


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load a screening policy JSON (defaults to the inspection-technician policy)."""
    target = Path(path) if path else DEFAULT_POLICY_PATH
    if not target.is_file():
        raise FileNotFoundError(f"SCREENING_POLICY_NOT_FOUND:{target}")
    with target.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"SCREENING_POLICY_INVALID:{target}")
    return payload


def _keywords_for(
    name: str,
    evidence_hints: str,
    *,
    description: str = "",
    dimension: str = DEFAULT_DIMENSION,
    include_hints: bool = True,
    name_is_strong: bool = True,
) -> tuple[list[str], list[str]]:
    """Derive (keywords, strong_keywords) for one requirement.

    词表侧优先（保证与简历标签同一口径）；``include_hints=True`` 时再用
    evidence_hints 里逗号/顿号切分的短语补充弱关键词——注意 hints 是「核实提示」，
    对政策来源的判据来说它们会和别的判据撞词（例如 C13 的提示里有「室间质评」，
    会让做过室间质评的简历被误判为有试剂管理线索），所以政策来源一律关闭。

    ``name_is_strong=False`` 用于 JD 文本来源：JD 行截出来的 name 只是「要求描述」，
    不是术语，若当强关键词会让简历里出现同一字段名就自证命中（例如岗位名为
    「生物信息工程师」时，简历「求职意向：生物信息工程师」会直接拿满覆盖度）。
    """
    entry = cues.find_cue(dimension, name)
    keywords: list[str] = []
    strong: list[str] = []
    weak_only: list[str] = []

    def push(token: str, is_strong: bool) -> None:
        token = _clean_text(token)
        if len(token) < 2 or len(token) > 24:
            return
        if token in keywords:
            if is_strong and token not in strong:
                strong.append(token)
            return
        keywords.append(token)
        if is_strong:
            strong.append(token)

    if name:
        push(name, name_is_strong)
    if entry:
        for kw in entry.strong_keywords:
            push(kw, True)
        for kw in entry.weak_keywords:
            push(kw, False)
        for kw in entry.weak_only_keywords:
            push(kw, False)
            if kw in keywords:
                weak_only.append(kw)
    if include_hints:
        for chunk in re.split(r"[、,，;；/|｜]", _clean_text(evidence_hints)):
            chunk = re.sub(r"^(提及|提到|如|例如|包括)", "", chunk).strip()
            chunk = re.sub(r"(等|等等|等项)$", "", chunk).strip()
            push(chunk, False)
    if not keywords and description:
        for chunk in re.split(r"[、,，;；/|｜。]", description)[:6]:
            push(chunk, False)
    return keywords, strong, weak_only


def _requirement(
    *,
    requirement_id: str,
    dimension: str,
    name: str,
    level: str,
    decision_mode: str,
    description: str,
    evidence_hints: str,
    criterion_id: str,
    source: str,
    canonical_name: str = "",
    include_hints: bool = True,
    name_is_strong: bool = True,
    extra_keywords: tuple[str, ...] = (),
) -> dict[str, Any]:
    dim = dimension if dimension in cues.DIMENSIONS else DEFAULT_DIMENSION
    normalized_level = normalize_level(level)
    keywords, strong, weak_only = _keywords_for(
        name, evidence_hints, description=description, dimension=dim,
        include_hints=include_hints, name_is_strong=name_is_strong,
    )
    for token in extra_keywords:
        token = _clean_text(token)
        if len(token) >= 2 and token not in keywords:
            keywords.append(token)
    return {
        "requirement_id": requirement_id,
        "dimension": dim,
        "name": name,
        "canonical_name": canonical_name or name,
        "level": normalized_level,
        "level_weight": LEVEL_WEIGHTS.get(normalized_level, 1.0),
        "decision_mode": decision_mode or "resume_evidence",
        "description": description,
        "evidence_hints": evidence_hints,
        "keywords": keywords,
        "strong_keywords": strong,
        "weak_only_keywords": weak_only,
        "criterion_id": criterion_id,
        "source": source,
        "applicable": True,
    }


def _apply_profile_overrides(
    requirements: list[dict[str, Any]],
    policy: dict[str, Any],
    profile_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    overrides = (policy.get("profile_criterion_overrides") or {}).get(profile_id) if profile_id else None
    if not isinstance(overrides, dict) or not overrides:
        return requirements, [], []
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    for row in requirements:
        patch = overrides.get(str(row.get("name") or ""))
        if isinstance(patch, dict) and patch.get("applicable") is False:
            excluded.append({
                "requirement_id": row["requirement_id"],
                "name": row["name"],
                "dimension": row.get("dimension"),
                "reason": patch.get("override_reason") or "画像未包含该要求",
            })
            continue
        if isinstance(patch, dict):
            for key in ("level", "decision_mode", "description", "evidence_hints"):
                if key in patch:
                    row[key] = patch[key]
            if patch.get("level"):
                row["level_weight"] = LEVEL_WEIGHTS.get(normalize_level(patch["level"]), 1.0)
            row["level"] = normalize_level(row.get("level"))
            row["source"] = f"profile_override:{profile_id}"
            if patch.get("override_reason"):
                row["override_reason"] = patch["override_reason"]
            keywords, strong, weak_only = _keywords_for(
                row["name"], row.get("evidence_hints") or "",
                description=row.get("description") or "", dimension=row["dimension"],
                include_hints=False,
            )
            row["keywords"], row["strong_keywords"], row["weak_only_keywords"] = keywords, strong, weak_only
            applied.append({
                "requirement_id": row["requirement_id"],
                "name": row["name"],
                "level": row["level"],
                "decision_mode": row.get("decision_mode"),
            })
        kept.append(row)
    return kept, excluded, applied


def requirements_from_policy(
    policy: dict[str, Any] | None = None,
    *,
    profile_id: str = "",
) -> dict[str, Any]:
    """Build the three-dimension requirement list from a screening policy JSON."""
    data = policy if isinstance(policy, dict) else load_policy(policy)
    requirements: list[dict[str, Any]] = []
    counter = 0
    for dimension, key in _POLICY_DIMENSION_KEYS:
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            counter += 1
            name = _clean_text(item.get("name"))
            if not name:
                continue
            criterion_id = _clean_text(item.get("criterion_id") or f"C{counter:02d}")
            requirements.append(_requirement(
                requirement_id=criterion_id,
                dimension=dimension,
                name=name,
                level=item.get("level") or DEFAULT_LEVEL,
                decision_mode=_clean_text(item.get("decision_mode")),
                description=_clean_text(item.get("description")),
                evidence_hints=_clean_text(item.get("evidence_hints")),
                criterion_id=criterion_id,
                source="policy",
                include_hints=False,
            ))
    excluded: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    if profile_id:
        requirements, excluded, applied = _apply_profile_overrides(requirements, data, profile_id)
    doc = _document(
        requirements,
        source="policy",
        policy_id=_clean_text(data.get("policy_id")),
        profile_id=profile_id,
        role_name=_clean_text(data.get("role_name")),
        job_name=_clean_text(data.get("role_name")),
    )
    doc["excluded"] = excluded
    doc["applied_overrides"] = applied
    doc["hard_filters"] = data.get("hard_filters") or {}
    doc["policy_name"] = _clean_text(data.get("policy_name"))
    doc["supported_titles"] = list(data.get("supported_titles") or [])
    doc["supported_profile_ids"] = list(data.get("supported_profile_ids") or [])
    return doc


def split_jd_lines(text: str) -> list[str]:
    """Split JD free text into candidate requirement lines (bullets/分句/长句拆分)."""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in raw.split("\n"):
        line = line.replace("\u3000", " ").strip()
        if not line:
            continue
        if _HEADER_RE.match(line):
            continue
        line = _BULLET_RE.sub("", line).strip()
        if not line:
            continue
        pieces = [line]
        # 分号是 JD 列表里最常见的分句符：必须先拆，否则一行里的敏感属性会把
        # 同一行的正常要求一起带走（「年龄35岁以下；能独立完成上机检测」）。
        if re.search(r"[；;]", line):
            pieces = [p for p in re.split(r"[；;]", line) if p.strip()]
        if len(line) > 80 and re.search(r"[。]", line):
            expanded: list[str] = []
            for piece in pieces:
                expanded.extend([p for p in re.split(r"[。]", piece) if p.strip()])
            pieces = expanded
        for piece in pieces:
            piece = _BULLET_RE.sub("", piece).strip()
            piece = _clean_text(piece).rstrip("。；;:：,，")
            if len(piece) < 4 or _NOISE_RE.search(piece):
                continue
            lines.append(piece)
    seen: set[str] = set()
    deduped: list[str] = []
    for line in lines:
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(line)
    return deduped


def is_sensitive_requirement(line: str) -> bool:
    """Whether a JD line states a legally protected attribute (性别/年龄/婚育/民族…).

    这类条件必须整行剔除：既不能作为能力/经验/特质参与覆盖度，也不能进入推荐顺序。
    """
    return bool(_SENSITIVE_RE.search(_clean_text(line)))


def _hard_requirement_kinds(line: str) -> list[str]:
    """Classify a JD line into every hard-condition bucket it touches.

    一行常同时写学历、专业、证书（「大专及以上学历，医学检验技术专业，持有检验士资格证」），
    所以这里必须返回全部命中项，不能早退只返回第一个——否则学历条件会整条消失。
    """
    text = _clean_text(line)
    kinds: list[str] = []
    if _CERTIFICATION_RE.search(text):
        kinds.append("certification")
    if _EDUCATION_RE.search(text):
        kinds.append("education")
    if _LOCATION_RE.search(text):
        kinds.append("location")
    return kinds


def _hard_filter_value(kind: str, line: str) -> str:
    """Normalize a hard-condition line before storing it in ``hard_filters``."""
    text = _clean_text(line)
    if kind == "location":
        # 去掉「工作地点：」这类标签，否则 matching 会拿整串做包含比较，永远匹配不上城市。
        return _HARD_LABEL_RE.sub("", text).strip()
    return text


def _jd_keyword_chunks(line: str) -> tuple[str, ...]:
    """Derive weak match keywords from a JD line that scored no canonical cue.

    JD 文本没有词表条目时，用标点切分出短语作为弱关键词，保证「JD 要求」和
    「简历文字」仍能逐字对齐；强关键词只来自词表，避免把普通描述当成已证实。
    """
    chunks: list[str] = []
    for raw in _JD_TOKEN_SPLIT_RE.split(_clean_text(line)):
        token = _JD_LEADING_VERB_RE.sub("", raw).strip()
        token = re.sub(r"(者优先|优先考虑|优先|等)$", "", token).strip()
        if 2 <= len(token) <= 14 and token not in chunks:
            chunks.append(token)
    return tuple(chunks)


def _classify_dimension(line: str) -> str:
    """Classify one JD line into a dimension (trait > experience > capability)."""
    text = _clean_text(line)
    if _TRAIT_MARKER_RE.search(text):
        return cues.DIMENSION_TRAIT
    trait_hits = cues.match_cues(text, dimensions=(cues.DIMENSION_TRAIT,))
    if any(row["strong"] for row in trait_hits):
        return cues.DIMENSION_TRAIT
    # 「项目」单独出现多半是「常规项目」「检测项目」，不能当成项目经验；
    # 只认「项目经验／项目经历／科研项目」这类明确表述。
    if re.search(r"(经验|经历|年限|年以上|工作过|从业|带教|培训|实习|项目经验|项目经历|科研项目|毕业项目|课题|背景|证书|资格)", text):
        return cues.DIMENSION_EXPERIENCE
    if cues.match_cues(text, dimensions=(cues.DIMENSION_EXPERIENCE,)):
        return cues.DIMENSION_EXPERIENCE
    if _EDUCATION_RE.search(text) or _CERTIFICATION_RE.search(text):
        return _EDU_DIMENSION
    return DEFAULT_DIMENSION


_TAG_LEAD_VERB_RE = re.compile(
    r"^(?:负责|执行|完成|参与|协助|从事|开展|进行|按照|根据|依据|依照|遵照|"
    r"严格|认真|及时|主动|熟练|熟悉|掌握|具备|具有|能够|能|可|会|有)\s*"
)
_TAG_TAIL_RE = re.compile(r"(?:（优先）|者优先|优先考虑|优先|等)\s*$")


def _condense_tag_name(text: str, limit: int = 12) -> str:
    """Turn a JD sentence that matched no vocabulary cue into a short tag label.

    聊天成稿与标签表都只该出现「常规项目检测」这样的短语，不能整句职责原文；
    词表命中时优先用词表名，这里只兜底没命中的描述句。
    """
    name = _clean_text(text).strip("。；;，,、 ")
    name = _TAG_LEAD_VERB_RE.sub("", name)
    name = _TAG_TAIL_RE.sub("", name).strip()
    if len(name) <= limit:
        return name
    parts = [part.strip("。；;，,、 ") for part in re.split(r"[、，,/；;]", name)]
    parts = [part for part in parts if part]
    if len(parts) >= 2:
        short = "、".join(parts[:2])
        if len(short) <= limit:
            return short
    return name[:limit] + "…"


def requirements_from_jd_text(text: str, *, job_name: str = "") -> dict[str, Any]:
    """Build a three-dimension requirement list from arbitrary JD free text.

    学历 / 证书 / 工作地点这类硬性条件不参与三维覆盖度加权，统一收进
    ``hard_filters``，由 matching 的 ``hard_filter_hints`` 逐条核验。
    """
    lines = split_jd_lines(text)
    requirements: list[dict[str, Any]] = []
    hard: dict[str, list[str]] = {"education": [], "certification": [], "location": []}
    sensitive_excluded: list[str] = []
    for line in lines[:40]:
        if is_sensitive_requirement(line):
            # 敏感属性整行剔除，不生成任何要求（也不能进推荐顺序）。
            if line not in sensitive_excluded:
                sensitive_excluded.append(line)
            continue
        kinds = _hard_requirement_kinds(line)
        if kinds:
            for kind in kinds:
                value = _hard_filter_value(kind, line)
                if value and value not in hard[kind]:
                    hard[kind].append(value)
            continue
        dimension = _classify_dimension(line)
        canonical = cues.canonicalize(line, dimension=dimension)
        # 词表命中就用词表名；没命中的描述句压成短标签，绝不让整句职责进标签名。
        name = canonical or _condense_tag_name(line)
        level = "优先" if _PREFERENCE_RE.search(line) else DEFAULT_LEVEL
        entry = cues.find_cue(dimension, canonical) if canonical else None
        hint_source = "、".join(entry.all_keywords()) if entry else ""
        requirements.append(_requirement(
            requirement_id=f"J{len(requirements) + 1:02d}",
            dimension=dimension,
            name=name,
            level=level,
            decision_mode="resume_evidence",
            description=line,
            evidence_hints=hint_source,
            criterion_id=f"J{len(requirements) + 1:02d}",
            source="jd_text",
            canonical_name=canonical or name,
            include_hints=False,
            name_is_strong=False,
            extra_keywords=_jd_keyword_chunks(line),
        ))
    merged: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in requirements:
        key = (row["dimension"], row["canonical_name"])
        if key in by_key:
            existing = by_key[key]
            existing["description"] = f"{existing['description']}；{row['description']}"
            if row["level"] == "必须":
                existing["level"] = "必须"
                existing["level_weight"] = LEVEL_WEIGHTS["必须"]
            for kw in row["keywords"]:
                if kw not in existing["keywords"]:
                    existing["keywords"].append(kw)
            continue
        by_key[key] = row
        merged.append(row)
    for index, row in enumerate(merged, start=1):
        row["requirement_id"] = f"J{index:02d}"
    doc = _document(merged, source="jd_text", job_name=job_name or "JD 文本岗位")
    doc["hard_filters"] = {key: "；".join(values) for key, values in hard.items() if values}
    doc["sensitive_excluded"] = sensitive_excluded
    if sensitive_excluded:
        doc["warnings"].append(
            "JD 中含性别/年龄/婚育等敏感属性条件 %d 条，已整行剔除，不参与匹配度与推荐顺序（依法不得作为筛选依据）。"
            % len(sensitive_excluded)
        )
    if doc["hard_filters"]:
        doc["warnings"].append(
            "学历/证书/地点类硬性条件已归入 hard_filters，不参与三维覆盖度加权，需人工核验证件。"
        )
    if not merged:
        if doc["hard_filters"]:
            doc["warnings"].append(
                "JD 只解析出学历/证书/地点等硬性条件，没有可归入三维度的要求；匹配报告将只做硬性条件核验。"
            )
        else:
            doc["warnings"].append("JD 文本未解析出任何要求条目：请检查文本是否为空或格式异常。")
    return doc


def _as_text_list(value: Any) -> list[str]:
    """Normalize a JD list field (responsibilities / skills / bonus_points) into strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for row in value:
            if isinstance(row, dict):
                text = _clean_text(row.get("name") or row.get("value") or "")
            else:
                text = _clean_text(row)
            if text:
                items.append(text)
        return items
    text = _clean_text(value)
    if not text:
        return []
    return [part.strip(" -•\t") for part in re.split(r"[\n；;]", text) if part.strip(" -•\t")]


def parse_age_requirement(text: str) -> tuple[int | None, int | None]:
    """Parse an explicit age condition into ``(min_age, max_age)`` (``None`` side = 不限)。

    只接受带年龄/岁语境的文本（「年龄 25-35 岁」「35 岁以下」「28 岁以上」「不超过 35 周岁」），
    避免把「2019-2022 年」这类年份区间误判成年龄条件。
    """
    raw = _clean_text(text)
    if not raw or not _AGE_CONTEXT_RE.search(raw):
        return None, None
    match = _AGE_BETWEEN_RE.search(raw)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        if 0 < low <= high <= 100:
            return low, high
    match = _AGE_NOT_OVER_RE.search(raw)
    if match:
        high = int(match.group(1))
        if 0 < high <= 100:
            return None, high
    match = _AGE_MAX_RE.search(raw)
    if match:
        high = int(match.group(1))
        if 0 < high <= 100:
            return None, high
    match = _AGE_MIN_RE.search(raw)
    if match:
        low = int(match.group(1))
        if 0 < low <= 100:
            return low, None
    return None, None


def _hard_items_from_jd(jd: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Flatten ``jd["hard_requirements"]`` into ``(name, value, gate_type)`` tuples."""
    items: list[tuple[str, str, str]] = []
    for row in jd.get("hard_requirements") or []:
        if isinstance(row, dict):
            name = _clean_text(row.get("name"))
            value = _clean_text(row.get("value"))
            gate = _clean_text(row.get("gate_type")) or "HARD_GATE"
        else:
            name, value, gate = _clean_text(row), "", "HARD_GATE"
        if name:
            items.append((name, value, gate))
    return items


_KSAO_TAG_LABEL_RE = re.compile(r"^(专业|经验|技能|职业素养|能力|知识)\s*[:：]\s*(.+)$")
# 带 KSAO 标签的硬条件其实描述的是能力/经验/素养，应当转成可计分标签；
# 「专业」例外：并入 education 硬性条件，用简历学历段里的专业自动核验。
_KSAO_TAG_LABELS = ("经验", "技能", "职业素养", "能力", "知识")


def requirements_from_jd(jd: dict[str, Any]) -> dict[str, Any]:
    """把生成 JD 拆成「评分标签 + 硬性条件」的岗位要求文档。

    - 职责 / 关键能力 → 能力或经验标签（沿用 JD 文本的启发式归类）；
    - 加分项 → 「优先」标签；
    - 学历 / 证书 / 地点 / 年龄 → ``hard_filters``（年龄由需求方显式提出，简历不符即不推荐）；
    - 其他硬要求（如「接受体检季加班」）→ ``hard_filters["other"]`` 逐条核验，不计入命中率分母。
    """
    jd = jd if isinstance(jd, dict) else {}
    title = _clean_text(jd.get("title"))
    responsibilities = _as_text_list(jd.get("responsibilities"))
    skills = _as_text_list(jd.get("key_experience_skills"))
    bonus = _as_text_list(jd.get("bonus_points"))

    hard_buckets: dict[str, list[str]] = {}
    ksa_tag_text: list[str] = []

    def push_bucket(kind: str, text: str) -> None:
        text = _clean_text(text)
        if text and text not in hard_buckets.setdefault(kind, []):
            hard_buckets[kind].append(text)

    for name, value, gate in _hard_items_from_jd(jd):
        if name in ("工作地点", "工作城市", "上班地点", "办公地点", "常驻地"):
            push_bucket("location", value or name)
            continue
        age_range = parse_age_requirement(name)
        if age_range != (None, None):
            push_bucket("age", name)
            continue
        kinds = _hard_requirement_kinds(name)
        if kinds:
            for kind in kinds:
                push_bucket(kind, _hard_filter_value(kind, name))
            continue
        labeled = _KSAO_TAG_LABEL_RE.match(name)
        if labeled and labeled.group(1) in _KSAO_TAG_LABELS:
            ksa_tag_text.append(labeled.group(2).strip())
            continue
        if labeled and labeled.group(1) == "专业":
            # 专业条件并入 education：matching 会用简历学历段里的专业做自动核验，
            # 否则它只会落到 other 里永远「待核实」。
            push_bucket("education", name)
            continue
        if gate == "VERIFY_ONLY" and "地点" in name:
            push_bucket("location", name)
            continue
        # 非学历/证书/地点/年龄的其他硬要求：单列核验，不作为可计分标签（简历通常不写「接受加班」这类意愿）。
        push_bucket("other", name)

    body = [*responsibilities, *skills, *ksa_tag_text]
    body.extend(f"{line}（优先）" for line in bonus)
    doc = requirements_from_jd_text("\n".join(body), job_name=title or "生成 JD")
    # 特质不进标签：JD 的「职业素养」仍留在可发布 JD 的任职要求里，只作面试参考，
    # 不生成「特质标签」（jd.tags.md 与聊天成稿都只列能力/经验）。
    doc["requirements"] = [
        row for row in doc["requirements"] if row.get("dimension") != cues.DIMENSION_TRAIT
    ]
    doc["dimension_counts"] = {
        dim: sum(1 for row in doc["requirements"] if row.get("dimension") == dim)
        for dim in cues.DIMENSIONS
    }

    for kind, values in hard_buckets.items():
        # JD 里显式写出的硬性条件优先：正文（职责/关键能力）推导出的同类条件可能只是
        # 能力名里带了「专业」二字（如「专业背景与项目经验」），混进来会污染学历条件。
        doc["hard_filters"][kind] = "；".join(values)
    if title and jd.get("location") and not doc["hard_filters"].get("location"):
        doc["hard_filters"]["location"] = _clean_text(jd.get("location"))

    doc["source"] = "jd"
    doc["jd_id"] = _clean_text(jd.get("jd_id"))
    doc["profile_id"] = _clean_text(jd.get("profile_id"))
    # 结构化 JD 不做敏感行剔除：年龄是否成为筛选条件由需求方显式提出（写入 hard_filters.age），
    # 其余敏感属性仍然不会从 JD 职责/能力文本里生成要求。
    doc["sensitive_excluded"] = []
    doc["warnings"] = [
        warning for warning in (doc.get("warnings") or [])
        if "敏感" not in str(warning) and "JD 文本未解析" not in str(warning)
    ]
    if not doc["requirements"]:
        if doc["hard_filters"]:
            doc["warnings"].append(
                "该 JD 只包含学历/证书/地点等硬性条件，没有可归入三维度的标签；报告只做硬性条件核验。"
            )
        else:
            doc["warnings"].append("生成 JD 没有解析出任何要求标签：请检查需求字段（职责/关键能力/硬性条件）。")
    if doc["hard_filters"].get("age"):
        doc["warnings"].append(
            "年龄是需求方显式提出的硬性条件：简历年龄不符合时报告结论为「不推荐」；"
            "请确认该条件符合适用地区的反歧视法规。"
        )
    return doc


def _document(
    requirements: list[dict[str, Any]],
    *,
    source: str,
    job_name: str = "",
    role_name: str = "",
    policy_id: str = "",
    profile_id: str = "",
) -> dict[str, Any]:
    counts = {dim: 0 for dim in cues.DIMENSIONS}
    for row in requirements:
        counts[row["dimension"]] = counts.get(row["dimension"], 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "job_name": job_name,
        "role_name": role_name,
        "policy_id": policy_id,
        "profile_id": profile_id,
        "dimensions": list(cues.DIMENSIONS),
        "dimension_labels": dict(cues.DIMENSION_LABELS),
        "dimension_weights": dict(DIMENSION_WEIGHTS),
        "dimension_counts": counts,
        "requirements": requirements,
        "excluded": [],
        "applied_overrides": [],
        "hard_filters": {},
        "sensitive_excluded": [],
        "warnings": [],
    }


def policy_supports(job: str, policy: dict[str, Any]) -> bool:
    """Whether a job name/title is covered by the policy JSON."""
    target = _clean_text(job)
    if not target:
        return False
    titles = [str(t).strip() for t in (policy.get("supported_titles") or []) if str(t).strip()]
    role = _clean_text(policy.get("role_name"))
    if role:
        titles.append(role)
    return any(title and title in target for title in titles)


class RequirementsUnavailableError(ValueError):
    """没有任何可用的岗位要求：无法评估匹配度，调用方应拒绝出分。"""


def has_usable_requirements(doc: dict[str, Any]) -> bool:
    """是否有任何可用于评估的岗位要求。

    三维度条目和学历/证书/地点等硬性条件都算「有要求」：只有硬性条件的 JD
    不是空要求，匹配报告会改为只做硬性条件核验。
    """
    if doc.get("requirements"):
        return True
    hard = doc.get("hard_filters")
    return isinstance(hard, dict) and any(str(value).strip() for value in hard.values())


def ensure_requirements(doc: dict[str, Any], strict: bool) -> dict[str, Any]:
    """Raise :class:`RequirementsUnavailableError` when ``strict`` and nothing usable parsed."""
    if strict and not has_usable_requirements(doc):
        detail = "；".join(str(item) for item in (doc.get("warnings") or []) if str(item).strip())
        raise RequirementsUnavailableError(detail or "岗位要求为空，无法评估匹配度")
    return doc


def build_job_requirements(
    *,
    job: str = "",
    job_text: str = "",
    policy: dict[str, Any] | str | Path | None = None,
    profile_id: str = "",
    strict: bool = False,
) -> dict[str, Any]:
    """Unified entry: policy-based when the job matches a policy, else JD text.

    ``strict=True`` 时空要求直接抛 :class:`RequirementsUnavailableError`
    （CLI / 上传接口用），避免「0 条要求 → 0.0% 低匹配」这种误导性结论。
    """
    if job_text and job_text.strip():
        doc = requirements_from_jd_text(job_text, job_name=job)
        doc["profile_id"] = profile_id
        return ensure_requirements(doc, strict)
    data = policy if isinstance(policy, dict) else load_policy(policy)
    if policy_supports(job, data):
        return ensure_requirements(requirements_from_policy(data, profile_id=profile_id), strict)
    if _clean_text(job):
        # 岗位名不在政策覆盖范围内：绝不把岗位名本身当 JD 文本解析——那会造出一条
        # 「要求 == 岗位名」的假要求，简历只要写了求职意向就命中，凭空拿到 100%。
        # 这里返回空要求清单 + 明确警告，由调用方拒绝出分或要求补充 JD。
        doc = _document([], source="unknown_job", job_name=job)
        doc["profile_id"] = profile_id
        doc["warnings"].append(
            f"岗位「{job}」不在政策覆盖范围内，且未提供 JD 文本，无法生成岗位要求；"
            "请用 --job-text 或 --jd-file 提供完整 JD。"
        )
        return ensure_requirements(doc, strict)
    return ensure_requirements(requirements_from_policy(data, profile_id=profile_id), strict)


HARD_FILTER_LABELS: dict[str, str] = {
    "education": "学历",
    "certification": "证书",
    "location": "工作地点",
    "age": "年龄",
    "other": "其他硬性条件",
}

_HARD_FILTER_ACTIONS: dict[str, str] = {
    "education": "不满足 → 不推荐；未写明 → 待核实",
    "certification": "不满足 → 不推荐；未写明 → 待核实",
    "location": "不满足 → 不推荐；未写明 → 待核实",
    "age": "不满足 → 不推荐（需求方显式提出）",
    "other": "不进命中率，逐条核验（简历未写明 → 待核实）",
}


def _keyword_preview(row: dict[str, Any], limit: int = 4) -> str:
    keywords = [str(token) for token in (row.get("keywords") or []) if str(token).strip()]
    if not keywords:
        return ""
    preview = "、".join(keywords[:limit])
    return preview + "…" if len(keywords) > limit else preview


_TAG_LABEL_MAX = 24
_HARD_LABEL_PREFIX_RE = re.compile(
    r"^(?:学历|专业|经验|技能|证书|资质|职业素养|年龄|性别|工作地点|工作城市|上班地点|办公地点|地点)\s*[:：]\s*"
)


def _tag_label(row: dict[str, Any]) -> str:
    """Compact tag label for tables (long JD sentences get truncated, 优先 suffix dropped)."""
    name = str(row.get("name") or "").strip()
    while True:
        # 「…经验者优先（优先）」要连剥两层后缀
        stripped = re.sub(r"(?:（优先）|者优先|优先考虑|优先)$", "", name).strip()
        if stripped == name:
            break
        name = stripped
    return name if len(name) <= _TAG_LABEL_MAX else name[:_TAG_LABEL_MAX] + "…"


def tag_display_name(name: str) -> str:
    """Public compact form of a tag name, for chat views that only list names."""
    return _tag_label({"name": name})


def _hard_value(text: str) -> str:
    """Strip the category label already shown in the 类别 column (「学历：」)."""
    return _HARD_LABEL_PREFIX_RE.sub("", str(text or "").strip())


def render_requirements_markdown(doc: dict[str, Any]) -> str:
    """Render a requirements document: scoring tags per dimension + hard filters.

    The tag tables are the「依据 JD 拆出来的评分标签」view: capability/experience
    rows form the hit-rate denominator, trait rows are display-only, and hard
    filters are verified separately (never in the denominator).
    """
    lines: list[str] = []
    title = doc.get("job_name") or doc.get("role_name") or "岗位"
    source = str(doc.get("source") or "")
    if source == "jd":
        # 标签是从 JD 派生出来的第二层，标题必须写明「取自 JD」，不能和 JD 的任职要求混在一起。
        lines.append(f"## 匹配标签（取自 JD：{title}，用于匹配候选人能力与经验）")
    else:
        lines.append(f"## 岗位要求（{title}）")
    label = {
        "policy": "政策画像",
        "jd_text": "JD 文本",
        "jd": "生成 JD（需求字段 + 标准画像）",
        "unknown_job": "未识别岗位（无要求）",
    }.get(source, source)
    lines.append("")
    lines.append(f"- 要求来源：{label}")
    if source == "jd":
        lines.append("- 派生关系：JD 取自标准画像 + 本次实际需求；本文件的评分标签取自该 JD 的任职要求与关键能力")
    if doc.get("profile_id"):
        lines.append(f"- 适用画像：{doc['profile_id']}")
    if doc.get("jd_id"):
        lines.append(f"- JD 编号：{doc['jd_id']}")
    requirements = doc.get("requirements") or []
    scored = [
        row for row in requirements
        if row.get("dimension") in (cues.DIMENSION_CAPABILITY, cues.DIMENSION_EXPERIENCE)
    ]
    lines.append(
        f"- 计分标签：能力 {sum(1 for row in requirements if row.get('dimension') == cues.DIMENSION_CAPABILITY)} 条"
        f" + 经验 {sum(1 for row in requirements if row.get('dimension') == cues.DIMENSION_EXPERIENCE)} 条"
        f" = {len(scored)} 条（命中率分母）"
    )
    lines.append("")
    lines.append(
        "> 内部筛选视图：能力/经验标签计入命中率，特质不生成标签（「职业素养」只留在 JD 任职要求里作面试参考），"
        "硬性条件单独核验；不要把它并入对外发布的 JD。"
    )
    lines.append("")
    for dim in cues.DIMENSIONS:
        rows = [r for r in requirements if r.get("dimension") == dim]
        if not rows:
            continue
        if dim == cues.DIMENSION_TRAIT:
            lines.append(f"### {cues.DIMENSION_LABELS[dim]}（{len(rows)} 条，只展示、不计分）")
        else:
            lines.append(f"### {cues.DIMENSION_LABELS[dim]}（{len(rows)} 条）")
        lines.append("")
        lines.append("| 编号 | 标签 | 级别 | 判据方式 | 简历命中线索 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in rows:
            lines.append(
                f"| {row.get('requirement_id','')} | {_tag_label(row)} "
                f"| {row.get('level','')} | {row.get('decision_mode','')} "
                f"| {_keyword_preview(row)} |"
            )
        lines.append("")
    hard = {key: value for key, value in (doc.get("hard_filters") or {}).items() if str(value or "").strip()}
    if hard:
        lines.append("### 硬性条件（不进命中率，单独核验）")
        lines.append("")
        lines.append("| 类别 | 条件 | 判定 |")
        lines.append("| --- | --- | --- |")
        for key in ("education", "certification", "location", "age", "other"):
            value = hard.get(key)
            if not str(value or "").strip():
                continue
            action = _HARD_FILTER_ACTIONS.get(key, "逐条核验")
            if key == "other":
                # 其他硬性条件常是多条（专业/经验/技能/职业素养），一行写在一起会糊掉。
                segments = [seg for seg in re.split(r"[；;]", str(value)) if seg.strip()]
                for index, segment in enumerate(segments or [str(value)]):
                    category = HARD_FILTER_LABELS[key] if index == 0 else "—"
                    lines.append(f"| {category} | {_hard_value(segment)} | {action} |")
                continue
            lines.append(f"| {HARD_FILTER_LABELS.get(key, key)} | {_hard_value(value)} | {action} |")
        lines.append("")
    excluded = doc.get("sensitive_excluded") or []
    if excluded:
        lines.append("### 已剔除（敏感属性，不参与匹配）")
        lines.append("")
        for row in excluded:
            lines.append(f"- {row}")
        lines.append("")
    lines.append(
        "> 这些标签用于把简历文字与 JD 要求逐条对齐：能力/经验命中进命中率，"
        "特质只作面试参考，硬性条件单独核验（证件/学历/年龄需人工复核）。"
    )
    warnings = [str(w) for w in (doc.get("warnings") or []) if str(w).strip()]
    if warnings:
        lines.append("")
        for warning in warnings:
            lines.append(f"- 提示：{warning}")
    return "\n".join(lines)


__all__ = [
    "DIMENSION_WEIGHTS",
    "LEVEL_WEIGHTS",
    "RequirementsUnavailableError",
    "SCHEMA_VERSION",
    "build_job_requirements",
    "has_usable_requirements",
    "is_sensitive_requirement",
    "load_policy",
    "normalize_level",
    "parse_age_requirement",
    "policy_supports",
    "render_requirements_markdown",
    "requirements_from_jd",
    "requirements_from_jd_text",
    "requirements_from_policy",
    "split_jd_lines",
    "tag_display_name",
]
