---
id: TC-GS-01
purpose: Verify a fresh ordinary Session receives one durable automatic workspace under native Documents
status: active
surface: desktop-ui
type: hybrid
obligations: [TO-A1, TO-A7, TO-A8, TO-R1]
tags: [ordinary-session, automatic-workspace, canonical-identity, restart]
entrypoint: new ordinary session
revision: 1
---

# TC-GS-01 — 普通 Session 默认工作区

## 前置

- 当前 macOS 候选构建、全新隔离 user-data、唯一未占用端口。
- 记录 native Documents 下 `SimpleHarnessProjects` 的创建前目录清单与 SHA-256。

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 从可见 UI 新建普通 Session，选择“使用默认目录”。 | UI 显示创建中；完成前不出现可使用的半绑定 Session。 |
| 2 | 打开 Session 工作区信息并记录显示路径。 | `SimpleHarnessProjects` 只新增一个该 Session 独占子目录；Session 仍显示为普通会话。 |
| 3 | 从真实聊天入口请求输出 `pwd` 并在工作区写入唯一 canary。 | UI 路径、持久化 canonical identity、工具 cwd 与 canary 实际目录一致；其他目录无写入。 |
| 4 | 完全退出 app 及其自有子进程，以同一 user-data 冷重启并打开该 Session。 | Session ID、工作区路径与 canonical identity 不变；新 fresh Run 仍写入同一根。 |

## 决定性证据

- UI 前后截图、Session/root correlation、目录前后清单、canary SHA-256、新进程 incarnation。
- 目录存在但没有真实 Run/物理写入不能判 PASS。
