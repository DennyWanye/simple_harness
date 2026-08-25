---
id: TC-PS-06
purpose: Verify project grouping and all physical execution remain distinct when project_root differs from execution_root
status: active
surface: desktop-ui
type: hybrid
obligations:
  - TO-A6
tags:
  - project-root
  - execution-root
  - worktree-fixture
entrypoint: project session inspector
revision: 1
---

# TC-PS-06 — Project root 与 execution root 分离

## 前置

- 通过公开测试 fixture 建立一个 Session：`project_root` 与 `execution_root` 是两个不同且均存在的目录。
- 两个目录各有唯一 canary；只允许修改 execution root 中的测试输出位置。

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 打开该 Session 并查看侧栏与右侧只读区域。 | Session 归属于 `project_root` 对应 Project；Inspector 同时、准确显示两个不同 root。 |
| 2 | 通过真实聊天入口请求读取 execution canary 并写入测试输出。 | 读取到 execution canary；输出只出现在 `execution_root`，`project_root` 没有新增或修改。 |
| 3 | 请求读取只存在于 `project_root`、但不在 `execution_root` 的 canary。 | 按 execution scope 返回不存在/越界错误；不能因为 Session 的 Project 归属而扩大工具根。 |
| 4 | 启动第二个 fresh Run并重启应用后再启动第三个 fresh Run。 | 每轮均保持相同 Project 归组和独立 execution root；未自动创建、发现或删除 worktree。 |

## 通过条件与证据

- 步骤 1～4 全部满足。
- primary evidence：Inspector 与分组截图、三个 root run 身份、两个目录前后清单和写入结果 hash。

