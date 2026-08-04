# Task 7 Runtime / Authority 集成结果

日期：2026-07-25

## 已完成

- `SqliteExecutionScopeAuthority` 只从 durable RunStart 与 bound catalog rows 重建
  owner/profile/binding/capability/scope facts。
- `CapabilityPlatform` 在 revocation shared barrier 内按
  `publish_lock → CatalogGate read` 获取 current execution scope lease。
- ToolBatch 对相同 execution scope 只获取一个共享 lease；全部 physical start
  ACK/NotStarted/Unknown 落定后再统一释放，避免逐 call 持 publish lock 的串行死锁。
- `CapabilityStoreRuntimeSetLedger` 已实现 owner runtime generation、activation、
  prepared set/member、exact launch claim、start outcome recovery、health、activated 与
  abort/cleanup_required 的 durable 协议，并注入生产 Platform。
- runtime instance id 在 set 持久化前由 operation/set/entry 确定；随机 nonce 只进入
  launch authorization hash，漂移 instance id 会在任何 adapter 调用前拒绝。
- Kernel 的 prepared context 已并入唯一 `LiveRun` index，authority census 恢复
  `run_map_authority_count=1`。

## 验证

- Capability：`165 passed`
- Companion 全量：`259 passed`
- runtime set / ledger / catalog gate：`18 passed`
- Task 7 关键组合（catalog/owner/runtime/skill/workflow/slash/Kernel/Executor）：`155 passed`
- Harness 分批回归覆盖全部文件；authority audit：
  `runtime_dml_authority_count=1`、`run_map_authority_count=1`、
  `transaction_starter_count=53`，target PASS。
- parity census：`141/141`、unmapped `0`。
- R4.5/当前 Companion construction LOC gate：`20 passed`；当前 bounded envelope
  raw/adjusted/core/Kernel 上限为 `105000/104500/34800/1300`。
- execution build manifest：重新生成后 `--check` PASS。
- 两次聚合 pytest 在 Windows 连接线程积压时超过有界等待；均仅按记录的 pytest
  command line、PID、parent、create-time 精确终止，分别释放
  `952991744` 与 `915075072` bytes private memory，survivor=0。后续按文件分批复跑定位并
  修复旧 schema fixture/insert/fault hook/写通道关闭问题。

## 仍保持失败关闭的边界

- 生产尚未注入 native managed Job helper 或 managed MCP session adapter；
  缺失 adapter 时不会启动进程。
- owner executable projection 的最终 commit/Manager receipt 属于 Task 9 activation saga；
  在该 port 接通前不能把 runtime set 记为可路由激活。
- Personal Workflow 完整 interpreter 与 effect/checkpoint recovery 属于 Task 10。
