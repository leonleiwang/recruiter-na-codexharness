# Recruiter-Na 腾讯云 CCC 招聘联系闭环：执行交接报告

**生成日期：** 2026-09-21  
**当前仓库：** `/Users/wanglei/Downloads/Indivadual Project/Github/CodexHarness/recruiter-na-codexharness`  
**状态：** 设计已确认且已提交；尚未开始功能代码改动、部署或真实外呼。

## 已完成且已核实

1. 现有 v5 选择入口位于 `runtime/rn/hiring_workflow_v5.py`：推荐候选人后，用户说“选第 N 个”或姓名会调用 `HiringRequestRepository.select_candidate()`，将招聘需求置为 `SELECTED`。
2. 当前招聘请求持久化在 `runtime/rn/hiring_requests.py` 的 SQLite 库中；每个公开读写均基于 `owner_staff_id` 过滤。
3. 当前生产机器人入口为 `im/recruiter-na.mjs`，通过 `runtime/rn/host_tool.py workflow handle` 调用 `HiringManagerWorkflow`。
4. 已提交的设计文件为 [2026-09-21-recruiter-na-tencent-ccc-contact-design.md](../superpowers/specs/2026-09-21-recruiter-na-tencent-ccc-contact-design.md)，提交 `421dc4a`。
5. 腾讯云 CCC 当前官方能力已经核验：
   - `CreateAIAgentCall` 可发起单次智能体外呼；
   - `DescribeTelCdr` 可按 `SdkAppId`、时段和 `SessionId` 查询电话服务记录与录音；
   - 语音智能体外呼前提包含语音智能体服务及自有电话号码接入；
   - 第三阶段的双人桥接 API 不能根据旧名称假定，须针对当前 CCC 实例及最新版官方 API 复核。

## 本轮未经完成的事项

- ContactTask 表/迁移、状态机、事件审计和租约调度；
- `CCCClient`、`FakeCCCClient` 与 Tencent SDK 生产适配器；
- 从 `SELECTED` 创建幂等联系任务的工作流接线；
- `联系进度` 与 `取消联系` 钉钉交互；
- CCC 回调鉴权、CDR、录音和 ASR 引用回写；
- 自动化测试与完整 Fake 流程；
- Recruiter-Na 云端部署；
- 受控演示同事的第一次真实接通外呼。

因此，不得声称该功能已经实现、已部署、测试通过或已经产生真实电话。

## 批准的产品流程

```text
SELECTED
  -> CONTACT_PENDING
  -> CANDIDATE_CALLING
     -> CANDIDATE_CONFIRMED / CANDIDATE_RESCHEDULE / DECLINED / RETRY_PENDING
  -> MANAGER_CALLING
     -> MANAGER_CONFIRMED / CANDIDATE_RESCHEDULE / CANCELLED
  -> SCHEDULED
  -> BRIDGE_CALLING
  -> COMPLETED
```

候选人与经理都必须对同一绝对时间明确确认；经理改期必须回到候选人确认。Candidate Decision、推荐顺序、画像和评分不得被联系子系统修改。

## 关键验收阶段

### M0：Fake 完整闭环

默认使用 `FakeCCCClient`，证明从 `SELECTED` 到 `COMPLETED` 的三阶段会话关联、幂等、回调和重启恢复。此阶段零真实拨号。

### M1：受控真实首次外呼

通过正常 DingTalk Top-3 推荐链路推荐一位已同意参与测试的演示同事。用人部门以正常“选第 N 个/姓名”操作选择该人。仅当全部安全门通过时，CCC 发起且对方实际接通候选人确认电话。

验收证据必须包含：请求 ID、候选人 ID、脱敏被叫、CCC `SessionId`、开始/结束时间、接通终态、录音/ASR 引用（若 CCC 已可用）。API 返回、仅响铃、Fake 回调都不算 M1 成功。

用户已提供演示号码作为敏感数据；**不要将该号码复制到本报告、源码、测试、Git、日志或普通聊天消息**。仅在需要真实试拨时，经服务器的 `RECRUITER_CCC_ALLOWED_CALLEES` 或等价受保护秘密注入；外呼前再次取得行动时确认。

## 安全和配置门

真实拨号必须同时满足：

- `RECRUITER_CCC_ENABLED=true`；
- `RECRUITER_CCC_REAL_DEMO_ENABLED=true`；
- `TENCENT_CCC_SECRET_ID`、`TENCENT_CCC_SECRET_KEY`、`TENCENT_CCC_SDK_APP_ID`、`TENCENT_CCC_AGENT_ID`、`TENCENT_CCC_CALLER_NUMBER` 和回调密钥已由受保护环境注入；
- CCC 智能体、套餐、自有号码接入及外呼能力已在当前应用中可用；
- 演示同事已确认接听及录音政策；
- 演示号码命中受保护白名单，日限额未超，且任务没有既有 SessionId/进行中幂等记录；
- 本次真实通话在动作前得到用户确认。

默认配置必须是 `RECRUITER_CCC_ENABLED=false` 和 `RECRUITER_CCC_REAL_DEMO_ENABLED=false`。失败时必须零外呼，并只记录脱敏失败原因。

## 服务器与部署边界

用户明确说明：应用已部署在 `42.192.114.176`，可以部署 Recruiter-Na；同机存在三个 PulseHR 正式/开发项目，**绝对不能动**。

已知历史部署约束（需在部署前再次现场核验）：

- 使用 SSH 账户 `ubuntu`；
- 仅允许修改/重启 `recruiter-na.service` 及本功能明确新增的 Recruiter-Na worker/timer；
- Recruiter-Na 生产入口曾为 `/opt/recruiter-na/app/im/recruiter-na.mjs`，环境文件曾为 `/etc/recruiter-na/recruiter-na.env`；
- PulseHR 保护服务历史上包括 `pulsehr-personal-dingtalk.service` 与 `pulsehr-personal-api.service`，但必须在本次部署前通过只读 `systemctl` 列表确认实际三个受保护目标；
- 部署前后都要记录受保护服务的 active 状态，绝不 restart、stop、edit 或 reload 它们；
- 先创建 Recruiter-Na 回滚锚点和配置备份，再停止/更新 Recruiter-Na；传输后比对 SHA-256；只启动并验证 Recruiter-Na。

## 下一窗口的建议执行顺序

1. 阅读设计文件与本报告，建立实现计划；在隔离 worktree 中完成开发，不在 `main` 直接写功能代码。
2. TDD：先写 `ContactTaskRepository` 的失败测试，再实现独立表、状态机和 owner 隔离。
3. TDD：实现默认禁用的 `CCCClient`/`FakeCCCClient`、安全门和 Session 归档边界。
4. TDD：将 `SELECTED` 接至幂等 `ContactTask`，实现“联系进度/取消联系”。
5. TDD：实现持久化调度器、租约/幂等、回调鉴权、CDR/ASR 引用获取；先使用 Fake 端到端验证。
6. 写 Tencent 生产适配器，并用当前官方文档和 CCC 控制台只读确认真实参数、号码和桥接 API；不得猜测第三阶段 API。
7. 运行全套 `scripts/verify.sh`，代码审查，生成实施报告。
8. 仅在获得 CCC 凭据/资源、演示同事同意和当次外呼确认后，将敏感值写入服务器受保护环境，部署 Recruiter-Na，并执行 M1 一次。

## 交接起点

```bash
cd '/Users/wanglei/Downloads/Indivadual Project/Github/CodexHarness/recruiter-na-codexharness'
git show --stat 421dc4a
sed -n '1,260p' docs/superpowers/specs/2026-09-21-recruiter-na-tencent-ccc-contact-design.md
sed -n '1,300p' docs/reports/TENCENT_CCC_CONTACT_IMPLEMENTATION_HANDOFF_20260921.md
```

