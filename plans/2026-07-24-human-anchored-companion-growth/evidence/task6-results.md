# Task 6A 验证结果：统一 Companion 动作策略与只读记忆基础

> 日期：2026-07-25  
> 结论：Task 6 中不依赖 Task 7 catalog single-capture 的部分已完成并通过自动化门；
> `CatalogGate/CurrentExecutionScopeLease` 的生产适配、MCP/local-runtime start ACK 接线
> 必须在 Task 7 完成后回补，因此本结果不把整个 Task 6 宣称为完成。

## 实现事实

- ToolSpec 的冻结 fingerprint 已覆盖 stable handler、typed effect/idempotency、
  target normalizer、artifact-derived build identity 和 dispatch adapter identity。
  checked-in source/effect/build manifests 必须同集，generator 的 `--check/--write`
  会拒绝重复、越界/reparse 路径、缺 artifact 和 stale bytes。
- `PreparedToolSet` 新增向后兼容、hash-covered 的 `confirm_only_names`、
  effect-policy version/hash/classification。`IrreversibleEffectPolicy` 对 Companion 前台和
  delegated Run 把 send/delete/pay/credential/privacy/unknown 强制并入 confirm-only；
  reflection/evaluation 不暴露这些工具。
- ReAct boundary 把 confirm-only 集、policy snapshot ref/hash 持久化。即使
  `auto_mode=ON`，Driver 仍打开原 durable permission decision；显式确认只签发绑定
  decision/nonce/call/effect/tool/args/capability/scope/snapshot 的一次性 grant。
  `TaskGrant` 历史覆盖和 `policy:auto` 不能绕过。
- `ToolExecutor` 再次核对当前 pinned PreparedToolSet 与 boundary snapshot hash，并要求
  confirm-only call 必须携带 exact one-shot grant，形成 Driver + Executor 双层门。
- 新增独立三阶段 `PreparedToolDispatch` 契约：
  `begin_prepared → start(ACK/NotStarted/Unknown) → completion`；它没有冒充 Task 7 尚未接入
  的 Registry/MCP/local-runtime adapter。
- `BackgroundRunAdapter` 直接使用 typed `KernelRunClient`，冻结 owner/job/purpose/
  evidence/persistence，不创建 `ProductVenueRunSession`，不投影 WS/TTS/codifier 或第二份
  growth。terminal usage 同 claim epoch 只结算一次。
- `CompanionActionDecisionService` 只接受 `decision_id + allow + trusted control identity`，
  从 execution DB 恢复原 session/principal/auth epoch/root/nonce/version/call/effect/hash，
  并校验 IdentityReady 与 active Companion run binding。
- `memory_recall` 的模型 schema 只有 `query/limit`。owner/generation/session set/as-of/
  tombstone scope 由 host 在 generation-0 growth snapshot 中原子冻结并持久化；
  readonly Retriever 使用 SQLite `mode=ro + query_only`，authorizer/trace 证明 SQL 写入为 0。
- Companion schema 从 v1 升至 v2，迁移
  `002_owner_memory_read_scope_v2.sql` 增加 durable memory scope；setuptools 与
  PyInstaller 均收集 Companion migration 和三份工具 manifest。

## 依赖裁决

计划把 `prepare_run_catalog_lease`、`CatalogGate` 和生产
`CurrentExecutionScopeLeasePort` 放在 Task 7，同时又要求 Task 6 直接使用它们。当前实施
按真实依赖拆成：

1. Task 6A（本结果）：声明、策略、durable decision、只读 memory、background adapter
   与 dispatch port；
2. Task 7：实现唯一 catalog/owner/runtime authority 和 production lease/adapter；
3. Task 6B：回到本 Task，把生产 Driver/Executor/MCP/local-runtime 接到 Task 7 的单一
   capture，并跑完整 send/delete/pay/credential crash matrix。

这样不会创建临时第二权威，也不会用 mock adapter 冒充 production 接线。

## 自动化证据

```text
Task 6A manifest/policy/snapshot：31 passed
Task 6A memory scope/readonly recall 邻接组合：65 passed
Task 6A background/action/dispatch + venue/runtime 邻接：84 passed
新增 confirm-only Driver/Executor + authorization runtime：24 passed
authorization UoW：4 passed

扩大组合：
152 collected；151 passed，1 个既有恢复 timing case 首轮超时未观察到 resume；
该 case 随后隔离重跑：1 passed

scripts/generate_execution_build_manifest.py --check：PASS
py_compile：PASS
pyproject TOML / PyInstaller spec compile：PASS
git diff --check：PASS
```

## 真人 E2E 边界

Task 6A 的 production Companion authority 仍 dormant，新的 `memory_recall` 仅在 manifest
中标记 planned，background/action service 未接主消息页，因此没有新的可点击生产行为。
本轮遵守当前逻辑 turn 的 computer-use 停止约束，没有继续驱动 UI。Task 7 + Task 6B 接线
后，在 Task 14/15 统一用真实源码 Tauri 主消息页验证 Auto 下 confirm-only waiting、
原 decision 恢复和一次性 effect claim；协议直注不能替代。

## 进程与端口清理

- 最终匹配 DeskPet pytest/python survivor=0；8100/5173 listener=0。
- 两次 authorization UoW 诊断最初因测试未关闭 `ExecutionWriteLane` 导致 pytest 已结束但
  进程不退。修复测试 teardown 后 `4 passed in 1.48s`。两次均按精确命令/PID/creation
  time 停止，分别释放约 `871 MB` private memory，survivor=0。
