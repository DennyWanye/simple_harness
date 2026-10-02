# HTN 补齐 阶段 A 第 1 条：删除实施小结

- 日期：2026-10-03
- 依据：`HTN补齐-开工前裁决-2026-10-02.md` 第二节、第三节
- 范围：裁决第三节所列删除项（"旧的 2000 条窗口检索"按裁决挪到 A′，本次不做）
- 改动只留在工作树，未提交。路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`，`T/` = `sdk/simple-harness-sdk/tests/orchestrator/full_target/`

---

## 一、逐项删除内容

### 1. 对外操作三种状态（控制、效果、记账）及就绪判断里的读方

- `SDK/contracts/resolution.py`：删 `OperationControl`、`EffectOutcome`、`AccountingState`、`_PRE_HANDOFF_CONTROL`、`OperationCurrentState`，以及 `__all__` 里的对应项。
- `SDK/graph/eligibility.py`：删 `UNSETTLED_EFFECTS`、`PendingOperation`、`EvidenceView.pending_operations`、`_operation_gate`、门序列里的 `_operation_gate` 一项、`ReadinessReason.WAITING_OPERATION_UNKNOWN`；模块文档里"十二种原因"改为"十一种"。`READINESS_PRECEDENCE` 由门序列推出，自动少一项。
- **保留** `EvidenceView.operation_range_revision`（读集里"不存在冲突写者"那一项仍在用）。
- `SDK/orchestrator/hierarchical_dispatch.py` 构造 `EvidenceView` 时本来只传见证，不用改。
- 前端 `tauri-app/src/views/liveGraph/model.ts`：删就绪原因 `WAITING_OPERATION_UNKNOWN` 的中文标签。前端别处没有用到这个值（同文件第 146 行的"操作结果核对中"属于另一个阶段值 `AWAITING_OUTCOME_REVIEW`，保留）。

### 2. `ReconciliationResult`

- `SDK/contracts/resolution.py`：删 `ReconciliationOutcome`、`ReconciliationResult`、`may_rehandoff`、只被它用的 `RECONCILIATION_RESULT_SCHEMA_VERSION`，以及随之不再使用的 `QueryCompleteness`、`MAX_REASON` 导入。
- 样例：删 `T/fixtures/aer/reconciliation-result.{schema,valid,invalid}.json` 三个文件；`T/fixtures/aer/index.json` 删两条索引。
- `T/fixtures/SOURCE.md`：删三行清单，`aer/index.json` 的哈希改为删减后的值，并新增"本地删减"一节说明。

### 3. `ExecutionFeedbackV1`

- `SDK/contracts/htn.py`：删 `FeedbackOutcome`、`DiagnosisCategory`、`FeedbackObservation`、`FeedbackDiagnosis`、`ExecutionFeedbackV1`、`EXECUTION_FEEDBACK_SCHEMA_VERSION`，以及 `__all__` 里的对应项。
- 样例：删 `T/fixtures/plan_pack/execution-feedback-v1.schema.json`；`T/fixtures/plan_pack/fixtures.json` 删 3 条（只删条目，其余字节不变）。
- `T/fixtures/SOURCE.md`：删 schema 一行，`plan_pack/fixtures.json` 的哈希改为删减后的值。

### 4. `leaf_decision` 一组

- `SDK/planning/htn/refinement.py`：删 `AttemptPolicy`、`LeafDecision`、`leaf_decision`、`bounded_attempt_admissible`。
- `SDK/planning/htn/registry.py`：删只被它们用的 `TaskTypeSpec.low_risk`。

### 5. `achieve_outcome` 一组

- `SDK/contracts/obligations.py`：删 `AchieveOutcomeAdmission`、`Selector`、`AchieveOutcomeDecision`、`achieve_outcome_admission`，模块文档和 `set_lifecycle` 注释里提到它的话改写，`__all__` 删四项。
- `SH/workflow/definition.py` 里的另一个 `Selector` 未动。

### 6. `refine()` 的语义判断部分

- `SDK/planning/htn/refinement.py`：删 `RefinementOutcome`、`CandidateAssessment`、`_STATUS_RANK`、`assess_candidates`、`RejectedCandidate`、`MethodProposalRequest`、`RefinementDecision`、`RefinementReport`、`refine`、`_refine_one`（含按义务扣燃料）、`_no_method`、`_task_type_for`、`_ancestor_repeat`、`_open_children`；模块文档改写为"规划前沿 + 待证据"，并写明"选哪种做法是规划器（LLM）的判断"。文件从 1091 行减到约 260 行。
- **保留** `FrontierItem`、`planning_frontier`、`EvidenceOccurrenceRequest`、`UnknownProposition`、`unknown_predicates`、`evidence_requests`。
- `SDK/planning/htn/__init__.py`：删 `AttemptPolicy`、`LeafDecision`、`MethodProposalRequest`、`RefinementDecision`、`RefinementOutcome`、`RefinementReport`、`refine` 的导入和导出；包文档里 `refinement.refine` 一段改写。
- 燃料：按裁决，按义务扣燃料随 `refine` 删除，生产仍在计划提交开子义务时扣（`plan_commits.py`）。账本本身的燃料规则（含 `BOUND_REACHED`）在 `T/test_htn_recursion_fuel.py` 里照旧钉住。

### 7. 调度里恒为 0 的"解锁价值"打分项

- `SDK/scheduling/allocator.py`：删 `WEIGHTS["unlock_value"]`、`_dependents`、`dependents = _dependents(live)` 和 `"unlock_value": ...` 计算行；公式注释改为去掉这一项，并写明删除原因。
- `SDK/context/retrieval.py` 的 `WEIGHTS` 未动。
- 已知影响（裁决已说明）：开发库已绑定的旧策略里还带 `unlock_value=0.2`，打分只按现有分项取权重，多出的键被忽略；权重哈希会变，启动时记一次漂移，属预期。

### 8. `RecordVersion`、`ValidityRevision`

- `SDK/contracts/htn.py`：删两个类型和构造函数 `record_version()`、`validity_revision()`，`__all__` 删四项；"五类版本轴"的说法改为列出实际在用的轴。全库无其他引用。

### 9. 支持、假设、监督三类边

- `SDK/contracts/htn.py`：删 `RelationKind.SUPPORT / ASSUMPTION / SUPERVISION`、只被它们用的 `EndpointKind.EVIDENCE / SUPERVISOR`、`_EVIDENCE_TARGETS`、端点表三行；`TypedEdge` 文档删掉这三类的说明。
- `SDK/graph/task_network.py`：删 `SupportView`、`SupervisionView`、`support_view()`、`supervision_view()` 和 `__all__` 两项，模块文档删对应段落。

---

## 二、测试改动

### 删除的用例（测的是被删代码自身，删掉理由见括号）

| 文件 | 删除的用例 | 理由 |
|---|---|---|
| `T/test_readiness_reasons.py` | 第 9 节"等待未知操作"8 个用例；变异自证 `test_mutant_operation_gate_lets_an_unknown_effect_through` | 测被删的 `_operation_gate` 和 `PendingOperation` |
| `T/test_aer_contract_codecs.py` | 对账 3 个（`may_rehandoff` 等）；操作三轴 5 个；`test_an_observation_must_arrive_with_at_least_one_piece_of_evidence` | 测被删的 `ReconciliationResult`、`OperationCurrentState`、`FeedbackObservation` |
| `T/test_semantic_binding_codec.py` | `test_worker_feedback_may_not_attribute_its_own_evidence_to_the_system` | 测被删的 `ExecutionFeedbackV1`；"模型不能冒充系统来源"在同文件 `PlanProposal` 用例里仍有覆盖 |
| `T/test_projection_integrity.py` | `test_support_view_keeps_sets_apart`、`test_supervision_view_maps_scope_to_occurrences` | 测被删的两个视图 |
| `T/test_htn_recursion_fuel.py` | `achieve_outcome` 三个用例 | 测被删函数；燃料与 `BOUND_REACHED` 用例全部保留 |
| `T/test_htn_and_or_shared_goal.py` | `test_the_applicable_alternative_is_chosen`、`test_both_alternatives_are_assessed_before_one_is_chosen`、`test_two_applicable_alternatives_leave_the_choice_deterministic`、`test_no_applicable_alternative_asks_for_a_new_method`、`test_a_candidate_comparison_is_bounded_by_the_budget` | 测 `refine` 自己"选哪种做法、没做法时要新做法、候选截断"的语义判断，产品里这是规划器（LLM）的事 |
| `T/test_htn_novel_method_admission.py` | "空做法库"一节 4 个用例 | 测 `refine` 的 `MethodProposalRequest` 和它的燃料行为 |
| `T/test_compound_gate_no_pseudo_cycle.py` | `test_refining_a_duty_nobody_opened_says_so_instead_of_crashing` | 测 `refine` 的 `OBLIGATION_NOT_OPENED` 结局 |
| `T/test_seed_methods.py` | 递归节：选中递归做法、首次扩展扣 1 点燃料、燃料耗尽 5 个、重复扩展 1 个、无状态变化 2 个；证据节："高风险叶子不派发"；尝试策略节 6 个（`bounded_attempt_admissible`、`leaf_decision`）；"证据与燃料"节 3 个 | 都是 `refine` / `leaf_decision` 自身的选择、扣燃料、叶子判定语义 |

### 改写的用例（改用真实编译器路径造草稿，不为测试保留 `refine`）

- 新增测试辅助 `T/fixtures/htn/htn_world.py::ground_draft`：做法由测试**点名**（产品里由规划器在 `RefineOperation` 里点名），然后走与 `SDK/planning/plan_preview.py::compile_candidate_from_snapshot` 相同的 `assess_method` → `ground_method` 两步造草稿，再交给 `SDK/planning/htn/compiler.py::compile_refinement_bundle`。可选传入证据快照（部署世界用库里读出的快照）。
- `T/test_htn_and_or_shared_goal.py`（保护"或"分支和共享逻辑）：
  - `test_the_refuted_alternative_is_reported_as_refuted` 改为 `..._and_cannot_be_grounded`：直接用 `assess_method` 判被否定的分支，并断言它在编译器路径上造不出草稿；
  - "输掉的分支不产生节点""根不被输掉的分支卡住""每个节点只采用一种做法""一份做法的槽位全部必需"四个用例改用 `ground_draft` 造草稿，断言不变；
  - 共享一节原本就走 `assess_method` + `ground_method` + 编译器，未改。
- `T/test_compound_gate_no_pseudo_cycle.py::test_a_newly_opened_sub_goal_can_be_refined`：开出子义务后，账本里有该义务，子目标用点名做法经编译器路径造草稿并编译成功、被采用。
- `T/test_htn_deployment_wiring.py` 第 3 节（证据轮与"或"分支）：原 `DemoWorld.refine()` 拆成三个产品形状的入口——`evidence()` 按 `HierarchicalDispatch.run_evidence_round` 的走法（对目标的每份做法取未知前提 → `evidence_requests`）、`status()` 用部署世界的快照跑 `assess_method`、`draft()` 走 `ground_draft`。各用例断言改为：看之前两个分支都是"待证据"；看之后被否认的分支是 `PRECONDITION_FALSE`、另一支 `APPLICABLE` 且能造出草稿、草稿槽位只有 `shared`、`b`；观察器不可用或崩溃时两支仍是"待证据"、库里没写任何记录；已定的命题第二轮不再问。`test_the_denied_alternative_loses_and_the_other_one_is_chosen` 改名为 `test_the_denied_alternative_is_refuted_and_the_other_one_applies`，两处变异说明同步改名。
- `T/test_seed_methods.py`：
  - `decompose` 改为"点名做法 → `ground_draft` → 编译器"，代码、AppWorld、自造 widget 三个领域的"能分解""编译出合法增量""走同样的调用"照旧覆盖；
  - 两个"选另一分支"用例改为：被否定的分支 `PRECONDITION_FALSE`（AppWorld 那条还断言造不出草稿），另一分支能编译出合法增量；
  - 递归节保留结构性断言：递归做法开出同目标类型的复合子节点、能展开第二层、递归子节点不开新义务（`new_obligations` 与 `obligation_openings` 都为空）、基础做法编译后规划前沿为空；
  - 证据节改为直接用保留的 `unknown_predicates` + `evidence_requests` 对 patch 叶子的前提求证据请求（只读、指名谓词），并新增"前提已确定就不要证据"；
  - HDDL 一节的 `code_delta` 和"未展开复合节点"用例改用新 `decompose`。
- 其他断言更新：`T/test_readiness_reasons.py` 原因总数 12 → 11、各节编号顺延；`T/test_aer_contract_codecs.py` 样例数 8 → 6 并改名，`MAX_REASON` 边界用例改用 `CriterionOutcome.limitations` 承载（原来借 `ReconciliationResult.explanation`）；`T/test_semantic_binding_codec.py` 计划包样例 13 → 10 并改名为 `test_all_ten_plan_fixtures_land_on_the_expected_side`，样例来源清单行数 18 → 14，与上游逐字节比较时跳过两个删减过的索引文件（`LOCALLY_TRIMMED_COPIES`，本表哈希仍核对）；`T/test_projection_integrity.py` 中"三类边不进执行投影"改为"资金、替代两类边不进执行投影"，`SUPERVISION` 空集断言改为 `FUNDING`；`T/test_semantic_binding_codec.py` 端点用例删三类边的行，补一行"资金边目标端点不对"；`T/test_obligation_conservation.py` 删一行 `achieve_outcome_admission` 断言和文档里的提及；`tests/orchestrator/step05/test_allocator_priority.py` 删 `unlock_value` 权重与分项，期望分数去掉 `0.20 × 2/3`。

---

## 三、编码清单与部署清单

- **编码清单** `SDK/graph/network_codec_manifest_v5.json`：全部删除改完后**只重写了一次**。做法：对每个条目按 `source_path`（相对 `src/`）重新计算 sha256 写回，其余字段不动。实际变化的源文件是 `contracts/htn.py`（7 个编码条目）和 `graph/task_network.py`（1 个支撑条目）；`models.py`、`state_machines.py`、`evidence_state.py` 本次未改，哈希不变。重写后已清 `src` 下的 `__pycache__`。
- **此后开发库旧任务的执行图历史读不出**：编码清单哈希一变，按旧清单哈希写下的执行图文档读回时会被判为 `NETWORK_DOCUMENT_INVALID`。开发期不兼容旧数据，按约定不做兼容。
- **部署清单**：在 `sdk/simple-harness-sdk` 下执行 `uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream ../../.local-test-evidence/2026-09-27/batch2a-upstream/evidence.json` 重生成（新部署编号 `d9a8006f…`，源文件 704 个），随后清 `__pycache__`。注意：主会话同时在 `SDK/observability/` 新增文件；如果它在此之后又加了源文件，需要再重生成一次。

---

## 四、定向测试（未跑全量回归）

| 范围 | 结果 |
|---|---|
| 被改文件对应测试 + 引用被删符号的测试：`test_readiness_reasons`、`test_aer_contract_codecs`、`test_semantic_binding_codec`、`test_projection_integrity`、`test_seed_methods`、`test_htn_deployment_wiring`、`test_htn_novel_method_admission`、`test_htn_and_or_shared_goal`、`test_compound_gate_no_pseudo_cycle`、`test_htn_recursion_fuel`、`test_obligation_conservation`、`step05/test_allocator_priority`、`test_allocator_form_gate` | 867 通过，1 跳过（上游计划包不在本机，逐字节比对按设计跳过） |
| `tests/orchestrator/full_target/taskgraph_exec` | 50 通过 |
| 其余导入了被改模块（`graph.eligibility`、`graph.task_network`、`contracts.resolution`、`contracts.obligations`、`planning.htn` 包与 `refinement`、`registry`、`scheduling.allocator`、`network_codec`）的测试文件，共 49 个 | 1340 通过，1 跳过（真实模型冒烟，需 `--run-real-provider`） |
| 前端 `src/views/liveGraph/model.test.ts`、`LiveGraph.test.tsx` | 18 通过 |
| 前端 `npm run typecheck` | 通过 |

改坏检验（变异）未做，留给主会话。

---

## 五、需要主会话复核的点

1. `MethodRegistry.note_trial_use` 唯一的生产调用方是被删的 `refine`；现在没有生产调用方，`trial_uses` 恒为 0，但 `SDK/orchestrator/taskgraph_plan_sources.py:163` 仍把它放进规划来源。本次没有删（不在裁决清单里），建议并入后续清理或补一个产品里的写方。
2. 删减后的 `aer/index.json` 与 `plan_pack/fixtures.json` 不再与上游计划包逐字节一致；漂移检查对这两个文件只核对 `SOURCE.md` 记录的哈希。
3. 被删的"按义务扣燃料、重复扩展、无状态变化、燃料耗尽报边界"这些 `refine` 层面的端到端保护不再有；账本层面的同名规则仍由 `test_htn_recursion_fuel.py` 钉住，生产在计划提交开子义务时扣燃料。是否要在计划提交路径补对应的端到端用例，请主会话判断。
4. 删除后的部署清单把主会话当时已在工作树里的 `observability/` 新文件也算进去了。
