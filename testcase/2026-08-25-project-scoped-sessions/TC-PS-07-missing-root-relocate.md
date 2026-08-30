---
id: TC-PS-07
purpose: Verify missing project roots remain visible, tools fail closed, and relocation only accepts the same project identity
status: superseded
surface: desktop-ui
type: hybrid
obligations:
  - TO-A7
tags:
  - missing-root
  - relocate
  - fail-closed
entrypoint: project inspector relocation
revision: 1
replacement: TC-HM-09
---

# TC-PS-07 — 目录缺失与同项目重新定位

## 前置

- 注册一个含两个 Sessions 的测试 Project，并完成一轮可区分历史消息。
- 准备同一卷 rename 的目标路径，以及内容相似但身份不同的无关目录。

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 完全退出应用，把 Project 目录同卷 rename 到目标路径，再重启并打开原 Session。 | Project 分组、两个 Sessions、标题和消息仍可见；UI 明确显示“项目目录不可用”。 |
| 2 | 在缺失状态尝试发起项目开发 Run、终端与文件读写。 | 在物理执行前 fail closed；原路径、全局 workspace 和无关目录都没有副作用。 |
| 3 | 选择无关目录执行重新定位。 | 明确拒绝身份不匹配；Project 路径、revision、Session 归组均不改变。 |
| 4 | 在一个该 Project 的 root Run 处于活跃状态时尝试重新定位到 rename 后的同一目录。 | 明确拒绝或要求等待；不会在活跃 Run 中途切换根。 |
| 5 | 活跃 Run 终止后，选择 rename 后的同一目录并确认重新定位。 | Project 路径原子更新；该 Project 下两个 Sessions 一起恢复，Session 身份和项目归属不变。 |
| 6 | 再用旧 revision 重复提交，并重启应用。 | stale 请求被拒绝且不覆盖新路径；重启后新路径、历史和所有 Session 恢复稳定。 |

## 通过条件与证据

- 步骤 1～6 全部满足；任何缺失状态的物理工具执行成功或无关目录被接受均为 FAIL。
- primary evidence：缺失/拒绝/恢复 UI 截图、目录前后 identity probe、project revision、Session 集合及目录清单。

## 2026-08-26 实际执行结果

- 结论：`PASS`（macOS 当前 debug `.app`，fresh 与 temporal-fault 两条 lane）。
- 缺失目录后两个 Session 和历史仍可见；开发请求在 Provider/Tool 物理执行前生成 durable failed SDK root，
  UI 显示“项目目录不可用，请重新定位同一项目后再试”。该 root 的 `provider_invocations=0`、
  `execution_effects=0`，禁止文件没有生成，两侧 canary hash 未变。
- 系统目录选择器选中无关目录后返回 `project_identity_mismatch`，Project path/revision 未改变；活跃 root Run
  存在时 Inspector 不提供 relocation，重启恢复把该 Run 终止为 `workspace_unavailable` 并释放本项目 admission。
- 同身份 relocation 后 Project revision 为 4，两个原 Session ID 保持不变；完整重启后分组、历史和只读
  Inspector 根目录均恢复。真实 `deepseek-v4-flash` 分别完成绑定根内绝对路径 `read_file` 和 `write_file`，
  无关目录只保留原 canary。
- 原始证据仅在 ignored `.local-test-evidence/2026-08-26/project-scoped-sessions-missing-root/`；gate 账本
  `run-20260825-190455` 记录 2 个 root runs 与 5 份 primary evidence。完整 release 仍受其他 required
  场景未执行阻塞，本 testcase 的 PASS 不等于 AC-1～AC-8 全绿。
- 观察到两个独立后续项：相对路径 `read_file` 返回 `tool_failed`，但同一绑定根绝对路径读取成功；同一轮并行
  激活多个工具时曾出现 immutable TaskGrant 冲突。本 testcase 依靠分开的真实 root runs 证明文件读写边界，
  不把这两个观察项写成已解决。
