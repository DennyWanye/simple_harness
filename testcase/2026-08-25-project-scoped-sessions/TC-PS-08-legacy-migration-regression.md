---
id: TC-PS-08
purpose: Verify idempotent legacy migration preserves Session history and affected existing behavior
status: active
surface: integration
type: hybrid
obligations:
  - TO-A8
  - TO-R4
tags:
  - migration
  - regression
  - restart
entrypoint: application startup migration
revision: 1
---

# TC-PS-08 — 旧数据迁移、幂等与受影响回归

## 前置

- 使用不含真实用户数据的旧库 fixture：至少两个可识别不同 project root 的 Code Sessions、一个等价路径重复项、一个无法识别 root 的历史会话、标题/消息/排序、已归档和已删除会话、Memory、Provider 与 resume 状态。
- 记录 fixture 文件 SHA-256、逻辑快照和可恢复备份位置。

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 用隔离 user-data 启动候选构建，等待迁移完成后进入 UI。 | 迁移成功后才开放会话创建；可识别 roots 映射到对应 Projects/Bindings，等价路径不重复；无法识别项保留为无项目会话。 |
| 2 | 比对迁移前后 Session 集合、消息正文/顺序、标题、排序、归档/删除可见性、Memory、Provider 与 resume 行为。 | 所有既有 authority 和历史语义保持；被删除 Session 不复活，无法识别会话不丢失。 |
| 3 | 完全退出并以同一数据再次启动，让迁移路径重复执行/确认已完成状态。 | 第二次启动不新增 Project/Binding、不改标题/排序/消息、不重复副作用；应用正常开放。 |
| 4 | 在迁移后的项目 Session 启动 fresh Run，再构造冲突的旧 project 字段。 | fresh Run 只接受新 Session Binding；旧字段不再成为新写入或执行 authority。 |
| 5 | 对迁移后的无项目聊天执行普通聊天、重命名、归档、恢复、删除与 Session resume 受影响 smoke。 | 行为与迁移前契约一致；无项目聊天不会获得本地开发能力。 |
| 6 | 对备份副本注入迁移中断并再次启动。 | 应用不开放会话创建；错误可见且原库/备份可恢复，没有部分迁移被当作成功。 |

## 通过条件与证据

- 步骤 1～6 全部满足；逻辑快照须逐项对账，不能只以 schema 版本作为 PASS。
- primary evidence：fixture hash、两次迁移计数/摘要、前后逻辑快照、UI 历史/空态截图和中断恢复日志。
