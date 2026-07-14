# Historical Blocked Audit: Durable Graph Workflows And Trace

日期：2026-07-10

## 结论

`plan-test` 第一轮完整审计在第 6 轮仍返回 `VERDICT FAIL`，因此当时停在 Phase 2。用户随后明确恢复完整范围并要求继续端到端执行；本文件仅保留为历史审计证据，不再表示当前任务停止。

## 剩余 Blockers

1. **Node execution 与 retry attempt 必须拆表**
   - `workflow_nodes` 只能保存一个 execution 的 latest projection。
   - 另建 `workflow_node_attempts(node_execution_id, retry_attempt, status, started_at, ended_at, error_ref, ...)` 复合主键，才能保留每次失败/放弃/重试证据并满足 AC-3。

2. **HITL 必须闭合到 LangGraph resume 协议**
   - decision 需要持久化 LangGraph `interrupt_id/task_id/checkpoint_ns`。
   - runner 必须使用同一 thread/config 调 `ainvoke(Command(resume={interrupt_id: validated_response}), durability="sync")`。
   - resume 值只能来自已 CAS 保存并校验 hash 的 DB response，不能直接信任 WebSocket payload。

3. **Workflow start 必须与 session delete 线性化**
   - start/run-create/accepted event 也要进入 per-session lock，并在锁内重新校验 delivery epoch/tombstone。
   - run 创建成功后才释放锁；startup recovery 要处理 tombstone 已写但 cancel/discard 未完成的半状态。

## 仍需同步修正的 Major

- 给 blocked delivery 增加 list/retry/discard IPC、审计事件和 Run Inspector 控件。
- 未分类动态 plugin/MCP 写工具只从 graph allowlist 排除，不应关闭全部 graph。
- “LLM 不重调”只保证 proposal pending/full write 已持久化之后；更强保证需要 model-result journal。
- v1 明确禁用 waiting checkpoint fork，或专门实现 single interrupted task 的 resume 清理；当前两种表述冲突。
- session delete 必须留下可验证的 accepted receipt closure/termination tombstone，不能永久停在 pending。

## 已完成产物

- 根目录 `acceptance.md`：AC-1 至 AC-18。
- `ARCHITECTURE/`：Harness 当前基线与生命周期契约。
- `plan.md`：六轮挑战后的详细实现协议和 22 个任务。
- Harness manifest/main callsite/docs 契约修正及相关测试：已通过 113-test gate，聚焦测试 16 passed。

## 建议的解除方式

把当前大目标拆成三个独立、可单独验收的计划：

1. `Runtime + checkpoint + HITL + effect journal`，先用最小演示图完成恢复语义。
2. `Trace + outbox + replay/eval UI`，建立统一观测与交付。
3. 依次迁移 `DeepResearch -> PPT Pro -> Complex Code`，每条生产图单独挑战、实现和真机验收。

只有用户确认重新拆分/启动新的 plan-test 审计后，才继续实施。
