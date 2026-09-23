# Operation 物化接线设计（待实现）

**状态：OPEN / 设计，不是完成证据。**  
**范围：** H1-H 新协议把一个已冻结 `OperationEnvelope` 精确交接给一个由 `accept_result()` 物化的 action，并使用既有 `events` 和已批准的四张 admission seams 表。**本文件不修改补遗，也不表示 O09 已通过。**

## 当前 blocker

SDK 中 `OperationEnvelope(...)` 和 `HtnStore.bind_operation(...)` 没有生产 callsite；现有调用均在测试。`accept_result()` 只从 result 的 `actions/*.json` artifact 读取 candidate，无法取得 `operation_occurrence_id`。`BoundPlanningOperationOrigin.provenance_receipt_id` 也没有 producer。

因此当前 O09 保持 **OPEN / SOURCE BLOCKED**。不得以 task、target、connector、artifact path、hash、latest action 或 `TaskSemanticBinding` 反推 origin。

## 不新增第五张表的 receipt

使用不可变 event 作为系统签发的 materialization receipt：

```text
type = OperationMaterializationBoundV1
idempotency_key = OperationMaterializationBoundV1:<logical-key>
receipt_id = event_id
```

`logical-key` 绑定 `mission_id + operation_occurrence_id + attempt_id + declared_action_output_path`。event payload 是 canonical document，并含 `command_hash` 和 `payload_hash`。写入前必须按 logical key 读取既有 event：

- 同一 canonical payload/hash：返回原 event，幂等；
- 同 logical key 的任一字段不同：`StoreConflict`；
- 零条：写入 event；
- 多条或不能严格解码：`SourceUnavailable(operation_materialization_receipt_unavailable)`。

不能仅依赖 `Store.append_event()` 的 idempotency 行为，因为它对同 idempotency key 直接返回旧 event，不比较 payload。

payload 至少冻结：

```text
schema_version, command_id, command_hash, payload_hash,
mission_id, tenant_id, issuer_principal_id, scope_id,
operation_id, operation_occurrence_id, request_hash, envelope_hash, obligation_id,
task_id, htn_occurrence_id, contract_revision, plan_revision,
attempt_id, declared_action_output_path, issued_at_ms
```

`planning_operation_action_links.provenance_receipt_id` 保存该 event ID；它仍只保存 identity bridge，不保存 action state。

## 可信入口、冻结时点与调用关系

Host 使用已有 `MissionControlV1` 认证装配得到的固定 `tenant_id + Principal`。新增的内部 Host/SDK operation issuer 必须在 Store 事务中验证 Mission tenant、Principal、scope、任务归属、HTN occurrence、计划/contract revision 与 attempt 归属；这些值不能从 worker、candidate JSON 或模型输出取得。

### 时序修订：不是“worker 派发前必须冻结”

本设计此前把冻结写成“真实 Attempt 创建之后、worker 执行之前”。这是为了让当前
`accept_result()` 能把 worker 的 `actions/*.json` candidate 接到 envelope 而加入的假设，
不是 AER 的硬要求，现予修订。

AER §6.3 的强制业务顺序是：报告 `Acceptance` → `ACTION_PROPOSAL` Review → 用户/政策授权
→ 实际执行 → `OPERATION_OUTCOME` Review。AER §14.1 的强制技术顺序是：

1. **A 编排授权事务**检查当前 plan/demand、要求/输入、Review/Acceptance、操作合同、审批、
   authority epoch、预算和资源冲突，并保存 action/outbox/稳定 `ExecutionBinding`；
2. **B 执行准入**在执行权威中原子去重绑定、冻结原请求、取得 execution fence、重读短期
   授权/有效性/目标条件，持久 `HANDED_OFF` 后才允许网络或 OS 操作；
3. **C 结果记录**先由执行权威保存 outcome/receipt/费用，再由编排导入。

因此，若 ACTION_PROPOSAL Review 依赖先前 worker 产出的 candidate，envelope 可以且必须在该
Review 与授权之后、A 事务创建 `ExecutionBinding` 时产生；它不应在 candidate 尚未存在时伪造。
无论 producer 位于何处，冻结请求及最后一轮来源/权限/目标检查必须发生在 B、实际网络/OS
调用之前。

修订后的权威调用关系是：

```text
authenticated Host / MissionControlV1 (fixed tenant + Principal)
  -> explicit OperationIntentCommand after official ACTION_PROPOSAL Review + authorization
  -> A: verify source records and issue immutable envelope / bind_operation / receipt event
       together with exact action-or-ExecutionBinding and outbox
  -> B: execution gateway atomically binds/fences and freezes the original request;
       records HANDED_OFF before external operation
  -> C: execution receipt/outcome enters the existing result/reconciliation ledger
  -> `accept_result()` may be adapted only as an exact existing candidate→action materializer;
       it must read the source-bound receipt, never construct an envelope from its candidate
  -> `begin_handoff()` re-reads the complete snapshot before reserving budget/outbox
```

`accept_result()` must treat missing, duplicate, malformed, cross-tenant, wrong principal/scope, wrong task/HTN occurrence, revision mismatch, wrong attempt, wrong output path, artifact hash mismatch, or binding/envelope mismatch as named `SOURCE_UNAVAILABLE` and write no action, link, reservation, outbox or connector call.

## 最小真实 intent producer 与缺失合同（待实现）

新增的内部命令必须是固定认证身份的 `OperationIntentCommand`，而不是让 worker/candidate
提供 `OperationEnvelope`：

```text
issue_operation_intent(
  command_id, mission_id, task_id, requested_operation_occurrence,
  reviewed_candidate_ref, parameters_ref, normalized_target,
  connector_id + connector_version, expected_target_version,
  effect_contract_ref, acceptance_ref, action_proposal_review_ref
) -> OperationMaterializationReceipt
```

构造 API 的 tenant、Principal、scope 来自 Host 认证装配。A 事务必须核验 Mission tenant、
Task/HTN occurrence、Task contract revision、独立 Requirements revision、当前/指定
Acceptance、官方 `ACTION_PROPOSAL` ReviewRecord 及其 package/候选引用、当前操作授权、
connector registry/version、预算和冲突。然后系统分配或验证 OperationId/OccurrenceId，按原始
来源产生 envelope，写 `bind_operation` 与 payload-equality checked receipt event，并写稳定的
action-or-ExecutionBinding/outbox；任一来源不可读、非官方、过期、跨 Mission/tenant 或 hash
不等时 `SOURCE_UNAVAILABLE`/授权拒绝，零业务写入。

现有 `ReviewPackage`/`ReviewRecord`/`Acceptance` 和 HtnStore getters 可复核 purpose、官方状态、
Mission、Requirements、Acceptance validity 及 content hash；但现有记录没有 operation intent、
normalized target、connector version、canonical parameters 或 effect contract。`ACTION_PROPOSAL`
目前是已有枚举/存储许可，尚无生产 producer。

`EffectContractV1` 需要是独立、不可变且可解析的 canonical record：至少冻结 connector/version、
`OperationSpec` content hash、期望外部效果/完成里程碑、目标前置条件、幂等 namespace/保留期、
条件写与 lookup/reconcile/retry/compensation 能力。现有 `runtime.connectors.OperationSpec` 只有
`name/level/required_params/mutates/kind/cost_micros_ceiling`，可作为该合同的组成或 hash 引用，
不能单独冒充 effect contract。

`ParametersArtifactV1` 同样需要 immutable canonical params bytes、TypedRef
`kind/id/revision/content_hash` resolver 和现有 `runtime.connectors.params_hash()` 的绑定。Review、
Acceptance、parameters 与 effect-contract 的 TypedRef 都须由各自权威 getter 逐项解析；
`EvidenceResolver` 只处理 `SourceCitation`，不能代替通用 TypedRef resolver。

验收 oracle：真实 Host intent command 必须拒绝缺任一 source/Principal/tenant/scope/Review/
Acceptance/authorization/contract/Attempt-or-ExecutionBinding，且零 envelope/binding/event/action/
outbox/budget/connector 写；合法命令重送同 canonical body 只得同 receipt/identity，差异 body
冲突；A 写中故障后 envelope+receipt+binding 全有或全无；B 必须在 HANDED_OFF 持久化后才允许
connector；C 与 candidate/action bridge 只能读取 receipt 指定来源，不能按 task/target/latest/
hash 推断。完成这些真实 producer/caller 和故障用例前，O09 仍是 **OPEN / SOURCE BLOCKED**。

## 为什么不在 accepted-result 后补绑

由可信 API 对已提交 result 显式绑定在语义上可表达，但现有 `accept_result()` 没有 Principal 参数，也不能与外部 API 共享同一 Store transaction。独立预绑定会留下 TOCTOU；在 result 已接受后才绑定又晚于 action 物化。

若将来采用该形式，必须是携带认证 Principal 的单一内部 accept command：在同一事务中验证尚未接受的 immutable result/artifact、写/验证 receipt event、物化 action/link。它不是当前最小接线方案。

## 实施任务与验收 oracle

| 任务 | 实施点 | oracle |
|---|---|---|
| M1：issuer | 已认证 Host facade 到 SDK 内部 issuer | 缺 Principal/tenant/scope/attempt/HTN occurrence 任一项拒绝，零 event/binding |
| M2：冻结 | 同一 Store transaction 的 `bind_operation` + receipt event | fault 前后只允许两者都不存在或都存在；重送同 command/body 返回同 event ID |
| M3：dispatch metadata | 系统拥有的 attempt package metadata | worker 无法从 candidate JSON 自设 receipt/path；输出只匹配预声明 path |
| M4：accept bridge | `accept_result()` action materialization loop | receipt、result、attempt、artifact、binding 逐项一致才传 origin；不做推断 |
| M5：handoff | `begin_handoff()` | 新协议 action 缺 link/marker 被删改/bridge 身份错均拒绝，零 action budget reservation 与 outbox；legacy 保持原行为 |
| M6：O09 fault/replay | real Host issuer → attempt → result → accept path | action 写后 link 写前 fault 回滚；重开 Store 重送得到一份 action/link/event；handoff 从不观察半 link |

O09 只有 M1–M5 的真实 producer/caller 接线完成，并且 M6 在真实 `accept_result()` 路径通过后，才可从 OPEN 改为 PASS。

## O09 最小 issuer 契约与当前 source 缺口（补充，未实现）

下列接口是可实施的最小形状，**不是当前 SDK 的生产能力，也不构成 O09 通过**：

```python
OperationMaterializationApi(
    store, *, tenant_id: str, principal: Principal, scope_id: str
).issue(
    envelope: OperationEnvelope,
    task_id: str,
    attempt_id: str,
    declared_action_output_path: str,
    command_id: str,
) -> OperationMaterializationReceipt
```

`tenant_id`、`principal` 和 `scope_id` 必须由既有已认证 Host / `MissionControlV1`
装配在构造 API 时固定；不能放入调用 body、worker package、candidate 或模型输出。
在一个外层 Store 事务中，issuer 必须读取并核验以下已有记录，然后才将
`HtnStore.bind_operation()` 与 payload-equality checked 的
`OperationMaterializationBoundV1` event 一起写入：

1. `Store.get_mission(envelope.mission_id)`：Mission 属于固定 tenant、仍可操作，且
   envelope scope 等于固定 scope。
2. `Store.get_task(task_id)` 与 `Store.get_attempt(attempt_id)`：Task/Attempt/Mission 三者
   精确相属；Attempt 是 dispatcher 已创建的实际 Attempt，而不是从 candidate 取得的 id。
3. `HtnStore.task_semantics_of(mission_id, task_id)`：Task binding 的 task、obligation、
   scope、Task contract revision 等于 receipt 中对应的 **Task contract 轴**。这个
   `TaskSemanticBindingV1.contract_revision` **不得**等同于
   `OperationEnvelope.requirements_revision`。
4. `HtnStore.active_plan_revision()` 加 `list_plan_memberships()`：active plan 中恰有一个
   membership 将该 task 映射到 receipt 的 `htn_occurrence_id`，并冻结其 plan revision。
5. `HtnStore.get_requirements_revision()`（或等价的当前 requirements getter）：单独读取并
   核验 envelope 的 `requirements_revision`，作为独立的 **RequirementsRevision 轴**。
   它与 Task contract revision 可以不同，不能用任一值替代另一个。

receipt 因而必须分别持久化 `task_contract_revision`、`requirements_revision`、
`plan_revision`，并保留 envelope 的 canonical bytes/hash、Task/Attempt/occurrence、
认证身份与 declared path。重送前先按 logical key 严格读取既有 event 并比较完整
canonical payload/hash；相同才回原 event ID，任一差异为 `StoreConflict`。不能依赖
`append_event()` 只按 idempotency key 返回旧 event 的行为。

### 三种 hash 不能互相替代

`parameters_artifact_ref.content_hash`、candidate `params_hash` 与 candidate artifact 的
整文件 hash 是三个不同对象：

| 值 | 所属对象 | 正确关系 |
|---|---|---|
| `OperationEnvelope.parameters_artifact_ref.content_hash` | 上游冻结的 typed parameters 引用 | issuer 必须通过权威 TypedRef resolver 读取其不可变 bytes 并核验此 hash。它不是 candidate 文件 hash。 |
| `params_hash` | 已接受 candidate 的 `params` canonical JSON，使用现有 `runtime.connectors.params_hash()` 公式 | issuer 在冻结 parameters bytes 后，才可冻结由同一 canonical params 得到的值；accept 时以同一函数计算 candidate params 并精确相等。禁止从 candidate params 反造 envelope。 |
| `artifact.content_hash` | result 中实际 `actions/*.json` candidate artifact 的完整字节 | accept 时从 Result/ArtifactStore 读取并核验，且 path 必须等于 receipt 的 declared path。它不等于任一 parameters hash。 |

当前没有能完成第一行的生产来源：`effect_contract_ref` 的 producer/schema/caller 不存在，
`OperationEnvelope(...)` 与 `bind_operation()` 也没有生产 caller。`EvidenceResolver` 仅能
解析 source 引用，不能作为所有 `TypedRef(kind, id, revision, content_hash)` 的通用 resolver。
因此也没有权威方式把 parameters、review、effect-contract 三个 TypedRef 与 Task semantic
binding、HTN occurrence 和真实 envelope 逐字段核验。

`accept_result()` 当前也没有 Principal、receipt token 或 declared output path 参数；它只从
accepted result 的 `actions/*.json` artifact 解析 candidate。实施时必须在其已有同一写事务中、
在 `propose_action()` 前，严格重读 result/task/attempt/artifact、receipt event、operation
identity/binding 和上述三个 revision 轴。只有全部相等时才构造
`BoundPlanningOperationOrigin(provenance_receipt_id=event_id)`；任何缺失、重复、不可解码或
不等都返回具名 `SOURCE_UNAVAILABLE`，不写 action/link/reservation/outbox，也不调用 connector。

在真实 Host envelope producer、TypedRef resolver、严格 receipt reader 与 accept caller 都补齐前，
本节只是实施约束；O09 继续为 **OPEN / SOURCE BLOCKED**，不得由测试 seam 或邻近 action
测试升级为 PASS。
