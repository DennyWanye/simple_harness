# S3 — MemoryManager identity/scope 与 committed-turn 原子写

<!-- slice-status: draft -->

## Release unit

- MUST AC：AC-3、AC-4、AC-7（3/8）
- Tasks：9/10
- 高风险系统：identity/scope、Memory schema、privacy lifecycle（3/3）
- 依赖：S1 Agent Memory v1 contract

## 文件影响

| 文件 | 修改 |
|---|---|
| `src/simple_harness_memory/core/manager.py` | 直接实现AgentMemoryPort；principal export/delete/forget APIs |
| `src/simple_harness_memory/core/conversation.py` | 删除公开Adapter/重复Harness DTO；保留internal error translation helper |
| `src/simple_harness_memory/core/identity.py`（新） | standalone principal/scope predicate和值对象 |
| `src/simple_harness_memory/core/fact_jobs.py`（新） | durable fact worker、claim/lease/backoff、extractor lineage与幂等apply |
| `src/simple_harness_memory/core/port.py` | backend committed-turn、privacy lifecycle抽象 |
| `src/simple_harness_memory/backends/sqlite.py` | fresh v4 DDL、turn transaction、identity-filtered operations |
| `src/simple_harness_memory/migrations/v3_to_v4.py`（新） | 显式offline、backup-first、identity-bound migrator |
| `src/simple_harness_memory/backends/mock.py` | conformance等价语义 |
| `src/simple_harness_memory/__init__.py` | 退休Adapter exports，发布新standalone privacy types |
| `pyproject.toml` | `[harness]` extra与joint typing config |

## Tasks

### S3-T1 — 单向integration packaging [AC-1]

- 新增 `harness = ["simple-harness-sdk>=0.3,<0.4"]`；基础包顶层不import Harness。
- `MemoryManager` integration方法内部lazy import canonical request/result/errors；缺extra时抛短且稳定的
  `harness_integration_extra_required`，不影响standalone APIs。
- joint mypy fixture用真实两包类型证明Protocol签名，不用runtime-checkable代替。

### S3-T2 — Fresh Memory v4 identity schema [AC-4]

- DDL把deployment/household/actor/session/scope_kind/scope_owner加入sessions/messages/facts/vectors/
  recall snapshots/receipts/jobs/erasure epochs/tombstones需要的主键或索引。
- 一个 shared predicate builder生成recall/get/export/delete/forget全部WHERE；禁止各函数手写不一致filter。
- 旧schema报unsupported，不自动迁移/删除；另提供显式offline migrator，要求closed source、online backup、
  目标AgentIdentity和旧user映射，迁移到临时v4后做count/FK/integrity再原子切换，失败保留原库。
- migrator按execution manifest的每个`source_event_id`处理已apply v3 row：`KEEP_COMPLETED_PAIR`从v3 row或
  hash一致的manifest payload补齐pair；`SUPPRESS_TENTATIVE/SUPPRESS_TERMINAL/DEFERRED_TURN`不复制message、inline embedding及
  FK `source_msg_id` facts，并写hash-only suppression receipt防止late replay。recall snapshots不复制，
  digital twin/其他facts聚合只从保留facts重建；禁止把含被抑制content的派生状态带入v4。

### S3-T3 — Immutable session binding [AC-4]

- 第一次turn/recall创建完整session binding；后续deployment/household/actor任一变化抛ownership conflict。
- 普通query/model字段不能覆盖identity；scope factory校验personal owner=actor、family owner=household。

### S3-T4 — `recall_for_turn`直接实现 [AC-1, AC-4]

- manager把Harness request转换成backend bounded query，并返回canonical Harness result；保留query id/hash/result
  retention/release。
- result携带当前principal/scope erasure epoch的opaque write fence；release按query/result hash幂等，TTL cleanup
  bounded且只删已过retention horizon的orphan。
- 默认读取明确的personal(actor)+family(household)集合；每条hit携带scope/owner provenance但不暴露其他主体。

### S3-T5 — committed-turn receipt + pair transaction [AC-3]

- `record_committed_turn`先比较write fence与当前erasure epoch，再在一个`BEGIN IMMEDIATE`写receipt、user
  row、assistant row和fact job；任何一步fault rollback全部。旧fence返回`REJECTED_ERASED` hash-only
  receipt；无fence时在同事务比较trusted `turn_started_at`与最新`erased_at`，严格晚于才绑定当前epoch，
  早于/相等、缺时间或时钟边界不可信均拒绝，不写content。
- unique key + payload hash：same返回already-applied及同record id，different抛apply conflict。
- 在`simple_harness_memory.migrations`提供显式公开、但独立于runtime `AgentMemoryPort`的
  `import_execution_manifest`；Memory migrator接收Harness的中立versioned/hash-protected manifest和产品冻结
  `LegacyIdentityMap`，只导入eligible终态legacy message。以旧event id+payload hash写receipt，same replay
  幂等、different冲突，并遵守相同erasure epoch/tombstone；runtime import拒绝三个非KEEP分类和未知版本，
  offline migrator则为三个非KEEP分类写suppression receipt。
- 产品coordinator另传独立hash-protected non-Harness provenance manifest；execution+product manifest必须
  恰好覆盖v3每个message source_event_id。未知、重复归属、缺pair、v3 row/manifest hash不符均fail closed，
  临时v4不发布。
- `auto_extract_facts`从单条append侧迁移为durable fact job worker：pending/claimed/applied/dead-letter、lease、
  attempt/backoff、startup recovery、bounded close drain。worker在事务外调用带version identity的extractor，
  随后把canonical extraction snapshot、deterministic fact ids和job applied在一个事务提交；提交前复核
  erasure/fact tombstone，因而不存在“部分facts已写、job未ack”的窗口。

### S3-T6 — Personal/family隔离矩阵 [AC-4]

- 自动turn默认personal；SDK-only显式share的正式签名为
  `MemoryManager.share_fact(principal: MemoryPrincipal, fact_id: int) -> str`。principal只由可信消费者身份层
  构造；仅可把同deployment/household、同actor personal source fact投影到family scope，跨actor/household
  稳定`MemoryOwnershipConflict`。projection id按source deterministic id+household content-addressed生成，
  same replay返回同id且只有一行，保留`projection_of` provenance；source forget级联删除projection并留
  tombstone，late fact job不能重建。该API不加入Harness自动`AgentMemoryPort`，模型不能自行选择scope。
- A/userA、A/userB、A family、B/userC全矩阵测试recall/get/search/export/delete/share，覆盖same replay、
  conflict、provenance与forget cascade。

### S3-T7 — Export/delete/forget/cascade [AC-4]

- `export_principal`输出versioned、bounded/paginated records和scope，不含raw embedding默认值。
- `delete_principal`/`delete_scope`先递增erasure epoch，再事务删除messages/facts/vectors/recall snapshots和
  job payload；保留hash-only erasure/turn/job tombstone至少覆盖execution outbox retention，返回counts
  receipt。`forget_fact`写fact/provenance tombstone并删除derived vectors/projections，late job不能重建。
- 全局`delete_all`只保留显式administrative API，不作为普通用户路径。

### S3-T8 — Safe observability [AC-4, AC-7]

- allowlist事件字段：deployment/household/actor/session使用hash或opaque id，外加count/bytes/duration/code；禁止
  content/token/vector/path/exception repr。
- 单独敏感字段扫描测试覆盖成功、timeout、corruption、conflict、delete/export失败。

### S3-T9 — 退休公开Adapter并过slice gate [AC-1, AC-3, AC-4]

- 从`__all__`删除`ConversationMemoryAdapter`和重复DTO/enums；internal translator不得被public snapshot导出。
- unit/integration/mock/sqlite/joint-contract矩阵；更新Memory architecture/README/migration guide/changelog；finalize。

## Required scenarios

| ID | 必须证明 |
|---|---|
| S3-C1 | standalone import无Harness依赖；extra安装后direct manager通过Protocol |
| S3-C2 | committed pair+receipt+fact worker从claim到facts全原子、crash恢复和幂等冲突 |
| S3-C3 | household/personal/family完整隔离矩阵 |
| S3-C4 | session rebind在读取前失败 |
| S3-C5 | export/delete/forget全级联且不越界 |
| S3-C6 | logs/diagnostics无敏感内容 |
| S3-C7 | delete/forget后重放旧turn或late fact job只得到tombstone no-op，不复活内容 |
| S3-C8 | v3 offline migration成功保留数据；任一fault恢复原库 |
| S3-C9 | 删除前无/旧fence重放被拒；删除后新Turn即使recall DB outage也可按可信起始时间记录 |
| S3-C10 | 多continuation已apply tentative及派生数据按四类级联排除；completed pair补齐且全source覆盖 |
