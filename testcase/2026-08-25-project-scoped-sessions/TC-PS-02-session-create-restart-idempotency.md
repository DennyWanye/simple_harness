---
id: TC-PS-02
purpose: Verify atomic immutable project Session creation, restart persistence, transaction rollback, and lost-ACK replay
status: superseded
surface: desktop-ui
type: hybrid
obligations:
  - TO-A2
  - TO-R1
tags:
  - session
  - restart
  - idempotency
entrypoint: new project session
revision: 1
replacement: TC-HM-09
---

# TC-PS-02 — 项目 Session 创建、重启与请求幂等

## 前置

- 已注册一个隔离测试 Project，当前没有 Session。
- 自动化 fixture 能在 Session 创建事务提交前注入一次失败，并能模拟“服务端已提交但客户端未收到 ACK”。

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 在 Project 分组点击“新建同项目 Session”。 | 新的空 Session 立即出现在该 Project 下；进入聊天界面前它已有稳定 Session 身份与项目绑定。 |
| 2 | 在新 Session 发送一条可区分消息并等待本轮结束。 | 消息与 Run 均归属该 Session；侧栏仍归于同一 Project。 |
| 3 | 检查 Session 的所有可见菜单、右侧项目信息和设置入口。 | 没有更换 Project、修改 `project_root` 或修改 `execution_root` 的入口。 |
| 4 | 完全退出应用及其自有后台进程，再用同一隔离 user-data 启动并打开该 Session。 | Session、消息、项目分组和两个只读 root 值恢复；项目绑定与重启前一致。 |
| 5 | 在事务提交前注入失败并发起一次创建。 | UI 显示可重试错误；列表中没有新 Session、孤儿 Project 或半绑定条目。 |
| 6 | 用一个新的请求标识创建 Session，在服务端提交后丢弃首次 ACK，再用完全相同请求重试。 | 两次响应指向同一个 Session；列表只增加一次，消息/绑定/创建副作用不重复。 |
| 7 | 使用步骤 6 的请求标识提交不同创建意图。 | 请求被稳定拒绝为冲突；原 Session 与绑定不改变，不产生新 Session。 |

## 通过条件与证据

- 步骤 1～7 全部满足；步骤 4 必须是真实完全重启，步骤 5～7 必须有持久结果计数证据。
- UI primary：空 Session 即时可见、菜单无编辑入口、重启后恢复截图。
- runtime primary：创建请求标识、Session/Project 身份、事务前后行数和 lost-ACK 两次响应摘要。
