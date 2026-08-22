# 架构基线：Harness / Memory 官方一等集成

<!-- last-updated: 2026-08-22 -->

## 校准锚点

| 仓库 | 路径 | 基线 HEAD | 架构挑战 |
|---|---|---|---|
| simple_harness | `/Users/denny/projects/simple_harness` | `8d0741a7be447110b80f7df6c15c38d2788dd884` | PASS |
| simple-harness-sdk | `/Users/denny/projects/simple-harness-sdk` | `869c76f2050b5f492b4edee68f4ce2400030b832` | PASS |
| simple-harness-memory-sdk | `/Users/denny/projects/simple-harness-memory-sdk` | `87820fe2c4cdde21c3a9356ca461b93fe00aadcb` | PASS |

本文件描述的是上述提交对应的当前生产事实，不把目标能力写成已实现能力。三个挑战代理先后核对了
SDK Runtime、Memory backend/manager 和 simple_harness 真入口；所有可证伪项修正后均为 PASS。

## 主要矛盾

当前已经有可靠的 bounded recall、冻结 Context、execution outbox、重放和 Memory 幂等积木，但它们被
拆成 query port、sink port、公开 Adapter、产品侧 prepare/recall 和逐消息 intent。消费者必须理解
生命周期、重试与恢复才能正确接入。真正的问题不是“再加一个便利函数”，而是把 **Turn 的 Memory
生命周期 authority 收回 Harness SDK**，同时让 Memory SDK 直接实现一份稳定契约，并保留现有 durability。

## Harness SDK 当前事实

- 公共 conversation 类型在 `src/simple_harness/runtime/conversation_memory.py`；identity 只有
  `user_id + session_id`，没有 deployment/household/actor/owner/scope。
- 当前稳定边界在 `src/simple_harness/runtime/ports.py`：
  `ConversationMemoryQueryPort.recall_bounded/release/close` 与
  `ConversationMemorySinkPort.apply/close` 两口分裂。
- `src/simple_harness/runtime/conversation_context.py` 已提供 durable claim、single winner、bounded recall、
  canonical query/result hash、USER/untrusted private snapshot、release retention；但调用方必须在
  `RunClient.start()` 或 `signal_conversation()` 前显式调用 prepare helper。
- `src/simple_harness/runtime/kernel.py::RunClient.start` 只转发到 `_start_run`；Memory enabled 时 kernel
  要求调用方已经给出 stage。`signal_conversation` 同样要求 stage/hash/prepared context。
- 当前 root start 和 continuation enqueue 会各自生成 user intent；completed terminal 才生成 assistant
  intent。failed/cancelled Run 仍可能把 user message 写入 Memory，不满足 committed-turn 语义。
- `src/simple_harness/execution/memory_outbox.py` 已有 lease、backoff、dead-letter、apply-before-ack crash
  replay；payload/receipt 的幂等单位却是单条 role message，而不是 user+assistant Turn。
- fresh execution schema v3 在
  `src/simple_harness/execution/sqlite/migrations/0003_fresh.sql` 固化了当前 identity、staging 和逐消息
  outbox。改 committed-turn envelope 必须是明确的新 fresh schema，而非只换 DTO。
- `src/simple_harness/runtime/production.py::ProductionRuntimeConfig` 要求 query/sink，并无 borrowed
  ownership；同一对象兼任两口会被双 close。`ConsumerRuntimePorts/build_consumer_runtime` 目前只在
  `simple_harness.runtime` 导出，且仍标 demo/basic；strict production builder 两层导出。
- recall timeout/transient/contract error 当前向上传播并阻止 Run，不是空 Memory 降级。
- public surface 由 `tests/unit/contracts/public-api.json` 和 `__all__` snapshot 固定；任何正式入口变化
  必须显式更新契约与 semver。

## Memory SDK 当前事实

- `src/simple_harness_memory/core/manager.py::MemoryManager` 是 standalone facade；公开方法仍是
  append/recall/forget/session-delete 等底层语义，尚未直接实现 Harness Turn contract。
- `src/simple_harness_memory/core/conversation.py` 重复定义 Harness-like DTO、enum、hash 和公开
  `ConversationMemoryAdapter`；这是消费者要手动组合的第二套 authority。
- identity 只有 user/session；`source_event_id` 和 `context_query_id` 是全局 key；没有 household、owner、
  actor 或 scope 隔离。
- SQLite 已有 WAL、FK、busy timeout、`BEGIN IMMEDIATE`、integrity check、0600、recall result retention、
  append idempotency；但没有 committed-turn receipt 和同一事务写 user+assistant pair。
- 当前 lexical retrieval 是先按 identity/recent SQL LIMIT 拉候选，再在 Python 做 substring/vector
  ranking；没有 FTS5 candidate index，规模增大后质量和复杂度都不满足 AC-5。
- embedding lineage 只有 kind/dimension/format，缺 provider/model/revision/normalization；不匹配向量会被
  跳过，reindex 切换时机不足以证明全量完成。
- 未注入 embedder 时生产 SQLite 路径可回退 Hash embedder；BGE 构造可触发运行时下载。目标要求生产
  fail-fast、资源路径显式、运行时不联网下载。
- 当前只有 user-scoped 单条 `forget_fact` 与 session delete；没有 principal/scope export、全级联
  delete、备份/恢复 API。
- 当前 backend 以单 connection + task lock 为主；不能宣称任意多进程安全。

## simple_harness 当前真实链路

- 真 UI 入口是 Tauri/React `chat/chat_v2` → backend `control_channel` →
  `_launch_product_harness_chat` → `_run_product_harness_chat`。
- `backend/main.py` 当前构造 `SQLiteMemoryBackend + ConversationMemoryAdapter`，把 query/sink 分别传给
  strict production builder；foreground 又显式调用 `prepare_consumer_conversation_context`。
- 产品自己的 `product_memory_outbox` 不是简单重复：它服务 non-Harness/companion/background provenance；
  只能迁移 foreground Harness authority，不能整表或整模块删除。
- foreground tool catalog 仍可能暴露 `memory_recall/memory_search`，可在自动 recall 后再次读 live Memory。
  目标必须隐藏这两个自动读取入口，或让它们只读取当前冻结 stage；simple_harness 现有显式
  remember/write 与 forget 仍走同一个 MemoryManager并使用独立 provenance/idempotency。产品当前没有
  `memory_share` Tool，本轮不新增其 schema/UI/权限流；SDK authorized share/projection API 单独保持接口就绪。
- Memory 当前是 hard dependency；prepare/recall 错误会阻断 Run。普通 UI 消息通常创建新 root Run；
  crash/restart 与 retry 必须测真实运行栈，不能用协议直注替代。
- 真测必须只启动 Tauri，让它自管 backend/Vite；设置 `DESKPET_BACKEND_DIR` 指向当前源码，禁用
  `DESKPET_SDK_DESKTOP_TEST`，并用 windows-mcp 做截图、坐标点击、输入和日志判定。

## 数据与信任边界

```text
trusted product identity
  -> ConversationTurnInput(identity, user message)
  -> Harness durable recall claim/stage
  -> AgentMemoryPort.recall_for_turn(...)
  -> bounded USER/untrusted Memory projection
  -> Provider/Tool retries reuse frozen stage
  -> COMPLETED terminal + committed-turn outbox (one execution transaction)
  -> dispatcher at-least-once
  -> MemoryManager.record_committed_turn(...)
  -> Memory receipt + user/assistant rows + fact job (one memory transaction)
```

- trusted：消费者认证结果、不可变 session binding、显式本地路径。
- untrusted：用户文本、Memory 内容、Tool output、Provider/model output。
- 两个 SQLite DB 之间不做分布式事务；execution outbox + Memory unique receipt 提供最终恰好一次效果。
- Memory 失败最大影响为本 Turn 空召回或后台写延迟，不能回滚已完成响应。

## 已验证的关键假设（可丢弃 spike）

1. **结构协议 + 可选依赖可行**：Memory SDK 在自定义 import blocker 明确阻止 `simple_harness` 时，
   `import simple_harness_memory` 成功并输出 `0.3.0 MemoryManager`；一个含
   `recall_for_turn/record_committed_turn/release_recall` 的实现通过 mypy 1.20.2 对 `Protocol` 的静态赋值。
   因此可采用“Harness 拥有 canonical Protocol/DTO；Memory `[harness]` extra + 方法内 lazy import”的
   单向依赖，不需要 Harness 反向依赖 Memory，也不需要重复公开 DTO。
2. **SQLite 能力可用**：本机目标 Python 3.11.15、3.12.13、3.13.13 均以 SQLite 3.50.4 成功执行
   WAL、FTS5 MATCH 和在线 backup，结果均为 `wal/1`。
3. **有界候选与 turn 原子写形状可行**：SQLite 临时库在一个 `BEGIN IMMEDIATE` 中写 turn receipt 和
   user/assistant 两行；20,000 条 filler 下 FTS5 `LIMIT 20` 查询约 2.085 ms，query plan 使用
   `VIRTUAL TABLE INDEX`；重复 turn key 不同 hash 命中唯一约束冲突。

## spike 暴露的约束

- `@runtime_checkable Protocol` 只检查属性存在，不检查签名；运行时只能做能力/版本握手和 DTO/hash
  验证，签名兼容必须由 mypy + joint conformance fixture 证明。
- 当前目标解释器的 SQLite 3.50.4 落在 SQLite 官方 2026 WAL-reset bug 的受影响范围。首版不得宣称
  任意多进程/多连接写入安全：simple_harness 使用单 owner connection；计划必须加入 runtime version
  诊断、writer/checkpoint 串行化与故障测试，并把跨进程共享写入保持为非承诺能力，直到固定 SQLite
  runtime 或明确的跨进程锁方案通过独立验证。

## 停止追踪点

- AIPhone、K6/AgentOS、NovelTagSystem 的代码、依赖和数据库只作为接口兼容背景，不读取后修改。
- 不设计远程同步、冲突合并、PostgreSQL backend 或旧生产数据迁移。
- 任何需要扩大上述边界的 finding 只能形成 scope-change proposal，不能静默并入 required plan。
