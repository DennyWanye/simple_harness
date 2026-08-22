# Program Plan：simple-harness-sdk × simple-harness-memory-sdk 官方一等集成

<!-- plan-status: finalized -->
<!-- last-updated: 2026-08-22 -->

## 主要矛盾

决定成败的不是 API 名字是否更短，而是 **谁拥有一个 Turn 的 Memory 生命周期**。如果 recall、Context
注入、terminal write、outbox、重试、恢复继续分散在产品和两个 SDK 之间，即便表面改成
`memory=...`，用户仍然需要维护最难的逻辑。目标结构是：消费者只提供可信 identity 和一个
`MemoryManager`；Harness 独占编排，Memory 独占存储，产品不再拥有自动 Memory 生命周期。

## 范围与发布单元

本 Program 覆盖 `acceptance.md` 的 AC-1～AC-8。总改动跨三个仓库、两个 fresh schema、发布制品和真
UI，超过 release-unit limit；因此 Program 本身不作为单一 release unit，而拆成六个可独立验收 slice：

| Slice | 文件 | 高风险系统（≤3） | 覆盖 |
|---|---|---|---|
| S1 | `slices/S1-harness-memory-contract.md` | public contract、Context staging、lifecycle | AC-1, AC-2, AC-7 |
| S2 | `slices/S2-harness-committed-turn.md` | terminal transaction、execution schema、dispatcher | AC-3, AC-7 |
| S3 | `slices/S3-memory-identity-turn.md` | identity/scope、Memory schema、privacy lifecycle | AC-3, AC-4, AC-7 |
| S4 | `slices/S4-memory-retrieval-operations.md` | retrieval、embedding、SQLite operations | AC-5, AC-7 |
| S5 | `slices/S5-joint-packaging-release.md` | package graph、conformance、release identity | AC-1, AC-8 |
| S6 | `slices/S6-simple-harness-cutover-e2e.md` | product composition、authority cutover、UI E2E | AC-6, AC-8 |

依赖顺序：`S1 → S2`；`S1 → S3 → S4`；`S2 + S3 + S4 → S5 → S6`。S2 与 S3 可并行，S4 可在
S3 contract 固定后与 S2 并行。任何 slice 未取得自己的 gate receipt，后继 slice 不得把它当成已完成。

## 关联事实源

- 唯一验收事实：`acceptance.md`
- assurance：`assurance-contract.json`（profile=`standard`）
- 当前实现：`architecture-baseline.md`
- Harness 当前事实：`/Users/denny/projects/simple-harness-sdk/ARCHITECTURE/`
- Memory 当前事实：`/Users/denny/projects/simple-harness-memory-sdk/ARCHITECTURE/`
- 产品当前事实：`/Users/denny/projects/simple_harness/ARCHITECTURE/`

## 目标公共契约

Harness SDK 是跨包契约 authority；Memory SDK 不再复制 Harness DTO，也不提供公开 Adapter。

```python
identity = AgentIdentity(
    deployment_id="device-installation-id",
    household_id="household-id",
    actor_id="authenticated-user-id",
    session_id="product-session-id",
)
memory = await MemoryManager.build_production(
    db_path=memory_path,
    embedder=ConfiguredEmbedder(...),
)
runtime = await build_consumer_runtime(
    ConsumerRuntimePorts(
        provider=provider,
        tool_executor=tools,
        authorization=authorization,
        database_path=execution_path,
        memory=memory,
        memory_ownership=ResourceOwnership.BORROWED,
        context_provider=CurrentMessageContextProvider(),
        policies=ConsumerRuntimePolicies.local_default(),
    )
)
await RunClient(runtime).start_conversation(
    ConversationTurnInput(identity=identity, message=user_message, memory_text=text)
)
```

正式 public surface（命名在 challenger 后冻结）：

- `AgentMemoryPort`：`recall_for_turn`、`release_recall`、`record_committed_turn`；不暴露 backend、outbox、
  retry 或 Adapter 概念。
- `AgentIdentity`：deployment/household/actor/session，所有字段由 trusted product ingress 构造并在首次
  session binding 后不可变。
- `MemoryScopeRef`：`personal(owner=actor)` 与 `family(owner=household)`；自动 Turn 默认写 personal，
  recall 默认按显式允许的 personal+family scopes。模型文本不能选择 scope。
- `MemoryRecallRequest/Result`：canonical query/hash、hard bounds、deadline、frozen lineage。
- `CommittedTurn/CommittedTurnReceipt`：一个 turn_id、可信 identity、user/assistant canonical text、
  write scope、payload hash；同 key 同 payload返回 already-applied，不同 payload稳定 conflict。
- `ResourceOwnership.BORROWED|RUNTIME`：默认 borrowed；只有显式 runtime-owned 才注册一个 close hook。
- `MemoryFailurePolicy`：默认 `DEGRADE_RECALL_AND_RETRY_RECORD`，不提供把 Memory outage 变成主任务失败的
  隐式模式。
- Memory SDK-only 显式分享接口冻结为
  `await MemoryManager.share_fact(principal: MemoryPrincipal, fact_id: int) -> str`。`principal`必须由消费者
  可信身份层构造；source fact必须属于同deployment/household下该actor的personal scope，否则稳定抛
  `MemoryOwnershipConflict`。成功时返回content-addressed family projection id，重复同调用返回同id且不重复
  建projection；projection保留`projection_of` provenance。`forget_fact`源事实时须级联删除family projection并
  保留防重建tombstone。该方法不进入`AgentMemoryPort`自动生命周期，也不授权模型自行选择scope。
- `ConversationContextProviderPort`：正式可选产品扩展点。最小消费者使用 SDK 的
  `CurrentMessageContextProvider`；复杂产品实现 `prepare_once(ConversationContextRequest)`。request 含
  deterministic preparation id、trusted identity、root/continuation id、immutable source snapshot ref、
  message/item/byte/deadline bounds；result 含 canonical payload/hash。它只提供 non-Memory Context，必须
  read-only/side-effect-free 且同 key 同 source ref 幂等。产品 ingress 在调用 SDK **之前**独占生成
  content-addressed immutable source snapshot；provider只读该snapshot，不写state.db。产品同时原子创建
  `PENDING` preparation binding/lease；SDK claim成功后产品将其标`CLAIMED`。cleanup对`PENDING`超过orphan
  horizon的记录，必须经execution只读claim-inspector确认无claim/ref后才删；inspector不可用则保留并重试。
- `ConsumerRuntimePolicies`：把 pricing/reconciliation/delivery 选择冻结成可编译配置；
  `local_default()` 明确表示 unpriced local、无外部 delivery、unknown side effect fail-closed，不再使用
  “零价格 + STILL_UNKNOWN”隐式 demo stub。高级产品仍可逐口覆盖。

`ConsumerRuntimePorts.memory` 和 `ProductionRuntimeConfig.memory` 使用同一契约与语义。前者从
`simple_harness` 顶层和 `simple_harness.runtime` 同时导出并成为正式 consumer composition；后者保留
advanced composition。`memory=None` 不创建 stage/outbox/pump，保持现有无 Memory bytes 和行为。

## Turn 状态与失败语义

```text
new trusted Turn
  -> durable context claim
  -> freeze product non-Memory context once (immutable source snapshot ref)
  -> memory recall once
       transient/timeout/corruption -> durable EMPTY-DEGRADED stage + safe event
       invalid trusted identity     -> reject before Memory call
       invalid/malicious result     -> discard + best-effort release + EMPTY-DEGRADED
  -> freeze USER/untrusted Memory with product-prepared non-Memory Context
  -> provider/tool/recovery reuse same stage hash
  -> COMPLETED terminal transaction
       execution terminal + one committed-turn outbox intent
  -> response remains committed
  -> dispatcher retry
  -> Memory transaction
       turn receipt + user row + assistant row + fact-job intent
  -> fact worker idempotently extracts/updates Facts
```

- `FAILED`、`CANCELLED`、parse failure、invalidated attempt 和 tentative Tool result 不创建 committed-turn
  outbox。旧的 start/enqueue user intent 路径删除。
- Memory record 永远不能回滚 successful terminal。permanent conflict 进入 dead-letter 并发 safe event；
  transient/timeout 退避重试。
- recall failure 的 durable empty stage 也有 canonical hash；恢复读取它，不在同一 Turn 二次探测 live
  Memory。下一个新 Turn 才重新尝试。
- SDK先把preparation id/source ref写入execution claim，再调用read-only provider；context stage分别保存
  product partition与Memory partition的result id/hash/outcome，再合并唯一Provider snapshot。provider后、
  stage前崩溃只会用同source ref重算相同bytes。simple_harness source snapshot采用content hash key和bounded
  TTL；TTL大于stage lease/recovery horizon。正常清理只删已过期且SDK stage已STAGED/CONSUMED的snapshot；
  ingress→claim崩溃形成的PENDING记录，仅在超过orphan horizon且claim-inspector证明无execution引用时回收，
  inspection失败fail-safe保留。content hash共享snapshot用binding refcount，不能被单个orphan误删。
- `ConversationContinuationInput`提供可选`context_source_snapshot_ref`并纳入canonical JSON/hash；
  `signal_conversation`只使用continuation自己的ref，禁止继承root ref。显式ref在provider前随continuation
  preparation claim持久化；同continuation id不同ref稳定conflict。未提供ref时SDK只为最小消费者按当前
  continuation message生成content-addressed fallback，仍不读取root snapshot。provider/stage前后crash均按
  continuation claim里的同ref重放。
- Memory recall result 带 opaque `write_fence`（当前 erasure epoch）。stage commit 同事务写
  `release_pending`；release worker按 query/result hash幂等重试，startup扫描未释放stage，Memory另有
  bounded TTL orphan cleanup。stage commit 后、release 前崩溃不会泄漏 retained result。
- request 另含SDK可信生成的`turn_started_at`。Memory在recall查询前先读取当前write fence，所以embedding/
  ranking timeout仍可返回fence + empty recall；只有DB整体不可达时fence缺失。record恢复后若fence缺失，
  Memory在同事务比较turn_started_at与最新erased_at：严格晚于删除才绑定当前epoch并写入，早于/相等则
  `REJECTED_ERASED`。时间来自受信OS且进canonical hash；边界/时钟倒退fail closed并保留retry诊断。
- identity mismatch、session rebind 和 execution/memory 同路径属于调用方契约错误，启动前 fail closed；
  它们不是可降级的 Memory outage。

## 持久化决策

### Harness execution v4

- 新增 self-contained `0004_fresh.sql`，正常loader只接受v4；另提供仅在runtime关闭时显式调用、backup-first
  的`execution_v3_to_v4` migrator，正常open绝不隐式迁移。
- conversation/session binding 持久化完整 `AgentIdentity`；hash、snapshot、continuation 和 replay 都纳入
  identity + scopes。
- `memory_outbox` 改为 turn envelope：`turn_id` 唯一、canonical payload、payload_hash、state、claim lease、
  attempt/backoff/dead-letter。terminal transaction 是唯一写入口。
- context stage 增加 `ready|degraded_empty` outcome 和 stable error code，但 private bytes 不保存异常、路径、
  token 或原始 Memory 内容以外的敏感诊断。

### Memory v4

- 新 fresh schema v4；SDK正常 open 不隐式迁移 0.3。旧库稳定报 `unsupported_schema`，不得静默
  delete/rebuild；另提供显式、离线、先备份且要求目标 AgentIdentity 的 v3→v4 migrator，仅供
  simple_harness cutover 调用。
- composite identity：deployment + household + actor + session；scope owner 独立列，所有 lookup/delete/export
  都从同一 principal predicate builder 生成，避免漏一个 filter。
- `committed_turn_receipts` 唯一约束覆盖 deployment/turn_id；receipt、两条 messages 与 fact job 在同一
  `BEGIN IMMEDIATE` transaction。
- principal/scope 维护 monotonic erasure epoch。recall在embedding/ranking前先读取当前 `write_fence`，
  committed turn携带它；
  delete先递增epoch并删除内容，但保留无内容的 erasure tombstone、turn payload-hash receipt和cancelled
  job tombstone至少覆盖execution outbox retention。旧fence事件返回`REJECTED_ERASED`幂等receipt；仅DB整体
  outage造成的缺失fence，按trusted `turn_started_at`严格晚于`erased_at`才绑定当前epoch，边界/回退fail
  closed。forget同样写fact/provenance tombstone，late fact job提交前复核fence。
- FTS5 external-content table/trigger（或同等 transactional sync）提供 lexical candidate；向量查询也必须
  先使用 identity/scope + SQL LIMIT 的候选，禁止把全部 vectors 拉进 Python。
- `embedding_index_lineage` 固定 provider/model/revision/dimension/normalization/format。lineage 不一致时
  recall 明确降级 lexical 或报 drift，不静默混用；reindex 先建新 generation、验证完整，再原子切换。
- export 产物显式 version + identity/scope；delete/forget 在事务内清除 messages/facts/vectors/recall
  snapshots和job payload，但保留最小防重放tombstone/receipt，并返回 count receipt。
- backup 使用 SQLite online backup snapshot，恢复到临时文件先做 schema/integrity/FK/lineage 校验，再由
  调用方在 manager closed 状态原子替换；禁止直接复制活跃 WAL 主文件。

## SQLite 并发边界

- 本 release 支持同一 `MemoryManager` 内多协程；一个 manager 一个 owner connection，写与 checkpoint
  走统一 async lock。不同 manager 不共享 connection。
- 由于目标 Python 当前携带 SQLite 3.50.4（官方 WAL-reset bug 受影响版本），本 release 不承诺多个进程
  同时写同一个 Memory DB。initialize 输出 version capability；若检测第二 active writer owner，则以
  stable `memory_multi_writer_unsupported` fail fast，不靠“应该没事”。
- 后续要为 K6 增加同库多进程写入，必须先固定已修复 SQLite runtime（≥3.50.7 backport 或 ≥3.51.3）
  或验证跨进程 writer/checkpoint lock；该扩展不在本 Program。

## 产品 Context 合并边界

SDK 自动 recall 不等于 SDK重建所有产品 Context。目标使用两部分：

1. simple_harness 的 persona、history、skills、attachments、project/task snapshot 仍由正式
   `ConversationContextProviderPort` 生成“非 Memory Context”；进入 SDK 前先用 preparation id + source
   revision冻结在state.db，确保崩溃重放不读取变化后的history/persona；
2. SDK 在 durable claim 内调用 Memory 并把冻结 Memory partition 合并进去；最终 private snapshot 与
   Provider messages 仍由 SDK验证 trust、lineage、current message 和 bounds。

这样删除的是产品手动 recall/Memory lineage 拼装，而不是删除产品已有 Context 能力。foreground 的
`memory_recall/memory_search` 从普通自动 Tool catalog 移除；若诊断 UI 需要查看，只能读当前 frozen
stage 的 public redaction。simple_harness 现有显式 `remember/write` 与 `forget` mutation tool 保留，走同一
manager 和 trusted run identity，并使用 `explicit-memory-action/v1/...` provenance，不进入自动 Turn outbox。
本轮不新增 simple_harness `memory_share` Tool/schema/权限 UI；Memory SDK 的正式 authorized share/projection
API 保持公开、受测且接口就绪，供未来 AIPhone、K6/AgentOS 与 NovelTagSystem 消费者使用。

simple_harness 已于 2026-08-09 退休 hosted relay/account identity，因此产品可信 actor 不再来自 `/v1/me`：
`deployment_id=state_db_identity.instance_id`，`actor_id`仅取 `LocalAuthSnapshotProvider` 校验后的
`HumanIdentity.identity_namespace_hash`。它是按本地 profile namespace 派生的稳定非原始标识；Provider/API key
变化不改变它，不同 user-data 目录彼此隔离，损坏或畸形 identity 在 LLM 前 fail closed。历史常量
`legacy_local_profile` 不得作为 Agent Memory actor。

## 依赖方向与安装

- `simple-harness-sdk` 不依赖 Memory SDK。
- `simple-harness-memory-sdk` 基础安装仍可 standalone import；新增 `[harness]` optional extra，声明
  `simple-harness-sdk>=0.3,<0.4`。
- `MemoryManager` 方法内 lazy import Harness canonical DTO/error，顶层 import 不触发 Harness；联合类型
  检查用专门 mypy fixture。运行时 Protocol 只做属性握手，不能替代签名测试。
- simple_harness 使用本轮 authoritative exact wheels + SHA，不从 editable/path import 偷跑。

## 最佳实践调研与本项目适配

- Python `Protocol` 适合结构子类型，但 runtime-checkable 只检查属性存在、不检查签名；本项目采用
  static mypy + canonical DTO/hash + joint wheel fixture 三重验证，不把 `isinstance` 当充分证据。
- PyPA extras 用于只在选中功能时合并额外依赖；因此 Memory→Harness 使用 `[harness]` extra，保持
  Memory standalone 安装，不制造 Harness→Memory 循环依赖。
- SQLite WAL 允许读写并行但仍只有一个 writer，且跨多个 attached DB 不提供整体原子性；本项目明确
  使用 execution outbox，而不是假装两个 DB 能同事务提交。
- SQLite online backup 提供一致 snapshot；本项目据此实现 backup/restore，不复制活跃 `.db/-wal/-shm`。
- FTS5 是 SQLite 原生倒排候选能力；本项目只把它用作 identity-filtered lexical candidate source，
  保留现有 RRF/向量重排，并为每层设置 LIMIT/bytes/deadline。

外部事实源：

- Python typing Protocol：<https://docs.python.org/3/library/typing.html#typing.Protocol>
- PyPA dependency extras：<https://packaging.python.org/en/latest/specifications/dependency-specifiers/#extras>
- SQLite WAL/concurrency：<https://sqlite.org/wal.html>
- SQLite Backup API：<https://sqlite.org/backup.html>
- SQLite FTS5：<https://sqlite.org/fts5.html>

## Attack-surface inventory

| 入口 | 不可信输入 | 保护措施 | 失败出口 |
|---|---|---|---|
| product identity → Turn | 普通 payload/模型伪造 identity | typed trusted construction + immutable binding | reject before recall |
| Memory recall result | prompt injection、超界 payload、错 lineage | canonical DTO/hash/bounds + USER/untrusted | discard, empty degraded stage |
| committed-turn delivery | replay、同 key 异 payload | execution hash + Memory unique receipt | already-applied / conflict dead-letter |
| scope query/export/delete | 跨家庭 ID、越权 owner | shared principal predicate + matrix tests | zero result / authorization error |
| embedding | 维度/模型漂移、运行时下载 | full lineage + local-only production builder | fail-fast / lexical degrade |
| logs/diagnostics | content/token/vector/path 泄露 | allowlisted fields + sensitive scan | test failure |
| package consumption | editable/path/旧 wheel | exact version/origin/SHA verifier | startup/build gate failure |

## Program 任务与验收映射

| Task | 执行 slice | 主要 AC | 关键证据 |
|---|---|---|---|
| P-1 冻结 Agent Memory v1 contract 与行为表 | S1 | AC-1,2,4 | API snapshot、mypy、contract tests |
| P-2 SDK 自动 recall/empty-degrade/ownership | S1 | AC-1,2,7 | counter、stage/recovery、close tests |
| P-3 committed-turn execution outbox v4 | S2 | AC-3,7 | terminal atomicity、crash windows |
| P-4 Memory identity/scope/turn receipt v4 | S3 | AC-3,4,7 | household matrix、conflict、privacy |
| P-5 FTS/embedding/backup/restore | S4 | AC-5,7 | scale/query plan、drift、corruption |
| P-6 joint wheels/conformance/release candidate | S5 | AC-1,8 | clean 3.11–3.13 exact-wheel matrix |
| P-7 simple_harness authority cutover | S6 | AC-6,8 | static authority graph + product regression |
| P-8 windows-mcp true UI E2E | S6 | AC-6,7 | SH-M1～M5 + AC-6故障条款派生SH-M6 primary evidence |
| P-9 release promotion与事实源回写 | S5/S6 | AC-8 | version/tag/hash/docs/ARCHITECTURE |

## 验证策略

### Cheap → expensive gate 顺序

1. 每 repo targeted unit + type/lint；schema/contract/public API tests。
2. 每 repo affected suite；Harness/Memory fresh/history/fault lanes。
3. build authoritative wheels；clean venv Python 3.11/3.12/3.13 joint fixture。
4. simple_harness backend affected/critical/full-surface shards、frontend type/vitest、Rust checks。
5. exact candidate wheels 接入 simple_harness，全新 user data，Tauri source backend 启动 smoke。
6. 冻结 testcase + gate init 后执行 windows-mcp SH-M1～SH-M5，并增加直接落实已批准 AC-6/AC-7
   故障条款的 `SH-M6`（recall timeout + record transient/restart）；每 case 都是 Snapshot/Screenshot →
   声明坐标/动作/期望 → 真点击/输入 → 截图 → Tauri/backend 日志与安全 receipt 判定。
7. full audit、ARCHITECTURE/PROJECT_STATUS 回写、re-attest、受影响场景重测、最终 finalize。

### 证据位置

- raw screenshots/logs/DB/recording：
  `/Users/denny/projects/simple_harness/.local-test-evidence/2026-08-22/<run>/`
- Git 仅保存 scenario/run ID、命令、结论、相对索引、SHA-256；不保存 token、密码、cookie、Memory 原文
  长日志或截图。

## 发布顺序

1. 构建 Harness 0.3.0 candidate（execution v4 + Agent Memory v1），保留原 bytes。
2. Memory 0.4.0 使用该 exact candidate 做 `[harness]` joint conformance，构建 candidate。
3. simple_harness exact pin 两个 candidate，完成自动化 + windows-mcp；任何行为改动使 candidate 作废并
   回到对应 slice 重建。
4. 所有 receipts 通过后，按两个 repo 现有 manual release workflow 上传**同一 bytes**，创建/核对 tag、
   BUILD_INFO、SHA256SUMS、changelog；不由 tag 触发第二次 build。
5. 将 simple_harness pin 切为已发布 exact versions/hashes，重新做 installed-origin smoke 和受影响 UI
   场景，随后更新三仓架构事实源。

simple_harness cutover 由产品级coordinator做可恢复双库升级；两个SDK互不调用。产品先冻结trusted
`LegacyIdentityMap`（每个v3 user/session必须唯一映射deployment/household/actor/session，缺失/歧义即停止），
Harness显式`execution_v3_to_v4` migrator只迁execution DB并输出versioned/hash-protected
`ExecutionMigrationManifest`；Memory显式migrator接收该公开中立manifest与identity map，在自己的临时v4中迁
Memory v3数据并通过公开migrations API导入eligible legacy message，绝不把私有方法放入AgentMemoryPort。
属于非终态run、仍可能与未来terminal配对的最新user entry标`DEFERRED_TURN`；同run更早、未进入最终pair的
root/continuation user entry标`SUPPRESS_TENTATIVE`。该run迁移后完成时由Harness用deferred immutable user
input+新assistant生成一个正常v4 committed turn，失败/取消则零turn，避免重复或半pair。manifest按每个
Harness `source_event_id`标`KEEP_COMPLETED_PAIR|SUPPRESS_TENTATIVE|SUPPRESS_TERMINAL|DEFERRED_TURN`：
completed run只有因果绑定最终terminal的最后user+assistant为KEEP；同run其余tentative user为
SUPPRESS_TENTATIVE；failed/cancelled为SUPPRESS_TERMINAL；非终态最多一个最新user为DEFERRED、其余tentative
仍SUPPRESS_TENTATIVE。Memory迁移v3已apply数据时也按此分类：三个非KEEP类不复制message、inline embedding及
`source_msg_id`级联facts并写hash-only suppression receipt，completed从v3 row或已校验manifest payload补齐
完整pair。recall snapshots不迁移，digital twin等聚合只由保留facts重建。产品另冻结non-Harness provenance
manifest；两份manifest必须完整覆盖每个v3 message `source_event_id`，未知/重复/内容hash不符一律停止。
- v3 assistant `continuation_id`为空时，Harness migrator不得按时间猜测：解析并验证v3冻结的terminal event/
  receipt identity（run id、terminal state、continuation id、claim epoch），再与continuation row、唯一progress
  receipt和run event durable sequence交叉核对；零个或多个候选均fail closed。root terminal只有在无任何terminal
  continuation候选时成立。
- 非终态迁入v4时创建versioned `legacy_turn_cursor`，保存当前DEFERRED source/input hash。迁移后若收到新
  continuation，enqueue UOW同事务把旧cursor写成`SUPPRESS_TENTATIVE` hash-only disposition并CAS替换为新
  active input；Memory迁移时已用独立`legacy-source:<event_id>` namespace写过无内容suppression receipt，
  所以无需跨库更新。terminal UOW只消费当时active cursor/continuation并以`turn:<turn_id>` namespace产生一个
  v4 pair；再来多个continuation则逐次supersede。crash恢复从cursor version重放，不能配旧user或产生双pair。
coordinator用本地upgrade journal记录两个temp hash、backup、swap phase；startup只选择校验
  通过的全旧或全新pair，检测mixed pair即从backup恢复，不把半升级库交给runtime。promotion 后若installed-origin复测失败，立即把消费者pin回上一个已验证版本、
恢复pre-cutover备份；已发布制品不改tag/bytes，按registry能力yank并发布新patch candidate，不能覆盖重传。

发布到外部 registry/tag 属不可逆外部动作；执行阶段在所有本地 candidate gates 通过后，若现有凭据或
目标 registry 无法从仓库配置唯一确定，将停在 promotion 前向用户请求一次明确确认，不自行改发布目标。

## 明确不做

- 不修改 AIPhone、K6/AgentOS、NovelTagSystem。
- 不为 K6 增加 Python 3.9 支持，不实现 PostgreSQL/pgvector backend。
- 不迁移 AIPhone/K6/NovelTag 的旧 Memory 数据；simple_harness 当前 0.3 Memory DB 做本地可恢复 v3→v4
  cutover，不实现跨设备同步。
- 不用 WebSocket 直注、backend 脚本回放、registry introspection 代替 simple_harness UI E2E。

## 完成判据

- 六个 slice 分别满足其 MUST AC、task limit、风险 closure 并取得有效 gate receipt。
- Program 汇总对 AC-1～AC-8 和 TO-01～TO-R5 无遗漏；SH-M1～SH-M5 以及由 AC-6/AC-7 故障条款
  派生的 SH-M6 均为真实 UI root PASS。
- 三仓 architecture/current status 与被测 exact wheels 一致；两个排除仓和 NovelTag 无本 Program 改动。
- 最终完成只由 deterministic `finalize` exit 0 与有效 receipt 宣布；plan 或报告中的手写 PASS 不具 authority。
