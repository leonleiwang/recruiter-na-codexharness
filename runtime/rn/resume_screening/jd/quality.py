"""对外发布 JD 撰写规范与自检。

规范提炼自成熟招聘工具的 JD 生成流程（先定框架 → 按动词库写职责 → 按 KSAO
写任职要求 → 字数/合规核验 → 输出格式块），落在本模块的常量与校验里：

- 三模块：职位名称 / 岗位职责 / 任职要求；职位名称用行业通用称谓，
  不出现公司名、集团名、内部部门/实验室名。
- 岗位职责 5–7 条，每条以强动词开头（``STRONG_VERBS``），15–30 字，
  按业务流程排序（样本接收 → 检测 → 仪器维护 → 质控 → 报告 → 生物安全）。
- 任职要求按 KSAO 分组：学历专业 / 知识技能 / 证书经验 / 职业素养，5–7 条。
- 全文 400–600 字（含标点与序号，不含 Markdown 标记）。
- 不出现企业信息与敏感词（性别 / 年龄 / 婚育 / 籍贯 / 民族 / 户籍等）。

校验只报告问题，不改写内容；改写交给 :mod:`rn.resume_screening.jd.draft`（LLM）
或用人部门确认。CLI 用 ``jd --check`` 触发，ERROR 级问题时退出码 4。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

# —— 岗位职责 ——

DUTY_MIN_COUNT = 5
DUTY_MAX_COUNT = 7
DUTY_MIN_CHARS = 15
DUTY_MAX_CHARS = 30

#: 强动词库：JD 职责必须以这些动作开头；「按照 / 根据 / 在…下」等状语开头视为不合格。
STRONG_VERBS = (
    "负责", "执行", "实施", "开展", "完成", "承担", "参与", "协助", "配合",
    "处理", "接收", "采集", "制备", "保存", "运送", "分发", "检测", "检验",
    "操作", "校准", "维护", "保养", "清洗", "消毒", "灭菌", "处置", "监控",
    "核查", "复核", "审核", "记录", "录入", "登记", "归档", "整理", "分析",
    "判定", "出具", "报告", "撰写", "制定", "优化", "培训", "指导", "沟通",
    "跟进", "反馈", "统计", "汇总", "申请", "领取", "配制", "离心", "培养",
    "分离", "涂片", "镜检", "管理", "落实", "保障", "确保",
)

# —— 任职要求（KSAO）——

REQUIREMENT_MIN_COUNT = 5
REQUIREMENT_MAX_COUNT = 7
KSAO_LANES = ("学历专业", "知识技能", "证书经验", "职业素养", "其他")
#: 本项目的能力/经验/素养分组 → KSAO 视角（用于自检报告，不改变文档分组）。
KSAO_BY_CATEGORY = {
    "学历": "学历专业",
    "专业": "学历专业",
    "技能": "知识技能",
    "经验": "证书经验",
    "证书": "证书经验",
    "素养": "职业素养",
    "其他": "其他",
}

# —— 篇幅与合规 ——

JD_MIN_CHARS = 400
JD_MAX_CHARS = 600

DEFAULT_COMPANY_TERMS = ("迪安诊断", "迪安", "集团总部", "本公司", "我司", "本企业")
#: 敏感词只用于「对外发布」校验（内部工单允许出现年龄等硬门槛，但不能对外发布）。
SENSITIVE_TERMS = (
    ("性别", "性别"), ("男士", "性别"), ("女士", "性别"), ("已婚", "婚育"), ("未婚", "婚育"),
    ("婚育", "婚育"), ("婚姻", "婚育"), ("生育", "婚育"), ("年龄", "年龄"), ("岁数", "年龄"),
    ("籍贯", "籍贯"), ("民族", "民族"), ("户籍", "户籍"), ("身份证", "身份信息"),
    ("政治面貌", "政治面貌"), ("宗教", "宗教"),
)

_MARKDOWN_PREFIX_RE = re.compile(r"^\s*(?:#{1,6}\s+|>\s?|[-*]\s+|\d+[.)、]\s+)")
#: 数字形式的年龄（「25-35 岁」「35 周岁」），SENSITIVE_TERMS 只覆盖了词语本身。
_AGE_VALUE_RE = re.compile(r"\d{1,2}\s*(?:[-–—~～至]\s*\d{1,2}\s*)?(?:周)?岁")


def company_terms() -> tuple[str, ...]:
    """公司标识词：内置词 + 环境变量 ``JD_FORBIDDEN_COMPANY_TERMS``（英文逗号分隔）。"""
    extra = [
        item.strip()
        for item in os.environ.get("JD_FORBIDDEN_COMPANY_TERMS", "").split(",")
        if item.strip()
    ]
    return tuple(dict.fromkeys([*DEFAULT_COMPANY_TERMS, *extra]))


def is_strong_verb_start(text: str) -> str:
    """返回命中的强动词；没有则返回空串。"""
    value = str(text).strip()
    for verb in STRONG_VERBS:
        if value.startswith(verb):
            return verb
    return ""


def duty_char_count(text: str) -> int:
    return len(str(text).strip())


def plain_text(document: str) -> str:
    """剥掉 Markdown 标记、序号与空白，得到自检用的正文。"""
    chunks: list[str] = []
    for line in str(document or "").splitlines():
        line = _MARKDOWN_PREFIX_RE.sub("", line).replace("**", "").replace("`", "").strip()
        if line:
            chunks.append(line)
    return "".join(chunks)


def document_char_count(document: str) -> tuple[int, int]:
    """返回 ``(总字数, 汉字数)``；总字数含标点与序号，不含 Markdown 标记。"""
    text = plain_text(document)
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return len(text), cjk


@dataclass
class JdQualityIssue:
    level: str  # "error" | "warn"
    code: str
    message: str


@dataclass
class JdQualityReport:
    title: str
    duty_count: int
    requirement_count: int
    total_chars: int
    cjk_chars: int
    ksao: dict[str, list[str]] = field(default_factory=dict)
    issues: list[JdQualityIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.level == "error" for issue in self.issues)

    @property
    def errors(self) -> list[JdQualityIssue]:
        return [issue for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> list[JdQualityIssue]:
        return [issue for issue in self.issues if issue.level == "warn"]


def ksao_groups(jd: dict[str, Any]) -> dict[str, list[str]]:
    """把文档里的要求分组映射到 KSAO 视角（同一条要求只出现一次）。"""
    from rn.resume_screening.jd.generator import requirement_groups

    lanes: dict[str, list[str]] = {lane: [] for lane in KSAO_LANES}
    seen: set[str] = set()
    for category, values in requirement_groups(jd).items():
        lane = KSAO_BY_CATEGORY.get(category, "其他")
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            lanes[lane].append(text)
    return lanes


def sensitive_hits(text: str) -> list[str]:
    """返回文本命中的敏感词类别（去重）；对外发布版据此删掉或报错。"""
    return _sensitive_hits(text)


def _company_hits(text: str) -> list[str]:
    return [term for term in company_terms() if term in text]


def _sensitive_hits(text: str) -> list[str]:
    hits: list[str] = []
    for word, label in SENSITIVE_TERMS:
        if word in text and label not in hits:
            hits.append(label)
    if _AGE_VALUE_RE.search(text) and "年龄" not in hits:
        hits.append("年龄")
    return hits


def _duties_for_check(jd: dict[str, Any]) -> tuple[list[str], bool]:
    duties = [str(x).strip() for x in jd.get("responsibilities") or [] if str(x).strip()]
    if duties:
        return duties, False
    reference = (jd.get("related_template") or {}).get("reference") or {}
    return [str(x).strip() for x in reference.get("responsibilities") or [] if str(x).strip()], True


def validate_jd(jd: dict[str, Any], *, document: str | None = None,
                external: bool = False) -> JdQualityReport:
    """按对外发布规范自检一份 JD。

    ``external=True`` 时企业信息与敏感词都是 ERROR（对外发布不可出现）；
    内部工单只把它们降级为 WARN，交给用人部门判断。
    """
    if document is None:
        from rn.resume_screening.jd.generator import render_jd_document

        document = render_jd_document(jd, external=external)

    title = str(jd.get("title") or "").strip()
    duties, from_template = _duties_for_check(jd)
    lanes = ksao_groups(jd)
    requirement_count = sum(len(values) for lane, values in lanes.items() if lane != "其他")
    total_chars, cjk_chars = document_char_count(document)
    issues: list[JdQualityIssue] = []

    def add(level: str, code: str, message: str) -> None:
        issues.append(JdQualityIssue(level=level, code=code, message=message))

    # 1) 三模块骨架
    if not title:
        add("error", "title_missing", "缺少职位名称。")
    if "## 岗位职责" not in document:
        add("error", "duties_section_missing", "缺少「## 岗位职责」模块。")
    if "## 任职要求" not in document:
        add("error", "requirements_section_missing", "缺少「## 任职要求」模块。")

    # 2) 职位名称：行业通用、不含公司标识
    for term in _company_hits(title):
        add("error", "title_company", f"职位名称含公司标识「{term}」，请换成行业通用称谓。")

    # 3) 岗位职责：条数 / 强动词 / 字数
    if from_template:
        add("warn", "duties_from_template", "职责来自同岗位模板，发布前需用人部门确认。")
    if len(duties) < DUTY_MIN_COUNT:
        add("error", "duty_count_low", f"岗位职责 {len(duties)} 条，少于 {DUTY_MIN_COUNT} 条。")
    if len(duties) > DUTY_MAX_COUNT:
        add("error", "duty_count_high", f"岗位职责 {len(duties)} 条，多于 {DUTY_MAX_COUNT} 条。")
    for index, duty in enumerate(duties, start=1):
        verb = is_strong_verb_start(duty)
        if not verb:
            add("error", "duty_weak_verb", f"职责 {index} 未以强动词开头：{duty}")
        size = duty_char_count(duty)
        if size < DUTY_MIN_CHARS or size > DUTY_MAX_CHARS:
            add(
                "error",
                "duty_length",
                f"职责 {index} 共 {size} 字，应为 {DUTY_MIN_CHARS}–{DUTY_MAX_CHARS} 字：{duty}",
            )

    # 4) 任职要求：条数 / KSAO 覆盖
    if requirement_count < REQUIREMENT_MIN_COUNT:
        add(
            "error",
            "requirement_count_low",
            f"任职要求 {requirement_count} 条，少于 {REQUIREMENT_MIN_COUNT} 条。",
        )
    if requirement_count > REQUIREMENT_MAX_COUNT:
        add(
            "warn",
            "requirement_count_high",
            f"任职要求 {requirement_count} 条，多于建议的 {REQUIREMENT_MAX_COUNT} 条。",
        )
    for lane in KSAO_LANES[:4]:
        if not lanes.get(lane):
            add("warn", "ksao_missing", f"任职要求未覆盖 KSAO 维度「{lane}」。")

    # 5) 篇幅
    if total_chars < JD_MIN_CHARS:
        add(
            "error",
            "length_low",
            f"全文 {total_chars} 字（含标点/序号），少于 {JD_MIN_CHARS} 字；可适当展开职责。",
        )
    elif total_chars > JD_MAX_CHARS:
        add(
            "warn",
            "length_high",
            f"全文 {total_chars} 字，超过建议的 {JD_MAX_CHARS} 字；对外发布建议精简。",
        )

    # 6) 合规：企业信息 / 敏感词
    company_hits = _company_hits(document)
    if company_hits:
        level = "error" if external else "warn"
        add(level, "company_info", "文中出现公司标识：" + "、".join(company_hits))
    sensitive_hits = _sensitive_hits(document)
    if sensitive_hits:
        level = "error" if external else "warn"
        add(level, "sensitive_terms", "文中出现敏感词类别：" + "、".join(sensitive_hits))
    elif external:
        # 对外版会主动删掉内部硬条件里的敏感信息；仍然提示一次，避免「悄悄消失」。
        raw_hits = _sensitive_hits(
            "\n".join(str(entry.get("name") or "") for entry in jd.get("hard_requirements") or [])
        )
        if raw_hits:
            add("warn", "sensitive_terms_dropped", "内部条件中的敏感信息已从对外版移除：" + "、".join(raw_hits))

    return JdQualityReport(
        title=title,
        duty_count=len(duties),
        requirement_count=requirement_count,
        total_chars=total_chars,
        cjk_chars=cjk_chars,
        ksao=lanes,
        issues=issues,
    )


def render_quality_report(report: JdQualityReport, *, external: bool = False) -> str:
    """把自检结果渲染成 Markdown 表格 + 问题清单。"""
    scope = "对外发布版" if external else "内部工单"
    lines = [
        f"### JD 撰写规范自检（{scope}）",
        "",
        "| 检查项 | 结果 |",
        "| --- | --- |",
        f"| 职位名称 | {report.title or '（缺失）'} |",
        f"| 岗位职责 | {report.duty_count} 条（要求 {DUTY_MIN_COUNT}–{DUTY_MAX_COUNT}） |",
        f"| 任职要求 | {report.requirement_count} 条（KSAO 视角） |",
        f"| 全文篇幅 | {report.total_chars} 字（要求 {JD_MIN_CHARS}–{JD_MAX_CHARS}；汉字 {report.cjk_chars}） |",
        f"| 结论 | {'通过' if report.ok else '未通过'} |",
    ]
    if report.errors:
        lines.extend(["", "**必须修改（ERROR）**"])
        lines.extend(f"- [{issue.code}] {issue.message}" for issue in report.errors)
    if report.warnings:
        lines.extend(["", "**建议优化（WARN）**"])
        lines.extend(f"- [{issue.code}] {issue.message}" for issue in report.warnings)
    return "\n".join(lines)
