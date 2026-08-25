# Challenge revision manifest

- Before SHA-256: `b10f08bfd72c75c43ba42f4e8026c07b691aeefee0b006fb9aad00ea12669428`
- After SHA-256: `2c9f1e00a58c1978621cf1ddf2456d9056514c7f2851452c48f73ab029b55f9c`
- Canonical synthesis: `synthesis.json`

## Exact section changes

1. `目标数据模型/projects`：新增 monotonic `project_revision`。
2. `目标数据模型/session_project_bindings`：新增 explicit-only immutable `execution_identity`；trigger 从
   UPDATE-only 扩展为 UPDATE/DELETE；删除保留 binding provenance。
3. 新增 `创建、列表与升级辅助状态`：冻结 `session_creation_receipts`、singleton catalog revision、
   backfill phase ledger 与 app-private `WorkspaceResolutionV1`/authority record v3。
4. `入口、持久化、信任与停止追踪点`：所有 built-in/MCP wrapper 统一按 ToolContext.run_id 取 private
   authority；新增 Run admission/project relocation CAS、nonterminal Run fence 与 effect 前双重 identity re-stat。
5. 文件影响清单新增 `sdk_adapters/tools.py`、Windows identity probe 与 guarded restore command；migration
   职责扩为 receipts/catalog/backfill state。
6. H-1～H-4 改为实测结果：macOS identity PASS、100k query p95 72.85ms、private authority wiring
   `3 passed in 0.35s`、现有 picker 可复用；Windows probe 保留强制 release stop gate。
7. Task 1：冻结 WorkspaceResolution/explicit identity、完整 v32 DDL/FK/trigger 与跨平台 probe oracle。
8. Task 2：替换 post-commit Provider tombstone 补偿，改为 request_id+intent_hash receipt 和
   provider-lock→DB-lock→single transaction；commit sole visibility；定义 retained provenance、terminal delete、
   no-revival/lost-ACK/fault matrix。
9. Task 3：替换 unbounded nested projects/sessions response，改为 catalog 与 per-scope 两级 keyset page；冻结
   query-time active/product predicate、catalog revision、stable cursor、pinned selection、128/256KiB budgets。
10. Task 4：冻结 authority v3，强制 private getter，77-tool total admission metadata，dynamic MCP default deny，
    two identity checks，active-Run relocate rejection 和 revision CAS。
11. Task 6：store 保存 bounded pages/cursors/pinned descriptors；windowing mounted rows ≤150；React update
    p95 ≤50ms。
12. Task 8：修正旧 v17-v31 prerequisite 事实；加入 startup-blocking coordinator、verified pre-v32 backup、
    frozen high-water/chunk cursor/count conservation/completion digest、crash matrix 与 guarded restore；无 down migration。
13. Task 9：加入 SQL/payload/React hard gates；Windows 先跑 Project/explicit identity probe，再真人 UI。
14. `behavior-contract.md` B-9 同步改为删除后保留 immutable binding/receipt provenance，但不可见且不可复活。
15. `ARCHITECTURE/ARCHITECTURE.md` 将已过时的 version-contract defect 修正为 v17-v31 migration-owner 当前事实。
