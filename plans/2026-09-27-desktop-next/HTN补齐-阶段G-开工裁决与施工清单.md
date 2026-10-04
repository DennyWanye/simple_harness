# HTN 补齐 · 阶段 G（全业务事件重放收尾）：开工裁决与施工清单

- 裁决人：独立裁决子代理（只读代码与文档；除本文件外没有改任何文件、没有跑测试，只开过一个临时空库看表结构与触发器；逐表内容核对另派三名只读核对员分组完成，结论汇总在 1.3 与附表）。日期 2026-10-04。代码以主检出 `main` `165dd8b4`（SDK opt.148，阶段 F1 已发版）为准；**下文行号都是"约"，以函数名为准**。
- 依据：`HTN补齐计划-2026-10-02.md` 第 3.19 版（阶段 G 一节、第二节各偏离、第六节流程、阶段 A 第 5 条 G0）；`HTN补齐-实施记录.md`（A-5 G0、各阶段"新事实进 v3"的记录、F1 全部，尤其 F1-3 屏障开销）；`HTN补齐-G0覆盖清单审计.md`；`HTN补齐-阶段F-开工裁决与施工清单.md`；验收资产 `T/acceptance_assets/crash_points.json`、`mutations.json`、`T/product_world/random_sequences.py`；原计划 v1.4 §16（`plans/taskSys2/升级planV1/v1.4/simpleharness-full-target-1.4/complete-plan.zh-CN.md`）与原 TaskGraph 代码级计划 §10、§16（`plans/TaskGraph/v1/simpleharness-taskgraph-code-execution-plan.zh-CN.md`）；仓库根 `CLAUDE.md`。
- 口径：用户 2026-10-03 定的流程——先写完全部代码再一起测；中途只做功能性测试；每个功能一条改坏；不许自行跑全量回归或整目录；计划与实际冲突走偏差单。判断交给模型、Harness 只管秩序；同一件事一条路径；开发期不做兼容层、旧路径直接删。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`；`FE/` = `tauri-app/src/`。

---

## 〇、一句话结论

**G 的难点不是"补约 30 处写入发事件"，而是先定"什么叫能由事件重建"，然后照这个口径把每张业务表收口。** 现在清单里"完全覆盖 55 张"只说明"同一事务里有事件"，G0 审计明说没核事件内容。三名只读核对员按内容逐表核下来（1.3）：**82 张业务表里，只凭事件就能还原整行的只有 5 张**；4 张生产上没有写方或始终为空；4 张完全不发事件；22 张只带编号或"该重读了"信号；47 张部分够。

- **重建口径（裁决 G-1）**：照原计划 v1.4 §16.1"足够 payload 或受保留保护的不可变引用"与原 TaskGraph 计划 §10.1"完整内容在同事务不可变记录中、事件引用该记录"——**事件要么带内容，要么点名一条"不可变源记录"（编号 + 内容哈希）**；不可变源记录 = 只增、库层有不许改不许删守卫的表（迁移 41 补齐守卫）。点名由存储层在每个事务提交前**自动**做（一条"源记录已写"事件），不逐处改调用方。会被改的表（任务、尝试、结果、预算、计划修订、规划请求与决定、义务等约 25 张）的**建行事件必须带整行内容**、每次改动都有事件，由事件折叠出来。这样比"事件里复制整行"改动小得多，也不会让计划网络文档、审阅包这类大 JSON 在库里存两份。
- **批次**：G-0 金丝雀 → G-1 引擎、自动点名、清单第 2 版、迁移 41 → G-2 规划与计划 → G-3 审阅与有效性 → G-4 执行、记账与两库边界（含 K04/K11/K12）→ G-5 保证通道与回执 → G-6 全局表 → G-7 v3 取代 v2、部署身份、诊断与界面 → G-8 验收与收尾。每批可独立验收：该批的表在 v3 报告里由"未覆盖"变"一致"。
- **工期**：估 15～18 个工作日（约 3～3.5 周），超过计划写的 2～3 周上沿，见偏差单 G-11。
- **需要问用户的只有一条**（G-10）：G 收尾的验收语料是"产品同形世界整个目录（26 个文件、约 91 条）+ 随机序列"，规模等于跑一个整目录，与"不许自行跑整目录"的硬规则相碰，开跑前请用户点头。其余都是技术裁决；4 条报用户知悉（第五节）。

---

## 一、现状盘点（按现行代码核，不照抄旧文档）

### 1.1 覆盖清单现在的数字

清单 `SDK/observability/business_replay_inventory.json` 与现行库结构逐表逐字段一致（临时空库上 `check_inventory` 通过）。**105 张表**（计划写的 109 张是 A 阶段的数；之后迁移 35、36、39、40 删了 `taskgraph_requirements`、`planning_repair_continuations`、`justification_sets`、`support_members`、`method_evaluations` 与规则摘要表，加了 `method_library`、`method_library_attributions`）：

| 类别 | 张数 | 其中 |
|---|---|---|
| 业务 business | 82 | 清单自称完全覆盖 55、部分覆盖 21、不发事件 6 |
| 派生 derived | 5 | `assurance_dependency_index`、`plan_memberships`、`taskgraph_demand_refs`、`taskgraph_member_pins`、`taskgraph_method_pins` |
| 运行 runtime | 9 | 保证通道全局纪元与时钟、事件游标、待办，派发意图，迁移记录，模型调用令牌，调度游标，执行图后续通知，有效性待重算 |
| 全局 global | 8 | `method_contracts`、`method_library`、`method_library_attributions`、`policy_versions`、`policy_activations`、`policy_proposals`、`policy_evaluations`、`policy_decisions` |
| 日志 log | 1 | `events` |

"排除"（不按任务重放）= 派生 + 运行 + 全局 + 日志 = 23 张。清单里没有"排除理由"一栏，只有一句 `note`；G-1 改清单第 2 版时补成必填。

不发事件的 6 张：`assurance_blob_pins`、`bound_inputs`、`imported_usage`、`obligation_relations`、`tool_calls`、`workspaces`。

### 1.2 清单已经过时的地方（G-1 一并改）

| # | 清单写的 | 现行代码 |
|---|---|---|
| a | `validity_epochs` 缺口："`HtnStore.bump_epoch` 无生产调用方" | 有两个生产写方：观察使命题真假翻转（`SDK/storage/htn_store.py` `insert_observation` 约 :1408，阶段 D）、改要求（`SDK/orchestrator/requirements_amendment.py` 约 :126，阶段 E）；每次都同事务发 `TaskGraphSourceChanged`（只带作用域、纪元号与哈希） |
| b | 多处"只在启用执行图的任务里发 `TaskGraphSourceChanged`" | 阶段 A′ 起每个任务建任务时就绑定执行图，`record_source_change`（`SDK/storage/taskgraph_source_events.py`）对没绑定的任务直接报错——这个条件已不存在，缺口描述要删 |
| c | `obligations` 的写入口 | 漏登记阶段 E 的 `ObligationStore.revise_requirement_refs`（改要求事务里改根义务的要求编号） |
| d | 阶段 E 的 `RequirementsAmended` | 实施记录 E-1 偏差 7 已记"内容核对归 G"，清单没加 |
| e | `policy_proposals`、`policy_evaluations`、`policy_decisions` 三张全局表 | 生产代码里既没有写方也没有读方（只剩建表语句、保证通道源列清单与 3 个屏障触发器）——"删策略评测与升级"（表一 8）的残留 |
| f | `bound_inputs`、`obligation_relations` | 写方 `HtnStore.insert_bound_input`、`ObligationStore.add_relation` 生产无调用方；`ObligationStore.consume_fuel`、`note_shape_change`（写库的那两个）、`HtnStore.adopt_goal_resolution` 同样只在测试里调（`ObligationStore.load_ledger` 里调的是内存账本同名方法，不写库） |
| g | `obligation_expansions`、`obligation_shape_changes` 有事件 `PlanRevisionCommitted` | `ObligationStore.persist` 只回写内存账本里的展开与形状变化，而内存账本的这两样只来自读库——**生产库里这两张表始终为空** |
| h | `review_records`、`criterion_evaluations` 的三条缺口（组合审阅、根终审 `record_review`、操作结果审阅） | 这三条路径现在只读 `official_review_record`，**不再写审阅记录**；唯一的生产写方是保证通道导入 `assurance_review_import.PreparedOfficialReview.import_locked`。`review_packages` 的组合审阅缺口同样过时（组合审阅已不写包）；根终审切包 `RootReviewCoordinator.cut` 现在发 `HierarchicalRootReviewCut`，但**在另一个事务里紧随其后**发 |
| i | 各表 `events` 列表 | 漏了 `PlanningDecisionEvaluated`（`planning_decisions`）、`RequirementsAmended`（`requirements_revisions`、`task_semantics`、`obligations`）、`ResultRejected`（`results`、`tasks`）、`TaskGraphRevisionRecorded`（`order_constraints` 等） |
| j | 缺口没登记的写入 | 收敛作业 `WAITING→WAITING` 只把 `row_version` 加 1、不发事件；`propose_action` 的"同一候选再投递"分支改写动作的规划来源、不发事件；同一分支写 `planning_operation_action_links` 也不发事件；`imported_usage` 的调用方漏了 `event_handler.py` 三处 |
| k | 写方还在、生产走不到 | `BudgetLedger.settle_known`（`_settle_subject` 里强制 `known_only=False`）；`CommitService.record_delivery_receipt`（`DeliveryReceiptRecorded` 只从这里来，无生产调用方） |
| l | 事件幂等键会吞掉第二次改动 | `VerificationLayerRecorded` 的键是"结果:层"，`PlanningDecisionEvaluated` 的键是"决定编号"——同一行第二次更新时事件被去重（`Store.append_event` 同键直接返回旧事件），重放会丢后一次变更 |

### 1.3 逐表：事件内容够不够重建

由三名只读核对员（Opus）按表分三组逐个写入口核对：顺调用链找到最外层事务里发的事件，看事件内容能不能还原这一行（时间字段除外）。**事件里的编号若指向的正是待重建的这张表本身，不算"不可变引用"**（核对时的口径；G-1 之后，只增表补了守卫、被事件点名，就算不可变源记录）。逐表明细在文末附表，这里是汇总：

| 结论（按最差的那个写入口） | 张数 | 表 |
|---|---|---|
| 全（只凭事件就能还原） | 5 | `acceptance_commit_receipts`（行就是事件内容的投影，现成样板）、`assurance_creation_contracts`、`human_overrides`（事件内容就是整条记录）、`operation_payload_objects`（以内容寻址仓库字节保留为前提）、`sources`（事件带整行） |
| 生产上没有写方或始终为空 | 4 | `bound_inputs`、`obligation_relations`、`obligation_expansions`、`obligation_shape_changes` |
| 不发事件 | 4 | `assurance_blob_pins`、`imported_usage`、`tool_calls`、`workspaces` |
| 只带编号或信号 | 22 | `artifacts`（记录结果时）、`budget_tail_holds`、`claims`（建行时只带条数）、`criterion_evaluations`、`data_requirements`、`method_child_occurrences`、`mission_planning_protocols`、`observations`、`operation_completion_scopes`、`order_constraints`、`plan_read_sets`、`planning_decisions`（解码、准入、编译三种进度行**完全不发事件**，终态只带正文哈希）、`planning_human_requests`（建题时）、`planning_lane_grants`、`planning_operation_action_links`、`planning_request_authority_bindings`（`bind_request` 不发事件）、`planning_requests`、`requirements_revisions`（第 1 版与改要求都只带哈希与编号，**原文只在表里**）、`review_records`、`task_semantics`、`taskgraph_convergence_targets`、`validity_witnesses` |
| 部分够 | 47 | 其余各表；典型缺口：`missions` 建行事件里的预算是请求值不是实际生效值、缺成功标准等；`tasks`、`attempts`、`results`、`actions`、`approvals` 建行只带编号；`plan_revisions` 缺读集原文；`taskgraph_revision_records` 缺网络文档；保证通道各绑定表"事件带引用与哈希、正文在本表或回执里"；预算族缺账户与工具调用数，`grow` 与尾部预留建立、释放没有可识别的事件；`commit_receipts` 约一半来源"事件内容就是回执"、另一半事件比回执少、还有 7 类根本没有事件 |

**三条专项**：
- **计划网络文档**：`PlanRevisionCommitted` 与 `TaskGraphRevisionRecorded` 只带编号、计数和哈希，网络文档本体只在 `taskgraph_revision_records.network_json`（只增、已有守卫）——按 G-1 正好是"不可变源记录"，被 `TaskGraphRevisionRecorded` 点名即可；`order_constraints`、`method_instances` 等由网络文档推出的行，G 里核"与网络文档一致"，不在事件里再写一遍。
- **`RequirementsAmended`**：只带前后版本号、新要求的内容哈希与增改删的编号，新要求原文只在 `requirements_revisions`（只增）——补守卫后它就是不可变源记录，事件点名即可。
- **`initialize_root`**：同事务只有 `ObligationDemandAdmitted`（义务编号、要求编号、主体），根义务的签名、燃料上限、根任务语义整行、要求第 1 版原文都不在事件里。义务表会被改 → 建行事件要带整行；任务语义与要求第 1 版只增 → 守卫 + 点名。

### 1.4 `storage/htn_store.py` 的写入点

`HtnStore` 本身**一处也不发业务事件**，事件都由调用方在同一事务里发（G0 审计也是按"最外层事务"判的）；只有三处顺带发 `TaskGraphSourceChanged`（只带来源编号与内容哈希，是"该重读了"的信号，不带行内容）：`insert_validity_witness`、`insert_observation`、`bump_epoch`。写入函数共 31 个：

| 组 | 函数 | 写的表 |
|---|---|---|
| 计划 | `insert_plan_revision`、`activate_plan_revision`、`insert_plan_membership`、`insert_order_constraint`、`insert_data_requirement`、`insert_method_instance`、`set_method_instance_state`、`_insert_child_occurrence`、`put_task_semantics`、`record_read_set`、`record_commit_receipt` | `plan_revisions`、`plan_memberships`（派生）、`order_constraints`、`data_requirements`、`method_instances`、`method_child_occurrences`、`task_semantics`、`plan_read_sets`、`plan_commit_receipts` |
| 要求 | `insert_requirements_revision` | `requirements_revisions` |
| 审阅 | `insert_review_package`、`insert_review_record`、`insert_input_manifest` | `review_packages`、`review_records` + `criterion_evaluations`、`input_manifests` + `input_manifest_bindings` |
| 验收与结论 | `insert_acceptance`、`insert_goal_resolution`、`adopt_goal_resolution`（无生产调用方）、`record_acceptance_receipt`、`record_delivery_receipt`、`insert_acceptance_output` | `acceptances`、`goal_resolutions`、`acceptance_commit_receipts`、`delivery_receipts`、`acceptance_outputs` |
| 有效性 | `insert_validity_witness`、`insert_observation`、`bump_epoch`、`mark_dirty`、`clear_revoked_generation` | `validity_witnesses`、`observations`、`validity_epochs`、`validity_dirty`（运行） |
| 操作 | `bind_operation` | `operation_identities` + `operation_bindings` |
| 做法（全局） | `register_method`、`set_method_registration` | `method_contracts` |
| 无生产调用方 | `insert_bound_input` | `bound_inputs` |

### 1.5 Host 侧建任务等写入

**Host 现在不直接写编排库**（`backend/deskpet/` 里没有对编排库的 INSERT/UPDATE，也没有直接调 SDK 存储层写方法；只有 `diagnostics.py`、`notices.py` 两处只读查询）。计划第 231 行写的"Host 建任务时写要求修订、义务、任务语义也不发事件"，在阶段 A′ 把根初始化搬进 SDK 以后，落在 **`SDK/deployment/root.py::initialize_root`**（由 `SDK/deployment/assembly.py` 约 :122 在建任务事务里调）：一个事务写根义务（`ObligationStore.register`）、准入需求（`commit.admit_obligation_demand`，发 `ObligationDemandAdmitted`）、根任务语义（`put_task_semantics`）、要求第 1 版（`insert_requirements_revision`）。同事务只有 `ObligationDemandAdmitted` 一个事件（另有建任务的 `MissionCreated`），内容够不够见 1.3。执行图要求表 `taskgraph_requirements`（G0 审计列的 Host 独写表）已在迁移 35 删除。

### 1.6 保证通道屏障触发器直接写的事件

临时空库上实数：**290 个触发器，其中 150 个往 `events` 里写**（G0 审计写 147 + 12 = 159 是按迁移 26 原文数的，之后随表删掉了一些）：

- **任务级 138 个**（46 张源表 × 增删改）：把 `validity_epochs` 的 `assurance:mission` 行纪元加 1、`bumped_by='assurance-source:<表名>'`，并写一条 `AssuranceEvidenceChanged{scope:'MISSION', epoch, source_table}`，事件号 `assurance-mission-epoch:<任务>:<纪元>`。**够重建 `validity_epochs` 的保证通道行**（初始行由绑定事务写、值固定；之后每次加 1 恰好对应一条事件），**不够重建任何源表**（只说哪张表变了，不说哪一行、变成什么）——所以它不能、也不应算源表的覆盖事件。时间戳是触发器取的整秒，只按先后核。
- **全局级 12 个**（`method_contracts`、`policy_versions`、`policy_proposals`、`policy_activations` × 增删改）：全局纪元加 1，并**给每一个曾绑定保证通道的任务（含已结束的）各写一条** `AssuranceEvidenceChanged{scope:'GLOBAL', epoch, source_receipt_id}`。内容够还原全局纪元（运行表），**不改任何业务表**——v3 把它当"外部来的信号"，不进任何表的重建。
- 已结束任务也收到全局事件的后果，F1-3 只量了写入这一下（1/10/50 个曾绑定任务 → 多写 1/10/50 条、0.50/1.05/2.86 毫秒）。没量的下游：保证通道的收尾消费者对 `AssuranceEvidenceChanged` 不看任务是否结束就排一份收尾工作（`SDK/orchestrator/assurance_consumers.py` 收尾的 `classify` 约 :376），有效性消费者把该任务仍"可用"的证书各排一份复核（同文件 `AssuranceValidityConsumer.classify` 约 :176）。G-0 第 2 条核实这两份工作对已结束任务是否真的排进待办。
- `SDK/storage/assurance_changes.py::original_source_mutation`（约 :63-130）在全局纪元变了却找不到任何唤醒事件时报 `SOURCE_MUTATION_EVENT_MISSING`——改触发器时它要跟着改（见 G-3）。

### 1.7 v2 的使用方（现行）

| 使用方 | 用了什么 |
|---|---|
| SDK 命令行 `replay`（`SDK/__main__.py` `cmd_replay` 约 :128） | `replay_mission`、`library_copy` |
| SDK 命令行 `policy list/show/status`（同文件约 :270-300） | 只借 `library_copy`（拷库再只读打开） |
| Host 诊断导出（`Host/diagnostics.py` 约 :22-29、:378-401） | `events_from_store`、`Projection`、`compare`、`formal_from_snapshot`、`failure_timeline`、`REPLAY_VERSION` |
| Host `Host/service.py::_detect_diagnostics`（约 :1415-1432） | 逐个检查上面这些函数存在才算"诊断可用" |
| 前端 `FE/views/MissionDiagnostics.tsx`（约 :72-105）与它的测试 | `report.replay` 的 `events`、`unknown_event_types`、`comparison`、`failure_timeline` |
| Host 测试 `backend/tests/orchestration/test_mission_diagnostics.py` | `report["replay"]["failure_timeline"]`，猴补 `diagnostics.failure_timeline` |
| SDK 测试 | `T/step08/test_replay.py`（v2 本身）、`T/p33/test_p33_sources.py`、`T/p33/test_p33_g1_create_sources.py`（用 v2 比对）、`T/p33_replay_audit.py`（v2 审计插件）与 `T/p33/test_g_replay_audit_plugin.py` |

`SDK/observability/business_replay.py` 的 `V2_CONSUMERS` 少列了 `service.py` 的检查、前端与 SDK 测试五处。不相干、不动：`simple_harness/workflow/replay.py` 与 `backend/deskpet/workflows/replay.py`（工作流回放，另一回事）。

### 1.8 部署身份检查里写死的两处

- `SDK/orchestrator/taskgraph_deployment.py` `InstalledHtnWiringAcceptance._read`（约 :40-52）：上游证据的键集合写死，含 `cold_replay_receipt_sha256` 与 `semantic_replay`，并要求 `upstream["semantic_replay"] == "PARTIAL"`。
- `sdk/simple-harness-sdk/scripts/build/taskgraph_manifest.py` `upstream_from`（约 :60-93）：读上游证据文件里的冷重放回执，核"同一任务、`unchanged: true`、回执哈希对得上"，并在约 :92 写死 `"semantic_replay": "PARTIAL"`（注释"旧回放投影仍报未知事件"）。
- 现在绑定的上游是 09-27 `.local-test-evidence/2026-09-27/batch2a-upstream/`（任务 `mission-a22c9fc39b8abed8`）：**冷重放回执证明的是"冷启动前后任务回执逐字节不变"，不是 v2 回放**；`PARTIAL` 才是 v2 的结论。改坏执行器 `scripts/acceptance/run_mutations.py` 约 :35 把这份上游路径写成默认值。

### 1.9 两库边界现状

- **用量**：`imported_usage` 由 `BudgetLedger.import_usage`（`SDK/governance/budgets.py` 约 :298）写，键 `usage_ref = "provider-invocation:<执行库调用编号>"`（`SDK/orchestrator/taskgraph_runtime_imports.py` 约 :73），本身就是一张"消费回执"表；**不发任何事件**；"用量未知"的行之后可被已知值覆盖（唯一的更新）。调用方：`event_handler.py` 三处、`accounting_recovery._import_hold`。
- **工具调用**：`tool_calls` 由 `Store.record_tool_call` 写，键 `call_key = <run_id>:<call_id>`（执行侧稳定编号），**不发事件**。
- **崩溃切点**（`T/acceptance_assets/crash_points.json`，归 G 的三行）：K04 执行侧已存结果、编排没消费（进程强退 `after_turn_committed`，已有用例 `T/full_target/taskgraph_exec/test_process_recovery.py::test_process_exit_at_attempt_boundary_preserves_original_accounting`，只核记账与尝试数，不核事件重建）；K11 用量晚到、任务已终态（已登记的用例 `test_late_accounting_quiet.py::test_ended_mission_holds_are_rechecked_every_five_minutes` 是猴补出来的节奏用例，**不是真场景**）；K12 迟到用量导入写库出错（**没有用例**；`_import_hold` 只接住预算类错误，库读写错误靠阶段 C 第 0′ 条的 `_mission_round` 边界接）。

---

## 二、开工前裁决

### G-1　重建口径：什么叫"能由事件重建"

- **计划原文**：阶段 G 第 4 条"覆盖清单范围内'不一致 = 0、未覆盖 = 0'"；原计划 v1.4 §16.1"每一次正式变更记录足够 payload 或受保留保护的不可变引用，纯 reducer 重建"；原 TaskGraph 计划 §10.1"`network_json` 完整内容在同事务 immutable record 中，事件引用该源记录……不能宣称单靠旧 Event payload 可重建全部"。
- **事实**：清单的"覆盖"只表示"同事务有事件"。按内容核（1.3），大批插入只发编号或"该重读"信号。82 张业务表里 54 张只增不改（其中 17 张库层已有不许改删的守卫触发器，35 张没有），28 张会被改。
- **可选做法**：
  - A. 事件里复制整行（最严）：每处写入都把整行放进事件；计划网络文档、审阅包、输入清单等大 JSON 在库里存两份；改动面最大。
  - B. **事件带内容，或带不可变源记录的编号 + 内容哈希**：只增表补库层守卫后算"不可变源记录"，v3 核"每一行都被同事务的一条事件按编号与哈希点名、哈希对得上、没有无主的行"；会改的表必须完全由事件折叠出来，事件内容不够就补字段或补事件。
  - C. 只核"每行都有同事务事件"（现状口径）：做不到"清空投影能重建"，不满足原计划。
- **推荐 B**。理由：与原计划两处原文一致；源记录只此一份，不出现"事件里一份、表里一份"的两份账（同一件事一条路径）；守卫是纯秩序。代价：迁移 41 给 35 张只增表补守卫；测试里如有直接 UPDATE 这些表造坏数据的夹具会被守卫挡住（G-0 第 4 条先清点）。
- **点名怎么做（推荐一处通用机制，不逐处改调用方）**：存储层在每个连接上装临时触发器，记下本事务插入了哪些不可变源记录表的哪些行；最外层事务提交前，按任务各追加一条 `SourceRecordsWritten{rows:[{table, key, content_hash}]}`（内容哈希按清单字段的规范 JSON 算），清空记录。这样 35 张只增表一次收口，以后新加的只增表登记进清单就自动被点名。前提是所有写入都走 `Store.transaction()` / `atomic()` 的最外层（G0 审计说两者可重入、并入最外层，G-0 第 1 条再核）；没有 `mission_id` 的只增表（`input_manifests`、`approval_decisions`、`planning_admission_checks`、`budget_tail_transfers`）按清单写明的关联键找归属。做不到时退回逐处在已有事件里加点名。
- **会被改的表**：建行事件必须带整行内容（照 `sources`、`HumanOverride`、`acceptance_commit_receipts` 的现成写法——"行 = 事件内容的投影"），每次改动都有事件且内容足以算出改后的列；大正文（结果信封、计划网络）可以放进内容寻址仓库，事件带对象哈希。**幂等键必须区分同一行的每一次改动**（1.2 第 l 条两处改键）。（偏差裁决 1 改：由存储层统一记'整行变化'，见 `HTN补齐-阶段G-偏差裁决-1.md`；领域事件不再要求带整行，内容不作为重建依据。）
- **口径写死在清单第 2 版**：每张业务表必须标 `rebuild: "fold"`（由事件折叠）或 `rebuild: "immutable_source"`（不可变源记录，必须有守卫、必须被点名），并写明重建它的函数名；`gaps` 必须为空，唯一允许的例外是"迁移时一次性补写、只作用于迁移前的旧库"（`Store._bind_legacy_missions`、`_renumber_artifact_lineage`、`assurance_upgrade` 的补写——新建任务不走，又被 G-6 的口径摘要排除在范围外）；时间字段沿用"只核先后"。

### G-2　几张表的归类、无写方的表

- **计划原文**：G0 审计第四节留给 G 定：`commit_receipts` 是否第二本日志；`assurance_blob_pins`、`workspaces` 是否运行表；修复续作表租约字段（表已删，不再议）；Host 独写的执行图要求表（已删）。
- **事实与裁决**：

| 表 | 现状 | 推荐 | 理由 |
|---|---|---|---|
| `commit_receipts` | 无 `mission_id`；只有 `Store.insert_receipt` 一个写方，代码里没有任何 UPDATE/DELETE，但库层没有守卫；保证通道、做法、操作、执行图的很多事实只记在这里 | **改为 log（命令回执账）**：迁移 41 加不许改删守卫；v3 把它当输入，不重建它；**每条回执都要被同事务的事件点名**（给它排序、定归属）——直接用 G-1 的自动点名机制（回执表一并装临时触发器；归属取本事务其它事件的任务，事务里没有任务事件时记在部署时间线 `deployment` 上）。现在没有事件的 7 类回执（审阅对象钉住三种、保证通道根与时钟、审阅调用被打断（现在在事件事务之外单写，G-5 改成同事务）、操作提议审阅预备、方针与做法登记的"源变更"回执）由此一并收口 | 原计划 v1.4 §16.2 本来就把"命令回执"与"事件"并列为同一提交里的两样，不是投影；守卫是秩序 |
| `assurance_blob_pins` | 内容寻址对象的钉住与释放（防被清理），读方只有保证通道存储层自己 | **改为 runtime** | 不是任务的业务结论；重建时不恢复，钉住动作仍记在回执账里 |
| `workspaces` | 工作目录登记、启动时重新绑定、过期清理（`event_handler.py` 约 :5094、:5188、`cleanup_workspaces`） | **改为 runtime** | 文件系统与运行环境状态；种子哈希由尝试的冻结输入决定，已在 `taskgraph_attempt_inputs` |
| `bound_inputs`、`obligation_relations` | 无生产写方 | **删表与写方**（迁移 41），连同 `HtnStore.insert_bound_input`、`ObligationStore.add_relation` 与只调它们的测试 | 开发期旧路径直接删；不为死表补事件 |
| `ObligationStore.consume_fuel`、`note_shape_change`（写库版）、`HtnStore.adopt_goal_resolution`；`obligation_expansions`、`obligation_shape_changes` | 函数无生产调用方；两张表生产库里始终为空（1.2 第 g 条） | **删函数、删两张表**，`ObligationStore.persist` / `load_ledger` 里读写这两张表的部分随之删 | 同上（`refine()` 按义务扣燃料已在 A 删，R08 偏离） |
| `BudgetLedger.settle_known`、`CommitService.record_delivery_receipt`（及只从它来的 `DeliveryReceiptRecorded`） | 生产走不到（1.2 第 k 条） | **删** | 同上 |
| `policy_proposals`、`policy_evaluations`、`policy_decisions` | 无写方、无读方 | **删表**（迁移 41；`policy_proposals` 的 3 个全局屏障触发器随表去掉；`SDK/storage/assurance_source_inventory.py` 的 `GLOBAL_TABLES` 与列清单同步删） | "删策略评测与升级"（表一 8）的残留 |

删表的先例：迁移 33 删 `criterion_assessments` 时触发器随表去掉，保证通道源列清单同步删。

### G-3　屏障开销：全局触发器只给未结束任务写

- **计划原文**：实施记录 C3"阶段 F 量一次，阶段 G 决定是否改成只给未结束任务写"；F1-3 数字（1/10/50 → 1/10/50 条、+1、0.50/1.05/2.86 毫秒）。
- **事实**：
  1. 单次写入时间很小，但**事件条数随"曾经绑定过的任务"线性增长，而每个任务的规划都会提出做法**——整个部署的寿命里，这类事件总数大致按"任务数的平方"增长（500 个历史任务、每任务提 3～5 个做法，就是每个新任务多写一两千条）。
  2. F1-3 没量下游：每条事件还会让保证通道的收尾与有效性两个消费者各排工作（1.6）。
  3. 已结束任务的事件流在结束之后还在变长，v3 若按任务重放，要专门解释这些"结束后的事件"。
  4. 任务是在收尾定稿的同一事务里变成"已完成"的（`SDK/orchestrator/assurance_final_writer.py`），所以"任务状态已是终态"就是"保证通道已不再需要它"的准确界线。
- **可选做法**：A. 保持现状，v3 把这些事件当外部信号忽略；B. **迁移 41 重建 9 个全局触发器（删 `policy_proposals` 后剩 3 张表 × 3），扇出只给 `missions.status` 不在终态的绑定任务写**；全局纪元照旧每次加 1（单行，便宜，未来的任务照常读到最新）；`original_source_mutation` 改成"没有未结束的绑定任务时允许唤醒列表为空、照常记回执"，"有未结束任务却没唤醒事件"仍报错。
- **推荐 B**。理由：这是秩序问题（谁需要被唤醒），不是语义判断；消掉平方增长与下游空转；结束后的事件流不再变长，v3 的不变式"终态之后任务事件流只剩迟到用量与通知类"能写死。G-1 附带：v3 一律把 `scope:'GLOBAL'` 的 `AssuranceEvidenceChanged` 当外部信号，不进任何业务表。
- **前提**（G-0 第 2 条核）：已结束任务的收尾与有效性工作不靠全局事件推进（收尾在终态时已定稿；证书在终态后只是历史）。

### G-4　改屏障、触发器、守卫都要新迁移，合成一次

- **计划原文**：已发布迁移文本 1～40 不许改；阶段 A 第 6 条"编码清单五个文件的改动合并成少数几次"。
- **裁决**：G 只加**一个**迁移 41（`orchestrator-g-replay-v3`），文本写成字面量、发布后冻结（迁移 37 是先例：`DROP TRIGGER` 再 `CREATE TRIGGER` 新版本），内容：①删 G-2 的死表（`bound_inputs`、`obligation_relations`、`obligation_expansions`、`obligation_shape_changes`、三张策略表）；②重建 9 个全局触发器（G-3）；③给 35 张只增业务表与 `commit_receipts` 补不许改、不许删守卫（G-1、G-2）。G 期间若再发现要改库结构，**并进同一个迁移 41，在 G 发版前不发布**。
- **不碰**：编码清单五个文件（`contracts/htn.py`、`state_machines.py`、`models.py`、`evidence_state.py`、`graph/task_network.py`）——事件类型是自由字符串（`contracts/models.py` `Event.type: str`），新事件与新字段都写在各提交服务里，不需要改这五个文件；万一要改，合成一次并在实施记录写明。工具说明（TOOL_SCHEMAS）、提示词、执行池身份都不碰，所以旧执行池照常起来；但迁移 41 之后的开发库只算"新建任务"（G-6）。
- **新事件内容必须由已提交的行确定地算出**（不取时钟、不取随机数）：`Store.append_event` 同幂等键直接返回旧事件，命令重放时新旧内容必须逐字节一样。

### G-5　全局表怎么单独核对

- **计划原文**：阶段 G 第 4 条"全局表（做法、策略）不按任务重放，单独核对"。
- **事实**：`method_library` 晋级事件 `MethodPromoted` 带整条目（`SDK/orchestrator/method_library.py` 约 :166），退役事件带条目编号与原因；**归因 `method_library_attributions` 不发事件**，命令行清空（`MethodLibraryStore.clear`）不发事件；策略两张表的事件 `PolicySeeded`、`PolicyConfigDrift` 写在部署时间线（`DEPLOYMENT_TIMELINE = "deployment"`，`SDK/governance/promotion.py` 约 :33）；`method_contracts` 由规划器提出做法的提交写（`register_method`），登记状态由 `set_method_registration` 改。
- **可选做法**：A. 只核"每行都有个事件点过名"；B. **按全库事件（不分任务）折叠出全局表，与库里比对**，同样分"折叠 / 不可变源记录"；缺事件的补：归因随所在事务补一条 `MethodLibraryAttributed`（或并进规划提交、根终审导入事件的内容），清空在部署时间线上发 `MethodLibraryCleared`。
- **推荐 B**，与业务表同一套口径、同一个比较器，只是输入是全库事件、输出单列一节"全局"。做法定义表 `method_contracts` 的行算不可变源记录还是折叠，由 1.3 的结果定（登记状态会被改 → 折叠）。

### G-6　验收口径里"新建任务"的界定

- **计划原文**：阶段 G 第 4 条"只用'最后一次编码清单变更之后、v3 发布之后新建的任务'"。
- **事实**：G 自己的验收语料（产品同形世界、随机序列）每次都在新空库里建任务，天然满足；真正要界定的是 F2 真机库与开发库——之后 TaskGraph 补全还会改编码清单，界线会再移动。
- **可选做法**：A. 按迁移 41 的应用时间与任务建任务时间比；B. **建任务时在 `MissionCreated` 的内容里写一个"重放口径摘要"**＝哈希（覆盖清单第 2 版全文 + 编码清单 v5 的哈希）；v3 只核摘要等于当前值的任务，其余如实报"范围外：按旧口径建的"，既不算一致也不算不一致。
- **推荐 B**。理由：任务自己说明它生在哪套口径下，清单或编码清单一改，界线自动跟着走，不用再记日期；不是兼容层（旧任务不重放、只如实报范围外）。

### G-7　两库边界：事件内容与三条切点怎么执行

- **计划原文**：阶段 G 第 3 条"执行库里的模型调用与工具事实按稳定引用与消费回执导入，不复制成第二份账"；崩溃测试用 F1 清单里归 G 的 K04、K11、K12。
- **事件内容的裁决**：可选 A. 事件只带执行库的稳定编号，v3 重建时去读执行库（重放要依赖两库，执行库的保留又不归编排管）；B. **事件带"消费回执"：稳定编号 + 当时导入的输入/输出用量与是否未知**（这正是编排据以结账的事实，不是执行库账本的副本：执行库仍是调用事实的唯一权威，编排只记"我消费了哪一条、按多少算的"）。**推荐 B**，新事件 `UsageImported{subject_id, facts:[{usage_ref, input_tokens, output_tokens, unknown}]}`，"未知→已知"的覆盖同样发一条（这张表会被改，必须折叠）；`tool_calls` 只增、行本身就是按执行侧调用编号记的消费回执，由 G-1 自动点名覆盖，不另加事件（一件事一条路径）。另加一项**两库对照**（只读，不进重建）：每条已消费的编号在执行库里存在、用量一致、没有被消费两次。
- **三条切点的执行方法**：

| 行 | 方式 | 断言（恢复要求 + v3） |
|---|---|---|
| K04 执行侧已存结果、编排没消费 | 复用 `crash_seed.py` 的 `after_executor` 进程强退（注入点 `after_turn_committed`），在已有用例同文件加一条恢复后的检查 | 恢复后导入原回执，执行者物理调用数不增；每个 `usage_ref` 只有一条 `UsageImported`；v3 对该任务一致；两库对照通过 |
| K11 用量晚到、任务已终态 | **新写真场景**（产品同形世界）：执行侧调用结账前取消任务 → 任务终态 → 再让执行侧补上用量；不再用猴补的节奏用例充数 | 用量导入原账户、按原预留结账；不新建尝试、不派发、任务状态不变；只多 `UsageImported` 与结账类事件；v3 一致 |
| K12 迟到用量导入写库出错 | 函数级：在 `BudgetLedger.import_usage` 里抛一次 `sqlite3.OperationalError`，放进 `T/product_world/test_round_faults.py`（与 K14～K16 同一个边界） | `run()` 不抛；另一个任务照常完成；出事任务记一轮故障；下一轮导入恰好一次（事务回滚，无半截行、无重复事件）；v3 一致 |

### G-8　v2 删除的范围

- **计划原文**：第二节"v3 取代 v2、不并存"；阶段 G 第 2 条"迁走 A 里列出的 v2 使用方"。
- **裁决（全删，一条路径）**：
  - 删 `SDK/observability/replay.py` 整个文件；`library_copy` 挪到存储层（如 `SDK/storage/store.py` 旁一个小函数），命令行 `policy` 与 v3 命令共用。
  - 命令行 `replay` 改成 v3：`replay --evidence-dir DIR MISSION_ID` 输出 v3 报告；不一致或未覆盖退出 1；`--events FILE`（只给事件文件的离线模式）删掉——v3 要读不可变源记录，单凭事件文件做不到；`--attribution` 保留（它读的是 `observability/traces.py`）。
  - `failure_timeline` 不随 v2 消失：挪到 `observability/traces.py`，改成读库里任务的现行状态加事件流（不再依赖 v2 的投影）。
  - Host 诊断：`report["replay"]` 整节换成 v3 报告（重建状态、一致 / 不一致 / 未覆盖 / 范围外的表与行数、全局一节），`failure_timeline` 移到报告顶层；`service.py::_detect_diagnostics` 的检查清单同步改；`business_replay` 骨架一节并进同一节，不留两节。
  - 前端 `MissionDiagnostics.tsx`：把"回放事件 / 未知事件类型"换成"重建结果：一致 N 张表 / 不一致 / 未覆盖 / 范围外"，失败过程照旧显示；它的测试同步改。按用户"界面简洁易懂"的要求，只显示结论与数字，明细收在展开里。
  - 测试：删 `T/step08/test_replay.py`；`T/p33/test_p33_sources.py`、`T/p33/test_p33_g1_create_sources.py` 里 v2 比对改成 v3 一致性断言（或删掉那一句）；`T/p33_replay_audit.py` 与 `T/p33/test_g_replay_audit_plugin.py` 改写成 v3 审计插件（复用它"每条用例结束后找出临时目录里的库逐个核"的骨架，见 G-8 验收）；Host `test_mission_diagnostics.py` 同步。
  - 清单 `V2_CONSUMERS` 常量与 `coverage_report` 的 `SKELETON` 状态删掉。

### G-9　部署身份检查

- **计划原文**：阶段 G 第 2 条"写死'回放状态是 PARTIAL'与绑定 09-27 冷重放回执的部分同步改"。
- **事实**：冷重放回执证明"冷启动前后任务回执不变"，与 v2 无关，仍有意义；`PARTIAL` 是 v2 的结论，v2 删了它就没有含义。G 期间没有新的真实上游局（真机都在 F2），09-27 的上游库在迁移 38～40 之前，跑不了 v3。
- **推荐**：上游证据里 `semantic_replay` 换成 `business_replay`，取值只有 `NOT_RUN` 与 `CONSISTENT`（与现有 `taskgraph_acceptance` 的 `NOT_RUN / VALIDATED` 同一个写法："读取方永远不把 NOT_RUN 当成通过"）；`CONSISTENT` 时必须带 `business_replay_receipt_sha256`（v3 命令对上游任务库输出的报告文件哈希）。G 发版时按 09-27 上游重生成为 `NOT_RUN`；**F2 刷新部署验收门时**用新的真实上游局跑 v3，改成 `CONSISTENT`。冷重放回执保留。`taskgraph_manifest.py verify` 的反例里加一条"`CONSISTENT` 却缺回执哈希被拒"。

### G-10　验收语料的规模（**用户 2026-10-04 已同意**：最后验收时跑一次整目录）

- **计划原文**：阶段 G 第 4 条"拿产品同形世界的功能用例库与 F1 随机状态机跑出的库（含关库重开）重建"；第六节与记忆硬规则"不许自行跑全量回归或整目录，只跑改动文件对应的测试"。
- **事实**：`T/product_world/` 共 26 个文件、约 91 条用例；G 改的是全库的事件写法，这些用例确实都碰到改动，但规模等于跑一个整目录。随机序列另跑。
- **推荐**：G-1～G-7 每批只跑该批表对应的那几个文件（各批写明）；**只在 G-8 跑一次完整语料**（`T/product_world/` 全部 + 随机序列 2 个种子 × 200 步、每 25 步关库重开），带 v3 审计插件出一份报告。**这一次开跑前请用户确认**——它是计划写明的验收语料，不是回归，但规模触碰硬规则。用户若不同意，退而只跑每批列出的文件之和（覆盖会少几条剧本，报告如实写）。

### G-11　工期

计划写 2～3 周。按第三节逐批估 15～18 个工作日（约 3～3.5 周），多出的部分来自：1.3 核出的"内容不够"远多于清单显示的缺口（73 张表不发事件、只带信号或部分够，清单只显示 27 张）；会被改的约 26 张表建行事件要带整行；两处幂等键吞事件；v2 下游有前端与三处测试插件。G-1 的自动点名已经把 35 张只增表与回执账的工作压到一处，再压就只能降口径。如实报用户知悉，不压缩。

---

## 三、施工清单

> **偏差裁决 1（2026-10-04）**：下面 G-2～G-5 的"领域事件补整行"各项作废，按 `HTN补齐-阶段G-偏差裁决-1.md` 第三节（机制规格）与第四节（逐批改写、改坏 G-05～G-22）施工；工期合计改为 11～13 个工作日。

总规矩：每批先改事件内容与重建函数，再改清单该批各表的 `rebuild` 标注；**每批结束时该批的表在 v3 报告里从"未覆盖"变成"一致"**，这就是该批的验收。每批只跑该批列出的文件。每个功能一条改坏，写进 `T/acceptance_assets/mutations.json`（格式同现有条目；`original` 在施工时取改后代码的原文一段，要求在文件里恰好出现一次，守护用例会核；下面各批写的是"改成什么"的意思）。条目形如：

```json
{
  "id": "G-09",
  "source": "阶段 G",
  "file": "src/agent_orchestrator/governance/budgets.py",
  "original": "（施工后 import_usage 里发 UsageImported 的那一段原文）",
  "replacement": "（同一段去掉发事件的调用）",
  "tests": ["tests/orchestrator/product_world/test_business_replay.py::test_execution_and_budget_tables_rebuild_with_an_operation"],
  "note": "导入用量不发事件：imported_usage 在 v3 报告里变成不一致"
}
```

用执行器 `scripts/acceptance/run_mutations.py G-01 …` 跑，只认断言失败。

### G-0　金丝雀核对（半天；只跑单条用例或临时脚本，不改正式代码）
1. 自动点名的前提：临时脚本在一个连接上装临时触发器，跑一个产品同形用例，核对①所有对只增表与回执表的插入都落在 `Store.transaction()` / `atomic()` 的最外层里（没有绕过它直接 `connection.execute` 再自行提交的写法）；②最外层提交前追加事件不撞已有的提交钩子与"事件必须在事务里"的检查。不成立就按 G-1 的退路逐处加点名，G-1 工期 +1 天。
2. 已结束任务收到全局 `AssuranceEvidenceChanged` 后，保证通道收尾与有效性消费者是否真的排进 `assurance_pending_work`、处理时是否只是空转（G-3 的前提）。
3. `original_source_mutation` 在"没有未结束绑定任务"时的行为（G-3 要改的那一处）。
4. 测试里有没有直接 UPDATE / DELETE 35 张只增表或回执表造坏数据的夹具（迁移 41 守卫会挡住它们），列清单。
5. 在一个跑完的产品同形库上，按 1.3 试折叠 `missions`、`tasks`、`plan_revisions` 三张会改的表，估一下"建行事件带整行"之后的事件表体积增长（结果信封、计划网络若直接进事件会不会太大——太大就走内容寻址仓库带哈希）。
每条一两句结论写进实施记录"G-0"。

### G-1　v3 引擎、自动点名、清单第 2 版、迁移 41（3 天）
- 改动面：存储层自动点名（G-1 裁决：每连接临时触发器 + 最外层提交前追加 `SourceRecordsWritten`，覆盖 35 张只增表与回执账）；`SDK/observability/business_replay.py` 重写为真正的 v3：`rebuild(store, mission_id)` 在只读副本上按事件折叠出各业务表的行、核不可变源记录的点名与哈希；`verify(...)` 与库里逐表逐字段比（时间字段只核先后），报告每张表 `CONSISTENT / INCONSISTENT / NOT_COVERED / OUT_OF_SCOPE` 与差异行号；全局一节（G-6 时填）；`check_inventory` 加三条规矩（业务表必须有 `rebuild` 与重建函数名、`gaps` 必须为空才算覆盖、非业务表必须写排除理由）。清单第 2 版：G-2 的归类改动、1.2 的过时处、删表。重放口径摘要写进 `MissionCreated`（G-6）。迁移 41（G-4 的三部分；全局触发器按 G-3）与 `assurance_changes.original_source_mutation` 的配套改动，`assurance_source_inventory.py` 同步。v3 审计插件（从 `T/p33_replay_audit.py` 改写，默认只观察、`--strict` 时有不一致或未覆盖就让整轮失败）；随机序列 `check_invariants` 加"v3 一致"一项。
- 功能用例：
  1. `T/full_target/test_business_replay_inventory.py` 改写：清单第 2 版规矩（缺 `rebuild`、有 `gaps`、排除无理由各失败一次）。
  2. 新 `T/product_world/test_business_replay.py::test_a_completed_mission_rebuilds_its_folded_and_source_tables`：一个产品同形任务跑完，已收口的表报 `CONSISTENT`，删一行源记录报 `INCONSISTENT`（在副本上删，不碰守卫）。
  3. 同文件 `::test_a_mission_born_under_another_inventory_is_out_of_scope`。
  4. 同文件 `::test_global_change_wakes_only_unfinished_missions`：一个已结束、一个进行中的绑定任务，登记一个新做法 → 只有进行中的任务多一条 `AssuranceEvidenceChanged`、全局纪元 +1。
  5. 同文件 `::test_immutable_sources_refuse_update_and_delete`（参数化抽 3 张表）。
  6. 同文件 `::test_every_immutable_row_and_receipt_is_named_in_its_own_transaction`：跑完一个任务，只增表与回执表的每一行都恰好被一条 `SourceRecordsWritten` 点名、哈希对得上；没有无主的行。
- 改坏：
  - G-01 `business_replay.py` 比较时跳过最后一列 → 用例 2 抓到；
  - G-02 迁移 41 全局触发器去掉"未结束"条件 → 用例 4；
  - G-03 `MissionCreated` 不写口径摘要 → 用例 3；
  - G-04 清单校验不查 `gaps` → 用例 1；
  - G-17 自动点名漏掉回执表（临时触发器清单里去掉 `commit_receipts`）→ 用例 6。
- 只跑：上面两个文件 + `test_acceptance_assets.py`。

### G-2　规划与计划（3 天）
- 表：`planning_requests`、`planning_decisions`（多数分支不发事件）、`planning_human_requests`、`planning_admission_checks`、`planning_lane_grants`、`planning_request_authority_bindings`、`planning_operation_action_links`、`plan_revisions`、`plan_read_sets`、`plan_commit_receipts`、`order_constraints`、`data_requirements`、`method_instances`、`method_child_occurrences`、`task_semantics`、`taskgraph_revision_records`、`taskgraph_policy_bindings`、`taskgraph_convergence_jobs/targets`、`taskgraph_attempt_inputs`、`requirements_revisions`、`obligations`、`mission_domains`、`mission_planning_protocols`、`mission_policies`、`missions`、`tasks`。
- 做法（只增表已由 G-1 自动点名收口，本批只管会改的表与派生核对）：
  - `planning_decisions`：解码、准入、编译三种进度行（`event_handler.py` 回复收集里的 `record_progress`，约 :6351）现在完全不发事件——每次写入发一条 `PlanningDecisionRecorded`，带这一行改后的全部列（正文大时放内容寻址仓库、带哈希）；`PlanningDecisionEvaluated` 的幂等键改成"决定编号 + 序号 + 状态"，不再吞掉同一决定的第二次评估。
  - `planning_requests`（建行只有 `BudgetReserved`、换派发意图 `rebind_planning_request_intent` 同样）、`planning_human_requests`（建题只带编号）、`plan_revisions`（读集原文）、`taskgraph_convergence_jobs`（`WAITING→WAITING` 只加版本号不发事件——改成发；建行补 `decision_id`、`request_id`、`source_revision`）、`missions`（`MissionCreated` 补实际生效预算、成功标准、停止条件、允许工具、风险级别、规划协议四项、领域版本与快照、策略提供方；`MissionCancelled` 与规划失败的 `MissionFailed` 补终报）、`tasks`（`TaskCommitted` 补完整任务与序号）、`obligations`（根义务与子义务建行补签名、燃料上限、预算谱系、授权；`revise_requirement_refs` 登记）、`method_instances`（建行补草稿全文，或核"与网络文档一致"）。
  - 由计划网络文档推出的只增行（`order_constraints`、`method_child_occurrences`、`data_requirements`、`operation_completion_scopes`）：v3 用 `taskgraph_revision_records.network_json` 现算并比对，算不出来的列记为缺口在本批补。（偏差裁决 3：前三张已由 `taskgraph_history_sources.validate_revision_sources` 覆盖；`operation_completion_scopes` 改按独立源记录核，不属网络文档投影。）
  - `initialize_root`：同事务的义务建行事件带整行（会改的表）；根任务语义、要求第 1 版（只增）由自动点名覆盖。
- 功能用例：`T/product_world/test_business_replay.py` 加 `::test_planning_and_plan_tables_rebuild_after_repair_and_amendment`（换做法一次、改要求一次、问人一次后上列各表 `CONSISTENT`）；`::test_a_second_evaluation_of_the_same_decision_is_not_swallowed`。
- 改坏：G-05 规划决定"进度"分支不发事件 → 抓到；G-06 `MissionCreated` 写请求预算而不是实际生效预算 → 抓到（有全局预算时两者不同）；G-18 `PlanningDecisionEvaluated` 键改回"决定编号" → 抓到。
- 只跑：`test_business_replay.py`、`test_requirements_amend.py`、`test_repair_replace_method.py`。

### G-3　审阅与有效性（2 天）
- 表：`review_packages`、`review_records`、`criterion_evaluations`、`input_manifests`、`input_manifest_bindings`、`validity_witnesses`、`observations`、`validity_epochs`、`acceptances`、`acceptance_outputs`、`delivery_receipts`、`goal_resolutions`、`claims`、`knowledge`、`verifications`。
- 做法：
  - 只增的审阅三件套、见证、观察由 G-1 自动点名收口；G0 审计列的"组合审阅、根终审记录、操作结果审阅"三条写记录缺口**已不存在**（1.2 第 h 条），清单删掉。
  - 根终审切包 `RootReviewCoordinator.cut` 写包与输入清单的事务，和发 `HierarchicalRootReviewCut` 的事务是两个——**并成一个**（中间崩溃会留下没人认领的包）；保证通道下操作提议审阅预备同样核"写包与点名同一事务"。
  - 会改的：`goal_resolutions`（`GoalResolutionCommitted` 补结论全文）、`claims`（建行补全文；判定、争议、取代各事件补改后的列）、`knowledge`（`KnowledgeCommitted` 补正文、类型、证据）、`verifications`（`VerificationLayerRecorded` 带完整明细，幂等键改成"结果:层:序号"）、`validity_epochs`（`bump_epoch` 的事件补 `bumped_by` 明文；保证通道行由触发器事件已够）。
- 功能用例：`::test_review_and_validity_tables_rebuild_after_root_rejection_and_observation_flip`（根终审打回一次、中间目标组合审阅一次、观察翻转一次、同一结果同一层验证两次）。
- 改坏：G-07 根终审切包与点名分两个事务（在两者之间抛错，留下无主的包）→ 抓到；G-08 `VerificationLayerRecorded` 键改回"结果:层" → 抓到。
- 只跑：`test_business_replay.py`、`test_sub_goal.py`、`test_desktop_preconditions.py`、`test_review_no_verdict.py`。

### G-4　执行、记账与两库边界（3 天）
- 表：`attempts`、`results`、`artifacts`、`tool_calls`、`imported_usage`、`budget_accounts`、`budget_reservations`（含 `BudgetLedger.grow` 不发事件那一处）、`budget_tail_holds`、`budget_tail_transfers`、`actions`、`approvals`、`approval_decisions`、`operation_*` 九张。
- 做法：G-7 的 `UsageImported`（`tool_calls` 由自动点名覆盖）；`grow`（模型调用追加额度）补 `BudgetReservationGrown`；尾部预留的建立与释放补 `BudgetTailHeld` / `BudgetTailReleased`；`BudgetReserved` 补账户、工具调用数，`MissionCreated` / `TaskCommitted` 补完整限额；`AttemptCreated`、`ResultSubmitted`（结果信封走内容寻址仓库带哈希）、`ActionProposed`、`ApprovalRequested` 等建行事件补整行；`propose_action` 再投递分支与同分支的规划来源链接补事件；`artifacts` 建行补整行（`record_result` 路径）；`artifacts` 的迁移改写路径只作用于旧库（G-1 的例外类）。两库对照函数（只读）。K04、K11、K12 三条按 G-7 的表执行，结果写回 `crash_points.json` 的 `tests` 一栏。
- 功能用例：K04（`test_process_recovery.py` 新加一条）、K11（新 `T/product_world/test_late_usage.py::test_usage_arriving_after_the_mission_ended_settles_the_original_account`）、K12（`test_round_faults.py` 新加一条）；`test_business_replay.py::test_execution_and_budget_tables_rebuild_with_an_operation`（含一次发布审批）。
- 改坏：G-09 `import_usage` 不发 `UsageImported` → 抓到；G-10 "未知→已知"覆盖时不发事件 → 抓到；G-11 K12 的导入写在故障边界之外 → 抓到（主循环冲出）。
- 只跑：上列四个文件 + `test_operation.py`。

### G-5　保证通道与回执账（1.5 天）
- 表：`assurance_*` 十张业务表（`assurance_blob_pins` 已改运行表）、`commit_receipts`（log）。
- 做法：回执账与保证通道只增的各绑定表（检查、审阅、审阅记录、准则策略、披露批次、任务绑定、证书、建任务契约）由 G-1 自动点名收口——它们的正文本来就在本表或回执里，补守卫后就是不可变源记录；`record_review_interruption` 现在在事件事务之外单写回执，改成在同一事务里写（否则回执点名会落在部署时间线、归属丢失）。会改的只有 `assurance_closeouts`：`AssuranceCloseoutEvaluated` 补评估正文（或带回执编号、正文在回执里）。`assurance_creation_contracts`、`commit_receipts` 的迁移补写只作用于旧库（G-1 的例外类）。
- 功能用例：`test_business_replay.py::test_assurance_tables_and_receipts_rebuild`（一次正常收尾 + 一次审阅调用被打断后复审）；v3 报告"无主回执 = 0"。
- 改坏：G-12 审阅调用被打断的回执移回事件事务之外 → 抓到（回执归到部署时间线，任务内点名缺一条）。
- 只跑：`test_business_replay.py`、`test_review_call_unanswered.py`。

### G-6　全局表（1 天）
- 做法：G-5 的全局折叠；补 `MethodLibraryAttributed` 与 `MethodLibraryCleared`；策略两张表由部署时间线事件折叠；报告"全局"一节。
- 功能用例：`T/product_world/test_method_library.py` 加 `::test_global_tables_rebuild_after_promotion_attribution_retirement_and_clear`。
- 改坏：G-13 清空不发事件 → 抓到；G-14 归因不发事件 → 抓到。
- 只跑：`test_method_library.py`、`test_business_replay.py`。

### G-7　v3 取代 v2、部署身份、诊断与界面（2 天）
- 做法：按 G-8 删 v2、迁使用方；按 G-9 改部署身份与清单生成脚本，重生成部署清单（`NOT_RUN`）。
- 功能用例：Host `test_mission_diagnostics.py` 改成断言 v3 一节与顶层失败过程；前端 `MissionDiagnostics.test.tsx` 同步；SDK 命令行 `replay` 一条（`T/` 下现有命令行用例文件里加，或新文件）——不一致时退出 1；`taskgraph_manifest.py verify` 新反例"`CONSISTENT` 缺回执哈希被拒"。
- 改坏：G-15 部署读取方接受 `business_replay` 为任意值 → `verify` 反例抓到；G-16 命令行 `replay` 遇到不一致仍退出 0 → 抓到。
- 只跑：上列各文件（Host 只跑 `test_mission_diagnostics.py`，前端只跑 `MissionDiagnostics.test.tsx`）。

### G-8　验收与收尾（2 天）
1. **完整语料一次**（G-10，开跑前问用户）：`T/product_world/` 全部 + 随机序列 2 个种子 × 200 步（每 25 步关库重开），带 v3 审计插件 `--strict`；**口径：覆盖清单范围内每个"范围内"任务每张业务表 `CONSISTENT`、`NOT_COVERED = 0`、`INCONSISTENT = 0`、无主回执 = 0；全局一节一致；两库对照通过**；清单外的表（派生、运行、全局、日志）在报告里逐张带排除理由。报告存 `.local-test-evidence/<日期>/g-replay/`（不进仓库）。
2. 守护：清单里每张业务表都有 `rebuild`（清单第 4 版删去 `writers`、`events` 两栏）；静态扫描"编排库的可写连接只来自存储层"（`agent_orchestrator` 里不带 `mode=ro` 的 `sqlite3.connect` 只许出现在允许清单的四个文件里），与"绕过存储层改一行 → 链断"用例一起，堵住"没被记下的写入"从后门回来（偏差裁决 2）。
3. 改坏执行器跑全部 G 条目（到 G-25；G-08、G-12、G-18 在改坏表里注明无可达触发），只认断言失败。
4. 实施记录"阶段 G"；台账 R37（关键业务数据都能由事件重建）改"已接入"、R38 核对；主计划升第 3.20 版（第五节末文字）；`ARCHITECTURE/` 与 SDK `ARCHITECTURE/index.md` 记 v3 的位置与用法；评估子代理对照本清单评估；一次 Opus 只读阻断核验；发版（SDK opt.149，Host 钉版）。

**工期合计**：G-0 0.5 + G-1 3 + G-2 3 + G-3 2 + G-4 3 + G-5 1.5 + G-6 1 + G-7 2 + G-8 2 ≈ **18 天**，按 15～18 天报（G-0 第 1 条成立、第 5 条显示事件体积可直接带整行时，G-2、G-4 各可省 1 天；第 1 条不成立则 G-1 加 1 天）。

---

## 四、不做

| 事项 | 理由 / 去向 |
|---|---|
| 旧库迁移与一致性快照（v1.4 §16.3） | 计划第 5 条；开发期不兼容旧库，迁移 41 之前建的任务如实报"范围外" |
| 完整恢复与删除墓碑（§16.4） | 计划第 5 条；与已删的受管恢复同属"恢复"，以后单独立项 |
| 长期承诺、周期相关的重放 | 表一 10 保持暂缓 |
| 用 v3 结果替换生产库、自动恢复 | 原 TaskGraph 计划 §10.3 第 6 条：只写离线目标库，不替用户换库 |
| 把运行表、派生表重建到可执行状态 | 运行表重建时不恢复，恢复运行前重新核对；派生表现算 |
| 执行库自身的事件重放 | 执行库是调用事实的唯一权威；G 只做"已消费编号"的两库对照 |
| 领域事件里复制整行 | 会被改的表由存储层记整行变化，只增表只点名（偏差裁决 1） |
| 真机库上的 v3 核对、随机序列 2000 步 | F2 |
| 已发布迁移 1～40、编码清单五个文件、工具说明、提示词 | 不改（G-4） |
| 组合审阅"先写后提交"改成一个事务 | 既定流程；只要求每个事务里的写入被本事务事件点名 |
| `_import_hold` 与 `_unguarded_usage` 里给未绑定执行图的旧任务留的跳过分支 | 属"旧路径直接删"的另一件事，不是重放；另记，不在 G 里顺手改 |

---

## 五、偏差单与主计划第 3.20 版文字

| # | 计划原文 | 发现的事实 | 裁决 | 告知用户 |
|---|---|---|---|---|
| G-a | "覆盖"＝有事件 | 清单只核了同事务有没有事件，没核内容 | 口径改为"事件带内容，或点名不可变源记录（编号与哈希，存储层每个事务自动点名）；可变表建行事件带整行、由事件折叠"（G-1） | **是** |
| G-b | 覆盖清单 109 张、业务 86 张 | 现 105 张、业务 82 张；6 处过时；3 张策略表、2 张义务表无写方 | 清单第 2 版；死表进迁移 41 删（G-2） | 否 |
| G-c | `commit_receipts`、`assurance_blob_pins`、`workspaces` 归类待定 | 见 G-2 | 回执账改 log 并加守卫、每条回执被事件点名；另两张改运行表 | 否 |
| G-d | 屏障开销"G 定" | 事件按历史任务数线性增长、全部署寿命内近似平方增长，下游还排工作 | 全局触发器只给未结束任务写（迁移 41）（G-3） | **是** |
| G-e | "最后一次编码清单变更之后新建的任务" | 界线以后还会动 | `MissionCreated` 带重放口径摘要（G-6） | 否 |
| G-f | 两库边界"不复制成第二份账" | 用量与工具调用两张消费表都不发事件 | 事件带消费回执（编号 + 当时按多少算）；另做两库对照；K11 改写真场景（G-7） | 否 |
| G-g | 部署身份"同步改" | 冷重放回执与 v2 无关；G 期间无新真实上游 | `semantic_replay` 换成 `business_replay: NOT_RUN / CONSISTENT`，F2 刷新为 `CONSISTENT`（G-9） | 否 |
| G-h | 验收用功能用例库与随机序列库 | 规模等于整目录 | G-8 跑一次（G-10） | **用户 2026-10-04 已同意** |
| G-i | G 2～3 周 | 估 15～18 个工作日 | 如实（G-11） | **是** |

**需要用户本人决定的事：一条（G-10，验收语料要跑一次 `T/product_world/` 整个目录）。**

### 主计划第 3.20 版可直接粘贴的文字

**第二节末尾加一段**：

> **阶段 G 开工裁决新增的偏离**（裁决子代理 2026-10-04，记录见 `HTN补齐-阶段G-开工裁决与施工清单.md`）：
> - "能由事件重建"的口径：事件带内容，或点名不可变源记录（只增、库层不许改删；存储层在每个事务提交前自动追加一条"源记录已写"事件，带编号与内容哈希）；会被改的表建行事件带整行、每次改动有事件，完全由事件折叠。依据原计划 v1.4 §16.1 与原 TaskGraph 计划 §10.1。
> - 保证通道全局触发器只给未结束的任务写证据变更事件（迁移 41）；全局纪元照旧加 1。
> - 命令回执账 `commit_receipts` 归为日志、加不许改删守卫，每条回执被同事务事件点名；`assurance_blob_pins`、`workspaces` 归为运行表；无写方或生产始终为空的 `bound_inputs`、`obligation_relations`、`obligation_expansions`、`obligation_shape_changes`、三张策略表删除，连同 `settle_known`、`record_delivery_receipt` 等走不到的写方。
> - 部署身份里的 `semantic_replay: PARTIAL` 换成 `business_replay: NOT_RUN / CONSISTENT`，F2 用新的真实上游局刷新。
> - 命令行 `replay` 的"只给事件文件"离线模式删除（v3 要读不可变源记录）。

**阶段 G 一节第 1 条**末尾加："口径与批次按 `HTN补齐-阶段G-开工裁决与施工清单.md`（9 步 G-0～G-8）；所有库结构改动合成一个迁移 41。"**第 4 条**末尾加："任务是否在范围内由 `MissionCreated` 里的重放口径摘要判定；G-8 跑一次完整语料（`T/product_world/` 全部 + 随机序列 2 种子 × 200 步），开跑前问用户。"

**第七节工期表**"G 收尾 | 2～3 周"改为"3～3.5 周（15～18 个工作日）"，合计相应加约半周。

**修订记录加一条**：

> - **第 3.20 版（2026-10-04）**：阶段 G 开工裁决（`HTN补齐-阶段G-开工裁决与施工清单.md`）改入：重建口径（内容或不可变源记录引用，存储层自动点名）；全局触发器只给未结束任务写；回执账归日志、两张表归运行、死表删除，库结构改动合成迁移 41；重放口径摘要界定"新建任务"；两库边界事件带消费回执、K11 改真场景；v2 全删（含命令行事件文件模式、前端诊断一节）；部署身份 `business_replay`；G 工期 3～3.5 周；验收语料跑一次整目录需用户确认。

---

## 附：逐表核对表（1.3）

结论按该表最差的写入口；"G 后"一栏是按 G-1 口径的收口方式：**点名** = 只增表（或回执账），补守卫后由自动点名覆盖；**折叠** = 会改的表，事件要带出这里列的缺项；**删** / **运行** = G-2 的归类。缩写：PRC = `PlanRevisionCommitted`，TGRR = `TaskGraphRevisionRecorded`，TGSC = `TaskGraphSourceChanged`。

| 表 | 主要写入口 | 同事务事件 | 结论 | 缺什么 | G 后 |
|---|---|---|---|---|---|
| acceptance_commit_receipts | `htn_store.record_acceptance_receipt` ← `resolution_commits._record_receipt` | AcceptanceCommitted / GoalResolutionCommitted | 全 | — | 点名（样板） |
| acceptance_outputs | `insert_acceptance_output` ← `accept_review` | AcceptanceCommitted | 部分 | 生产者实例与结果、支持修订、内容哈希、`output_json` | 点名 |
| acceptances | `insert_acceptance` ← `accept_review` | AcceptanceCommitted | 部分 | `acceptance_json.policy_ref` | 点名 |
| actions | `store.put_action` ← `action_commits` 7 处 | ActionProposed / Refused / Superseded / HandedOff 等 | 部分 | 建行：参数、理由、结果与尝试、产物、所需批准、规划来源、历史；**再投递分支不发事件** | 折叠 |
| approval_decisions | `store.insert_decision` | ApprovalGranted / Rejected | 部分 | `nonce`、完整主体 | 点名 |
| approvals | `store.put_approval`（约 15 处） | Approval* / Source* / VerificationSuspended | 部分 | 建行卡片摘要、批准人列表、绑定全文 | 折叠 |
| artifacts | `upsert_artifact` ← `record_result`；本地检查导入；`update_artifact_verification` | ResultSubmitted（只有编号）等 | 仅信号 | 记录结果时整行 | 折叠 |
| assurance_blob_pins | `acquire_pin`、`transition_pin` | 无（内容只在回执） | 无事件 | 整行 | 运行 |
| assurance_check_bindings | `record_check_binding` | AssuranceCheckBound | 部分 | `binding_json` 大部分 | 点名 |
| assurance_closeouts | `_write_locked`、`finalize_assured_mission` | AssuranceCloseoutEvaluated 等 | 部分 | `check_body_json` 评估正文 | 折叠 |
| assurance_creation_contracts | `record_creation_contract`；迁移补写 | MissionCreated、AssuranceProfileActivated | 全（迁移补写除外） | — | 点名 |
| assurance_criterion_policies | `record_criterion_policy` | AssuranceCheckPolicyApproved | 部分 | 要求版本与哈希、范围哈希、策略正文 | 点名 |
| assurance_disclosure_batches | `record_disclosure` | AssuranceEvidenceDisclosed | 部分 | 披露条目正文、批次哈希 | 点名 |
| assurance_mission_bindings | `bind_profile_locked` | AssuranceProfileActivated | 部分 | `policy_json` | 点名 |
| assurance_review_bindings | `ensure_review_invocation` | AssuranceReservationLinked、BudgetReserved | 部分 | 包、请求、轮次、要求、清单、绑定正文 | 点名 |
| assurance_review_invocations | 同上 | 同上 | 部分 | 复审原因与前序回执 | 点名 |
| assurance_review_record_bindings | `PreparedOfficialReview.import_locked` | AssuranceReviewImported | 部分 | 审阅键、原文哈希、证据清单哈希、绑定正文 | 点名 |
| assurance_use_certificates | `record_certificate` | AssuranceUseCertified | 部分 | 读集与证书正文、期限 | 点名 |
| attempts | `insert_attempt`；`update_attempt` 14 处 | AttemptCreated、Attempt* 等 | 部分 | 建行：提示词与上下文版本、执行池、输入哈希、任务版本；心跳进度、结果失败风险 | 折叠 |
| bound_inputs | `insert_bound_input` | — | 无生产调用方 | — | 删 |
| budget_accounts | `open_account`；`_apply` | MissionCreated、TaskCommitted、Budget* | 部分 | 全局与任务的完整限额、工具调用数增减、`grow` 增量 | 折叠 |
| budget_reservations | `reserve`、`settle`、`grow`、`transfer_tail` | BudgetReserved / Released 等 | 部分 | 账户、工具调用数、`grow` 无事件、尾部预留无事件 | 折叠 |
| budget_tail_holds | `reserve_tail`、`transfer_tail`、`release_tail` | 没有一条讲尾部预留 | 仅信号 | 整行 | 折叠（新事件） |
| budget_tail_transfers | `transfer_tail` | BudgetReserved | 部分 | 任务修订、工具调用分配 | 点名 |
| claims | `upsert_claim`（建行与 6 种改动） | ResultSubmitted（只有条数）等 | 仅信号 | 建行整行；判定结果、置信、争议全集 | 折叠 |
| commit_receipts | `insert_receipt`（约 40 处） | 约一半"事件就是回执"，一半事件比回执少，7 类无事件 | 部分 | 见 G-2 | 日志（点名） |
| criterion_evaluations | `insert_review_record` ← `import_locked`（唯一） | AssuranceReviewImported（只有记录编号） | 仅信号 | 整行 | 点名 |
| data_requirements | `insert_data_requirement` ← `plan_commits._write` | PRC（只有个数） | 仅信号 | 整行 | 点名（另核与网络文档一致） |
| delivery_receipts | `record_delivery_receipt` ← `accept_review` | AcceptanceCommitted | 部分 | 收据编号、阶段、操作、证据引用 | 点名 |
| goal_resolutions | `insert_goal_resolution` | GoalResolutionCommitted | 部分 | 审阅回执、结论、有效性、结论正文 | 折叠（`adopted` 由事件推） |
| human_overrides | `insert_override` | HumanOverride（整条记录） | 全 | — | 点名 |
| imported_usage | `BudgetLedger.import_usage`（4 个调用方） | — | 无事件 | 整行、"未知→已知"覆盖 | 折叠（`UsageImported`） |
| input_manifest_bindings | 10 个调用点 | 各路径不一 | 部分 | 多处缺 `task_id`、清单哈希；本地检查非首次、根终审切包无事件 | 点名 |
| input_manifests | `insert_input_manifest` | 最多带清单哈希 | 部分 | 清单正文 | 点名 |
| knowledge | `upsert_knowledge` | KnowledgeCommitted / Used / Disputed / Superseded | 部分 | 正文、类型、提出者、来源结果、证据 | 折叠 |
| method_child_occurrences | `_insert_child_occurrence` | PRC | 仅信号 | 整行 | 点名（核与网络文档一致） |
| method_instances | `insert_method_instance`、`set_method_instance_state` | PRC（只有编号；退役全） | 部分 | 目标、义务、做法引用、参数摘要、草稿 | 折叠 |
| mission_domains | `bind_mission_domain` | MissionCreated（只有领域编号） | 部分 | 领域版本与快照 | 点名 |
| mission_planning_protocols | `bind_mission_protocol` | MissionCreated | 仅信号 | 协议、规划包、提示词版本、绑定哈希 | 点名 |
| mission_policies | `bind_mission_policy`；旧库补绑 | MissionCreated | 部分 | 提供方、正文 | 点名 |
| missions | `insert_mission`、`update_mission` | MissionCreated / Planning / Failed / Completed / Cancelled 等 | 部分 | 幂等键、实际生效预算、成功标准等；取消与规划失败的终报 | 折叠 |
| obligation_expansions | `persist`（生产始终为空）、`consume_fuel` | — | 生产为空 | — | 删 |
| obligation_relations | `add_relation` | — | 无生产调用方 | — | 删 |
| obligation_shape_changes | `persist`（生产始终为空）、`note_shape_change` | — | 生产为空 | — | 删 |
| obligations | `register`、`persist`、`admit/withdraw_demand`、`set_lifecycle`、`revise_requirement_refs` | ObligationDemandAdmitted / Withdrawn、GoalResolutionCommitted、PRC、RequirementsAmended | 部分 | 建行签名、燃料上限、预算谱系、授权；父义务扣燃料后的值 | 折叠 |
| observations | `insert_observation` | TGSC（只有引用与哈希） | 仅信号 | 整行 | 点名 |
| operation_acceptance_scopes | `insert_acceptance_scope` | AcceptanceCommitted | 部分 | 范围与贡献正文 | 点名 |
| operation_bindings | `bind_operation` | OperationMaterialized | 部分 | 义务、操作种类、信封正文 | 点名 |
| operation_completion_scopes | `insert_scope` ← 计划提交 | PRC | 仅信号 | 整行 | 点名 |
| operation_completion_specs | `insert_spec` | OperationCompletionSpecApproved | 部分 | 规格正文 | 点名 |
| operation_identities | `bind_operation` | OperationMaterialized | 部分 | 信封正文 | 点名 |
| operation_intent_bindings | `OperationIntentStore.insert` | OperationIntentSubmitted | 部分 | 来源、生产者、要求与计划版本等十几列 | 点名 |
| operation_outcome_review_bindings | `insert_outcome_binding` | OperationOutcomeReviewPrepared | 部分 | 规格、效果、意图、操作等与正文 | 点名 |
| operation_payload_objects | `put_payload` | OperationIntentSubmitted（三个对象引用） | 全（以仓库字节保留为前提） | — | 点名 |
| order_constraints | `insert_order_constraint` | PRC（只有个数）、TGRR | 仅信号 | 整行 | 点名（核与网络文档一致） |
| plan_commit_receipts | `record_commit_receipt` | PRC | 部分 | 读集原文、全量出现、任务版本 | 点名 |
| plan_read_sets | `record_read_set` | PRC（只有读集哈希） | 仅信号 | 整行 | 点名 |
| plan_revisions | `insert_plan_revision`、`activate_plan_revision` | PRC、TGRR | 部分 | 读集原文（激活全） | 折叠 |
| planning_admission_checks | `put_admission_check` | TGRR、PRC | 部分 | 请求、各哈希、明细 | 点名 |
| planning_decisions | `record_planning_decision`（进度三种无事件；终态） | PlanningDecisionEvaluated 等 | 仅信号 / 无事件 | 进度行整行；终态正文；幂等键吞第二次 | 折叠 |
| planning_human_requests | `register`、`answer`、`retire_stale` | PlanningHumanRequested / Answered / Stale | 仅信号（建题） | 问题原文、选项、绑定 | 折叠 |
| planning_lane_grants | `put_grant` | TGSC | 仅信号 | 授权正文 | 点名 |
| planning_operation_action_links | `put_operation_action_link` | ActionProposed（发在写链接之前，不带它）；再投递分支无事件 | 仅信号 | 整行 | 点名 |
| planning_request_authority_bindings | `_insert_binding`（经 `put_grant`、`bind_request`） | TGSC / 无 | 仅信号 / 无事件 | 整行 | 点名 |
| planning_requests | `insert_planning_request`、`rebind_planning_request_intent` | BudgetReserved | 仅信号 | 整行、换派发意图 | 折叠 |
| requirements_revisions | `insert_requirements_revision`（建任务、改要求） | AssuranceProfileActivated / RequirementsAmended（只有哈希与编号） | 仅信号 | 要求原文 | 点名 |
| results | `insert_result`、`set_result_verification` | ResultSubmitted、Verification*、ResultRejected | 部分 | 结果信封、回合编号、结果哈希 | 折叠 |
| review_packages | `insert_review_package`（叶子、保证通道、操作结果、操作提议、根终审切包） | 各异；根终审切包事件**在另一事务** | 部分 | 包正文 | 点名（根终审并成一个事务） |
| review_records | `insert_review_record` ← `import_locked`（唯一） | AssuranceReviewImported | 仅信号 | 整行 | 点名 |
| sources | `put_source` | SourceRegistered / Superseded / Revoked（带整行） | 全 | — | 折叠（已够） |
| task_semantics | `put_task_semantics`（计划提交、撤销在跑工作、根初始化、改要求） | PRC、RequirementsAmended 等 | 仅信号 | 整行 | 点名 |
| taskgraph_attempt_inputs | `insert_attempt_inputs` | TaskGraphDispatchBound | 部分 | 合同哈希、创建键、输入编号与冻结哈希 | 点名（已有守卫） |
| taskgraph_convergence_jobs | `begin_convergence`、`_transition` | Requested / Advanced；`WAITING→WAITING` 无事件 | 部分 | 决定、请求、来源修订；版本号 | 折叠 |
| taskgraph_convergence_targets | `begin_convergence` | Requested（只有影响哈希） | 仅信号 | 整行 | 点名（已有守卫） |
| taskgraph_policy_bindings | `enable_taskgraph_contract` | TaskGraphContractEnabled | 部分 | 策略正文 | 点名（已有守卫） |
| taskgraph_revision_records | `insert_revision_record` | TGRR | 部分 | 网络文档、证书、要求版本 | 点名（已有守卫） |
| tasks | `insert_task`、`update_task` | TaskCommitted、Task* 等 | 部分 | 建行完整任务与序号 | 折叠 |
| tool_calls | `record_tool_call` | — | 无事件 | 整行 | 点名（只增；行本身就是按执行侧调用编号记的消费回执） |
| validity_epochs | 屏障触发器；`bind_profile_locked`；`bump_epoch` | AssuranceEvidenceChanged（全）、TGSC（缺 `bumped_by`） | 部分 | `bumped_by` 明文 | 折叠 |
| validity_witnesses | `insert_validity_witness` ← `_record_witness`（唯一） | TGSC | 仅信号 | 整行 | 点名 |
| verifications | `upsert_verification` | VerificationLayerRecorded（键吞第二次） | 部分 | 明细正文 | 折叠 |
| workspaces | `register_workspace`、`set_workspace_state` | — | 无事件 | 整行 | 运行 |

说明：标"点名"的表里，有 17 张库层已有不许改删守卫，其余 35 张由迁移 41 补。
