# 钉钉机器人接入 DSH：会话绑定失效故障记录

**记录日期：** 2026-09-23
**现象级别：** 高（该机器人上每一条消息都失败）
**状态：** 已定位、已修复、已在真实钉钉上验证通过

## 1. 现象

用人经理给钉钉机器人「Ai招聘专员」发任何消息（包括 `/status`、`/new`、`你好`），
客户端都显示平台级失败卡片：

```
处理失败
任务未完成，暂时无法确定原因。请重试；若持续发生，请将参考号提供给管理员。
错误码：INTERNAL_UNKNOWN；参考号：MF-A005E622 / MF-5A61EF69 / MF-04E6F0BA / MF-3B74107E / MF-4A3774BC
```

## 2. 系统结构（两个消费者必须分清）

```
钉钉用户 -> DingTalk Stream -> dsh-im 插件 -> DSH Host -> recruiting-na preset -> 钉钉回复
```

- 机器人绑定：`~/.dsh/integrations/dsh-dingtalk/config.json`（clientId `ding0yc9sflomcx9ny72`）
- 绑定关系：`workspaces.json` → workspace `D:\迪安\人力AI工具\deepseek harness\recruiter-na-dingtalk`，preset `recruiting-na`
- **同一个 clientId 只能有一个 Stream 消费者**。独立的 `recruiter-na` 桥接（`im/recruiter-na.mjs`）
  若同时运行，会与 dsh-im 互相顶掉连接（`TERMINATE SOCKET: Ping Pong does not transfer heartbeat`），
  造成消息丢失与失败卡片。**两者不可同时运行。**

## 3. 根因

`bots/dt_e02a3cdbe594992c39ff51e7/state.json` 里的 p2p 会话映射指向一条**过期会话**：

```json
{"p2p:01572739536219986257": "session-5c3c8f38-0b1b-4865-8dd7-428142c51d71"}
```

该会话创建于 8 月 24 日，cwd 为 `C:\Users\MECHREVO`、preset 为 `liangshen`，而机器人后来被改成
workspace `recruiter-na-dingtalk` + preset `recruiting-na`。dsh-im 每次收到消息都去恢复这条不再匹配的
旧会话，harness 直接返回 turn 错误；dsh-im 把 `harness-turn-failed` 映射成 `INTERNAL_UNKNOWN`，
于是**每条消息都变成失败卡片**，且会话文件自 9 月 9 日起再无写入。

dsh-im 的错误码映射表（`@xmanrui/dsh-im/lib/index.js`）：

```
harness-turn-failed            -> INTERNAL_UNKNOWN
harness-http-failed / rpc      -> HARNESS_SERVICE
harness-response-invalid       -> HARNESS_PROTOCOL
model-* (providerCode 存在时)  -> MODEL_AUTH / MODEL_QUOTA / ...
```

因为失败不带 providerCode，所以只显示笼统的 `INTERNAL_UNKNOWN`。

## 4. 修复步骤

1. 停掉一切独立桥接实例（`bash scripts/stop-local.sh`），确保机器人只有一个消费者；
2. 备份并清空 `bots/<botId>/state.json` 的 `sessions` 字段——**必须写成无 BOM 的 UTF-8**，
   Windows PowerShell 5.1 的 `Out-File -Encoding utf8` 会写入 BOM，导致 dsh-im 无法解析该文件；
3. 重启 DSH web（清空动作只在启动时读取，运行中的实例会把旧映射写回）：

```powershell
$env:DSH_HOME = "$env:USERPROFILE\.dsh"
Start-Process -FilePath "D:\Trae CN\node.exe" `
  -ArgumentList @("--import","tsx/esm","apps/cli/src/bin.ts","web") `
  -WorkingDirectory "$env:USERPROFILE\deepseek-harness" `
  -RedirectStandardOutput "$env:USERPROFILE\.dsh\dsh-web.out.log" `
  -RedirectStandardError  "$env:USERPROFILE\.dsh\dsh-web.err.log" -WindowStyle Hidden
```

4. 等端口 3080 就绪，并等 `[<ts>] connect success` 出现在 web 日志里（本次约等了数十秒到数分钟），
   期间发消息仍然收不到；
5. 再发一条消息，`state.json` 的 `sessions` 会生成指向 **新会话**的映射，且新会话落在
   `recruiter-na-dingtalk` workspace、`agentPreset=recruiting-na`。

## 5. 验证证据（2026-09-23 12:31）

```
state.json: {"p2p:01572739536219986257": "session-2b9d1c59-aad5-4186-9418-eb4f5ecc344a"}
新会话 cwd      = D:\迪安\人力AI工具\deepseek harness\recruiter-na-dingtalk
新会话 preset   = recruiting-na
事件序列        = turn/start -> user/message(你好) -> assistant/chunk(block-end) -> finish(stop)
用量            = 9721 in / 223 out tokens
回复正文        = "你好！我是招聘娜，负责协助用人部门和 HRBP 做招聘需求整理和简历筛选推荐。……"
```

## 6. 运维要点

- **重启 DSH web 后不要立刻测试**：Stream 重新建连需要时间，日志里出现 `connect success` 才算就绪；
- 改了机器人的 workspace / preset 后，若旧会话仍被映射，同样会复现本故障；处理方式同上；
- 该机器人的唯一消费者是 dsh-im；要用独立 `recruiter-na` 桥接，必须先解绑/停用 dsh-im 对该机器人的绑定；
- dsh-im 的 `seenMessageIds` 只是"收到过"的记录，**不等于成功处理**；判断成功要看是否新建/更新了会话。

## 7. 相关文件

| 文件 | 作用 |
| --- | --- |
| `~/.dsh/integrations/dsh-dingtalk/config.json` | 机器人 clientId 与密钥引用 |
| `~/.dsh/integrations/dsh-dingtalk/workspaces.json` | 机器人绑定的 workspace 与 preset |
| `~/.dsh/integrations/dsh-dingtalk/bots/<botId>/state.json` | p2p 会话映射与已见消息 |
| `~/.dsh/.agent-presets/recruiting-na/` | 招聘娜 preset（persona + 工具集） |
| `~/.dsh/dsh-web-verify.{out,err}.log` | 本次重启后的 DSH web 日志 |
| `scripts/list-bridges.ps1` / `stop-local.sh` | 枚举与停止独立桥接实例 |