---
id: TC-HM-14
purpose: Verify fresh Host composition, primary-authority fences, durable recovery, emergency export, and affected public API compatibility
status: active
surface: api
type: scripted
obligations: [HM-S4-TO-INTEGRATION, HM-S4-TO-DATA, HM-S4-TO-REGRESSION]
tags: [human-memory, host-api, primary-authority, recovery, data-integrity, regression]
entrypoint: public Human Memory Host API/facade
preconditions:
  - Fresh isolated user-data directory is available
  - A public-only adapter is pinned by path and SHA-256 for the current run
  - Raw evidence output directory is under ignored .local-test-evidence
revision: 2
---

# TC-HM-14 — Host 入口、恢复与原始数据守恒

## 范围与禁止

- 只调用对外公开 Host API/facade 和明示的 test fault seam；禁止 private import、直连 SQL、repository object 或实现内部状态断言。
- 只覆盖 fresh `human-memory-v1` 数据目录。legacy/future epoch 只验证稳定拒绝，不验证迁移、导入或 UI 展示。
- 原始结果、manifest、export、receipt 和日志只写 ignored `.local-test-evidence/<date>/<run>/`。

## 步骤与预期

| 步骤 | 公开黑盒动作 | 预期结果 |
|---:|---|---|
| 1 | 以 fresh isolated data dir 启动 Host，读取 data-format compatibility 与 primary-open 回执，然后再次调用同一初始化。 | 默认启用 `human-memory-v1`；只有一个 writable primary authority；重试返回同一 immutable ref 或稳定 idempotent receipt，不产生第二 primary。 |
| 2 | 通过公开 API 追加 primary evidence，创建 A/B TaskScope，写入 binding、checkpoint、queue turn 与 control；读取 pre-operation raw integrity manifest。 | 所有原始记录 append-only，且有 immutable receipt/ref/hash；manifest 含各 raw 集合的 row count/content hash，不以 projection/cache hash 代替。 |
| 3 | 依次用旧 Session create/switch/rename/delete 直连 primary authority，包含重放和 lost-ACK retry。 | 每个操作均返回稳定 `human_memory_primary_authority_immutable`；primary ref、raw row count/content hash 和既有内容不变。 |
| 4 | 分别用 legacy epoch 和 future epoch 打开公开 Host 写入路径，随后重启 fresh 目录。 | 非兼容 epoch 在任何写入前稳定 fail closed，不自动迁移不建第二 primary；fresh 目录重启后仍可读写。 |
| 5 | 关闭 ingress，在 projection/search/queue 待处理项存在时依次注入 crash/lost-ACK/busy-checkpoint，执行 drain-or-park、WAL checkpoint 和 manifest，再冷重启恢复；独立使 FTS5 unavailable 并调用搜索。 | fence 后新 ingress 稳定拒绝；已入账项要么 drain 要么有 park receipt/ref；busy/outbox-gap/hash-mismatch 均 fail closed；重启可重放且无丢失/重复；FTS5 缺失返回冻结 `human_memory_search_unavailable`，不降级为未经 permission filter 的模糊扫描。 |
| 6 | 在步骤 2–5 前后重读 raw integrity manifest，对既有 raw 集合比较 row count 与 content hash，并验证新 receipt 只以 append 方式增加。 | 既有 raw rows/count/content hash 不回退不改写；新 recovery/control/receipt 只 append；派生 cache 可变但不计入 raw 守恒断言。 |
| 7 | 在普通恢复被故障阻断时调用 emergency read/export，关闭并重开 Host 后再次导出。 | export 只读且包含 canonical refs、row counts、content hashes、format/manifest receipt；两次导出在同一 canonical revision 上 byte-identical；不包含认证材料。 |
| 8 | 对 critical + affected 公开入口各发一个有效请求，并对 wrong principal、FTS unavailable、legacy/future epoch 和旧 CRUD fence 等冻结的受影响失败边界发必要负例：primary open/append、TaskScope create/search/open/mutate、binding append、queue/control、audit refs、recovery/export、legacy CRUD fence。 | 每个入口返回 typed success 或冻结 stable error code，ref/receipt/hash 完整；无 5xx、无第二 primary、无 authority 扩大。 |

## 决定性证据

- public-only adapter path/SHA-256 与公开调用清单；fresh/cold process incarnation；compatibility/fence/drain/park/WAL/export receipts；critical+affected API status/code/ref/hash 矩阵。
- 每个阶段的 raw manifest（row count/content hash）、append-only delta、duplicate/lost-ACK replay 结果，以及旧 CRUD 稳定拒绝证据。
