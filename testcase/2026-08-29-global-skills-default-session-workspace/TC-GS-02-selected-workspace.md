---
id: TC-GS-02
purpose: Verify selected directory precedence, picker cancellation fallback, and invalid-directory atomic failure
status: active
surface: desktop-ui
type: hybrid
obligations: [TO-A2, TO-A8, TO-R1]
tags: [ordinary-session, selected-workspace, picker, atomicity]
entrypoint: new ordinary session
revision: 1
---

# TC-GS-02 — 用户选择目录优先

## 前置

- 三个独立 user-data lane；准备一个现有 Unicode 目录、一个随后删除的目录、一个普通文件和一个只读目录。
- 每 lane 记录 `SimpleHarnessProjects`、Session 与 binding 的基线计数。

## 步骤与预期

| 步骤 | 真人操作 | 预期结果 |
|---:|---|---|
| 1 | 新建普通 Session，打开 native picker 并选择 Unicode 目录。 | Session 冻结使用该目录；本 lane 不创建默认子目录。 |
| 2 | 真实 Run 写入 canary 后重启。 | canary 只在所选目录；重启后 canonical identity 与 Session 绑定不漂移。 |
| 3 | 新 lane 打开 picker 后取消，并从创建出的 Session 发起第二个独立 fresh Run 写入 cancel canary。 | 按“未选择”处理且只创建一个默认工作区；第二个 root_run_id 只写入该默认根。 |
| 4 | 分别选择已删除目录、文件与只读目录。 | 每次显示明确可重试错误；Session/binding/default-dir 均为零增量，无半绑定条目。 |

## 决定性证据

- picker、路径预览、错误态截图；每 lane 的目录/Session/binding 前后计数与 canonical identity。
