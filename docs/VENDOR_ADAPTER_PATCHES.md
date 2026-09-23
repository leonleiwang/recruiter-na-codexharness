# 第三方适配器（vendor）补丁说明

`vendor/codexharness-im/` 因授权与来源原因**不纳入本仓库**（见 `.gitignore`），但会随发布包一起分发。
因此对该目录的改动不会被 git 记录。刷新 vendor 后，请按下表重新应用这些补丁。

## 文件

`vendor/codexharness-im/src/adapters/dingtalk-stream-bot.mjs`

## 补丁 1：入站消息不再静默丢弃

**问题：** JSON 解析失败、或 `normalizeDingTalkRobotMessage()` 返回 `null`（缺 `senderStaffId`/`senderId`/`msgId`）时，
原实现都是直接 `return`，日志里没有任何痕迹，无法区分"平台没投递"与"投递了但载荷不认识"。

**改动：**

- 新增导出函数 `summarizeDingTalkRobotMessage(message)`：只输出字段**有无**摘要
  （msgtype / conversationType / msgId / senderStaffId / senderId / text / sessionWebhook / robotCode / keys），
  **不输出消息正文**；
- 解析失败时 `logger.warn("[dingtalk] inbound payload is not JSON: …")`；
- 归一化失败时 `logger.warn("[dingtalk] inbound message ignored: " + summarize…)`。

## 补丁 2：可选的 SDK 原始帧日志

**改动：** `DWClient` 构造参数增加

```js
debug: String(process.env.DINGTALK_STREAM_DEBUG || "").toLowerCase() === "true",
```

**默认必须关闭**：原始帧里含 `sessionWebhook`（凭据），只应短期开启用于排障。

## 补丁 3：回调入口日志（可选，排障用）

在回调处理函数最前面增加一行无条件日志，用于区分"回调没被触发"与"回调触发了但载荷不识别"：

```js
this.logger?.info?.('[dingtalk] callback invoked topic=' + TOPIC_ROBOT + ' dataType=' + typeof res?.data);
```

确认无误后可移除，避免每次消息都打一行。