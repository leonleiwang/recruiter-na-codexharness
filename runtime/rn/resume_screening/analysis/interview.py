"""面试问题生成：把「待验证清单」和待核实的硬性条件变成可以直接问出口的问题。

匹配报告里的「待验证清单」只说明了还差哪几项（简历没写 / 只有线索），
招聘方真正要做的是面试验证，所以这里按标签名给一条针对性的问题，再按
硬性条件（学历/证书/地点/年龄）给出证件核对类问题。问题只是提词器，
不参与打分，也不改变判定。
"""
from __future__ import annotations

from typing import Any

# 标签名 → 面试问题（按实际要核验的行为写，不评价候选人）。
QUESTION_BY_TAG: dict[str, str] = {
    "快速学习能力": "请举一个你短期内学会新设备/新流程并独立上手的例子：学了多久、怎么验证自己学会了？",
    "团队合作能力": "请举一个你和同事或临床科室配合完成任务的例子：你的具体分工是什么？",
    "沟通表达能力": "请举一个你向临床医生解释结果或交接异常情况的例子：对方最后是否理解并接受了？",
    "专业理论知识": "请选一个你熟悉的检验项目（如血常规/生化/免疫），说明原理以及异常结果怎么判断。",
    "规范操作与执行能力": "请完整描述一次你按 SOP 做室内质控/室间质评的流程；如果质控失控你会怎么处理？",
    "问题分析与解决能力": "请讲一次你排查异常结果或仪器故障的过程：从发现、定位原因到最后怎么解决。",
    "试剂耗材管理能力": "请说明你平时怎么做试剂效期管理、批号比对和库存核对，出过什么问题、怎么补救的。",
    "仪器操作能力": "请说明你独立操作过的检验仪器型号、日常保养项目，以及遇到过哪些常见故障。",
    "样本检测能力": "请说明你独立完成过的检测项目、涉及哪些标本类型，以及结果复核是怎么做的。",
    "检验科经验": "请说明你在检验科或第三方实验室的具体岗位、做了多久、独立承担哪些工作。",
    "分子或微生物检测经验": "请说明你做过哪些分子/PCR 或微生物检测项目，用的什么方法和仪器。",
}

# 硬性条件 filter 字段 → 证件/信息核对问题。
QUESTION_BY_FILTER: dict[str, str] = {
    "education": "请确认你的学历层次、专业全称与毕业时间，并准备毕业证/学位证（后续核验证件）。",
    "certification": "请确认你持有的检验类资格证全称、编号与取得时间；未取得的请说明是否已报名待考（后续核验证件）。",
    "location": "请确认你的现居地、期望工作城市，以及可接受的通勤范围。",
    "age": "请确认你的出生年份/年龄，与身份证一致。",
}

_DEFAULT_TAG_QUESTION = "请说明你在「{name}」方面的具体经历：做过什么、怎么做的、结果如何？"


def _tag_question(name: str) -> str:
    return QUESTION_BY_TAG.get(name) or _DEFAULT_TAG_QUESTION.format(name=name)


def build_interview_questions(matching: dict[str, Any], *, limit: int = 0) -> list[dict[str, Any]]:
    """Build an ordered interview checklist from one matching report.

    Each item: ``kind``（tag / hard）、``name``、``state_label``（仅标签）、
    ``question``、``evidence``（简历里已有的线索，便于面试时追问）。
    顺序与判定报告一致：先标签待验证项，再硬性条件。
    """
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for row in matching.get("verification_items") or []:
        name = str(row.get("name") or "").strip()
        if not name or ("tag", name) in seen:
            continue
        seen.add(("tag", name))
        items.append(
            {
                "kind": "tag",
                "name": name,
                "dimension": row.get("dimension", ""),
                "state": row.get("state", ""),
                "state_label": row.get("state_label", ""),
                "question": _tag_question(name),
                "evidence": row.get("evidence_hints") or "",
            }
        )

    for row in matching.get("hard_filter_hints") or []:
        status = str(row.get("status") or "").strip()
        if status != "待核实":
            continue
        filter_key = str(row.get("filter") or "").strip()
        name = str(row.get("requirement") or filter_key or "硬性条件").strip()
        if ("hard", name) in seen:
            continue
        seen.add(("hard", name))
        items.append(
            {
                "kind": "hard",
                "name": name,
                "dimension": "hard",
                "state": "",
                "state_label": status,
                "question": QUESTION_BY_FILTER.get(filter_key, "请确认这一项的信息，并准备相应证明材料。"),
                "evidence": row.get("evidence") or "",
            }
        )

    if limit and limit > 0:
        items = items[:limit]
    return items


def render_interview_markdown(matching: dict[str, Any], *, limit: int = 0) -> str:
    """Render the interview checklist as a Markdown section (empty string when nothing pending)."""
    questions = build_interview_questions(matching, limit=limit)
    if not questions:
        return ""
    lines: list[str] = ["## 面试问题（按待验证项生成）", ""]
    lines.append("面试时逐项确认下面的问题，答案回填后再改判定；问题只作提词，不自动改变结论。")
    lines.append("")
    for index, item in enumerate(questions, start=1):
        label = f"{item['name']}（{item['state_label']}）" if item.get("state_label") else item["name"]
        lines.append(f"{index}. **{label}**：{item['question']}")
        evidence = str(item.get("evidence") or "").strip()
        if evidence:
            lines.append(f"   - 简历线索：{evidence}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "QUESTION_BY_FILTER",
    "QUESTION_BY_TAG",
    "build_interview_questions",
    "render_interview_markdown",
]
