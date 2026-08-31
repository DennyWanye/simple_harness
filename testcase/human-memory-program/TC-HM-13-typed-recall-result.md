---
id: TC-HM-13
purpose: Verify typed recall source bindings, result-bound disclosure, atomic conflict confirmation, and fail-closed replay
status: active
surface: api
type: scripted
obligations: [HM-TO-A4, HM-TO-A7, HM-TO-A8, HM-TO-R2, HM-TO-R3, HM-TO-R4, HM-TO-R5]
tags: [human-memory, typed-recall, decision-result, conflict-group, privacy, replay, fault-recovery]
entrypoint: clean public Harness and Memory SDK consumers
revision: 1
---

# TC-HM-13 — Typed Recall Decision/Result 与结果绑定披露

## 冻结输入

- 只通过已安装 candidate wheels 的公共导出和 Memory 公共 port 执行；禁止 import 私有 repository、直接 SQL 或读取实现对象。
- fixture：`fixtures/typed-recall-v1.json`，SHA-256
  `3c1d17551aa6056dec51e4c7b8fae026d8da2f0f0c28d4c54b08e0cbb97af6d3`。
- fault seam：`fixtures/fault-matrix.json` 的 `typed-recall-decision-result` lane；lane 或 runner 不存在时必须记
  `NOT_RUN/BLOCKED`，不得以邻近单测替代。

## 步骤与预期

| 步骤 | 黑盒操作 | 冻结预期 |
|---:|---|---|
| 1 | 在 clean consumer 中 canonical round-trip long-term-only、short-horizon-only、mixed sources 与 atomic confirmation；再输入 v3 wire、认知项缺 revision、short-horizon 伪 revision。 | v4 合法载荷 byte-stable；各 source 语义不被平行数组伪造。v3 和非法 discriminant 在调用 Memory 或读取 candidate 前 fail closed。 |
| 2 | 用 mixed 与 short-only plan 执行 recall，并冻结 decision/result/item bytes 与 hash。 | `RecallDecisionV4` 的有序 source binding 与 `TypedRecallResultV1` 一一对应；canonical source hash、公开 payload hash、item/result hash 相互绑定且不得混用；classification、evidence manifest、source TaskScope set、active TaskScope 与 cross-scope 均可审计。 |
| 3 | 依次用裸 `memory_id/revision/chunk_ref`、错误 result hash、错误 item/page coordinate、过期 result 与正确 result-bound 坐标请求 page-in。 | 裸 ref 公共入口不存在或稳定拒绝且 payload/query 均为 0；任何 binding/hash/expiry 错误零内容。只有正确 `result_id+result_hash+coordinates+bounds` 返回 hash-identical page/receipt。 |
| 4 | 执行 `contested-dependent-complete` 与 `contested-dependent-partial`。随后尝试把完整组成员放入普通 selected、单独 page-in 一侧或改序。 | 完整且两侧均 eligible 的 active group 只产生一个有序原子 confirmation；成员普通选择数为 0。任一侧不可见/过期/suppressed/hash 漂移时整组、两侧、candidate count 和冲突存在性均不披露。拆组、改序、单边 page-in 全拒绝。 |
| 5 | 穷举 fixture 的 lifecycle、epistemic×verification、recipient×purpose×privacy、attribute floor、有效期、current head、suppression 和 Short-Horizon source chain。 | 仅冻结白名单组合可进入 rank；所有未列组合 fail closed。资格门在 lane/cap/rank 前生效；被拒绝项的 ref、数量和内容不进入普通输出或普通 audit。 |
| 6 | 对 EVENT/ENVIRONMENT/TASK_PHASE、EXACT/TEMPORAL/GRAPH 逐项及组合执行；随后 exact replay 与 conflicting replay。 | 每个 unsupported member 使整 plan `INVALID_PLAN`，candidate query 始终 0；exact replay byte-identical 且 query=0；same key/different hash 为 `IDEMPOTENCY_CONFLICT` 且 query=0。 |
| 7 | 从 result 构造 recalled Context fragments 与 snapshot manifest，分别执行 suppression-first、receipt-first，并用新的 provider continuation/attempt 重试。 | recalled fragment 必须携带公开 payload及 decision/result/item/page-or-use binding；非 recall fragment 禁带。suppression-first 返回 `RECALL_AUTHORITY_STALE` 零内容；receipt-first 只允许 exact immutable snapshot/attempt 一次，之后所有新 attempt 需重新授权并失败。assembly decision 绑定 fragment `(id,hash)`。 |
| 8 | 在 decision header、items 间、result 前后、terminal 前及 commit-before-ACK 逐 seam fault，跨进程重启后 open/rebuild/replay。 | commit 前最多保留 start attempt，无半 decision/result/content；commit 后 ACK 丢失 exact replay，不重复 candidate/state。open/rebuild 重算全部 header/item/group/content/payload/evidence/terminal hash，任一漂移 fail closed。 |
| 9 | 运行候选 cap、RRF lane 缺失/降级、稳定排序、dedupe、ASCII/CJK/emoji/escaping 的 exact-limit/limit+1、oversize-first+smaller-fit 和 public deadline 边界。 | gate 先于 cap；排序和去重遵守 fixture；bytes/tokens 以 canonical JSON 保守计算且单项不截断；较大项放不下仍尝试较小项。deadline 覆盖入口到返回，超时零内容并留下 durable terminal/close-drain 证据。 |
| 10 | 检查质量报告状态。 | 本 fixture 的确定性 protocol/eligibility PASS 不得改写真实模型 corpus authority；未完成人工 review/freeze 时 required-type、overall semantic quality 继续 `NOT_RUN/BLOCKED`。 |

## 决定性证据

- exact wheel/version/commit hash、consumer stdout、canonical bytes/hash、candidate-access trap/SQL trace、decision/result/page/use receipts；
- 每个 fault seam 的 before/after durable state hash、restart identity、candidate query count 与 terminal reason；
- 每个 matrix cell、cap/budget/deadline case 的逐项 outcome，不接受只报聚合 PASS；
- 普通输出、trace、ContextSnapshot 与 page receipt 的 forbidden canary 零命中扫描。

## 失败判定

- 任意裸 ref 读取成功、source hash 与 public payload hash 混用、冲突组单边泄露、unsupported plan 开始 candidate 查询、
  stale result 进入新 Provider attempt、半状态/非 exact replay、或把本 deterministic fixture 宣称为真实模型质量 PASS，均为 FAIL。
