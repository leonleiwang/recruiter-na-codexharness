# 钉钉机器人错误回复 CCC 联系承诺 — 缺陷记录

**记录日期：** 2026-09-22
**严重级别：** 高（对用人经理作出系统无法兑现的承诺，并吞掉用户正在提交的招聘字段）
**状态：** 已定位、已本地复现；**尚未修复**
**相关模块：** `runtime/rn/hiring_workflow_v5.py`、`im/recruiter-na.mjs`
**问题清单编号：** ISSUE-001（清单见 [DINGTALK_BOT_ISSUE_LIST_20260922.md](./DINGTALK_BOT_ISSUE_LIST_20260922.md)）

---

## 1. 现象

用人经理在钉钉里回答标准画像补充问题后，机器人没有记录该字段，而是回复了一段与企业云联络中心（腾讯云 CCC）有关的承诺。截图原文（2026-09-22，用户"世龙"）：

> **机器人：** 已匹配公司标准岗位画像 检验技术员（应届），通用能力/经验不用你重复填写。
> 还需要补充：期望到岗时间/紧急程度
> 可以一次性回复，已经说明过的内容不用重复。
> **用户（16:34）：** 紧急
> **机器人：** 已记录。我将通过企业云联络中心完成电话沟通和对话记录，语音智能体将联系目标候选人询问可预约通话时间，并将结果告知您。

问题有三层：

1. 这句回复**不是对"紧急"的回应**，与"紧急程度"这个字段毫无关系；
2. 用户真正提交的字段（期望到岗时间/紧急程度）**被丢弃，没有落库**；
3. 回复中承诺的电话沟通、对话记录、语音智能体外呼**在当前代码中并不存在任何实现**。

## 2. 根因

### 2.1 台词的唯一出处

`runtime/rn/hiring_workflow_v5.py:359-366`：

```python
def _confirm_ccc_contact(self, owner, request):
    target = self._selected_target(request)
    if not target:
        return {..., "message": "当前未能定位已选候选人，暂不能安排企业云联络中心联系。"}
    self._set_candidate_contact_status(owner, request, target, "CONTACT_REQUESTED", "CCC_CONTACT_REQUESTED")
    return {..., "message": "已记录。我将通过企业云联络中心完成电话沟通和对话记录，语音智能体将联系目标候选人询问可预约通话时间，并将结果告知您。"}
```

它只做两件事：把 `candidate_contact.status` 写成 `CONTACT_REQUESTED` 落库，然后回复上面那句话。**没有外呼、没有任务调度、没有回调。**

### 2.2 唯一调用点：SELECTED 抢答分支

`_confirm_ccc_contact` 全仓库只有一个调用点：`hiring_workflow_v5.py:462`，位于 `handle()` 最前端的 SELECTED 抢答分支（第 451-464 行）：

```python
selected = self.requests.list_for_owner(owner, statuses={"SELECTED"}, limit=1)
if selected:                       # 该钉钉账号名下只要还有任意一条 SELECTED 需求
    selected_semantic = _classify_intent_semantic(self.analyzer, t, "SELECTED", ...)
    ...
    if selected_semantic.get("intent") == "confirm_ccc_contact":
        return self._confirm_ccc_contact(owner, selected_request)   # 立即 return，不再往下走
```

因此：**一条历史 SELECTED 需求会永久"粘"在该账号上**，此后该用户发出的**每一条消息**，都会先被拿去按"当前需求状态：SELECTED"做意图分类；一旦分类器判成 `confirm_ccc_contact`，就立刻返回电话承诺，正在进行的 DRAFT 采集被完全跳过。

### 2.3 分类器为何会误判

`INTENT_CLASSIFY_PROMPT`（`hiring_workflow_v5.py:49-84`）对 `confirm_ccc_contact` 的定义只有：

```
- confirm_ccc_contact: 同意/确认让企业云联络中心或语音智能体联系已选候选人
```

**没有任何"必须上一轮刚问过联系确认"的前置条件**（对比 `new_hiring` / `modify_jd` 都写了明确决策边界）。提示词同时要求"不能因为用户没有使用示例措辞而返回 generic"，等于逼模型在意图表中选一个，"紧急"在这一上下文里被选成了"确认外呼"。

此外，`hiring_workflow_v5.py:87-103` 的 `_classify_intent_semantic` 用 `except Exception: pass`（第 101-102 行）吞掉全部异常，LLM 配置缺失（`HIRING_ANALYZER_CONFIG_MISSING`）、超时、JSON 非法都不会留下任何日志，线上无法从 journalctl 判因。

### 2.4 承诺的功能尚未实现

- `runtime/rn/`（main）**没有** `ccc_client.py`、`contact_tasks.py`、`contact_orchestrator.py`、`contact_resolvers.py`；
- `runtime/rn/host_tool.py` **没有**任何 `contact/ccc` 接线（构造 workflow 时未传 contact orchestrator）；
- 上述 4 个模块（约 1172 行）只存在于 `.worktrees/tencent-ccc-contact/runtime/rn/`，而 `.gitignore:17` 忽略了 `.worktrees/`；
- `docs/reports/TENCENT_CCC_CONTACT_IMPLEMENTATION_HANDOFF_20260921.md:5` 明确记载："设计已确认且已提交；**尚未开始功能代码改动、部署或真实外呼**"；第 21-28 行的未完成项包含 ContactTask 表/状态机、`CCCClient`/`FakeCCCClient`、工作流接线、"联系进度/取消联系"、回调鉴权、CDR/ASR、自动化测试、部署、首次真实外呼；第 30 行写明"**不得声称该功能已经实现、已部署、测试通过或已经产生真实电话**"。

结论：机器人向用人经理承诺了一件系统做不到的事。

## 3. 影响范围

- 任何曾成功选择过候选人的用人经理（账号下存在 `SELECTED` 需求），其后续**全部**消息都会先经过 SELECTED 上下文分类；
- 一旦被判成 `confirm_ccc_contact`：当前正在填写的招聘需求字段被丢弃（需求停留在 DRAFT），同时旧需求的 `candidate_contact.status` 被改写为 `CONTACT_REQUESTED`（状态被污染，后续若接入真实 CCC 会产生歧义）；
- 被判成 `decline_ccc_contact` 时会写入 `CCC_CONTACT_DECLINED`，同样与用户本意无关；
- 同类误判还可能出现在 `resend_resume`、`select`（改选）、`pause`、`restart_hiring` 等 SELECTED 分支内的意图上。

## 4. 复现

复现脚本：`scripts/diagnose-ccc-routing.py`（本地桩分类器，不调用真实 LLM）。

```
[准备] 旧需求状态 = SELECTED ；新需求状态 = DRAFT

=== 用人经理发送：紧急 ===
---- 分类器实际拿到的上下文 ----
   当前需求状态: SELECTED          ← 不是他正在填的那条 DRAFT
   岗位名称: 检验技术员
   用户消息: 紧急
---- 分类器返回 ----
   {"intent": "confirm_ccc_contact"}
[机器人回复] 已记录。我将通过企业云联络中心完成电话沟通和对话记录，语音智能体将联系目标候选人询问可预约通话时间，并将结果告知您。
[落库状态] 新需求 = DRAFT ｜旧需求 contact_status = {'status': 'CONTACT_REQUESTED', 'candidate_id': 'C1', 'candidate_name': '张三'}
[事件] 旧需求: [... 'CANDIDATE_SELECTED', 'CCC_CONTACT_REQUESTED']
[事件] 新需求: ['REQUEST_CREATED']   ← "紧急"字段被彻底丢弃
```

输出与截图逐字一致，且可观察到新需求只留下 `REQUEST_CREATED` 事件、字段未落库。

命令：

```bash
cd recruiter-na-codexharness
PYTHONUTF8=1 python scripts/diagnose-ccc-routing.py
```

## 5. 生产版本核对

本次定位针对的代码与线上部署包一致（本地工作区 vs `recruiter-na-codexharness.zip`，sha256 前 12 位）：

| 文件 | sha256（前 12 位） |
| --- | --- |
| `runtime/rn/hiring_workflow_v5.py` | `0adbab2e43d4` |
| `runtime/rn/host_tool.py` | `5c8aedf8325f` |
| `im/recruiter-na.mjs` | `76ee81e33596` |
| `runtime/rn/screening_v5.py` | `772c4cdd3f10` |

补充：CCC 相关改动（`_confirm_ccc_contact`、`_set_candidate_contact_status`、`follow_up_message`）在工作区均为**未提交**改动；`git show HEAD:runtime/rn/hiring_workflow_v5.py` 中不存在任何 CCC 代码。

## 6. 线上取证命令（只读）

```bash
# ① 确认线上确实没有外呼实现
ls /opt/recruiter-na/app/runtime/rn/ | grep -Ei 'ccc|contact' || echo "NO_CCC_MODULES"

# ② 找出"粘"在该账号上的 SELECTED 需求与 CCC 事件
sudo -u recruiter-na /opt/recruiter-na/app/.venv/bin/python - <<'PY'
import json, sqlite3
con = sqlite3.connect('/opt/recruiter-na/app/workspace/hiring_manager_requests.sqlite3')
con.row_factory = sqlite3.Row
print("== SELECTED 需求 ==")
for r in con.execute("SELECT request_id,owner_staff_id,status,position_title,request_json,updated_at FROM recruitment_requests WHERE status='SELECTED' ORDER BY updated_at DESC"):
    p = json.loads(r["request_json"] or "{}")
    print(r["request_id"], r["owner_staff_id"], r["position_title"], p.get("candidate_contact"), r["updated_at"])
print("== CCC 联系事件 ==")
for r in con.execute("SELECT request_id,event_type,created_at FROM recruitment_request_events WHERE event_type LIKE 'CCC%' ORDER BY event_id DESC LIMIT 10"):
    print(dict(r))
PY

# ③ 关联用户看到的那次回复
journalctl -u recruiter-na --since "7 days ago" --no-pager | grep -E '\[reply\]|\[workflow\]' | tail -60
```

预期：能看到被"紧急"意外写入的 `CCC_CONTACT_REQUESTED` 事件，且对应需求不是用户当时正在补充的那条。

## 7. 修复方向（未执行）

1. **止血**：`_confirm_ccc_contact` 改为如实回复（联系服务尚未上线，将由 HR 跟进），不再承诺电话、录音与语音智能体；
2. **消除抢答**：SELECTED 分支增加前置条件——仅当该需求确实存在 `AWAITING_CONFIRMATION` 且上一轮刚发出过联系确认提问时才进入 CCC 判定，否则正常走状态路由，用户才能继续新建/补充自己的招聘需求；
3. **提示词收口**：在 `INTENT_CLASSIFY_PROMPT` 中为 `confirm_ccc_contact` / `decline_ccc_contact` 补上上下文约束，明确"仅在机器人刚询问是否通过企业云联络中心联系某位已选候选人时"才可判定；
4. **可观测性**：`_classify_intent_semantic` 失败时记录 `intent=none reason=<异常>`，LLM 配置缺失时给运维可见的错误而非给用人经理"无法理解"；
5. **正式落地 CCC 能力**：将 `.worktrees/tencent-ccc-contact/` 的 `ccc_client` / `contact_tasks` / `contact_orchestrator` / `contact_resolvers` 与 `host_tool` 接线并入 main，默认 `RECRUITER_CCC_MODE=disabled`，先完成 M0 Fake 闭环，再评估 M1 真实外呼；
6. **数据订正**：清理被误写的 `CONTACT_REQUESTED` / `CCC_CONTACT_DECLINED` 状态，并补回被丢弃的招聘字段。

## 8. 未验证事项（诚实边界）

- 未直接读取生产数据库，第 2.2 节"该账号存在历史 SELECTED 需求"是根据截图现象 + 唯一调用点推导，需用第 6 节命令确认；
- 本地复现使用的是桩分类器（固定返回 `confirm_ccc_contact`），**未**用真实 LLM 复测"紧急 → confirm_ccc_contact"这一步的概率；
- 未验证线上 `RECRUITER_LLM_*` 配置是否完整，因此"分类器异常被静默吞掉"是否为并发现象尚未确认；
- 截图未显示更早的对话历史，无法确认用户此前是否见过"是否要使用企业云联络中心服务联系X…"的提问。