# S2 — Harness committed-turn Outbox 与恢复

<!-- slice-status: draft -->

## Release unit

- MUST AC：AC-3、AC-7（2/8）
- Tasks：8/10
- 高风险系统：terminal transaction、execution schema、dispatcher（3/3）
- 依赖：S1 contract frozen

## 文件影响

| 文件 | 修改 |
|---|---|
| `src/simple_harness/execution/sqlite/migrations/0004_fresh.sql` | 核对并启用S1已冻结的final turn-outbox schema，不另建migration |
| `src/simple_harness/execution/sqlite/migrations/__init__.py` | loader只接受fresh v4，旧v3 fail closed |
| `src/simple_harness/execution/sqlite/migrations/execution_v3_to_v4.py`（新） | 显式offline、identity map、临时库校验与中立manifest输出 |
| `src/simple_harness/execution/memory_outbox.py` | `CommittedTurnSpec` repository/dispatcher |
| `src/simple_harness/execution/uow.py`、`sqlite/uow.py` | terminal-only intent接口和原子写/replay conflict |
| `src/simple_harness/runtime/kernel.py`, `terminal.py` | completed terminal构造turn；删除start/enqueue user intent |
| `tests/execution/test_memory_outbox_*` | v4 UOW、claim/retry/crash矩阵 |
| `tests/runtime/test_react_conversation_memory.py` | failed/cancelled/continuation terminal行为 |

## Tasks

### S2-T1 — 激活 fresh execution v4 turn-outbox 部分 [AC-3]

- 复核S1创建的self-contained v4 DDL，不拼legacy migration；descriptor/hash/version test继续唯一。
- 启用其中`memory_outbox` final列：一行代表完整Turn，unique `turn_id`，保存payload bytes/hash、state/lease/
  attempt/error。context/session继续使用S1 identity；v1–v3保持stable fail-closed。

### S2-T2 — Canonical committed-turn spec [AC-3]

- `CommittedTurnSpec.from_domain` 验证 user/assistant role、identity、scope、text、turn_id、SDK可信生成的
  canonical `turn_started_at`、recall取得的opaque write fence和payload hash；非文本message按明确policy跳过或只写显式memory_text，不把附件/tool
  payload/reasoning隐式持久化。
- 同 turn same payload replay成功；missing/added/different payload稳定 conflict。

### S2-T3 — 删除 tentative user intent [AC-3]

- 从 root start和continuation enqueue UOW/kernel路径删除 Memory intent参数、fault points和wake。
- start/continuation仍提交执行事实与Context消费，Memory outbox只在对应 completed terminal创建。
- failed/cancelled/invalidated/parse失败不产生任何turn row。

### S2-T4 — Terminal + outbox 单事务 [AC-3]

- root/continuation terminal在确定最终 assistant output后构造 user+assistant committed turn；与 terminal state、
  delivery、parent wake在同SQLite transaction写入。
- 加 before-write/after-write/before-commit/after-commit fault hooks；rollback不留半条 intent。

### S2-T5 — Dispatcher与apply-before-ack恢复 [AC-3, AC-7]

- dispatcher调用 `AgentMemoryPort.record_committed_turn`；核对 turn_id/payload_hash receipt。
- `REJECTED_ERASED` 是含相同turn/hash的成功终态no-op receipt，dispatcher可ack但必须发safe privacy event；
  它不同于payload conflict/dead-letter。
- transient/timeout指数退避，permanent/conflict dead-letter；apply成功ack前崩溃重放同payload。
- pump startup recovery、bounded close drain和observability保留；日志只含ID/hash/attempt/code/duration。

### S2-T6 — Concurrency与backlog [AC-3, AC-7]

- 两Runtime owner/lease竞争时一个claim winner；lease过期可接管；旧token/attempt不能ack新claim。
- backlog counts按pending/claimed/applied/dead-letter；cleanup仅bounded applied slice。

### S2-T7 — 显式 execution v3→v4 offline migrator [AC-3, AC-7]

- 正常loader继续对v1–v3 fail closed；显式migrator要求runtime关闭并先备份，把v3 run/event/snapshot/
  continuation/delivery/parent-wake/context及所有终态/非终态状态复制到临时v4，再做逐表count、canonical hash、
  FK/integrity read-back，全部通过后才原子替换。
- migrator只接受产品冻结的`LegacyIdentityMap`，每个v3 user/session必须唯一映射完整AgentIdentity，缺失/歧义
  fail closed；它不import或调用Memory SDK，只输出versioned/hash-protected `ExecutionMigrationManifest`。
- manifest枚举每个Harness `source_event_id`并分类：completed run只有因果绑定最终terminal的最后user+
  assistant为`KEEP_COMPLETED_PAIR`；同run更早root/continuation user为`SUPPRESS_TENTATIVE`；failed/cancelled
  为`SUPPRESS_TERMINAL`；非终态最多一个最新user为`DEFERRED_TURN`、更早user仍SUPPRESS_TENTATIVE。每项包含
  payload hash、run/turn/causal-terminal关联和可补齐pending entry的canonical payload。迁移后deferred run completed时用immutable user input+assistant
  构造一个正常v4 committed turn；failed/cancelled零turn。任一copy/check/rename fault恢复execution原文件；
  双库swap/旧pin由产品coordinator负责。
- 对assistant `continuation_id=NULL`，只接受可由v3 canonical terminal event/receipt identity解析并与
  continuation id、claim epoch、唯一progress receipt、run event durable sequence全部交叉验证的唯一候选；
  不按created_at猜测，歧义/缺损fail closed。
- v4增加versioned `legacy_turn_cursor`：非终态迁移保存当前DEFERRED input/hash；迁移后每次新continuation
  enqueue与旧cursor `SUPPRESS_TENTATIVE` disposition、新active cursor替换同事务CAS。terminal与cursor consume+
  committed turn同事务，只配当前active user；failed/cancelled消费cursor但零turn。cursor/disposition恢复按
  version幂等，Memory suppression用`legacy-source:`、正常turn receipt用`turn:`隔离key namespace。
- 迁移矩阵覆盖所有run状态、continuation、delivery、parent wake、identity mapping、legacy outbox same/
  different replay和deferred completion。

### S2-T8 — Slice gate与架构回写 [AC-3, AC-7]

- targeted + full execution/runtime suite；fresh/history/temporal/fault lanes。
- 更新Harness architecture/schema/recovery文档和changelog；gate finalize exit 0。

## Required scenarios

| ID | 必须证明 |
|---|---|
| S2-C1 | completed root/continuation各一份turn intent |
| S2-C2 | failed/cancelled/parse/invalidate零intent |
| S2-C3 | terminal与intent在每个fault window原子 |
| S2-C4 | apply后ack前崩溃最终同turn一份receipt |
| S2-C5 | same replay通过，different payload conflict |
| S2-C6 | 并发claim、lease takeover、close drain有界 |
| S2-C7 | 删除前旧fence/outbox被拒；删除后新Turn在recall DB outage后凭turn_started_at绑定当前epoch |
| S2-C8 | migration后再来多continuation逐次supersede；NULL FK唯一解析；完成一pair/失败零pair/crash幂等 |
