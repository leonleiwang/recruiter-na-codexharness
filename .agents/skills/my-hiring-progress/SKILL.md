---
name: my-hiring-progress
description: 查看当前钉钉发送者本人发起的招聘需求与进展，并继续自己已暂停、候选评审中或等待 HR 寻源的需求。绝不返回其他用户的招聘需求。
---

# 我的招聘进展

## 权限硬边界

所有 Recruitment Request 必须按标准化 `senderId` / `owner_staff_id` 在 Repository 层过滤。

允许：
- 查看我自己发起的招聘需求。
- 继续我自己的 JD 待确认、候选评审、暂停或等待 HR 寻源流程。
- 对我自己已经收到的候选人执行“换一批 / 选择”。

禁止：
- 查询其他人发起的招聘需求。
- 查询公司所有招聘需求。
- 浏览或管理整个画像库、JD 库、简历数据库。
- 修改/删除简历。

Host Workflow 是权限权威；Prompt 不是权限边界。
