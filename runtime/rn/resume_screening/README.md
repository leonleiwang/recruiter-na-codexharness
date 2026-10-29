# 简历解析与筛选决策引擎（resume_screening）

本目录集中 Recruiter-Na 中所有「简历解析 / 简历筛选 / 候选人决策 / JD 生成」功能，是
后续精修的唯一入口。包外模块只允许通过下面列出的公开入口访问本包。

包内分为四条互不越界的链路：

1. **上传解析 + 标签命中率匹配（v5 之外的辅助视图）**：`ingest/` → `analysis/` → `service.py` / `cli.py`。
   主链路是「招聘需求字段 → 生成 JD → JD 拆成能力/经验标签 → 简历命中几个标签」：
   把 PDF / DOCX / 图片 / 文本简历解析成指定字段，打能力/经验/特质标签，与 JD 标签比对，
   **综合匹配度 = 命中标签数 / 适用标签数**（只算能力/经验，特质只做面试关注点不计分）。
   学历/证书/地点单列核验；年龄条件仅提示人工合规审查，不影响推荐结论与顺序。
   这份匹配度**不写回 v5 决策、不产生录用建议**，正式判定仍只走 `decision/`。
2. **v5 正式筛选链路**：`decision/`（无数值评分，Resume Facts → LLM 语义证据 → 证据接地 → 确定性判定）。
3. **v3/v4 历史评分链路**：`scoring/`，仅保留给回归脚本与历史测试，v5 活跃文件禁止 import。
4. **JD 生成**：`jd/`（招聘需求语义抽取 → 结构化 JD → 已验证模板库措辞参考）。
   只产出 JD，不做候选人评分；不做关键词猜测，模板只作措辞参考、不冒充专属要求。

## 目录结构

```
runtime/rn/resume_screening/
├── ingest/                         # ① 文件 → 纯文本（DocumentText）
│   ├── document.py                 #   load_document / load_bytes / DocumentText / 类型判定
│   ├── ocr.py                      #   OCR 引擎链：windows-ocr → rapidocr → tesseract
│   └── _windows_ocr.ps1            #   PowerShell 5.1 + WinRT Windows.Media.Ocr 桥
├── analysis/                       # ② 文本 → 字段 → 三维标签 → JD 标签要求 → 命中率报告
│   ├── textutil.py                 #   归一化（OCR 逐字空格/标点）、证据片段
│   ├── fields.py                   #   extract_resume_fields / looks_like_resume（22 个字段）
│   ├── cues.py                     #   能力/经验/特质线索词表（19 条）与 canonicalize
│   ├── requirements.py             #   政策 JSON / JD 文本 / 生成 JD → 标签要求（硬性条件单列）
│   ├── tags.py                     #   简历 → 三维度标签（带证据与置信度）
│   └── matching.py                 #   要求 × 标签 → 标签命中率/结论/缺口/硬性条件提示
├── service.py                      # ③ 编排 + Markdown/JSON 报告 + 落盘
├── cli.py                          # ④ python -m rn.resume_screening.cli analyze|requirements|jd
├── paths.py                        # 仓库根与默认路径（parents[3]）
├── jd/                             # JD 生成（不评分）
│   ├── intake.py                   # HiringIntentAnalyzer：LLM 需求抽取 + 标准画像匹配
│   ├── generator.py                # build_request_jd / render_jd_document（发布版）/ render_jd_summary
│   └── templates.py                # JDRepository：已验证 JD 模板库（精确/相关检索分离）
├── parsing/                        # 简历入库与事实抽取（v5 输入侧）
│   ├── resume_db.py                # ResumeBackend 接口、JsonResumeStore、ResumeHttpBackend、
│   │                               #   parse_text_resume（文本简历 KV 解析）、sha256 工具
│   ├── resume_jsonl.py             # JSONL 只读后端 ResumeJsonlBackend + extract_profile_fallbacks
│   ├── resume_mysql.py             # MySQL 生产后端 ResumeMySQLBackend
│   └── resume_repository.py        # 仓库门面 ResumeRepository（生产 fail-closed）
├── scoring/                        # v3/v4 确定性数值评分（legacy，v5 活跃链路禁用）
│   ├── scoring_engine_v3.py        # RQ1-RQ4、JM/CS 计算
│   ├── scoring_v4.py               # ProfileMatch/JDMatch 40/60 混合
│   ├── screening_pipeline_v3.py    # v3 筛选流水线
│   └── screening_v4.py             # v4 排序服务 HiringScreeningService
└── decision/                       # v5 活跃链路（无数值评分）
    ├── job_contract_v3.py          # Resume Facts 规范化（性别/年龄/证件号等脱敏）
    ├── semantic_evidence_evaluator.py
    ├── evidence_grounding.py
    ├── decision_evidence_evaluator.py
    ├── criteria_adapter_v5.py      # 政策文件 → 实际判定条件
    ├── candidate_decision_v5.py    # 确定性判定 + 推荐排序
    └── screening_v5.py             # HiringDecisionScreeningService（服务入口）
```

## 对外入口（包外模块使用的稳定 API）

### 上传简历 → 匹配度（辅助参考）

| 入口 | 用途 |
| --- | --- |
| `resume_screening.service.analyze_resume_bytes(data, filename=…, job/job_text=…)` | 上传流（图片/Word/PDF 字节）端到端分析 |
| `resume_screening.service.analyze_resume_file(path, …)` | 单个简历文件端到端分析 |
| `resume_screening.service.analyze_resume_files(paths, …)` | 多份简历同一岗位排序（单文件失败进 `errors`） |
| `resume_screening.service.analyze_text(text, …)` | 已有文本直接分析（不经过 ingest） |
| `resume_screening.service.write_report` / `write_batch_report` | 落盘 JSON + Markdown 到 `workspace/runs/resume-screening-analysis/` |
| `resume_screening.ingest.document.load_document` / `load_bytes` | 只做文件解析，拿 `DocumentText` |
| `resume_screening.analysis.fields.extract_resume_fields(text)` | 只做字段抽取（22 个字段，见 `RESUME_FIELD_KEYS`） |
| `resume_screening.analysis.tags.extract_resume_tags(fields)` | 只做三维度标签 |
| `resume_screening.analysis.requirements.build_job_requirements(job=… / job_text=…)` | 只做政策/JD 文本的岗位要求 |
| `resume_screening.analysis.requirements.requirements_from_jd(jd)` | 生成 JD → 标签要求（职责/能力/加分项→标签，学历/证书/地点/年龄/其他→硬性条件） |
| `resume_screening.analysis.matching.match_requirements(requirements, tags, fields)` | 只做匹配报告（标签命中率 + 硬性条件提示 + 年龄合规提示） |

`analyze_*` 与 `analyze_document` 都接受 `requirements_doc=`：传入 `requirements_from_jd(...)` 的结果即按
「生成 JD 的标签」评分，跳过 `job/job_text/policy` 的岗位要求推理（CLI 的 `--fields-file` / `--jd-json` 走的就是它）。

### JD 生成

| 入口 | 用途 |
| --- | --- |
| `resume_screening.jd.intake.HiringIntentAnalyzer` | 自然语言招聘需求 → `fields` + `profile_match`（LLM，无关键词兜底） |
| `resume_screening.jd.generator.build_request_jd(request_id, fields, profile=None, related_jd=None)` | 需求字段 + 画像 + 相关模板 → 结构化 JD（`schema_version=4.0`）；职责只放用户明确给的内容，画像叙事放 `profile_context` |
| `resume_screening.jd.generator.render_jd_document(jd, *, external=False)` | **发布版 JD**（Markdown）：岗位名称 + 部门/地点/HC/薪资 → `## 岗位职责` → `## 岗位定位` → `## 任职要求`（学历/专业/经验/证书/技能与能力/职业素养/其他硬性条件）→ `## 加分项`；`external=True` 时去掉内部管理信息、岗位定位与公司标识，并过滤「其他」里的敏感条件 |
| `resume_screening.jd.generator.requirement_groups(jd)` | 硬性条件拆句归类（学历/专业/经验/技能/证书/素养/其他），供文档与核对复用 |
| `resume_screening.jd.generator.render_jd_summary(jd)` | JD 的聊天摘要（Markdown），用于 workflow 对话，不用于发布 |
| `resume_screening.jd.quality.validate_jd(jd, *, document=None, external=False)` | 对外发布规范自检（结构/强动词/条数/KSAO/篇幅/合规），返回 `JdQualityReport`；`render_quality_report` 出 Markdown |
| `resume_screening.jd.draft.draft_request_jd(request_id, fields, profile=None, related_jd=None, *, analyzer=None)` | 可选 LLM 起草（prompt 内含发布规范），结果并回同一套 `build_request_jd`；不可用时抛 `JdDraftUnavailable` |
| `resume_screening.jd.templates.is_related_title(query, title)` | 保守模板匹配门槛（最长公共子串 ≥ 3），避免把不相干岗位当同岗位模板 |
| `resume_screening.jd.templates.JDRepository` | 已验证 JD 模板库：`search_exact` / `search_related` / `get` / `health` |

`rn.repositories` 仍保留 `JDRepository` 的惰性再导出（`__getattr__`）以兼容既有调用；
新代码请直接用 `resume_screening.jd.templates`。

### v5 正式链路

| 入口 | 用途 | 现有调用方 |
| --- | --- | --- |
| `resume_screening.decision.screening_v5.HiringDecisionScreeningService` | v5 候选人筛选服务 | `rn/host_tool.py`、`scripts/screen-local.py` |
| `resume_screening.decision.screening_v5.UnsupportedScreeningPolicy` | 不支持岗位的拒答异常 | `rn/hiring_workflow_v5.py` |
| `resume_screening.parsing.resume_repository.ResumeRepository` | 简历库门面 | `rn/repositories/__init__.py`、`rn/data_doctor.py` |
| `resume_screening.parsing.resume_db.{ResumeBackend,JsonResumeStore,parse_text_resume,sha256_of_text}` | 入库与解析 | `rn/mail_watch.py`、`rn/cli.py` |
| `resume_screening.decision.job_contract_v3.normalize_resume_facts` | 事实规范化 | `rn/mail_watch.py` |
| `resume_screening.decision.criteria_adapter_v5.load_inspection_technician_policy` | 读取岗位政策 | `rn/cli.py` |

v3/v4 评分模块（`scoring/`）只被回归脚本与历史测试引用，**不得**被 v5 活跃文件
import —— `scripts/verify.sh` 第 7 段与 `tests/test_no_active_scoring_v5.py` 会守住这条边界。
`analysis/matching.py` 产出的匹配度同理：只是辅助视图，不得进入 `decision/`。

## 数据流

### A. 上传解析 + 标签命中率匹配（辅助视图，`ingest → analysis → service`）

主链路（需求字段 → JD → 标签）：

```
招聘需求字段（职位信息 + 用户自定义要求：学历/经验/年龄…）
        │ jd.generator.build_request_jd（可继承标准画像的能力/经验/描述）
        ▼
结构化 JD（responsibilities / key_experience_skills / bonus_points / hard_requirements）
        │ analysis.requirements.requirements_from_jd
        │   ├─ 职责 + 关键能力 → 能力/经验标签（「必须」）
        │   ├─ 加分项 → 「优先」标签
        │   └─ 学历/证书/地点/年龄/其他硬要求 → hard_filters（单列核验，不进分母）
        ▼
岗位标签要求（J01…）
```

简历侧：

```
图片 / Word / PDF / 文本
        │ ingest.document.load_document / load_bytes
        │   ├─ PDF：PyMuPDF 文本层；扫描页渲染后走 OCR
        │   ├─ DOCX：段落 + 表格
        │   └─ 图片：Windows OCR（WinRT，zh-Hans-CN）
        ▼
DocumentText（text + engines + warnings）
        │ analysis.fields.extract_resume_fields（22 个字段，缺失项进 missing_fields）
        ▼
结构化字段 dict
        │ analysis.tags.extract_resume_tags（每个标签带 evidence 片段 + confidence）
        ▼
三维度简历标签（capability / experience / trait）
        │ analysis.matching.match_requirements（matched / weak / not_found）
        ▼
综合匹配度 = 命中标签数 / 适用标签数 → service 落盘 JSON + Markdown
```

匹配语义要点：

- 每条要求三态：`matched`（命中，简历有证据）/ `weak`（待验证，有线索待确认）/
  `not_found`（待验证，简历未提及）；「**未命中**」只留给有反向证据的情况
  （`contradicted`，如简历明确写了没做过/专业不符），**没写不等于不会**，一律进面试待验证；
- **综合匹配度 = 标签命中率**：`tag_hit_rate = tag_hit_count / tag_applied_count`，
  `overall_percent == tag_hit_percent`、`score_basis == "tag_hit_rate"`；
  只统计能力/经验标签（特质不计分），`weak` 不算命中但仍在报告里展示；
  旧的维度加权覆盖度保留在 `overall_coverage`（能力 0.40 / 经验 0.35、level 加权）供追溯；
- 报告新增「待验证清单（面试/证件确认）」（`verification_items`）：所有未证实的计分项
  （weak + not_found）都列成面试要确认的问题，避免把「简历没写」当成「不会」；
- `decision_mode == "interview_verify"` 的要求未命中时算「待面试核实」，**不计入命中率分母**
  （简历没写 ≠ 不匹配），单列在 `interview_pending`；
- **特质不计分**：岗位特质要求与简历特质标签仍会展示，但既不进综合匹配度也不进缺口清单，
  只做面试关注点（`scored_dimensions` / `reference_dimensions` 可区分）；
- **年龄不用于自动决策**：需求中显式提出的年龄条件保留在 `hard_filters.age` 仅供人工合规审查。
  不读取或比较候选人年龄，不影响命中率、`disqualified`、推荐等级或排序；年龄缺失也不改变结果。
- **其他硬要求**（如「接受体检季加班」）进 `hard_filters["other"]`，逐条显示「待核实」，
  不计入标签命中率分母（简历通常不会写这类意愿）；
- 学历 / 证书 / 地点硬性条件在 `hard_filter_hints` 里逐条给「满足 / 待核实 / 不满足」，
  不进入命中率分母；
- 岗位要求若只有特质等参考项（没有能力/经验条目），`label` 为「仅参考信息（无计分要求）」、
  `overall_percent` 为 `null`；
- 只有硬性条件的 JD（例如「大专及以上，医学检验技术专业，持有检验士资格证；工作地点：杭州市西湖区」）：
  `requirements_available` 仍为 `True`（`requirements.has_usable_requirements` 把 `hard_filters` 也算作可用），
  `label` 为「仅硬性条件（无三维度要求）」、`overall_percent` 为 `null`，只看 `hard_filter_hints`，
  不把「没有标签要求」伪装成 0% 命中率；
- 多份简历批量排序：非「不推荐」优先，其次按 `tag_hit_rate` 降序（`match_rank_key`）；
  批量报告的「排序总览」先给每人**命中了几项**（`命中/适用（百分比）`），再列**待验证项**
  （`verification_items` 的标签名清单，面试/证件核验时确认），并用单独一列给出「未命中（有反证）」
  数量——简历没写不算未命中；
- **结论只有一套说法**：批量表与单份报告的结论列统一为「推荐 / 待核实 / 不推荐」
  （`matching.recommendation`）；「高匹配/较高匹配/中等匹配/偏低匹配/低匹配」档位（`label`）
  只保留为内部字段，不再对外显示，避免两套口径并存；
- **推荐语义（按 10 份真实简历人工回填校准）**：硬性条件不满足 → 「不推荐」；
  其余有计分标签时命中率 ≥ 40% → 「推荐」（进人工复核/面试）、< 40% → 「待核实」
  （命中率不足四成只说明标签写得少，不直接淘汰）；没有计分标签 → 空。命中率与结论分开看，
  结论只在报告 `recommendation` 字段，标签证据仍逐条列出；
- 敏感属性（性别/婚育/籍贯/身份证等）在 JD 文本侧整行剔除，进 `sensitive_excluded`，
  不进入要求列表、不参与排序；生成 JD 走结构化字段，敏感属性不生成评分要求，结构化年龄字段仅作合规提示；
- 报告固定带 disclaimer：辅助参考、非录用评分、不替代面试与证件核验。

- **词表口径（按 10 份真实检验技术员简历的人工回填校准）**：课程名不算经验证据
  （`_COURSE_LINE_RE` / `_course_free_corpus`，折行压缩后的证据片段也按课程标记判断）；
  轮转/轮岗行**算**经验接触证据（用户裁决：轮转接触过即视为经验，结论由面试复核）；
  「参加过学习培训/学习适应能力较好」这类泛泛表述带 `?` 前缀，只能判「待验证（有线索）」，
  不能靠数量叠加升级成命中；
  「临床医学检验技术（士/师）资格证」归一化为 检验士/检验师；通用词（仪器/设备/实验室/检验科…）
  单独出现不算命中；样本检测认 标本/样本处理系列；分子或微生物经验认 微生物/PCR/分子检验 等；
  快速学习只认被验证过的证据：**排名前 15%（含更前）**、培训合格证书、奖学金；
  GPA/绩点只看是否伴随排名，具体数值不参与判定（学校难度不可比），只有 GPA 数字 → 待验证；
  简历里自述「快速学习能力/学习能力强」带 `?` 标记，只能判「待验证（有线索）」，面试再确认；
  团队合作认 社团/协助检验医师/配合完成。
  人工核对闭环：`scripts/eval-matching.py` 生成 audit → `scripts/make-review-sheet.py` 生成
  `人工校验表.xlsx`（一人一行）→ 人工回填 → `eval-matching.py --review-xlsx` 输出结论一致率与
  标签单元一致率；`scripts/make-tag-spec-sheet.py` 另生成「标签口径表」（一行一个标签：维度/级别/
  判据方式/强词/弱词/规则/示例证据 + 10 份简历的当前判定格），用于直接修正词表口径。
  按 2026-10-26 人工裁决修正后：结论一致率 10/10、标签单元一致率 80/80。

岗位要求的三种来源：政策画像（`build_job_requirements(job=…)`）、自备 JD 文本
（`requirements_from_jd_text`）、已生成 JD（`requirements_from_jd`，CLI `--fields-file` / `--jd-json`）。

### B. v5 正式筛选链路（`parsing → decision`）

```
原始简历（PDF/DOCX/邮件/JSONL/MySQL）
        │ parsing/resume_db.parse_text_resume
        │ parsing/resume_jsonl.ResumeJsonlBackend / parsing/resume_mysql.ResumeMySQLBackend
        ▼
结构化 resume dict（RESUME_SCHEMA_KEYS）
        │ decision/job_contract_v3.normalize_resume_facts   ← 脱敏
        ▼
Resume Facts
        │ decision/semantic_evidence_evaluator（LLM，只产证据）
        ▼
Semantic Evidence
        │ decision/evidence_grounding.validate_decision_grounding（可 repair）
        ▼
Grounded Evidence
        │ decision/candidate_decision_v5.decide_candidate（确定性）
        ▼
Candidate Decision + 推荐排序（screening_v5 汇总、写 run 目录）
```

### C. JD 生成（`jd/intake → jd/generator`，模板库只作措辞参考）

```
用人经理自然语言需求
        │ jd.intake.HiringIntentAnalyzer.analyze_initial / analyze_update（LLM）
        ▼
fields + profile_match（MATCHED / AMBIGUOUS / NO_MATCH）
        │ 标准画像（ProfileRepository.raw）补全通用能力/经验与描述
        │ jd.generator.build_request_jd（确定性合并，不做关键词猜测）
        │   ├─ 硬性条件 → hard_requirements（HARD_GATE / VERIFY_ONLY）
        │   └─ 相关 JD 模板 → related_template（match_type=related，usage=wording_reference_only）
        ▼
结构化 JD（schema_version 4.0）→ CLI `jd` 写 jd.json + jd.md（jd.md = `render_jd_document` 发布版）
        │ 可选 --draft：jd.draft.draft_request_jd（LLM 起草，失败回退确定性生成）
        │ 可选 --external：对外版（去内部管理信息/岗位定位/公司标识/敏感条件）
        ▼
可选 --check：jd.quality.validate_jd 自检 → 打印报告 + 写 jd.quality.md（有 ERROR 退出码 4）
可选 --tags：requirements.requirements_from_jd 拆标签 → 打印标签表 + 写 jd.tags.md（内部筛选用，不发布）
```

发布版 JD 的硬约束（改动时必须保持；完整规范见 `docs/JD_WRITING_STANDARD.md`）：

- `## 岗位职责` 只写用户明确提供的职责；用户没给时，才用同岗位模板的职责作**措辞参考**并加
  引用块说明「发布前需用人部门确认」；画像 description 只进 `## 岗位定位`，不冒充职责。
- 职责 5–7 条、每条 15–30 字、强动词开头；任职要求 5–7 条且覆盖 KSAO 四维（学历专业/知识技能/
  证书经验/职业素养）；对外版全文 400–600 字；不出现公司标识与性别/婚育/年龄等敏感条件。
- `## 任职要求` 必须按 学历/专业/经验/证书/技能/素养/其他硬性条件 分组，来自 `hard_requirements`
  拆句归类；`request_key_experience_skills`（需求方明确给出的关键能力）归入「技能与能力」。
  画像继承的能力/经验（`key_experience_skills`）只作匹配标签来源，**不得**变成 JD 的任职要求条目
  ——否则「快速学习能力」「检验科经验」这类标签名会被误读成对外 JD 的要求。
- **两层结构**：JD 取自标准画像 + 本次实际需求（`cli jd` → `jd.md`）；匹配标签取自该 JD
  （`cli jd --tags` → `jd.tags.md`，标题「匹配标签（取自 JD：<岗位>，用于匹配候选人能力与经验）」）。
  标签是派生视图，不写回 `jd.md`，也不改 JD 的任职要求分组。
- 筛选口径（命中率标签、三态判定、推荐结论规则）不进 JD；JD 由 CLI 产出，聊天里不手写。
- `--tags` 产出的 `jd.tags.md` 是内部筛选视图（能力/经验标签计分、硬性条件单独核验；**特质不生成标签**，
  JD 的「职业素养」仍留在可发布 JD 的任职要求里作面试参考），由
  `requirements.render_requirements_markdown` 渲染，禁止并入可发布的 `jd.md`。
- **最后呈现（三段）**：`cli jd --presentation` 由 `jd/presentation.render_presentation` 合成
  `jd.presentation.md`，固定三个 `##` 大项——`## 岗位职责`、`## 任职要求`（加分项折入其中）、
  `## 匹配标签（取自 JD：<岗位>…）`。匹配标签**只列实际参与评分的标签名**（直接复用
  `requirements_from_jd` 的结果，成稿与命中率永远同一套标签；不含特质），写成一行能力、一行经验；
  判据方式、命中线索与硬性条件仍只在 `jd.tags.md`，不进聊天成稿。
  标签名必须是短标签：词表命中用词表名，未命中的描述句用 `_condense_tag_name` 压到 ≤12 字
  （如「分子或微生物检测经验」），绝不出现整句职责原文。
  聊天交付直接贴这份，`jd.md` 保持两节可发布版。

JD 的持久化（`jd_json`）和对话编排留在包外：`rn/hiring_requests.py`、`rn/hiring_workflow_v5.py`；
本包只负责「需求 → JD」本身。

## 改动指引

### 上传解析 + 匹配度

- **加/改解析字段**：先改 `analysis/fields.py` 的 `RESUME_FIELD_KEYS` 与抽取逻辑，
  再同步 `service.render_report_markdown` 的字段表格；`tags.py` 消费的是
  `raw_text` 优先的语料，字段变化一般不需要动标签。
- **加/改三维度线索词**：`analysis/cues.py`（`!` 前缀 = 强线索；强线索直接判 `matched`）。
  政策里新增判据时，必须同步补一条同名 cue，否则该要求永远只能靠关键词命中。
- **改岗位要求**：主链路用生成 JD（`requirements.requirements_from_jd`，CLI `--fields-file`/`--jd-json`）；
  政策画像（`knowledge/recruiting/policies/inspection_technician_v1.json` 的
  `capabilities/experiences/traits` + `profile_criterion_overrides`）仍可 `--job` 使用；
  自备 JD 文本走 `requirements.requirements_from_jd_text`（学历/证书/地点自动进 `hard_filters`）。
- **改 JD → 标签拆解**：`analysis/requirements.py::requirements_from_jd`（职责/能力→标签、
  加分项→「优先」、学历/证书/地点/年龄/其他硬要求→`hard_filters`）；
  年龄解析在 `parse_age_requirement`（区间/以下/以上/不超过；没有年龄语境不误判年份区间）。
- **改匹配判定**：`analysis/matching.py::_evaluate_requirement`（三态判定顺序）、
  `_dimension_block`（命中率分母）、`_match_label`（结论阈值）、`_hard_filter_hints`（硬性条件提示，
  含年龄 `blocking`）、`match_rank_key`（不推荐垫底）。
- **改计分口径**：主口径是标签命中率（`tag_hit_rate`）；`analysis/cues.py` 的
  `SCORING_DIMENSIONS`（默认能力/经验）与 `REFERENCE_DIMENSIONS`（默认特质，只展示不计分）
  决定哪些维度进分母，`requirements.DIMENSION_WEIGHTS` 只影响旧的加权覆盖度 `overall_coverage`。
- **改匹配度结论文案/免责声明**：`analysis/matching.py` 的 `DISCLAIMER` 与
  `render_matching_markdown`。
- **换 OCR / 加文件格式**：`ingest/ocr.py`（引擎链）与 `ingest/document.py`（类型判定与解析）。
  本机默认真实引擎是 Windows OCR（`powershell.exe` 5.1 + WinRT，识别语言只有 zh-Hans-CN）；
  部署到 Linux 时需补 rapidocr/tesseract 引擎（当前环境网络装不上 rapidocr）。

### v5 正式链路

- **卡片「符合标签 x/y」的口径**：`analysis/jd_card_tags.py` 直接复用
  `requirements_from_jd` 与 `match_requirements`；分子是该 JD 已命中的能力/经验标签，
  分母仅包含本次 JD 在简历阶段可核验的适用标签。已批准判据中的面试确认项从分子、分母同时排除，
  JD 确认清单也使用同一解析器并单列面试项目。可核验 3 项且全部命中时显示 3/3；
  可核验项缺少原文证据仍保留在分母，不以命中数反推分母。
  数量随 JD 的归类、去重和适用结果变化，不固定为 7/8/9，也不取 v5 policy 标签数。
  辅助引擎的资格提示、推荐结论与百分比不用于 v5 决策或推荐顺序；
  v5 继续负责资格、最终判定、待核实与面试重点。
  学历/检验资格证/工作地点单独展示；报考资格与待考不表示已持证，愿意到岗单独说明。
  特质只进面试重点。历史快照需重新筛选后才采用新口径。
  每位候选人的标签证据及阶段分组落盘为 `jd_tag_matching.json`。
  面试重点以「提问方向（标签）」展示，方向复用已批准判据描述，不重复罗列缺少证据的原因。
  JD 里写出的标准标签名（`cues.CUES` 的 name，如「沟通表达能力」「专业背景与项目经验」）
  优先按词表归类：不会被「沟通」判成特质、也不会因为带「专业」二字被并进学历硬条件；
  生成 JD 把它们列进 `hard_requirements` 时同样按标签处理，不当门槛。
  标签仍按已批准判据划分简历核验与面试确认阶段；面试确认项不进入简历阶段的分子或分母。
- **v5 内部参考标签**：原作者新增的 `_scan_resume_tags()` 继续按 policy 判据扫描，
  并应用 grounded 反证；`resume_tag_scan` 用于内部追溯与 v5 推荐顺序。
  它不覆盖经理端 JD 标签，卡片也不回退到该扫描或 LLM 计数。
- **改判定规则 / 阈值**：优先改政策文件，由 `decision/criteria_adapter_v5.py` 编译为实际条件；
  硬性门禁与三态判定在 `decision/candidate_decision_v5.py`。
- **改证据契约或提示词**：`decision/decision_evidence_evaluator.py`（v5）与
  `decision/semantic_evidence_evaluator.py`（通用 LLM 调用层）。
- **改推荐排序**：`decision/candidate_decision_v5.py::recommendation_order_key`。
- **改 run 目录/并发/重试**：`decision/screening_v5.py` 的 `HiringDecisionScreeningService`。

### 入库解析（parsing）

- `parsing/resume_db.py` 的 `RESUME_SCHEMA_KEYS` 与 `parse_text_resume`；JSONL 侧同步
  `parsing/resume_jsonl.py` 的 `_extract_*`；MySQL 侧同步
  `parsing/resume_mysql.py` 的 `_resume_row_to_dict`。三处字段名必须保持一致（JSONL 映射以 MySQL 为准）。

### JD 生成（jd）

- **改 JD 字段/结构**：`jd/generator.py::build_request_jd`（`schema_version`、`hard_requirements.gate_type`）；
  摘要文案在同文件 `render_jd_summary`。
- **改对外发布规范/自检**：`jd/quality.py`（条数/字数/强动词/KSAO/合规阈值与 `validate_jd`）；
  规范文档是 `docs/JD_WRITING_STANDARD.md`，改阈值时同步改文档与 `tests/test_resume_screening_jd_quality.py`。
- **改 LLM 起草**：`jd/draft.py` 的 `build_draft_prompt` 与 JSON 契约（`{title, responsibilities[], requirements[], bonus_points[]}`）；
  起草结果必须仍走 `build_request_jd` + `validate_jd`，不得绕过。
- **改需求抽取提示词/画像匹配**：`jd/intake.py` 的 prompt 与 `HiringAnalysisError` 契约；
  不允许加关键词或正则兜底（无 LLM 配置时按 `UNAVAILABLE` 继续完整需求采集）。
- **改模板库检索**：`jd/templates.py`（精确/相关分离、相关分数阈值与 `_domain_terms`）；
  库文件由 `scripts/build-data-foundation.py` 从源 Excel 重建为 `data/generated/jd/jd_library.json`，
  未核验条目（`source_status != "VERIFIED_SOURCE"`）会直接 `ValueError`。
- **JD 持久化与聊天编排**：不在本包——`rn/hiring_requests.py`（`jd_json`）与
  `rn/hiring_workflow_v5.py` 调用本包的 `build_request_jd` / `render_jd_summary`。

## 路径约定（移动后注意）

本包位于 `runtime/rn/resume_screening/<layer>/`，因此「仓库根目录」要向上四层：

```python
ROOT = Path(__file__).resolve().parents[4]   # runtime/rn/resume_screening/decision/x.py
```

涉及文件：`decision/criteria_adapter_v5.py`（政策文件路径）、
`decision/screening_v5.py`（默认 run 目录 `workspace/runs`）、
`scoring/screening_v4.py`（默认 run 目录）、
`parsing/resume_repository.py`（dev fixture `data/dev/resume_db.fixture.json`）。
`jd/templates.py` 用 `paths.REPO_ROOT` 定位 `data/generated/jd/jd_library.json`（不再自己数层级）。
`parsing/resume_jsonl.py` 通过向上查找 `.env.local` / `im/` 自行定位仓库根，不依赖层级。
新增的 `paths.py` 统一用 `parents[3]`（它本身在包根，不在 layer 子目录）。

## 常用命令

```bash
export PYTHONPATH=runtime

# —— 主链路：需求字段 → JD → 标签 → 命中率（年龄是硬门槛）——
python -m rn.resume_screening.cli jd --fields-file fields.json --profile-id PROFILE-412167F2937C \
    --out workspace/runs/jd-demo
python -m rn.resume_screening.cli analyze 简历.pdf --fields-file fields.json --profile-id PROFILE-412167F2937C
python -m rn.resume_screening.cli analyze 简历.pdf --jd-json workspace/runs/jd-demo/jd.json
python -m rn.resume_screening.cli analyze 简历A.docx 简历B.png --fields-file fields.json --out workspace/runs/demo

# —— 政策画像 / 自备 JD 文本 ——
python -m rn.resume_screening.cli analyze 简历.pdf --jd-file jd.txt
python -m rn.resume_screening.cli analyze --text "姓名：张三 电话：138..." --job 检验技术员 --json
python -m rn.resume_screening.cli requirements --job 检验技术员          # 只看三维度要求
python -m rn.resume_screening                               # 等价入口（__main__.py）

# —— 只生成 JD（不评分）——
python -m rn.resume_screening.cli jd --fields-file fields.json --json --no-write   # 结构化 JSON
python -m rn.resume_screening.cli jd --fields-file fields.json --summary --no-write  # 只看聊天摘要
python -m rn.resume_screening.cli jd --fields-file fields.json --external --check \
    --out workspace/runs/jd-publish                                     # 对外版 + 规范自检（jd.quality.md）
python -m rn.resume_screening.cli jd --fields-file fields.json --draft \
    --out workspace/runs/jd-draft                                       # 可选 LLM 起草，失败自动回退
python -m rn.resume_screening.cli jd --fields-file fields.json --external --tags \
    --out workspace/runs/jd-with-tags                                   # JD 正文 + 内部评分标签（jd.tags.md）
python -m rn.resume_screening.cli jd --fields-file fields.json --external --presentation \
    --out workspace/runs/jd-presentation                                # 最后呈现三段（jd.presentation.md）

# —— 回归与调试 ——
python -m pytest tests -q                                  # 全量回归
python scripts/screen-local.py --help                      # 本地 v5 筛选调试
python -m rn.resume_screening.scoring.scoring_engine_v3 --help   # legacy v3 评分
```

### 退出码与写盘约定

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功（批量时允许部分文件失败，失败项在报告 `errors` 里） |
| 2 | 输入/参数错误：`--text` 与文件同时给、`--fields-file`/`--jd-json` 与 `--jd-file`/`--job` 同时给、`--fields-file` 与 `--jd-json` 同时给、文件不存在、JD 文件解码失败、岗位要求为空（未加 `--allow-empty-requirements`） |
| 3 | 分析或写盘失败：单文件分析异常、报告写盘失败、批量全部失败（不会创建 `--out` 目录） |
| 4 | JD 规范自检未通过（`jd --check` 存在 ERROR，报告仍写 `jd.quality.md`） |

- `jd --external`：渲染对外发布版（去部门/紧急程度/到岗时间/岗位定位/公司标识，过滤其他硬性条件里的
  性别/年龄等敏感值）；`jd --check`：按 `docs/JD_WRITING_STANDARD.md` 自检并打印报告；
  `jd --tags`：把 JD 拆成评分标签（能力/经验计分、特质不生成标签、学历/证书/地点/年龄硬性条件单独核验）并写
  `jd.tags.md`，属内部筛选用，不得并入可发布 JD；
  `jd --presentation`：合成「最后呈现」三段（岗位职责 / 任职要求 / 匹配标签）并写 `jd.presentation.md`，
  聊天交付贴这份，`jd.md` 仍是可发布的两节正文；
  `jd --draft`：先让 LLM 起草职责/要求（需 `RECRUITER_LLM_*` 环境变量），不可用或不合规时回退确定性生成；
- `--fields-file` / `--jd-json`：岗位要求来自「生成 JD 拆出的标签」，与 `--jd-file` / `--job` 互斥；
  `--fields-file` 可再配 `--profile-id` / `--profile-file` / `--related-jd-title` / `--library-file`；

- `--overwrite`：默认不覆盖已有报告，同 stem 重复写会直接 `FileExistsError` 并提示加 `--overwrite`；
- `--allow-empty-requirements`：允许「岗位要求为空」继续分析（报告里 `requirements_available=false`，
  提示补 `--jd-file` 或 `--job`），默认 strict 模式直接拒绝；
- 写盘用「临时文件 + 原子替换」，stem 经 `_safe_component` 收敛（`../../evil` 只会落到 `out_dir/evil.json`）；
- 文本输入自动识别编码（UTF-8 BOM / UTF-16/32 BOM 或探测 / GB18030 兜底），
  因此 GBK 保存的 JD 文件也能直接 `--jd-file`。

## 语义覆盖、批量缓存与面试问题

- 语义覆盖（P1）：`analysis/textutil.py` 在归一化阶段把常用**繁体/异体字**折成简体
  （`醫學檢驗技術` → `医学检验技术`，`檢驗科` → `检验科`），词表只维护简体一份；
  词表补了行业**缩写**：`ISO15189` / `CNAS` / `CLIA`（规范操作）、`LIS` / `HIS`（仪器操作）、
  `CAPA` / `PDCA`（问题分析）。规则词表仍是保底层，LLM 抽取增强留待配置 `RECRUITER_LLM_*` 后接入。
- 批量缓存/断点续跑（P1）：`analyze --cache-dir <dir>` 按「文件内容 sha256 + 岗位要求指纹」
  缓存匹配结果，同一份简历同一份 JD 重跑直接复用（不再 OCR）；返回 JSON 里有
  `cache: {enabled, hits, misses}`。100+ 份批处理失败重跑只补缺失项。
- 面试问题（P1）：单份/批量报告的「待验证清单」之后新增 `## 面试问题（按待验证项生成）`，
  由 `analysis/interview.py` 把待验证标签与「待核实」硬性条件（学历/证书/地点）各生成一条
  针对性问题，附简历线索；只做提词器，不参与打分。
- 隐私保留期（P2）：`python scripts/purge-runs.py --older-than-days 30`（dry-run 先看）
  `--yes` 才真删；只允许清理 `workspace/` 之内，`workspace/test_resumes` 需显式
  `--include-test-resumes`。真实简历属 PII，不再需要时按此清理。
- JD 确认留痕（P2）：`jd --confirm-by 王主管 --confirm-note "职责第 3 条已核对"`
  额外写 `jd.confirmation.json`（确认人/时间/备注/JD ID），自检报告加一行
  `发布确认：已确认（…）`；不传则写 `发布确认：待用人部门确认`，可追溯哪份 JD 已被确认。
- 数据基础测试（P2）：`tests/test_data_foundation_v34.py` 里需要 14 份原始画像 PDF /
  JD 原始 Excel 的两条用例改为**缺源包时 skip**（发布环境有完整源包会自动恢复校验），
  CI 不再因本地缺数据长期红。

### PR #1 日期和模板兼容性说明

工作经历新增布尔字段 `duration_estimated`，标识起始或结束月份缺失；报告显示“月份不完整，时长估算”。
缺失结束月份采用同期、首尾月份计入的估算：2016.12–2019 为 37 个月，2019.02–2019 为 1 个月；
完整日期算法不变，只有年份的既有保守口径保留。估算不是确切履历事实，年限门槛附近仍须核实原始日期。
同岗位模板精确命中时 `related_template.match_type` 为 `exact`（其他仍为 `related`/`none`）；
模板职责仅进入 `reference` 并标注需用人部门确认，不进入已确认职责或评分标签。
