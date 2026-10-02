# HTN 补齐 · G0 覆盖清单审计（全业务事件重放 v3）

日期：2026-10-03　审计人：审计子代理（Opus）　对象：`sdk/simple-harness-sdk/src/agent_orchestrator/observability/business_replay_inventory.json` 里 86 张 business 表

## 一、结论先说

- 86 张业务表，写入口一共 275 个：Python 函数 128 个，加上保证通道屏障触发器 147 个（这 147 个全部写 `validity_epochs`）。
- **完全覆盖 55 张，部分覆盖 22 张，完全不发事件 9 张**。
- 完全不发事件的 9 张：`assurance_blob_pins`、`bound_inputs`、`imported_usage`、`justification_sets`、`obligation_relations`、`support_members`、`taskgraph_requirements`、`tool_calls`、`workspaces`。其中 `bound_inputs`、`justification_sets`、`support_members`、`obligation_relations` 在 SDK 和 Host 的生产代码里都没有调用方，只在测试里用。
- 最大的成片缺口有四处：
  1. **审阅三件套**（`review_packages`、`review_records` / `criterion_evaluations`、`input_manifests` / `input_manifest_bindings`、`validity_witnesses`）。叶子验收那条路径完整（在 `CommitService._accept_result` 的事务里，与 VerificationPassed、AcceptanceCommitted 同事务）。但中间目标组合审阅（`CompositionAcceptanceAssembly.resolve_one`）是先写、后在另一个事务里 `commit_goal_resolution`；根审阅的切包（`RootReviewCoordinator.cut`）和记录（`record_review`）、操作结果审阅记录（`record_operation_outcome_review`）、保证通道下的操作提议审阅预备（`ensure_assured_proposal_reviews`）这几条路径，都是写了不发事件。
  2. **规划决定**：`planning_decisions` 的大多数写入（进度状态、证据轮、过期、延后修复）不发事件，只有拒绝、重试授权、问人、提出做法、物化子任务这几个分支有事件同行。`planning_repair_continuations` 的认领和状态迁移也全部不发事件。
  3. **只发信号的表**：`observations`、`planning_lane_grants`、`planning_request_authority_bindings`、`validity_witnesses` 的写入口只发 `TaskGraphSourceChanged`，而且只在启用执行图的任务里发；事件里只有来源引用，没有行内容。
  4. **`commit_receipts`**：保证通道很多事实只记成回执、不发事件（审阅对象钉住、根与时钟、审阅调用被打断、方针种子与漂移、做法登记、评测预言）。
- **`gaps` 字段没能写进 JSON。** `business_replay.py` 的 `inventory()` 规定 business 条目只允许 `class/fields/note/writers/events` 五个键（第 61～64 行），多一个 `gaps` 就会抛 `InventoryError`，守护测试的 3 条全部失败（已实测）。按约束我不能改 .py，所以 JSON 里只填了 writers 和 events，测试 3 条全过；缺口完整列在本文第三节。主会话把 `"gaps"` 加进那个允许集合以后，运行 `python3 <scratchpad>/apply.py --with-gaps` 就能把缺口原样写回 JSON（脚本在本会话 scratchpad：`/private/tmp/claude-501/-Users-taiwan-PROJECTS-SimplaHarness/f717df17-0265-4f32-a069-7b56d3015a4f/scratchpad/apply.py`，需要同目录下的 `W.json` 和 `trig_writes.txt`）。

## 二、口径与做法

- **写入口**：生产代码里直接执行 INSERT/UPDATE/DELETE 的函数。动态表名按调用点还原到具体函数，例如：`Store._cas` 归到 `update_mission` / `update_task` / `update_attempt`；`AssuranceStore._insert` 归到各个 `record_*`；`OperationCompletionStore._insert_exact` 归到各个 `insert_*`；`assurance_review_transport.ensure_review_invocation` 和 `PreparedOfficialReview.import_locked` 直接调用 `_insert`，它们本身就算入口。迁移脚本里的写入记成 `agent_orchestrator/storage/assurance_upgrade.py:DDL`、`Store._bind_legacy_missions`、`Store._renumber_artifact_lineage`。`evaluation/` 在 src 里，按生产代码计入。
- **同一事务**：`Store.transaction()` 和 `atomic()` 可以重入，内层并入最外层事务。所以判定标准是：写表和 `append_event` / `_emit` / `append_hierarchical_event` 落在同一个最外层事务里。逐个入口顺着调用链往上找最外层事务，再看这个事务里发了哪些事件。`events` 是这个写入口所有生产路径上同事务事件的并集；只要有一条路径不发，就记一条缺口。
- **条件事件**：`TaskGraphSourceChanged` 是写入口自己发的，计入 `events`，但只在启用执行图的任务里发，所以同时记成缺口。
- **触发器**：屏障 SQL 里 147 个 `assurance_source_*` AFTER 触发器写 `validity_epochs`，并往 events 写 `AssuranceEvidenceChanged`。它们作为 `trigger:<名>` 记进 `validity_epochs` 的 writers，`AssuranceEvidenceChanged` 计入 `validity_epochs` 的 events。对于被监视的源表（sources、artifacts、obligations 等 40 多张），这个触发器不是它们的写入口，所以**没有**把 `AssuranceEvidenceChanged` 算作这些表的覆盖事件。原因见第五节：这个事件不带行内容，而且只在保证通道任务里发。
- **方法**：先用 AST 扫全部字符串字面量找写表语句，再按名字建调用图，最后人工逐条核对调用点和事务边界。改名冲突（如 `register`、`prepare`、`persist`、`_record`）已人工排除。
- **只核对有没有事件**：本审计不评估各事件的 payload 能不能还原整行；只有触发器事件单独评估了（第五节）。这一项留给阶段 G 逐表实现重建器时再核。
- **局限**：调用链很深的几处（`_collect_plan_decision` 里的各个事务块、`AssuranceLocalChecks.prepare` 的上游）只核到能下结论的程度，没有逐个分支走完。另一个子代理在同一工作树删代码，期间函数总数从 5038 降到 5015；写完 JSON 后已复核：275 个写入口指向的文件和函数都还在。

## 三、逐表明细

"写入口数"含触发器。"发事件的入口数"是至少有一条路径同事务发事件的入口个数（条件事件也算）。缺口写法是"入口：情况"。

| 表 | 写入口数 | 发事件的入口数 | 覆盖 | 缺口 |
|---|---|---|---|---|
| acceptance_commit_receipts | 1 | 1 | 完全 | — |
| acceptance_outputs | 1 | 1 | 完全 | — |
| acceptances | 1 | 1 | 完全 | — |
| actions | 1 | 1 | 完全 | — |
| approval_decisions | 1 | 1 | 完全 | — |
| approvals | 1 | 1 | 完全 | — |
| artifacts | 4 | 2 | 部分 | Store._renumber_artifact_lineage：迁移时改写，不发事件；Store.update_artifact_storage：启动时（Orchestrator.__aenter__）和离线恢复（restore_offline）改存放位置，不发事件 |
| assurance_blob_pins | 2 | 0 | 无 | AssuranceStore.acquire_pin：不发事件（只写 commit_receipts 回执）；AssuranceStore.transition_pin：不发事件（只写 commit_receipts 回执） |
| assurance_check_bindings | 1 | 1 | 完全 | — |
| assurance_closeouts | 2 | 2 | 完全 | — |
| assurance_creation_contracts | 2 | 1 | 部分 | DDL：迁移一次性补写（MIGRATION_CLASSIFICATION），不发事件 |
| assurance_criterion_policies | 1 | 1 | 完全 | — |
| assurance_disclosure_batches | 1 | 1 | 完全 | — |
| assurance_mission_bindings | 1 | 1 | 完全 | — |
| assurance_review_bindings | 1 | 1 | 完全 | — |
| assurance_review_invocations | 1 | 1 | 完全 | — |
| assurance_review_record_bindings | 1 | 1 | 完全 | — |
| assurance_use_certificates | 1 | 1 | 完全 | — |
| attempts | 2 | 2 | 完全 | — |
| bound_inputs | 1 | 0 | 无 | HtnStore.insert_bound_input：SDK 与 Host 生产代码均无调用方（只在测试里用），不发事件 |
| budget_accounts | 2 | 2 | 部分 | BudgetLedger._apply：经 BudgetLedger.grow（ProviderBudgetGuard._acquire 模型调用追加额度）不发事件 |
| budget_reservations | 6 | 5 | 部分 | BudgetLedger.grow：不发事件（唯一调用方 ProviderBudgetGuard._acquire 无事件） |
| budget_tail_holds | 3 | 3 | 完全 | — |
| budget_tail_transfers | 1 | 1 | 完全 | — |
| claims | 1 | 1 | 完全 | — |
| commit_receipts | 2 | 1 | 部分 | DDL：迁移一次性补写 AssuranceLegacyClassified 回执，不发事件；Store.insert_receipt：经 assurance_review_pins.ensure_review_blob_pins / _transition（审阅对象钉住与释放）不发事件；Store.insert_receipt：经 assurance_root_commits.install_native_root / _initialize_clock / reauthorize_restored_read（保证通道根与时钟）不发事件；Store.insert_receipt：经 failure_classes.record_review_interruption（审阅调用被打断）在事件事务之外单独写，不发事件；Store.insert_receipt：经 operation_proposal_review.persist_action_proposal_review_inputs 在 operation_runtime.ensure_assured_proposal_reviews 路径上不发事件；Store.insert_receipt：经 assurance_changes.original_source_mutation 在 PolicyCommitsMixin.seed_policy / record_policy_drift、HtnStore.register_method / set_method_registration 路径上不发事件；Store.insert_receipt：经 MethodEvaluationStore.record_oracle 与 evaluation/htn_method_source.py（评测）不发事件 |
| criterion_evaluations | 1 | 1 | 部分 | HtnStore.insert_review_record：经 CompositionAcceptanceAssembly.resolve_one（中间目标组合审阅）在 commit_goal_resolution 事务之外先写，不发事件；HtnStore.insert_review_record：经 operation_outcomes.record_operation_outcome_review（操作结果审阅记录）单独一个事务，不发事件；HtnStore.insert_review_record：经 RootReviewCoordinator.record_review（根审阅记录）不发事件 |
| data_requirements | 1 | 1 | 完全 | — |
| delivery_receipts | 1 | 1 | 完全 | — |
| goal_resolutions | 2 | 1 | 部分 | HtnStore.adopt_goal_resolution：SDK 与 Host 生产代码均无调用方（只在测试里用），不发事件 |
| human_overrides | 1 | 1 | 完全 | — |
| imported_usage | 1 | 0 | 无 | BudgetLedger.import_usage：不发事件（经 CommitService.import_usage、accounting_recovery._import_hold） |
| input_manifest_bindings | 1 | 1 | 部分 | HtnStore.insert_input_manifest：经 CompositionAcceptanceAssembly.resolve_one（中间目标组合审阅）在 commit_goal_resolution 事务之外先写，不发事件；HtnStore.insert_input_manifest：经 RootReviewCoordinator.cut（根审阅切包）不发事件；HtnStore.insert_input_manifest：经 operation_runtime.ensure_assured_proposal_reviews → ActionProposalReviewCoordinator.prepare_review 不发事件；HtnStore.insert_input_manifest：经 AssuranceLocalChecks.prepare 时只有首次登记检查规格才发 AssuranceCheckSpecRegistered，之后不发事件 |
| input_manifests | 1 | 1 | 部分 | HtnStore.insert_input_manifest：经 CompositionAcceptanceAssembly.resolve_one（中间目标组合审阅）在 commit_goal_resolution 事务之外先写，不发事件；HtnStore.insert_input_manifest：经 RootReviewCoordinator.cut（根审阅切包）不发事件；HtnStore.insert_input_manifest：经 operation_runtime.ensure_assured_proposal_reviews → ActionProposalReviewCoordinator.prepare_review 不发事件；HtnStore.insert_input_manifest：经 AssuranceLocalChecks.prepare 时只有首次登记检查规格才发 AssuranceCheckSpecRegistered，之后不发事件 |
| justification_sets | 1 | 0 | 无 | HtnStore.insert_justification_set：SDK 与 Host 生产代码均无调用方（只在测试里用），不发事件 |
| knowledge | 1 | 1 | 完全 | — |
| method_child_occurrences | 1 | 1 | 完全 | — |
| method_instances | 2 | 2 | 完全 | — |
| mission_domains | 1 | 1 | 完全 | — |
| mission_planning_protocols | 1 | 1 | 完全 | — |
| mission_policies | 2 | 1 | 部分 | Store._bind_legacy_missions：迁移时补绑旧任务，不发事件 |
| missions | 2 | 2 | 完全 | — |
| obligation_expansions | 2 | 1 | 部分 | ObligationStore.consume_fuel：SDK 与 Host 生产代码均无调用方（只在测试里用），不发事件 |
| obligation_relations | 1 | 0 | 无 | ObligationStore.add_relation：SDK 与 Host 生产代码均无调用方（只在测试里用），不发事件 |
| obligation_shape_changes | 2 | 1 | 部分 | ObligationStore.note_shape_change：SDK 与 Host 生产代码均无调用方（只在测试里用），不发事件 |
| obligations | 8 | 5 | 部分 | ObligationStore.consume_fuel：SDK 与 Host 生产代码均无调用方（只在测试里用），不发事件；ObligationStore.record_failure：SDK 与 Host 生产代码均无调用方（只在测试里用），不发事件；ObligationStore.record_spend：SDK 与 Host 生产代码均无调用方（只在测试里用），不发事件 |
| observations | 1 | 1 | 部分 | HtnStore.insert_observation：只在启用执行图的任务里发 TaskGraphSourceChanged（只带来源引用），其余任务不发事件（调用方 planning_evidence.persist_questions 所在事务也不发别的事件） |
| operation_acceptance_scopes | 1 | 1 | 完全 | — |
| operation_bindings | 1 | 1 | 完全 | — |
| operation_completion_scopes | 1 | 1 | 完全 | — |
| operation_completion_specs | 1 | 1 | 完全 | — |
| operation_identities | 1 | 1 | 完全 | — |
| operation_intent_bindings | 1 | 1 | 完全 | — |
| operation_outcome_review_bindings | 1 | 1 | 完全 | — |
| operation_payload_objects | 1 | 1 | 完全 | — |
| order_constraints | 1 | 1 | 完全 | — |
| plan_commit_receipts | 1 | 1 | 完全 | — |
| plan_read_sets | 1 | 1 | 完全 | — |
| plan_revisions | 2 | 2 | 完全 | — |
| planning_admission_checks | 1 | 1 | 完全 | — |
| planning_decisions | 1 | 1 | 部分 | PlanningDecisionStore.record_planning_decision：经 Orchestrator._collect_plan_decision.record_decision 的多数调用（进度状态 record_progress、证据轮、过期、延后修复 defer_current_repair 等）同一事务内不发事件 |
| planning_human_requests | 4 | 4 | 完全 | — |
| planning_lane_grants | 1 | 1 | 部分 | PlanningAdmissionStore.put_grant：只在启用执行图的任务里发 TaskGraphSourceChanged（只带来源引用），其余任务不发事件（调用方 PlanningAuthorizationApi.issue / renew / revoke 不发别的事件） |
| planning_operation_action_links | 1 | 1 | 完全 | — |
| planning_repair_continuations | 4 | 1 | 部分 | PlanningRepairStore.claim_due：认领续跑不发事件；PlanningRepairStore.transition：续跑状态迁移（resume_pending_planning_repairs / claim_due_planning_repair / settle_claimed_planning_repair）不发事件；PlanningRepairStore.administrative_transition：管理迁移（set_planning_repair_state）不发事件 |
| planning_request_authority_bindings | 1 | 1 | 部分 | PlanningAdmissionStore._insert_binding：经 bind_request（facade.planning_authorization / PlanningAuthorizationApi.bind_request）不发事件；经 put_grant 时只在启用执行图的任务里发 TaskGraphSourceChanged（只带来源引用），其余任务不发事件 |
| planning_requests | 2 | 2 | 完全 | — |
| requirements_revisions | 1 | 1 | 完全 | — |
| results | 2 | 2 | 完全 | — |
| review_packages | 1 | 1 | 部分 | HtnStore.insert_review_package：经 CompositionAcceptanceAssembly.resolve_one（中间目标组合审阅）在 commit_goal_resolution 事务之外先写，不发事件；HtnStore.insert_review_package：经 RootReviewCoordinator.cut（根审阅切包）不发事件；HtnStore.insert_review_package：经 operation_runtime.ensure_assured_proposal_reviews → ActionProposalReviewCoordinator.prepare_review 不发事件 |
| review_records | 1 | 1 | 部分 | HtnStore.insert_review_record：经 CompositionAcceptanceAssembly.resolve_one（中间目标组合审阅）在 commit_goal_resolution 事务之外先写，不发事件；HtnStore.insert_review_record：经 operation_outcomes.record_operation_outcome_review（操作结果审阅记录）单独一个事务，不发事件；HtnStore.insert_review_record：经 RootReviewCoordinator.record_review（根审阅记录）不发事件 |
| sources | 1 | 1 | 完全 | — |
| support_members | 1 | 0 | 无 | HtnStore.insert_justification_set：SDK 与 Host 生产代码均无调用方（只在测试里用），不发事件 |
| task_semantics | 1 | 1 | 完全 | — |
| taskgraph_attempt_inputs | 1 | 1 | 完全 | — |
| taskgraph_convergence_jobs | 2 | 2 | 完全 | — |
| taskgraph_convergence_targets | 1 | 1 | 完全 | — |
| taskgraph_policy_bindings | 1 | 1 | 完全 | — |
| taskgraph_requirements | 1 | 0 | 无 | require_taskgraph：不发事件（SDK 内无调用方，Host deskpet/orchestration/service.py 调用） |
| taskgraph_revision_records | 1 | 1 | 完全 | — |
| tasks | 2 | 2 | 完全 | — |
| tool_calls | 1 | 0 | 无 | Store.record_tool_call：不发事件（经 CommitService.record_tool_call ← Orchestrator._record_tool_call） |
| validity_epochs | 149（含触发器 147） | 148 | 部分 | HtnStore.bump_epoch：SDK 与 Host 生产代码均无调用方（只在测试里用），不发事件；即便调用也只在启用执行图时发 TaskGraphSourceChanged |
| validity_witnesses | 1 | 1 | 部分 | HtnStore.insert_validity_witness：只在启用执行图的任务里发 TaskGraphSourceChanged（只带来源引用），其余任务不发事件；在 CompositionAcceptanceAssembly.resolve_one、RootReviewCoordinator.cut、HierarchicalDispatch._record_witness 路径上同事务没有别的事件 |
| verifications | 1 | 1 | 完全 | — |
| workspaces | 2 | 0 | 无 | Store.register_workspace：不发事件（Orchestrator._bind_workspace / _register_copy）；Store.set_workspace_state：不发事件（Orchestrator._bind_workspace / cleanup_workspaces） |

## 四、我认为可能分错类的表（只报告，未改文件）

| 表 | 现分类 | 建议 | 理由 |
|---|---|---|---|
| commit_receipts | business | log（第二本日志），或拆开 | 没有 mission_id，跨任务。保证通道、方针、做法、操作、执行图都往里写只追加的回执，本身就是一本"提交回执账"。很多保证通道事实只在这里、不在 events 里。如果仍算 business，就要给第三节里那几类不发事件的回执来源逐一补事件。 |
| assurance_blob_pins | business | runtime | 是内容存储对象的钉住和释放（ACQUIRED/BOUND/RELEASED），作用是防止被垃圾回收，不是任务的业务结论。重建时可以按审阅包重新钉住。 |
| workspaces | business | runtime | 是工作目录与文件系统的绑定、启动时重新绑定、清理状态，属于运行环境；Orchestrator 启动和清理时都会改它。 |
| planning_repair_continuations | business | 拆开，或在字段级标注 | 续跑正文（decision_id、targets、authority_binding 等）是业务事实。但 owner_id、lease_until_ms、next_check_at_ms、resume_count、row_version 是租约和队列运行态，`claim_due` / `transition` 正是在改这些字段，所以它们不发事件也合理。建议这些字段不参与按值比较，或把租约部分拆出去。 |
| taskgraph_requirements | business | 可保留，但需说明 | 是部署层"本任务必须走执行图"的标记，只由 Host 写，SDK 内没有入口。属于业务配置，需要事件或在建任务事件里带上。 |

另外有 7 张 business 表没有 mission_id：`approval_decisions`、`budget_tail_transfers`、`commit_receipts`、`input_manifests`、`planning_admission_checks`、`planning_decisions`、`verifications`（`coverage_report` 已经单独列为 unscoped）。这不是分错类，只是按任务重放时要经关联表找归属。

## 五、保证通道屏障触发器写事件的情况

**触发器做什么**：`assurance_barrier_v26.sql` 里有 159 个 AFTER 触发器写表。
- 147 个是任务级的：监视 49 张源表（含 events 自身里 6 种保证通道导入事件、`validity_epochs` 自身，以及已删除的 `criterion_assessments`）。每次增删改，做两件事：
  1. 把 `validity_epochs(scope_id='assurance:mission')` 的 epoch 加 1，`bumped_by='assurance-source:<表名>'`；
  2. 写一条 `AssuranceEvidenceChanged`，id 和幂等键都是 `assurance-mission-epoch:<任务>:<epoch>`，actor 是 `assurance-source-v1`，payload 是 `{scope:'MISSION', epoch, source_table}`。
- 12 个是全局级的：监视 `method_contracts`、`policy_versions`、`policy_proposals`、`policy_activations`。每次变化把 `assurance_environment_state` 的 epoch 加 1，并且**给每一个绑定过保证通道的任务各写一条** `AssuranceEvidenceChanged{scope:'GLOBAL', epoch, source_receipt_id}`。

**能重建什么**：
- **`validity_epochs` 的保证通道行，够。** 初始行（epoch=0）由 `bind_profile_locked` 写，与 MissionCreated、AssuranceProfileActivated 同事务，值是固定的。之后每次 +1 都恰好对应一条 AssuranceEvidenceChanged：mission_id 取自事件，epoch 取自 payload，bumped_by 由 source_table 拼出，updated_at 按时间字段口径只核先后。按事件顺序折叠就能逐值还原。
- **`assurance_environment_state`**（runtime 类）也能由 GLOBAL 事件的 epoch 和 source_receipt_id 还原，不过它不在业务重放范围内。

**不够的地方**：
1. **源表本身一行也重建不了。** 事件只说"哪张表变了"，不说哪一行、变成了什么，所以不能把它当作 sources、artifacts、obligations 等表的覆盖事件（本次也确实没有这样算）。
2. **只在保证通道任务里有。** 没绑定 `assurance_mission_bindings` 的任务，既不 +1 也不发事件。
3. **全局变化会扇出到历史任务。** 一次全局方针或做法变化，会往所有绑定过保证通道的任务（包括已结束的）的事件流里各追加一条。按任务重放时，这些"任务外原因"的事件会让已结束任务的事件序列在结束之后继续增长。阶段 G 需要决定：v3 是把它们当作任务的业务事件，还是当作外部输入。
4. **时间戳来源不一致。** 触发器用 `strftime('%s','now')`（整秒），Python 侧用 `store.now`。按现有口径只核先后，但同一秒里的先后要靠 seq，不能靠 created_at。
5. **Python 侧的另一条写法没有对应事件。** `HtnStore.bump_epoch` 也写 `validity_epochs`，只在执行图任务里发 `TaskGraphSourceChanged`，而且目前没有生产调用方。如果以后启用，要么发同类事件，要么并到触发器口径里。

## 六、建议给主会话

1. 先放开 `business_replay.py` 允许的键，加上 `gaps`，然后用 `apply.py --with-gaps` 写回缺口。
2. 第四节的分类问题请裁决，尤其是 `commit_receipts`：它是改成 log，还是逐来源补事件，决定了保证通道那一大片的工作量。
3. 四张"无调用方"的表（`bound_inputs`、`justification_sets` / `support_members`、`obligation_relations`），以及 `ObligationStore` 的 `consume_fuel`、`record_failure`、`record_spend`、`note_shape_change`、`HtnStore.adopt_goal_resolution`、`HtnStore.bump_epoch`，按"旧路径直接删"的规矩，可以考虑和阶段 A 的删除一起清掉，不必补事件。
4. 审阅三件套是阶段 G 的主要补点：组合审阅、根审阅、操作结果审阅这三条路径要么并进各自的提交事务，要么各补一个"已切包 / 已记录"事件。
