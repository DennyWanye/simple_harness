---
id: TC-HM-13
purpose: Verify typed recall writes, source bindings, result-bound disclosure, atomic conflict confirmation, and fail-closed replay
status: active
surface: api
type: scripted
obligations: [HM-TO-A4, HM-TO-A7, HM-TO-A8, HM-TO-R2, HM-TO-R3, HM-TO-R4, HM-TO-R5]
tags: [human-memory, typed-recall, decision-result, conflict-group, privacy, replay, fault-recovery]
entrypoint: clean public Harness and Memory SDK consumers
preconditions:
  - Exact candidate Harness SDK wheel path and SHA-256 are available
  - Exact candidate Memory SDK wheel path and SHA-256 are available
  - Candidate Memory root exports the public black-box fixture callable
  - Fresh isolated artifact directory is outside tracked testcase files
revision: 2
---

# TC-HM-13 rev2 — Typed Recall 写入、Decision/Result 与结果绑定披露

## Authority 与固定输入

- 只允许从 candidate wheels 的 `simple_harness_sdk` / `simple_harness_memory` 包根调用公开导出；禁止 source checkout
  import、私有 submodule、repository object、直接 SQL 和读取实现 diff。
- 主 fixture：`fixtures/typed-recall-v2.json`，revision 2，SHA-256
  `71d87d7c9c67faf45b8e1f16bbf9a3b0d61596b40a186d0dd27f304241b6bddd`。
- fault fixture：`fixtures/fault-matrix.json` 的 `typed-recall-decision-result` lane，SHA-256
  `6882fe06cb561cb8cd1fbe1f0ad8fff1589218ffa295c3d4d12cc36283e1446c`。
- official runner：`runners/run_typed_recall_public_consumer.py`，SHA-256
  `eda919b10601d651aa4720fcec5b29bccdd4aa9b559d222081277dd021bb8815`。
- runner/候选 callable 或任一 exact wheel 缺失时，本用例是 `NOT_RUN/BLOCKED`，不得以自检、邻近单测或私有探针替代。

## 先验自检与正式命令

从 `simple_harness` testcase worktree 根执行：

```bash
python3 testcase/human-memory-program/runners/run_typed_recall_public_consumer.py \
  --fixture testcase/human-memory-program/fixtures/typed-recall-v2.json \
  --self-check
```

该命令只证明 fixture 的独立预计算值一致，不证明产品行为。正式黑盒执行：

```bash
python3 testcase/human-memory-program/runners/run_typed_recall_public_consumer.py \
  --fixture testcase/human-memory-program/fixtures/typed-recall-v2.json \
  --harness-wheel /absolute/path/to/simple_harness_sdk-candidate.whl \
  --memory-wheel /absolute/path/to/simple_harness_memory-candidate.whl \
  --consumer-entrypoint simple_harness_memory:run_typed_recall_black_box_fixture \
  --artifact-dir /absolute/path/to/.local-test-evidence/typed-recall-task5
```

runner 会建立隔离 venv、`pip install --no-deps` exact wheels、清空 `PYTHONPATH` 并从临时目录调用 package root。
PASS 必须产生：`protocol.json`、`conflict-state.json`、`eligibility.json`、`current-use.json`、
`unsupported-replay.json`、`selection-budget.json`、`fault-recovery.json` 和 `evidence-index.json`；result envelope
必须回绑 fixture revision/hash。原始 artifacts 只留 `.local-test-evidence/`，不得提交 Git。

## 命名 lane 与冻结步骤

| Lane | 黑盒操作 | 冻结预期 |
|---|---|---|
| `protocol` | canonical round-trip long-only、short-only、mixed、atomic confirmation；输入 v3 wire、认知项缺 revision、short 项伪 revision。 | 合法 v4 byte-stable，ordinal 从 1 连续；v3/非法 discriminant 在 Memory candidate access 前拒绝，strict-v3-rejected=true。 |
| `conflict-state` | 按 fixture 对 exact current `r7` 执行 distinct-content/distinct-evidence CONTEST；再逐项执行 no-evidence、same-content、stale-target、shared-evidence、active-group、nested、cross-principal；对 active group 分别 select-incumbent、replacement、SUPERSEDE、SUPPRESS resolution 并 replay。 | create 同一事务得到 challenger head `r8`、两项有序 member、group/relation/decision/receipt/head CAS，hash 与 fixture 相同；reject 全部 state_delta=0；授权 resolution 只创建 `r9`，不回滚 head，旧 group 永不 active/revive；commit replay 不重复。 |
| `result-page` | 执行 mixed/short-only recall，冻结 decision/result/item bytes/hash；依次用裸 ref、错误 result hash、错误 coordinate、过期 result 与正确 result binding page-in。 | source hash 与 public payload hash 独立回绑；裸 ref 不存在或零读取，所有错误 binding 零内容；正确 `result_id+result_hash+coordinate+bounds` 才返回 hash-identical page/receipt。 |
| `conflict-recall` | 执行 `contested-dependent-complete` 与 `contested-dependent-partial`，再拆组、改序和单边 page-in。 | 完整且两侧 eligible 只返回一个有序 atomic confirmation，ordinary member count=0；任一侧失败则整组、双方、数量、冲突存在性零披露。 |
| `eligibility` | 逐 cell 执行 lifecycle、epistemic×verification、valid-from/until limit/limit+1、current/stale head、suppression、ordinary conflict status、Short-Horizon registration/classification/evidence/occurred/expiry、Procedure applicability、Prospective trigger/signal、recipient×purpose×privacy 与 attribute floor。 | 每个 cell 都有独立 outcome/reason/pre-rank counter/canary；只允许显式白名单，未列组合 fail closed。资格门在 lane/cap/rank 前；拒绝项 ref/count/content 不进普通 output/audit。 |
| `unsupported-replay` | 执行单一和 combined unsupported payload；按冻结顺序混入 EVENT/ENVIRONMENT/TASK_PHASE 与 EXACT/TEMPORAL/GRAPH。对 base request 的 protocol/principal/run/context hash+revision/plan id+hash/disclosure hash/recipient/purpose/四个 budget 字段逐一只改一项，并 exact/conflicting replay。 | combined reason vector 顺序完全等于 fixture；全部 candidate_query_started=false。14 个 one-field mutation 的 domain-separated hash 必须逐项命中且互异；exact replay bytes 相同/query=0，conflict 为 `IDEMPOTENCY_CONFLICT`/query=0。 |
| `current-use` | 对 suppression、revoke、supersede、contest、classification、policy hash、Short-Horizon invalidation/expiry/cleanup、result/context expiry逐 event 执行 old-result use；再跑 suppression-first、receipt-first、same-attempt duplicate、new attempt、new continuation、wrong snapshot。 | 每个 state-changing event 的 epoch/policy/expiry 前后值与 fixture 一致且 old result 失效；same attempt 只 exact replay 不二次消费；新 attempt/continuation 必须重新授权。两个并发顺序遵守冻结线性化。 |
| `selection-budget` | 用固定 lane ranks 计算 RRF，执行 equal-score tie、32/33 cap、四组 exact/cross-source dedupe、五类 minimal projections；对 literal ASCII/CJK/emoji/escaping canonical JSON 跑 exact limit/limit+1，并跑 oversize-first+smaller-fit 和 2s deadline。 | score 精确到 12 位并按 `[cand-d,cand-a,cand-b,cand-c]`；cap-033 丢弃；dedupe count 与 fixture 相同；projection hash/字段恰好命中。每项 bytes/codepoints/tokens/hash 和 select/skip 命中预计算值，单项不截断；deadline 入口到返回，超时零内容并 durable terminal/close-drain。 |
| `fault-recovery` | 按 fault lane 在 group/member/relation/decision/receipt/head-CAS、resolution/head-CAS、decision/result/terminal、authority event/head CAS、context-use receipt、两种并发点和 commit-before-ACK 注入；重启 open/rebuild/replay。 | 每项 all-old 或 all-new；final recall commit 前只可留 start attempt；commit 后 exact replay；所有 header/item/group/content/payload/evidence/terminal hash 重算，任一漂移 fail closed。 |

## 每 lane 证据约束

`evidence-index.json` 必须逐 lane 列出 fixture cell ID、product outcome、reason、candidate query count、before/after
durable hash、artifact relative path 与 artifact SHA-256。不得只保存一个 `matrix_passed=true` 聚合值。`HM-S13A/HM-S13B`
的 required business facts 必须引用该 index hash，并分别证明所有 delivery / negative-safety cells。

## 质量门边界

本 deterministic fixture 即使所有 lane PASS，也只证明 Task 5 公共协议、状态、资格与预算行为。真实 200+ query corpus
尚未完成独立人工 review/freeze、两轮真实主模型执行和阈值证据时，required-type 与 overall semantic quality 永远保持
`NOT_RUN/BLOCKED`。

## 失败判定

- 任意私有 import/SQL 替代 public consumer、ordinal 0、假 hash、CONTEST 非原子或不可 resolution、旧 group 复活；
- eligibility 聚合 PASS 但缺 cell、combined unsupported 静默丢项、request hash 漏一个字段、stale result 被新 attempt 使用；
- 复用产品 scorer/accountant 作 oracle、Unicode/escaping 计数错误、裸 ref 读取、半状态/非 exact replay；
- runner 缺失仍判 PASS，或把 deterministic fixture 宣称为真实模型质量 PASS。

任一发生即 FAIL。
