---
id: TC-GS-05
purpose: Verify cold restart restores the primary conversation, TaskScopes, and shared global Skill and Tool catalogs without reinstall
status: active
surface: desktop-ui
type: hybrid
obligations: [TO-A1, TO-A4, TO-A7, TO-A8, TO-R5, HM-TO-R4]
tags: [cold-start, restart, catalog, provider]
entrypoint: application cold restart
revision: 2
---

# TC-GS-05 — 冷重启恢复

## 步骤与预期

| 步骤 | 真人操作 | 预期结果 |
|---:|---|---|
| 1 | 在 TC-GS-04 三类 Run 全部成功后记录 primary conversation、TaskScope、root、generation、descriptor digest 与 fixture hash。 | 基线身份完整且不含 secret。 |
| 2 | 完全退出 app、backend 与 Vite，确认旧进程均结束。 | 没有旧进程继续提供 catalog 或对话/TaskScope 状态。 |
| 3 | 用同一 user-data 重新启动当前 Tauri build，且不重新安装/修复 catalog。 | 新 app/backend incarnation 恢复同一 primary conversation、TaskScopes、binding revisions、global generation 和 descriptor digest。 |
| 4 | 三类 Run 各发起新 fresh root 并调用 fixture。 | 三次真实 Provider/Skill 调用均完成，identity/hash 与重启前 authority 一致。 |

## 决定性证据

- 重启前后进程拓扑、primary conversation/TaskScope UI、root/generation/digest 与三个 fresh root terminal。
- 旧 Session/Project Skill 数据迁移明确不属于本轮原型，不得以删除式 migration 作为本用例 PASS 条件。
