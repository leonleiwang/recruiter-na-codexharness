"""简历结构化分析与三维度标签匹配层（analysis）。

三层分工（均为纯确定性代码，不调用 LLM、不写库、不读环境变量）::

    fields.py        文本 → 指定字段（结构化简历事实）
    tags.py          简历事实 → 能力 / 经验 / 特质 三维度标签（带逐字证据）
    requirements.py  岗位要求（政策 JSON 或 JD 文本）→ 三维度要求清单
    matching.py      要求清单 × 简历标签 → 三维度匹配报告（辅助参考，不进 v5 正式决策）

``matching`` 产出的是「辅助匹配视图」：只陈述已命中 / 有线索 / 未提及，
并给出按标签加权的覆盖度百分比。它不是录用评分，也不得写入 v5
Candidate Decision 的决策字段（正式决策仍由 ``decision/candidate_decision_v5.py``
基于证据与政策判据产出）。
"""
