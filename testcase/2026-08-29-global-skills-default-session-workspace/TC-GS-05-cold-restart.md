---
id: TC-GS-05
purpose: Verify cold restart restores Session workspaces and the shared global Skill and Tool catalogs without reinstall
status: superseded
surface: desktop-ui
type: hybrid
obligations: [TO-A1, TO-A4, TO-A7, TO-A8, TO-R5]
tags: [cold-start, restart, catalog, provider]
entrypoint: application cold restart
revision: 1
replacement: TC-HM-02
---

# TC-GS-05 — 冷重启恢复

## 步骤与预期

| 步骤 | 真人操作 | 预期结果 |
|---:|---|---|
| 1 | 在 TC-GS-04 三类 Session 全部成功后记录 root、generation、descriptor digest 与 fixture hash。 | 基线身份完整且不含 secret。 |
| 2 | 完全退出 app、backend 与 Vite，确认旧进程均结束。 | 没有旧进程继续提供 catalog 或 Session 状态。 |
| 3 | 用同一 user-data 重新启动当前 Tauri build，且不重新安装/修复 catalog。 | 新 app/backend incarnation 恢复原 Session ID、root、global generation 和 descriptor digest。 |
| 4 | 三类 Session 各发起新 fresh Run 并调用 fixture。 | 三次真实 Provider/Skill 调用均完成，identity/hash 与重启前 authority 一致。 |
| 5 | 校验 `history-seed.sha256`，按 `history-fixtures.json` 的 loader command 注入 legacy projectless fixture，打开原 Session并发起首次 fresh Run，再冷重启。 | 一对一创建 automatic workspace；Session ID、历史、普通会话分类保持，重启不重复分配。 |
| 6 | 分别启动 terminal、nonterminal 与 conflict legacy Project Skill fixtures。 | terminal exact binding 收敛为唯一 user-global并退休 Project binding；nonterminal superseded；conflict durable fail-closed且上一 snapshot不变。 |

## 决定性证据

- 重启前后进程拓扑、三类 Session UI、root/generation/digest、三个 fresh root terminal、四个 history fixture 的迁移计数与终态。
