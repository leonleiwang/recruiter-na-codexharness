"""简历解析与筛选决策引擎（Resume Screening Engine）。

这个包把 Recruiter-Na 里所有「简历解析 / 简历筛选 / 候选人决策 / JD 生成」相关的
模块集中到一处，便于单独演进、测试和替换。六块的分工：

上传解析 + JD 标签命中率匹配（辅助视图，不进入正式决策）：

- ``ingest``    文件 → 文本：PDF（PyMuPDF）/ DOCX / 图片（Windows OCR）/ 纯文本，
                统一成 ``DocumentText``；引擎不可用时抛错，不静默返回空串。
- ``analysis``  文本 → 字段（``fields``）→ 能力/经验/特质标签（``tags``）→ 岗位标签要求
                （``requirements``：政策 / JD 文本 / 生成 JD）→ 匹配报告（``matching``）：
                综合匹配度 = 命中标签数 / 适用标签数（只算能力/经验），
                学历/证书/地点单列核验，需求方显式提出的年龄不符即「不推荐」；全部确定性、不调 LLM。
- ``service`` / ``cli``  编排上述链路，输出 JSON / Markdown 报告，
                入口 ``python -m rn.resume_screening.cli analyze|requirements|jd``。

JD 生成（不评分）：

- ``jd``        招聘需求语义抽取（``intake``）→ 结构化 JD（``generator``）→
                已验证模板库措辞参考（``templates``）。

v5 正式链路与历史链路：

- ``parsing``  简历入库与事实抽取：JSONL / MySQL / 本地 JSON 后端、文本简历解析、
              简历仓库门面（ResumeRepository）。
- ``scoring``  v3 / v4 的确定性数值评分链路（RQ / ProfileMatch / JDMatch / CS）。
              **legacy**：保留用于回归对比与回滚分析，不进入 v5 公开链路。
- ``decision`` v5 活跃链路：Resume Facts → LLM Semantic Evidence → Evidence
              Grounding → deterministic Candidate Decision，不产生数值分数。

依赖方向固定为 ``service/cli → analysis → ingest``、``decision → scoring → parsing``
（解析层不感知上层），禁止反向 import；``analysis`` 产出的匹配度只是辅助参考，
不得写回 ``decision`` 的判定结果。

子包 ``__init__`` 刻意不 eager import：``parsing`` 与 ``decision`` 之间通过
``rn.mail_watch`` 等外部模块存在间接引用，集中 import 容易产生循环依赖；
请直接使用完整路径导入具体模块，例如::

    from rn.resume_screening.service import analyze_resume_file
    from rn.resume_screening.decision.screening_v5 import HiringDecisionScreeningService
    from rn.resume_screening.parsing.resume_repository import ResumeRepository

模块地图、数据流与改动指引见同目录 ``README.md``。
"""
