"""岗位要求 × 简历标签 → 标签命中率 + 三维度匹配报告（辅助参考视图）。

重要定位：这里产出的是 **辅助匹配视图**，不是录用评分，也不进入 v5 正式决策链路。
它只回答三个问题：

1. 岗位要求（按能力 / 经验 / 特质分类）中，哪些能在简历里找到逐字证据；
2. 哪些只有线索、需要面试或证件核实；
3. **命中率**：可评估的计分标签里，简历命中了几个。

打分口径：**综合匹配度 = 标签命中率 = 命中标签数 / 适用标签数**（只统计能力/经验，
``cues.SCORING_DIMENSIONS``）；**特质不计分**（``cues.REFERENCE_DIMENSIONS``），只做参考
展示和面试提问线索。硬性条件（学历/证书/地点/年龄）不进命中率，单独核验；
其中需求方显式提出的**年龄**条件是硬门槛——简历年龄不符合时结论直接为「不推荐」。

判定语义（三态 + 一个面试核实态）：

``matched``   简历有明确证据（强线索命中，或同维度标签 + 多处关键词共现）
``weak``      简历有 1 条弱线索，或标签置信度不足（有线索、待核实）
``not_found`` 简历完全未提及（面试核实类要求会按面试基线计入，并单独列 ``interview_pending``）

命中率 = 命中标签数 / 可评估标签数（面试核实类「简历未提及」不计入分母，待核实单独列出）；
维度覆盖度（加权）仍保留在 ``dimensions[*].coverage`` 供追溯，但不再作为主分数。
"""
from __future__ import annotations

import re
from typing import Any

from rn.resume_screening.analysis import cues, requirements as requirements_mod
from rn.resume_screening.analysis.textutil import compact_text, evidence_snippet, find_keywords, normalize_text

SCHEMA_VERSION = "resume-match/1.0"

STATE_MATCHED = "matched"
STATE_WEAK = "weak"
STATE_NOT_FOUND = "not_found"
# 「未命中」只留给有反向证据的情况（明确写没做过/专业不符/证件明确没有），
# 简历没写的一律算待验证——待验证 = 面试或证件核验环节要确认的项。
STATE_CONTRADICTED = "contradicted"
STATE_VALUES: dict[str, float] = {
    STATE_MATCHED: 1.0,
    STATE_WEAK: 0.6,
    STATE_NOT_FOUND: 0.0,
    STATE_CONTRADICTED: 0.0,
}
INTERVIEW_BASELINE = 0.35  # 面试核实项「未提及」的参考基线，仅用于展示说明，不计入覆盖度分母
MATCHED_MIN_CONFIDENCE = 0.7
STATE_LABELS: dict[str, str] = {
    STATE_MATCHED: "命中（有证据）",
    STATE_WEAK: "待验证（有线索）",
    STATE_NOT_FOUND: "待验证（简历未提及）",
    STATE_CONTRADICTED: "未命中（有反证）",
}
DIMENSION_LABELS = dict(cues.DIMENSION_LABELS)
DIMENSION_WEIGHTS = dict(requirements_mod.DIMENSION_WEIGHTS)

_LEVEL_RANK = {"高中": 0.25, "中专": 0.5, "大专": 1.0, "专科": 1.0, "本科": 2.0, "硕士": 3.0, "研究生": 3.0, "博士": 4.0}
_EDU_ORDER = ("博士", "硕士", "研究生", "本科", "大专", "专科", "中专", "高中")
_MAJOR_TOKENS = ("医学检验技术", "医学检验", "临床医学", "生物技术", "卫生检验", "生物化学")
# 检验类资格的等级：检验士 < 检验师 < 检验技师。不能用「资格证」「上岗证」这类通用词，
# 否则「教师资格证」也会被当成命中检验师资格。
_CERT_FAMILY: dict[str, int] = {"检验士": 1, "检验师": 2, "检验技师": 3}
# 证书否定语境：简历写了「已报名，尚未取得检验师资格证」时不能判「满足」。
_CERT_NEGATION_RE = re.compile(r"(尚未|未取得|未获|未持有|未拿到|待考|在考|报名|准备考|备考|预计|计划考|没有)")
# 求职意向里的「检验技师」是目标岗位，不是持证，核验证书前先剔除。
_CERT_ASPIRATION_RE = re.compile(
    r"(?:意向岗位|求职意向|期望岗位|应聘岗位|目标岗位|求职目标)[^\n。；;]{0,24}"
)
_EDU_SCOPE_RE = re.compile(r"(学历|学位|院校|学校|毕业|专业|本科|大专|硕士|博士|中专)")
_LOCATION_LABEL_RE = re.compile(r"^(?:工作地点|工作城市|常驻地|base\s*地?|办公地点|工作地址|上班地点)\s*[:：]\s*")
# 城市后缀：除了「杭州市/地区/自治州/盟」，还要认「杭州集团总部实验室」「杭州实验室」这类
# 不带「市」字的写法，否则「工作地点：杭州集团总部实验室」提不出城市，位置核验永远待核实。
_CITY_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,4}?)(?:市|地区|自治州|盟|集团|总部|实验室|园区|分公司|公司|基地)"
)
_CITY_SPLIT_RE = re.compile(r"[/、,，;；|或]")
# 城市后缀匹配会顺带提出「总部/集团/实验室」这类组织词，不能当成城市参与比较。
_CITY_STOPWORDS = frozenset({"总部", "集团", "公司", "基地", "园区", "实验室", "分公司"})
# 课程/培养方案行：这些行里出现「分子生物学」「临床检验基础」只说明上过课，
# 不能当成「分子检测经验」「检验科经验」。经验维度匹配时整行剔除。
_COURSE_LINE_RE = re.compile(
    r"(主修课程|修课程|专业课程|课程[:：]|课程包括|所学课程|培养方案|主修[:：]|主修：)"
)
# 轮转/轮岗行：只说明「去过这个科室」，不等于做过该类检测（王习之「轮转了理化、流式、
# 血液、病理、微生物等科室」不能算分子/微生物检测经验）。经验维度匹配时整行剔除。
_ROTATION_LINE_RE = re.compile(r"(轮转|轮岗|轮训|转科)")
# 反证：简历明确写「没有/未接触过/不具备 + 该项关键词」才算「未命中（有反证）」。
# 必须紧邻（同一短句内 ≤6 字），否则整篇折成一行的 PDF 只要出现一个「无」字就会误判。
_NEGATION_RE = re.compile(
    r"(?:没有|无|未(?:曾)?(?:接触过|从事过|操作过|做过|学习过|取得|获得|考取)|"
    r"不具备|缺乏|不熟悉|不了解|不会)"
)
# 通用词：单独出现只能算线索，不单独构成「命中」（否则规范操作/仪器操作几乎人人命中）。
_GENERIC_KEYWORDS = frozenset({
    "仪器", "设备", "实验室", "检验科", "医院", "临床", "样本", "标本",
    "上机", "检测", "操作", "报告", "工作", "项目",
})
# 快速学习能力：不看 GPA/绩点的具体分数（学校难度不可比），只看排名是否在前 15%。
_LEARNING_RANK_RE = re.compile(
    r"(?:专业|成绩|年级|综合|班级)?\s*(?:排名)?\s*前\s*(\d{1,2}(?:\.\d+)?)\s*[%％]"
)
_LEARNING_RANK_LIMIT = 15.0
# 被验证过、且不依赖学校分数的替代证据。
_LEARNING_VERIFIED_KEYWORDS = frozenset({"培训合格证书", "奖学金"})
# 「临床医学检验技术（士/师）资格证」是检验类职称的正式写法，先归一化成 检验士/检验师。
# 「临床」前缀常被简历省略（医学检验技术（士）资格证），因此设为可选。
_CERT_ALIAS_RE = re.compile(
    r"(?:临床)?医学检验技术\s*[（(]?\s*(?:士|师)\s*(?:[/／]\s*(?:士|师))?\s*[）)]"
)
# 卫生检验（士/师）是另一套职称序列，不能算作临床检验士/检验师。
_CERT_OTHER_FAMILY_RE = re.compile(
    r"卫生检验(?:技术)?\s*[（(]?\s*(?:士|师)\s*[）)]?"
)

DISCLAIMER = (
    "本报告是辅助匹配视图：综合匹配度 = 标签命中率（命中标签数 / 适用标签数），"
    "只反映「岗位要求标签在简历文字里的可证实比例」，不是录用评分，也不能替代面试与证件核验；"
    "正式录用决策由 v5 决策链路另行产出。"
)


def _resume_rank(level: str) -> float:
    return _LEVEL_RANK.get(str(level or "").strip(), 0.0)


def _resume_age(fields: dict[str, Any]) -> int | None:
    """Parse the resume age field into an int (None when missing/unparseable)."""
    raw = fields.get("age")
    match = re.search(r"\d{1,3}", str(raw or ""))
    if not match:
        return None
    age = int(match.group())
    return age if 0 < age <= 100 else None


def _min_education_rank(text: str) -> tuple[float, str]:
    """Extract the minimum accepted education level from hard-filter text."""
    match = re.search(r"(博士|硕士|研究生|本科|大专|专科|中专|高中)(?:及以上|以上|起)?", str(text or ""))
    if not match:
        return 0.0, ""
    token = match.group(1)
    return _LEVEL_RANK.get(token, 0.0), token


def _education_scope(corpus: str) -> str:
    """Return only the education-related lines of a resume corpus (used for 专业 matching)."""
    lines = [line.strip() for line in str(corpus or "").splitlines() if line.strip()]
    return "\n".join(line for line in lines if _EDU_SCOPE_RE.search(line))


def _major_hit(education: dict[str, Any], corpus: str) -> str:
    """Match the declared major only within education fields / education lines.

    不能用整篇简历语料做子串匹配：文秘专业 + 简历里出现「医学检验所」工作经历时，
    会把非相关专业误判成「专业匹配」。
    """
    major = str(education.get("major") or "").strip()
    if major:
        return next((token for token in _MAJOR_TOKENS if token in major), "")
    scope = _education_scope(corpus)
    if not scope:
        return ""
    return next((token for token in _MAJOR_TOKENS if token in scope), "")


def _cert_negated(text: str, token: str, *, radius: int = 12) -> bool:
    """Whether every occurrence of ``token`` sits in a 未取得/待考 context."""
    occurrences = list(re.finditer(re.escape(token), text))
    if not occurrences:
        return False
    for match in occurrences:
        window = text[max(0, match.start() - radius): match.end() + radius]
        if not _CERT_NEGATION_RE.search(window):
            return False
    return True


def _normalize_cert_text(text: str) -> str:
    """Normalize「临床医学检验技术（士/师）资格证」to the 检验士/检验师 family tokens.

    简历常用职称全称（「临床医学检验技术（师）资格证」），只按「检验师」字面扫描会漏判。
    """
    def _sub(match: re.Match[str]) -> str:
        segment = match.group(0)
        return "检验师" if "师" in segment.replace("技术", "") else "检验士"

    return _CERT_ALIAS_RE.sub(_sub, text)


def _course_free_corpus(corpus: str) -> str:
    """Drop course / training-plan lines so they cannot become 经验证据.

    轮转/轮岗行按用户口径保留：轮转接触过某类科室即视为经验证据（合规边界内由面试复核）。
    """
    return "\n".join(
        line
        for line in str(corpus or "").splitlines()
        if not _COURSE_LINE_RE.search(line)
    )


def _course_only_evidence(corpus: str, evidence: str) -> bool:
    """Whether a tag's evidence snippet only occurs in course lines."""
    needle = re.sub(r"^…|…$", "", str(evidence or "")).strip()
    if len(needle) < 4:
        return False
    # 证据片段常被折行/压缩过，逐行 contains 可能对不上；只要片段本身带课程标记
    # （「主修课程：临床分子生物学检验…」），就按弱证据处理。
    if _COURSE_LINE_RE.search(needle):
        return True
    needle = needle[:40]
    lines = [line for line in str(corpus or "").splitlines() if needle in line]
    return bool(lines) and all(_COURSE_LINE_RE.search(line) for line in lines)


def _contradiction_evidence(corpus: str, keywords: list[str]) -> str:
    """Return the sentence that explicitly denies this requirement, else ''.

    只认「简历自己写明没有/未接触过/不具备」这类反向证据；简历没写不算反证。
    判定要求否定词与该项要求的关键词**紧邻**（同一短句、间隔 ≤6 字），避免两种误报：
    整篇 PDF 折成一行的文本里随便一个「无」字把所有人的标签都判成反证。
    """
    wanted = [kw for kw in keywords if len(kw) >= 2]
    if not wanted:
        return ""
    for clause in re.split(r"[。；;\n]", str(corpus or "")):
        clause = clause.strip()
        # 长句（折行失败的整篇文本）不参与判定，宁可漏判也不误判。
        if not clause or len(clause) > 80:
            continue
        for keyword in wanted:
            pattern = (
                r"(?:没有|无|未(?:曾)?(?:接触过|从事过|操作过|做过|学习过|取得|获得|考取)|"
                r"不具备|缺乏|不熟悉|不了解|不会)"
                rf"[^，,、。；;]{{0,6}}{re.escape(keyword)}"
            )
            if re.search(pattern, clause):
                return clause[:120]
    return ""


def _learning_rank_percent(corpus: str) -> float | None:
    """Return the best (smallest) 排名前 X% found in the resume, else None."""
    percents = [float(m.group(1)) for m in _LEARNING_RANK_RE.finditer(str(corpus or ""))]
    return min(percents) if percents else None


def _learning_rank_snippet(corpus: str) -> str:
    match = _LEARNING_RANK_RE.search(str(corpus or ""))
    return evidence_snippet(corpus, match.group(0)) if match else ""


def _cert_match(text: str) -> dict[str, Any]:
    """Find the highest 检验类资格 mentioned in ``text`` and whether it is negated."""
    text = _normalize_cert_text(str(text or ""))
    # 卫生检验（士/师）属另一序列：先剔除，避免「卫生检验士」被当成临床检验士。
    text = _CERT_OTHER_FAMILY_RE.sub(" ", text)
    # 「意向岗位：检验技师」是求职意向，不是持证；否则每个人都会被判满足。
    text = _CERT_ASPIRATION_RE.sub(" ", text)
    positive = ""
    positive_rank = 0
    negated = ""
    negated_rank = 0
    for token, rank in _CERT_FAMILY.items():
        if token not in text:
            continue
        if _cert_negated(text, token):
            if rank > negated_rank:
                negated, negated_rank = token, rank
            continue
        if rank > positive_rank:
            positive, positive_rank = token, rank
    return {"token": positive, "rank": positive_rank, "negated": negated}


def _city_tokens(text: str) -> set[str]:
    """Extract comparable city names from a hard-filter / resume location string."""
    cleaned = _LOCATION_LABEL_RE.sub("", str(text or "").strip())
    cleaned = re.sub(r"\s+", "", cleaned)
    tokens: set[str] = set()
    for match in _CITY_RE.finditer(cleaned):
        token = match.group(1)
        if token not in _CITY_STOPWORDS:
            tokens.add(token)
    for chunk in _CITY_SPLIT_RE.split(cleaned):
        chunk = re.sub(r"(市|地区|自治州|盟)$", "", chunk.strip())
        if 2 <= len(chunk) <= 6:
            tokens.add(chunk)
    return tokens


def _location_overlap(target_tokens: set[str], resume_text: str) -> str:
    """Return the matched city token, or "" when no overlap (city-level, not substring of label)."""
    if not target_tokens:
        return ""
    resume_tokens = _city_tokens(resume_text)
    if not resume_tokens:
        return ""
    for target in target_tokens:
        for candidate in resume_tokens:
            if target == candidate or target in candidate or candidate in target:
                return target
    return ""


def _hard_filter_hints(requirements_doc: dict[str, Any], fields: dict[str, Any], corpus: str) -> list[dict[str, Any]]:
    """Auxiliary checks for policy hard filters (education / certification / location)."""
    hard = requirements_doc.get("hard_filters") or {}
    if not isinstance(hard, dict) or not hard:
        return []
    hints: list[dict[str, Any]] = []
    education = fields.get("education") if isinstance(fields.get("education"), dict) else {}

    edu_text = str(hard.get("education") or "").strip()
    if edu_text:
        required_rank, required_token = _min_education_rank(edu_text)
        actual_level = str(education.get("level") or "").strip()
        actual_rank = _resume_rank(actual_level)
        major = str(education.get("major") or "").strip()
        major_hit = _major_hit(education, corpus)
        major_required = "专业" in edu_text
        if required_rank and actual_rank:
            if actual_rank >= required_rank:
                status = "满足" if (major_hit or not major_required) else "待核实"
                note = f"学历 {actual_level} ≥ 要求 {required_token}；专业{'匹配 ' + major_hit if major_hit else '未确认'}"
            else:
                status = "不满足"
                note = f"学历 {actual_level} < 要求 {required_token}"
        elif required_rank:
            major_note = f"；专业命中 {major_hit}" if major_hit else ""
            status = "待核实"
            note = f"要求 {required_token}；简历未提取到学历字段{major_note}"
        else:
            status = "待核实"
            note = "政策要求无法解析出最低学历"
        if status == "待核实" and actual_rank and major_hit:
            status = "满足"
            note = f"学历 {actual_level} ≥ 要求 {required_token}；专业匹配 {major_hit}"
        hints.append({
            "filter": "education",
            "requirement": edu_text,
            "status": status,
            "evidence": f"{actual_level} {education.get('school','')} {major}".strip(),
            "note": note,
        })

    cert_text = str(hard.get("certification") or "").strip()
    if cert_text:
        certifications = [str(c) for c in (fields.get("certifications") or []) if str(c).strip()]
        required = _cert_match(cert_text)
        # 证书既可能在 raw_text 里，也可能只在结构化 certifications 字段里（PDF/Word 抽取常见）
        cert_corpus = " ".join([corpus, "、".join(certifications)])
        resume_cert = _cert_match(cert_corpus)
        if resume_cert["token"] and (not required["rank"] or resume_cert["rank"] >= required["rank"]):
            status = "满足"
            note = f"简历持有检验类证书：{resume_cert['token']}"
            if required["token"]:
                note += f"；岗位要求 {required['token']}"
        elif resume_cert["token"]:
            status = "待核实"
            note = (
                f"简历持有 {resume_cert['token']}，岗位要求 {required['token']}，"
                "等级是否满足需核验证件"
            )
        elif resume_cert["negated"]:
            status = "待核实"
            note = f"简历提到「{resume_cert['negated']}」但处于未取得/待考语境，需上传证件核实"
        elif certifications:
            status = "待核实"
            note = "简历有证书但未命中检验类资格证，需核验证件"
        else:
            status = "待核实"
            note = "简历未写证书；政策允许应届已报名待考，需上传证件核实"
        hints.append({
            "filter": "certification",
            "requirement": cert_text,
            "status": status,
            "evidence": "、".join(certifications) if certifications else "",
            "note": note,
        })

    location_text = str(hard.get("location") or "").strip()
    if location_text:
        location = str(fields.get("location") or "").strip()
        expected_city = str(fields.get("expected_city") or "").strip()
        target_tokens = _city_tokens(location_text)
        resume_text = " ".join(part for part in (location, expected_city) if part).strip()
        target_display = "、".join(sorted(target_tokens)) or _LOCATION_LABEL_RE.sub("", location_text)
        hit = _location_overlap(target_tokens, resume_text) if resume_text else ""
        if hit:
            status = "满足"
            note = f"现居/期望工作地命中 {hit}"
        elif not resume_text:
            status = "待核实"
            note = f"要求工作地 {target_display}；简历未提取到现居地/期望城市"
        else:
            status = "待核实"
            note = f"要求工作地 {target_display}；简历现居地 {location or '未知'}、期望城市 {expected_city or '未知'}，需确认是否接受"
        hints.append({
            "filter": "location",
            "requirement": location_text,
            "status": status,
            "evidence": resume_text,
            "note": note,
        })

    age_text = str(hard.get("age") or "").strip()
    if age_text:
        low, high = requirements_mod.parse_age_requirement(age_text)
        actual_age = _resume_age(fields)
        if low is not None and high is not None:
            condition = f"{low}-{high} 岁"
        elif low is not None:
            condition = f"{low} 岁以上"
        elif high is not None:
            condition = f"{high} 岁以下"
        else:
            condition = age_text
        if low is None and high is None:
            status = "待核实"
            note = "年龄条件无法解析出具体范围，需人工确认"
        elif actual_age is None:
            status = "待核实"
            note = f"要求年龄 {condition}；简历未提取到年龄"
        elif (low is not None and actual_age < low) or (high is not None and actual_age > high):
            status = "不满足"
            note = f"简历年龄 {actual_age} 岁，不符合 {condition}：需求方将该条件设为硬性门槛，结论为不推荐"
        else:
            status = "满足"
            note = f"简历年龄 {actual_age} 岁，符合 {condition}"
        hints.append({
            "filter": "age",
            "requirement": age_text,
            "status": status,
            "evidence": "" if actual_age is None else f"{actual_age} 岁",
            "note": note,
            # 年龄是需求方显式提出的硬门槛：不满足时直接「不推荐」，不只是提示。
            "blocking": True,
        })

    other_text = str(hard.get("other") or "").strip()
    if other_text:
        # 需求方显式提出、但无法从简历自动判定的硬性条件：单列待核实，不进入命中率分母。
        for item in (part.strip() for part in other_text.split("；") if part.strip()):
            hints.append({
                "filter": "other",
                "requirement": item,
                "status": "待核实",
                "evidence": "",
                "note": f"硬性条件：{item}；简历未提供直接证据，需人工核验（不计入标签命中率）",
            })
    return hints


def _evaluate_requirement(
    requirement: dict[str, Any],
    tags_by_dimension: dict[str, list[dict[str, Any]]],
    corpus: str,
    *,
    course_free_corpus: str | None = None,
    line_corpus: str | None = None,
) -> dict[str, Any]:
    dimension = requirement.get("dimension") or cues.DIMENSION_CAPABILITY
    canonical = str(requirement.get("canonical_name") or requirement.get("name") or "").strip()
    row_tags = tags_by_dimension.get(dimension) or []
    tag_hits = [
        tag for tag in row_tags
        if canonical and canonical in {str(tag.get("name") or ""), str(tag.get("canonical_name") or "")}
    ]
    keywords = [str(kw) for kw in (requirement.get("keywords") or []) if str(kw).strip()]
    strong_keywords = {str(kw) for kw in (requirement.get("strong_keywords") or []) if str(kw).strip()}
    # 「?」弱证据词（参加学习培训/学习适应…）只能算待核实，不能靠数量叠加升级成命中。
    weak_only_keywords = {
        str(kw) for kw in (requirement.get("weak_only_keywords") or []) if str(kw).strip()
    }
    # 经验维度不看课程行：修过「临床分子生物学检验」不等于有分子检测经验。
    # ``course_free_corpus`` 必须在换行还在的原始文本上过滤，否则整篇被折成一行，
    # 只要出现过「课程」就会把整份简历清空。
    is_experience = dimension == cues.DIMENSION_EXPERIENCE
    match_corpus = (course_free_corpus if course_free_corpus is not None else corpus) if is_experience else corpus
    found = find_keywords(match_corpus, keywords)
    strong_found = [kw for kw in found if kw in strong_keywords]
    # 通用词（仪器/实验室/检验科/标本…）单独出现只算线索，不能单独构成命中；
    # 弱证据词同样不计入「多处线索」的升级计数。
    meaningful = [
        kw for kw in found if kw not in _GENERIC_KEYWORDS and kw not in weak_only_keywords
    ]
    weak_only_found = [kw for kw in found if kw in weak_only_keywords]

    best_tag = max(tag_hits, key=lambda tag: float(tag.get("confidence") or 0.0)) if tag_hits else None
    if best_tag is not None and is_experience and _course_only_evidence(
        line_corpus if line_corpus is not None else corpus,
        str(best_tag.get("evidence") or ""),
    ):
        best_tag = None
    evidence = ""
    state = STATE_NOT_FOUND

    if best_tag is not None and float(best_tag.get("confidence") or 0.0) >= MATCHED_MIN_CONFIDENCE:
        state = STATE_MATCHED
        evidence = str(best_tag.get("evidence") or "")
    elif strong_found:
        state = STATE_MATCHED
        evidence = evidence_snippet(match_corpus, strong_found[0])
    elif len(meaningful) >= 2:
        state = STATE_MATCHED
        evidence = evidence_snippet(match_corpus, meaningful[0])
    elif found:
        state = STATE_WEAK
        evidence = evidence_snippet(match_corpus, found[0])
    elif best_tag is not None:
        state = STATE_WEAK
        evidence = str(best_tag.get("evidence") or "")

    # 快速学习能力：GPA/绩点的具体数值不看（学校难度不可比），只看排名是否在前 15%，
    # 或培训合格证书/奖学金这类被验证过的证据；其余自述一律最多「待验证（有线索）」。
    if str(requirement.get("canonical_name") or requirement.get("name") or "").strip() == "快速学习能力":
        percent = _learning_rank_percent(match_corpus)
        verified_other = any(kw in _LEARNING_VERIFIED_KEYWORDS for kw in strong_found)
        if percent is not None and percent <= _LEARNING_RANK_LIMIT:
            state = STATE_MATCHED
            evidence = _learning_rank_snippet(match_corpus) or evidence
        elif verified_other:
            state = STATE_MATCHED
        elif percent is not None or found:
            state = STATE_WEAK
            evidence = _learning_rank_snippet(match_corpus) or evidence or (
                evidence_snippet(match_corpus, found[0]) if found else ""
            )

    # 反证优先：简历明确写「没有/未接触过/不具备」这一项时，才判「未命中（有反证）」。
    # 没写仍然算「待验证（简历未提及）」——没写不等于不会。
    contradiction = _contradiction_evidence(match_corpus, keywords)
    if contradiction:
        state = STATE_CONTRADICTED
        evidence = contradiction

    interview_required = str(requirement.get("decision_mode") or "") == "interview_verify"
    # 面试核实类要求：简历没有提及不等于不匹配，只是无法从简历判断。
    # 这类条目不计入覆盖度分母（否则所有候选人都会被几条「待面试」拖成同一档），
    # 只单列在 interview_pending 里交给面试环节。
    interview_baseline_applied = interview_required and state == STATE_NOT_FOUND
    value = STATE_VALUES[state]
    if interview_baseline_applied:
        value = 0.0

    return {
        "requirement_id": requirement.get("requirement_id"),
        "criterion_id": requirement.get("criterion_id"),
        "dimension": dimension,
        "name": requirement.get("name"),
        "level": requirement.get("level"),
        "level_weight": float(requirement.get("level_weight") or requirements_mod.LEVEL_WEIGHTS.get(requirement.get("level"), 1.0)),
        "decision_mode": requirement.get("decision_mode"),
        "state": state,
        "state_label": STATE_LABELS[state],
        "value": round(value, 3),
        "counted": not interview_baseline_applied,
        "interview_required": interview_required,
        "interview_baseline_applied": interview_baseline_applied,
        "matched_keywords": found,
        "strong_keywords": strong_found,
        "weak_only_keywords": weak_only_found,
        "tag_ids": [str(tag.get("tag_id")) for tag in tag_hits],
        "evidence": evidence,
        "description": requirement.get("description"),
        "evidence_hints": requirement.get("evidence_hints"),
    }


def _dimension_block(items: list[dict[str, Any]]) -> dict[str, Any]:
    counted = [item for item in items if item.get("counted", True)]
    total_weight = sum(item["level_weight"] for item in counted)
    weighted = sum(item["level_weight"] * item["value"] for item in counted)
    coverage = (weighted / total_weight) if total_weight else 0.0
    applied = len(counted)
    hit_count = sum(1 for item in counted if item["state"] == STATE_MATCHED)
    return {
        "requirement_count": len(items),
        "evaluable_count": applied,
        "applied_count": applied,
        "hit_count": hit_count,
        "hit_percent": round((hit_count / applied) * 100, 1) if applied else None,
        "must_count": sum(1 for item in items if item["level"] == "必须"),
        "preferred_count": sum(1 for item in items if item["level"] == "优先"),
        "matched": [item["name"] for item in items if item["state"] == STATE_MATCHED],
        "weak": [item["name"] for item in items if item["state"] == STATE_WEAK],
        "missing": [item["name"] for item in items if item["state"] == STATE_NOT_FOUND and not item["interview_required"]],
        "interview_pending": [item["name"] for item in items if item["interview_baseline_applied"]],
        "coverage": round(coverage, 4),
        "coverage_percent": round(coverage * 100, 1),
        "resume_only_coverage": round(coverage, 4),
        "items": items,
    }


def _match_label(overall: float) -> str:
    if overall >= 0.75:
        return "高匹配"
    if overall >= 0.55:
        return "较高匹配"
    if overall >= 0.40:
        return "中等匹配"
    if overall >= 0.25:
        return "偏低匹配"
    return "低匹配"


def match_requirements(
    requirements_doc: dict[str, Any],
    tags_doc: dict[str, Any],
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Compute the three-dimension auxiliary match report."""
    requirements_doc = requirements_doc if isinstance(requirements_doc, dict) else {}
    tags_doc = tags_doc if isinstance(tags_doc, dict) else {}
    fields = fields if isinstance(fields, dict) else {}
    tags_by_dimension: dict[str, list[dict[str, Any]]] = {
        dim: list((tags_doc.get("tags") or {}).get(dim) or []) for dim in cues.DIMENSIONS
    }
    raw_text = str(fields.get("raw_text") or "")
    corpus = compact_text(raw_text)
    if not corpus:
        corpus = compact_text(" ".join(
            str(tag.get("evidence") or "") for rows in tags_by_dimension.values() for tag in rows
        ))
    # 课程行过滤要基于保留换行的原文；compact_text 会把全文折成一行。
    line_corpus = normalize_text(raw_text) if raw_text else corpus
    course_free_corpus = compact_text(_course_free_corpus(line_corpus)) if raw_text else corpus

    rows = [row for row in (requirements_doc.get("requirements") or []) if isinstance(row, dict) and row.get("applicable", True)]
    hard_filters = requirements_doc.get("hard_filters") or {}
    has_hard_filters = isinstance(hard_filters, dict) and any(str(v).strip() for v in hard_filters.values())
    # 「只有硬性条件」的 JD（如「大专以上、有检验士证、工作地杭州」）同样不是空要求：
    # 此时没有三维度条目（覆盖度不适用），但仍应按硬性条件给出核验结论。
    requirements_available = requirements_mod.has_usable_requirements(requirements_doc)
    evaluated = [
        _evaluate_requirement(
            row,
            tags_by_dimension,
            corpus,
            course_free_corpus=course_free_corpus,
            line_corpus=line_corpus,
        )
        for row in rows
    ]

    dimensions: dict[str, dict[str, Any]] = {}
    for dim in cues.DIMENSIONS:
        items = [item for item in evaluated if item["dimension"] == dim]
        if items:
            block = _dimension_block(items)
            block["scored"] = dim in cues.SCORING_DIMENSIONS
            dimensions[dim] = block

    # 只有计分维度（能力/经验）参与加权；特质即使有要求也只展示、不计分。
    active_weights = {
        dim: DIMENSION_WEIGHTS.get(dim, 0.0)
        for dim in dimensions
        if dim in cues.SCORING_DIMENSIONS
    }
    weight_sum = sum(active_weights.values())
    if weight_sum:
        overall = sum(
            block["coverage"] * active_weights[dim]
            for dim, block in dimensions.items()
            if dim in active_weights
        ) / weight_sum
    else:
        overall = 0.0
    scored_evaluable_count = sum(
        block["evaluable_count"] for dim, block in dimensions.items()
        if dim in cues.SCORING_DIMENSIONS
    )
    scored_requirement_count = sum(
        block["requirement_count"] for dim, block in dimensions.items()
        if dim in cues.SCORING_DIMENSIONS
    )

    # 主分数口径：标签命中率 = 命中标签数 / 可评估标签数（只算能力/经验，面试核实待定项不进分母）。
    scored_items = [
        item for item in evaluated
        if item["dimension"] in cues.SCORING_DIMENSIONS and item.get("counted", True)
    ]
    tag_applied = len(scored_items)
    tag_hit = sum(1 for item in scored_items if item["state"] == STATE_MATCHED)
    tag_weak = sum(1 for item in scored_items if item["state"] == STATE_WEAK)
    tag_missing = sum(1 for item in scored_items if item["state"] == STATE_NOT_FOUND)
    tag_contradicted = sum(1 for item in scored_items if item["state"] == STATE_CONTRADICTED)
    tag_pending = sum(1 for item in evaluated if item["interview_baseline_applied"])
    tag_hit_rate = (tag_hit / tag_applied) if tag_applied else 0.0
    hit_percent = round(tag_hit_rate * 100, 1) if tag_applied else None

    # 硬性条件：学历/证书/地点是提示，年龄是需求方显式提出的硬门槛（不满足 → 不推荐）。
    hard_hints = _hard_filter_hints(requirements_doc, fields, corpus)
    blocking_hints = [hint for hint in hard_hints if hint.get("blocking") and hint.get("status") == "不满足"]
    disqualified = bool(blocking_hints)
    disqualification_reason = str(blocking_hints[0].get("note") or "") if blocking_hints else ""

    matched_must = [
        item["name"] for item in evaluated
        if item["state"] == STATE_MATCHED and item["level"] == "必须"
        and item["dimension"] in cues.SCORING_DIMENSIONS
    ]
    gaps = [
        item for item in evaluated
        if item["state"] != STATE_MATCHED and item["level"] == "必须"
        and not item["interview_baseline_applied"]
        and item["dimension"] in cues.SCORING_DIMENSIONS
    ]
    references = [item for item in evaluated if item["state"] == STATE_MATCHED and item["evidence"]]

    notes: list[str] = []
    if not requirements_available:
        notes.append("岗位要求为空：无法评估匹配度。请提供完整 JD 文本（--job-text / --jd-file）或改用政策覆盖的岗位名。")
    elif has_hard_filters and not rows:
        notes.append("该 JD 只包含学历/证书/工作地等硬性条件，没有可归入三维度的要求：综合覆盖度不适用，请直接看硬性条件提示。")
    if requirements_doc.get("source") == "jd_text":
        notes.append("岗位要求来自 JD 文本，三维度归类为启发式结果，建议人工抽查归类是否合理。")
    if not tags_doc.get("tags"):
        notes.append("简历未生成任何标签：请先确认文件解析是否成功（扫描件需要 OCR）。")
    if dimensions.get(cues.DIMENSION_TRAIT):
        if (tags_doc.get("tags") or {}).get(cues.DIMENSION_TRAIT):
            notes.append("特质维度不参与匹配度加权，仅供参考；综合匹配度只由能力/经验决定。")
        else:
            notes.append("简历未自述特质；特质不参与匹配度加权，仅作面试关注点展示。")
    if requirements_doc.get("source") == "jd":
        notes.append(
            "岗位要求来自生成 JD：职责/关键能力拆成能力-经验标签，加分项记为「优先」，"
            "学历/证书/地点/年龄归入硬性条件；标签拆解为启发式结果，建议人工抽查。"
        )
    if disqualified:
        notes.append(f"硬性条件不满足，结论为「不推荐」：{disqualification_reason}")

    if not requirements_available:
        label = "无法评估（岗位要求为空）"
        overall_percent = None
    elif disqualified:
        label = "不推荐（年龄不符合硬性要求）"
        overall_percent = hit_percent
    elif tag_applied:
        label = _match_label(tag_hit_rate)
        overall_percent = hit_percent
    elif rows:
        label = "仅参考信息（无计分要求）"
        overall_percent = None
        notes.append("岗位要求只有特质等参考项，没有能力/经验类可计分要求，无法给出标签命中率。")
    else:
        label = "仅硬性条件（无三维度要求）"
        overall_percent = None

    # 推荐口径（与人工校验一致）：不推荐只留给硬性条件不满足；命中率 ≥40% 可推荐，
    # 低于 40% 说明简历证据太少，给「待核实」而不是直接淘汰。
    if disqualified:
        recommendation = "不推荐"
    elif tag_applied and tag_hit_rate >= 0.40:
        recommendation = "推荐"
    elif tag_applied:
        recommendation = "待核实"
    else:
        recommendation = ""
    if recommendation == "待核实":
        notes.append(
            "标签命中率不足四成：简历里可核实的证据偏少，结论为「待核实」，"
            "建议补充材料或面试核实后再决定，不直接淘汰。"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "job_name": requirements_doc.get("job_name") or requirements_doc.get("role_name") or "",
        "requirements_source": requirements_doc.get("source") or "",
        "jd_id": requirements_doc.get("jd_id") or "",
        "profile_id": requirements_doc.get("profile_id") or "",
        "score_basis": "tag_hit_rate",
        "overall_percent": overall_percent,
        "label": label,
        "tag_applied_count": tag_applied,
        "tag_hit_count": tag_hit,
        "tag_weak_count": tag_weak,
        "tag_missing_count": tag_missing,
        "tag_contradicted_count": tag_contradicted,
        "tag_pending_count": tag_pending,
        "tag_hit_rate": round(tag_hit_rate, 4),
        "tag_hit_percent": hit_percent,
        "disqualified": disqualified,
        "disqualification_reason": disqualification_reason,
        "recommendation": recommendation,
        # 旧的加权覆盖度保留在 overall_coverage 供追溯；主分数以上面的标签命中率为准。
        "overall_coverage": round(overall, 4),
        "requirements_available": requirements_available,
        "hard_filters_available": has_hard_filters,
        "requirement_count": len(evaluated),
        "evaluable_count": sum(1 for item in evaluated if item.get("counted", True)),
        "scored_requirement_count": scored_requirement_count,
        "scored_evaluable_count": scored_evaluable_count,
        "interview_pending_count": sum(1 for item in evaluated if item["interview_baseline_applied"]),
        "scored_dimensions": list(cues.SCORING_DIMENSIONS),
        "reference_dimensions": list(cues.REFERENCE_DIMENSIONS),
        "dimension_weights": {dim: active_weights[dim] for dim in active_weights},
        "dimensions": dimensions,
        "matched_must_have": matched_must,
        "strong_references": [
            {"name": item["name"], "dimension": item["dimension"], "evidence": item["evidence"]}
            for item in references[:6]
        ],
        "gaps": [
            {"name": item["name"], "dimension": item["dimension"], "level": item["level"],
             "state": item["state"], "evidence_hints": item.get("evidence_hints")}
            for item in gaps
        ],
        "interview_pending": [
            item["name"] for item in evaluated if item["interview_baseline_applied"]
        ],
        # 所有还没证实的计分项都要在面试/证件核验时确认（含「简历没写」），
        # 这样面试清单不会漏掉「没写 ≠ 不会」的项。
        "verification_items": [
            {
                "name": item["name"],
                "dimension": item["dimension"],
                "state": item["state"],
                "state_label": item["state_label"],
                "evidence_hints": item.get("evidence_hints"),
            }
            for item in evaluated
            if item.get("counted") and item["state"] in (STATE_WEAK, STATE_NOT_FOUND)
        ],
        "hard_filter_hints": hard_hints,
        "legend": {
            "matched": STATE_LABELS[STATE_MATCHED],
            "weak": STATE_LABELS[STATE_WEAK],
            "not_found": STATE_LABELS[STATE_NOT_FOUND],
            "note": (
                "综合匹配度 = 标签命中率 = 命中标签数 / 适用标签数（只统计能力/经验计分标签）；"
                "面试核实类要求（decision_mode=interview_verify）简历未提及时不计入分母，"
                "单列在待面试核实里，避免把「没写」误判成「不匹配」。"
                "特质不计分（仅参考）；学历/证书/地点只做硬性条件提示，年龄是需求方显式提出的硬门槛。"
            ),
        },
        "notes": notes,
        "disclaimer": DISCLAIMER,
    }


def percent_text(value: Any) -> str:
    """Format an overall/dimension percentage for display; ``None`` means「不适用」."""
    if value is None or value == "":
        return "—"
    return f"{value}%"


def match_rank_key(report: dict[str, Any]) -> tuple[int, float, int, int]:
    """Sort key for candidate lists: 命中硬门槛优先，其次标签命中率 desc，再 matched must-have desc."""
    dimensions = report.get("dimensions") or {}
    missing_must = sum(
        sum(1 for item in block.get("items") or [] if item.get("level") == "必须" and item.get("state") == STATE_NOT_FOUND)
        for block in dimensions.values()
    )
    return (
        0 if report.get("disqualified") else 1,
        float(report.get("tag_hit_rate") or 0.0),
        len(report.get("matched_must_have") or []),
        -missing_must,
    )


_STATE_ICONS = {STATE_MATCHED: "✅", STATE_WEAK: "⚠️", STATE_NOT_FOUND: "❌"}


def render_matching_markdown(
    report: dict[str, Any],
    *,
    resume_summary: dict[str, Any] | None = None,
    include_notes: bool = True,
    include_interview: bool = True,
) -> str:
    """Render a match report as Markdown (HR-facing, evidence-first).

    ``include_notes=False`` 供上层报告使用：上层已经用去重后的 notes 统一输出说明，
    这里不再重复打印同一批 note。
    """
    lines: list[str] = []
    lines.append("# 简历匹配报告（辅助参考）")
    lines.append("")
    if resume_summary:
        lines.append("## 候选人摘要")
        lines.append("")
        lines.append("| 项目 | 内容 |")
        lines.append("| --- | --- |")
        labels = (
            ("姓名", "name"), ("工作年限", "total_work_years"), ("学历", "education_level"),
            ("院校", "school"), ("专业", "major"), ("现居", "location"),
            ("当前公司", "current_company"), ("当前职位", "current_position"),
            ("期望职位", "expected_position"), ("期望城市", "expected_city"),
        )
        for label, key in labels:
            value = resume_summary.get(key)
            if value in (None, "", []):
                continue
            lines.append(f"| {label} | {value} |")
        lines.append("")
    # 结论统一成「推荐/待核实/不推荐」口径；档位（高匹配/较高匹配…）只作内部字段，
    # 不再单独出现在对外报告里，避免和推荐结论两套说法并存。
    verdict = report.get("label", "")
    if not report.get("disqualified"):
        verdict = report.get("recommendation") or verdict
    lines.append(
        f"**综合匹配度（标签命中率，仅能力/经验计分）："
        f"命中 {report.get('tag_hit_count', 0)} / 适用 {report.get('tag_applied_count', 0)} 个标签 "
        f"= {percent_text(report.get('overall_percent'))}　结论：{verdict}**"
    )
    if report.get("disqualified"):
        lines.append("")
        lines.append(f"> ⚠️ **不推荐**：{report.get('disqualification_reason') or '硬性条件不满足'}")
    if report.get("interview_pending_count"):
        lines.append("")
        lines.append(
            f"> 命中率基于 {report.get('tag_applied_count', 0)} 个简历可评估的计分标签；"
            f"另有 {report.get('interview_pending_count')} 项面试核实类要求未在简历体现，已单列，不计入分母。"
        )
    lines.append("")
    lines.append("| 维度 | 计分 | 可评估/总数 | 必须/优先 | 已证实 | 有线索 | 未提及 | 待面试核实 | 命中/适用 | 命中率 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for dim in cues.DIMENSIONS:
        block = (report.get("dimensions") or {}).get(dim)
        if not block:
            continue
        scored_text = "计分" if block.get("scored", dim in cues.SCORING_DIMENSIONS) else "不计分"
        lines.append(
            f"| {DIMENSION_LABELS[dim]} | {scored_text} "
            f"| {block.get('evaluable_count', block['requirement_count'])}/{block['requirement_count']} "
            f"| {block['must_count']}/{block['preferred_count']} "
            f"| {len(block['matched'])} | {len(block['weak'])} | {len(block['missing'])} "
            f"| {len(block.get('interview_pending') or [])} "
            f"| {block.get('hit_count', 0)}/{block.get('applied_count', block.get('evaluable_count', 0))} "
            f"| {percent_text(block.get('hit_percent'))} |"
        )
    lines.append("")
    for dim in cues.DIMENSIONS:
        block = (report.get("dimensions") or {}).get(dim)
        if not block:
            continue
        if block.get("scored", dim in cues.SCORING_DIMENSIONS):
            lines.append(
                f"## {DIMENSION_LABELS[dim]}（命中率 {percent_text(block.get('hit_percent'))}，"
                f"覆盖度 {block['coverage_percent']}%）"
            )
        else:
            lines.append(f"## {DIMENSION_LABELS[dim]}（不计分，仅参考）")
        lines.append("")
        for item in block["items"]:
            icon = _STATE_ICONS[item["state"]]
            state_label = item["state_label"]
            if item["interview_baseline_applied"]:
                icon = "⏳"
                state_label = "待面试核实"
            lines.append(f"- {icon} **{item['name']}**（{item['level']}，{state_label}）")
            if item.get("evidence"):
                lines.append(f"  - 证据：{item['evidence']}")
            elif item.get("evidence_hints"):
                lines.append(f"  - 待核实点：{item['evidence_hints']}")
        lines.append("")
    hints = report.get("hard_filter_hints") or []
    if hints:
        lines.append("## 硬性条件提示（需人工核验证件）")
        lines.append("")
        lines.append("| 条件 | 状态 | 依据 | 说明 |")
        lines.append("| --- | --- | --- | --- |")
        for hint in hints:
            lines.append(f"| {hint.get('requirement','')} | {hint.get('status','')} | {hint.get('evidence','')} | {hint.get('note','')} |")
        lines.append("")
    if report.get("gaps"):
        lines.append("## 主要缺口（计分维度必须项未证实）")
        lines.append("")
        for gap in report["gaps"]:
            lines.append(f"- {gap['name']}：{gap.get('evidence_hints') or ''}")
        lines.append("")
    if report.get("verification_items"):
        lines.append("## 待验证清单（面试/证件确认）")
        lines.append("")
        lines.append("简历没写或只有线索的计分项都在这里，面试时逐项确认，不当作不会。")
        lines.append("")
        for item in report["verification_items"]:
            hint = str(item.get("evidence_hints") or "")
            chunks = [chunk for chunk in re.split(r"[、,，;；/|]", hint) if chunk.strip()][:6]
            suffix = "（线索：" + "、".join(chunks) + ("…" if len(chunks) == 6 else "") + "）" if chunks else ""
            lines.append(f"- {item['name']}（{item['state_label']}）{suffix}")
        lines.append("")
    if report.get("interview_pending"):
        lines.append("## 待面试核实")
        lines.append("")
        for name in report["interview_pending"]:
            lines.append(f"- {name}")
        lines.append("")
    if include_interview:
        from rn.resume_screening.analysis.interview import render_interview_markdown

        questions = render_interview_markdown(report)
        if questions:
            lines.append(questions.rstrip("\n"))
            lines.append("")
    lines.append("---")
    lines.append(report.get("disclaimer") or DISCLAIMER)
    if include_notes:
        for note in report.get("notes") or []:
            lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "DISCLAIMER",
    "SCHEMA_VERSION",
    "STATE_LABELS",
    "STATE_MATCHED",
    "STATE_NOT_FOUND",
    "STATE_WEAK",
    "match_rank_key",
    "match_requirements",
    "percent_text",
    "render_matching_markdown",
]
