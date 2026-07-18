# T11/T12 定向回炉挑战 — Round 11（Round 8 窄复核）

> 范围：只复核 Round 8 唯一 High——retention component 是否显式清理全部 run（含 root）的 start/audit operations、start requests、session refs，并具备零残留与 rollback oracle。

## 复核结论

该项已完整闭环，没有发现新的 schema/FK 悬挂。

### Contract 与 plan

- `v6-contracts.md:355` 已把删除集合冻结为：lineage child-to-root 后，显式删除 **component 全部 run** 的 start/audit operations、`workflow_start_requests` 和 session refs，再删除 snapshot/blob，最后 child runs→root run。
- 同一条款明确指出 root start request 没有 run FK cascade，不能依赖删除 root run自动清理。
- `plan.md:551` 与 contract 顺序一致，不再只写 descendant start refs。
- `plan.md:553` 已加入 parent/child 全部 start/audit/request/session 表零残留，以及每个 delete point fault 后全 rollback 的自动化 oracle。

### 当前 schema 一致性

- `workflow_start_requests` 没有 `workflow_runs` FK（`backend/deskpet/workflows/store/schema.py:18-23`），现 contract 要求 component-wide 显式删除，已消除 root dangling request key。
- `workflow_operations` 有 `run_id -> workflow_runs ON DELETE CASCADE`（`schema.py:129-133`），但 contract 仍提前显式删除 component 全部 start/audit operations；顺序合法，也让零残留/fault oracle不依赖 cascade。
- `workflow_session_refs` 有 run cascade（`schema.py:44-48`），提前按 component run IDs 显式删除同样合法。
- lineage 先按 descendant-to-root 删除，随后 snapshot，再 child runs/root run，与 `workflow_research_lineage.parent_operation_id/snapshot_hash ON DELETE RESTRICT` 以及 snapshot `run_id ON DELETE RESTRICT` 的真实 FK 顺序一致。

## 判定

Round 8 的唯一 High 已修正为无二义、可编码、可故障注入并可用真实 schema 查询验证的删除契约。本轮窄复核无 blocker/high。

VERDICT: PASS
