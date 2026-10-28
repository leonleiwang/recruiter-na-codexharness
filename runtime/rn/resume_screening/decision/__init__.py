"""v5 候选人决策链路（活跃路径）。

数据流：Resume Facts → LLM Semantic Evidence → Evidence Grounding →
deterministic Candidate Decision。

- ``job_contract_v3``            简历事实规范化（脱敏）与 JD 合同构造。
- ``semantic_evidence_evaluator`` LLM 语义证据评估器（只产证据，不打分）。
- ``evidence_grounding``         把模型引用的证据回挂到简历事实，并支持修复。
- ``decision_evidence_evaluator`` v5 Candidate Decision 的严格证据契约评估器。
- ``criteria_adapter_v5``        从政策文件生成「实际判定条件」。
- ``candidate_decision_v5``      确定性通过/待核实/不通过判定与推荐排序。
- ``screening_v5``               ``HiringDecisionScreeningService`` 服务入口，
                                 对外暴露的候选人筛选入口。

本子包内不允许出现数值评分（ProfileMatch/JDMatch/RQ/CS），也不允许 import
``..scoring`` 下的任何模块。
"""
