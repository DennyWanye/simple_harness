# Task 6B 三阶段工具 dispatch 结果

日期：2026-07-25

## 本次落地

- `ToolRegistry.begin_prepared()` 只解析并冻结 exact `ToolSpec`、调用参数、adapter/build
  identity 与 trusted context，不执行 handler。
- 内建 callable 使用 `FunctionPreparedToolDispatch`：
  `begin_prepared → start/started-ack → completion` 三段分离；`start()` 创建唯一物理
  task，并在 handler 获得 event-loop 执行机会前返回绑定 run/call/effect/adapter/runtime
  identity 的 ACK。
- `EffectBatchExecutor` 支持注入 `CurrentExecutionScopeLeasePort`，在 prepare/claim 前取得
  trusted owner scope，started/not-started/unknown handoff 持久化后立即释放短 fence，
  长 completion 在 fence 外等待，返回前重新验证 owner epoch。
- 既有 `execution_effects/attempts` 的
  `not_started/started/started_may_complete` 状态与 receipt API 被直接复用，没有增加第二套
  effect authority。
- `PreparedRunContextV1 → RunContext → ToolExecutionContext` 已增加 host-only
  `owner_key/profile_generation/binding_epoch` 传递；模型 payload 不能构造这些字段。
- dispatch adapter fingerprint 现在包含 artifact-derived execution build identity。

## 自动化证据

- `backend/tests/harness_simplification/test_wi2_tool_executor.py`：`25 passed`
- `backend/tests/harness_simplification/test_run_kernel.py`：`62 passed`
- brokered effect / workflow production wiring 邻接组合：`21 passed`

合计本 slice 聚焦回归：`108 passed`。

新增断言直接验证：

1. begin 阶段物理 handler 调用数为 0；
2. started ACK 先于 handler；
3. effect started receipt 在 scope fence 释放前持久化；
4. scope fence 在 completion/handler 长等待前释放；
5. 既有重启 unknown 与 settled-effect reuse 行为不读取漂移的 live policy。

## 尚未宣称完成

本文件只证明 Task 6B 的 product-neutral dispatch 机制。Task 7 仍需完成
`CapabilityPlatform` production scope authority、run-catalog atomic projection/ReadyGate、
Store/runtime ledger 与主组合根注入，之后才能称为生产 Capability 执行闭环。
