# S1 — Harness Agent Memory 契约与自动 Recall

<!-- slice-status: draft -->

## Release unit

- MUST AC：AC-1、AC-2、AC-7（3/8）
- Tasks：8/10
- 高风险系统：public contract、Context staging、Runtime lifecycle（3/3）
- 依赖：无；以 Harness HEAD `869c76f...` 为起点

## 目标行为

- 消费者把 `memory=MemoryManager` 和 trusted `AgentIdentity` 交给 builder；不再显式 prepare recall。
- 每个新 Turn 一次 recall；retry/recovery 使用冻结 stage；失败形成 durable empty stage并继续主 Run。
- `memory=None` 保持原行为；默认 borrowed；runtime-owned 只 close 一次。

## 文件影响

| 文件 | 修改 |
|---|---|
| `src/simple_harness/runtime/agent_memory.py`（新） | canonical Protocol、identity/scope/turn/recall/write-fence DTO、error/ownership enum、hash factory |
| `src/simple_harness/runtime/conversation_context_provider.py`（新） | 正式non-Memory Context provider contract、request/result bounds、默认current-message实现 |
| `src/simple_harness/execution/sqlite/migrations/0004_fresh.sql`（新） | 最终v4 identity/stage/release/turn-outbox DDL；本slice先启用identity/stage/release部分 |
| `src/simple_harness/runtime/conversation_memory.py` | Conversation input 改持有 AgentIdentity；保留 Message canonicalization；删除逐消息 Memory DTO authority |
| `src/simple_harness/runtime/conversation_context.py` | 把 sdk-prepared recall 收敛成 internal orchestrator；empty-degraded stage；合并产品 non-Memory Context |
| `src/simple_harness/runtime/kernel.py` | `RunClient.start_conversation`/continuation 自动 prepare；generic start 兼容 memory=None |
| `src/simple_harness/runtime/consumer_adapter.py` | `ConsumerRuntimePorts.memory/ownership/failure policy`；升格官方 consumer builder |
| `src/simple_harness/runtime/production.py` | strict config 改用统一 `memory`，去掉 query/sink 双口与双 close |
| `src/simple_harness/runtime/__init__.py`, `src/simple_harness/__init__.py` | 两层一致导出正式 surface |
| `tests/unit/contracts/public-api.json` | 冻结新增/退休符号和顺序 |

## Tasks

### S1-T1 — 冻结 Agent Memory v1 canonical domain [AC-1, AC-4]

- 在 `agent_memory.py` 实现 frozen/slotted DTO；每个 identity 字段拒绝 blank/NUL，scope 只能由 enum/factory
  创建；canonical JSON protocol string带 `v1`，hash 覆盖 identity、scope、bounds 和 payload。
- `AgentMemoryPort` 只含 `recall_for_turn(request)`、`release_recall(request)`、
  `record_committed_turn(request)`；close 不放进 port，ownership 通过 builder 的可调用 close capability 管理。
- recall result/release request包含opaque write fence、query id/result hash；fence进入committed turn hash但不由
  consumer解析。
- 验证：hash golden、字段 mutation、malformed scope、mypy structural implementation。

### S1-T2 — 改 Conversation identity 与 snapshot [AC-1, AC-2]

- `ConversationTurnInput` 用 `identity: AgentIdentity` 替换顶层 user/session；continuation 从不可变 root binding
  读取 identity。`StartSnapshot`/context input hash/private snapshot包含完整 identity 和 recall scopes。
- `ConversationContinuationInput`增加可选`context_source_snapshot_ref`并进入canonical JSON/hash；
  `signal_conversation`禁止复用root ref，只把continuation自己的ref交给prepare。未提供时SDK按当前message
  生成content-addressed fallback，保持最小消费者易用性。
- session 首次 start 持久化 binding；后续 root/continuation 任一字段变化 stable conflict。
- 创建完整self-contained fresh v4 DDL并切loader只接受v4；DDL一次性包含S2将启用的final turn-outbox列，避免
  两个slice各造schema。S1只写identity/context/release表，turn-outbox保持空。
- 验证：roundtrip、rebind、replay same/different identity。

### S1-T3 — 冻结正式 non-Memory Context provider [AC-1, AC-2]

- 定义public `ConversationContextProviderPort.prepare_once(request)`，只返回 persona/history/skills/
  attachments/project/current message，不接收或返回 Memory lineage。request固定preparation id、identity、
  root/continuation、immutable source snapshot ref、item/byte/deadline bounds；result固定canonical bytes/hash。
- 产品ingress在调用SDK前创建content-addressed immutable source snapshot；provider只读snapshot、不得写产品DB，
  且同key/ref幂等。SDK先持久preparation claim/source ref再调用provider；provider返回后、stage写前崩溃按同ref
  重算相同bytes。ingress创建snapshot时同事务创建`PENDING` binding/lease，claim后标`CLAIMED`；snapshot TTL
  长于stage恢复horizon。PENDING超过orphan horizon后仅在execution claim-inspector确认无claim/ref时回收，
  inspector不可用则保留；共享content hash按binding refcount删除。
- SDK提供`CurrentMessageContextProvider`默认实现；stage分别持久product result和Memory result，再合并唯一
  snapshot；恢复不重读已持久partition。
- 改 `prepare_consumer_conversation_context` 为 deprecated/internal compatibility wrapper；正式 builder 不要求
  消费者调用它。
- 验证：产品partition bounds/hash/idempotency/crash replay；伪造Memory partition、SYSTEM Memory或遗漏
  current message被拒绝。

### S1-T4 — Runtime 自动 durable recall [AC-2]

- `RunClient.start_conversation` 在 start transaction 前 claim stage；owner 才调用
  `memory.recall_for_turn`；stage winner/recovery直接复用。
- `RunClient.signal_conversation` 改为async（或新增唯一async正式入口并退休旧sync签名），continuation使用
  同一helper和独立deterministic query id；Provider/tool retry不进入recall函数。
- continuation preparation claim在调用provider前持久化独立source ref；同continuation id不同ref/payload
  stable conflict，claim后/provider前及provider后/stage前crash都按claim ref重放，绝不回读root snapshot。
- result 继续按 USER/untrusted private partition编码并受 item/byte/deadline hard bounds。
- successful/empty stage commit 同事务记录`release_pending`；release scanner用lease/backoff幂等调用
  `release_recall`，startup恢复未释放项，Memory TTL cleanup只兜底orphan。
- 验证：call counter=1、lease takeover、crash after result/before stage、restart result hash一致。

### S1-T5 — Durable empty-degraded 语义 [AC-2, AC-7]

- timeout/transient/corruption/invalid result 不向主 Run传播；best-effort release后写
  `degraded_empty + stable error_code + zero counts` stage，且不把 exception text/path/content写入 DB/log。
- SDK在recall查询/embedding前要求Memory先读取write fence，因后续查询故障形成的empty stage仍携带fence。
  只有Memory DB整体不可达时允许`write_fence=None`，并携带SDK可信生成的canonical `turn_started_at`；恢复
  record时若时间严格晚于最新`erased_at`，Memory在事务中绑定当前epoch后写入，早于/相等或时钟回退/边界
  不明确则`REJECTED_ERASED`，从而允许删除后的真实新Turn而不复活删除前outbox。
- trusted identity invalid/rebind仍在 recall前 fail closed；execution/memory同 resolved path 在 builder拒绝。
- 验证：每类 fault 的 Run completed、Provider看到零 Memory、同 Turn恢复不再 recall。

### S1-T6 — Builder 与 ownership [AC-1, AC-7]

- 两个 builder接受统一 memory；`ResourceOwnership.BORROWED` 不注册 close，`RUNTIME` 只注册一次 identity-
  deduplicated async close hook。`memory=None` 不建 dispatcher/staging authority。
- consumer builder docstring去掉 demo/basic，补真实 Memory语义；新增`ConsumerRuntimePolicies`，其
  `local_default()`明确unpriced local/no external delivery/unknown side effect fail-closed，高级消费者可
  显式提供pricing/reconciliation，而非隐藏zero/`STILL_UNKNOWN` stub。
- 验证：borrowed 0 close、owned 1 close、build failure cleanup、重复 close、memory=None byte snapshot。

### S1-T7 — Public API 与 compatibility [AC-1]

- 更新两层 `__all__` 和 snapshot；旧 `ConversationMemoryQueryPort/SinkPort`、reserved `MemoryQueryPort/
  MemoryWritePort` 从 public surface退休，release note给出迁移表。
- 旧 generic `RunClient.start` 在 Memory disabled时保留；Memory enabled若绕过 `start_conversation` 则明确
  `conversation_entrypoint_required`，不隐式猜 identity。
- 验证：public-api contract、import fixture、negative legacy import契约。

### S1-T8 — Slice gate 与事实源 [AC-1, AC-2, AC-7]

- targeted：conversation DTO/context/production/consumer/public API/mypy/ruff。
- fault lane：timeout、hang、oversize、malicious role、crash/recovery、ownership。
- 更新 Harness `ARCHITECTURE/ARCHITECTURE.md`、index、README/Quickstart；gate finalize exit 0。

## Required scenarios

| ID | 必须证明 |
|---|---|
| S1-C1 | `ConsumerRuntimePorts(memory=structural implementation)` 可 build/start/close |
| S1-C2 | memory=None与旧无 Memory start行为一致 |
| S1-C3 | root/continuation各 recall一次，recovery不二次 recall |
| S1-C4 | timeout/transient/corrupt/malicious result均为空降级，主 Run继续 |
| S1-C5 | identity rebind和同路径在 Memory调用前被拒绝 |
| S1-C6 | borrowed/owned close计数分别0/1 |
| S1-C7 | product partition与Memory partition crash后都复用冻结hash，release最终收敛且retention有界 |
| S1-C8 | 删除后新Turn在recall outage下最终可记录；删除前旧outbox被拒，时钟边界/回退fail closed |
| S1-C9 | ingress写snapshot后、SDK claim前崩溃的orphan有界回收；活跃/共享ref与inspector故障不误删 |
| S1-C10 | root与连续continuation使用不同ref；重启复用各自hash，同continuation换ref冲突，fallback只含当前消息 |
