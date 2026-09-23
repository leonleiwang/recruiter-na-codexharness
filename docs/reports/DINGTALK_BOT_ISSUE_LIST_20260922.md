# Recruiter-Na 钉钉机器人 问题清单

**清单建立：** 2026-09-22
**适用对象：** 生产环境钉钉（DingTalk Stream）招聘机器人 `/opt/recruiter-na/app`
**代码基线：** 工作区未提交版本；已核对与部署包 `recruiter-na-codexharness.zip` 一致
（`hiring_workflow_v5.py` sha256 前 12 位 `0adbab2e43d4`）
**状态说明：** 已定位 / 已复现 / 待修复 / 已修复（代码） / 待运维
**相关详细记录：** [DINGTALK_CCC_CONTACT_MISREPLY_20260922.md](./DINGTALK_CCC_CONTACT_MISREPLY_20260922.md)
**修复批次：** 2026-09-23（代码 + 部署配置），回归：`pytest tests` 148 passed（另 2 项为公开版缺原始素材的环境失败）、`node --test im/*test.mjs` 4 passed、`verify.sh` 各项守卫通过

## 总览

| 编号 | 问题 | 严重级别 | 状态 |
| --- | --- | --- | --- |
| ISSUE-001 | 普通回复被误判为"同意企业云联络中心联系"，且承诺的功能不存在 | 高 | **已修复（代码）** |
| ISSUE-002 | "选第1个"被当作"改选"，选人落到另一条 SELECTED 旧需求 | 高 | **已修复（代码）** |
| ISSUE-003 | 简历原件投递在配置层面必然失败（`RECRUITER_RESUME_SOURCE_ROOTS` 未配置） | 高 | **已修复（代码+配置模板）**，待运维填实际路径 |
| ISSUE-004 | 投递失败原因被静默吞掉，无任何日志 | 中 | **已修复（代码）** |
| ISSUE-005 | 钉钉附件送达不可确认，URL 兜底同样缺配置 | 中 | **部分修复**：配置项与目录已补齐，静态下载服务需运维提供 |
| ISSUE-006 | CCC 外呼子系统未并入 main，工作流却已对外承诺 | 高 | **已止血**（不再承诺/追问）；正式接入未开始，见下方说明 |
| ISSUE-007 | 意图分类器异常被 `except Exception: pass` 吞掉 | 中 | **已修复（代码）** |
| ISSUE-008 | 部署配置缺失若干被代码读取的环境变量 | 中 | **已修复（配置）** |
| ISSUE-009 | `runtime/rn/*.backup-*` 备份文件会随 rsync 进入生产 | 低 | **已修复（仓库+脚本）** |
| ISSUE-010 | `im/router-v34.test.mjs` 断言陈旧，导致发布门禁 `verify.sh` 必然失败 | 中 | **已修复（测试）** |
| ISSUE-011 | 补充招聘字段被误判成"新需求"，每答一次就另开一张工单 | 高 | **已修复（代码）**，真实 LLM 复测通过 |
| ISSUE-012 | 后端不可用时，内部错误原文直接回给用人经理 | 中 | **已修复（代码）**，真实错误路径复测通过 |
| ISSUE-013 | 多桥接实例抢占同一机器人的 Stream，导致钉钉侧报 INTERNAL_UNKNOWN | 高 | **已修复（部署脚本）**，真实钉钉收发复测通过 |
| ISSUE-014 | 入站消息被静默丢弃（解析失败/字段缺失都无日志） | 中 | **已修复（适配器）** |
| ISSUE-015 | 机器人被两个消费者抢占（DSH dsh-im 与独立桥接） | 高 | **已修复**（改为由 v5 桥接独占，v2.2 已整体删除） |
| ISSUE-016 | 钉钉回复整条乱码（Python stdout 按 GBK 编码被当作 UTF-8 解码） | 高 | **已修复（代码+部署配置）**，已在钉钉消息库验证 |
| ISSUE-017 | markdown 形态的钉钉消息正文被判为空，掉进 Codex 兜底报 ENOENT | 高 | **已修复（适配器）**，markdown 形态复测通过 |

### 修复摘要（2026-09-23）

| 编号 | 改动位置 | 内容 |
| --- | --- | --- |
| ISSUE-001/002 | `runtime/rn/hiring_workflow_v5.py` `handle()` | SELECTED 分支不再接管每条消息：先取活跃需求，仅当"没有活跃需求"或"消息确实谈到简历/联系"时才用已选需求解释意图；`select` 只在没有活跃需求时按改选处理；联系意向还须消息本身含 `联系/外呼/电话/沟通/语音` 佐证 |
| ISSUE-006 | 同上 + `_confirm_ccc_contact` | 选择候选人不再追问外呼服务；确认联系的回复改为"外呼能力尚未接入，本次不会发起电话或录音"；事件名改为 `CANDIDATE_CONTACT_PENDING_HR` |
| ISSUE-004 | `hiring_workflow_v5.py` 四处投递 catch | 记录 `resume_delivery_failed request=… candidate=… reason=…`；用户侧仍是安全文案 |
| ISSUE-007 | `_classify_intent_semantic` | 失败记 `intent_classify_failed status=… error=…`，返回非法意图记 `intent_classify_unusable` |
| 可观测性 | `im/recruiter-na.mjs` `hostWorkflow()` | 子进程 stderr 转发到桥接进程 stderr，Python 诊断才会进 journalctl（原先只存进变量、从不输出） |
| ISSUE-003 | `runtime/rn/resume_delivery.py` | 未配置根目录时抛 `RESUME_SOURCE_ROOTS_NOT_CONFIGURED`（原先统一报 `RESUME_SOURCE_OUTSIDE_ALLOWED_ROOT`，无法指向真正原因） |
| ISSUE-003/005/008 | `.env.example`、`deploy/recruiter-na.env.example` | 补 `RECRUITER_RESUME_SOURCE_ROOTS`、`RECRUITER_RESUME_MAX_BYTES`、`RECRUITER_RESUME_URL_BASE`、`RECRUITER_RESUME_URL_DIR`、`DINGTALK_AI_CARD_TEMPLATE_ID`、`DINGTALK_PROGRESS_CARD`、`DINGTALK_PROGRESS_THROTTLE_MS` |
| ISSUE-005/009 | `deploy/install-cloud.sh` 与 4 个 `upgrade-v5.0.*.sh` | 创建 `workspace/resume-url`；rsync 增加 `--exclude '*.backup-*'`；安装脚本打印必填环境变量提示 |
| ISSUE-009 | `runtime/rn/` | 4 个 `*.backup-*` 恢复副本移到 `backups/runtime-rn/`，不再进入发布同步范围 |
| ISSUE-010 | `im/router-v34.test.mjs` | `/new` 期望文案更新为当前产品行为（否则 `verify.sh` 第 5 步必然失败，升级脚本会中止） |
| ISSUE-011 | `hiring_workflow_v5.py` 提示词 + `_missing_labels()` | 分类器新增"当前待补字段"上下文；决策边界改为"待补字段非空时，用户提供这些字段取值一律是 modify_jd（继续草稿），即使整段像招聘描述"，并保留"明确另起新岗位 = new_hiring" |
| ISSUE-012 | `_screen()` + `im/recruiter-na.mjs` | 筛选后端异常改为：用户看到"筛选服务当前不可用，需求与 JD 已保留"且工单退回 `JD_READY` 可重试；真实原因记 `[workflow] screening_failed request=… error=…`；桥接不再把 `result.error` 拼进用户消息 |

回归用例新增于 `tests/test_hiring_workflow_v5.py`：
`test_field_answer_is_not_hijacked_by_an_earlier_selected_request`、
`test_ordinal_selection_targets_the_request_that_showed_the_batch`、
`test_intent_classifier_failure_is_reported_to_the_host_log`、
`test_resume_delivery_failure_logs_the_specific_reason`、
`test_selection_does_not_solicit_an_unintegrated_contact_service`；
`tests/test_resume_delivery.py` 增加未配置根目录的用例。
端到端复现脚本：`scripts/diagnose-ccc-routing.py`（两个场景修复后均符合预期）。

### ISSUE-006 未完成的部分（必须明说）

企业云联络中心（腾讯云 CCC）的实际外呼能力**仍然没有接入**：`ccc_client.py`、
`contact_tasks.py`、`contact_orchestrator.py`、`contact_resolvers.py` 依旧只存在于被
`.gitignore` 忽略的 `.worktrees/tencent-ccc-contact/`。本次只做了"停止对外承诺"，
没有擅自把未经 M0 验收的子系统并入 `main`——交接报告
（`TENCENT_CCC_CONTACT_IMPLEMENTATION_HANDOFF_20260921.md`）要求先完成 Fake 闭环与安全门，
且"不得声称该功能已经实现、已部署、测试通过"。
因此：**产品层已不再虚假承诺，功能层仍待按 M0/M1 门禁推进。**

两条统一根因：

- **根因 A（SELECTED 抢答）**：`hiring_workflow_v5.py:451-464` 的前置分支，让一条历史 SELECTED 需求接管该账号之后的**每一条**消息 → ISSUE-001、ISSUE-002。
- **根因 B（投递链路三处缺口）**：投递根目录未配置、失败原因无日志、附件兜底未配置 → ISSUE-003、ISSUE-004、ISSUE-005。

---

## ISSUE-001 普通回复被误判为"同意企业云联络中心联系"

**严重级别：** 高 ｜ **状态：** 已定位、已复现，待修复
**首次发现：** 2026-09-22，用人经理"世龙"

### 现象

机器人询问"还需要补充：期望到岗时间/紧急程度"，用户答 **"紧急"**，机器人却回复：

> 已记录。我将通过企业云联络中心完成电话沟通和对话记录，语音智能体将联系目标候选人询问可预约通话时间，并将结果告知您。

三层问题：答非所问；用户提交的字段被丢弃（需求停在 DRAFT）；回复承诺的电话/录音/语音智能体**在代码中不存在任何实现**。

### 根因

- 台词唯一出处：`runtime/rn/hiring_workflow_v5.py:359-366` `_confirm_ccc_contact()`，只把 `candidate_contact.status` 写成 `CONTACT_REQUESTED` 后即回复；
- 唯一调用点：同文件 `462` 行，位于 SELECTED 抢答分支（`451-464`）；
- 分类器提示词（`49-84`）对 `confirm_ccc_contact` 没有"必须上一轮刚问过"的前置条件，且要求"不能因措辞不同就返回 generic"，逼模型在意图表中选一个；
- 该分支命中后立即 `return`，DRAFT 字段采集被完全跳过。

### 证据

本地复现脚本 `scripts/diagnose-ccc-routing.py`（桩分类器），输出与截图逐字一致：

```
[准备] 旧需求状态 = SELECTED ；新需求状态 = DRAFT
当前需求状态: SELECTED        ← 分类器拿到的不是用户正在填的那条 DRAFT
用户消息: 紧急
{"intent": "confirm_ccc_contact"}
[机器人回复] 已记录。我将通过企业云联络中心完成电话沟通和对话记录，……
[事件] 新需求: ['REQUEST_CREATED']   ← "紧急"字段被彻底丢弃
```

### 修复方向

1. `_confirm_ccc_contact` 改为如实回复（联系服务未上线，由 HR 跟进）；
2. SELECTED 分支加前置条件：仅当该需求处于 `AWAITING_CONFIRMATION` 且上一轮刚发出联系确认提问时才进入 CCC 判定；
3. 提示词为 `confirm_ccc_contact` / `decline_ccc_contact` 补上下文约束。

---

## ISSUE-002 "选第1个"被当作"改选"，选人落到另一条 SELECTED 旧需求

**严重级别：** 高 ｜ **状态：** 已定位，待修复
**首次发现：** 2026-09-22，同一账号

### 现象

机器人在 17:45 展示了本批候选人（含 ①②③④ 面试重点），用户回复 **"选第1个"**，机器人回复：

> 已将已选候选人改为 **周清**，后续面试/录用仍由 HR 与业务流程确认。简历原件暂未准备完成，HR 将协助补发。
> 是否要使用企业云联络中心服务联系周清，开始招聘流程的下一阶段？

用户预期："选第一个就应该把简历发给我"。

### 根因

- 台词"**已将已选候选人改为**"只出自 `hiring_workflow_v5.py:349` 的 `_correct_selected_candidate()`；
- 该函数唯一入口是 SELECTED 抢答分支 `459-460` 行——即该账号名下已存在一条 SELECTED 需求，于是这次选择被理解为"在旧需求上换人"，而不是"在刚展示的候选卡上首次选择"；
- 首次选择的正确路径在 `523-540` 行，台词应为"**已记录你选择 X**。该招聘需求已进入 SELECTED 状态…"；
- **跨需求错位**：候选卡片由 `_render_candidates()`（`189` 行）按**当前活跃需求**渲染，而选人落在**那条 SELECTED 需求**的 `ranking[-3:]` 上（`_shown_selection_target()`）。用户看到的批次与实际生效的需求可能不是同一条。

### 影响

- 用户在 A 需求里选人，可能实际改写了 B 需求的选择；
- 被选中的候选人未必是用户所指的那一位；
- 触发 `CANDIDATE_SELECTION_CORRECTED` 事件，且附带无实现的 CCC 追问（见 ISSUE-006）。

### 验证方式

```sql
-- 看 CANDIDATE_SELECTION_CORRECTED 落在哪个 request_id 上
SELECT request_id, event_type, created_at FROM recruitment_request_events
WHERE event_type LIKE 'CANDIDATE_%' ORDER BY event_id DESC LIMIT 20;
```

### 修复方向

与 ISSUE-001 第 2 条同源：SELECTED 分支不得无条件接管全部消息；选择意图应优先作用于"最近展示过候选批次的那条需求"。

---

## ISSUE-003 简历原件投递在配置层面必然失败

**严重级别：** 高 ｜ **状态：** 已定位，待修复

### 现象

任何一次选人（首次选择或改选）都不会收到简历附件，机器人固定回复"简历原件暂未准备完成，HR 将协助补发"。

### 根因

`runtime/rn/resume_delivery.py`：

```python
configured = [x for x in os.environ.get("RECRUITER_RESUME_SOURCE_ROOTS", "").split(":") if x]  # 17
self.allowed_roots = [Path(root).resolve() for root in configured]                               # 19
...
if not self.allowed_roots or not any(source.is_relative_to(root) for root in self.allowed_roots):
    raise ResumeDeliveryError("RESUME_SOURCE_OUTSIDE_ALLOWED_ROOT")                              # 36
```

`RECRUITER_RESUME_SOURCE_ROOTS` **未出现在任何部署配置中**（`deploy/recruiter-na.env.example`、`.env.example`、`deploy/install-cloud.sh`、`deploy/recruiter-na.service` 均无）。因此 `allowed_roots` 恒为空列表，第 36 行**必然抛错**——与候选人是谁、是否首次选择无关。

即使配置后，仍要求 MySQL 行的 `source_file_path` 非空且文件在允许目录内（字段由 `runtime/rn/resume_mysql.py:308` 映射为 `_mysql_source_file_path`），否则分别抛 `RESUME_SOURCE_PATH_MISSING` / `RESUME_SOURCE_NOT_REGULAR_FILE`。

### 验证方式

```bash
sudo grep -E 'RECRUITER_RESUME_SOURCE_ROOTS|RECRUITER_RESUME_URL_BASE' /etc/recruiter-na/recruiter-na.env \
  || echo "→ 全部未配置：简历必然发不出去"

sudo bash -c 'set -a; . /etc/recruiter-na/recruiter-na.env; set +a; cd /opt/recruiter-na/app; \
  PYTHONPATH=runtime python3 -c "
from rn.repositories import ResumeRepository
from rn.resume_delivery import SelectedResumeDeliveryService
print(\"allowed_roots =\", SelectedResumeDeliveryService(ResumeRepository()).allowed_roots)"'
# allowed_roots = [] → 即 RESUME_SOURCE_OUTSIDE_ALLOWED_ROOT
```

### 修复方向

在 `/etc/recruiter-na/recruiter-na.env` 配置 `RECRUITER_RESUME_SOURCE_ROOTS=<简历原件目录>`（多个用 `:` 分隔），并确认该目录内确为简历原件、进程用户 `recruiter-na` 有读权限。

---

## ISSUE-004 投递失败原因被静默吞掉

**严重级别：** 中 ｜ **状态：** 已定位，待修复

### 根因

`hiring_workflow_v5.py:352-356`（及首次选择路径 `535-539`、`552-556`）：

```python
except ResumeDeliveryError:
    result["message"] += " 简历原件暂未准备完成，HR 将协助补发。"
```

异常被丢弃，既未写日志，也未把 7 种具体原因（`RESUME_DELIVERY_NOT_AUTHORIZED`、`RESUME_SOURCE_NOT_FOUND`、`RESUME_SOURCE_PATH_MISSING`、`RESUME_SOURCE_OUTSIDE_ALLOWED_ROOT`、`RESUME_SOURCE_NOT_REGULAR_FILE`、`RESUME_SOURCE_TOO_LARGE`、`RESUME_SOURCE_FILENAME_MISSING`）回传。

### 影响

线上无法从 journalctl 判因，只能看到一句面向用户的模糊文案，直接导致 ISSUE-003 被长期掩盖。

### 修复方向

捕获时记录 `reason=<异常码>`、`request_id`、`candidate_id` 到日志；面向用户仍保持安全文案。

---

## ISSUE-005 钉钉附件送达不可确认，URL 兜底同样缺配置

**严重级别：** 中 ｜ **状态：** 已定位，待修复

### 根因

- `im/recruiter-na.mjs:159` 通过 sessionWebhook 发文件，代码注释自述"只代表 API 接收，不代表收件人看到附件"；
- URL 兜底 `im/recruiter-na.mjs:169` `publishResumeFallback()` 依赖 `RECRUITER_RESUME_URL_BASE`（`27-28` 行读取），该变量**不在任何部署配置中**，未配置时直接抛 `RECRUITER_RESUME_URL_BASE is required for URL fallback`，最终降级为"暂未送达"；
- `deploy/install-cloud.sh:45` 只创建了 `workspace/runs`、`workspace/reports`、`workspace/.host-progress`，**没有** `workspace/resume-url`；
- 也未记录任何静态文件服务（nginx 等）来承载该下载地址。

### 修复方向

配置 `RECRUITER_RESUME_URL_BASE`（指向可公开访问且带随机 token 的下载前缀）、`RECRUITER_RESUME_URL_DIR`，install 脚本补建目录与权限，并明确静态服务由谁提供。

---

## ISSUE-006 CCC 外呼子系统未并入 main，工作流却已对外承诺

**严重级别：** 高 ｜ **状态：** 已定位，待修复

### 根因

- `runtime/rn/`（main）**没有** `ccc_client.py`、`contact_tasks.py`、`contact_orchestrator.py`、`contact_resolvers.py`；
- `runtime/rn/host_tool.py` **没有**任何 `contact/ccc` 接线（构造 workflow 时未传 contact orchestrator）；
- 上述 4 个模块（约 1172 行）只存在于 `.worktrees/tencent-ccc-contact/runtime/rn/`，而 `.gitignore:17` 忽略了 `.worktrees/`；
- `docs/reports/TENCENT_CCC_CONTACT_IMPLEMENTATION_HANDOFF_20260921.md:5` 记载"尚未开始功能代码改动、部署或真实外呼"；第 21-28 行列出未完成项；第 30 行写明"**不得声称该功能已经实现、已部署、测试通过或已经产生真实电话**"。

### 影响

机器人对用人经理承诺电话沟通、对话记录与语音智能体外呼，实际零实现；即便用户同意，也不会有任何电话产生，且 `candidate_contact.status` 已被写成 `CONTACT_REQUESTED`，污染状态。

### 修复方向

短期改文案止血；正式方案是把 worktree 实现并入 main（默认 `RECRUITER_CCC_MODE=disabled`），先完成 M0 Fake 闭环，再评估 M1 真实外呼。

---

## ISSUE-007 意图分类器异常被静默吞掉

**严重级别：** 中 ｜ **状态：** 已定位，待修复

### 根因

`hiring_workflow_v5.py:87-103`：

```python
try:
    ...
    result = analyzer._call(prompt)
    ...
except Exception:      # 101
    pass               # 102
return None
```

LLM 配置缺失（`HIRING_ANALYZER_CONFIG_MISSING`，见 `hiring_analyzer_v4.py:73-75`）、超时、返回非法 JSON 全部被吞。上层对 `None` 的响应是"当前暂时无法理解这条消息，请稍后换一种说法再试。"（`479`、`489-490` 行），用户与运维都拿不到真实原因。

### 修复方向

捕获时记录 `intent=none reason=<异常>` 与 `sender_id`；配置缺失时给运维可见的明确错误。

---

## ISSUE-008 部署配置缺失若干被代码读取的环境变量

**严重级别：** 中 ｜ **状态：** 已定位，待修复

| 变量 | 读取位置 | 是否在部署配置中 |
| --- | --- | --- |
| `RECRUITER_RESUME_SOURCE_ROOTS` | `runtime/rn/resume_delivery.py:17` | ❌ 无 |
| `RECRUITER_RESUME_MAX_BYTES` | `runtime/rn/resume_delivery.py:20` | ❌ 无（有默认值） |
| `RECRUITER_RESUME_URL_BASE` | `im/recruiter-na.mjs:28` | ❌ 无 |
| `RECRUITER_RESUME_URL_DIR` | `im/recruiter-na.mjs:27` | ❌ 无（有默认值） |
| `DINGTALK_AI_CARD_TEMPLATE_ID` | `vendor/codexharness-im/src/adapters/dingtalk-stream-bot.mjs:143` | ❌ 无 |
| `DINGTALK_PROGRESS_CARD` / `DINGTALK_PROGRESS_THROTTLE_MS` | 同上 `145-146` | ❌ 无（有默认值） |

### 影响

新环境按 `deploy/recruiter-na.env.example` 部署后，简历投递与进度卡片相关能力静默失效，且无人知晓需要补哪些变量。

### 修复方向

补齐 `deploy/recruiter-na.env.example`（含注释说明用途与格式），并在 `install-cloud.sh` 的提示信息中列出必填项。

---

## ISSUE-009 `runtime/rn/*.backup-*` 备份文件会随 rsync 进入生产

**严重级别：** 低 ｜ **状态：** 已定位，待修复

### 现状

工作区存在 4 个备份残留：

```
runtime/rn/hiring_workflow_v5.py.backup-20260911-semantic
runtime/rn/screening_v5.py.backup-20260911
runtime/rn/screening_v5.py.backup-resume
runtime/rn/screening_v5.py.backup-resume2
```

`deploy/install-cloud.sh:25-26` 的 rsync 只排除 `.venv`、`node_modules`、`.env`、私有简历与隔离区，**不排除** `*.backup-*`。

### 影响

生产目录混入旧版本 workflow / screening 源码，增加误读、误引用与安全扫描噪声风险。

### 修复方向

删除工作区备份文件（改用 git 历史），或在 rsync 中增加 `--exclude '*.backup-*'`。

---

## ISSUE-011 补充字段被误判成"新需求"

**严重级别：** 高 ｜ **状态：** 已修复（真实 LLM 复测通过）

### 现象

标准画像匹配后，机器人追问 6 个字段；用户一次性回复

> 质量部，1人，杭州，10-13K，尽快，没有额外硬要求

机器人**没有把这几个字段填进当前需求**，而是回复"当前需求未匹配到现有标准岗位画像，这次按新岗位完整采集信息"，重新从岗位名称问起。连续两次补充产生了 3 张工单（`A70A1C19` / `97A9C716` / `348027B0`）。

### 根因

`INTENT_CLASSIFY_PROMPT` 的两条规则互相冲突：

- "重新给出一整段可独立成立的岗位、部门、人数、地点、薪资、到岗等招聘描述时，意图是 new_hiring" —— 字段补充恰好命中这个模式；
- "仅当消息明显是在回答上一轮的一个缺失字段…才继续当前 DRAFT" —— 与上一条冲突。

而且分类器**拿不到"助手刚才在问什么"**：提示词只传了 status 与 title，所以模型无法判断这是一次回答。

### 修复

- 分类器上下文新增 `当前待补字段: …`（由 `_missing_labels()` 从 `_missing_fields()` 生成中文标签）；
- 决策边界改写为：待补字段非空时，提供这些字段的取值一律 `modify_jd`（继续草稿），**哪怕整段读起来像招聘描述**；只有"明确另起新岗位/新需求"或"无待补字段且消息本身是完整招聘描述"才是 `new_hiring`。

### 验证

真实 DeepSeek 模型下重放同一路径：

```
1) 我要招一个检验技术员            -> DRAFT  REQ-20260923-889108D8
2) 常规临检免疫方向，社招，有2年检验科经验 -> DRAFT  匹配到 检验技术员（社招）
3) 质量部，1人，杭州，10-13K，尽快，没有额外硬要求 -> JD_READY  同一 request_id
   该 owner 工单数 = 1（修复前每次补充都会 +1）
```

新增用例：`test_field_answer_prompt_carries_the_pending_intake_fields`、
`test_prompt_has_no_pending_fields_when_no_request_exists`、
`test_missing_labels_is_safe_for_an_incomplete_record`。

---

## ISSUE-012 内部错误原文回给用人经理

**严重级别：** 中 ｜ **状态：** 已修复（真实错误路径复测通过）

### 现象

未配置 MySQL 时发送"开始筛选"，用人经理直接收到：

```
处理失败：RESUME_BACKEND_UNAVAILABLE: MYSQL_DATABASE is required
```

同时工单被留在 `SCREENING` 状态（后续只说"服务中断后状态重置中"，无法直接重试）。

### 根因

- `_screen()` 只捕获 `UnsupportedScreeningPolicy`，其它异常向上冒泡到 `host_tool`，以 `{"error": "…"}` 返回；
- `im/recruiter-na.mjs` 用 `result.message || 处理失败：${result.error}` 把内部错误原文拼进了回复；
- 状态机没有回退，`SCREENING` 成为死状态。

### 修复

- `_screen()` 捕获后端异常：工单退回 `JD_READY`（可重试），记 `[workflow] screening_failed request=… error=…`，用户只看到"筛选服务当前不可用，你的招聘需求与 JD 已保留"；
- 桥接不再把 `result.error` 拼进用户消息，改为 `console.error` 进 journalctl。

### 验证

```
用户侧：{"state": "JD_READY", "message": "筛选服务当前不可用，你的招聘需求与 JD 已保留。请联系管理员处理后再回复「开始筛选」。"}
运维侧：[workflow] screening_failed request=REQ-20260923-889108D8 error=RuntimeError: RESUME_BACKEND_UNAVAILABLE: MYSQL_DATABASE is required
```

新增用例：`test_screening_backend_failure_hides_internal_error_and_stays_retryable`。

---

## ISSUE-015 机器人被两个消费者抢占（DSH dsh-im 与独立桥接）

**严重级别：** 高 ｜ **状态：** 已修复（已按 DSH 链路为准完成切换）

同一个 clientId（ding0yc9sflomcx9ny72）被 **DSH 的 dsh-im 插件** 和 **独立 recruiter-na 桥接** 同时占用时，
两者的 Stream 连接会互相顶掉（日志特征：TERMINATE SOCKET: Ping Pong does not transfer heartbeat），
表现为消息丢失 + 客户端失败卡片。**规则：一个机器人只能有一个 Stream 消费者。**

按业务决定，该机器人以 **DSH 链路为准**（钉钉 → dsh-im → DSH Host → recruiting-na preset → 钉钉回复）；
独立桥接已停止（bash scripts/stop-local.sh）。

DSH 侧另有一个独立故障（陈旧会话绑定导致每条消息都失败），根因与修复步骤见
[DSH_DINGTALK_BOT_BINDING_FIX_20260923.md](./DSH_DINGTALK_BOT_BINDING_FIX_20260923.md)。

---

## ISSUE-013 多实例抢占 Stream 导致钉钉侧失败卡片

**严重级别：** 高 ｜ **状态：** 已修复（真实钉钉收发复测通过）

### 现象

用人经理在钉钉里给「Ai招聘专员」发消息，客户端显示：

```
处理失败
任务未完成，暂时无法确定原因。请重试；若持续发生，请将参考号提供给管理员。
错误码：INTERNAL_UNKNOWN；参考号：MF-4A3774BC / MF-3B74107E / MF-04E6F0BA / MF-5A61EF69
```

而本机桥接日志里**一条入站记录都没有**。

### 根因

钉钉 Stream 对同一个机器人（clientId）**每条消息只投递给一个连接**。本机当时同时跑着 3 个桥接实例
（PID 26612 / 31100 / 30568，均为反复重启留下的残留，`taskkill` 校验方式不可靠导致误判为已停止）。
消息被投给不发响应的僵尸连接 → 平台等待超时 → 客户端渲染平台级失败卡片。

雪上加霜：重启时用过 `rm -f workspace/bridge.log`，老实例仍持有已删除文件的句柄，
**日志写进了被 unlink 的 inode**，"日志空白"进一步掩盖了真相。

### 修复

- 新增 `scripts/list-bridges.ps1`（按命令行枚举桥接实例，唯一可靠）、`scripts/stop-local.sh` / `stop-local.cmd`（停全部实例）；
- `scripts/start-local.sh` 增加**去重保护**：检测到已有实例即拒绝启动（`RECRUITER_ALLOW_MULTI=1` 可跳过）。

### 验证

```
[dingtalk] callback invoked topic=/v1.0/im/bot/messages/get
[im] claimed pid=26440 owner=dingtalk:01572739536219986257   <- 身份用 senderStaffId，owner 隔离正确
[reply] source=hiring-workflow state=DRAFT request=REQ-20260923-0E45447B
```

用人经理侧收到正常文字回复（画像澄清提问）。

### 运维提示

重启桥接后的**头几十秒**，平台上可能仍保留旧连接注册，此窗口内发消息仍可能看到失败卡片，稍等重发即可。
日常请用 `scripts/stop-local.sh` 停、`scripts/start-local.cmd` 起，不要连续快速重启。

---

## ISSUE-014 入站消息被静默丢弃

**严重级别：** 中 ｜ **状态：** 已修复（适配器）

### 现象

消息没进桥接时，日志里连"收到但忽略"都没有，无法区分"平台没投递"与"投递了但载荷不认识"。

### 根因

`vendor/codexharness-im/src/adapters/dingtalk-stream-bot.mjs` 的回调里：

- JSON 解析失败 → `catch { return; }`，静默；
- `normalizeDingTalkRobotMessage()` 返回 `null`（缺 senderStaffId/senderId 或 msgId）→ `if (!envelope) return;`，静默。

### 修复

- 两条静默路径都记录原因；丢弃时只打印**字段有无摘要**（msgtype / conversationType / 是否有 msgId、senderStaffId、senderId、text、sessionWebhook、robotCode），不落消息正文；
- 新增 `DINGTALK_STREAM_DEBUG=true` 打开 SDK 原始帧日志（**默认关闭**：原始帧含 sessionWebhook，属凭据，不应长期落盘）。

### 验证

排障期间开启 debug 后可见完整回调帧，确认 `topic=/v1.0/im/bot/messages/get` 与 `senderStaffId` 均正常。

---

## ISSUE-016 钉钉回复整条乱码

**严重级别：** 高 ｜ **状态：** 已修复，已在钉钉消息库验证原文

### 现象

钉钉里机器人每条回复都是乱码，例如：

```
��ǰ������ܶ�Ӧ�����׼��λ����Ϊ�������׻�����ֻȷ��һ���ؼ��㣺
```

### 根因

Windows 上 Python 的 stdout 默认按**控制台代码页（GBK/cp936）**编码；桥接用 Node 读取子进程 stdout，
Node 默认按 **UTF-8** 解码 → 整条回复乱码。本地手工验证时命令带着 `PYTHONUTF8=1`，所以一直没暴露。

### 证据（同一条命令两种环境的字节对比）

| 环境 | 能否按 UTF-8 解码 | 内容 |
| --- | --- | --- |
| 无 `PYTHONUTF8`（桥接环境） | 否（invalid start byte） | 按 GBK 解码可读，UTF-8 下是乱码 |
| `PYTHONUTF8=1` | 是 | `"message": "当前需求可能对应多个标准岗位画像…"` |

### 修复

| 位置 | 改动 |
| --- | --- |
| `im/recruiter-na.mjs` 的 spawn env | 强制 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8` |
| `scripts/start-local.sh` | 导出同样两个变量 |
| `deploy/recruiter-na.service` | 增加 `Environment=PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`（Linux 上 LANG 缺失时同理） |
| `deploy/recruiter-na.env.example`、`.env.local` | 文档化并默认开启 |

### 验证

重发后从钉钉消息库检索到机器人回复原文：

```
sender: Ai招聘专员
text:   当前需求可能对应多个标准岗位画像，为避免误套画像，我只确认一个关键点：

        该检验技术员岗位是面向应届生（从零培养）还是面向有经验的社招人员（可独立上岗）？
```

---

## ISSUE-017 markdown 形态的钉钉消息被判为空文本

**严重级别：** 高 ｜ **状态：** 已修复（适配器）

### 现象

某些入站消息（例如以 markdown 形式发出的消息）进到桥接后正文为空，
于是不满足 `shouldUseHiringWorkflow` 的判断，直接掉进防御性 Codex 兜底并报 `spawn codex ENOENT`。

### 根因

钉钉机器人消息的正文位置随消息类型而变：文本消息在 `text.content`，**markdown 消息在 `markdown.text`**；
适配器只读前者。

### 修复

新增 `firstNonEmptyText()`，正文按 `text.content` → `markdown.text` → `content` 依次取第一个非空值；
字段摘要增加 `markdown=` 提示；正文为空时记一行 warn（此前是完全静默）。
详见 `docs/VENDOR_ADAPTER_PATCHES.md` 补丁 3/4。

### 验证

markdown 形态消息复测：`[reply] source=hiring-workflow state=DRAFT`（修复前是 `spawn codex ENOENT`）。

---

## 附：与问题直接相关的关键行号索引

| 位置 | 作用 |
| --- | --- |
| `hiring_workflow_v5.py:87-103` | `_classify_intent_semantic`，异常静默吞掉（ISSUE-007） |
| `hiring_workflow_v5.py:189-217` | `_render_candidates`，候选卡片渲染 |
| `hiring_workflow_v5.py:349` | "已将已选候选人改为 X"（ISSUE-002） |
| `hiring_workflow_v5.py:352-356` | 投递失败被吞掉（ISSUE-004） |
| `hiring_workflow_v5.py:359-366` | "已记录。我将通过企业云联络中心……"（ISSUE-001） |
| `hiring_workflow_v5.py:451-464` | SELECTED 抢答分支（根因 A） |
| `hiring_workflow_v5.py:523-540` | 首次选择正确路径 |
| `resume_delivery.py:17-41` | 简历投递的 7 种失败原因（ISSUE-003） |
| `resume_mysql.py:308` | `source_file_path` → `_mysql_source_file_path` |
| `im/recruiter-na.mjs:155-182` | 附件投递、URL 兜底、follow_up 发送 |
| `deploy/install-cloud.sh:25-26,45` | rsync 排除项与 workspace 目录创建 |
| `.gitignore:17` | 忽略 `.worktrees/`，CCC 实现未进 git |

## 说明与边界

- 上表行号为**修复前**的定位行号；修复后 `hiring_workflow_v5.py` 的 `handle()` 已重写，
  新旧行号不再一一对应，请以函数名检索。
- 本清单中"已复现"仅指 ISSUE-001（本地桩分类器复现，未使用真实 LLM 复测"紧急 → confirm_ccc_contact"这一步的概率）；
- ISSUE-002 / ISSUE-003 为代码推导 + 配置事实核对，需按各条"验证方式"在生产服务器上确认；
- 未直接读取生产数据库与 `/etc/recruiter-na/recruiter-na.env`；
- 本批修复均未部署：代码改动仍在工作区（未提交），上线仍需按 `deploy/upgrade-*.sh` 走发布流程；
- `scripts/verify.sh` 第 1 步（data foundation audit）与 2 个 `test_data_foundation_v34.py` 用例
  依赖公开版未包含的原始 PDF/Excel 素材，本地无法通过，与本次改动无关。

## 上线后建议复验

1. 用人经理回答字段（如"紧急"）后，需求应正常推进，且**不得**收到企业云联络中心相关话术；
2. 历史已选需求存在时，"选第N个"应作用于刚展示批次的那条需求，回复应为"已记录你选择 X"；
3. 选人后 `journalctl -u recruiter-na` 应出现 `[workflow] resume_delivery_failed … reason=RESUME_SOURCE_ROOTS_NOT_CONFIGURED`，
   配置 `RECRUITER_RESUME_SOURCE_ROOTS` 后该行应消失并能收到附件或下载链接；
4. 分类器故障时日志应出现 `[workflow] intent_classify_failed status=… error=…`。