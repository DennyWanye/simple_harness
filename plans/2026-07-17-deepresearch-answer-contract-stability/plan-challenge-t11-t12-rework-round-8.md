# T11/T12 定向回炉挑战 — Round 8（T12 增量复核）

> 范围：只复核 Round 5 的 component checkpoint closure、audit operation，以及它们与 existing-head 和当前 schema/FK 的一致性。  
> 结果：Round 5 两项均已闭环；发现一个新的 retention schema 高风险缺口。

## Round 5 Finding 复核

### 1. Component checkpoint/effect/pending/owner closure — PASS

- `v6-contracts.md:346-348` 已冻结 live roots、external shared owner、单个 `BEGIN IMMEDIATE` 和完整 checkpoint/effect/pending/owner/ref 清理顺序。
- 当前无 cascade 的 `workflow_checkpoint_effects`、`workflow_pending_writes`、`workflow_checkpoint_owners`、`workflow_checkpoints` 均被显式列出；effect-owned blob refs 也在 zero-ref blob 删除前清理。
- `plan.md:551-553` 已加入 retained checkpoint bytes 可解引用、删除后 checkpoint/effect/pending/owner 零残留及逐点 rollback oracle。
- continuation head 在 run 之前删除，child lineage 在 parent/root run 之前删除，与 `workflow_research_continuation_heads` 的 `ON DELETE RESTRICT` 和 lineage parent-operation FK方向一致。

### 2. Audit operation identity/transaction/fault points — PASS

- `v6-contracts.md:338` 已冻结 audit ID、caller-key hash、operation kind、request hash、result exact JSON、`run_id=child_run_id`、same-key replay与 different-key语义。
- `v6-contracts.md:340` 已把 audit insert 放入 create/get 同一事务：新建分支在 child run 后插入，existing 分支在 head canonical validation 后插入；十一 write points 包含 `after_audit_operation_insert`，existing 分支也覆盖 audit/before-commit。
- stored `created` 语义固定：same key返回首次 stored result；different key可记录 `created=false`，但两者都返回同 child/start/head并统一 notify。该行为与 existing-head 不重读 pin/closure相容。
- `plan.md:546,553` 已同步十一点 fault matrix、same/different-key audit oracle和 commit-before-notify恢复。

## 新 Finding

### [High] 后序删除只明确清 descendant start requests；root run 的 start request 没有 FK cascade，会留下 durable idempotency dangling row

**证据**

- `v6-contracts.md:348` 的顺序写为 `descendant lineage/start operations/start requests/session refs ... descendant child runs -> root run`。按通常含义，`descendant` 不包含 component root。
- `workflow_start_requests` 只有 `request_key` 主键和业务唯一约束，没有到 `workflow_runs` 的 FK：`backend/deskpet/workflows/store/schema.py:18-23`。删除 root run不会级联 root start request。
- 当前 retention 明确对全部待删 run IDs执行 `DELETE FROM workflow_start_requests WHERE run_id IN (...)`：`backend/deskpet/workflows/retention.py:865`、`:1004`。若新 component transaction按修订文字只清 descendants，root request key仍指向不存在的 run；未来同 key start/replay会命中 dangling durable identity。
- `plan.md:553` 的零残留 oracle只点名 checkpoint/effect/pending/owner，没有断言 component 全部 `workflow_start_requests` 均为零，因此该缺口不会被现有测试要求必然发现。

**精确改法**

把 §6.3 删除项改为：

`all component lineage rows child-to-parent -> all component start operations/audit operations/start requests/session refs (including component root) -> ... -> child runs -> root run`

并在删除事务开始时冻结 `component_run_ids`，所有上述表均按该集合删除/验证。测试补充：parent→child→grandchild 删除后，`workflow_start_requests WHERE run_id IN component_run_ids`、`workflow_operations`、`workflow_session_refs`、`workflow_research_lineage` 全为零；任一点 fault 后三代 start request 集合完整恢复。若 capability retention也在本计划处理，应仅在所有 run 删除后按 `NOT EXISTS workflow_runs(capability_hash)` 删除 orphan capability；否则明确 capability由既有全局策略保留，避免实现者临场决定。

## Round 8 结论

Round 5 的两项原问题已完整修正，audit/existing-head/FK 主链一致。当前唯一未收敛点是 root start request没有 FK cascade而 contract仅写 descendant cleanup；这是一个局部、可一行修正并补 oracle的问题。修正后本审计范围可 PASS。

VERDICT: FAIL
