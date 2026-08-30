---
id: TC-PS-04
purpose: Verify every fresh Run and physical file operation derives one immutable execution root from the Session binding
status: superseded
surface: integration
type: hybrid
obligations:
  - TO-A4
  - TO-R2
tags:
  - workspace-authority
  - tools
  - fail-closed
entrypoint: project chat run
revision: 1
replacement: TC-HM-09
---

# TC-PS-04 — 单一 workspace authority

## 前置

- Project execution root 中放置唯一 canary 文件与一个可安全覆盖的输出位置。
- 另准备两个不同目录，分别作为前端临时路径 canary 与 legacy/最近 Run 路径 canary；其中不得存在正确 canary。

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 从真实项目 Session 通过生产聊天入口发起 fresh Run，要求读取 canary，并执行一次仅写入测试输出文件的受控操作。 | 读取内容正确；输出只出现在绑定的 `execution_root`，其他目录没有新文件或修改。 |
| 2 | 对账本轮公开可观测的 Session、Run、workspace、write scope、终端 cwd、文件工具根与项目规则发现根。 | 所有值均追溯到同一 Session Binding 版本，且有效根完全一致。 |
| 3 | 依次向公开入口提供冲突的前端路径、旧 Code Session 路径、最近 Run 路径和模型文本路径，再各启动一个 fresh Run。 | 每轮仍只使用 Session Binding 派生的 root；冲突字段被忽略或明确拒绝，不能改变物理落盘位置。 |
| 4 | 构造缺失或无法恢复的绑定 authority，再尝试终端和文件工具。 | Run/工具在物理执行前 fail closed；不得回退全局 workspace，也不得在任何 canary 目录产生副作用。 |
| 5 | 重启后再次执行步骤 1。 | fresh Run 使用与 Session Binding 一致的根；重启前后 authority provenance 连续且没有 latest-Run 漂移。 |

## 通过条件与证据

- 步骤 1～5 全部满足；物理输出目录树前后 hash/清单必须证明无越界写入。
- primary evidence 至少包含真实 UI 提交截图、root/Session correlation 日志、各 authority 值摘要和三棵目录的前后文件清单。
