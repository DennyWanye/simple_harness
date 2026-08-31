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
  - Exact candidate Harness SDK source commit is available
  - Exact candidate Memory SDK wheel path and SHA-256 are available
  - Exact candidate Memory SDK source commit is available
  - Candidate Memory root exports the public black-box fixture callable
  - Fresh isolated artifact directory is outside tracked testcase files
revision: 3
---

# TC-HM-13 rev3 — Typed Recall 写入、Decision/Result 与结果绑定披露

## Authority 与固定输入

- 只允许从 candidate wheels 的 `simple_harness` / `simple_harness_memory` 包根调用公开导出；禁止 source checkout
  import、私有 submodule、repository object、直接 SQL 和读取实现 diff。
- 主 fixture：`fixtures/typed-recall-v3.json`，revision 3，SHA-256
  `92202927c9817c8a1fc2eed2f26751ac8971b52452144bebc5e8c545e9a79e10`。
- fault fixture：`fixtures/fault-matrix.json` 的 `typed-recall-decision-result` lane，SHA-256
  `c67881ae20a3f6b442f1ac46db9e6e9a472edc3ec09079e8a42ef219e9b6bc6b`。
- official runner：`runners/run_typed_recall_public_consumer.py`，SHA-256
  `85cd4d61005d818fa6dca6bdea451606b2e8841af6a671dc269dc2ee6e724a1f`。
- Harness candidate 固定为 `simple-harness-sdk==0.7.0`、source commit `fb491574db8bb4d19d8a7f9df0c72ae460bb08f4`、wheel SHA-256
  `36522c4abce5ba598e084a9c45aca0fb32ded2b9e8d9bc3eb8c28694eb39b99f`；Memory candidate 固定为 `simple-harness-memory-sdk==0.6.0`、source commit `9c79fa7ed96214aac7de93a11970e02891afedae`、wheel SHA-256 `cd324e68aa851e0cb7940b44bfe0bbf1b3a5cbb33a6803e035ab503e1760de1c`。任一 identity 缺失或不匹配均不得执行或 PASS。
- runner/候选 callable 或任一 exact wheel 缺失时，本用例是 `NOT_RUN/BLOCKED`，不得以自检、邻近单测或私有探针替代。

## 先验自检与正式命令

从 `simple_harness` testcase worktree 根执行：

```bash
/Users/denny/projects/simple-harness-memory-sdk-memory-plan/.venv/bin/python testcase/human-memory-program/runners/run_typed_recall_public_consumer.py \
  --fixture testcase/human-memory-program/fixtures/typed-recall-v3.json \
  --self-check
```

该命令只证明 fixture 的独立预计算值一致，不证明产品行为。正式黑盒执行：

```bash
/Users/denny/projects/simple-harness-memory-sdk-memory-plan/.venv/bin/python testcase/human-memory-program/runners/run_typed_recall_public_consumer.py \
  --fixture testcase/human-memory-program/fixtures/typed-recall-v3.json \
  --harness-wheel /tmp/simple-harness-task5-wheel3.MtoX75/simple_harness_sdk-0.7.0-py3-none-any.whl \
  --harness-wheel-sha256 36522c4abce5ba598e084a9c45aca0fb32ded2b9e8d9bc3eb8c28694eb39b99f \
  --harness-source-commit fb491574db8bb4d19d8a7f9df0c72ae460bb08f4 \
  --memory-wheel /tmp/simple-harness-memory-task5-wheel.Eftjjy/simple_harness_memory_sdk-0.6.0-py3-none-any.whl \
  --memory-wheel-sha256 cd324e68aa851e0cb7940b44bfe0bbf1b3a5cbb33a6803e035ab503e1760de1c \
  --memory-source-commit 9c79fa7ed96214aac7de93a11970e02891afedae \
  --consumer-entrypoint simple_harness_memory:run_typed_recall_black_box_fixture \
  --artifact-dir /absolute/path/to/.local-test-evidence/typed-recall-task5
```

runner 会拒绝任何已存在的 artifact run directory，先核对两 wheel SHA-256，再用兼容 Python 与 `uv` 建立隔离 venv、从 exact candidate wheel 路径安装候选并解析其声明依赖、清空 `PYTHONPATH`，记录 distribution/version/module origin/source commit 后才从临时目录调用 package root。公开 callable/import 缺失为 `NOT_RUN/BLOCKED`；callable 已执行后 assertion、exception 或非零退出一律 `FAIL`。
PASS 必须产生：`protocol.json`、`conflict-state.json`、`eligibility.json`、`current-use.json`、
`unsupported-replay.json`、`selection-budget.json`、`fault-recovery.json` 和 `evidence-index.json`；result envelope
必须回绑 fixture revision/hash。runner 必须解析全部 lane JSON 与 `evidence-index.json`，要求 401 个冻结 cell 的 exact set/unique ID/outcome/reason/query-count/canary/observed values 一致，重算逐 artifact/index SHA-256，并校验 index path/hash/timestamp、wheel identity 与 run freshness；旧文件、空文件、聚合 PASS、篡改 hash 或 pre-existing directory 均 `FAIL`。原始 artifacts 只留 `.local-test-evidence/`，不得提交 Git。

## 命名 lane 与冻结步骤

| Lane | 黑盒操作 | 冻结预期 |
|---|---|---|
| `protocol` | canonical round-trip long-only、short-only、mixed、atomic confirmation；输入 v3 wire、认知项缺 revision、short 项伪 revision。 | 合法 v4 byte-stable，ordinal 从 1 连续；v3/非法 discriminant 在 Memory candidate access 前拒绝，strict-v3-rejected=true。 |
| `conflict-state` | 按 fixture 对 exact current `r7` 执行 distinct-content/distinct-evidence CONTEST；再逐项执行 no-evidence、same-content、stale-target、shared-evidence、active-group、nested、cross-principal、cross-memory、1-member、3-member；对 active group 分别 select-incumbent、replacement、SUPERSEDE、SUPPRESS resolution 并 replay/reopen tampered rows。 | create 同一事务得到 challenger head `r8`、恰好两项同 principal/memory 的有序 member、group/relation/decision/receipt/head CAS，hash 与 fixture 相同；reject 全部 state_delta=0；1/3-member 或 cross-memory durable tamper 在 reopen/rebuild 时 fail closed 且零披露；授权 resolution 只创建 `r9`，旧 group 永不 active/revive。 |
| `result-page` | 执行 mixed/short-only recall，冻结 decision/result/item bytes/hash；依次用裸 ref、错误 result hash、错误 coordinate、过期 result 与正确 result binding page-in。 | source hash 与 public payload hash 独立回绑；裸 ref 不存在或零读取，所有错误 binding 零内容；正确 `result_id+result_hash+coordinate+bounds` 才返回 hash-identical page/receipt。 |
| `conflict-recall` | 执行 `contested-dependent-complete` 与 `contested-dependent-partial`，再拆组、改序和单边 page-in。 | 完整且两侧 eligible 只返回一个有序 atomic confirmation，ordinary member count=0；任一侧失败则整组、双方、数量、冲突存在性零披露。 |
| `eligibility` | 逐 cell 执行 lifecycle、epistemic×verification、valid-from/until limit/limit+1 与 `valid_until=null`、current/stale head、suppression、ordinary conflict status、Short-Horizon registration/classification/evidence/occurred/expiry、Procedure applicability、Prospective trigger/signal、recipient×purpose×privacy，以及 HOUSEHOLD/TASK_COLLABORATOR 全部六类 attribute floor。 | 每个 cell 都有独立 outcome/reason/pre-rank counter/canary；只允许显式白名单，未列组合 fail closed。无上界 `valid_until=null` 在其他 gate 满足时 eligible；HOUSEHOLD 不得以 PUBLIC 普通 allow 绕过敏感属性 floor。 |
| `unsupported-replay` | 执行单一和 combined unsupported payload；按冻结顺序混入 EVENT/ENVIRONMENT/TASK_PHASE 与 EXACT/TEMPORAL/GRAPH。对 base request 的 protocol/principal/run/context hash+revision/plan id+hash/disclosure hash/recipient/purpose/四个 budget 字段逐一只改一项，并 exact/conflicting replay。 | combined reason vector 顺序完全等于 fixture；全部 candidate_query_started=false。14 个 one-field mutation 的 domain-separated hash 必须逐项命中且互异；exact replay bytes 相同/query=0，conflict 为 `IDEMPOTENCY_CONFLICT`/query=0。 |
| `current-use` | 对 suppression、revoke、supersede、contest、classification、policy hash、Short-Horizon invalidation/expiry/cleanup、result/context expiry逐 event 执行 old-result use；再跑 suppression-first、receipt-first、same-attempt duplicate、new attempt、new continuation、wrong snapshot。 | immutable receipt canonical hash 必须绑定 before/after epoch+policy、evaluated/authorized/context/use/expiry time、decision/result/item hashes、snapshot、run/turn/continuation/provider-attempt 与 outcome；11 events 和 receipt-first/replay 使用 fixture 已知答案独立重算。 |
| `selection-budget` | 用固定 lane ranks 计算 RRF，逐级执行 score、matched-lane-count、descending typed-source-time、source-kind、memory-type-or-empty、source-ref、revision-or-zero tie；执行 vector unavailable/no-request degrade、32/33 cap、四组 dedupe、五类 rich-source→minimal projection；再跑 literal budget/greedy/deadline。 | 每个 tie-break 都由独立 pair 决定；新时间优先；vector 只降级被请求 lane并留稳定 audit，绝不注入未授权 fallback；rich source 的 evidence/classification/conflict/cross-scope/source/extra typed 字段全部被精确剥离。 |
| `fault-recovery` | 按 fault lane 在 group/member/relation/decision/receipt/head-CAS、resolution/head-CAS、decision/result/terminal、authority event/head CAS、context-use receipt、两种并发点和 commit-before-ACK 注入；重启 open/rebuild/replay。 | 每项 all-old 或 all-new；final recall commit 前只可留 start attempt；commit 后 exact replay；所有 header/item/group/content/payload/evidence/terminal hash 重算，任一漂移 fail closed。 |

## 每 lane 证据约束

`evidence-index.json` 必须逐 lane 列出 fixture cell ID、product outcome、reason、candidate query count、forbidden canary hits、before/after
durable hash、artifact relative path、artifact SHA-256 与 lane/index RFC3339 时间。index 必须回绑 fresh root run、fixture pins、两 wheel SHA/source commit、installed distribution/version/module origin；runner 从 fresh invocation timestamp 与文件 mtime 证明非旧产物。不得只保存一个 `matrix_passed=true` 聚合值。`HM-S13A/HM-S13B`
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
