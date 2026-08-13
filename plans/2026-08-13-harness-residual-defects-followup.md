# Follow-up — Harness 剩余契约、测试账本与结构债务

> **状态**：📋 已登记，后续处理；当前 canonical Harness 主路径可用于受信任的复杂任务
> **优先级**：P1/P2（按下文顺序处理）
> **登记时间**：2026-08-13

## 1. 当前事实

当前完整 Harness 回归为 `921 passed / 4 xfailed / 0 failed`，可靠性门禁为后端
`58 passed`、前端 `14 passed`，最终 `HARNESS_RELIABILITY: PASS`。这说明当前 canonical
RunKernel、EffectBatchExecutor、durable cancel 与恢复主路径没有已知 P0 阻断。

单独用 `pytest --runxfail` 强制执行
`backend/tests/harness_simplification/test_current_failures.py` 中四个 expected-red 用例，结果为
`4 failed`。但对当前生产契约的直接测试结果为 `3 passed`：

- `EffectBatchExecutor` 已按原始顺序执行连续 safe segment，并在 unsafe 调用前后建立屏障；
- canonical `prepare_call` 已拒绝模型提供 `session_id`、`_write_scope_root` 等宿主保留字段；
- `chat_v2_interrupt` 已进入 `_cancel_product_harness_run`，并调用 durable
  `run_client.cancel`。

因此，四个 expected-red 不能继续整体解释为四个当前产品缺陷。其中三个仍绑定退役的
`AgentLoop` 执行假设或仅按函数名判断取消行为，已经成为陈旧测试账本；另一个则暴露了旧兼容
API 的真实加固缺口。

## 2. P1 — 旧 `execute_tool` 兼容 API 的宿主字段覆盖

`ToolRegistry.execute_tool()` 当前先写入 session context，再用调用参数覆盖。直接调用旧 API 时，
模型参数可以覆盖 `_session_id`、`_write_scope_root` 等宿主字段。canonical prepared path 已通过
`reject_reserved_model_fields` fail closed，当前 Harness 主路径不受该问题影响，但兼容入口不能
保留不同的信任语义。

后续处理要求：

1. 审计 `execute_tool`、`execute_tool_outcome`、`partition_dispatch` 的实际调用图，区分生产兼容入口
   与仅测试/手工脚本入口。
2. 对所有模型可控参数复用 canonical reserved-field 校验；优先显式拒绝并返回 typed error，不能
   静默接受后再依赖字典 merge 顺序纠正。
3. 宿主上下文只能由 `ToolExecutionContext` 或受信任 session context 提供；模型不得提供任何等价
   alias 绕过。
4. 增加同步/异步 handler、permission continuation、MCP 以及 direct compatibility API 的负向测试。
5. 若确认兼容 API 已无生产调用，优先退役而不是长期维护第二套执行语义。

验收：所有公开执行入口对宿主保留字段采取相同 fail-closed 语义，handler 永远只能观察到宿主
绑定值；完整 Harness 与相关工具回归零意外失败。

## 3. P1 — 清理陈旧 expected-red 测试账本

`test_current_failures.py` 的两个并发用例直接消费 `AgentLoop.run()`，但当前 `AgentLoop` 只产生
`ToolBatchEvent`，真实执行和屏障属于 `EffectBatchExecutor`；当前失败表现为 snapshot `KeyError`，
不是生产并发违约。`/stop` 用例只在 interrupt 分支内按函数名搜索 `workflow/run_kernel/kernel`，
没有追踪 `_cancel_product_harness_run -> run_client.cancel`，因此同样误报。

后续处理要求：

1. 删除已被 canonical 直接测试覆盖的陈旧 expected-red，或改成真正穿过当前生产接缝的回归。
2. 将旧 `execute_tool` 的 reserved-field 反例迁入对应兼容 API 测试，修复后取消 xfail。
3. 增加门禁：expected-red 必须标明当前 owner、入口和清除条件；owner 退役时测试必须同步迁移。
4. 最终完整 Harness 应达到 `0 failed` 且不再保留这四个误导性 xfail。

## 4. P2 — 核心文件结构余量过小

当前 `RunKernel=1,291/1,300`、`AgentLoop class=3,727/3,800`、`react.py=5,132/5,200`。
预算仍通过，但 Kernel 只剩 9 行余量，下一次小改动就可能重新触发结构门。

后续修改必须优先抽取单一职责 leaf module，并保持 authority、事务与 durable state owner 不变；
不得通过抬高预算、压缩可读性或删除验证逻辑换取通过。每次结构修改继续运行 construction、parity、
authority 和 reliability gates。

## 5. 已单独登记的产品/审计边界

- **P1 — DeepResearch Top N 与 partial 主卡**：见
  `plans/2026-07-20-deepresearch-topn-quality-followup.md`。后端业务终态为 partial 时，UI 主卡不能只
  显示 engine completed/100%；Top N 还需确定性数量与逐项引用门。
- **P2 — `run_shell` 瞬时文件事件审计**：见
  `plans/2026-08-13-shell-file-event-audit-followup.md`。当前可验证最终残留，但无法完整观察同一次 shell
  内创建后立即删除的 workspace 外文件。

## 6. 非本 follow-up 范围

- 冷启动性能对照按用户决定暂不测试，不列为当前 Harness 缺陷。
- Realtime 语音按用户决定继续关闭，待 Harness 稳定后另行恢复。
- plan-test gate 继续暂停使用。
- `plans/2026-08-13-simple-harness-sdk/` 属于独立工作，不纳入本次提交。
- 原始截图、录屏、日志、数据库、receipt 和 diagnostic bundle 继续仅保存在 gitignored 本地目录，
  等后续归档到局域网 NAS；Git 只提交本 follow-up 的结论与可复跑命令口径。
