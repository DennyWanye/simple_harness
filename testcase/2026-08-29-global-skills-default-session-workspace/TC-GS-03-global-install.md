---
id: TC-GS-03
purpose: Verify Settings and Chat publish one idempotent user-global Skill with no Project or Session copy
status: active
surface: desktop-ui
type: hybrid
obligations: [TO-A3, TO-A7, TO-A8, TO-R2]
tags: [skill-install, global-scope, receipt, idempotency]
entrypoint: settings and chat skill install
revision: 1
---

# TC-GS-03 — 全局 Skill 安装

## 前置

- 使用 `GS-FX-IMMUTABLE-01` exact commit；记录 managed catalog 与所有 Project/Session/legacy skill 路径的前置清单。
- 准备无显式 Project 的 Settings 状态和一个用户选择目录 Session。

## 步骤与预期

| 步骤 | 真人操作 | 预期结果 |
|---:|---|---|
| 1 | Settings 从 URL 安装 fixture。 | UI 先显示全局安装进行中；只有 terminal global receipt 与 runtime verification 成功后显示成功。 |
| 2 | 在 Auto 模式的选目录 Session 通过 Chat 正式入口提交同一 exact request。 | 完整预检后无需人工确认，返回同一稳定 durable auto-approved terminal receipt/already-installed；不新增 active binding/version。 |
| 3 | 查看安装状态与 scope。 | 显示“全局/所有会话可用”、catalog generation、exact commit/hash；无当前 Project 依赖。 |
| 4 | 对账目录。 | 不写 Project `.claude/skills`、`.codex/skills`、每 Session 副本或 legacy userdata skills authority。 |
| 5 | 在新隔离 lane 从当前 Settings UI 安装 pinned `https://github.com/octocat/Hello-World` commit `7fd1a60b01f91b314f59955a4e4d4e80d8edf11d`（无 `SKILL.md`）。 | UI 从进行中转为失败，显示 `skill_candidate_not_found` 或其公开稳定映射与重试出口；绝不显示成功/已安装，catalog/receipt/managed tree 零错误增量。 |

## 决定性证据

- 两入口与失败 lane UI、operation/receipt/generation correlation、runtime verification 结果、目录树 SHA-256。
