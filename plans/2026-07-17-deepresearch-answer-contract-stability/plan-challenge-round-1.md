# Plan Challenge Round 1

> 结果：`VERDICT: FAIL`  
> 状态：2026-07-18 已逐项闭环，供 Round 2 只挑战新增/未闭环问题。

| Round 1 blocker | 闭环位置 |
|---|---|
| native 同步 terminal projector 无法 async 读取 manifest | `plan.md` T1/T10：新增 version capability + async `TerminalCommitProjectionRegistry`，明确 `_drive()` 调用点与 v1～v5 兼容 |
| 节点内 page 完成不等于 durable checkpoint | T4/T5：每页 stable effect + `PageExtractionResultV1` blob/journal；独立 admission node 按 ordinal 构造 fact head |
| `context_visibility` 只写入仍污染 FTS/vector/backfill/context | T10：state DB v19、trigger、四路 Retriever、VectorWorker、Context OS、summarizer 全路径过滤 |
| continuation single-head 无 durable owner | T12：workflow schema v4 `workflow_research_continuation_heads`、BEGIN IMMEDIATE 创建/复用、迁移/retention |
| monotonic deadline 不能跨重启 | `v6-contracts.md` §4 + T5：wall guard/remaining budget/fence CAS 后重建 local lease、effect replay 矩阵 |
| official direct path 无 URL/source owner | `v6-contracts.md` §3 + T5：通用 authority registry/resolver、source search、final URL verifier、禁止 legacy query pack/oracle |
| RegisteredBlobStore 只验全局存在不验 run owner | T9/T10：same-run/pending/checkpoint authorization、transactional promotion/cleanup、continuation closure |
| delivery handler return 被忽略 | T10：typed `DeliveryAttemptResult`、outbox disposition/backoff/claim reclaim、required aggregate |
| 四 requirement schema 未冻结 | `v6-contracts.md` §1～§2 + T2：exact fields、ID/hash、merge lattice、stable errors |
| network >120s 环境故障分类、final cardinality 歧义 | T13 timing calibration 与 T10 `projection_kind=final_assistant` 明确统计边界；下一轮继续审查环境 reason code |

本轮确认的结构问题均按正式扩展点/持久化 owner 解决，没有引入 v6 `if` 特判、第二 research DB 或 UI 业务 owner。
