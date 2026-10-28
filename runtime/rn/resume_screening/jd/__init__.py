"""JD 生成层：需求语义抽取 → 结构化 JD → 模板措辞参考。

- ``intake``    LLM 招聘需求抽取与标准画像匹配（``HiringIntentAnalyzer``）。
- ``draft``     LLM 按对外发布 JD 撰写规范起草职责/任职要求（``draft_request_jd``，
                 不可用时抛 ``JdDraftUnavailable`` 由调用方回退）。
- ``quality``   撰写规范常量与自检（``validate_jd`` / ``render_quality_report``）。
- ``generator`` 需求字段 + 画像 + 相关模板 → 结构化 JD（``build_request_jd``）；
                发布版文档 ``render_jd_document``（岗位职责 + 分组任职要求），
                聊天摘要 ``render_jd_summary``。
- ``templates`` 已验证 JD 模板库（``JDRepository``，精确/相关检索分离）。
- ``presentation`` 「最后呈现」三段成稿（``render_presentation``：岗位职责 / 任职要求 / 匹配标签）。

本层只生成与查询 JD，不做候选人评分；JD 的持久化（招聘工单）与聊天编排留在
``rn.hiring_requests`` / ``rn.hiring_workflow_v5``。子模块按需导入，``__init__``
不做 eager import。
"""
