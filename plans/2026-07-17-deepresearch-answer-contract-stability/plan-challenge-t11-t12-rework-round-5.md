# T11/T12 定向回炉挑战 — Round 5（T12 增量复核）

> 范围：只复核 Round 2 的四项 finding、Round 1/3 的交叉约束，以及修订后新增冲突；不重复已闭环项。  
> 结果：Round 2 四项原 finding 均已实质闭环，但新增一项 Blocker 与一项 High，尚不能判定 100% code-executable。

## 已闭环复核

1. **existing head / expired pin**：PASS。`plan.md:546` 与 `v6-contracts.md:316-320` 已冻结唯一 `create_or_get_continuation_v6()` 入口、head-first 短路、禁止旧 resolver TOCTOU、existing 分支不重读 pin/closure，以及 created/existing 都从 persisted start payload notify。
2. **900 秒与 30 秒 settle**：PASS。`v6-contracts.md:322` 已明确映射到现有 `DurableDeadlineV1.created_at/last_observed_at/wall_not_after`，冻结 deterministic deadline ID、create-or-load 不延长，以及 `min(accepted_at+30, wall_not_after)`、T+900 后拒绝。
3. **retention component 方向**：原先“head/lineage 无条件 root”矛盾已修正。`v6-contracts.md:324` 现在从 live roots 建 parent/child 无向 component，head 与 parent 同事务删除，missing/corrupt closure fail closed。
4. **continuation identity**：PASS。`v6-contracts.md:312-314` 已冻结空 payload、logical slot、start operation、start payload/request hash，以及独立固定 UUID golden vector；caller key 不再污染 child identity。

## 新增 Findings

### 1. [Blocker] component 删除序列会删除 checkpoint blob owner，却没有删除 checkpoint/owner 行，可能留下可恢复 checkpoint 指向已删 closure

**证据**

- `v6-contracts.md:324` 的单事务顺序为 `descendant heads -> inherited child staging/checkpoint owners... -> ... -> blobs/spec -> runs`，但没有删除 `workflow_checkpoints`、`workflow_checkpoint_owners` 或 `workflow_checkpoint_effects` 行。
- 当前 schema 中 `workflow_checkpoints` 与 `workflow_checkpoint_owners` 没有到 `workflow_runs` 的外键或级联：`backend/deskpet/workflows/store/schema.py:108-127`。删除 run 不会自动删除这些行。
- checkpoint 的 blob authorization 是独立 `workflow_blob_refs(owner_kind='checkpoint', owner_id=checkpoint_id)`：`backend/deskpet/workflows/store/checkpointer.py:2017-2028`。若按新顺序先删 checkpoint owner/ref，再因“reference-count=0”删除 spec/fact/provenance blobs，残留 checkpoint bytes 仍可被 history/recovery 枚举，却已不能解引用。
- 当前 retention 正是先单独执行 checkpoint cleanup，再做 research lineage/snapshot/run cleanup：`backend/deskpet/workflows/retention.py:282-301`。新 contract 要求 component 单事务后序删除，但没有说明是复用此前 cleanup 的“零 checkpoint 行”前置条件，还是把 checkpoint rows 纳入新事务；两种实现的 crash/recovery 行为不同。

**精确改法**

在 §6.3 冻结一种且仅一种方案。推荐把完整 component 的 checkpoint 清理纳入同一个 `BEGIN IMMEDIATE`：

1. 收集 component 全部 run/thread/checkpoint IDs，并再次验证没有 live control/required delivery/retention root；
2. 删除这些 checkpoint 的 `workflow_checkpoint_effects`、`workflow_pending_writes`、`workflow_checkpoint_owners`、对应 `workflow_blob_refs(checkpoint|pending_task)`，再删除 `workflow_checkpoints`；
3. 删除 component effects/owner refs或证明 run cascade前已无 effect-only blob owner；
4. 才继续 head → pins/inherited owners → child-to-parent lineage/start refs → snapshot → zero-ref blobs → child runs → root run；
5. 每个 fault point rollback 后断言 checkpoint rows、owners、blob refs与 blobs集合全部恢复。

若坚持复用现有 `_cleanup_checkpoints` 前置阶段，则必须明确 component transaction 的入口 precondition 为“component 内 `workflow_checkpoints/workflow_checkpoint_owners/workflow_checkpoint_effects/pending_writes` 均为 0”，不满足就 fail closed/protect，且不得再称 checkpoint 删除与 component 删除为同一事务。无论选择哪种，都要新增 parent→child→grandchild 的 checkpoint bytes 可解引用 oracle，不能只查 FK/reference count。

### 2. [High] 新增 `audit_operation_id` 是 durable 返回 identity，但未冻结派生、record shape 与事务/fault 边界

**证据**

- `v6-contracts.md:316` 把 `audit_operation_id` 放进 exact `ContinuationCreateResultV1`；`v6-contracts.md:314` 只说 caller key 写“独立 audit/control operation”，没有定义 operation ID、operation kind、request hash、result JSON 或与 create/head transaction 的原子关系。
- `workflow_operations.operation_id` 是主键且要求 `operation_kind/request_hash/result_json`：`backend/deskpet/workflows/store/schema.py:129-135`。实现者不能只保存一个裸 ID。
- `v6-contracts.md:318` 的十个 fault point 不含 audit insert。若 audit 在事务外先写，crash 可留下“已 audit、无 child”；若在 create 事务内写，则十点矩阵漏掉第一项 mutation；若复用 control journal，则 result 字段命名和状态/consume 规则又不同。
- `plan.md:553` 要求 different-key、accepted-window crash 和 duplicate action，但没有 audit row 的固定 oracle，因此三种实现都可能自称通过。

**精确改法**

冻结 audit owner。推荐复用 `workflow_operations` 并纳入同一 create-or-get 事务：

- `audit_operation_id=sha256('deep-research-v6-continuation-audit-v1|'+parent_run_id+'|'+caller_idempotency_key)`；
- `operation_kind='research_continue_audit_v1'`；
- request exact JSON=`schema_version,parent_run_id,caller_idempotency_key_hash`，`request_hash=sha256(canonical_json(request))`；
- result exact JSON=`schema_version,parent_run_id,child_run_id,child_operation_id,created`；同 caller 重放要求 exact row，相异 caller keys 产生不同 audit row但同 child；
- 将 `after_audit_operation_insert` 加为第一个 fault point（十点相应变十一点），或明确 audit 已由外层 control journal事务完成并冻结 accepted→create recovery算法。不能保持两种都合法。

增加固定 oracle：same key replay audit row唯一；different keys audit IDs不同但 child/start/capability/head完全相同；audit insert后 fault全 rollback；commit后notify前恢复返回同 audit结果。

## Round 5 结论

Round 2 要求的 head-first、deadline、component reachability 和 UUID/start identity 已全部回写成功。本轮 FAIL 只针对新 contract 自身的两个未冻结边界：component checkpoint rows的真实删除闭包，以及新增 audit durable identity。二者补齐后无需重开已闭环的四项，只需做一次增量 challenge。

VERDICT: FAIL
