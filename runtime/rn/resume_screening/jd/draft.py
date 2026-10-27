"""按对外发布 JD 撰写规范起草职责与任职要求（LLM）。

确定性生成器（:mod:`rn.resume_screening.jd.generator`）只能整理用人部门给的
信息；当需求里只有岗位名与零散条件时，JD 会显得空。本模块把成熟招聘工具的
JD 撰写流程写进 prompt，让 LLM 在**不新增公司专属事实**的前提下起草：

1. 定框架：职位名称（行业通用称谓，不含公司/集团/内部部门名）/ 岗位职责 / 任职要求；
2. 岗位职责 5–7 条，强动词开头，每条 15–30 字，按业务流程排序
   （样本接收 → 检测 → 仪器维护 → 质控 → 报告归档 → 生物安全）；
3. 任职要求 5–7 条，按 KSAO 分组（学历专业 / 知识技能 / 证书经验 / 职业素养）；
4. 全文 400–600 字，不出现企业信息与敏感词（性别/年龄/婚育/籍贯/民族等）。

LLM 只输出 JSON，由本模块并入 ``fields`` 后交给确定性生成器，保证
``jd.json`` 结构与筛选侧消费者不变。LLM 不可用或输出不合契约时抛
:class:`JdDraftUnavailable`，调用方应回退到确定性生成并提示用户。
"""
from __future__ import annotations

import json
import re
from typing import Any

from rn.resume_screening.jd.generator import (
    build_request_jd,
    sanitize_title,
)
from rn.resume_screening.jd.intake import HiringAnalysisError, HiringIntentAnalyzer
from rn.resume_screening.jd.quality import KSAO_LANES

#: 允许的任职要求标签：KSAO 四维 + 其他（其他会渲染成「其他硬性条件」）。
_ALLOWED_LABELS = ("学历专业", "知识技能", "证书经验", "职业素养")
_REQUIREMENT_LABEL_MAP = {
    "学历": "学历专业",
    "专业": "学历专业",
    "学历专业": "学历专业",
    "知识": "知识技能",
    "技能": "知识技能",
    "知识技能": "知识技能",
    "经验": "证书经验",
    "证书": "证书经验",
    "证书经验": "证书经验",
    "素养": "职业素养",
    "素质": "职业素养",
    "职业素养": "职业素养",
    "其他": "其他",
}
_BULLET_RE = re.compile(r"^\s*(?:[0-9]+[.、)）]|[-*•·]|[（(][0-9]+[)）])\s*")


class JdDraftUnavailable(RuntimeError):
    """LLM 起草不可用（未配置 / 调用失败 / 输出不合契约）。"""


def _clean_text(value: Any) -> str:
    text = _BULLET_RE.sub("", str(value or "")).strip()
    text = re.sub(r"\s{2,}", " ", text).strip("；; ")
    return text


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [_clean_text(x) for x in re.split(r"[\n；;]", value) if _clean_text(x)]
    if isinstance(value, list):
        return [_clean_text(x) for x in value if _clean_text(x)]
    return []


def _profile_block(profile: dict[str, Any] | None) -> str:
    if not profile:
        return "（无标准岗位画像，仅按用户提供的需求起草，不补充画像专属信息。）"
    abilities = [
        str(item.get("name") if isinstance(item, dict) else item).strip()
        for item in profile.get("key_abilities") or []
        if str(item.get("name") if isinstance(item, dict) else item).strip()
    ]
    experiences = [
        str(item.get("name") if isinstance(item, dict) else item).strip()
        for item in profile.get("key_experiences") or []
        if str(item.get("name") if isinstance(item, dict) else item).strip()
    ]
    return json.dumps(
        {
            "name": profile.get("name", ""),
            "description": profile.get("description") or profile.get("business_challenge") or "",
            "key_abilities": abilities,
            "key_experiences": experiences,
        },
        ensure_ascii=False,
        indent=2,
    )


def _template_block(related_jd: Any | None) -> str:
    if related_jd is None:
        return "（无同岗位 JD 模板，按通用行业表述起草。）"
    duties = getattr(related_jd, "core_responsibilities", "") or ""
    requirements = getattr(related_jd, "requirements_text", "") or ""
    return json.dumps(
        {"title": getattr(related_jd, "title", ""), "duties": duties, "requirements": requirements},
        ensure_ascii=False,
        indent=2,
    )


def build_draft_prompt(fields: dict[str, Any], profile: dict[str, Any] | None = None,
                       related_jd: Any | None = None) -> str:
    """把撰写规范 + 已知需求 + 画像/模板参考拼成起草 prompt。"""
    known = {
        key: fields.get(key)
        for key in (
            "position_title", "department", "headcount", "location", "work_mode",
            "salary_range", "urgency", "employment_type", "core_responsibilities",
            "hard_requirements", "key_experience_skills", "bonus_points",
        )
        if fields.get(key) not in (None, "", [], {})
    }
    return f"""你是企业招聘 JD 撰写专家。请根据用人部门提供的需求，起草一份可直接对外发布的 JD。

撰写规范（必须逐条满足）：
1. 职位名称用行业通用称谓（如「医学检验技术员」「检验技师」），不得出现公司名、集团名、内部部门或实验室名。
2. 岗位职责 5–7 条，按业务流程排序：样本接收/处理 → 常规检验/检测 → 仪器维护/校准 → 质量管控 → 报告/记录归档 → 生物安全/废弃物处置。每条必须以强动词开头（负责、执行、完成、操作、维护、监控、记录、处置等），15–30 字，不得写「按照 SOP…」这类状语开头。
3. 任职要求 5–7 条，按 KSAO 分组输出 label：学历专业 / 知识技能 / 证书经验 / 职业素养。学历专业写学历层次与专业；知识技能写仪器、方法学、软件等；证书经验写执业/资格证与年限；职业素养写责任心、沟通、团队协作等。
4. 全文 400–600 字（含标点与序号）。
5. 不得出现性别、年龄、婚育、籍贯、民族、户籍、政治面貌、宗教等敏感信息。
6. 不得编造公司专属事实（具体产品线、内部系统名、内部实验室名、具体薪资以外的承诺）。需求没给的信息可写行业通用表述，但不得声称是公司特有要求。
7. 只输出 JSON，不要解释文字，不要 Markdown 代码块以外的内容。

已知招聘需求（JSON，唯一事实来源；未出现的字段不要臆测具体值）：
{json.dumps(known, ensure_ascii=False, indent=2)}

标准岗位画像参考（仅用于能力/经验的通用措辞，不得写进职位名称，也不得当作公司专属事实）：
{_profile_block(profile)}

同岗位 JD 模板参考（仅用于措辞，不得照抄为本次专属要求）：
{_template_block(related_jd)}

输出 JSON 契约：
{{
  "title": "行业通用职位名称",
  "responsibilities": ["职责1", "职责2", "…"],
  "requirements": [
    {{"label": "{_ALLOWED_LABELS[0]}", "text": "学历与专业要求"}},
    {{"label": "{_ALLOWED_LABELS[1]}", "text": "知识技能要求"}},
    {{"label": "{_ALLOWED_LABELS[2]}", "text": "证书与经验要求"}},
    {{"label": "{_ALLOWED_LABELS[3]}", "text": "职业素养要求"}}
  ],
  "bonus_points": ["加分项1", "…"]
}}
"""


def _normalize_draft(payload: dict[str, Any]) -> dict[str, Any]:
    """校验并清洗 LLM 输出；不合契约时抛 :class:`JdDraftUnavailable`。"""
    if not isinstance(payload, dict):
        raise JdDraftUnavailable("JD_DRAFT_INVALID_CONTRACT")
    inner = payload.get("jd") if isinstance(payload.get("jd"), dict) else payload
    title = sanitize_title(inner.get("title") or "")
    responsibilities = _clean_list(inner.get("responsibilities") or inner.get("core_responsibilities"))
    requirements_raw = inner.get("requirements") or []
    requirements: list[dict[str, str]] = []
    if isinstance(requirements_raw, str):
        requirements_raw = _clean_list(requirements_raw)
    for item in requirements_raw:
        if isinstance(item, str):
            match = re.match(r"^([^:：]{2,6})[:：]\s*(.+)$", item)
            label, text = (match.group(1), match.group(2)) if match else ("其他", item)
        elif isinstance(item, dict):
            label = str(item.get("label") or item.get("category") or "其他").strip()
            text = _clean_text(item.get("text") or item.get("value") or item.get("name"))
        else:
            continue
        text = _clean_text(text)
        if not text:
            continue
        label = _REQUIREMENT_LABEL_MAP.get(label.replace(" ", ""), "其他")
        requirements.append({"label": label, "text": text})
    bonus = _clean_list(inner.get("bonus_points") or inner.get("bonus"))
    if not title or not responsibilities:
        raise JdDraftUnavailable("JD_DRAFT_INCOMPLETE")
    return {
        "title": title,
        "responsibilities": responsibilities,
        "requirements": requirements,
        "bonus_points": bonus,
    }


def _merge_requirements(existing: list[str], draft_items: list[dict[str, str]]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for text in existing:
        token = text.strip()
        if token and token not in seen:
            seen.add(token)
            merged.append(token)
    for item in draft_items:
        text = f"{item['label']}：{item['text']}"
        if text not in seen:
            seen.add(text)
            merged.append(text)
    return merged


def draft_request_jd(request_id: str, fields: dict[str, Any], profile: dict[str, Any] | None = None,
                     related_jd: Any | None = None, *,
                     analyzer: HiringIntentAnalyzer | None = None) -> dict[str, Any]:
    """让 LLM 按规范起草 JD 并并入 ``fields``，返回结构化 JD。

    ``analyzer`` 仅为测试注入（默认 :meth:`HiringIntentAnalyzer.from_env`）。
    """
    engine = analyzer or HiringIntentAnalyzer.from_env()
    prompt = build_draft_prompt(fields, profile, related_jd)
    try:
        payload = engine._call(prompt)
    except HiringAnalysisError as exc:
        raise JdDraftUnavailable(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 网络/序列化异常统一降级
        raise JdDraftUnavailable(f"{type(exc).__name__}: {exc}") from exc

    draft = _normalize_draft(payload)
    merged = dict(fields)
    merged["position_title"] = draft["title"]
    merged["core_responsibilities"] = draft["responsibilities"]
    merged["hard_requirements"] = _merge_requirements(
        [str(x).strip() for x in fields.get("hard_requirements") or [] if str(x).strip()],
        draft["requirements"],
    )
    if draft["bonus_points"]:
        bonus = [str(x).strip() for x in fields.get("bonus_points") or [] if str(x).strip()]
        merged["bonus_points"] = list(dict.fromkeys([*bonus, *draft["bonus_points"]]))
    return build_request_jd(request_id, merged, profile=profile, related_jd=related_jd)


__all__ = [
    "JdDraftUnavailable",
    "build_draft_prompt",
    "draft_request_jd",
    "KSAO_LANES",
]
