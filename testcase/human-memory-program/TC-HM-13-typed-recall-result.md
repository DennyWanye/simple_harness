---
id: TC-HM-13
purpose: Verify typed recall writes, source bindings, result-bound disclosure, atomic conflict confirmation, and fail-closed replay
status: active
surface: api
type: scripted
obligations: [HM-TO-A4, HM-TO-A7, HM-TO-A8, HM-TO-R2, HM-TO-R3, HM-TO-R4, HM-TO-R5, S5A-TO-MEMORY-SURFACE]
tags: [human-memory, typed-recall, decision-result, conflict-group, privacy, replay, fault-recovery]
entrypoint: clean public Harness and Memory SDK consumers
preconditions:
  - Exact candidate Harness SDK wheel path and SHA-256 are available
  - Exact candidate Harness SDK source commit is available
  - Exact candidate Memory SDK wheel path and SHA-256 are available
  - Exact candidate Memory SDK source commit is available
  - Validation-side public Manager adapter covers the exact frozen 391-cell set
  - Exact-source integration/fault evidence covers the exact frozen 10-cell set
  - Fresh isolated artifact directory is outside tracked testcase files
revision: 5
---

# TC-HM-13 rev5 — Typed Recall 写入、Decision/Result 与结果绑定披露

## Authority 与固定输入

- 只允许从 candidate wheels 的 `simple_harness` / `simple_harness_memory` 包根调用公开导出；禁止 source checkout
  import、私有 submodule、repository object、直接 SQL 和读取实现 diff。
- 主 fixture：`fixtures/typed-recall-v3.json`，revision 4，SHA-256
  `02419918d27237faf2af5e6d75f4180275c1e816cbad183c29ed871f508be649`。
- 执行分层 fixture：`fixtures/typed-recall-execution-layers-v1.json`，revision 2，SHA-256
  `594189edb4c46ff1c52a778c1caeef9a94d324bfd67f778ce61a3ec1c5f63d8a`。它冻结 401 个 cell
  的 exact union：391 个 clean-wheel public Manager cell 与 10 个 exact-source corruption/fault cell；两层必须分别
  PASS，任何一层的静态 digest、自检或另一层 PASS 都不能代替本层产品证据。
- fault fixture：`fixtures/fault-matrix.json` 的 `typed-recall-decision-result` lane，SHA-256
  `b4dcb2f39a2e5c2f7afeeb1dd496aa94587fe075c8772ea44fab880115bc74da`。
- official oracle/combined-artifact validator：`runners/run_typed_recall_public_consumer.py`。该 runner 不得再要求
  Memory 包根提供测试专用 callable；clean-wheel 产品操作由验证侧 adapter 通过公开 builder/Manager 驱动。当前代码身份由 execution request 的 validation_code_sha256 与 summary 的 oracle_code_sha256 绑定。旧 runner hash 仅属 rev4 历史。
- Harness candidate 固定为 `simple-harness-sdk==0.7.2`、source commit `2b8428465cbd41032ba024a0b7199183161f5ecd`、wheel SHA-256
  `53bded3fea87168e5d2ad9e49fea5f99e1c1edb1d6077b2a52dd62716692f9ed`；Memory candidate 固定为 `simple-harness-memory-sdk==0.6.3`、source commit `2f3d73814fe6a884e0458d87567b918c5863033e`、wheel SHA-256 `6b20ae5bff6c3ecfe1108ccaff9bb41c4dc6a3b98bb754dac2c418673ab77c78`。任一 identity 缺失或不匹配均不得执行或 PASS。
- 391-cell adapter、10-cell exact-source evidence 或任一 exact wheel 缺失时，本用例是 `NOT_RUN/BLOCKED`；不得以
  fixture 自检、包根 capability probe、邻近单测、私有 SQL 或产品测试 helper 替代。

## rev5 执行与历史 oracle 边界

用户批准已先于修改固化于 `38356e7f`，oracle/14 攻击映射在首次执行前提交 `e46edaa0`。
主 fixture 保留原始场景、负例、数值和所有 IDs；`approved_oracle` 是本次 §3–4 修订入口。
`legacy_commitment_sections` 中旧 request/receipt/conflict/state literal hash 仅为历史，不能当产品 gold；
所有未变化段落的独立 hash 位于 `unchanged_sections_sha256`。rev3 合成 artifact validator 不再接纳 rev4 fixture。

独立 oracle/桥回归命令与本轮真实执行命令见 [首批执行记录](runners/TYPED-RECALL-A2-FIRST-BATCH.md)。
`--self-check` 的旧401合成路径对 rev4 禁用；独立小型回归不是产品证据。
当前桥 observations 由父进程按受审输入进行业务断言，适配器无权给 PASS。正式401要求不变；
新发现的 domain preimage 差异、冻结时钟缺口、异常路径零读取见证缺口均保留 BLOCKED。
原始 plan/acceptance 未修改。下文正式证据要求仍是完整验收的要求，首批 observation 不能替代。

PASS 必须产生：`protocol.json`、`conflict-state.json`、`eligibility.json`、`current-use.json`、
`unsupported-replay.json`、`selection-budget.json`、`fault-recovery.json` 和 `evidence-index.json`；result envelope
必须回绑 fixture revision/hash。runner 必须解析全部 lane JSON 与 `evidence-index.json`，要求 401 个冻结 cell 的 exact set/unique ID/outcome/reason/query-count/canary/observed values 一致，重算逐 artifact/index SHA-256，并校验 index path/hash/timestamp、wheel identity 与 run freshness。每个 cell 的 before/after 必须来自实际 manifest；按已批准 §4 的预声明 protected tables 独立重算。旧 cell old/new 标签不再作为状态见证。commit 前 final tables 必须保持 old（仅 start/attempt 允许写入）；commit 后 ACK 丢失必须完整 committed terminal + exact replay/零查询/无重复。no-fault 控制先满足独立业务断言，不能统一任选 old/new。旧文件、空文件、聚合 PASS、篡改 hash 或 pre-existing directory 均 `FAIL`。原始 artifacts 只留 `.local-test-evidence/`，不得提交 Git。

## 命名 lane 与冻结步骤

| Lane | 黑盒操作 | 冻结预期 |
|---|---|---|
| `protocol` | canonical round-trip long-only、short-only、mixed、atomic confirmation；输入 v3 wire、认知项缺 revision、short 项伪 revision。 | 合法 v4 byte-stable，ordinal 从 1 连续；v3/非法 discriminant 在 Memory candidate access 前拒绝，strict-v3-rejected=true。 |
| `conflict-state` | 按 fixture 对 exact current `r7` 执行 distinct-content/distinct-evidence CONTEST；再逐项执行 no-evidence、same-content、stale-target、shared-evidence、active-group、nested、cross-principal、cross-memory、1-member、3-member；对 active group 分别 select-incumbent、replacement、SUPERSEDE、SUPPRESS resolution 并 replay/reopen tampered rows。 | create 同一事务得到 challenger head `r8`、恰好两项同 principal/memory 的有序 member、group/relation/decision/receipt/head CAS，hash 与 fixture 相同；reject 全部 state_delta=0；1/3-member 或 cross-memory durable tamper 在 reopen/rebuild 时 fail closed 且零披露；授权 resolution 只创建 `r9`，旧 group 永不 active/revive。 |
| `result-page` | 执行 mixed/short-only recall，冻结 decision/result/item bytes/hash；依次用裸 ref、错误 result hash、错误 coordinate、过期 result 与正确 result binding page-in。 | source hash 与 public payload hash 独立回绑；裸 ref 不存在或零读取，所有错误 binding 零内容；正确 `result_id+result_hash+coordinate+bounds` 才返回 hash-identical page/receipt。 |
| `conflict-recall` | 执行 `contested-dependent-complete` 与 `contested-dependent-partial`，再拆组、改序和单边 page-in。 | 完整且两侧 eligible 只返回一个有序 atomic confirmation，ordinary member count=0；任一侧失败则整组、双方、数量、冲突存在性零披露。 |
| `eligibility` | 逐 cell 执行 lifecycle、epistemic×verification、valid-from/until limit/limit+1 与 `valid_until=null`、current/stale head、suppression、ordinary conflict status、Short-Horizon registration/classification/evidence/occurred/expiry、Procedure applicability、Prospective trigger/signal、recipient×purpose×privacy，以及 HOUSEHOLD/TASK_COLLABORATOR 全部六类 attribute floor。 | 每个 cell 都有独立 outcome/reason/pre-rank counter/canary；只允许显式白名单，未列组合 fail closed。无上界 `valid_until=null` 在其他 gate 满足时 eligible；HOUSEHOLD 不得以 PUBLIC 普通 allow 绕过敏感属性 floor。 |
| `unsupported-replay` | 执行单一和 combined unsupported payload；按冻结顺序混入 EVENT/ENVIRONMENT/TASK_PHASE 与 EXACT/TEMPORAL/GRAPH。对 base request 的 protocol/principal/run/context hash+revision/plan id+hash/disclosure hash/recipient/purpose/四个 budget 字段逐一只改一项，并 exact/conflicting replay。 | combined reason vector 顺序完全等于 fixture；全部 candidate_query_started=false。14 个 one-field mutation 的 domain-separated hash 必须逐项命中且互异；exact replay bytes 相同/query=0，conflict 为 `IDEMPOTENCY_CONFLICT`/query=0。 |
| `current-use` | 对 suppression、revoke、supersede、contest、classification、policy hash、Short-Horizon invalidation/expiry/cleanup、result/context expiry逐 event 执行 old-result use；再跑 suppression-first、receipt-first、same-attempt duplicate、new attempt、new continuation、wrong snapshot。 | 事件场景与公开 use receipt 分开验证；receipt exact fields/domain 依已批准 §3.3，拒绝不能生成假 receipt。11 events 与所有原场景保持不变；canonical 差异未解决时不计 PASS。 |
| `selection-budget` | 用固定 lane ranks 计算 RRF，逐级执行 score、matched-lane-count、descending typed-source-time、source-kind、memory-type-or-empty、source-ref、revision-or-zero tie；执行 vector unavailable/no-request degrade、32/33 cap、四组 dedupe、五类 rich-source→minimal projection；再跑 literal budget/greedy/deadline。 | 每个 tie-break 都由独立 pair 决定；新时间优先；vector 只降级被请求 lane并留稳定 audit，绝不注入未授权 fallback；rich source 的 evidence/classification/conflict/cross-scope/source/extra typed 字段全部被精确剥离。 |
| `fault-recovery` | 按 fault lane 在 group/member/relation/decision/receipt/head-CAS、resolution/head-CAS、decision/result/terminal、authority event/head CAS、context-use receipt、两种并发点和 commit-before-ACK 注入；重启 open/rebuild/replay。 | 严格按预声明事务阶段：commit 前 final tables unchanged（可留 start attempt）；commit 后只接受完整 terminal 和 exact replay；所有 header/item/group/content/payload/evidence/terminal hash 重算，任一漂移 fail closed。 |

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
