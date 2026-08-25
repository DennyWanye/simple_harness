---
id: TC-PS-07
purpose: Verify missing project roots remain visible, tools fail closed, and relocation only accepts the same project identity
status: active
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
