# S5b — effect gate / semantic closure / memory analysis（2026-09-03）

被测 HEAD：Host `a234284d`（收尾期提交见 RUNLOG）。机器账本：
`plans/2026-08-29-human-memory-digital-twin/increments/2026-09-02-s5b-effect-closure-memory/verification/r3-s5b`。

## 场景结果

| Testcase / 场景 | required 步骤 | 结果 | 证据 |
|---|---|---|---|
| TC-HM-11 rev6 / S5B-S1 | 最小验证动作：真实 provider 改文件 → 客观事件 → `task_scope_update` 收口 → 同事务 outbox → analysis accepted；≥2 独立 root | **PASS**（×2） | `.local-test-evidence/real-ui-channel/prod-lane-05`、`prod-lane-08`（另 `verify-06` 第 3 次复现） |
| TC-HM-11 rev6 / S5B-S2 | 收口故障矩阵（11 seam kill/replay） | PASS | r3 `artifacts/faults/s2-fault-runner.log` |
| TC-HM-08 rev5 / S5B-S3 | analysis 幂等与五态 attempt（11 seam） | PASS | r3 `artifacts/faults/s3-fault-runner.log` |
| TC-HM-09 rev5 / S5B-S4 | effect gate 六类拒绝 + run-fault 三稳定码（11 seam） | PASS | r3 `artifacts/faults/s4-fault-runner.log` |
| TC-HM-14 rev4 / S5B-S7 | 冷启动 v46 + composition 逐缺件 fail + 载荷五类变异 | PASS | r3 `artifacts/ui-a/`（fresh userdata 0→46 + 真实 UI 1-turn） |
| TC-HM-11 rev6 / S5B-S8 | 真实 UI 通道 1-turn 真实 provider | PASS | 同上（Browser pane 真实点击，终答非空） |
| — / S5B-REG-FULL | Host 全量 + Memory 全量 | PASS | Host 6459 passed / 恰好 7 条已知环境红 / 零意外；Memory 全绿 |

## 口径说明（不得省略）

1. **S5B-S1 的驱动入口是生产 `queue.enqueue`**（控制 WS `human_memory_request`，
   `memory/human_memory_api.py:207`），不是 pytest 里程碑车道。独立裁决（2026-09-03，裁决 D）
   判定该车道替换了 `ProductForegroundToolPort.freeze`、`context_route → append_binding`、
   `ForegroundRuntimeExecutionAuthority` 等多处生产接缝，不能作为本 AC 主证据。
2. **真实桌面 UI 载体面按 acceptance A14 移交 S6**（用户 2026-09-03 显式批准，
   hash `6086418d…`）：该链在桌面端**没有任何 UI 入口**，前端零调用 `queue.enqueue`，
   属 S6 Task 1/2 交付物。≥20 turn 长会话一并移交。
3. 全 AI 驾驶经用户批准（hash `7a84b793…`）。

## 本增量在真实执行中发现并修复的既有缺陷（10 处）

S5B-UI-F1（不可激活能力的披露与拒绝）、F2（schema 校验失败杀 Run，两轮扩面）、
F3（委派工具恒失败且不透明）、以及前台执行链 7 处：首次绑定死锁、`ingress.start` 不传
conversation、派生执行会话缺 sessions 行、context source 载荷缺 provider_messages、
Host 首轮路由回执不落账、`edit_file` 路径按进程 cwd 解析且拒绝不透明、
授权决策后不唤醒驱动 + 重入观察幂等键不含版本。

**共同根因**：`_drive_claimed` 全流程在 pytest 里零覆盖——测试基座手工按序推进任务，
真正的驱动器一步没跑，于是每处生产装配缺口都被基座恰好补上。
