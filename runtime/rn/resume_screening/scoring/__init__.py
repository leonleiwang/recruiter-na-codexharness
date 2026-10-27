"""v3 / v4 确定性数值评分链路（legacy，未进入 v5 公开链路）。

- ``scoring_engine_v3``       RQ1-RQ4 与 JM/CS 的确定性计算（需要上游语义证据）。
- ``scoring_v4``              ProfileMatch / JDMatch 加权混合（40/60）与 RQ 混合。
- ``screening_pipeline_v3``   v3 合同优先筛选流水线。
- ``screening_v4``            v4 候选人排序服务 ``HiringScreeningService``。

v5 的活跃路径明确禁止 import 本子包（见 ``scripts/verify.sh`` 第 7 段与
``tests/test_no_active_scoring_v5.py``）；这里保留的唯一目的是回归对比与回滚。
"""
