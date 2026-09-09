# Manual 模式旅程 run4（2026-09-09 11:30–12:00，Host 1a540f01 源码 + bundle 60ab03a1，Memory 0.6.37，flash 关闭 thinking）— 驱动在 T7 按硬判据停止

证据：`.local-test-evidence/2026-09-09/native-manual-run4/primary-ui-*`。驱动：加固后的 `manual_driver.sh`（绑定轮硬判据、600 s）+ 自动应答循环（同时答 `允许本次绑定` 与 `允许一次`）。

| 轮 | 结果 | 观察 |
|---|---|---|
| T1–T2 | COMPLETED（14 s / 37 s） | 无弹窗，grant 全 `policy:auto`（MM-2 / NC-M1 ✅） |
| T3 | UI | `workflow.db.authorization_policy_state` = manual / gen 1 / user_explicit ✅（MM-D1 修复后驱动读对库） |
| T4 | COMPLETED（41 s） | **绑定卡 1 张 + 工具卡 2 张均应答**，`manual_decisions_allow` +1、`binding_grants_manual` +1（MM-3 ✅、MM-D2 的「从未答过绑定卡」已消除） |
| T5 | COMPLETED（65 s） | 工具卡 4、绑定卡 1 |
| T6 | COMPLETED（87 s） | 工具卡 4 |
| **T7** | **failed `binding_not_decided`**（251 s，Run COMPLETED） | 模型先 `task_scope_search 二号任务` 却路由 `continue_active`（当前活跃任务是一号），再 `resume_existing` 二号被拒 `task_scope_conflict`（一个 Run 只能绑一个 scope），随后 `task_scope_update` 又被拒，最终向用户解释「本 Run 绑在一号任务」而未提出 root B 的绑定 → **MM-D3**：点名任务 ≠ 活跃任务时的路由选择与 `task_scope_conflict` 拒绝文案缺可执行下一步（同 B/U/Z 模式） |

`manual_verify.py` 对本段的判定见上方输出（部分项因未跑完记 INCONCLUSIVE）。下一次：MM-D3 修复后从 T1 重跑。
