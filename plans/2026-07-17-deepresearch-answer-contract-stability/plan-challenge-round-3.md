# Plan Challenge Round 3

> 结果：`VERDICT: FAIL`  
> 状态：2026-07-18 已逐项闭环，供下一轮只挑战新增/未闭环问题。

| Round 3 blocker | 闭环位置 |
|---|---|
| deadline/resource budget 只有算法、缺 durable schema owner | `v6-contracts.md` §4：deadline、budget、reservation exact DDL 与 begin/commit/reconcile 事务顺序 |
| page extraction/outcome schema 与取消来源不精确 | §6 + T4/T5：exact `PageExtractionResultV1`/`PageAttemptOutcomeV1`、business typed signal、runner cancellation 原样传播、partial checkpoint 后再 settle |
| terminal commit request/projection、engine tuple 与 refs merge 不精确 | §7 + T10：exact request/projection/manifest、native 单一 engine tuple、async 跳过旧 projector、ref set merge、一次 commit |
| logical intent 和 physical delivery 混为一个层次 | §7/T9/T10：`intent_specs[].delivery_specs[]` exact schema、固定物理映射、materializer 禁止隐式扩展 channel |
| CanonicalContentResolver 构造时序形成循环依赖 | T10：先建 immutable runtime services，再把 services 传入 `delivery_handler_factory`，最后构造 outbox/service/runner |
| delivery aggregate 无唯一 reducer、query owner 与 UI 暴露路径 | §8/T10：required-row reducer、状态优先级、ManifestIntentReader、run detail/history/CAS notification 同一 DTO |
| visibility 漏掉 snapshot 与 facts/chunk backfill | §9/T10：完整 reader allowlist + repo-wide SQL call-site audit |
| 未默认开启前无法走真实 UI，默认切换后又没有复验 | §10/Q1/T13：隔离 dev env override 真 UI ingress；冻结默认 v6 后移除 override 并重跑 identity/recovery/UI/reconnect/regression |
| v6 exact contracts 仍缺 page/evidence/claim/terminal 对象 | §6～§8：对象 exact keys、ID/hash、状态域、owner 与 byte-roundtrip 约束 |
| SQLite DDL/FK/index 和 delivery retry/backoff 仍需临场猜测 | §4.1/§8、T1/T12：effect attempt 列/FK/index/fingerprint、delivery exact ALTER/index/claim lease/backoff、continuation index |

本轮闭环后，v6 的 runtime dependency、durable page attempt、terminal commit、delivery、visibility 和开发 UI 入口均有明确 owner、schema、调用顺序与复验门；下一轮不得重复已回答问题，除非指出新的内部矛盾或真实代码不可达点。
