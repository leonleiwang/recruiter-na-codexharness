---
name: hiring-request
description: 用人部门发起、补充、确认并推进自己的招聘需求。标准岗位画像只在后台补全；Recruiter-Na v5 对已支持岗位使用 Candidate Decision，而不是候选人数值评分。
---

# 发起招聘需求

这是 Recruiter-Na v5 面向**用人部门**的核心能力。

## 产品边界

- 用户只描述自己的招聘需求，不需要查看或维护标准岗位画像、`decision_mode`、`evidence_hints` 等招聘后台规则。
- 标准画像只作为后台补全器；命中画像后只询问**本次招聘的增量/特殊要求**。
- JD 模板只作为措辞参考，不得偷偷增加硬条件。
- 生产简历只由 Host Hiring Workflow / ResumeRepository(MySQL) 访问。
- v5 正式筛选不产生候选人数值总分。LLM 只负责语义事实与证据，Python 负责确定性 Candidate Decision。

## 用人部门流程

1. 用户自然语言说明“我要招一个……”以及已知信息。
2. Host Workflow 对已核验标准画像做语义匹配：MATCHED / AMBIGUOUS / NO_MATCH。
3. MATCHED：标准画像留在后台，只追问这次招聘仍缺的字段或特殊要求。
4. NO_MATCH：仍可完成招聘需求与 JD；但若该岗位尚无 v5 Decision Policy，开始筛选时必须明确告知暂不支持，**不得回退旧评分系统**。
5. AMBIGUOUS：只问一个最能区分画像的问题。
6. 生成本次招聘专属 JD，用户确认“开始筛选”。
7. 已支持岗位执行：Resume Facts → Semantic Evidence → Grounding → deterministic Candidate Decision。
8. 每次只向该需求 owner 推荐 3 人；“换一批”展示下一组，不得重复。
9. 候选池耗尽后进入 WAITING_HR_SOURCING。

## v5 Candidate Decision 合同

内部至少区分三个事实状态：

- `satisfied`：证据支持满足要求。
- `not_satisfied`：证据明确支持不满足要求。
- `unknown`：简历无法可靠判断。

证据强度使用独立字段 `evidence_status = confirmed / partial / none`，不得把“证据弱”等同于“不满足”。

只有两类情况允许自动 FAIL：

1. Eligibility hard check 有**明确、grounded** 的失败事实；
2. 内部规则为 `hard_gate` 且 `criterion_result = not_satisfied`，同时证据 Grounding 通过。

`UNKNOWN` 永远不得自动淘汰，必须进入 `needs_verification` 或面试确认。`resume_evidence`、`interview_verify`、`preference` 只是内部执行模式，不向用人部门要求配置。

## 当前 v5.0.2 筛选作用域

正式 Candidate Decision 当前仅启用已验收的**检验技术员** Policy（含已核验的检验技术员应届/社招画像）。微生物、NGS 等其他专门岗位不得仅因标题包含“检验技术员”而套用本 Policy。其他岗位仍可创建需求和生成 JD，但开始正式候选筛选时应返回 unsupported policy，不得近似套用，也不得启用 legacy scoring。
