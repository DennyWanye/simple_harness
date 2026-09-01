---
id: TC-HM-14
purpose: Verify fresh Host runtime composition, authenticated binding, v44 audit retention, durable recovery, emergency export, and affected public API compatibility
status: active
surface: api
type: scripted
obligations: [HM-S4-TO-INTEGRATION, HM-S4-TO-DATA, HM-S4-TO-AUTHORITY, HM-S4-TO-REGRESSION]
tags: [human-memory, host-api, primary-authority, recovery, data-integrity, regression]
entrypoint: public Human Memory Host API/facade
preconditions:
  - Fresh isolated user-data directory is available
  - A public-only adapter is pinned by path and SHA-256 for the current run
  - The exact candidate Harness SDK wheel filename, distribution version, SHA-256, source commit, and module origin are pinned
  - Raw evidence output directory is under ignored .local-test-evidence
revision: 3
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
| 2 | 通过公开 API 追加 primary evidence，创建 A/B TaskScope，写入 checkpoint 与 queue turn；读取 pre-operation raw integrity manifest 和 HUMAN composition snapshot。 | 所有原始记录 append-only，且有 immutable receipt/ref/hash；manifest 含各 raw 集合的 row count/content hash，不以 projection/cache hash 代替；fresh HUMAN lane 注册真实 binding/recovery/scheduler/runtime authority，legacy/future/invalid lane 不注册。 |
| 2a | 在 Manual 模式调用一次性 append，再执行 `propose_manual_binding`，随后用 wrong actor、deny、allow、expired nonce 和 exact replay 分别调用 `decide_manual_binding`。 | 一次性 append 只返回 `workspace_binding_manual_authorization_required` 与 challenge ref；proposal/challenge/raw interaction/nonce/decision/grant/receipt 全部 durable。只有同一认证 actor 的未过期 allow 可 CAS append；deny/expired/wrong actor 稳定拒绝；exact replay 返回原结果。 |
| 2b | 对当前 foreground Run 读取 Auto snapshot并追加 configured workspace 内 root，再尝试 queued payload 自报 mode/root、stale generation、非当前 Run、workspace 外 root。 | Auto 只使用 durable current Run 的 exact scope/binding/context/configuration/generation facts；合法 root CAS append，其他输入全部稳定 fail closed，客户端不能选择 authority mode。 |
| 3 | 依次用旧 Session create/switch/rename/delete 直连 primary authority，包含重放和 lost-ACK retry。 | 每个操作均返回稳定 `human_memory_primary_authority_immutable`；primary ref、raw row count/content hash 和既有内容不变。 |
| 4 | 分别用 legacy epoch 和 future epoch 打开公开 Host 写入路径，随后重启 fresh 目录。 | 非兼容 epoch 在任何写入前稳定 fail closed，不自动迁移不建第二 primary；fresh 目录重启后仍可读写。 |
| 5 | 关闭 ingress，在 projection/search/queue/execution 待处理项存在时依次注入 crash/lost-ACK/busy-checkpoint，执行 drain-or-park、WAL checkpoint 和 manifest，再冷重启恢复；独立使 FTS5 unavailable 并调用搜索。 | fence 后新 ingress 稳定拒绝；已入账项要么 drain 要么有 park receipt/ref；busy/outbox-gap/hash-mismatch 均 fail closed；重启可重放且无丢失/重复 SDK start；FTS5 缺失返回冻结 `human_memory_search_unavailable`，不降级为未经 permission filter 的模糊扫描。 |
| 6 | 在步骤 2–5 前后重读 raw integrity manifest，对既有 raw 集合比较 row count 与 content hash，并逐表核对 v44 execution taxonomy。 | 既有 raw rows/count/content hash 不回退不改写；claimed payload、preparation、start intent、start observation、reconciliation、terminal lineage 永久保留并进入 A/C taxonomy；generation/lease/current heads 按 B invariant 校验；新 recovery/control/execution receipt 只 append；只有 D 类派生 cache 可重建。 |
| 7 | 在普通恢复被故障阻断时调用 emergency read/export，关闭并重开 Host 后再次导出。 | export 只读且包含 canonical refs、row counts、content hashes、format/manifest receipt 和 v44 execution audit lineage；两次导出在同一 canonical revision 上 byte-identical；不包含 API key/token/provider private payload 或其他认证材料。 |
| 8 | 对 critical + affected 公开入口各发一个有效请求，并对 wrong principal、request-selected authority、Manual/Auto 违规、stale generation、FTS unavailable、legacy/future epoch 和旧 CRUD fence 等冻结失败边界发必要负例：primary、TaskScope、Manual/Auto binding、queue/control、scheduler/runtime audit、recovery/export、legacy CRUD fence。 | 每个入口返回 typed success 或冻结 stable error code，ref/receipt/hash 完整；无 5xx、无第二 primary、无 authority 扩大、无 fixture port 替代 production composition。 |
| 9 | 在 clean environment 安装 exact candidate Harness wheel，只从 package root 读取 distribution metadata/public contract，并运行 fresh routed start、duplicate delivery 和 cold restart。 | filename/version/SHA-256/source commit/module origin/public contract manifest 全匹配；旧 wheel、错误 hash、source checkout 或 private import 均 fail closed；同一 start identity 只恢复同一 SDK Run。 |

## 决定性证据

- public-only adapter path/SHA-256 与公开调用清单；exact wheel identity/origin/public contract manifest；fresh/cold process incarnation；HUMAN port presence/absence；Manual challenge/decision 与 Auto current-Run receipts；compatibility/fence/drain/park/WAL/export receipts；critical+affected API status/code/ref/hash 矩阵。
- 每个阶段的 raw manifest（row count/content hash）、v44 per-table taxonomy/root、append-only delta、duplicate/lost-ACK replay 结果，以及旧 CRUD 稳定拒绝证据。
