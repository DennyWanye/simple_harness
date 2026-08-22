# SDK Observability 架构基线

校准日期：2026-08-23  
代码锚点：Harness `8b5c665`；Memory `aaa49db`；Host `e08a397`

## 主要矛盾

三个仓库已有若干安全日志、durable 状态和 correlation identity，但没有一个由 SDK 自己控制、默认拒绝正文、可注入且故障隔离的统一观测运行时。继续增加 `logging`/`structlog` 调用会形成第三套非契约数据，仍无法可靠重建故障，也无法保证 Host processor、exception repr 和 diagnostic bundle 不泄漏正文。

## 当前生产链路

```text
Host lifespan
  -> MemoryManager.build_production()                 [Memory authority]
  -> Harness build_production_runtime(memory=manager)
  -> Context preparation -> manager.recall_for_turn()
       -> recall snapshot retained/replayed/released [Memory DB authority]
  -> Harness Run/provider/tool/context stages        [execution DB authority]
  -> Harness memory outbox claim/settle/retry
       -> manager.record_committed_turn()
       -> receipt + optional fact job                [Memory DB authority]
  -> restart recovery: expired claims/jobs requeued
  -> Host JSON log -> recent logs copied into bundle
```

Observability 必须位于这些 authority 的提交结果之后，不能参与 CAS、事务成功、授权或重试决策。

## Harness 当前事实

- `contracts.events.EventEnvelope` 已有 typed event/correlation/sequence/schema，但 payload 是任意 JSON，且 runtime 并不以它作为统一发射协议（`src/simple_harness/contracts/events.py:18-98`）。
- `CorrelationIds` 只有 execution session/run/request/call/effect，缺少跨 SDK trace/root/parent/operation/attempt 语义（`contracts/identity.py:69-108`）。
- `ProductionRuntimeConfig` 与 `RuntimePorts` 是公共/内部注入缝，但没有 sink 或 diagnostics registry（`runtime/production.py:64-105`；`runtime/kernel.py:515-569`）。
- Memory outbox 已有 `pending/retry_wait/claimed/applied/dead_letter`、attempt、lease 和稳定错误码（`execution/memory_outbox.py:32-86,121-260`）。
- Context staging 已有 `new/preparing/staged/consumed/abandoned`、lease takeover、degraded outcome 和 error code（`execution/context_staging.py:27-58,132-419`）。
- Recovery 有稳定 blocker/resolution DTO，但仅有聚合日志，没有逐转换事件（`execution/recovery.py:15-78`；`runtime/kernel.py:1404`）。
- 现有日志测试主要检查 logger 调用和字段名，不覆盖 schema、sink、全 correlation、canary 或可重建性（`tests/unit/runtime/test_logging_observability.py:146-273`）。

## Memory 当前事实

- `MemoryManager.build/build_development/build_production` 是 canonical composition boundary，当前无 sink/correlation 参数（`core/manager.py:62-165`）。
- Recall 链包含 retained snapshot replay、embedding→lexical degraded、release/cleanup，但 manager 丢弃 replay 标志且无完整事件（`core/manager.py:199-271`；`backends/sqlite.py:922-1198`）。
- committed turn receipt 与 fact job 已有 replay/rejected-erased/pending/claimed/applied/retry/dead-letter/lost-lease/recovery 状态（`backends/sqlite.py:1218-1518`）。Memory SDK 没有另一套 outbox；fact job 是本地异步工作队列。
- 当前使用 ambient `structlog`，没有 SDK 控制的末端 redactor、sink 故障隔离、correlation ContextVar 或 snapshot（`core/manager.py:124,259,317,643`；`core/fact_jobs.py:63-79`）。
- 架构文档虽禁止正文/token/path/exception repr，但约束靠调用点自觉，测试只覆盖少量 stdout canary（`ARCHITECTURE/ARCHITECTURE.md:117`；`tests/unit/test_logging_observability.py:41-84`）。

## Host 与 bundle 当前事实

- Host 同时配置 stdlib/structlog JSON 日志与 rotating file（`backend/main.py:54-120`）。
- 最终边界会先从 exception 提取 `str(error)`，再依赖 key/regex redaction；这不是 Memory 正文 default-deny（`backend/observability/log_redaction.py:15-69`）。
- MemoryManager 在 lifespan 构建，Harness runtime 在统一 composition 入口构建，适合注入同一个 composite sink（`backend/main.py:2915-2947,7300-7445`）。
- diagnostic bundle 直接复制最近三份日志与 metrics，没有 SDK snapshot/export API，且 bundle 本身无总量门（`tauri-app/src-tauri/src/diagnostics.rs:88-115,180-220,257`）。

## 目标边界

1. 协议实现首先落在 Harness SDK 的独立 `simple_harness.observability` 公共包；Memory SDK 通过可选兼容依赖/Protocol 适配同一 wire schema，基础 import 不强制导入 Harness runtime。
2. SDK-owned `SafeEmitter` 在进入任何 Host sink 前执行字段 allowlist、类型/长度限制、错误码归一和 fault isolation。
3. 状态事件只在 authoritative transition 已成功后发射；开始/尝试事件不得宣称事务结果。
4. correlation 通过显式 DTO + ContextVar 传播；需要跨重启的 opaque identifiers 只作为非授权诊断元数据持久化。
5. `diagnostics_snapshot()` 只查 aggregate/count/age/code，不读取 payload/content 列。
6. JSONL、ring 和 Python logging 都是同一安全 envelope 的 sink；Host diagnostic bundle 只消费 SDK export，而不是重新解析任意业务日志。

## 技术债体检

- 现有 `EventEnvelope` 与 ad-hoc logging 是双轨；本轮应收敛为“协议事件为 canonical observability，logging 为 adapter sink”。
- 两 SDK 独立发布，不能复制协议实现后长期漂移；需要 conformance fixture 和 exact-wheel cross-package test。
- Host bundle 当前按文件复制且缺总量限制；接入 SDK 事件时必须显式限定条数、字节和文件数。
