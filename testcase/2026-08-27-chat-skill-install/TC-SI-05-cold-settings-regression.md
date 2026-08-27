---
id: TC-SI-05
purpose: Verify cold-start persistence, Settings service parity, and unaffected first-party capability permission project and shell surfaces
status: active
surface: desktop-ui
type: hybrid
obligations: [TO-SI-5, TO-SI-6, TO-SI-R4]
tags: [skill-install, cold-start, settings, regression]
entrypoint: fresh app and Settings Skill Store
revision: 1
---

# TC-SI-05 — 冷启动、Settings 一致性与回归

## 步骤与预期

| 步骤 | 真人操作 | 预期结果 |
|---:|---|---|
| 1 | 使用全新隔离 user-data 启动候选构建，首次登录，注册 A/B，立即在 A 执行 TC-SI-01。 | 异步注册/登录冷路径中 typed installer、确认与 verification 均可用；不得靠暖重启恢复。 |
| 2 | 完全退出本轮 app/backend/Vite，重启后在 A fresh Run page-in 已装 Skill。 | binding/receipt/catalog 从 durable authority 恢复，hash 与重启前一致。 |
| 3 | 重启后在 B 与 projectless fresh Run 搜索同名 Skill。 | 均不可见；A 的 authority 不泄漏。 |
| 4 | 从 Settings“从 URL 添加”安装另一个 single fixture。 | stage、确认卡字段、权限、receipt、错误码和 chat 路径一致；只确认一次且新 Run 可用。 |
| 5 | 执行 critical-surface smoke：17 个 first-party Skill 清单/代表性 page-in、Capability Pack 搜索激活、普通 permission deny/allow、Project A/B/projectless、普通 shell 受控读操作。 | 数量/代表性调用、能力目录、权限卡、Project 隔离和 shell 行为均不回归；legacy user inventory 不成为 authority。 |

## 通过条件

- 步骤 1 是真正 fresh userdata→首次登录→直达功能；步骤 2 是完整进程重启。
- primary evidence 含 UI capture、fresh/restart process topology、三域 catalog/hash、Settings/chat service correlation 与 smoke 结果。

