# H1 PlanningDecision 协议 — 实施拆解

**日期：2026-09-18**  
**性质：实施拆解（只读对照，不改代码、不 commit）**  
**对照计划：`plans/taskSys2/升级planV1/v1.4/simpleharness-llm-native-htn-execution-plan.zh-CN.md`（HTN-LLM-NATIVE-1.0）§5–§13、§28–§31、§46–§47、§54–§61、§68 H0/H1、§69–§70、§83**  
**对照 SDK：`/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-sdk` HEAD `c0e13a4927717abfa5281e78d6fdca1ee32d732c`（0.12.2 candidate，第 6 切）**  
**计划文件写的基线是 `f2dfa64`；本拆解以当时主干 `c0e13a4` 为准。**

H1 只做一件事：把「模型建议系统下一步怎样改变规划状态」收成 **PlanningDecision Protocol V1**。不重写 MethodContract、不实现 Repair AST、不换 Backend、不改默认 live 路径。

验收口径（计划 §68 / §83）：

```text
旧 PlanProposal 行为不变
新 Envelope round-trip
系统字段无法由模型设置
unknown refs reject
```

---

## 1. H0 冻结基线（记录项与现值）

H0 不改功能。下列现值在 `c0e13a4` 上由源码与已归档核验报告读出；本拆解未重跑 full_target / 旧模式套件。

### 1.1 HEAD

| 项 | 现值 |
|---|---|
| SDK HEAD | `c0e13a4927717abfa5281e78d6fdca1ee32d732c` |
| 短 SHA | `c0e13a4` |
| 说明 | `release: simple-harness-sdk 0.12.2 candidate, sixth cut (FULL-TARGET-1.4 P2.3e–P2.3v)` |
| 工作树 | 对照时干净（本拆解不改 SDK） |
| 计划文件基线 | `f2dfa6426ff55f944887fa938dea6fa8d0bfab82`（过期；以 HEAD 为准） |

### 1.2 Schema

| 合同 | 常量 / 位置 | 现值 |
|---|---|---|
| 计划修订提案 | `contracts/htn.py:63` `PLAN_REVISION_PROPOSAL_SCHEMA_VERSION` | `1`（`plan-revision-proposal-v1`） |
| 分层 Planner 包 | `planning/htn/planner_package.py:67` `HIERARCHICAL_PACKAGE_VERSION` | `"planner-package-hierarchical-v4"` |
| 包–提示词配对号 | `runtime/role_templates.py:715` `HIERARCHICAL_PLANNER_PACKAGE_VERSION` | `3`（v5/v6/v7） |
| 编排语义 | `orchestrator/plan_commits.py:115-122` | `legacy` / `hierarchical`（别名 `full-target-v1`） |
| 新协议 | **不存在** | H1 新增 `planning_protocol_version = "planning-decision-v1"` |

### 1.3 full_target 测试数

| 项 | 现值 | 来源 |
|---|---|---|
| passed / skipped | **2960 / 2** | SDK `plans/2026-09-16-full-target/P2.3c/journal.md` 第 4111 行；`reviews/核验-P2.3v-d64044b-2026-09-18.md` |
| 2 skip 含义 | `test_real_provider_hierarchical_smoke.py` 整文件 `pytestmark = real_provider`（无 `--run-real-provider` 即 skip） | `tests/orchestrator/full_target/test_real_provider_hierarchical_smoke.py:74` |

H1 结束时：旧 2960/2 必须仍绿；增量只来自本拆解第 5 节清单，不得吃掉 skip。

### 1.4 旧模式回归

| 项 | 现值 |
|---|---|
| 套件 | `tests/orchestrator/step02` + `step05` + `step06` + `step07` + `p34` + `p35` |
| passed / skipped / failed | **560 / 13 / 0** |
| skip 性质 | 既有 real-provider / pinned tokenizer，零新增失败 |
| `_new_mode` 哨兵 | **19** 处 `self._new_mode(mission)`（`tests/orchestrator/full_target/test_hierarchical_event_flow.py:1540`） |

### 1.5 真实模型场景清单

H 臂冻结题集（用户 2026-09-16 批准，见 `impl/Grok验收题集-用户决定-2026-09-16.zh-CN.md`）。H0 冻结的是**题单身份**，不是某一次跑分。

**L1 不退步（AppWorld A96 12 道 dev，每题 ×2 臂 ×2 遍）：**

`0d8a4ee_1`、`23cf851_1`、`37a8675_1`、`383cbac_1`、`396c5a2_1`、`4fab96f_1`、`50e1ac9_1`、`530b157_1`、`6171bbc_1`、`68ee2c9_1`、`6c2c621_1`、`fac291d_1`

**L2 加难（12 道，非榜单口径）：**

`988af8e_1`、`80acbaf_1`、`986aa4e_1`、`3fcc458_1`、`f3a6713_1`、`6b6ca61_1`、`8d42650_1`、`a1d3dfd_1`、`f6be291_1`、`fa327a6_1`、`7238049_1`、`32616b5_1`

**L3 code 自然题（4 道，C4 为替换项，当前跑 C1–C4）：**

| 代号 | 一句话 |
|---|---|
| C1 | 多线程采集下 `reporter.summary()` 不能丢样本也不能重复计数 |
| C2 | 让 ingest 流水线端到端跑通，坏行要留痕不要丢 |
| C3 | 把 `test_window.py` 的失败修掉，但别丢上周加的 rolling 窗口特性 |
| C4 | 分页最后一页少一条，修掉 |

**L4 机制构造题（3 道）：**

| 代号 | 机制 |
|---|---|
| M1 | 方法切换 + 共享只读子目标复用（浅克隆否定 revert） |
| M2 | UNKNOWN 前提取证后再选方法 |
| M3 | 共享只读子目标 + 一分支失败仍服务其他消费者 |

**SDK 内真实模型冒烟（opt-in，计入 2 skip）：**  
`tests/orchestrator/full_target/test_real_provider_hierarchical_smoke.py` — 分层 Mission 在真实模型上 Planner 块必须可读，终态 COMPLETED 或带结构化停机原因。

第 6 批 H 臂（`c0e13a4`）跑的是 C1×2、C2×2、C4×2、M2-r1、M3×2；第 5 批（`f2dfa64`）归档 C3×2、M1×2、M2-r0。H0 记录题单，不把某批跑分成协议基线。

### 1.6 冻结提示词 sha 位置

**主表（参数化钉字节）：**  
`tests/orchestrator/full_target/test_output_port_claims.py:193-289` `FROZEN_PROMPT_DIGESTS`  
同文件 `:294-299` `FROZEN_REGISTERED_DIGESTS`（域模块注册的 worker 版本）  
守卫：`test_a_shipped_prompt_keeps_its_bytes`、`test_the_frozen_digests_cover_the_prompts_this_slice_depends_on`

**DAG Planner 旧版另钉：**  
`tests/orchestrator/full_target/test_planner_typed_proposal.py:512-515` `FROZEN_PLANNER_PROMPTS`  
- `planner-v3` `353b0dd0a0727b4a8ce74fe82354d17a02b6afe0a6c883f011b77ee22c4287e9`  
- `planner-v4` `13537f0abf6322c7075af9b5ddb3c0b7316c0271830311f49c3d6c195f5c9aad`

**分层 Planner 现值（本机对 `c0e13a4` 重算，与核验报告一致）：**

| 版本 | sha256 | 是否在 `FROZEN_PROMPT_DIGESTS` | 包配对 |
|---|---|---|---|
| `planner-hierarchical-v1` | `2acb2294fca09f55c30831ffd43dd7eae5685af72daa4c85de8759b440e43830` | **否（H0 缺口）** | 包 1 |
| `planner-hierarchical-v2` | `f8a8bba7221bfcc1c33d8b3f71517678bced6c905c7dbfdcee1558f0374a0387` | **否（H0 缺口）** | 包 1 |
| `planner-hierarchical-v3` | `ba244a12bf504051d7ebc954f462cf23f9c7734dff980c539187834a0a670acb` | 是 | 包 2 |
| `planner-hierarchical-v4` | `5ae3b39acf9326888e20bab934848ed6e1d21482884ccda932ac693b31122223` | 是 | 包 2 |
| `planner-hierarchical-v5` | `2517d5fe727ba27ffffa105d72e786e72109893c342e56adebeaa033794f7608` | 是 | 包 3 |
| `planner-hierarchical-v6` | `b13d16f7d1d5aaa8919d983e93b7639105446bd6d95bd73d05353571a9a185a6` | 是 | 包 3 |
| `planner-hierarchical-v7` | `5b87b9624fbf4a4e1c31e6d9c4a765de2ac4ec689b5d708000b427676cb50f15` | 是 | 包 3（**当前默认**） |

H1 新增 prompt v1 时：v1–v7 字节不得动；v1/v2 建议顺手补进 `FROZEN_PROMPT_DIGESTS`（纯测试补钉，不算改功能）。默认选择器仍是 `_hierarchical_planner_template` → `PLANNER_HIERARCHICAL_V7`（`event_handler.py:3087-3126`）。

**旧函数 hash：**  
`tests/orchestrator/full_target/test_allocator_form_gate.py:113-114`

- `LEGACY_FRONTIER_SHA256` = `0ae7cd4c24ee902e1fac2f8e8193920a64e9ff10c408baf0f33d1d6cc366e1b8`
- `LEGACY_ALLOCATE_SHA256` = `5940ab39e18168a83f9af8c422a9924758faf41ddf8793021b5a89c6cb5ab78c`

**legacy 事件 golden：**  
`tests/orchestrator/full_target/test_hierarchical_event_flow.py:1392-1421` `NEW_EVENT_TYPES`；`test_the_legacy_run_appends_none_of_the_new_event_types`、`test_the_legacy_path_never_enters_the_assembly_at_all`

### 1.7 H0 必须记进实施 journal 的检查表

实施 H1 的第一天把上表抄进 SDK `plans/2026-09-16-full-target/` 下 H1 journal，并钉：

1. HEAD `c0e13a4`
2. full_target 2960/2
3. 旧模式 560/13/0
4. `_new_mode == 19`
5. `FROZEN_PROMPT_DIGESTS` 全行 + 分层 v1/v2 两行
6. 题单 L1–L4 身份
7. `HIERARCHICAL_PACKAGE_VERSION` / `HIERARCHICAL_PLANNER_PACKAGE_VERSION` / `PLAN_REVISION_PROPOSAL_SCHEMA_VERSION`

---

## 2. 现状映射表

计划对象 → 现有最近类型（文件:行）→ 处置。  
**复用** = 原类型原语义继续用；**扩展** = 在原文件加字段/枚举值；**新建** = 计划 §54 指定的新文件；**适配器** = 只翻译能证明的语义，缺就留空。

| 计划对象 | 现有最近类型 / 位置 | 处置 | 说明 |
|---|---|---|---|
| `PlanningDecisionEnvelopeV1` | 无。最接近：`PlanProposal` `contracts/htn.py:2604` + `MethodProposal` `planning/htn/registry.py:757` | **新建** `contracts/planning_decisions.py` | 不删旧类型。Envelope 是新 wire；旧块继续可解析 |
| `PlanningDecisionType` | 无枚举。操作散落在 `parse_plan_operation` `contracts/htn.py:2550-2600`（`refine` / `retire_method` / `bind_shared_goal` / `propose_successor`）+ `NoApplicableMethodDeclared` `planning/planner.py:67` | **新建** `StrEnum` | 10 个值一次给齐；H1 只准入 REFINE / PROPOSE_METHOD / REQUEST_EVIDENCE / DECLARE_BLOCKED / NO_CHANGE。其余值可解码，admission 以具名码拒绝（见 §3.6） |
| `PlanningSubject` | 无独立类型。`RefineOperation.goal_id` + `obligation_id` `contracts/htn.py:2482`；`MethodProposalRequest` 已有 `task_id` / `occurrence_id` / `obligation_id` `planning/htn/refinement.py:545` | **新建** | 从 request context 绑定，模型不能改投 Mission / occurrence。`MethodProposalRequest` 的三元组是形状参考，不是 wire |
| `TypedRef` | `contracts/semantic_base.py:420` `TypedRef`；`TypedRefKind` `:313` | **复用** `TypedRef` | 见下一行的 kind 缺口 |
| `ReasonRefs` | 无。最接近：`PlanProposal.read_set`（`ReadItem` `contracts/htn.py:2215`，kind 含 `fact` `:312`）+ `trigger_refs`（`EvidenceRef`） | **适配器 + 小扩展** | 计划示例 `{"kind":"fact","id":…}`。`TypedRefKind` **没有** `fact`（有 `OBSERVATION`）。H1 用规划侧 `PlanningRef`：`fact`/`method`/`task`/`acceptance`/`obligation` 走 `ReadItem` 形状；AER `TypedRef` 不改，避免动附件合同 |
| `AssumptionHint` | 无 | **新建** | 不得冒充 Fact / Evidence / TRUE（计划 §10） |
| `RefineDecision` | `RefineOperation` `contracts/htn.py:2482`（`method_ref` + `bindings` + 操作里的 `goal_id`/`obligation_id`） | **新建** payload；**适配器** 从 `RefineOperation` 填 | 新 payload **没有** goal/obligation（在 Subject）。模型不能输出新 Task/Occurrence/Budget/Acceptance/Operation/DispatchGeneration ID |
| `EvidenceRequestDecision` | `EvidenceOccurrenceRequest` `planning/htn/refinement.py:274`；`EvidenceAsk` `planning/htn/evidence_round.py:48`；`evidence_requests()` `:350` | **新建** payload；执行仍复用 `evidence_round` | 现网是程序从 UNKNOWN 前提生成只读观察，不是模型决策。H1 只定义「模型请求取证」的 wire；真正跑观察留在原模块 |
| `PlanningRejectionCode` | 无枚举。`PlanningRejected.reason` 是自由字符串，由 `event_handler._planning_rejected` `:4456` 与 `apply_planner_reply` 的 `ContractError` 写入 | **新建** 枚举；**适配器** 把旧 reason 字符串映过去（能证明的才映） | 现值包括：`proposal_unreadable`、`proposal_wrong_block`、`proposal_not_grounded`、`no_applicable_method`、`plan_commit_refused`、`root_review_rejected`、`read_only_leaf_needs_write`、`repeated_verification_failure`、`method_synthesis_refused`。Commit 侧另有 `READ_SET_STALE` / `PLAN_REVISION_STALE` / `READ_SET_UNRESOLVED`（`plan_commits.py:560-583`） |
| `PlanningFeedbackV1` | `hierarchical_planner_package` 的 `planning_rejected` 列表 `planner_package.py:549`；拒绝详情在 `PlanningRejected` 事件 `detail`（含 `repair_hint`，`event_handler.py:5113`） | **新建**；旧事件/包字段 **保留** | H1 不替换 live 反馈形状。新类型给 admission 返回值与测试用；H2 再收口拼包 |
| `PlannerPackageV1.visible_refs` | 无统一元组。实际可见 ID 散落：`method_library[].refine_method_ref`、`facts[].read_set_entry`、`plan.open_compound_goals`、`rejected_refinements`（`planner_package.py:507-571`） | **扩展** 一个纯函数收集器，挂在 `planner_package.py` | H1 **不**重写拼包（那是 H2）。收集器从现包算出 `tuple[PlanningRef, …]`，admission 用它做 `reason_ref ∈ visible_refs` |
| `ReplanConditionHint` | `Condition` AST：`PredicateCondition` `contracts/htn.py:463` 等 | **新建** 外壳，Condition **复用** | 模型不能注册 Python callback（§31） |
| 旧 `PlanProposal` | 同上 `:2604`；解析 `planning/planner.py:132` `parse_plan_proposal` | **保留 + 适配器** | H1 默认 live 路径仍走它 |
| 旧 `MethodProposal` | `registry.py:757`；解析 `planner.py:171` `parse_method_proposal`；合成 `synthesis.py:729` `accept_response`；装配 `hierarchical_dispatch.py:2753` `admit_method_proposal` | **保留 + 适配器**；PROPOSE_METHOD payload **复用** `MethodProposal` | 计划 §12 |
| repair hints | `runtime/output_blocks.py:170` `repair_hint`；`REPAIR_HINTS` `:155`（含 `proposal_wrong_block`） | **保留** | H1 不改 hint 文本。新 codec 的 BlockError 可复用同一函数，tag 换成 `planning_decision` |
| root-review repair | `ROOT_REVIEW_REPAIR_REASON = "root_review_rejected"` `hierarchical_dispatch.py:269`；`rejected_refinements()`；`compile_proposal` 的 retire+refine 对 `:3760` | **不接线**（H4） | Envelope 的 `REPAIR` 值可存在，admission 拒绝。适配器对 retire+refine **不**伪造 Repair AST |
| `PlanningRejected` 理由字符串 | 见上 | **保留**；新码另表 | 旧历史不重新解析成新 Decision（§55） |
| Planner 拼包 | `hierarchical_planner_package` `planner_package.py:456`；调用点 `event_handler.py:3048` | **保留**；H1 只加收集器 | 默认 `output_contract` 仍是 `<plan_revision_proposal>` |
| prompt pin | `template_for` `role_templates.py:1382`；分层选择器 `event_handler.py:3087`；配对表 `role_templates.py:719` | **扩展** 新版本 + 新配对号；旧 pin **保留** | 见 §3.3 |
| 系统绑定字段 | `SYSTEM_BOUND_FIELDS` `planning/planner.py:33-50`（14 项）；拒绝 `_refuse_authority_claims` `:115`；金表 `GOLDEN_BOUND_FIELDS` `test_planner_typed_proposal.py:279` | **新建** Envelope 专用集合（超集）；**不改** 旧 14 项金表 | 见 §3.5 |
| deterministic `chosen = selectable[0]` | `planning/htn/refinement.py:803` | **保留**（H3 才改默认） | H1 不改选方法 |
| Commit | `plan_commits.py:293` `commit_plan_revision` | **保留** | 计划 §45：Planner 的「我已确认」无权限。H1 admission 在 compile 之前 |

---

## 3. H1 交付清单（§83 → 文件 / 函数）

计划 §83 交付范围逐项落地。目录按 §54：有等价位置就扩展原文件，不平行复制。

### 3.1 新文件

| 计划项 | 路径 | 函数 / 类型 | 职责边界 |
|---|---|---|---|
| Envelope + Type + Subject + Assumption + Refine + Evidence | `src/agent_orchestrator/contracts/planning_decisions.py` | `PlanningDecisionEnvelopeV1`、`PlanningDecisionType`、`PlanningSubject`、`AssumptionHint`、`RefineDecision`、`EvidenceRequestDecision`、`EvidenceQuestion`、`ReplanConditionHint`、`AlternativeSummary`、`PlanningDecisionPayload` | 只数据。`from_json` / `to_json`。`schema_version = 1` |
| Rejection + Feedback | 同上（或同文件后半；不要新包） | `PlanningRejectionCode`、`PlanningProblemDetail`、`PlanningFeedbackV1` | Feedback 是 admission 的返回值，不是事件替代 |
| Decision codec | `src/agent_orchestrator/planning/decision_codec.py` | `parse_planning_decision(text, *, context)`、`serialize_planning_decision`、`PLANNING_DECISION_TAG = "planning_decision"` | 调 `extract_block`（`output_blocks.py:29`）。malformed → `MALFORMED_DECISION`。**不**在这里 compile / commit |
| Decision admission | `src/agent_orchestrator/planning/decision_admission.py` | `admit_planning_decision(decision, *, package, context) -> PlanningFeedbackV1` | 确定性检查：系统字段、subject 绑定、`reason_ref ∈ visible_refs`、method 存在/版本/RETIRED、predicate 已注册。通过也不等于 Commit |
| legacy PlanProposal adapter | `src/agent_orchestrator/planning/decision_adapters.py` | `legacy_plan_proposal_to_decision(proposal, context) -> PlanningDecisionEnvelopeV1` | 规则见 §3.2。禁止补假的 alternatives / assumptions / reason_refs |
| legacy MethodProposal adapter | 同上 | `legacy_method_proposal_to_decision(proposal, subject) -> PlanningDecisionEnvelopeV1` | 恒为 `PROPOSE_METHOD`；payload 就是原 `MethodProposal` |
| visible_refs 收集器 | `planning/htn/planner_package.py`（扩展） | `visible_refs_from_hierarchical_package(package) -> tuple[PlanningRef, ...]` | 只读现包。H2 再把 `visible_refs` 做成一等字段 |
| prompt v1 | `runtime/role_templates.py` | `PLANNER_DECISION_V1`（建议 `prompt_version="planner-decision-v1"`，**不要**叫 `planner-hierarchical-v8`：v8 会让人以为仍输出 `<plan_revision_proposal>`） | 新 tag `<planning_decision>`。用 `_revise` 从 v7 改输出合同，或全新 RoleTemplate。v1–v7 字节不动 |

**不新建、H1 禁止改行为的文件：**  
`plan_commits.py`、`compiler.py`、`refinement.py` 的 `chosen = selectable[0]`、`root_review.py`、只读叶守卫、repair reconcile。接线点只在 `event_handler.py` 的选择器 / 收集函数上加 **flag 分支**，默认走旧路径。

### 3.2 两个 legacy adapter 的映射规则

原则（§56 / §57）：**只映射能证明的语义；缺就留空。** 禁止补 `alternatives_considered`、`assumptions`、`reason_refs`。

#### `legacy_plan_proposal_to_decision`

公共字段（能证明才填）：

| Envelope 字段 | 来源 | 否则 |
|---|---|---|
| `schema_version` | 恒 `1` | — |
| `decision_id` | `proposal.proposal_id` | — |
| `subject` | request `context`（occurrence / task / obligation），**不是**模型写的 `goal_id` 单独说了算 | context 缺 → 适配失败，不猜 |
| `rationale` | `proposal.rationale` | — |
| `reason_refs` | **空**。`read_set` 是「读过什么」不是「理由引用」；二者语义不同，不自动拷 | 空元组 |
| `assumptions` | 空 | 空 |
| `alternatives_considered` | 空 | 空 |
| `stop_or_replan_conditions` | 空 | 空 |
| `model_declared_uncertainties` | 空 | 空 |

`decision_type` / `payload`（按 operations 形状，**不**看 rationale 文本猜）：

| 能证明的 operations | type | payload | 留下空的 |
|---|---|---|---|
| 恰好 1 个 `refine`，0 个 retire | `REFINE` | `RefineDecision(method_ref, parameter_bindings=bindings)`；`reuse_hints`/`preferred_order_hints` 空 | 假设、理由引用 |
| 1 个 `refine` + 1 个 `retire_method`（现网修复轮形状） | **不映射为 `REPAIR`**（Repair AST 是 H4，伪造即违反「能证明」） | 仍出 `REFINE` payload；**另**把 `retire_method.method_instance_id` 写入 Envelope 的非权威旁注字段 **仅当**我们决定加 `legacy_retire_instance_id: str \| None = None`（可选、文档标明非协议权威）。若不想加旁注：整单视为 **不可证**，adapter 返回 type=`NO_CHANGE` 且 payload 空 **不对** — 那是假语义。正确做法：adapter **拒绝转换**，调用方继续走旧 `parse_plan_proposal` 路径 | 不发明 RepairActionType |
| `operations == []` 且已是 `NoApplicableMethodDeclared` | `DECLARE_BLOCKED` | 空 | — |
| 恰好 1 个 `bind_shared_goal` 且 `resolution_id` 非空 | `REUSE_ACCEPTED_RESULT` | 只填能从操作读到的 id；reuse policy 仍由系统查 | 假设 |
| `propose_successor` | **不映射** | — | 无对应 DecisionType |
| 多 refine / 杂糅 | **不映射** | — | 现网 `compile_proposal` 也会拒 |

`running_work_policy`、`expected_plan_revision`、`mission_id`：**不**进入模型可写 payload。它们属于 request context。adapter 把它们拷到 `PlanningRequestContext`，不写进 Envelope 的模型字段。

#### `legacy_method_proposal_to_decision`

| 字段 | 值 |
|---|---|
| `decision_type` | `PROPOSE_METHOD` |
| `payload.method_proposal` | 原 `MethodProposal`（含 `declared_status`，admission 继续拒绝非 DRAFT） |
| `subject` | 参数传入，不从 method JSON 猜 |
| `rationale` | `proposal.rationale` |
| `reason_refs` / `assumptions` / `alternatives_considered` | 空 |

模型声明 `ADMITTED`：适配器照抄，**不**改写；拒绝仍由 `MethodRegistry.admit`（`registry.py:912`）。

### 3.3 Prompt v1 位置与旧 pin 保留

| 项 | 规定 |
|---|---|
| 新模板 | `runtime/role_templates.py`，`name="planner"`，`prompt_version="planner-decision-v1"` |
| 输出块 | `<planning_decision>{json}</planning_decision>`（§58） |
| 规则文本 | §59：可任意内部推演；最后必须一个 PlanningDecision；不伪造系统 ID；假设≠事实；不知用 REQUEST_EVIDENCE；缺方法用 PROPOSE_METHOD；路线不适用用 REPAIR（H1 提示可写这句话，但 admission 对 REPAIR 返回「本阶段不准入」具名码，避免模型以为能修） |
| 旧 pin | `HIERARCHICAL_PLANNER_VERSIONS` 仍含 v1–v7；`HIERARCHICAL_PLANNER_VERSIONS_BY_PACKAGE[3]` **不**加入 decision-v1 |
| 新配对 | 新增包号 `4`，仅当 `planning_protocol_version=="planning-decision-v1"`：`{planner-decision-v1}`。prompt 与包必须一起选（P2.3c P1-8 同类缺陷：v2 提示「没有 fact」配 v3 包 → `READ_SET_UNRESOLVED`） |
| 默认 | `_hierarchical_planner_template` 仍回 `PLANNER_HIERARCHICAL_V7` |
| 冻结表 | 新行加入 `FROZEN_PROMPT_DIGESTS`；顺手补分层 v1/v2；**一行旧 digest 都不得改** |
| MethodSynthesizer | 继续 `<method_proposal>`（§58）。H1 不统一进 PROPOSE_METHOD 的模型输出 |

### 3.4 `planning_protocol_version = "planning-decision-v1"` 接线点与默认策略

**默认：关闭。** 缺省 / 旧 Mission / 未写该键 = 现网 `<plan_revision_proposal>`。旧历史不重新解析（§55）。

建议记在 **Mission context bag**（与 `SEMANTICS_KEY = "orchestration_semantics_version"` 同形，`plan_commits.py:127`），**不要**在 H1 写入 `PolicySnapshot.prompt_versions` 白名单——那会改策略快照 digest。

| 接线点 | 文件:行 | flag 关 | flag 开 |
|---|---|---|---|
| 创建 Mission | `commit_service.py` 创建路径 / `MissionSpec` | 不写该键 | 写入 `"planning-decision-v1"` |
| 选 Planner 提示词 | `event_handler.py:3087` `_hierarchical_planner_template` | v7 | `planner-decision-v1`（且仅当 pin 属于包 4） |
| 拼包 `output_contract` | `planner_package.py:569` | `<plan_revision_proposal>` | `<planning_decision>`；`package_version` 新字符串，旧 `"planner-package-hierarchical-v4"` 字节留给关 flag 的路径 |
| 收 Planner 回复 | `event_handler.py:5022` `_collect_plan_hierarchical` | `parse_plan_proposal` → `apply_planner_reply` | `parse_planning_decision` → `admit_planning_decision`；**通过后仍**经 adapter **回** `PlanProposal` 再进现有 `compile_proposal` / `commit_plan_revision`（H1 不换 compiler） |
| 合成器 | `synthesis.py:729` / `event_handler` 合成意图 | 不变 | 不变 |

反向路径（新 Decision → 现网 compile）只对 `REFINE`（单 refine）和 `PROPOSE_METHOD`（交给 registry）实现。`REQUEST_EVIDENCE` 可调用现有 `evidence_round`（只读）。其余 type 在 admission 停住。

### 3.5 系统字段：谁绑定、模型写了如何拒绝

现网 14 项（`SYSTEM_BOUND_FIELDS`，金表 14，**H1 不得缩小**）：

`mission_id`、`principal`、`principal_id`、`scope`、`scope_id`、`manager_epoch`、`budget_account`、`budget_grant_revision`、`registry_status`、`opened_by`、`authorization_ref`、`grant_ref`、`provenance`、`authored_by`

计划 §7 额外点名、现网未列入拒绝集的：

| 字段 | 绑定来源 | 模型写入 Envelope / payload / reason_refs | 具名码 |
|---|---|---|---|
| 上表 14 项 | 与现网相同：Mission / principal / epoch / 账户 | 拒绝 | `SYSTEM_FIELD_FORBIDDEN`（计划枚举无此码，H1 **必须新增**；不得并进含糊的 `MALFORMED_DECISION` 以致测试无法点名） |
| `dispatch_generation` | 当前 dispatch intent | 拒绝 | `SYSTEM_FIELD_FORBIDDEN` |
| `plan_revision` / `expected_plan_revision`（权威值） | 当前 network.plan_revision（request context） | 拒绝。旧 PlanProposal **允许**模型抄写 expected；新 Envelope 改为 context 绑定 | `SYSTEM_FIELD_FORBIDDEN` |
| `approval_id` / `authorization` 权威 | 授权子系统 | 拒绝 | `SYSTEM_FIELD_FORBIDDEN` |
| `operation_id` | compiler / 执行账本 | 拒绝（可见 refs 里已有的除外，且只能出现在 reason_refs） | `SYSTEM_FIELD_FORBIDDEN` 或 `DECISION_REF_OUTSIDE_CONTEXT` |
| `acceptance_id` | 已有 Acceptance（仅当 ∈ visible_refs） | 当作新 ID 写入 payload → 拒绝；引用 visible_refs → 允许 | 同上 |
| `budget_grant` / `budget_account` | 预算子系统 | 拒绝 | `SYSTEM_FIELD_FORBIDDEN` |
| `registry_status` | `MethodRegistry.admit` | 拒绝（现网已拒） | `SYSTEM_FIELD_FORBIDDEN` |
| `PlanningSubject` 三元组 | 打开本轮 Planner 的 occurrence / task / obligation | 模型另写一个 occurrence / 改投 Mission | `SUBJECT_MISMATCH`（H1 新增具名码） |
| `decision_id` | 允许模型生成 **或** 系统在缺省时填写；不得与权威账本 ID 碰撞 | 与已有 operation/acceptance/grant 同形权威前缀 | 系统改写或 `MALFORMED_DECISION`；优先系统填写，模型字段视为 hint |

实现：`decision_codec` 对 Envelope 顶层、payload 对象、reason_refs 条目做与 `_refuse_authority_claims` 同构的检查。**方法参数名碰巧叫 `scope` 仍是值**（现网 `test_a_method_parameter_that_shares_a_name_with_a_bound_field_is_a_value`）。

旧 `parse_plan_proposal` **不**扩大拒绝集，否则 2960 里系统字段金表与分层提示词「列出每一个绑定字段」会红。

### 3.6 H1 对各 DecisionType 的准入

| type | H1 codec | H1 admission | 之后阶段 |
|---|---|---|---|
| `REFINE` | 是 | 是（method 存在、hash/version 匹配、非 RETIRED、∈ library、subject 匹配） | compile 仍走 `compile_proposal` |
| `SELECT_METHOD` | 解码 | `DECISION_TYPE_NOT_ENABLED` | H3 |
| `PROPOSE_METHOD` | 是 | 交给 `MethodRegistry.admit`，最多 TRIAL_ADMITTED | 已有 |
| `REQUEST_EVIDENCE` | 是 | predicate 已注册；不让模型指定 TRUE/FALSE | 执行复用 `evidence_round` |
| `REPAIR` | 解码 | `DECISION_TYPE_NOT_ENABLED` | H4 |
| `RETIRE_METHOD_USE` | 解码 | `DECISION_TYPE_NOT_ENABLED`（现网 retire 只作为 refine 伴侣，且必须是修复轮实例） | H4 |
| `REUSE_ACCEPTED_RESULT` | 解码 | 可选：id ∈ visible_refs 则记录，仍不跳过 reuse policy | 后续 |
| `DECLARE_BLOCKED` | 是 | 是（报告，不自动 Mission FAIL） | 现网 `no_applicable_method` |
| `REQUEST_HUMAN` | 解码 | `DECISION_TYPE_NOT_ENABLED` | 后续 |
| `NO_CHANGE` | 是 | 是 | — |

`DECISION_TYPE_NOT_ENABLED` 为 H1 新增具名码，避免把「协议认识但本阶段不执行」说成 malformed。

### 3.7 选 RETIRED method / stale version

| 情况 | 检查点 | 具名码 |
|---|---|---|
| method_id 不在 registry | `decision_admission` 调 `registry.definition` / `get_method` | `METHOD_NOT_FOUND` |
| id 在、version/content_hash 与可见条目不一致 | 对照 `visible_refs` 与 registry | `METHOD_VERSION_STALE` |
| 注册状态 `RETIRED`（`MethodRegistryStatus` `contracts/htn.py:274-284`） | registry | `METHOD_NOT_AUTHORIZED`（计划无 METHOD_RETIRED；用此码 + `problems[].detail` 写 `RETIRED`） |
| 根评审 / 只读叶已打标 `rejected_by_root_review` / `rejected_by_read_only_leaf` | 包内 library 行 | `METHOD_NOT_APPLICABLE`（与现网 `method_rejected_by_root_review` 同义；H1 不改 compile 字符串） |
| reason_ref 不在 visible_refs | admission | `DECISION_REF_OUTSIDE_CONTEXT` |
| 块缺 / 双块 / 非 JSON / 非 object | codec（`extract_block`） | `MALFORMED_DECISION` |
| 系统字段 | codec | `SYSTEM_FIELD_FORBIDDEN` |

---

## 4. 测试清单

新文件建议：`tests/orchestrator/full_target/test_planning_decision_protocol.py`（契约 + 负例 + adapter）。回归继续用现有 golden，不把旧测试改去调新 codec。

### 4.1 Round-trip

| # | 断言 |
|---|---|
| R1 | 合法 Envelope JSON 经 `parse` → 对象 → `serialize` → 再 parse，字段相等（含空元组缺省） |
| R2 | `REFINE` / `PROPOSE_METHOD` / `REQUEST_EVIDENCE` / `DECLARE_BLOCKED` / `NO_CHANGE` 各一条 |
| R3 | 块外散文可容忍（与 `test_prose_around_the_block_is_tolerated` 同形） |
| R4 | 块内 fenced ` ```json ` 可容忍 |
| R5 | `AssumptionHint` 往返；admission **不**把 assumption 写成 Fact |

### 4.2 Negative

| # | 输入 | 码 |
|---|---|---|
| N1 | 无块 / 双块 / 非法 JSON / 非 object | `MALFORMED_DECISION` |
| N2 | `reason_refs` 引用包中不存在的 observation / method | `DECISION_REF_OUTSIDE_CONTEXT` |
| N3 | 顶层或 payload 写 `mission_id` / `manager_epoch` / `dispatch_generation` / `plan_revision` / `registry_status` | `SYSTEM_FIELD_FORBIDDEN` |
| N4 | Subject.occurrence_id ≠ 本轮 context | `SUBJECT_MISMATCH` |
| N5 | `method_ref` 指向 `RETIRED` | `METHOD_NOT_AUTHORIZED` |
| N6 | version 或 content_hash 与 visible 条目不一致 | `METHOD_VERSION_STALE` |
| N7 | 未知 method_id | `METHOD_NOT_FOUND` |
| N8 | `REPAIR` / `SELECT_METHOD` / `REQUEST_HUMAN` / 单独 `RETIRE_METHOD_USE` | `DECISION_TYPE_NOT_ENABLED` |
| N9 | `PROPOSE_METHOD` 声明 `ADMITTED` | 仍由 registry 拒绝（现网语义），不得在 codec 里默默改成 DRAFT |
| N10 | `REQUEST_EVIDENCE` 指定结果为 TRUE | `MALFORMED_DECISION` 或专用码；不得记 Fact |
| N11 | 旧 `<plan_revision_proposal>` 喂给新 codec | `MALFORMED_DECISION`（缺 tag），**不得**自动当 Envelope |
| N12 | 新 `<planning_decision>` 喂给 `parse_plan_proposal` | 现网 `block_missing` / 若含 method 块则 `proposal_wrong_block` — **保持**，证明旧解析器没被教坏 |

### 4.3 Adapter

| # | 断言 |
|---|---|
| A1 | 单 refine `PlanProposal` → `REFINE`，`reason_refs`/`assumptions`/`alternatives_considered` 均为空 |
| A2 | `MethodProposal` → `PROPOSE_METHOD`，`declared_status` 原样 |
| A3 | retire+refine **不**变成带假 Repair AST 的 `REPAIR` |
| A4 | `NoApplicableMethodDeclared` → `DECLARE_BLOCKED` |
| A5 | `propose_successor` 不映射（明确失败/跳过，不编 type） |

### 4.4 旧行为不变（回归）

| 守卫 | 位置 | H1 必须 |
|---|---|---|
| legacy 事件 golden | `test_hierarchical_event_flow.py:1425` | 绿；`NEW_EVENT_TYPES` 仅当 H1 **真的**追加了新事件才扩表。H1 默认不应发新事件 |
| 旧函数 hash | `test_allocator_form_gate.py:478-485` | 绿，hash 常量不动 |
| `_new_mode` 哨兵 19 | `test_hierarchical_event_flow.py:1540` | **仍 19**。H1 禁止第 20 个 `self._new_mode(mission)`；flag 读已有 `new_mode` 对象或 Mission context |
| 冻结提示词 sha | `test_output_port_claims.py` + `test_planner_typed_proposal.py` `FROZEN_PLANNER_PROMPTS` | 旧行字节不变；只追加 |
| `SYSTEM_BOUND_FIELDS` 金表 14 | `test_planner_typed_proposal.py:299` | 旧集合不变 |
| 旧模式 560/13/0 | step02/05/06/07/p34/p35 | 零新增失败 |
| 分层默认提示词 | `_hierarchical_planner_template` 无 flag | 仍 v7 |

### 4.5 full_target 预期增量

按切片合计（允许 ±5，journal 写实测）：

| 片 | 约新增 |
|---|--:|
| 合同 round-trip | 8 |
| 负例 | 12 |
| adapter | 5 |
| 系统字段 / subject | 6 |
| visible_refs 收集器 | 3 |
| prompt 冻结追加（含 v1/v2 补钉 + decision-v1） | 3–4 参数化 |
| flag 默认关闭守卫 | 4 |
| 与 P2.3 交叉不接线 | 3 |
| **合计** | **约 +44–50** |

预期结束：**3004–3010 passed / 2 skipped**（基线 2960/2）。旧模式仍 560/13/0。

未跑本机 pytest；增量是计划值，合入以实测为准。

---

## 5. 建议切片（H1.a … H1.r）

每片独立可核验、工作量按 ≤1 天估。后片可依赖前片类型，但不得把 H2/H3/H4 范围带进来。

### H1.a 合同骨架

- **范围：** `contracts/planning_decisions.py`：`PlanningDecisionType`（10 值）、`PlanningSubject`、`PlanningDecisionEnvelopeV1`（payload 先用带 tag 的 union / 小协议）。导出加入 `contracts/__init__.py` 若现网有惯例；否则仅子模块导出，避免无调用方的大 `__all__`。
- **验收：** 构造 + `to_json`/`from_json` round-trip ≥4；非法 type 字符串拒。full_target +4。
- **风险：** 误改 `htn.py` PlanProposal 字段。
- **交叉：** 无。

### H1.b 系统字段集合与 Subject 绑定类型

- **范围：** `PLANNING_DECISION_BOUND_FIELDS`（14 + dispatch_generation / plan_revision / approval_id / operation_id / acceptance_id / budget_grant）。`PlanningRequestContext` dataclass（mission_id、principal、epoch、plan_revision、subject、visible_refs）。**不改** `SYSTEM_BOUND_FIELDS`。
- **验收：** 金表测试：旧 14 不变；新集合是超集。+3。
- **风险：** 把新字段塞进旧 parser。
- **交叉：** 无。

### H1.c AssumptionHint + ReasonRefs

- **范围：** `AssumptionHint`；`PlanningRef`（兼容 `ReadItem` 的 kind，含 `fact`）；Envelope.`reason_refs` / `assumptions`。不改 `TypedRefKind`。
- **验收：** assumption 不能经 from_json 变成 Fact；未知 kind 拒。+3。
- **风险：** 把 `fact` 加进 AER `TypedRefKind` 导致附件 schema 漂移。
- **交叉：** 无。

### H1.d RefineDecision + EvidenceRequestDecision

- **范围：** 两个 payload + `EvidenceQuestion`。`PROPOSE_METHOD` payload 引用现有 `MethodProposal`。
- **验收：** Refine 无 goal_id/obligation_id 字段（防模型写 ID）；EvidenceQuestion 无 polarity/result 字段。+4。
- **风险：** 把 `RefineOperation` 改成新形状，打断 `compile_proposal`。
- **交叉：** 无。

### H1.e RejectionCode + Feedback

- **范围：** 计划 §46 全码 + H1 增补 `SYSTEM_FIELD_FORBIDDEN` / `SUBJECT_MISMATCH` / `DECISION_TYPE_NOT_ENABLED`。`PlanningFeedbackV1`。
- **验收：** 枚举稳定、JSON 往返。+2。
- **风险：** 把 live `PlanningRejected.reason` 改成枚举，导致修复轮读字符串失败（P2.3j/m/v）。
- **交叉：** **P2.3j/m/v** 的 `root_review_rejected` / `read_only_leaf_needs_write` / `repeated_verification_failure` 必须仍是旧字符串。本片只加新类型，不改事件写入。

### H1.f decision_codec

- **范围：** `planning/decision_codec.py`：`extract_block(..., "planning_decision")`；系统字段扫描；`parse_planning_decision`。
- **验收：** R1–R4、N1、N3、N11、N12。+10。
- **风险：** 改 `extract_block` 全局行为。只加 tag，不改函数语义。
- **交叉：** `proposal_wrong_block` 提示词仍假设 Planner 不写 `<method_proposal>`。新 tag 不得被旧 `parse_plan_proposal` 误认为 method 块。

### H1.g visible_refs 收集器

- **范围：** `visible_refs_from_hierarchical_package`。输入=现包 dict。输出含 method 三元组、fact read_set_entry、open goals 的 occurrence/task/obligation、rejected_refinements 的 instance id。
- **验收：** 用 `fixtures/htn` 一份真包：列出的 id 都能在包里找到；包外 id 不出现。+3。
- **风险：** 在包里加字段导致 `HIERARCHICAL_PACKAGE_VERSION` / 请求 hash 漂移。本片 **只读、不改包形状**。
- **交叉：** P2.3q 把 `rejected_by_root_review` 与 `rejected_by_read_only_leaf` 分列 — 收集器两行都要纳入「可见但不适用」。

### H1.h decision_admission（引用 / 方法 / 版本）

- **范围：** `admit_planning_decision`：N2、N5、N6、N7、N8。不调用 commit。
- **验收：** 上列负例全红码对齐。+6。
- **风险：** 在 write transaction 里调模型（§70）— 本片无模型。
- **交叉：** 根评审打标方法与 `compile_proposal` `:3839-3846` 的 `method_rejected_by_root_review` 双重拒绝可接受；H1 不要删 compile 侧检查。

### H1.i request-context 绑定

- **范围：** parse/admit 签名强制 `context: PlanningRequestContext`。模型写的 subject 必须等于 context.subject。缺 context 直接拒，不从 JSON 猜 Mission。
- **验收：** N4；漏传 context 的测试。+3。
- **风险：** 从 Envelope 读 mission_id「方便一下」。
- **交叉：** 无。

### H1.j legacy PlanProposal adapter

- **范围：** `legacy_plan_proposal_to_decision` + A1、A3、A4、A5。
- **验收：** 单 refine 映射；retire+refine **不是** REPAIR AST；successor 不映射；空 refs。+5。
- **风险：** 为了「完整」补 reason_refs=read_set。
- **交叉：** P2.3s 的 retire+refine 仍只走旧 `apply_planner_reply`。adapter 测试用纯数据，不跑 reconcile。

### H1.k legacy MethodProposal adapter

- **范围：** `legacy_method_proposal_to_decision` + A2、N9。
- **验收：** type 恒 PROPOSE_METHOD；declared_status 不改写。+2。
- **风险：** 在 adapter 里把 ADMITTED 改成 DRAFT（现网明确禁止静默改写）。
- **交叉：** 合成器 `accept_response` 作者锁 MODEL（`synthesis.py:738`）不动。

### H1.l Planner prompt v1 + 旧 pin

- **范围：** 注册 `planner-decision-v1`；`FROZEN_PROMPT_DIGESTS` 追加；补分层 v1/v2 digest；`HIERARCHICAL_PLANNER_VERSIONS_BY_PACKAGE[4]`；选择器 **仅 flag 开** 才选新提示词。
- **验收：** 旧 digest 测试全绿；无 flag 时仍 v7；pin `planner-hierarchical-v5` 在关 flag 的分层 Mission 仍选 v5。+4。
- **风险：** **prompt/包错配**（最大风险之一）。flag 关时 output_contract 仍旧 tag。
- **交叉：** 无 live 修复轮提示词改写。`planner-hierarchical-v7` 关于两个 rejected_by_* 字段的句子保持。

### H1.m `planning_protocol_version` 接线（默认关）

- **范围：** Mission context 读写；`_collect_plan_hierarchical` / `_hierarchical_planner_template` / 拼包 output_contract 三处 flag。开 flag 的集成测试用脚本化 fixture，**一条** REFINE → adapter 回 PlanProposal → 现有 compile（可选，若一天不够则本片只做选择器+包合同，compile 留给 H1.n）。
- **验收：** 默认关：既有 `test_htn_end_to_end` / 修复轮 / 合成 全绿。开 flag：新 codec 被调用（可用 monkeypatch/计数探针）。+4。
- **风险：** 默认开导致 2960 红。**默认必须关。**
- **交叉：** 见 §5.1。

### H1.n 开 flag 的最小正向路径（可选同一天，否则独立）

- **范围：** flag 开 + 脚本化 `<planning_decision>` REFINE → admit → 转旧 `PlanProposal` → `compile_proposal` → `commit_plan_revision`。仅单 refine。不碰 repair。
- **验收：** 真 `HierarchicalDispatch` 上 plan_revision +1。+2。
- **风险：** 转换时把 context 的 plan_revision 交给模型字段。
- **交叉：** 不得走 P2.3s reconcile（无 retire）。

### H1.o 负例集中文件

- **范围：** 把 N1–N12 收成参数化，变异至少杀「漏检系统字段」「可见 refs 当摆设」。
- **验收：** 12 负例 + 2 变异叙述。计数计入 4.5。
- **风险：** 用源码字符串断言代替行为。
- **交叉：** 无。

### H1.p 回归守卫片

- **范围：** 跑（或至少定向）：事件 golden、frontier/allocate hash、`_new_mode==19`、冻结 sha、旧模式 560/13/0、full_target 全量。journal 写实测。
- **验收数字：** full_target ≥2960+本阶段增量 / 2 skip；旧模式 560/13/0；`_new_mode` 19。
- **风险：** 有人在 event_handler 加第 20 处 `_new_mode`。
- **交叉：** 全。

### H1.q 与 P2.3m/o/p/q/s/t/u/v 的「不接线」守卫

- **范围：** 测试证明：H1 默认路径仍调用 `parse_plan_proposal`；read-only 叶取消理由仍是 `read_only_leaf_needs_write`；修复轮仍吃 `<plan_revision_proposal>` retire+refine；空 Planner 短路仍看 `no_applicable_method`；连续相同验证失败仍写 `repeated_verification_failure`。
- **验收：** +3 行为断言（不要只 grep 源码，除非外部不可观察）。
- **风险：** 为新协议改 `REPAIR_REASONS` 集合。
- **交叉：** 本片就是交叉清单，见下。

### H1.r journal + 审阅对照

- **范围：** SDK journal（中文）+ Host 本拆解回写实测计数。对照计划 §69（8 项交付）与 §70 审阅必查。
- **验收：** 8 项均有路径；§70 十条打勾（尤其：未给 LLM 新 authority、未让 LLM 写系统字段、未在 write txn 调模型、未第二套 TaskNetwork）。
- **风险：** 「类型定义好了」宣布完成（§69 禁止）。
- **交叉：** 无代码。

### 5.1 与已实现 P2.3m/o/p/q/s/t/u/v 的交叉

这些机制已在 `c0e13a4` 上工作。H1 **默认不得改它们的触发字符串、事件名、compile 形状**。

| 片 | 机制 | 交叉点 | H1 规则 |
|---|---|---|---|
| P2.3m | 只读叶改写有界，升级到规划层 | `READ_ONLY_REWRITE_REPAIR_REASON`；包内 `rejected_by_read_only_leaf` | 新 Envelope 不要把该理由改名。修复轮仍旧块 |
| P2.3o | 下游工作区预铺上游已验收产物 | overlay / `inputs`；与 Planner 协议无关 | 不碰 |
| P2.3p | 交接后连续 UNKNOWN 有界停机 | `_planning_rejected` 里 `provider_outcome_unknown`  streak | 新 codec 失败仍走同一 `_planning_rejected` 门，不要另开梯子 |
| P2.3q | 修复轮复用已验收只读叶；空 Planner 短路；拒绝理由分字段 | `PlannerRoundSkippedForSynthesis`；`no_applicable_method`；v7 提示词 | `DECLARE_BLOCKED` 仅 flag 开时对应空操作；关 flag 仍空 operations + 前缀 rationale。不得让短路去等新 tag |
| P2.3s | retire 前 reconcile 仍 OPEN 的兄弟 attempt | `apply_planner_reply` `:3704`；`RepairCompileDeferred` | flag 开的正向路径 H1.n **禁止**带 retire。adapter 不把该对说成 REPAIR |
| P2.3t | 写型 tests 端口；根评审修复上限具名停机 | `max_root_review_repairs`；synthesizer v7 / worker v3–v4 | 不改提示词旧版；不停机字符串 |
| P2.3u | 只读叶写守卫（事前拒已有文件） | `read_only_existing`；worker-hierarchical-v4 | 不碰 Worker |
| P2.3v | 相同验证失败有界早停 | `repeated_verification_failure` → 修复轮 | 同 P2.3m：理由字符串冻结 |

根评审 repair（P2.3j 起）：findings 走 `PlanningRejected{root_review_rejected}`，包 `rejected_refinements`，提案必须是 **一条** retire_method + **一条** refine。H1 的 `REPAIR` type 只占位。

只读叶（P2.3k/m/u）：与 Decision 协议正交。H1 测试不要用只读叶夹具「顺便」验证 Envelope。

---

## 6. 风险与不做清单（§80 口径）

### 6.1 H1 最大风险（按杀伤力）

1. **Prompt / 包 / 解析器错配**  
   默认路径若被换成新 tag 而包仍写 `<plan_revision_proposal>`（或相反），真实模型局会重演 P2.3b/c 的 `proposal_unreadable` / `READ_SET_UNRESOLVED`。缓解：默认关 flag；包号与提示词集合硬配对；N11/N12。

2. **默认启用新协议或改旧 parser 拒绝集**  
   2960 基线、修复轮、合成器、空 Planner 短路会连环红。缓解：live 默认仍 `parse_plan_proposal`；`SYSTEM_BOUND_FIELDS` 14 项金表不动。

3. **Adapter 补假语义**（次主）  
   把 `read_set` 当成 `reason_refs`、把 retire+refine 当成 Repair AST。缓解：§3.2 空字段测试 A1/A3。

4. **第 20 处 `_new_mode` 或新事件进入 legacy 路径**  
   哨兵与 golden 会红。缓解：H1.p。

### 6.2 明确不做（计划 §80 + H1 收窄）

本计划完成条件 **不包括**（整个 LLM-native 计划，不单 H1）：

```text
实现通用 A*
实现通用 MCTS
复制 Aries
复制 PANDA
完整 PDDL 支持
完整 temporal planning
训练 learned planner
在线 RL
全部世界状态形式化
```

H1 **额外不做**：

- 删除或停止解析 `PlanProposal` / `MethodProposal`
- 重写 `MethodContract` / 生命周期晋级（H6）
- RepairDecision AST 与 impact analysis（H4）
- 把 `selectable[0]` 改成问 Planner（H3）
- 统一 PlannerPackage V1、删除分散拼包（H2）
- PlanningBackendPort / 改 PANDA（H7）
- 让模型写 `PlanRevision` / 系统 authority
- 把 UNKNOWN 当 FALSE
- 第二套 TaskNetwork / Budget / Operation ledger
- 在数据库事务中调用 LLM
- 为新 Domain 在 HTN core 加 `if domain`
- 把 Solver SOLVED 当 Acceptance
- 改 PolicySnapshot digest、新增配置项、第 20 个 `_new_mode`
- 重跑 Grok 验收（H0 只记录题单）
- 把旧 `PlanningRejected` 历史迁移成新 Feedback

### 6.3 §70 审阅必查（H1 预答）

| 必查 | H1 预期 |
|---|---|
| 是否增加了新的 authority？ | 否。admission 通过 ≠ commit |
| 是否让 LLM 决定系统字段？ | 否。`SYSTEM_FIELD_FORBIDDEN` |
| 是否把 UNKNOWN 当 FALSE？ | 否。REQUEST_EVIDENCE 不写 polarity |
| 是否引入 domain hardcode？ | 否 |
| 是否把 Solver result 当 Acceptance？ | 不涉及 |
| 是否出现 second TaskNetwork truth？ | 否。compile 仍旧 |
| 是否重置 Obligation？ | 否 |
| 是否绕过 Commit？ | 否 |
| 是否在 write transaction 调模型？ | 否 |
| 是否用文本相似度决定 Operation identity？ | 否 |

### 6.4 §69 每阶段交付（H1 DoD）

1. code（§3.1 文件）  
2. contract tests（round-trip）  
3. negative tests（§4.2）  
4. state/recovery：H1 无新持久形状则用「重启后旧 Mission 仍走旧 parser」一条  
5. backward compatibility（§4.4）  
6. design journal（中文）  
7. independent review  
8. exact acceptance report（实测 2960+Δ / 2，560/13/0，哨兵 19）

不能：「类型定义好了」→ 宣布完成。

---

## 7. 实施顺序（最短路径）

```text
H1.a → H1.b → H1.c → H1.d → H1.e
         ↓
      H1.f codec
         ↓
      H1.g visible_refs → H1.h admission → H1.i context
         ↓
      H1.j / H1.k adapters（可并行）
         ↓
      H1.l prompt+pin → H1.m flag 默认关 → H1.n 可选正向
         ↓
      H1.o 负例收口 → H1.q 交叉守卫 → H1.p 全量回归 → H1.r journal/审阅
```

H1.j 与 H1.k 无互相依赖，可并行。H1.l 不得早于 H1.m 的「默认关」测试，否则容易把新提示词做成默认。

---

## 8. 关键源码索引（实施时打开）

| 主题 | 位置 |
|---|---|
| PlanProposal / RefineOperation | `contracts/htn.py:2482, 2550, 2604` |
| MethodProposal / admit | `planning/htn/registry.py:757, 912` |
| parse_plan_proposal / SYSTEM_BOUND_FIELDS | `planning/planner.py:33, 115, 132, 171` |
| extract_block / repair_hint | `runtime/output_blocks.py:29, 170` |
| 分层包 | `planning/htn/planner_package.py:67, 456, 549, 569` |
| apply / compile | `orchestrator/hierarchical_dispatch.py:269, 2753, 3676, 3760` |
| 收 Planner 回复 | `orchestrator/event_handler.py:3048, 3087, 4456, 5022` |
| commit | `orchestrator/plan_commits.py:115, 293, 560` |
| 提示词 v7 / 配对 | `runtime/role_templates.py:655, 693, 715, 719` |
| selectable[0] | `planning/htn/refinement.py:803` |
| 取证 | `planning/htn/evidence_round.py:48`；`refinement.py:274, 350` |
| 合成 | `planning/htn/synthesis.py:729` |
| TypedRef | `contracts/semantic_base.py:313, 420` |
| 冻结 sha | `tests/orchestrator/full_target/test_output_port_claims.py:193` |
| `_new_mode` 哨兵 | `tests/orchestrator/full_target/test_hierarchical_event_flow.py:1540` |
