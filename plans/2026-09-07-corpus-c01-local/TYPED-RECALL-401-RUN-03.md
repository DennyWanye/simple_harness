# 401 矩阵本机首次完整扫描（run-03，M0.6.22 pin）结果分析

> **独立子代理分析，主代理复核。** 2026-09-07。只读调查：未改仓库代码、未运行原生应用、未重跑矩阵。行号以本机 Host main `082a6dd9` 工作树与 Memory SDK 源 `../simple-harness-memory-sdk` HEAD `78ddf38` 为准；run-03 使用的 fixture 为 rev 10（sha `d43645f9…`，即提交 `5b3fa124` 版本），与工作树 rev 11 相比仅候选身份 pin 不同，disclosure/attribute/epistemic 等 oracle 输入逐字相同（`git diff 5b3fa124 6044f1ec -- fixtures/typed-recall-v3.json` 只有 pin 与 lineage 行）。

## 0. 一句话结论

- **44 个 FAIL 全部同一根因，不是 0.6.20–0.6.22 的回归，也与 09-07「遗忘只针对记忆」决定无关**：Memory 0.6.14（提交 `d7cb3ca`，2026-09-06，"bind ordinary disclosure to its final audience"）要求 `recipient` 与 `intended_audience` 配对；401 消费者适配器至今把所有 recipient 的 `intended_audience` 固定为 `USER_SELF`，于是 HOUSEHOLD / TASK_COLLABORATOR 在**计划层**被 `recall_disclosure_denied` 拒绝（零候选读取），而 oracle 仍按 0.6.13 以前的"只看 recipient"模型期望 `no_recall` / `recall`。归类 **③ adapter 过期 + ② oracle/fixture 缺 audience 轴**；这 44 格在 H073/M0613 十一批正式并集中曾全部 PASS。
- **174 个 BLOCKED 全部是历史上就 BLOCKED 的同类原因**（0.6.13 并集 219 → 现 174，减少的 45 格正好是历史盘点里后继执行器新增的 PASS）；无任何新出现的阻塞类别。30 executor 未实现、96 fixture/前置条件与公共 API 不符、48 oracle 未闭合。
- **source 层 10 格实际逐格 PASS**（已计入总 PASS 183 = public 173 + source 10），`layers.source.status` 仍显示 `NOT_RUN/BLOCKED`、`passed_cells=[]`、无 `reason`，是 bridge runner 的层状态簿记只对 public 分支写 `passed_cells`/`failed_cells`、对 source 分支漏写的不对称；runner 本身没有任何路径把层或整体状态置为 PASS。
- **0.6.23** 对这 44 格无影响（run-04 observe 同样 40+4 FAIL）；受影响的是 10 个 source 格（schema 7.4 描述符，已由 `6044f1ec` 重 pin 修正，待新正式 run 确认）和 3 个 `selection-budget/vector-*` 格（当消费者绑定 embedder 后退化码语义变化，oracle 第 588 行需改）。

## 1. 44 个 FAIL：fixture 期望 vs 实际观测

### 1.1 观测事实（`run-03/public-observations.json`）

44 格 = `eligibility/attribute:{HOUSEHOLD,TASK_COLLABORATOR}:{identity,relationship,family,health,location,financial}`（12 格）+ `eligibility/disclosure:{HOUSEHOLD,TASK_COLLABORATOR}:{task_execution,personalization,task_resume,user_review}:{PUBLIC,PERSONAL,SENSITIVE,RESTRICTED}`（32 格）。44 格观测**完全一致**：

```
decision.outcome = "rejected"
decision.reason_codes = ["recall_disclosure_denied"]
result.items = []            candidate_query_count = 0     candidate_query_started = false
filtered_candidate_count = 0 candidate_count_stage = "after_all_eligibility_gates"
unsupported_capabilities = [] degradation_codes = []
context.disclosure_context = {recipient: "household" | "task_collaborator", intended_audience: "user_self",
                              purpose: <recipe>, trust: "trusted_authority", generation: "current", ...}
```

对照组（同一 run）：`disclosure:USER_SELF:*:{PUBLIC,PERSONAL,SENSITIVE}` → `recall`/1 项/`candidate_query_count=1`；`USER_SELF:*:RESTRICTED` → `no_recall`/`recall_no_eligible_memory`/`candidate_query_count=1`；`EXTERNAL_PARTY|PUBLIC|UNKNOWN:*` → `rejected`/`recall_disclosure_denied`/0 读取，全部 PASS。即 SDK 把 HOUSEHOLD/TASK_COLLABORATOR 当成了 EXTERNAL_PARTY 一样的"全局拒绝"受众。

### 1.2 期望（fixture rev 10 + oracle）

| 组 | 格数 | fixture 期望 | oracle 推导的期望执行结果（`typed_recall_a2_oracle.py`） | 实际 | FAIL 点 |
|---|---:|---|---|---|---|
| A. disclosure PUBLIC × task_execution/task_resume（HOUSEHOLD、TASK_COLLABORATOR） | 4 | `disclosure_cases`：`allowed:["PUBLIC"]` → ELIGIBLE | `normal_expected` 第 390-392 行 selected=True → 1 项、outcome `recall`、`recall_user_fact_dependency`、候选读取 1 | 0 项、`rejected` | 第 553 行 `original inclusion/exclusion oracle differs`（先于 outcome 检查） |
| B. disclosure 其余 28 格（PERSONAL/SENSITIVE/RESTRICTED，或 personalization/user_review 全部） | 28 | `disclosure_cases` denied → INELIGIBLE（`DISCLOSURE_COMBINATION_FORBIDDEN`） | 第 554-557 行：recipient ∈ {user_self,household,task_collaborator} 且 purpose ∈ 四种 → `globally_denied=False` → 期望 `no_recall` + `recall_no_eligible_memory` + 候选读取 1（记忆进入候选后被 `_candidate_disclosure_allowed` 过滤） | `rejected` + `recall_disclosure_denied` + 读取 0 | 第 559 行 `eligibility execution outcome/reason differs; invalid-plan rejection cannot pass` |
| C. attribute floor 12 格（PUBLIC × task_execution × 六类敏感属性） | 12 | `attribute_floor_cases`：INELIGIBLE、`ATTRIBUTE_FLOOR_FORBIDDEN`、`public_candidate_count_delta:0` | 第 393-394 行 selected=False，同 B：`no_recall` + `recall_no_eligible_memory` + 读取 1 | 同 B | 第 559 行 |

`exhaustive_axis_contract.disclosure` 只有 recipient × purpose × privacy 三轴，**没有 intended_audience 轴**（fixture rev 10 `exhaustive_axis_contract`）；oracle 第 554 行 `globally_denied` 也只看 recipient/purpose。

### 1.3 根因链（证据）

1. **SDK 门**：`sqlite_v5.py:3666` `globally_denied = not self._ordinary_recall_disclosure_allowed(plan.disclosure_context)` → `:3673-3700` 不进候选查询直接持久化 `rejected` / `recall_disclosure_denied`。`:5575-5582` `_ordinary_recall_disclosure_allowed` 要求 `ordinary_audience_matches(disclosure)`；`backends/disclosure_audience.py:9-18`：`{"user_self":"user_self","household":"household","task_collaborator":"task_collaborators"}`，`intended_audience.value` 必须等于映射值。`:5585-5606` `_candidate_disclosure_allowed`（候选层，household/task_collaborator 只允许 PUBLIC × task_execution/task_resume 且无敏感属性）**与 fixture `disclosure_cases`/`attribute_floor_cases` 逐条一致**——只要能进入候选层，fixture 与 oracle 的期望就是对的。
2. **引入时间**：`git log -S ordinary_audience_matches` → 唯一提交 `d7cb3ca`（2026-09-06 09:39 +0800，"fix(memory): bind ordinary disclosure to its final audience"），diff 把 `_ordinary_recall_disclosure_allowed` 的 `recipient.value in {"user_self","household","task_collaborator"}` 改为 `ordinary_audience_matches(disclosure)`，并在 `_candidate_disclosure_allowed` 开头加同一检查。首个含它的版本号提交 `dff3fbe` "assign 0.6.14 audience-binding candidate"；0.6.13（`d7cb3ca^`）不含。0.6.20（`52910b0`，CJK 二字组合）、0.6.21/0.6.22（`8010540`/`67b176d`，抑制快照）的 CHANGELOG 与 diff 均不触及 disclosure/recipient；run-04（0.6.23 observe）FAIL 集合与 run-03 完全相同（40+4），进一步排除 ①。
3. **适配器**：`adapters/semantic_relation_public_manager.py:35-48` `_disclosure()` 固定 `recipient=USER_SELF, intended_audience=IntendedAudience.USER_SELF`；`adapters/typed_recall_case_manager.py:45` 复用它，`:248-249` `dc.replace(self.disclosure, recipient=..., purpose=..., reason_codes=...)` **只替换 recipient/purpose，不替换 intended_audience**。观测里 44 格全部 `intended_audience:"user_self"`。
4. **Host 侧已知未接线**：`plans/2026-09-06-disclosure-audience/COMBINED.md`（Host 对 M0.6.14 的组合复核，"SELF召回不能携带不同最终受众"）与 `plans/2026-09-06-host-trusted-disclosure/契约.md:15`："当前Memory仅收紧final audience并修collaborator枚举……**typed recall非SELF接线属于后继**"。生产 Host 全部 `IntendedAudience.USER_SELF`（`backend/deskpet/memory/human_memory_v7.py:375`、`human_memory_service.py:502/1835/1906/1987` 等），所以生产链路未暴露该差异，只有 401 矩阵的非 SELF 格暴露。
5. **与 09-07 决定无关**：产品决定"遗忘只针对记忆"改的是 `_resolve_suppression_snapshot_unlocked`（0.6.21/0.6.22），run-03 中依赖抑制的格（`eligibility/suppressed`、`eligibility/short-source-suppressed`、`current-use/authority:suppression`、`current-use/context:suppression-first`）状态与 0.6.13 并集一致（前两者仍是历史同名 BLOCKED，后两者 PASS）。

### 1.4 历史对比：这些格之前是否 PASS

- `plans/2026-09-06-typed-recall-0613/FORMAL-BATCHES.md:10-14`：H073/**M0613** 固定候选十一批并集 public 391 = **182 PASS / 0 FAIL / 209 BLOCKED**；"未发现正式FAIL"。其未闭合原因分组（:75-99）中没有任何 disclosure/attribute 非 AUDIT 格，因此 `disclosure:{HOUSEHOLD,TASK_COLLABORATOR}:*`（32）与 `attribute:*`（12）在 0.6.13 时全部 PASS。
- 更早：`runners/TYPED-RECALL-NORMAL-BATCH-2026-09-05.md:67-69`（M0.6.4）"132 PASS … 132通过来自原 eligibility 与两个原 literal budget cells"；本次 disclosure+attribute 156 格 = 88 PASS + 44 FAIL + 24 BLOCKED(AUDIT)，88+44 = 132，与该数字吻合。
- 另一台 Mac 的原始证据包 `.local-test-evidence/from-other-mac/` 中**没有**任何 `bridge-summary.json`、`cell-classification.json` 或 `coverage-inventory/inventory.json`（`find` 为空），本文历史结论只依据上述已提交记录文档；`RESUME-2026-09-07.md:59,75` 也说明 0.6.20/0.6.22 pin 后"observe/正式扫描尚未运行"，即 run-03 是 0.6.14 受众绑定落地后第一次跑到这 44 格。
- 结论：**44 格历史有 PASS，本次由 PASS 转 FAIL；变化点是 SDK 0.6.14，不是 0.6.20–0.6.22。**

## 2. 174 个 BLOCKED 分类

按 `bridge-summary.json` 的 `blocker_categories`（runner `typed_recall_bridge.py:476-488` 自动归类）与 0.6.13 并集（FORMAL-BATCHES.md:75-99）逐项对照：

| 类别 | reason | 格数 | 性质 | 0.6.13 并集 |
|---|---|---:|---|---:|
| EXECUTOR_UNIMPLEMENTED | `CELL_EXECUTOR_NOT_IMPLEMENTED` | 30 | **adapter 未实现**：`current-use/authority:*` 10（classification_change/contest/context_expiry/policy_hash_change/result_expiry/revoke/short_source_cleanup/short_source_expiry/short_source_invalidation/supersede）、`current-use/context:new-continuation`、`eligibility/short-registration-invalid`、`eligibility/short-classification-invalid`、`selection-budget/*` 17（budget:deadline/greedy/short-emoji/short-escaping、dedupe:4、projection:short_horizon、ranking-order、tie-*:7） | 36（少 6） |
| FIXTURE_INVALID_OR_INSUFFICIENT | `PUBLIC_CASE_PRECONDITION_REJECTED:ValueError:audit disclosure requires an AuditAccessDecision and audit recipient` | 24 | fixture 让 6 种普通 recipient 携带 purpose=AUDIT，Harness 公共 DTO 明确要求 AUDIT 必须配 AuditAccessDecision + 审计 recipient；`TYPED-RECALL-NORMAL-EXECUTION.md:100-101` 禁止把 recipient 改成 AUDIT_REVIEWER 来"转绿"。fixture 与公共 API 契约不符 | 24 |
| 同上 | `…ValueError:verified states require trusted typed observation evidence` | 16 | epistemic llm_inference/unknown × source_verified/repeated_observation：SDK 写入侧禁止；fixture 期望 INELIGIBLE，但入口拒绝不能当召回资格门（NORMAL-EXECUTION.md:12-13）。`SETUP-122-NEXT.md` 已判定"即使补typed仍被M禁止，非普通适配遗漏" | 16 |
| 同上 | `…ValueError:verified_external requires source_verified state` | 16 | verified_external × 其余 4 个 verification 值，与公开写入契约冲突 | 16 |
| 同上 | `…MemoryValidationError:mutation_inference_cannot_be_authoritative` / `mutation_unknown_cannot_be_authoritative` | 12+12 | llm_inference/unknown 的默认 active 状态由 runner 选择（`typed_recall_normal_inputs.py:25`），SDK 拒绝 | 12+12 |
| 同上 | `…MemoryValidationError:mutation_observed_procedure_cannot_activate` | 6 | observed Procedure 不能 create 即 active；其中 repeated_observation 正例需真实观察晋升路径 | 6 |
| 同上 | `REQUIRED_APPLICABILITY_OR_SIGNAL_AUTHORITY_NOT_ESTABLISHED` | 5 | `eligibility/procedure-{draft,inapplicable,reinforced,revised,superseded}`：Procedure applicability 前置未建立（后继执行器已把此类从 30 收到 5） | 30 |
| 同上 | `FULL_SOURCE_RECORD_CANARY_AND_CROSS_SCOPE_SETUP_NOT_ESTABLISHED`（含组合） | 4 | `selection-budget/projection:{semantic,episode,procedure,prospective}`：跨 scope canary setup 未建；episode 另有 occurred_interval 投影差异 | 4 |
| 同上 | `FROZEN_128_BYTE_PAGE_BOUND_CANNOT_FIT_PUBLIC_BINDING` | 1 | `protocol/page-correct-binding`：冻结 128 字节页界装不下公开绑定 | 1 |
| ORACLE_GAP | `CONFLICT_REJECTION_OR_PRECONDITION_REQUIRES_FULL_ORACLE` | 10 | `conflict-state/contest-*` 10 格：完整 conflict oracle 未写 | 10 |
| 同上 | `RETURN_CELL_COMPLETE_ATTACK_AND_READ_WITNESS_ADMISSION_PENDING` | 8 | `protocol/*` 8 格 | 8 |
| 同上 | `CONFLICT_DURABLE_GROUP_MEMBER_RESOLUTION_HASH_ORACLE_PENDING` | 6 | resolve-*/contested-dependent-complete/contest-create-distinct-evidence | 6 |
| 同上 | `STATE_COMPLETE_RECEIPT_AND_PROTECTED_TRANSITION_BINDING_PENDING` | 6 | `eligibility/{current-head,stale-head,suppressed,ordinary-contested,ordinary-resolved}` + `conflict-state/contested-dependent-partial` | 6 |
| 同上 | `SHORT_COMPLETE_REGISTRATION_CLASSIFICATION_TIME_AND_ORIGINAL_HASH_BINDINGS_PENDING` | 6 | `eligibility/short-*` 4 + `protocol/{mixed-long-short,short-only}` | 6 |
| 同上 | `FULL_ORIGINAL_CELL_ADMISSION_PENDING` | 5 | `eligibility/valid-until-null-unbounded`、`unsupported-replay/{combined-all,combined-selector-mode,event-only,exact-replay}`（`assess_cell` 通过全部现有断言但保留最终准入门，`a2_oracle.py:346-349`） | 5 |
| 同上 | `PUBLIC_EXECUTED_LANE_WITNESS_UNAVAILABLE` | 3 | `selection-budget/vector-*`：公开 API 无"实际执行了哪些 lane"的见证（`a2_oracle.py:587-591`） | 3 |
| 同上 | `CURRENT_USE_HARNESS_RESERVATION_EXACT_ONCE_WITNESS_UNVERIFIED` | 2 | `current-use/context:{duplicate-same-provider-attempt,receipt-first}`（0.6.13 时同两格在 `CURRENT_USE_ORIGINAL_TWO_ITEM_EPOCH_CONTINUATION_ORACLE_PENDING` 6 格内，另 4 格现已 PASS） | 6 |
| 同上 | `CONSTRUCTION_CONFLICT:strict public ProspectiveMemoryPayload cannot represent missing typed trigger` | 1 | `eligibility/prospective-trigger-missing`：严格公开 DTO 无法构造缺 trigger，入口拒绝不当召回通过（`plans/2026-09-06-typed-recall-trigger-executor/RESULTS.md:10`） | （在 30 内） |
| 同上 | `PUBLIC_EXCEPTION_HAS_NO_PER_INVOCATION_CANDIDATE_READ_WITNESS` | 1 | `unsupported-replay/conflicting-replay` | 1 |

合计 30 + 96 + 48 = 174。`oracle_blockers` 字段为空（`typed_recall_bridge.py:25` `ORACLE_BLOCKERS = []`），所以"oracle 明确的 blocker"全部体现在逐格 reason 的 ORACLE_GAP 类，而不是 summary 顶层。

**历史对照**：0.6.13 并集 219 BLOCKED（122 fixture/setup、61 oracle、36 executor）→ run-03 174（96/48/30）。消失的 45 格 = 后继执行器新增 PASS：source 10（`SOURCE_FULL_STATE_PK…` 7 + `CORRUPTION_EXACT…` 3）、current-use 4、Procedure/Prospective/trigger/applicability 25 + `procedure-eligible-state` 1 + 另 4（与 `历史覆盖盘点.md` 的"45 个不同格，新增 42 通过、3 仍阻塞"一致；剩余差 3 未逐格核对，因另一台 Mac 的 `coverage-inventory/inventory.json` 不在证据包内）。**run-03 没有任何 0.6.13 并集之外的新 BLOCKED 原因。**

## 3. `layers.source` 为何 `NOT_RUN/BLOCKED` 且无 reason

`typed_recall_bridge.py`：

- `validate_layer`（:94-128）对两层统一返回 `{"status": "FAIL" if failed else "NOT_RUN/BLOCKED", "passed_cells": [], observed/blocked/failed/missing}`——这里 blocked/failed 只指**适配器自报**的 BLOCKED/FAIL，全部 10 个 source 格自报 OBSERVED，所以 `observed_cells=10`、其余为空，没有 `reason` 键（summary 里就没有该字段，读出来是 None）。
- public 分支（:424-430）：`assess_observed_cells` 之后**回写** `layers.public.passed_cells` 与 FAIL 时的 `failed_cells`/`status="FAIL"`。
- source 分支（:432-436）：`assess_source_cells` 结果只写入 `summary["cell_results"]`，只在有 FAIL 时置 `status="FAIL"`；**没有回写 `passed_cells`，也没有把 `blocked_cells`/`observed_cells` 与判定结果同步**。因此 10 格在 `cell_results` 中均 `status=PASS`（`business_assertions` 含 "real revision7/8 no-fault confirmation control" / "complete source schema/PK/request/attempt/terminal and unchanged nonfinal roots"），并已计入顶层 `passed_cells`（183 = public 173 + source 10）与 `acceptance_counts.PASS`，但 `layers.source.passed_cells=[]`、`status` 保持初值。
- 更根本：runner 里**没有任何语句把层状态或整体状态置为 "PASS"**——公共层即使全部 PASS 也保持 `NOT_RUN/BLOCKED`（bridge 自测 `tests/test_typed_recall_bridge.py:45-46,74` 就断言 `passed_cells==[]` 且 `NOT_RUN/BLOCKED`）；整体 `status` 只有初值 `NOT_RUN/BLOCKED` 与 `FAIL` 两态（:330,462-465）。`typed-recall-execution-layers-v1.json.combined_gate` 要求两层均 `PASS` 才能合并，这一层级 PASS 的产生逻辑目前不存在。
- 判定：**逐格 PASS 有效、层级簿记不对称**（public 有 `passed_cells`，source 没有）。建议在 :432-436 补 `summary["layers"]["source"]["passed_cells"] = sorted(PASS 格)`，并定义"层内全部 required 格 PASS ⇒ 层 PASS"的显式规则（当前 174 BLOCKED 下任何一层都不可能满足，所以不影响本次结论）。observe 模式（`--observe-candidate` 或 pin 变化）会把两层 PASS 改写为 `CANDIDATE_OBSERVATION_ONLY`（:438-444），run-04 即如此。

## 4. 清单

### 4.1 真实回归（需要修 SDK）

**无。** 0.6.20–0.6.22 三个改动（CJK 二字组合、抑制快照两处）在 run-03 中没有造成任何由 PASS 转 FAIL/BLOCKED 的格；44 个 FAIL 全部由 0.6.14 受众绑定 + 适配器未随之更新造成，且 SDK 行为符合其自身意图（`disclosure_audience.py:1-5` 注释："self delivery alone must never authorize private inputs for that wider audience"）与 Host 对 M0.6.14 的复核结论。若主代理认为"recipient=household 但 intended_audience=user_self 应当进入候选层再被过滤"才是产品语义，那属于重新裁决 0.6.14，不是回归修复。

### 4.2 需要更新 oracle / fixture（附理由）

| 对象 | 位置 | 改法 | 理由 |
|---|---|---|---|
| fixture `exhaustive_axis_contract.disclosure` + `disclosure_cases`/`attribute_floor_cases` | `fixtures/typed-recall-v3.json`（rev 11 → rev 12，内容变更，需独立复审；`candidate_pin_change.scope` 不能再写"oracle inputs unchanged"） | 显式声明每个 recipient 的 final audience：USER_SELF→user_self、HOUSEHOLD→household、TASK_COLLABORATOR→task_collaborators、EXTERNAL_PARTY→external、PUBLIC→public、UNKNOWN→unknown；可选再加 4 格负例"recipient=HOUSEHOLD 但 intended_audience=user_self ⇒ REJECTED/DISCLOSURE_DENIED/零读取"，把 0.6.14 的门变成矩阵显式义务 | 当前三轴契约无法表达 0.6.14 之后的公共契约；不加负例则受众绑定永远不在 401 里被验证 |
| oracle `globally_denied` | `runners/typed_recall_a2_oracle.py:554-557` | 从 recipe 读 `intended_audience`（或按上表映射），`globally_denied = not(audience 匹配 且 recipient∈三种 且 purpose∈四种)` | 与 SDK `_ordinary_recall_disclosure_allowed` 同构；否则 oracle 只对"适配器恰好给对 audience"成立 |
| oracle `normal_expected` disclosure/attribute 分支 | 同文件 :390-394 | 不变（fixture 表已与 `_candidate_disclosure_allowed` 一致） | — |
| source oracle schema 描述符 | `runners/typed_recall_source_oracle.py`（`6044f1ec` 已改为 M0623/7.4：94 表、`cognitive_vector_schema_integrity_differs`） | 已完成，待新正式 run 验证 | run-04 的 10 个 source FAIL "exact M0620 fresh schema descriptor differs" 是旧 pin 对 0.6.23 的预期差异，不是产品缺陷 |
| vector 退化码期望 | `a2_oracle.py:588`：`codes=['cognitive_vector_unavailable'] if 'vector' in modes` | 保持（消费者无 embedder 时 0.6.23 仍给同一码）；**若**消费者按生产绑定 embedder，则改为 `cognitive_vector_no_generation`（未 rebuild）/ `[]`（rebuild 后命中）并给 fixture `ranking_oracle.vector_degradation_cases` 增加 no_generation/stale/deadline 三例 | 0.6.23 CHANGELOG："`cognitive_vector_unavailable` 仅表示 backend 无 embedder；新增 no_generation/stale/deadline" |

### 4.3 需要补 adapter

| 对象 | 位置 | 改法 |
|---|---|---|
| **非 SELF 受众配对（修 44 FAIL 的直接动作）** | `adapters/typed_recall_case_manager.py:248-249`（及 `semantic_relation_public_manager.py:35-48` 的 `_disclosure` 保持 SELF 用于 seed 写入） | `request()` 里 `dc.replace(..., intended_audience=h.IntendedAudience(<按 recipient 映射>))`。预期效果：HOUSEHOLD/TASK_COLLABORATOR 进入候选层 → 4 格 PUBLIC×task_execution/task_resume 召回 1 项、28 格 no_recall、12 格 attribute floor no_recall（`_candidate_disclosure_allowed` 的 sensitive 集合 = fixture 六类属性），与现 oracle 完全吻合；EXTERNAL_PARTY/PUBLIC/UNKNOWN 因不在 `_ORDINARY_AUDIENCES` 仍全局拒绝，现有 PASS 不变。这是"typed recall 非 SELF 接线"后继项在矩阵侧的对应 |
| 30 个 `CELL_EXECUTOR_NOT_IMPLEMENTED` | `adapters/typed_recall_context_use_cases.py`（authority 事件 10 + new-continuation）、`typed_recall_short_cases.py`（short registration/classification invalid 2）、`typed_recall_normal_cases.py`/新文件（selection-budget 17：budget 4、dedupe 4、projection:short_horizon、ranking-order、tie 7） | 历史上就未实现，与本次 SDK 版本无关 |
| 前置条件 96 格 | 见 §2，多数已被 `SETUP-122-NEXT.md` 判定为 fixture 与公共契约冲突（AUDIT 24、epistemic 56）而非适配遗漏；可补的是 Procedure applicability 5 格与 projection canary 4 格 | — |

### 4.4 0.6.23 预期影响的格

run-04（`run-04-observe-m0623/`，`--observe-candidate`，M0.6.23 wheel `56a1a0dc…`，但 runner/oracle 仍是 0.6.22 pin 版本）实测：BLOCKED 347 / FAIL 54 / PASS 0；其中 173 格 `CANDIDATE_OBSERVATION_ONLY`（= run-03 的 public PASS 集合），FAIL 54 = 与 run-03 完全相同的 40+4 + **10 个 source 格** `exact M0620 fresh schema descriptor differs`。

| 格 | 0.6.23 影响 | 状态 |
|---|---|---|
| 10 个 source 格（`conflict-state/contested-tampered-*` 3、`fault-recovery/fault:*` 7） | schema 7.4 追加 `cognitive_vector_generations`/`cognitive_vectors`/`cognitive_vector_audit`，fresh-schema 与 columns/PK 描述符、腐败拒绝原因集合变化 | `6044f1ec` 已重算 pin（94 表，`cognitive_vector_schema_integrity_differs`），待正式 run |
| `selection-budget/vector-only-unavailable`、`vector-unavailable-full-text-survives` | 消费者无 embedder → 仍 `cognitive_vector_unavailable`，oracle :588 不变；仍因无执行 lane 见证 BLOCKED。若消费者绑定 embedder，则退化码变为 `cognitive_vector_no_generation`（未 rebuild）或消失（rebuild 后 vector lane 真实参与计分，`vector-only` 格可能从 NO_RECALL 变为 RECALL） | 现状不变；接 embedder 后需改 oracle + fixture |
| `selection-budget/vector-not-requested` | 不变 | 不变 |
| 其余 normal 家族（含 44 FAIL 格） | 消费者按 recipe `modes=('full_text',)`（`typed_recall_normal_cases.py:53`），未跟随 Host 生产"每个 typed 计划恒请求 VECTOR"（`backend/deskpet/memory/human_memory_v7.py:411-413`）。资格门顺序与词法门不变（0.6.23 CHANGELOG），44 格在计划层即被拒绝，与 vector 无关 | 不变。若矩阵要镜像生产恒请求 VECTOR，需同时改 recipe modes、`a2_oracle.py:521` 的 modes 比较与全部 normal 格的退化码期望（那将是一次内容级 fixture 修订） |
| `unsupported-replay/request-hash:*`（14 攻击格） | 攻击前后 manifest 比较覆盖全部 table_roots（`a2_oracle.py:309-318`），新增三张表只要在拒绝重放中不变即通过；run-04 观察态下这 14 格未出现新 FAIL | 预期不变 |

## 5. 建议的下一步（供主代理裁决）

1. 先做 4.3 第一项（适配器 audience 映射，约 3 行）并在 0.6.23 pin 下重跑 401 正式 run；预期 44 FAIL → PASS、10 source 格恢复 PASS，PASS 约 237、FAIL 0、BLOCKED 164。
2. 再做 4.2 的 fixture rev 12（加 audience 轴与负例）+ oracle :554 同步，走独立复审；不要用"oracle 改成全部非 SELF 全局拒绝"的方式让 44 格转绿——那会让 fixture 中 HOUSEHOLD/TASK_COLLABORATOR 的 PUBLIC 允许表永远不被执行。
3. bridge runner 补 source 层 `passed_cells` 回写（§3），属簿记修正，不改变任何格结果。
4. 历史"224 有 PASS / 177 无 PASS"台账在本机不可核对（证据包缺 inventory.json）；如需逐格历史对照，需从另一台 Mac 补 `.local-test-evidence/2026-09-06/coverage-inventory/inventory.json` 与 `typed-recall-0613-formal/cell-classification.json`。

## 主代理复核与处置（2026-09-07 晚）

- 复核结论：采纳第 1/3/5 点。44 个 FAIL 根因为适配器只替换 recipient、未同步 `intended_audience`（Memory ≥0.6.14 要求同域配对），属验证侧过期，不是 SDK 回归；source 层 `passed_cells` 漏写属 bridge 聚合缺陷。
- 处置：`adapters/typed_recall_case_manager.py` 按 recipient 同域映射 `intended_audience`（user_self/household/task_collaborators/external/public/auditor/unknown）；`runners/typed_recall_bridge.py` source 分支补写 `passed_cells`/`failed_cells`。runner 自回归 106 项通过。
- 正式扫描 run-06（M0.6.23 pin，fixture rev 11 / layers rev 9，`.local-test-evidence/2026-09-07/typed-recall-401/run-06-m0623-audience/`）：**PASS 227 / FAIL 0 / BLOCKED 174**（public 217 + source 10）；runner 退出 3 = 整体仍 BLOCKED（174 格：执行器未实现 30、fixture 与公共契约不符 96、oracle 未闭合 48），与历史 0.6.13 并集 182 PASS 相比新增 45 格 PASS。
- 未做：fixture rev 12 加 audience 轴与负例；174 BLOCKED 的执行器/oracle 补齐；`selection-budget/vector-*` 3 格在消费者绑 embedder 后的退化码期望更新。
