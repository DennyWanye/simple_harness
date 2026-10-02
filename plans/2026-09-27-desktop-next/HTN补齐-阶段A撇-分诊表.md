# HTN 补齐 阶段 A′ 测试分诊表（2026-10-03）

- 依据：`HTN补齐-阶段A撇-方案.md` 第 3 版第五节（分诊口径）；`HTN补齐-阶段A撇-清点.md` 第二节第 6 小节（文件清单）。
- 做法：只读。逐文件读源码；对每个用例用脚本沿本地辅助函数做传递分析（是否真起 `Orchestrator`、是否碰"提交计划 / 建尝试 / 记结果 / 验收 / 结算 / 审阅"、是否需要任务行），再人工逐个核。8 个大文件（共 673 条）由三个 Opus 子代理逐用例分诊，我审读后并入。没跑测试、没改代码。
- 范围：清点列出的 SDK 测试文件 225 个（含 11 个 0 用例的辅助模块，另算）+ 清点标"不受影响"的 15 个文件（核一遍）+ `scripts/assurance_seams/` 19 个脚本（17 个接缝 + 2 个辅助）。用例按 `def test_` 计，共 2042 条；清点按文本计的 `test_real_provider_hierarchical_smoke.py` 是 3 条，实际 1 条（另两条是写进临时仓库的字符串）。
- 当前工作树：`htn-a-prime`，含已提交的第 2 步部分（`deployment/`、`testing/product_world.py`、两条代表用例）。

**分类键**（与方案一致；按用例判，混合文件拆开计）

| 键 | 含义 |
|---|---|
| A | 真起 `Orchestrator` 主循环 → 共用构造器换芯，用例保留。`object.__new__(Orchestrator)` 和 `_assured_fixture.AssuredRuntime`（拼出来的假编排器）**不算**主循环 |
| B | 直接建旧运行时 → 换原生运行时构造器 |
| C 重写 | 提交层且碰上述六环节，没被覆盖 → 重写成主循环用例（多条合并成参数化用例） |
| C 删除 | 提交层且碰上述六环节，已被覆盖 → 删，写明被哪条覆盖 |
| D | 只建任务、读写存储 → 经产品组装建任务 |
| E | 不需要任务（纯函数、编解码、合同、存储迁移、读源码）→ 照常；"改 E"指现在借了任务、应改成直接测函数 |
| F | 只测要删的路 → 随删除删 |
| 随接缝迁移 | 用例只是 `subprocess` 调接缝脚本，随脚本迁移，调用方保留 |
| 待裁定 | 被测能力在产品路径上没有调用方或要用户定，见第四节 |

**A 类换芯难度**：低 = 种子经规划器、自带 `Orchestrator`；中 = 自带规划世界 `Env`、写死根任务号、手工确认/授权，要改成 `world_factory` + 脚本化规划器；高 = 种子里手工 `create_attempt` / 伪造结果或验收，产品同形世界里做不出来，换芯实为重写种子。

**覆盖引用简称**
- 【整圈】`tests/orchestrator/product_world/test_full_circle.py::test_a_user_mission_completes_on_the_product_deployment`
- 【子目标】`tests/orchestrator/product_world/test_sub_goal.py::test_a_sub_goal_is_planned_reviewed_and_completed_on_the_product_deployment`
- 【Host 通道】`backend/tests/orchestration/test_layered_scripted_lane.py::…`（10 条：整圈、返工、格式重做、格式上限、调用中取消、调用中重启、附资料、强杀续跑、幂等建任务、两任务并行）
- "换芯后"：引用的是第 2 步换芯成功后进入产品路径的 A 类用例。
- 【代表用例 3】方案第五节的"对外操作"代表用例，**还没写**；凡写"并入代表用例 3"的，都要等它写成后再删原用例。

---

## 〇、先说结论

1. **C 类比方案预想的大得多**：要重写的原用例 436 条，按机制合并后约 **95 个新主循环用例**，另有约 50 处补进已有用例的断言或参数；还有 79 条"待裁定"（被测能力产品走不到或计划里另有安排）。第 3 步工作量要按这个重估，单这一步就超出方案里整体的 6～9 天。
2. **建议删除 219 条**：C 删除 120（每条都给了覆盖用例，9 条例外见第六节），F 随删 99。另有约 **95 条**现在借任务测纯函数，应改成直接测函数（计在 E 里）。
3. **两个"共用构造器"换不了芯，只能整体重写**：`scripts/assurance_seams/_assured_fixture.py`（`AssuredRuntime` 是绑定了 `Orchestrator` 方法的假对象 + 旧运行时 + 手工伪造结果，15 个测试文件 + 7 个接缝脚本在用）和 `test_htn_end_to_end.build_world` 一族（`CommitService` + `HierarchicalDispatch(planning=env)` 手搭世界，31 个文件导入）。方案把 `_assured_fixture` 列在"主循环类构造器"里，这一点要改。
4. **A 类里约 27 条"高难度"**：机械上起了主循环，但种子是手工建尝试 / 伪造结果（`leaf_world.loop_leaf`、`_operation_world`、`test_scoped_content_commit._mixed_world` 等），换芯等于重写种子。
5. **共用的换芯难点**（第一节详列）：40 个文件写死 `task-root` / `obl-root` / `c-root`；测试要求书用 `root_review.root_requirements`（DERIVED，条件号取绑定名），产品是 `c-user-<n>`；测试规划世界是内存 `Env`（能凭空注入事实），产品是 `world_factory` 且 `observers=()`；`decision_loop.auto_grant` 在建意图的同一事务里签授权，产品在两轮之间签，轮次 / 序号断言要重核；审阅脚本从 `[role:critic]` + `<critic_verdict>` 换成保证通道审阅（无角色标记，归 `unknown`，`schema_version 2`）。
6. **子代理核实的关键事实**：旧根审阅员一整套（`RootReviewCoordinator.request()`、`record_review`、`record_unreadable`、`_child_review`、`ROOT_REVIEWER`）只被非保证段调用，属 F；保证通道终审走 `assurance_review_runtime.ensure_mission_final`。因此 `test_root_review_user_goal.py`（"终审员看得到用户原话、不露主机路径"）整文件随删——**保证通道终审是否也做了这两件事要另核，没做是产品缺口**。
7. **产品走不到的能力（第四节裁定，共 79 条）**：交付合同（`delivery_contract_ref` 在 src 里没有写入方）、撤销验收（src 没有撤销命令）、前提 / 观测 / 启动许可（产品 `observers=()`，阶段 D 才接）、只读子目标共享（阶段 D 才补）、整数图版本闸（待定③未定）、金额计价与两人审批（10-02 定不做、残留应删）、代码领域种子做法（产品 `domains=()`）、H6 做法评测（阶段 C3 已定删）。
8. **h1h / h1i**：建议删除 h1h 7 条，全部是"同一断言已在 h1i / h1h 主循环用例里"，标偏离，见第六节；h1i 不删。另有 9 条删除在覆盖目录里找不到逐条对应（元测试、夹具自检、产品不可达；其中 2 条有目录外的 E 类用例钉住），也列在第六节。
9. **接缝脚本**：17 个里只有 7 个被测试调用；另 10 个没有任何调用方（其中 5 个文档串一字不差），README 自称"诊断源码、不承诺皆绿"。用户定了"迁移"，但全迁是不必要的工作，建议向用户确认（第四节⑦）。

---

## 一、共用构造器清单

| 构造器 | 位置 | 谁在用（数量与代表） | 现在的世界 | 换芯难点 | 建议 |
|---|---|---|---|---|---|
| `_seed_new_protocol` / `_open_planner_round` / `_config` / `_refine_reply` | `full_target/test_h1i_production_entry.py` | 38（36 个测试与辅助：h1i 全组、h1h 主循环组、`production_fixture`、`leaf_world`、`test_v14_runtime_closure` 等；2 个等待期探针脚本要删） | 旧执行池；`install_hierarchical(planning=)`；代码领域真实世界（借 `test_real_provider_hierarchical_smoke._repo`）；根要求书用 `root_review.root_requirements`；人工确认完成映射；不装保证通道 | 要一个**代码领域的 `world_factory`**（产品世界是 `domains=()`）；`ROOT_TASK` 取自 e2e 写死；要求书换成 `user_requirements` 后单条件哈希会变；审阅脚本换成保证通道格式 | 换芯（中）。它是 h1h/h1i 门禁证据的底座，先换它、跑通 h1i 全组再动别的 |
| `enabled_world` / `ProductionWorld.commit_seed` | `taskgraph_exec/production_fixture.py`（+ `crash_seed.py` 子进程） | 14（taskgraph_exec 13 个测试 + crash_seed） | 在上一行种子上再手工签授权、`enable_taskgraph_contract`；已绑执行图但旧池、无保证通道 | "签授权后再启用"的时序随建任务时绑定消失，`test_production_seed` 等要改断言；`InstalledHtnWiringAcceptance()._read()` 要求已核验的安装源 | 换芯（中），直接建在 `product_world(world_factory=代码领域)` 上 |
| `assured_loop` / `approve_method_policies` / `run_until` | `assurance_exec/_assured_loop.py`（+ `_subgoal_world.py`） | 6（`test_method_review_gate`、`test_planner_chooses_and_proposes`、`test_planning_budget_one_ladder`、`test_subgoal_outputs`、`test_subgoal_planning`、`_subgoal_world`） | 真主循环 + 保证通道，但旧池、未绑执行图、`install_hierarchical(planning=env)`；自带 `plan.goal/plan.leaf` 世界和库内做法；根 `task-root` | 保证通道根安装命令 `"install"` vs 产品 `host-native-root`；做法计划策略命令号少两段（已漂移）；`auto_grant` 时序；库内做法要 `world_factory` 能登记（`product_world` 现在没有这个口） | 换芯（中）。给 `product_world` 加"登记库内做法"的口，或让 `world_factory` 顺带登记 |
| `deployment` / `spec` | `assurance_exec/_deploy.py` | 3（`test_assured_planner_unknown_bounded`、`test_c_assurance`、`test_v_assurance`） | 真主循环 + `install_assurance`；`select_profile` 对非 `assured` 键返回 None；不装规划世界；`commit.create_mission` 直接建 | "选不选保证通道"随删；`test_v_assurance._run` 建的 `legacy-v` 对照任务要去掉 | 换芯（低～中） |
| `auto_grant` / `decision_text` / `refine_step` / `content_critic_step` | `full_target/decision_loop.py` | 10 | 补丁 `_create_planner_intent_now`，同事务签授权；`content_critic_step` 产出旧 `<critic_verdict>` | 方案定删 `auto_grant`；`content_critic_step` 只服务旧审阅 | 回复构造并入 `testing/scripted_replies.py`；`auto_grant`、`content_critic_step` 删 |
| `AssuredRuntime` / `build_world` / `FixtureCommit` / `ReviewPump` | `scripts/assurance_seams/_assured_fixture.py` | 22（15 个测试文件：assurance_exec 12 个、`test_evidence_recheck`、`tests/agents/arp/test_arp_assurance_acceptance`；7 个接缝脚本） | 假编排器（`SimpleNamespace` 上绑 `Orchestrator` 方法）+ `build_agent_runtime` + `AllowAllAuthorization` + 补丁过的 `CommitService` + `_mixed_world` 手工伪造结果 | **换不了芯**：结果、范围、审阅都是手工伪造 | 整体退役；用户改为"产品同形世界跑到待审处 + 脚本化审阅员" |
| `build_world` / `committed` / `World` / `_accept_leaf` / `_stalled` / `_install` / `_unassembled` | `full_target/test_htn_end_to_end.py` | 31 | `CommitService` + `HierarchicalDispatch(planning=env)`；`apply_scripted_plan` 绕过规划器提交；`seed_verified_result` 伪造结果；写死 `task-root/obl-root/c-root/req-1` | **换不了芯**：提交与验收都绕过主循环；`_stalled` 靠 `demand=False` 造 NOT_SELECTED，产品会自动准入需求，造不出来 | 先给出产品同形等价物（规划器脚本提交 + 挂住执行者），31 个导入方分批改 |
| `leaf_world` / `loop_leaf` / `drive_to_running` | `full_target/leaf_world.py` | 16（p35 大部分、step02、step06、p32、p36、`knowledge_helpers`、`helpers_step07`、`tests/integration/test_protocol_failure_usage`） | 提交层：分层任务 + 手工提交计划，叶子可直接 `create_attempt/record_result`；`loop_leaf` 是真主循环但重试尝试仍手工建 | 文档串自己写明"产品同形世界里尝试只能由真实派发建"——**换不了芯** | 退役；用户改用 `product_world` + 可挂住 / 可注入故障的脚本化提供方 |
| `two_leaf_service` | `step04/knowledge_helpers.py` | 8（step04 5 个、gap_phase1 3 个） | 建在 `leaf_world` 上，手工建尝试、记带声明的结果 | 同上 | 退役，见第二节知识族 |
| `ledger_service` / `candidate` | `step07/helpers_step07.py` | 8（step07 4 个、h1h 4 个） | 建在 `leaf_world` 上，四个叶子 + 动作台账 | 候选挂在叶子任务上，而产品组装只建根 | 见第四节③"D′" |
| `materialized_file_publish` | `operation_completion/operation_runtime_fixture.py` | 8 | 手工物化一次文件发布操作（T0/T1） | 换不了芯 | 退役，由【代表用例 3】的种子取代 |
| `OperationWorld` | `assurance_exec/_operation_world.py` | 4 | 手工 T0/T1/T3 | 同上 | 同上 |
| `_world` / `_second_revision` / `_spec` | `full_target/test_plan_commits.py` | 19（12 个测试、7 个接缝脚本；含 h1h 7 个） | 裸 `CommitService` + `Env`，直接 `commit_plan_revision` | 产品提交只能经收集器（带执行图参与方和准入）；`base_graph_version=1`、`PlanPrincipal("manager-1")` 写死 | 退役；h1h 用户随之改（见子代理报告） |
| `_approval_world` / `_api` / `_command` | `operation_completion/test_completion_spec_approval.py` | 20 | 在 `_world` 上发布要求、确认完成映射 | 确认映射本身不在六环节里，可经产品组装（`auto=False`）做 | 改成产品组装版（D） |
| `_admitted_single_root` | `operation_completion/test_completion_plan_commit.py` | 8 | 手工准入并提交单根计划 | 同 `_world` | 退役 |
| `_mixed_world` | `operation_completion/test_scoped_content_commit.py` | 12（含 7 个接缝脚本） | MIXED 范围 + 手工准备验收 | 换不了芯 | 退役，由【代表用例 3】种子取代 |
| `apply_scripted_plan` / `approve_content_only_completion` / `seed_verified_result` | `full_target/scripted_plans.py` | 15 | 绕过规划器提交、人工确认、伪造结果 | 后两者只能在测试替身层用 | `plan_revision_proposal_step` 等回复构造并入 `scripted_replies`，其余删 |
| `Env` / `method` / `step` / `task_binding` | `fixtures/htn/htn_world.py` | 29（多数是 E 类规划 / 编译测试） | 内存规划世界，`say()` 能注入事实 | 给主循环用要一个 `Env → world_factory` 适配；E 类用户不动 | 保留；加适配 |
| `setup_runtime` / `ActualProvider` | `p35/test_provider_budget_guard.py` | 11（p35 全组） | `leaf_world` + 旧运行时 + 计价估算器 | 换不了芯 | 供方记账族统一改写（第二节、第四节②） |
| `_open_loop` | `full_target/test_service_intent_provider_blocker.py` | 3 | e2e 世界 + 旧池 + `auto_grant` | 同 e2e | 换芯（中） |
| `_both_lane_world` / `CodeWorld` / `REPOSITORY` | `full_target/test_htn_deployment_wiring.py`、`test_root_review_evidence.py` | 8 | 代码领域 + 观察器 + 脚本直接提交 | 依赖代码领域与观察器，产品没有 | 随第四节④⑥裁定 |
| `_binding` | `full_target/test_read_only_leaf_policy.py` | 3 | 纯构造语义绑定 | 无 | 不动 |

---

## 二、逐文件分诊表

（"分类"一栏是该文件各类的用例条数：A 换芯、B 换运行时、重写 = C 重写、删除 = C 删除、D、E、F、接缝 = 随接缝迁移、待裁定。大文件只列结论，逐用例在第三节。）

#### `tests/agents/arp/`（1 个文件，5 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_arp_assurance_acceptance.py` | 5 | 重写 5 | 重写 5 条 → 约 3 个主循环用例（评测任务经正式验收/根终审后技能准入；派发记录之前签发的验收/根结论不能准入；别的任务的根结论被拒） | 技能准入端口读保证通道台账；世界是 _assured_fixture 手工伪造结果。主循环侧 taskgraph_exec/test_worker_skills.py::test_a_skill_in_trial_is_used_by_the_worker_of_its_own_evaluation_mission 只覆盖试用派发，不覆盖“经正式验收才准入”。难点：产品同形世界的原生池要能挂技能（test_worker_skills 现用 production_fixture + arp_fixture） |

#### `tests/agents/`（24 个文件，143 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_agent_close.py` | 9 | B 9 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_agent_lifecycle.py` | 1 | B 1 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_agent_open_scope.py` | 5 | B 5 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_build_agent_runtime.py` | 8 | B 8 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_cancel_turn.py` | 9 | B 9 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_context_journal.py` | 12 | B 11 / F 1 | 11 条换原生运行时构造器；test_unknown_resume_reuses_the_frozen_request_without_a_new_selection 随旧召回删 | 该用例把探针塞进 runtime.kernel._ports.context._recall（旧 _RecallAdapter）；原生侧同一保护在 tests/agents/arp/test_arp_index_recall.py |
| `test_context_real_provider.py` | 1 | B 1 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_create_many_batches.py` | 9 | B 9 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_delegate_tool.py` | 17 | B 17 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_delegation_e2e_mock.py` | 1 | B 1 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_delegation_e2e_real_provider.py` | 1 | B 1 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_input_queue.py` | 8 | B 8 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_long_context_capacity.py` | 1 | B 1 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_provider_response_durability.py` | 5 | B 5 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_provider_wire.py` | 3 | B 3 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_recall_untrusted_frame.py` | 4 | F 4 | 随旧召回适配器删 | 只测 _RecallAdapter 的框定 |
| `test_same_runtime_lease_recovery.py` | 1 | B 1 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_session_memory.py` | 12 | F 12 | 随旧检索删 | 只测旧 SessionRetriever / 旧索引泵 / 旧 SessionHistoryTools |
| `test_session_memory_real_embedding.py` | 1 | F 1 | 随旧检索删 | 旧检索真嵌入 |
| `test_slice5_recovery.py` | 7 | B 7 | 换原生运行时构造器；test_history_queries_are_bounded_tool_calls 换后核工具名 | 该条用旧 SessionHistoryTools 的 session_history_search；原生历史工具见 tests/agents/arp/test_arp_history_tools.py，名字或计次口径不同就改断言，不新增 |
| `test_slice5_review.py` | 6 | B 6 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_tool_output_length_recovery.py` | 9 | B 9 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_turn_limits.py` | 7 | B 7 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |
| `test_turn_resume_identity.py` | 6 | B 6 | 换原生运行时构造器 | 直接调 build_agent_runtime；原生池内部仍调它，只是上下文与历史工具换成原生那套 |

#### `tests/execution/`（1 个文件，5 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_execution_v9_to_v10_migration.py` | 5 | B 5 | 换原生运行时构造器 | 直接调 build_agent_runtime |

#### `tests/integration/`（1 个文件，3 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_protocol_failure_usage.py` | 3 | 重写 3 | 并入“供方记账族”重写（见第四节②）：协议错误只留有效用量、不重采样、不调工具 → 1 个产品同形用例（执行者回合遇协议错误） | leaf_world 手工 create_attempt；断言里有计价（amount_micros、estimator_digest），产品不配价格表，计价部分见第四节② |

#### `tests/orchestrator/full_target/assurance_exec/`（30 个文件，114 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `_assured_loop.py` | 0 | — | 见第一节 | 辅助模块 |
| `_deploy.py` | 0 | — | 见第一节 | 辅助模块 |
| `_method_plan_world.py`（清点：不受影响） | 0 | — | 见第一节 | 辅助模块 |
| `_operation_world.py` | 0 | — | 见第一节 | 辅助模块 |
| `_subgoal_world.py` | 0 | — | 见第一节 | 辅助模块 |
| `test_a_assurance.py` | 18 | 重写 10 / 删除 1 / E 4 / 接缝 3 | E 4（strict_boundary、formula_truth_tables、any_branch_not_mandatory、review_new_round_identity）；随接缝迁移 3（check_receipt_scope、extra_evidence_exposure、check_budget_and_format_bounds 只调接缝脚本）；删 1：pending_effect_no_worker_loop；重写 10 → 约 5 个：审阅派发原子+审阅员独立+来源绑定 1 个（篡改型）、scope_pinned_not_latest 1 个、requirement_authority 1 个、composition_not_all_children 1 个、preparation_does_not_complete / preparation_data_not_order / root_requirement_effect_catalogue / direct_final_commit_guard 并入代表用例 3 的变体 | 运行时用例走 AssuredRuntime（拼出来的假编排器 + 旧运行时 + 手工伪造结果），换不了芯。删除那条被 operation_completion/test_completion_pending_stall.py::test_prepared_mixed_effect_pending_skips_idle_stall_and_stop 覆盖，但那条自身种子是手工伪造（高），须先随代表用例 3 重写种子 |
| `test_assured_planner_unknown_bounded.py` | 2 | A 2 | 保留（换芯，低） | _deploy.deployment；任务键都以 assured 开头 |
| `test_c07_host_api.py` | 2 | A 1 / E 1 | 1 条换芯（低），1 条纯合同校验保留 | 自带 _deployment，select_profile 对非 assured 键返回 None —— 换芯时这一支随删 |
| `test_c_assurance.py` | 6 | A 1 / 重写 1 / E 1 / 接缝 3 | two_connection_concurrency 换芯（低）；review_cold_resume、event_cursor_atomicity、closeout_and_notification 调接缝脚本，随接缝迁移；acceptance_atomic_faults 重写 1（在写验收的各个点注入故障，产品同形世界里整体回滚）；real_migration_and_legacy 是库升级，E | acceptance_atomic_faults 走 AssuredRuntime；主循环侧 taskgraph_exec/test_commit_atomicity.py 只管计划提交的原子性，不管验收 |
| `test_c_integration.py` | 1 | 重写 1 | 重写，并入 test_review_turn_retry_e2e 的重写用例（审阅员调用用量晚到，不改审阅结论与生命周期） | AssuredRuntime |
| `test_check_policy_lossless_mapping.py` | 7 | 重写 3 / 删除 2 / E 2 | 删 2：spells_out_the_original_and_its_approval_succeeds、mission_final_mapping_covers_the_whole_root_requirements（product_world/test_full_circle.py::test_a_user_mission_completes_on_the_product_deployment 不批准内容与终审策略就到不了 COMPLETED；Host 侧 test_assurance_host_api.py::test_check_policy_projection_is_replay_safe_and_needs_a_frozen_scope）；E 2：unknown_scope_is_unresolved、unknown_purpose_is_refused；重写 3：host_auto_approval_is_recorded_as_system（给整圈加一条“批准来源=HOST_LOSSLESS_AUTO”断言即可，不新增用例）、两条操作审阅投影并入代表用例 3 | _assured_fixture.build_world 手工伪造冻结范围 |
| `test_check_policy_projector_port.py` | 2 | A 2 | 保留（换芯，低） | 自带 Orchestrator + install_assurance；第 2 条“没有投影器时只认人工批准”在部署组装里投影器恒装，换芯后改成直接装保证通道不带投影器（不经部署），仍是主循环 |
| `test_disclosure_batch_message_order.py` | 1 | 重写 1 | 重写，并入证据工具接缝迁移后的那条（无序的工具结果消息号记录并重放） | _assured_fixture.build_world |
| `test_e_assurance.py` | 8 | 重写 7 / F 1 | 重写 7（E01～E07）→ 并入代表用例 3 + 约 3 个变体（同一回执多效果、结果身份各轴、晚到事实/新要求）；test_content_legacy_compatibility 随“非保证通道”删 | _operation_world 手工 T0/T1/T3；E08 对照的是 legacy（非保证通道）任务 |
| `test_evidence_recheck.py` | 3 | A 1 / 重写 2 | the_planning_loop_records... 换芯（中）；另 2 条重写成 1 个产品同形用例（证据变了→证书过时→收尾拒绝直到规划器处理） | 两条走 AssuredRuntime；没有主循环覆盖 |
| `test_fixed_authority_access_key_per_source.py` | 1 | A 1 | 保留（换芯，低） | 自带 Orchestrator + install_assurance |
| `test_method_plan_policy_projection.py` | 2 | 删除 1 / D 1 | 删 1：the_planning_subject_mapping_is_lossless_and_its_approval_opens_the_method_review（product_world/test_sub_goal.py::test_a_sub_goal_is_planned_reviewed_and_completed_on_the_product_deployment 断言子目标的 method-plan 策略已投影且子目标做法过了独立审阅；整圈要过根的做法审阅）；D 1：a_task_that_is_not_a_planning_subject_has_no_mapping（经产品组装建任务，只看根以外的绑定，不提交） | AssuredRuntime + _method_plan_world |
| `test_method_review_gate.py` | 6 | A 6 | 保留（换芯，中） | _assured_loop；自带 plan.goal/plan.leaf 世界与 ROOT_TASK |
| `test_mission_final_sees_root_effects.py` | 1 | 重写 1 | 重写，并入代表用例 3：断言终审包里有根自己的已验收操作效果与回读 | _operation_world 手工伪造 |
| `test_planner_chooses_and_proposes.py` | 6 | A 6 | 保留（换芯，中） | _assured_loop + decision_loop |
| `test_planning_budget_one_ladder.py` | 3 | A 3 | 保留（换芯，中） | _assured_loop（2 条主循环 + 1 条读默认配置，一起换） |
| `test_review_second_opinion.py` | 4 | 重写 4 | 重写 4 → 2 个产品同形用例：①第一次判不下→换会话复审→仍判不下→问人→通过才准验收（并断言判得下时只调一次审阅）；②人判不通过不准验收 | AssuredRuntime。主循环侧 assurance_exec/test_method_review_gate.py::test_two_inconclusive_method_reviews_ask_the_person_and_the_ruling_decides 是做法审阅那一支，内容验收的“许可”一半没有覆盖 |
| `test_review_turn_retry_e2e.py` | 4 | 重写 4 | 重写 4 → 2 个（参数化：TURN_FAILED / 服务端错误 → 重试一次后导入；第二次也失败 → 用尽不调第三次；另 1 个：重启打断审阅 → 未核对、保持预留） | AssuredRuntime；Host 的 backend/tests/orchestration/test_layered_scripted_lane.py::test_a_restart_during_a_model_call_resumes_and_completes 断在执行者调用上，不是审阅员 |
| `test_root_review_adjudication.py` | 2 | 重写 2 | 重写 2 → 1 个参数化（终审两次判不下→问人：通过则根结论成立 / 不通过则进修复） | AssuredRuntime。test_adjudication_question_never_replans.py 只管“回答不开规划轮”，不管根结论 |
| `test_root_review_rework_is_a_generic_request.py` | 3 | 重写 3 | 重写 3 → 2 个（打回→恰一条通用修复请求且不碰别的；按任务计上限、用尽具名停 + 不许修复的任务不出请求 合一个参数化） | AssuredRuntime。test_repair_request_failure_facts.py::test_a_final_review_request_is_about_every_step_so_a_change_on_any_step_answers_it 只管请求范围 |
| `test_settlement_held_not_fatal.py` | 1 | 重写 1 | 重写，并入供方记账族的产品同形用例（执行者用量未知 → 尝试保留预留、任务不判失败） | AssuredRuntime |
| `test_stateful_assurance.py` | 1 | 重写 1 | 重写成产品同形的小号随机用例（对跑到待审的叶子随机施加审阅回复/重启/取消，查不变式；步数按 09-23 定的小号版） | AssuredRuntime；随机序列现在直接调提交层 |
| `test_subgoal_outputs.py` | 2 | A 2 | 保留（换芯，中） | _assured_loop + _subgoal_world（1 条只组世界，随文件换） |
| `test_subgoal_planning.py` | 12 | A 12 | 保留（换芯，中） | _assured_loop + _subgoal_world |
| `test_v_assurance.py` | 16 | A 6 / E 9 / 接缝 1 | E 9（有效性求值器、证书绑定的纯函数）；6 条经 _run（_deploy.deployment）的换芯（中）；restore_quarantine_and_current_reauthorization 调 root-gate 接缝，随接缝迁移 | _run 里另建一条“没选保证通道”的对照任务（legacy-v）并断言它不在保证通道——这一半随“选不选保证通道”删，换芯时去掉对照任务与相应断言 |

#### `tests/orchestrator/full_target/`（107 个文件，1397 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `decision_loop.py` | 0 | — | 见第一节 | 辅助模块 |
| `leaf_world.py` | 0 | — | 见第一节 | 辅助模块 |
| `test_adjudication_question_never_replans.py` | 1 | A 1 | 保留（换芯，中） | h1i 种子 |
| `test_after_handoff_unknown_bounded.py` | 5 | A 4 / E 1 | 4 条换芯（中）；test_the_consecutive_bound_is_the_p23f_retry_width 是常量核对，E | _open_loop + e2e.build_world + decision_loop.auto_grant；auto_grant 换成部署职责后，规划轮次/序号断言要按“两轮之间代签”的时序重核 |
| `test_appworld_hierarchical_worker.py` | 5 | A 3 / E 2 | 3 条换芯（中，需 AppWorld 领域的 world_factory）；2 条领域表/模板登记核对 E | AppWorld 底层零件（领域档案与模板）按用户 10-03 决定保留；分层臂删了，这里测的是领域执行者模板，不是评测臂 |
| `test_business_replay_inventory.py` | 3 | 重写 1 / E 2 | 2 条（表/字段分类守卫）E；test_the_skeleton_reports_coverage_honestly_for_one_mission 改为在整圈跑完的任务上出覆盖报告（并进整圈用例或其副本，不新增主循环用例） | 只借 e2e.committed 造一个有数据的任务 |
| `test_composition_review_consumer.py` | 3 | A 2 / E 1 | 2 条换芯（中）；test_the_action_on_an_official_composition_record 是纯判断函数，E | h1i 种子 |
| `test_compound_gate_no_pseudo_cycle.py`（清点：不受影响） | 53 | E 53 | 不动（纯规划/编译） | 清点：不受影响 |
| `test_criteria_driven_write_step.py` | 6 | E 6 | 保留（纯函数：覆盖层、做法受理、提示词） | 不需要任务；用代码领域做法当素材 |
| `test_domain_schema_gate.py` | 4 | A 3 / E 1 | 3 条换芯（低）；档案往返 E | 自带 Orchestrator |
| `test_finalizer_output_ports.py` | 12 | 重写 7 / 删除 5 | 删 5：finalizer_step_is_a_criterion_linked_occurrence / criterion_linked_finalizer_declares_the_port / finalizer_leaf_is_told_about_its_declared_output_port / finalizer_that_claims_its_port_is_accepted_and_indexed（product_world/test_full_circle.py::test_a_user_mission_completes_on_the_product_deployment：脚本化执行者按请求里的声明端口认领，收尾步不声明或认领不上就验收不了、到不了 COMPLETED）、finalizer_that_claims_no_port_is_refused（test_unclaimed_port_is_a_rejected_result.py::test_an_unclaimed_declared_port_is_refused_as_a_result_and_the_loop_survives，换芯后）；重写 7（三个读者同答案、消费端口带边的模式、既不消费也不链接、完成协议下自带端口、链接到非收尾步、可选端口不欠、按发生号匹配绑定）→ 约 2 个：脚本化规划器提几种做法形状、跑到计划提交，再断言三个读者的端口集合（参数化） | e2e.build_world 手工提交计划；这些断言只需要已提交的计划，不需要尝试 |
| `test_fixed_task_allowance_config.py` | 2 | A 1 / E 1 | 1 条换芯（低）；1 条配置 E |  |
| `test_flat_mode_removed.py` | 3 | A 2 / E 1 | 保留：2 条换芯（低）；规格只写一种模式 E | 平面入口拒绝与遗留平面任务按名停，是产品仍要守的门 |
| `test_h1h_action_handoff.py` | 5 | 重写 5 | 重写 5 → 约 2 个（并入代表用例 3 的变体：篡改/删除标记与链接、桥接身份错 → 不交接；O06 弱核对/空查询重交接不碰连接器，保持新协议的扣留） | helpers_step07 + operation_runtime_fixture 手工物化操作；h1h 门禁证据，不删 |
| `test_h1h_authority_matrix.py` | 9 | A 3 / 重写 6 | 3 条换芯（中）；a02×4、a03、a04 共 6 条重写 → 约 2 个（参数化：授权表读不了/JSON 坏/悬空授权/从未授权 → 具名拒绝且不写；主体或范围不符 + 授权变更后旧回复过期） | 6 条用 test_plan_commits._world 直接调计划提交；h1h 门禁证据，不删 |
| `test_h1h_authority_throughput.py` | 1 | A 1 | 保留（换芯，中） | h1i 种子 |
| `test_h1h_authority_vs_action_approval.py` | 3 | A 2 / 重写 1 | 2 条换芯（中）；a06 未批准的外部写入不能交接 → 并入代表用例 3 的“未批准不交接”变体 | ledger_service 手工候选 |
| `test_h1h_commit_guard.py` | 6 | 重写 4 / 删除 2 | 偏离：删 2——a08 撤销后重放回原回执（taskgraph_exec/test_commit_atomicity.py::test_committed_reply_replay_after_revoke_is_readonly_and_changed_bytes_conflict 与 test_h1i_decision_replay.py::test_committed_refine_replays_after_grant_revocation_without_new_mutation，换芯后，断言相同）、i06 预览到提交之间授权变更即过期（test_h1h_authority_matrix.py::test_a04_reply_bound_before_grant_change_is_stale_and_never_rebound，本表重写后的参数化用例）；重写 4（a05 过期授权在提交内复核并回滚、o08 预览后新动作、o03 退役未知动作挡提交、预览读集身份不符）→ 约 2 个 | test_plan_commits._world 直接提交；删除理由：同一断言在 h1i/h1h 主循环用例里已有，门禁证据不减 |
| `test_h1h_commit_interleaving.py` | 1 | 重写 1 | 重写 1（主循环里收集器提交计划时，另一连接持写锁写授权/动作，计划提交判过期） | 真实 BEGIN IMMEDIATE 竞争；h1h，不删 |
| `test_h1h_missing_assembly_planning.py` | 1 | A 1 | 保留（低） | 测“没装分层装配就不发规划”；共享世界安装口删了，但“主循环起来时没装部署”仍可能（只读命令行），守卫保留 |
| `test_h1h_no_nanojev_process.py` | 1 | A 1 | 保留（中，子进程里跑 h1i 种子，随种子换芯） |  |
| `test_h1h_nonmutating_collect.py` | 2 | A 2 | 保留（换芯，中） | h1i 种子 + 等待生命周期辅助 |
| `test_h1h_operation_alias.py` | 5 | D 5 | D：只建任务并读写动作台账，不进提交/尝试/审阅；但候选要挂在叶子任务上 → 需“D′”（见第四节③），否则重写 | ledger_service（leaf_world 四个叶子）；h1h，不删 |
| `test_h1h_operation_current_gates.py` | 5 | 重写 4 / E 1 | 1 条 E；4 条（O08 交接后预览快照作废、A06 规划授权与动作批准独立、O04 外租户拿不到规划授权、O09 物化回滚后重放一次）→ 并入代表用例 3 的约 2 个变体 | operation_runtime_fixture；h1h，不删 |
| `test_h1h_operation_live_boundaries.py` | 2 | 重写 2 | 重写 2 → 1 个（并入代表用例 3：T0/T1 成功回执被应用、错回执被拒；动作与链接一起回滚后同命令只重放一次） | operation_runtime_fixture；h1h |
| `test_h1h_operation_matrix.py`（清点：不受影响） | 3 | D 3 | D：CommitService 建任务行，只测编译；第 4 步若在建任务入口强制“分层任务经部署建”，改经产品组装 | 清点：不受影响 |
| `test_h1h_operation_tenant.py`（清点：不受影响） | 2 | D 2 | 同上 | 清点：不受影响 |
| `test_h1h_operation_two_real_producers.py` | 1 | 重写 1 | 重写，并入代表用例 3 的“两个 T0 生产者同一目标不串链接”变体 | h1h |
| `test_h1h_p02_compiler_cycles.py` | 1 | A 1 | 保留（换芯，中） | 清点归提交层，实际经 test_h1h_preview_compiler_refusal 的 Orchestrator 预览 |
| `test_h1h_p03_compiler_data_coverage_resources.py` | 1 | A 1 | 保留（换芯，中） | 同上 |
| `test_h1h_preview_compiler_refusal.py` | 7 | A 6 / E 1 | 6 条换芯（中）；plan_admission_keeps_exact_typed_preview_code E |  |
| `test_h1h_preview_purity.py` | 1 | A 1 | 保留（换芯，中） |  |
| `test_h1h_request_binding.py` | 2 | 删除 2 | 偏离：删 2——请求快照=封好的包与提示词（test_h1i_production_entry.py::test_the_planning_request_is_the_package_and_nothing_appended，换芯后）；读不懂的回复写新旧两种拒绝事件（test_h1i_raw_artifact_collector.py::test_malformed_decode_only_reply_is_unreadable_but_preserves_exact_raw_artifact 与 test_v14_runtime_closure.py::test_v14_malformed_planning_reply_records_unreadable_without_secondary_failure，换芯后） | object.__new__(Orchestrator) 假编排器；删前须核对两条 h1i 用例确实断言了“新旧拒绝账都写”，若只断言其一，改为给 h1i 用例补一条断言 |
| `test_h1h_retired_method_unknown_action.py` | 1 | 重写 1 | 重写，并入代表用例 3 变体（退役分支上的未知操作动作仍可见并挡提交） | h1h |
| `test_h1h_retired_running_work.py` | 1 | 重写 1 | 重写 1（取消的任务上外来的活跃尝试仍挡真实提交） | test_plan_commits._world；h1h |
| `test_h1h_scoped_critic_policy.py` | 4 | A 3 / E 1 | 3 条换芯（中）；suspended_method_is_not_offered E | 第 1 条“计划提交为只读内容安排独立审阅”在保证通道下是做法审阅/内容审阅，换芯后核断言里的审阅种类 |
| `test_h1h_wiring.py` | 12 | 重写 7 / 删除 3 / E 2 | 偏离：删 3——格式重试带原请求号（test_h1i_production_entry.py::test_format_retry_keeps_opener_package_and_request_identity，断言请求行 = (开启者, 重试) 且请求事实不变）、意图号列移到作答意图（::test_malformed_decision_retry_reuses_the_frozen_request_package，断言 request_after[4] != opener）、只许重问一次（test_planning_request_retry_scope.py::test_later_request_retries_its_own_frozen_package_once），均换芯后；重写 7（答在开启者请求的第 1 序号、非格式拒绝不复用请求号、读不懂拒绝报告剩余重试、第一轮不继承身份、重试不许改别的、没绑请求的决定轮失败关闭、准入上下文报本轮剩余格式重试）→ 约 2 个；E 2（HTTP 字段经 spec_from_request 进规格与绑定、不可配对的包版本被拒） | object.__new__(Orchestrator) 假编排器 + CommitService 建任务；删的 3 条已核对 h1i 用例断言同一件事 |
| `test_h1i_commit_recovery.py` | 3 | A 3 | 保留（换芯，中） | h1i 种子 |
| `test_h1i_decision_replay.py` | 2 | A 2 | 保留（换芯，中） | h1i 种子 |
| `test_h1i_deferred_repair_resume.py` | 1 | A 1 | 保留（换芯，高：种子里手工 create_attempt，要改成真实派发） | h1i 种子 |
| `test_h1i_preview_recovery.py` | 1 | A 1 | 保留（换芯，中） | h1i 种子 |
| `test_h1i_production_entry.py` | 7 | A 7 | 保留（换芯，中） | h1i 种子 _seed_new_protocol：代码领域真实世界 + install_hierarchical(planning=) + root_review.root_requirements + 手工 begin_planning； |
| `test_h1i_raw_artifact_collector.py` | 3 | A 3 | 保留（换芯，中） | h1i 种子 |
| `test_h1i_wait_lifecycle.py` | 15 | A 15 | 保留（换芯，中），列入改坏名单 | 方案定：替代两个等待期真实模型探针；h1i 种子 |
| `test_h1i_wait_snapshot.py` | 1 | A 1 | 保留（换芯，中） | h1i 种子 |
| `test_h3_method_selection.py` | 4 | 删除 1 / E 3 | E 3（请求证据是可用决定、证据要求登记的观察器与授权、问题上限与去重）；删 1：real_hierarchical_dispatch_lists_zero_one_and_many_candidates（assurance_exec/test_planner_chooses_and_proposes.py::test_one_candidate_is_still_the_planners_choice_and_a_library_method_needs_no_review，换芯后，断言候选清单由派发给出且一个候选也交规划器选） | 删前核对换芯后的那条是否也覆盖“零候选/多候选”两档；不覆盖就把两档加成该用例的参数 |
| `test_h4_graph_repair_commits.py` | 8 | 重写 7 / 删除 1 | 删 1：propose_successor_commits_a_fresh_task_with_the_same_obligation_and_budget（taskgraph_exec/test_successor_with_taskgraph.py::test_a_successor_for_a_leaf_commits_a_second_plan_revision_with_the_taskgraph_on，换芯后）；重写 7（后继不能转走原义务、改绑输入拒旧要求哈希、取消必需子分支被拒、已验收下游不许改写、改绑到另一声明生产者并冷读、两消费者共享活跃目标、取消可选分支只放它的需求）→ 约 3 个（脚本化规划器发修复决定，参数化） | test_plan_commits._world 直接提交 |
| `test_h4_package7_entry.py` | 1 | A 1 | 保留（换芯，中） |  |
| `test_h4_repair_adapter.py` | 8 | A 1 / 删除 1 / E 6 | E 6（触发来源分派、未知操作提交前延后、请人未授权仍挡、未知事件失败关闭、具名辅助覆盖修复路径——纯函数）；event_handler_runtime_trigger_persists_h4_audit 换芯（中）；删 1：hierarchical_dispatch_exposes_the_h4_event_boundary（test_h4_retry_runtime_entry.py::test_runtime_wakes_fence_stale_planner_and_repeated_source_transitions，换芯后，经同一边界入库） | object.__new__(Orchestrator) + e2e.build_world |
| `test_h4_retained_completion.py` | 2 | 重写 2 | 重写 2 → 1 个（修复后保留真实验收内容；脏了的复用钉不能提交、当前结论复用可提交并冷读） |  |
| `test_h4_retry_runtime_entry.py` | 6 | A 6 | 保留（换芯，高：手工 create_attempt 造超时/阻塞尝试，改由真实派发 + 脚本化执行者造） | h1i 种子 |
| `test_h6_oracle_bound_evaluation.py` | 4 | 待裁定 4 | 待裁定（第四节⑤）：测 H6 做法评测的预言回执（MethodEvaluationStore）。HTN 补齐计划阶段 C3 已定“评测晋级连同表、脚本删掉”；现在重写是不必要的测试，建议 A′ 提前删（偏离：提前随 C3 删） | e2e.build_world 手工伪造完成 |
| `test_h6_source_recovery.py` | 1 | F 1 | 随评测模块删 | 只测 evaluation/htn_method_source（H6 评测，只为评测服务，孤儿扫描范围内） |
| `test_h7_real_solver_commit.py` | 1 | A 1 | 保留（换芯，中） |  |
| `test_h8_formal_root_completion.py` | 2 | F 2 | 随评测分层臂删 | 导入 evaluation/htn_hierarchical.read_formal_root_completion 与 htn_matrix 回执谓词 |
| `test_hierarchical_event_flow.py` | 58 | A 4 / 重写 27 / 删除 20 / D 2 / E 4 / F 1 | A 4（事件处理器分支）；重写 27（并入 R1/R2/R3/R5，另 10 条补进本文件与 e2e 的 A 类用例）；删 20（含 2 条变异元测试无目录覆盖）；D 2；E 4；F 1（无规划世界）（逐用例表见第三节） |  |
| `test_hierarchical_judgment_tree.py` | 2 | F 2 | 随 _judge 旧合并树删 | 清点已判只测旧路 |
| `test_htn_and_or_shared_goal.py`（清点：不受影响） | 33 | E 33 | 不动 | 清点：不受影响 |
| `test_htn_deployment_wiring.py` | 42 | 删除 2 / D 19 / E 19 / 待裁定 2 | E 19；D 19（证据轮/做法上下文，只借任务号与存储）；删 2（被 test_planner_chooses_and_proposes 的做法上下文用例覆盖）；待裁定 2（第 6 节两条 START 通道：产品 observers=() 走不到）（逐用例表见第三节） |  |
| `test_htn_end_to_end.py` | 187 | A 13 / 重写 52 / 删除 31 / D 1 / E 51 / F 5 / 待裁定 34 | A 13（§13 一条、F11/M17 直接入口两条、§18 停滞组 10 条）；重写 52 → 新写 R1 预算守恒 / R2 被拒计划不留痕 / R3 提交后读侧 / R5 根结论前置与原因码 / R6 完成判定四种拒绝 / R11 验收重放（与 event_flow 合用），另 15 条补进已有用例；待裁定 34（R7 交付回执 6、R8 撤销后读侧 3、R10 前提/观测/启动许可 14、R14 只读子目标共享 11）；删 31（含 2 条无目录覆盖）；E 51；F 5（_unassembled 4 + 无规划世界 1）（逐用例表见第三节） | build_world / committed 是 31 个文件的公共构造器 |
| `test_htn_grounding_partial_order.py`（清点：不受影响） | 67 | E 67 | 不动 | 清点：不受影响 |
| `test_htn_novel_method_admission.py`（清点：不受影响） | 51 | E 51 | 不动 | 清点：不受影响 |
| `test_input_manifest_same_source.py` | 6 | 重写 5 / 删除 1 | 删 1：consumer_with_no_input_is_admitted_with_the_empty_manifest（product_world/test_full_circle.py::test_a_user_mission_completes_on_the_product_deployment：一步做法的唯一叶子无输入，准入不了就到不了 COMPLETED）；重写 5（准入不再解析第二次、清单不一致被拒、就绪报告缺解析被拒、必需端口无边不是空成功、就绪后生产者被撤销则不准入）→ 约 2 个（篡改型，参数化） | e2e.build_world + _accept_leaf 手工验收 |
| `test_inspect_leaf_patch_input.py` | 6 | E 3 / 待裁定 3 | E 3（v1 行保字节、v2 端口可选、操作者每类型只列最新版）；3 条（合成做法不给 inspect 叶子任何东西 / 绑到 patch 端口就等并收到 / summarize 拿到发现与验证报告）待裁定：只测代码领域种子做法（第四节④） | deployment_wiring 世界 + committed 手工提交与验收 |
| `test_leaf_owns_linked_criteria.py` | 5 | E 5 | 保留（纯函数 occurrence_criteria / owned_criteria / scoped_goal） | 清点误归提交层 |
| `test_lease_lost_is_redone.py` | 2 | A 2 | 保留（换芯，高：loop_leaf 手工造尝试，改由真实派发 + 被杀的执行者造租约丢失） |  |
| `test_method_proposals.py`（清点：不受影响） | 42 | E 42 | 不动 | 清点：不受影响 |
| `test_nested_compound_composition.py` | 5 | 重写 3 / 删除 2 | 删 2：linked_assess_by_reading_still_forms_a_resolution（product_world/test_sub_goal.py::test_a_sub_goal_is_planned_reviewed_and_completed_on_the_product_deployment：子目标的组合审阅通过形成目标结论，否则根终审到不了）、the_fixture_really_parks_the_successor_on_waiting_order（夹具自检，随夹具消失）；重写 3（缺链接的叶子准则不填 PASS、共享义务下不混兄弟验收、c-composition 无覆盖不形成接受）→ 约 2 个 | 清点归主循环，逐用例看 5 条都是 e2e 手工提交 + _accept_leaf |
| `test_nested_compound_refinement.py` | 8 | A 7 / 删除 1 | 7 条换芯（中）；删 1：the_committed_plan_really_holds_an_unrefined_compound（夹具自检；换芯后 an_unrefined_nested_compound_reopens_the_planner 依赖同一前提，前提不成立它就失败） |  |
| `test_output_port_claims.py` | 18 | A 2 / E 16 | 2 条换芯（中）；16 条端口认领解析/模板字节/提示词 E |  |
| `test_plan_commits.py` | 100 | 重写 42 / 删除 16 / D 2 / E 32 / 待裁定 8 | 重写 42 → 约 4 个（RW-P1 封包后提交前状态变化/重放 8 档、RW-P2 篡改编译产物或未哈希字段约 15 档、RW-P3 运行中替换并入 test_successor_with_taskgraph、RW-P4 首次提交账本），另扩 test_commit_atomicity；删 16；E 32（含改 E 30：授权纯函数、读集检查器参数化、快照合同；其中管理纪元 2 条待定）；待裁定 8（整数图版本闸 7 + 自动变基变异 1：子代理判平面残留，但 HTN 补齐计划待定③“真正写上还是由计划修订号取代”未定，建议先改 E 暂留）（逐用例表见第三节） | 整数图版本在 src 只读不写（平面残留）；管理纪元 bump_epoch 在 src 无调用方 |
| `test_planner_package_refs_and_fields.py` | 73 | 删除 1 / D 16 / E 56 | E 56（含改 E 3）；D 16（经产品组装建任务后直接组包，合并后约 5 条）；删 1（逐用例表见第三节） |  |
| `test_planner_package_single_layer.py` | 9 | A 2 / E 7 | 2 条换芯（中）；7 条组包上限/裁剪纯函数 E |  |
| `test_planner_rounds_in_flight.py` | 2 | E 2 | 保留；test_only_planner_and_root_review_rounds_count 里的 "root_reviewer" 角色名随旧根审阅员删，改成保证通道终审回合的标识（只改断言） | 假对象 + 存储 |
| `test_planning_decision_enablement_contract.py` | 7 | A 2 / E 5 | 2 条换芯（中）；5 条 E |  |
| `test_planning_protocol_switch.py` | 14 | A 1 / D 6 / E 7 | 1 条换芯（低）；E 7（默认值、解析器默认、删掉的协议名、未知值、环境不影响、策略摘要不含协议）；D 6（建任务写绑定、失败回滚、重放幂等、篡改冲突、新连接仍在、缺/旧绑定报不支持）改经产品组装建任务 |  |
| `test_planning_request_retry_scope.py` | 3 | A 3 | 保留（换芯，中；其中 1 条种子手工 create_attempt，高） |  |
| `test_progress_acceptance_2b.py` | 4 | A 3 / 删除 1 | 3 条换芯（中）；删 1：abc_fixture_really_has_one_data_edge_and_one_pure_order_edge（夹具自检，改成换芯后种子里的前提断言，不单列用例） |  |
| `test_progress_acceptance_2b_repair.py` | 3 | A 2 / 重写 1 | 2 条换芯（高：_operation_world 手工种子）；settling_an_unknown_charge_at_its_upper_bound... 并入代表用例 3 的“未知费用按上限结清不动 UNKNOWN 动作”变体 |  |
| `test_provider_grant_rehandoff.py` | 4 | A 4 | 保留（换芯，中） | e2e + decision_loop.auto_grant |
| `test_read_only_leaf_policy.py` | 13 | A 2 / E 9 / 待裁定 2 | 2 条换芯（中）；E 9；the_c3_plans_read_only_leaves_are_materialised_without_code_test / the_task_committed_proposal_says_the_same 待裁定（代码领域 C3 做法，第四节④） |  |
| `test_read_only_leaf_write_guard.py` | 14 | A 3 / E 11 | 3 条换芯（中）；11 条写守卫/收集器纯函数 E |  |
| `test_read_only_rewrite_bound.py` | 2 | E 2 | 保留（纯函数） | 清点归主循环，实际不起主循环 |
| `test_real_provider_hierarchical_smoke.py` | 1 | A 1 | 保留（换芯，中；需 --run-real-provider 才跑）。清点按文本计 3 条，另两条是写进临时仓库的字符串 | 它的 _repo / 代码领域世界是 h1i 种子的来源，换芯时一起改 |
| `test_repair_request_failure_facts.py` | 4 | A 3 / E 1 | 3 条换芯（2 条高：手工 create_attempt）；指纹 E |  |
| `test_repeated_failure_early_stop.py` | 7 | E 7 | 保留（指纹与 diff 应用纯函数） | 清点归主循环，实际不起主循环 |
| `test_repeated_planner_question.py` | 2 | A 2 | 保留（换芯，中） |  |
| `test_resolution_commits.py` | 108 | 重写 59 / 删除 2 / E 33 / 待裁定 14 | 重写 59 → 约 5 个（RW-A1+R1 提交入口篡改、RW-A2 审阅中状态变化、RW-R2 根触发器读后状态变化、RW-L+A3 账本与重放、RW-A4 原子性）；待裁定 14（RW-R3 交付闸门：要求书的 delivery_contract_ref 在 src 里没有写入方，产品走不到）；删 2；E 33（含改 E 26：授权、读集检查器、命令合同、acceptable() 纯函数）（逐用例表见第三节） |  |
| `test_retry_after_regeneration.py` | 1 | A 1 | 保留（换芯，高） |  |
| `test_revoked_generation_cleared.py` | 1 | 重写 1 | 重写，并入 taskgraph_exec/test_successor_with_taskgraph.py 的换芯用例：提后继后跑到根结论，断言旧代“派发作废”标记已清、别的标记不动 | test_successor_with_taskgraph 现在只断言到第 2 版计划，不跑到完成，覆盖不了 |
| `test_role_prompts_single_copy.py` | 7 | E 7 | 保留；钉 ROOT_REVIEWER/CRITIC 登记的那条随模板删改断言 | 角色登记表，纯数据 |
| `test_root_review_coordinator.py` | 75 | A 1 / 重写 25 / 删除 13 / E 10 / F 26 | A 1；重写 25 → 5 个（RA 终审时序、RB 完成后包形状、RC 重切通道参数化、RD 切包上限、RE 修复请求了结范围）；删 13（含 3 条无目录覆盖：479 生产者自审、539 切包后叶子不能再验收、1721 READY 说明文字）；E 10；F 26（旧 request()/record_review/_ask_root_reviewer 非保证段/_collect_root_review）（逐用例表见第三节） |  |
| `test_root_review_evidence.py` | 30 | A 1 / 重写 3 / 删除 2 / E 4 / F 20 | A 1；重写 3（并进 coordinator 的 RA/RB 与本文件 1068）；删 2；E 4；F 20（旧根审阅请求的摘录/covered_by/提示词与旧通道端到端）（逐用例表见第三节） |  |
| `test_root_review_repair_library.py` | 5 | 重写 4 / 删除 1 | 删 1：retire_and_refine_replaces_the_root_method_in_one_revision（test_h1i_deferred_repair_resume.py::test_deferred_replace_method_cold_resume_reuses_frozen_decision_without_llm，换芯后，换做法经收集器一次提交）；重写 4（退役须点名被采用实例、不退役再细化被拒、替换等退役叶子的未结尝试、同做法同参数重采被具名拒）→ 约 2 个（脚本化规划器发错误的替换决定，参数化） | e2e 手工 plan()；删前核对 h1i 那条确为“替换”而非“细化” |
| `test_root_review_user_goal.py` | 8 | F 8 | 随旧根审阅请求删（RootReviewCoordinator.request()、ROOT_REVIEWER 提示词、placeholder 只服务它） | 子代理核实 request() 只被 _ask_root_reviewer 非保证段用。**另需核**：保证通道终审有没有给审阅员看用户原话、去掉主机路径——这组保护随旧请求消失，若保证通道没做，是产品缺口（不是测试问题） |
| `test_root_reviewer_prompt.py` | 2 | F 2 | 随 ROOT_REVIEWER 删 |  |
| `test_run_loop_inflight_planning.py` | 1 | A 1 | 保留（换芯，中） |  |
| `test_runtime_lost_native_retry.py` | 7 | 删除 1 / E 6 | E 6（系统原样重做的判断纯函数）；删 1：the_systems_own_retry_is_recorded_as_such（backend/tests/orchestration/test_layered_scripted_lane.py::test_a_malformed_result_is_redone_in_place_without_charging_the_step 断言系统原地重做、不问规划器、不扣次数） | 删前核对 Host 那条是否断言了“来源标为系统”；没有就给它补一句断言 |
| `test_seed_methods.py`（清点：不受影响） | 62 | E 62 | 不动（但保护对象是代码领域种子做法，见第四节④） | 清点：不受影响 |
| `test_service_intent_provider_blocker.py` | 5 | A 5 | 保留（换芯，中） | 其中 critic 两条在保证通道下是审阅员调用，换芯后脚本按 "unknown" 角色给 |
| `test_stall_asks_planner_first.py` | 11 | A 7 / E 4 | 7 条换芯（中，_stalled 世界）；4 条 E | _stalled 用 e2e.committed + install_hierarchical |
| `test_subgoal_levels.py`（清点：不受影响） | 6 | E 6 | 不动 | 清点：不受影响 |
| `test_terminal_unknown_release.py` | 6 | A 5 / D 1 | 5 条换芯（中）；cancel_mission_writes_usage_flags... 只建任务后取消，D |  |
| `test_unclaimed_port_is_a_rejected_result.py` | 1 | A 1 | 保留（换芯，中） |  |
| `test_unknown_usage_upper_bound.py` | 6 | 重写 5 / E 1 | E 1（新代码下重估收尾）；重写 5 → 并入供方记账族约 2 个产品同形用例（未知用量按上限计入收尾、已知事实不截、全部覆盖才计；只等无法回答的原始调用的审阅不挡收尾、预留已没的豁免审阅照样收） | 直接写意图行与用量行 |
| `test_v14_runtime_closure.py` | 10 | A 1 / 重写 4 / F 5 | 1 条换芯（中）；h4 问题 4 条（不阻塞问题持久且不停派发、阻塞问题暂停且回答 CAS 幂等、过期问题答不了、修复请求只被同主题已提交消费）→ 约 2 个；F 5（h6 评测拒造数字、h8 持久计量×3、h8 非正常局须触发干预——只测评测模块） | 评测模块 htn_meter / htn_matrix / experiment 的 H8 部分随评测臂删（孤儿扫描确认） |
| `test_verify_workspace_inputs.py` | 7 | E 6 / 待裁定 1 | E 6（覆盖层纯函数）；inspect_at_2_still_receives_the_patch_port_after_overlay 待裁定（代码领域，第四节④） |  |

#### `tests/orchestrator/full_target/operation_completion/`（18 个文件，97 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `operation_runtime_fixture.py` | 0 | — | 第 4 步后删（被代表用例 3 的种子取代） | 辅助模块 |
| `test_completion_compound_commit.py` | 1 | E 1 | 改 E：直接对嵌套组合的计划调完成范围编译器（同 test_completion_scope_compiler 的做法），断言局部组合链不被改写成根要求 | 现在借 e2e 手工提交计划再读范围 |
| `test_completion_consumers.py` | 3 | 重写 3 | 重写 3 → 1 个代表用例 3 变体（准备好的 MIXED 范围数据可读、顺序与终结仍关；冷库读原结果、不从当前端口补认领） | test_scoped_content_commit 手工世界 |
| `test_completion_contract.py` | 20 | 重写 11 / E 8 / F 1 | E 8（OCC-02 编解码 5 + 范围覆盖/复核/原子根 3）；F 1：report_only_and_legacy_remain_compatible（委托 E08 legacy 对照）；重写 11（OCC-01/03～06/08～12）→ 约 4 个代表用例 3 变体：结果身份各轴、要求/输入撤销、多效果不合并、写入点故障整体回滚+冷重放；其中 OCC-03 正路即代表用例 3 本身 | _operation_world 手工 T0/T1/T3；effect_pending 那条起了主循环但种子手工伪造，同列重写 |
| `test_completion_data_dispatch.py` | 1 | A 1 | 保留（换芯，高：上游验收是手工伪造的，改由真实上游叶子跑完） |  |
| `test_completion_pending_stall.py` | 1 | A 1 | 保留（换芯，高：MIXED 准备验收手工伪造，改由代表用例 3 的种子跑到“效果待办”） |  |
| `test_completion_plan_commit.py` | 5 | 重写 3 / 删除 2 | 删 2：occ09_plan_commit_freezes_exact_single_root_completion_scope（product_world/test_full_circle.py::test_a_user_mission_completes_on_the_product_deployment：没冻结范围就没有内容审阅）、occ09_replayed_plan_commit_does_not_duplicate...（taskgraph_exec/test_commit_atomicity.py::test_committed_reply_replay_after_revoke_is_readonly_and_changed_bytes_conflict，换芯后，重放只读）；重写 3（范围读者复核当前来源、范围写入故障整体回滚、没确认映射的计划在发布前被拒）→ 约 2 个（故障一档并入 test_commit_atomicity 的故障注入参数） | 删前核对 test_commit_atomicity 的“只读”断言是否覆盖完成范围表 |
| `test_completion_scope_compiler.py`（清点：不受影响） | 11 | D 11 | D：只建任务行测编译；第 4 步若在建任务入口强制经部署，改经产品组装 | 清点：不受影响 |
| `test_completion_spec_approval.py` | 13 | 重写 1 / D 12 | D 12（确认写入、引用错误、回执重放、故障回滚、跨租户、非人主体、要求修订、同要求换命令、迁移、自动确认记为系统/人、系统不确认操作效果）改经产品组装建任务（auto=False）；重写 1：scope_store_revalidates_exact_spec_plan_task_and_unique_identity（要计划提交） | 确认完成映射不在“提交/尝试/结果/验收/结算/审阅”之列 |
| `test_completion_storage_integrity.py`（清点：不受影响） | 4 | D 4 | 同上 | 清点：不受影响 |
| `test_operation_intent_sources.py`（清点：不受影响） | 5 | 重写 1 / E 4 | E 4；sources_resolve_real_preparation... 用 _mixed_world 手工准备验收，并入代表用例 3 变体 | 清点归“不受影响”，有 1 条其实借了手工验收 |
| `test_operation_payloads.py`（清点：不受影响） | 5 | D 1 / E 4 | E 4；payload_store_requires_same_mission_receipt D | 清点：不受影响 |
| `test_operation_t0_t3_runtime.py` | 1 | 重写 1 | 改写成代表用例 3（本文件就是它的原型：T0→T1→发布→T3，含首轮审阅被打断） |  |
| `test_operation_workspace_projection.py`（清点：不受影响） | 2 | 重写 2 | 并入代表用例 3 变体（工作区投影只投已验收的准备产物） | 清点归“不受影响”，实际借 _mixed_world 手工验收 |
| `test_scoped_content_commit.py` | 3 | 重写 3 | 重写 3 → 1 个代表用例 3 变体（TASK_CONTENT 只用已验证层、MIXED 准备验收按范围且可重放、贡献写入故障整体回滚） | _mixed_world 手工伪造；被 6 个测试文件和 7 个接缝脚本当构造器借用 |
| `test_scoped_content_integrity.py` | 5 | 重写 5 | 重写 5 → 约 2 个（篡改型参数化：换冻结输入/结果认领、包判别的准则、根必需检查被审阅 PASS 顶替、输出指向结果外的产物、产物出处由系统绑定） |  |
| `test_scoped_reconciliation.py` | 8 | 重写 8 | 重写 8 → 约 3 个代表用例 3 变体（登记的否定证明与过期批准、重交接上限与交接上限、人裁决查不清的动作） | operation_runtime_fixture 手工物化 |
| `test_system_operation_intents.py` | 9 | 重写 7 / E 2 | E 2（续写文件取最下游、声明生产者决定发布哪份）；重写 7：系统按批准效果备申请单 / 先装运行时再提交 即代表用例 3 本身，其余 5 条（缺文件问规划器或停、审阅判不下重提两次后停、审阅员拒绝停给人、无需批准的操作不由系统备、只有确认人带名额授权）→ 约 3 个变体 |  |

#### `tests/orchestrator/full_target/taskgraph_exec/`（20 个文件，40 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `crash_seed.py` | 0 | — | 随 production_fixture 换芯 | 辅助模块（子进程） |
| `production_fixture.py` | 0 | — | 换芯（见第一节） | 辅助模块 |
| `test_captured_baseline_removed.py` | 2 | A 2 | 保留（换芯，中） | production_fixture.enabled_world； |
| `test_commit_atomicity.py` | 2 | A 2 | 保留（换芯，中） | production_fixture.enabled_world； |
| `test_convergence_advanced_event.py` | 2 | D 1 / E 1 | D 1（只建任务+收敛作业行）；E 1 |  |
| `test_convergence_wake_terminal_mission.py` | 2 | D 2 | D：经产品组装建任务（取消走门面），再写收敛作业行 |  |
| `test_execution_view.py` | 1 | A 1 | 保留（换芯，中） | production_fixture.enabled_world； |
| `test_late_accounting_quiet.py` | 2 | A 1 / E 1 | 1 条换芯（中）；1 条 E |  |
| `test_mission_sources.py` | 4 | A 2 / E 2 | 2 条换芯（中）；2 条 E（别用途的审阅包被拒、终审绑定判定视图） | the_final_judge_binds_its_judgment_view：若“任务级终审裁判”指旧 judge_mission，随 _judge 删；是保证通道 MISSION_FINAL 则保留——换芯时核 |
| `test_old_domain_recovery.py` | 1 | A 1 | 保留（换芯，中） | production_fixture.enabled_world；crash_seed 子进程 |
| `test_process_recovery.py` | 2 | A 2 | 保留（换芯，中） | production_fixture.enabled_world；crash_seed 子进程 |
| `test_production_seed.py` | 1 | A 1 | 保留（换芯，中），改断言 | “显式启用”这一步在建任务时绑定后不存在；改成断言建任务回执里已绑定、首个派发与修订回执照旧 |
| `test_read_history_protocol.py` | 5 | A 5 | 保留（换芯，中） | production_fixture.enabled_world；“重复启用读原回执”改成建任务时的那张回执 |
| `test_revision_read_cache.py` | 1 | A 1 | 保留（换芯，中） | production_fixture.enabled_world； |
| `test_source_change_replan.py` | 1 | A 1 | 保留（换芯，中） | production_fixture.enabled_world； |
| `test_successor_with_taskgraph.py` | 1 | A 1 | 保留（换芯，中） | production_fixture.enabled_world；并入 test_revoked_generation_cleared 的断言（跑到根结论、作废标记已清） |
| `test_taskgraph_required.py` | 4 | D 1 / F 3 | F 3（等待期：拿到授权后等绑定、要求只在建任务时写、授权撤销放开等待——等待机制整体删）；no_unbound_plan_is_committed_for_a_required_mission 改成：绕过部署手工建一条未绑定分层任务，计划提交具名报 TASKGRAPH_NOT_BOUND（D，合同校验） | 方案第二节 1：未绑定一律 TASKGRAPH_NOT_BOUND |
| `test_terminal_event_transaction.py`（清点：不受影响） | 1 | E 1 | 保留；两个同名 taskgraph_enabled 合并后改 monkeypatch 目标 | 清点：不受影响 |
| `test_wait_on_method_instance.py` | 3 | A 3 | 保留（换芯，中） | production_fixture.enabled_world； |
| `test_worker_skills.py` | 5 | A 2 / E 2 / F 1 | 2 条换芯（中）；E 2；test_a_legacy_pool_never_freezes_skill_tools_it_cannot_serve 随旧执行池删 |  |

#### `tests/orchestrator/gap_phase1/`（3 个文件，16 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_code_knowledge.py` | 8 | 重写 6 / E 2 | E 2（无领域即通用档案、code-v2 提示词）；重写 6 → 约 2 个（执行者交的声明引用本次跑过并通过的 run_tests 才判 VERIFIED、悬空/无关引用不支撑、已验证的不可改） | two_leaf_service 手工建尝试、记带声明的结果 |
| `test_context_consumption.py` | 7 | 重写 7 | 重写 7 → 约 2 个（下游执行者上下文里有上游已验证知识及来源；精确标识/路径/中英查询命中；被取代或依据失效的知识不当现行） | 同上 |
| `test_result_output_contract.py` | 1 | 重写 1 | 并入上面的知识用例（版本化输出示例进到真实派发） | 同上 |

#### `tests/orchestrator/step04/`（6 个文件，15 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `knowledge_helpers.py` | 0 | — | 第 4 步后删（被产品同形世界取代） | 辅助模块 |
| `test_artifact_versions.py` | 2 | 重写 2 | 并入知识用例的产物部分（同路径同版本第二行被表结构拒；同一回合重复投递不改版本）；第一条若只需直插表，可改 E | 同上 |
| `test_blackboard.py` | 1 | 重写 1 | 并入知识用例（黑板只读、已核实与候选两层分开） | 同上 |
| `test_disputes_on_leaf_steps.py` | 3 | 重写 3 | 重写 3 → 1 个（两步声明互相矛盾都标争议并互指；与已核实知识矛盾的不进知识） | 同上 |
| `test_retrieval_context.py` | 5 | 重写 1 / E 4 | E 4；摘要按分支确定且不改声明状态 → 并入知识用例 |  |
| `test_step04_review_round1.py` | 4 | 重写 1 / E 3 | E 3；取代保留记录版本 → 并入知识用例 |  |

#### `tests/orchestrator/host_support/`（2 个文件，14 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_create_mission_entry.py` | 5 | A 4 / F 1 | 4 条换芯（中：建任务改走 deployment.create_mission，断言“同一道门”时以部署组装为准）；test_cli_mission_create_records_the_provider_kind 随命令行 mission create 删 |  |
| `test_facade.py` | 9 | A 9 | 保留（换芯，低） | 门面 create 单独用会建出没根、没绑定的分层任务；换芯后建任务一律经 world.create（部署组装），门面字段/回执断言不变 |

#### `tests/orchestrator/p32/`（1 个文件，2 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_startup_failure_lifecycle.py` | 2 | A 2 | 保留（换芯；第 1 条高：leaf_world 手工建尝试，改由真实派发造在途调用） |  |

#### `tests/orchestrator/p33/`（11 个文件，96 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_g_critic_dispatch_recovery.py` | 1 | A 1 | 保留（低） | 审阅员等待窗口继承 SDK 回合截止，保证通道审阅仍用 |
| `test_g_large_read_context.py` | 11 | A 1 / B 10 | 1 条换芯（中）；10 条换原生运行时构造器（经 test_g_workspace_paging 的旧运行时）；legacy_runtime_cannot_be_silently_upgraded 换时核是否只测旧运行时，是则随删 |  |
| `test_g_replay_audit_plugin.py` | 19 | D 15 / E 4 | D 15（只建任务、写坏数据给观察器看）；E 4（子进程/直写 SQLite 的发现与 WAL 读取） |  |
| `test_g_workspace_paging.py` | 12 | B 12 | 换原生运行时构造器 | 直接调 build_agent_runtime |
| `test_p33_domain_binding.py` | 5 | D 4 / E 1 | D 4（建任务冻结领域）；E 1（规格哈希） |  |
| `test_p33_domain_prompts.py` | 3 | A 1 / D 2 | 1 条换芯（低）；2 条 D |  |
| `test_p33_g1_create_sources.py` | 9 | A 8 / E 1 | 8 条换芯（低：建任务改走 deployment.create_mission_with_sources）；1 条 E |  |
| `test_p33_publish_source_guard.py` | 10 | 重写 4 / 删除 2 / E 4 | 删 2：reopened_code_action_cannot_publish_inside_other_missions_source_storage（p33/test_p33_source_runtime.py::test_source_mission_rejects_publisher_overlapping_actual_source_roots，换芯后）、disjoint_publish_executes...（::test_source_mission_accepts_disjoint_publish_directory，换芯后）；重写 4（撤销/已结束任务仍保护共享存储、无登记来源的任务已占存储、执行器建好后新建的来源任务也看得见、已完成回执照样对账）→ 约 2 个；E 4（无根钩子/无物理根时失败关闭、共享路径解析符号链接、自定义 CAS/工作区根受保护） | operation_runtime_fixture 手工物化发布 |
| `test_p33_result_evidence_gate.py` | 1 | 待裁定 1 | 待裁定（代码领域 record_result 证据闸，第四节④） | leaf_world 手工记结果 |
| `test_p33_source_runtime.py` | 4 | A 3 / E 1 | 3 条换芯（低）；1 条 E |  |
| `test_p33_sources.py` | 21 | D 21 | D：来源命令只建任务、登记/批准来源，不进提交环节 |  |

#### `tests/orchestrator/p34/`（1 个文件，5 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_mission_runtime_profile.py` | 5 | A 4 / D 1 | 4 条换芯（低）；bare_commit_cannot_bypass_profile_binding D |  |

#### `tests/orchestrator/p36/`（1 个文件，1 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_functional_acceptance.py` | 1 | D 1 | D：导出证据只需要库里有一个任务 | 文档串自己写明“做没做完无关” |

#### `tests/orchestrator/step02/`（3 个文件，11 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_attempt_charge_by_fault.py` | 3 | A 2 / E 1 | 2 条换芯（高：loop_leaf 手工尝试）；归责表 E |  |
| `test_cli_demo.py` | 2 | D 1 / F 1 | mission_create_validates_and_is_idempotent 随命令行 mission create 删；replay 那条改成经产品组装建任务后跑命令行 replay（D） |  |
| `test_commit_service.py` | 6 | A 2 / 重写 1 / 删除 3 | 2 条换芯（高：loop_leaf，失败重试与停、拒结果与从 VERIFYING 取消）；删 3：建任务幂等与冲突（backend/tests/orchestration/test_layered_scripted_lane.py::test_creating_the_same_mission_twice_is_one_mission）、预留/派发身份/结果验收（product_world/test_full_circle.py::test_a_user_mission_completes_on_the_product_deployment）、r1 过期属主不能提交裁决（backend/tests/orchestration/test_layered_scripted_lane.py::test_a_restart_during_a_model_call_resumes_and_completes 与 test_a_backend_killed_inside_a_model_call_is_resumed_by_the_next_one 走的正是租约换主）；重写 1：r1 任务级并发上限在提交里查 | 删第 3 条前核对 Host 那两条断言了旧属主写不进去；没有就改成重写 |

#### `tests/orchestrator/step06/`（1 个文件，3 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_step06_review_fixes.py` | 3 | 重写 1 / E 2 | E 2；p1_6 未知用量占着的预留可见、上时间线 → 并入供方记账族重写 |  |

#### `tests/orchestrator/step07/`（6 个文件，24 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `helpers_step07.py` | 0 | — | 底座从 leaf_world 换成产品组装（或 D′） | 辅助模块 |
| `test_action_execution.py` | 2 | D 2 | D（只建任务+动作台账；候选挂叶子任务则走 D′，第四节③） | ledger_service |
| `test_action_ledger.py` | 15 | D 11 / E 2 / 待裁定 2 | E 2；D 11（同上）；待裁定 2：l3_needs_two_independent_grants_from_different_people_by_default / without_the_distinct_people_rule... 测“两人审批”——10-02 定“暂时不做、残留在阶段 A 一并删”，建议随残留删（F） |  |
| `test_approvals.py` | 1 | A 1 | 保留（换芯，低） |  |
| `test_human_review.py` | 4 | 重写 3 / D 1 | D 1（评论作为数据挂在任务/请求上）；重写 3（接管停止把依据记账、带备注重试留在本步、接管不复活已结束的任务）→ 约 1 个 | ledger_service + 手工建尝试 |
| `test_waiting_view.py` | 2 | D 2 | D（同 test_action_ledger） |  |

#### `tests/orchestrator/step09/`（1 个文件，7 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_policy_library.py` | 7 | A 1 / D 3 / E 3 | 1 条换芯（低）；D 3（库只播种一次、配置漂移只记录、策略接口与命令行只读）；E 3 | 清点归主循环；命令行只读子命令不受影响 |

#### `tests/orchestrator/p35/`（14 个文件，44 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `test_admission_collection_runtime.py` | 1 | A 1 | 保留（换芯，高） | leaf_world 手工尝试 |
| `test_admission_estimator_candidates.py` | 1 | A 1 | 保留（换芯，低） |  |
| `test_admission_slot_change.py` | 1 | E 1 | 保留 | 纯函数 |
| `test_first_protected_tail_hooks.py` | 4 | 重写 3 / E 1 | E 1；3 条并入供方记账族重写（首次请求尾部钩子用实际身份/金额、尾部与尝试一起回滚、虚构尝试被拒） |  |
| `test_first_request_guard_integration.py` | 1 | 重写 1 | 并入供方记账族重写 |  |
| `test_multi_profile_load.py` | 2 | 重写 1 / E 1 | E 1；1 条并入供方记账族重写 |  |
| `test_priced_budget_cold_reopen.py` | 1 | 待裁定 1 | 待裁定（计价，第四节②） |  |
| `test_provider_accounting.py` | 5 | 重写 4 / 待裁定 1 | 4 条并入供方记账族重写；actual_late_accounting_preserves_response_and_prices_original_call_once 待裁定（计价） |  |
| `test_provider_accounting_boundaries.py` | 3 | 重写 3 | 并入供方记账族重写 |  |
| `test_provider_budget_guard.py` | 7 | 重写 7 | 并入供方记账族重写 | setup_runtime = leaf_world + 旧运行时 |
| `test_provider_budget_identity.py` | 4 | 重写 3 / 待裁定 1 | 3 条并入供方记账族重写；priced_ports_require_explicit_admission_capability 待裁定（计价） |  |
| `test_provider_budget_recovery.py` | 7 | 重写 7 | 并入供方记账族重写 |  |
| `test_succeeded_missing_usage_boundary.py` | 2 | 重写 1 / F 1 | legacy_execution_reconciliation_cannot_replace_succeeded_usage 测旧运行时的执行对账口，随旧执行池删；另 1 条并入供方记账族重写 |  |
| `test_tail_and_priced_budget.py` | 5 | 待裁定 5 | 待裁定（计价，第四节②） |  |

#### `tests/orchestrator/full_target/fixtures/htn/`（1 个文件，0 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `htn_world.py`（清点：不受影响） | 0 | — | 见第一节 | 辅助模块 |

#### `scripts/assurance_seams/`（19 个文件，0 个用例）

| 文件 | 用例 | 分类（条数） | 建议 | 理由 / 难点 |
|---|---|---|---|---|
| `_assured_fixture.py` | 0 | — | 第 4 步删（被产品同形世界取代） | 被 15 个测试文件 + 7 个接缝脚本导入 |
| `check-binding-seam.py` | 0 | — | 待裁定：没有任何测试调用（见第四节⑦） | README 自称“诊断源码，不承诺皆绿、禁止当产品验收”；前 5 个文档串一字不差 |
| `check-use-seam.py` | 0 | — | 待裁定：没有任何测试调用（见第四节⑦） | README 自称“诊断源码，不承诺皆绿、禁止当产品验收”；前 5 个文档串一字不差 |
| `content-review-seam.py` | 0 | — | 待裁定：没有任何测试调用（见第四节⑦） | README 自称“诊断源码，不承诺皆绿、禁止当产品验收”；前 5 个文档串一字不差 |
| `critic-format-repair-seam.py` | 0 | — | 迁移（有测试调用） | test_a_assurance::test_check_budget_and_format_bounds 调用；FixtureCommit+_mixed_world+旧运行时 |
| `critic-runner-seam.py` | 0 | — | 待裁定：没有任何测试调用（见第四节⑦） | README 自称“诊断源码，不承诺皆绿、禁止当产品验收”；前 5 个文档串一字不差 |
| `evidence-tools-seam.py` | 0 | — | 迁移（有测试调用） | test_a_assurance::test_extra_evidence_exposure 调用；AssuredRuntime |
| `executor-check-seam.py` | 0 | — | 迁移（有测试调用） | test_a_assurance::test_check_receipt_scope 调用；build_world |
| `final-writer-seam.py` | 0 | — | 迁移（有测试调用） | test_c_assurance::test_closeout_and_notification 调用；Orchestrator+install_assurance（A 部分）+AssuredRuntime（B 部分） |
| `four-consumer-seam.py` | 0 | — | 迁移（有测试调用） | test_c_assurance 调用；A 部分已是真 Orchestrator+install_assurance，B 部分 AssuredRuntime |
| `local-check-pin-seam.py` | 0 | — | 待裁定：没有任何测试调用（见第四节⑦） | README 自称“诊断源码，不承诺皆绿、禁止当产品验收”；前 5 个文档串一字不差 |
| `purpose-builders-seam.py` | 0 | — | 待裁定：没有任何测试调用（见第四节⑦） | README 自称“诊断源码，不承诺皆绿、禁止当产品验收”；前 5 个文档串一字不差 |
| `recovery-seam.py` | 0 | — | 迁移（有测试调用） | test_c_assurance::test_review_cold_resume 调用；两次 Orchestrator 生命周期+AssuredRuntime |
| `review-import-seam.py` | 0 | — | 待裁定：没有任何测试调用（见第四节⑦） | README 自称“诊断源码，不承诺皆绿、禁止当产品验收”；前 5 个文档串一字不差 |
| `review-runtime-seam.py` | 0 | — | 待裁定：没有任何测试调用（见第四节⑦） | README 自称“诊断源码，不承诺皆绿、禁止当产品验收”；前 5 个文档串一字不差 |
| `root-gate-seam.py` | 0 | — | 迁移（有测试调用） | test_v_assurance::test_restore_quarantine_and_current_reauthorization 调用；真 Orchestrator，无模型 |
| `seam_paths.py` | 0 | — | 保留 | 路径工具 |
| `tick-factory-seam.py` | 0 | — | 待裁定：没有任何测试调用（见第四节⑦） | README 自称“诊断源码，不承诺皆绿、禁止当产品验收”；前 5 个文档串一字不差 |
| `validity-accept-seam.py` | 0 | — | 待裁定：没有任何测试调用（见第四节⑦） | README 自称“诊断源码，不承诺皆绿、禁止当产品验收”；前 5 个文档串一字不差 |
---

## 三、八个大文件的逐组分诊（子代理逐用例做，我审读后并入）

### 1. `full_target/test_htn_end_to_end.py`（187）

| 组 | 条数 | 类 | 建议 | 覆盖 / 补的断言 |
|---|---|---|---|---|
| §1 任务行与计划占用一一对应 | 1 | C | 删除 | 【整圈】：少了任务行派不出工作 |
| §1 行字段（占用号/修订号、原子 READY/复合 BLOCKED、无 dependency_ids、kind=work、带判据） | 5 | C | 改 E | 纯函数 `occurrence_tasks.occurrence_task()` |
| §1 物化事件一条、每行一条 TaskCommitted | 2 | C | 重写→R1 | |
| §1 账户挂在任务账户下、前缀 | 2 | C | 删除 | 【整圈】：前缀错找不到上级账户，提交失败 |
| §2 守恒、复合 0、两叶平分、固定额度、超池回落、总和≤池 | 6 | C | 重写→R1 | 主循环里没有份额断言 |
| §2 池=任务预算、额度为正、份额除数、无上限守恒 | 4 | E | 保留（第 1 条改 E） | |
| §2 预算不足被拒、被拒不留修订与账户 | 2 | C | 重写→R2 | |
| §3 提交后 ACTIVE | 1 | C | 删除 | 【整圈】 |
| §3 提交事件带 mission_status | 1 | C | 重写→R1 | |
| §3 首份修订前仍 PLANNING | 1 | D | 经组装建任务（`auto=False`）直接读 | |
| §4 重启不重复物化、不重复扣池 | 2 | C | 删除 | 【Host 通道】`test_a_restart_during_a_model_call_resumes_and_completes`（重启前后 PlanRevisionCommitted 只 1 条） |
| §4 同一回复再来不再物化 | 1 | C | 重写→R2 | |
| §5 复合从不准入 | 1 | C | 删除 | `test_progress_acceptance_2b.py::test_a_branch_that_needs_structural_planning_does_not_block_the_independent_d`（换芯后） |
| §5 生产者准入/数据消费者不准入、分配器只批已准入 | 2 | C | 删除 | `test_progress_acceptance_2b.py::test_c_waiting_for_data_does_not_hold_back_the_independent_d`（换芯后） |
| §5 准入是门自己的记录 | 1 | C | 删除 | 【整圈】；本文件 A 类 `test_an_object_that_merely_claims_the_flag_is_not_an_admission` 保留 |
| §5 无需求 NOT_SELECTED、复合行写 READY 不改答案、撤回记录每修订每原因一次、明细码不合并 | 4 | C | 重写→R3 | |
| §6 端口声明、未声明拒、改标签拒、编解码往返、保留 provisional | 5 | C | 改 E | 纯函数 |
| §6 已记录产出到达解析器 | 1 | C | 删除 | `test_progress_acceptance_2b.py::test_abc_runs_to_completed_without_asking_the_main_planner_again`（换芯后） |
| §6 被丢掉占用的产出不再供给 | 1 | C | 重写（补断言） | 补进 `taskgraph_exec/test_successor_with_taskgraph.py` |
| §7 迁移版本、接受侧接在唯一提交服务上 | 2 | E | 保留 | |
| §7 新表 STRICT、回执只收 id、命令里放回执对象被拒 | 3 | C | 改 E | |
| §7 回执引用未存验收/别的任务 | 2 | C | **待裁定**→R7 | 交付回执，产品走不到 |
| §8 子女验收前根审不就绪等 7 条 | 7 | C | 重写→R5 | |
| §8 接受侧拒收 PlanPrincipal | 1 | C | 改 E | |
| §8 三条读源码结构钉 | 3 | E | 保留 | |
| §10 种子包有开放根目标、带做法引用、版本与每样一次 | 3 | C | 删除 | `test_planner_package_single_layer.py::test_the_request_is_nine_views_and_nothing_twice`（换芯后） |
| §10 已细化目标不再待细化、包里列已提交原子步 | 2 | C | 重写（补断言） | 补进【子目标】：规划器回调查看第二份包 |
| §10 选分层提示词、钉版不改提示词 | 2 | E | 保留 | |
| §11 观测纯函数 5 条 | 5 | E | 保留 | |
| §11 不可用不入库、观测入库、一个失败不停整批 | 3 | C | 改 E | 只需任务号 |
| §12 分配入口（读源码） | 1 | E | 保留 | |
| §13 主循环不在无根结论时完成任务 | 1 | A | 换芯 | |
| §14 复合拿原子份额会透支 | 1 | C | 重写→R1 | |
| §14 五条变异（三条改 E、两条读源码） | 5 | E | 保留 / 改 E | |
| §14 手写账户前缀 | 1 | C | 删除 | 【整圈】 |
| §14 按 READY 字符串分配 | 1 | C | 删除 | `test_progress_acceptance_2b.py::test_c_waiting_for_data...`（换芯后） |
| COMPLETE_COVERAGE 常量 | 1 | E | 保留 | |
| §15 已验证叶子成验收 | 1 | C | 删除 | 【Host 通道】整圈（断言 AcceptanceCommitted） |
| §15 审查锚点先冻结 | 1 | C | 删除 | 【整圈】 |
| §15 有一层 FAIL 不验收 | 1 | C | 删除 | 【Host 通道】`test_a_step_review_that_sends_the_work_back_is_redone_and_then_accepted` |
| §15 产出按端口入索引、到达数据消费者 | 2 | C | 删除 | `test_progress_acceptance_2b.py::test_abc_runs...`（换芯后） |
| §15 必需端口没人认领拒收 | 1 | C | 删除 | `test_unclaimed_port_is_a_rejected_result.py::test_an_unclaimed_declared_port_is_refused_as_a_result_and_the_loop_survives`（换芯后） |
| §15 撤销后可再验收；复合目标不走叶子验收 | 2 | C | **删除（无目录覆盖）** | 前者产品不可达（无撤销命令）；后者主循环从不对复合调叶子验收 |
| §15 收尾步端口照样声明 | 1 | C | 重写（补断言） | 补进【整圈】：收尾产出登记在 `delivery` 端口 |
| §15 索引 schema 取边、认领指哪个登记哪个、多余文件只当证据、没人认领不入索引 | 4 | C | 重写（补断言） | 补进 `test_abc_runs...`（换芯后） |
| §15 检查层 ERROR 不算过、认领未声明端口被拒 | 2 | C | 重写（补参数） | 并入 `test_unclaimed_port...` 参数化 |
| §15 许可按键命名、重放不重复写索引 | 2 | C | 重写→R11 | |
| §15 路径推不出端口 | 1 | C | 改 E | |
| §16 共享生产者 4 条 | 4 | C | **待裁定**→R14 | 只读子目标共享，产品世界无可复用类型 |
| §16 再验收一次不算第二次执行 | 1 | C | 重写→R11 | |
| §16 发许可后消费者可派 | 1 | C | 删除 | `test_progress_acceptance_2b.py::test_abc_runs...`（换芯后） |
| §16 给别的消费者的许可不能转用 | 1 | C | 重写（补断言） | 补进 `test_abc_runs...` |
| §16 先发许可再读就绪（读源码） | 1 | E | 保留 | |
| §17 `_unassembled` 四条 | 4 | F | 随删 | 缺装配这条路 |
| G2 只读子目标共享 7 条 | 7 | C | **待裁定**→R14 | |
| F7 两轮细化预算守恒 4 条 | 4 | C | 重写→R1 | |
| F6 根审就绪、锚点形成、复述结论、交结论身份 | 4 | C | 删除 | 【整圈】 |
| F6 引用审时要求版本、审后要求变动被拒、REJECTED 拒形成 | 3 | C | 重写→R5 | |
| F6 没审到的判据复述 UNKNOWN | 1 | C | 改 E | |
| F6 完成判定四种拒绝 | 4 | C | 重写→R6 | |
| F5 闭包内外回执 4 条 | 4 | C | **待裁定**→R7 | |
| F5 无交付合同不给回执 | 1 | C | 删除 | 【整圈】 |
| F12 同一根命令稍后重放 | 1 | C | 重写→R5 | |
| F12 时钟不进意图哈希 | 1 | C | 改 E | |
| F2/F3 唯一写入方（扫源码） | 1 | E | 保留 | |
| F2/F3 撤销后不供给、不发许可、不算贡献 | 3 | C | **待裁定**→R8 | 撤销验收，产品不可达 |
| F8 一次读取 | 1 | C | 重写→R3 | |
| F11/M17 直接入口无准入、伪造准入 | 2 | A | 换芯 | |
| M13 无边消费者空清单 | 1 | C | 删除 | `test_progress_acceptance_2b.py::test_c_waiting_for_data...`（换芯后） |
| M16 两个原因两条记录 | 1 | C | 重写→R3 | |
| F16 适用性/拒绝理由/新修订重评/观测每修订一次/重开 | 6 | C | **待裁定**→R10 | 前提与观测，产品 `observers=()` |
| F16 事实行纯函数 2、读集检查器 2 | 4 | C | 改 E | |
| §18 带前提叶子扣住/发许可后可派/许可写明条件/UNKNOWN→BLOCKED/证据更新换新许可 | 5 | C | **待裁定**→R10 | |
| §18 前提继承、先发启动许可再读就绪 | 2 | E | 保留 / 改 E | |
| §18 停滞组 10 条 | 10 | A | 换芯（最难：产品会自动准入需求，`demand=False` 造不出 NOT_SELECTED，要换别的方式造停滞） | |
| §18 两种许可并存、同主体冲突、同键第二意见被拒 | 3 | C | **待裁定**→R10 | |
| §18 装了但无规划世界不发许可 | 1 | F | 随删 | |

### 2. `full_target/test_hierarchical_event_flow.py`（58）

| 组 | 条数 | 类 | 建议 | 覆盖 / 补的断言 |
|---|---|---|---|---|
| 一轮规划：提交一个修订、成当前、读回 3 个占用、只一个根、图版本不前进 | 5 | C | 删除 | 前两条【Host 通道】整圈；读回、单根【整圈】；图版本【子目标】 |
| 种子网络只有根 | 1 | D | 删除 | `test_planner_package_single_layer.py::test_the_request_is_nine_views_and_nothing_twice`（换芯后） |
| 提交事件与来源带 proposal_id | 2 | C | 重写→R1 | |
| 回复带权限字段被拒 | 1 | C | 改 E | `PlanProposal.from_json` |
| 没有规划世界的部署 | 1 | F | 随删 | |
| 同一回复再来不出第二个修订 | 1 | C | 重写→R2 | |
| 复合门：拦截、记事件、不看旧状态、不建尝试 | 4 | C | 重写（补参数） | 并入 e2e A 类 `test_a_task_with_no_admission_is_not_dispatched_by_the_direct_entry` |
| 原子步不被拦 | 1 | C | 删除 | 【整圈】 |
| 无语义绑定在门口即损坏 | 1 | C | 删除 | 本文件 A 类 `test_the_dispatch_branch_stops_one_mission_on_a_missing_binding`（换芯后） |
| 就绪集里没有复合 | 1 | C | 删除 | `test_progress_acceptance_2b.py::test_a_branch_that_needs_structural_planning...`（换芯后） |
| 投影 7 条（改写状态不改答案、旧状态只诊断等） | 7 | C | 重写→R3 | |
| 规划前沿有待细化复合 | 1 | D | 经组装建任务 | |
| 计划成员读不回 | 1 | C | 删除 | 本文件 A 类 `test_the_decide_branch_stops_one_mission_on_a_damaged_plan`（换芯后） |
| 完全没有语义绑定 | 1 | C | 重写（补参数） | 并入本文件 A 类 `test_a_damaged_plan_stops_that_mission_through_the_planner_branch` |
| 根审等所有子女、只验收一个不解锁 | 2 | C | 重写→R5 | |
| 子女都验收后解锁、读作 ACCEPTED | 2 | C | 删除 | 【整圈】 |
| 未细化是 PLANNING_READY | 1 | D | 经组装建任务 | |
| 等子女、进组合审阅 | 2 | C | 删除 | 【子目标】 |
| 阶段事件一条不建尝试、不看旧状态 | 2 | C | 重写→R3 | |
| 拒原子占用、RESOLUTION_COMMITTED、每阶段有显示状态 | 3 | E | 保留 / 改 E | |
| 输入：无边取空、生产者没完取空、未知任务损坏 | 3 | C | 重写→R3 | |
| 消费者在等数据；返回 UpstreamInput 形状 | 2 | C | 删除 | `test_progress_acceptance_2b.py` 两条（换芯后） |
| 变异自检元测试（两个参数化函数） | 2 | C | **删除（无目录覆盖）** | 元测试，见证断言与 R3 同 |
| 计划被拒不被吞成已提交 | 1 | C | 删除 | 测的是测试辅助函数；产品对应 `test_h1i_production_entry.py::test_commit_refusal_records_domain_code_and_not_commit_rejected`（换芯后） |
| 事件处理器分支 4 条 | 4 | A | 换芯 | |
| 容忍读取带出错误码 | 1 | C | 删除 | 本文件 `test_the_decide_branch_stops...`（换芯后） |
| 默认读抛错等 5 条 | 5 | C | 重写（补断言） | 补进 `test_the_decide_branch_stops...` |

**这两个文件新写的主循环用例**（`product_world`，参数化）：R1 预算守恒（16 条→1）、R2 被拒计划不留痕（4→1）、R3 提交后读侧（18→1）、R5 根结论前置与原因码（13→1，原因码以保证通道实际给出为准）、R6 完成判定四种拒绝（4→1；`_judge` 删后入口可能移位）、R11 验收重放幂等（3→1）。待裁定：R7 交付回执（6）、R8 撤销后读侧（3）、R10 前提/观测/启动许可（14）、R14 只读子目标共享（11）。另 39 条补进 8 个已有 A 类用例，不新增用例。

### 3. `full_target/test_plan_commits.py`（100）

两条全局事实：①产品计划提交入口 `commit_planning_revision` 先比对预览身份（增量+网络编译哈希、读集哈希），封包后改命令里的增量 / 网络 / 读集一律先被 `PREVIEW_IDENTITY_STALE` 拒，要撞到提交层自己的检查，篡改点得在"编译产物进预览之前"，或改不进哈希的字段；②整数图版本在 src 只读不写（平面残留），管理纪元 `bump_epoch` 在 src 无调用方。

| 组 | 条数 | 类 | 建议 | 覆盖 / 补的断言 |
|---|---|---|---|---|
| 默认就是分层、分层拼写 | 2 | D | 删除 | 【整圈】断言 `semantics_of(mission)==HIERARCHICAL` |
| 默认规格哈希同显式 | 1 | E | 保留 | |
| 未知语义版本在写入前拒 | 1 | D | 改 E | 直接测 `MissionSpec` 抛 ContractError |
| 主体 / 范围 / 未签名 / 空白签发人 | 5 | C | 改 E | `CommitService._authorize` 纯函数，合成 1 个参数化 |
| 同命令两次 | 1 | C | 删除 | `test_h1i_commit_recovery.py::test_i05_exit_after_durable_commit_replays_exactly_without_new_writes`（换芯后） |
| 同命令号换意图冲突 + 对应变异 | 2 | C | 改 E | `CommitPlanCommand.intent_hash` 与 `_replayed_receipt` |
| 任务结束后重放、结束后新命令被拒 | 2 | C | 重写→RW-P1 | |
| 管理纪元两条 | 2 | C | 改 E（**待定**） | 产品无推进方，建议连闸门一起裁定 |
| 整数图版本七条 | 7 | 待裁定 | 先改 E 暂留 | 子代理判平面残留（src 只读不写），但 HTN 补齐计划待定③（真正写上还是由计划修订号取代）未定 |
| 读集：要求改版、目标改契约、做法定义变、做法停用 | 4 | C | 重写→RW-P1 | 封包后提交前改状态，断言 `READ_SET_STALE`、无修订、规划器下一轮被叫 |
| 读集：其余 15 条（观测、验收、义务、授权、支持集、有效性纪元、缺席、一个渠道过期仍报其余） | 15 | C | 改 E | 产品计划编译器只产出要求 / 目标 / 做法三个渠道，其余主循环写不出；直接测 `SemanticReadSetChecker.verify`，与验收侧同类合成一个按渠道参数化的检查器用例 |
| 基于错修订的增量 | 1 | C | 重写→RW-P1 | 先跑金丝雀，执行图参与方可能先以别的码拒 |
| 别修订的证书、网络不含增量、别任务的网络 | 3 | C | 重写→RW-P2 | 若预览先拒，说明提交层这道是冗余闸，交主会话裁定 |
| 成环 + 对应变异 | 2 | C | 删除 | `test_h1h_p02_compiler_cycles.py::test_p02_cycle_refusal_preserves_store_budget_events_and_files`（换芯后） |
| 结构预算装不下 + 对应变异 | 2 | C | 删除 | `test_h1h_preview_compiler_refusal.py::test_p04_real_projection_bound_refusal_keeps_planning_bound_code`（换芯后） |
| 如实申报通过 | 1 | C | 删除 | 【整圈】 |
| 少报成本、父义务付不起 | 2 | C | 重写→RW-P2 | |
| 运行中替换 6 条 | 6 | C | 重写→RW-P3 | 扩 `taskgraph_exec/test_successor_with_taskgraph.py` |
| 点名不属本任务的占用、保留策略下退役做法 | 2 | C | 重写→RW-P2 | |
| 成功提交：成员、语义绑定、做法实例入库 | 3 | C | 删除 | 【整圈】 |
| 数据需求落在新修订 | 1 | C | 删除 | `assurance_exec/test_subgoal_outputs.py::test_a_step_after_the_sub_goal_is_fed_by_the_sub_goals_finalizer_once_the_goal_is_complete`（换芯后） |
| 读集索引、事件一条、载荷规范、回执字段、提交不派发、准入事件写主体与槽位 | 6 | C | 重写→RW-P4 | |
| 写入半段失败全回滚 | 1 | C | 删除 | `taskgraph_exec/test_commit_atomicity.py::test_commit_write_failure_leaves_no_plan_tasks_budget_or_graph_receipt`（换芯后） |
| 两个管理者同时提议 | 1 | C | 重写→RW-P1 | |
| 生效在事务内、失败不留 PREPARED | 2 | C | 重写 | 给 `test_commit_atomicity` 加一个参数 |
| 两步生效变异 | 1 | C | 删除 | 【整圈】 |
| 快照拒两个已采用做法、未采用替代允许 | 2 | C | 改 E | `TaskNetworkSnapshot` 合同 |
| 增量采用证书外实例、第二个采用实例 | 2 | C | 重写→RW-P2 | |
| 静默丢占用、拒绝点名、已取代放行 | 3 | C | 重写→RW-P2 | |
| 首修订无可保留 | 1 | C | 删除 | 【整圈】 |
| commit-ready 6 条拒绝 | 6 | C | 重写→RW-P2 | |
| 细化开口的需求来自采用它的槽位 | 1 | C | 删除 | 【子目标】 |
| 同一义务放弃后可再要、未签名撤回 | 2 | D | 经组装建任务 | |
| 独立开口无授权引用、模型提议不能声称已准入 | 2 | C | 改 E | |
| 提交路径之外无人准入需求（扫源码） | 1 | E | 保留 | |
| 自动变基变异 | 1 | 待裁定 | 同上 | 跟整数图版本闸走 |
| 放过未知主体的读集检查变异 | 1 | C | 重写→RW-P1 | |
| 网络快照类型 | 1 | C | 改 E | |
| 重新落地已存的做法实例 | 1 | C | 重写→RW-P1 | 09-27 真机缺陷，产品可达 |

新写：RW-P1（封包后提交前状态变化 / 重放，8 档，10 条→1）、RW-P2（篡改编译产物或未哈希字段，约 15 档，18 条→1）、RW-P3（并入 `test_successor_with_taskgraph`）、RW-P4（首次提交账本，6 条→1）；另扩 `test_commit_atomicity`。**这个文件的换芯要和 7 个 h1h 文件同步**（它们导入 `_world` 等），否则会误伤门禁证据。

### 4. `full_target/test_resolution_commits.py`（108）

验收与根结论入口前面没有预览身份闸，提交入口处篡改能直接撞到检查，所以伪造类都能写成主循环里的变体（在真实 `accept_review` / `commit_goal_resolution` 外包一层，先逐个提交变体，都被拒后再放行真命令）。

| 组 | 条数 | 类 | 建议 | 覆盖 / 补的断言 |
|---|---|---|---|---|
| 头部类结构自检 | 2 | E | 保留 | |
| 1：未签名、作者不可改、跨范围 | 3 | C | 改 E | `resolution_commits._authorize` 纯函数 |
| 1/1b/7/10：身份先于回执、同命令两次、同号换意图、根命令两次、重放不扫全任务、错类回执冲突、已满足不二次结论、复用回执变异 | 8 | C | 重写→RW-A3 | |
| 1/5：已终结不收新验收、取消挂起挡验收 | 2 | C | 重写→RW-A2 | 审阅调用中取消任务 |
| 2：绑定检查 13 条 | 13 | C | 重写→RW-A1 | 逐变体断言拒绝码、验收数 0 |
| 3：管理纪元两条 | 2 | C | 改 E（**待定**） | 同计划侧 |
| 3/10：要求读过期、跳过读集复核变异 | 2 | C | 重写→RW-A2 | 审阅中用户重新确认要求 |
| 3：范围纪元、支持集、别契约修订、验收已不在 | 4 | C | 改 E | 并入检查器参数化用例 |
| 4/10：见证 5 条 + 用途变异 | 6 | C | 重写→RW-A1 | |
| 5：缺独立性事实不能建命令 | 1 | E | 保留 | |
| 5：独立性值、姿态值 | 2 | C | 改 E | |
| 5：自审、可写候选、返工结论、未认领关键操作 | 4 | C | 改 E | 纯函数 `acceptable()`；E 层 `test_acceptance_rules.py` 已有同名断言，核对一致后删本文件这几条 |
| 5/10：必需检查没跑、默认独立性变异 | 2 | C | 重写→RW-A1 | |
| 6/10：验收不关义务 + 对应变异 | 2 | C | 删除 | 【整圈】：验收若关了义务，根结论以 OBLIGATION_NOT_OPEN 拒、任务不完成 |
| 6/7/1b/7b：账本 8 条 | 8 | C | 重写→RW-L | |
| 7/10/7b：子女验收缺失或过期、退役实例、贡献取自命令变异、取消/取代的义务 | 7 | C | 重写→RW-R2 | |
| 7/8/9：根结论篡改 9 条 | 9 | C | 重写→RW-R1 | |
| 8：交付阶段顺序、失败交付不到阶段 | 2 | E | 保留 | |
| 8/10/8b：交付闸门 14 条 | 14 | C | **待裁定** | 要求书的 `delivery_contract_ref` 在 src 里没有写入方，产品走不到（与 e2e 的 R7 同一件事） |
| 9：事件写失败回滚验收 / 结论与生命周期 | 2 | C | 重写→RW-A4 | |
| 3b：同一检查器、每渠道复核（结构自检） | 2 | E | 保留 | |
| 3b：其余 9 个渠道 | 9 | C | 改 E | 并入检查器参数化用例（两种调用口都跑） |
| 1b：原因码大写下划线 | 2 | C | 改 E | 空库上直接测 |

新写约 5 个：RW-A1+R1（篡改，30 条→1，按变体参数化）、RW-A2（审阅中状态变化，4→1）、RW-R2（根触发器读后状态变化，7→1）、RW-L+A3（账本与重放，16→1）、RW-A4（原子性，2→1）。

### 5. `full_target/test_planner_package_refs_and_fields.py`（73）

| 组 | 条数 | 类 | 建议 | 说明 |
|---|---|---|---|---|
| 第 2 节协议字段等 | 6 | E | 保留 | 桩包 |
| 第 3 节主题键唯一、字段齐、覆盖全部节点 | 3 | D | 经组装建任务后直接组包，合并成 1 | |
| 已提交计划仍出唯一主题键 | 1 | C | 删除 | 【子目标】：第二轮按 subject_key 点名子目标 |
| 第 4 节引用收集（桩包或 `_WideNetwork`） | 37 | E | 保留 | |
| 坏权威行不崩、封包不渲染权威表 | 2 | D | 改 E | |
| 任务引用哈希即绑定摘要 | 5 | D | 合并成 1 | 1216 要对照存储列，必须真入库 |
| 义务权威 | 2 | D | 合并成 1 | 换芯后去掉 method 引用种类或给 `world_factory` 加库内做法 |
| 调用方顺序无关 | 2 | D | 合并成 1 | |
| 引用合法、只四个字段、证明不了就不出 | 4 | D | 合并成 1 | |
| 第 5 节哈希辅助 | 7 | E | 保留 | |
| 用组好的包测哈希辅助 | 1 | D | 改 E | |
| 第 6 节桩包 | 3 | E | 保留 | |

### 6. `full_target/test_htn_deployment_wiring.py`（42）

| 组 | 条数 | 类 | 建议 | 说明 |
|---|---|---|---|---|
| 第 1 节规划世界、能力表、种子做法、未知领域、`seed_env` | 10 | E | 保留 | |
| 证据快照每次从存储读、反向观察不被压过、观察器宕机不写 | 3 | D | 换芯 | 只要任务行与观察行 |
| 第 2 节观察器分派、优先序、工作树读取 | 7 | E | 保留 | |
| 第 3 节 demo 证据轮 10 条、第 5 节 demo 自证 2 条 | 12 | D | 换芯 | 只借任务号与存储 |
| 第 5 节自证 2 条 | 2 | E | 保留 | |
| 写做法上下文描述目标与操作者、每个准则带原文 | 2 | D | 删除 | `assurance_exec/test_planner_chooses_and_proposes.py::test_the_context_for_writing_a_method_carries_the_requirement_text_and_a_fresh_identity`（换芯后） |
| 无权威词汇、发布由系统做的说明、做法宽度上限、不可用能力 | 4 | D | 换芯 | 改用 `loop._new_mode(mission).method_proposal_context(...)` |
| 第 6 节两条 START 通道同时许可、许可写在当时范围周期 | 2 | C | **待裁定** | 前提通道产品走不到 |

### 7. `full_target/test_root_review_coordinator.py`（75）

共用部件（保证通道仍在用）：切包、读状态、当前有效包、过期判断、切包次数上限、根准则、终审许可、`attempt_root_resolution`。只服务旧根审阅员（F）：`request()`、`record_review`、`record_unreadable`、`_child_review`、`refuse_self_contradicting_accept`、`ROOT_REVIEWER`、`HierarchicalRootReviewRejected`。

| 组 | 条数 | 类 | 建议 | 覆盖 / 补的断言 |
|---|---|---|---|---|
| 根准则来自根目标、只判不执行、无准则拒审 | 3 | E | 改 E | 直接构造语义绑定 |
| 默认切包上限 | 1 | E | 保留 | |
| 第 10 节解析器负向 5 条 | 5 | E | 保留 | `parse_critic_verdict` 还被 `operation_outcomes.py:420` 调；若那一路也随旧审阅删，这 5 条随删 |
| 只有被打断的错误码才算打断 | 1 | E | 保留 | |
| 切包前缺包、绑当时要求版本、贡献集等于当前验收、终审许可、锚点形成结论、逐准则复述、义务终结 | 7 | C | 删除 | 【整圈】 |
| RA 终审时序 4 条 | 4 | C | 重写→RA | |
| RB 完成后包形状 7 条 | 7 | C | 重写→RB | 规则 2、3 要在库里补写副本包，属"手写锚点"，见第四节① |
| RC 重切通道 9 条 | 9 | C | 重写→RC（参数化） | |
| RD 切包上限 3 条 | 3 | C | 重写→RD | |
| RE 修复请求了结范围 2 条 | 2 | C | 重写→RE | |
| 自证 3 条（不作废旧包、不数切包、忘记生产者） | 3 | C | 删除 | 守卫已写进 RC/RD/RB |
| 生产者自审不能通过、切包后叶子不能再验收、READY 说明文字 | 3 | C | **删除（无目录覆盖）** | 前者由 E 层 `test_acceptance_rules.py` 钉住；后两者主循环到不了 |
| 答过不再问、驳回只一条修复请求 | 1 | A | 换芯 + 改断言 | |
| 旧请求与旧模板 4、旧写入口 10、`_ask_root_reviewer` 非保证段 7、`_collect_root_review` 原始裁决 5 | 26 | F | 随删 | |

### 8. `full_target/test_root_review_evidence.py`（30）

| 组 | 条数 | 类 | 建议 | 说明 |
|---|---|---|---|---|
| 修复前 C3 包夹具 | 1 | F | 随删（连同 `fixtures/htn/c3_root_review/*.json`） | |
| 摘录 8、叶子判据 2、covered_by/版本说明/提示词/自证 5、旧通道端到端 4 | 19 | F | 随删 | 只读旧 `request()` |
| `criteria_for` 单测、种子做法 JSON、版本号、旧库新库共存 | 4 | E | 保留 | |
| 叶子审阅包只带叶子准则 | 1 | C | 重写，并进 RB | |
| 执行者被告知承担哪些根准则 | 1 | C | 重写，并进本文件 1068 | |
| 执行者上下文包带这个区块 | 1 | C | 删除 | 本文件 `test_a_linked_leaf_is_handed_its_carried_root_criteria_and_an_unlinked_one_is_not`（换芯后） |
| 没审阅员 PASS 就没根结论 | 1 | C | 重写，并进 RA | |
| 这种库上规划用 @2 拒 @1 | 1 | C | 删除 | 同文件 E 类 958 钉住 registry 只给 @2 |
| 链接的叶子拿到根准则 | 1 | A | 换芯 | |

---

## 四、要主会话裁定的事项

**① 产品同形世界里允许哪些测试替身动作**（决定约 80 条重写能不能落地）
- a. 在真实提交入口外"包一层"，先送篡改过的命令变体、确认被拒后再放行真命令（计划侧 RW-P2、验收侧 RW-A1/R1，共约 48 条）。
- b. 在库里手写锚点 / 用 SQL 造损坏或撤销（终审 RB 的规则 2/3、R6 完成判定、R8）。
- c. 直接调存储层的 `bump_epoch` 之类"状态变化"（RC、RW-P1）。
- 这些都不是手工建尝试 / 记结果 / 伪造审阅，但属于测试替身。若不接受，对应断言只能改 E（直接测检查函数）或删。
- 另：计划侧"篡改编译产物"如果被预览身份闸先拒，说明提交层那道检查是冗余兜底，按"同一件事只留一条路径"要么删检查、要么删用例——要先跑一个金丝雀再定。

**② 金额计价（8 条）** `p35/test_tail_and_priced_budget.py` 5、`test_priced_budget_cold_reopen.py` 1、`test_provider_accounting.py` 1、`test_provider_budget_identity.py` 1；另 `tests/integration/test_protocol_failure_usage.py` 有计价断言。
- 10-02 已定"金额计价暂时不做，代码里已有的相关残留在阶段 A 一并删"，产品 `price_table=None`。阶段 A 漏了 `ProviderBudgetGuard` 的价格表分支。
- 建议：A′ 一并删计价准入分支，这 8 条随删（F）；`test_protocol_failure_usage` 去掉计价断言后并入供方记账族。

**③ 动作台账类要"跑到第一版计划提交为止"（D′）** `step07/test_action_ledger.py` 11、`test_action_execution.py` 2、`test_waiting_view.py` 2、`test_human_review.py` 1、`full_target/test_h1h_operation_alias.py` 5（共 21 条）
- 它们不碰六环节，按口径是 D；但候选要挂在叶子任务上，而方案的 D 只装工厂和根、不跑主循环，没有叶子。
- 建议允许 D′：用 `product_world` 跑到第一版计划提交、挂住执行者，再做存储层断言。否则这 21 条只能全按 C 重写。

**④ 代码领域种子做法（7 条待裁定 + 影响面更大）**
- 产品 `domains=()`；评测分层臂删后，代码领域种子库（`code.fix-*` 等）在产品路径上没有调用方。10-02 也定了"跨领域验证暂不做，产品只做桌面"。
- 直接待裁定的：`test_inspect_leaf_patch_input.py` 3、`test_read_only_leaf_policy.py` 2、`test_verify_workspace_inputs.py` 1、`p33/test_p33_result_evidence_gate.py` 1。
- 更大的影响：h1i 种子 `_seed_new_protocol`（38 个导入方，h1h/h1i 门禁证据的底座）、`test_seed_methods.py`（62 条 E）、`test_root_review_evidence.py` 都建在代码领域上。
- 建议：本轮把代码领域当"SDK 测试用规划世界"保留（给 `product_world` 写一个代码领域的 `world_factory`），7 条按 C 重写；删不删代码领域另行裁定，不在 A′ 动。

**⑤ H6 做法评测（4 条）** `full_target/test_h6_oracle_bound_evaluation.py`
- HTN 补齐计划阶段 C3 已定"评测晋级连同表、脚本删掉"。现在为它重写是不必要的测试。
- 建议：A′ 提前删（标偏离：提前随 C3 删）。

**⑥ 计划里另有安排、或产品根本走不到的能力（58 条）**

| 能力 | 条数 | 现状 | 建议 |
|---|---|---|---|
| 前提 / 观测 / 启动许可 | 16（e2e R10 14 + 部署接线第 6 节 2） | 产品 `observers=()`；HTN 补齐计划阶段 D（表二 1a）要接上桌面观察器 | 现在用带观察器的测试 `world_factory` 重写成 1 个参数化主循环用例（阶段 D 后即产品路径），不删 |
| 只读子目标共享 | 11（e2e R14） | 产品世界无可复用类型；阶段 D（表二 2、补全方案 2.4）要补 | 同上，重写成 1 个 |
| 整数图版本闸 | 8（计划提交） | src 只读不写；待定③（写上还是由计划修订号取代）阶段 D 前定 | 先改 E 暂留，待定③定了再处理 |
| 交付合同 / 交付回执闸门 | 20（e2e R7 6 + 验收与根结论 RW-R3 14） | 要求书的 `delivery_contract_ref` 在 src 里没有写入方，`record_delivery_receipt` 只有提交层自己调；计划里没有排期 | 建议列入孤儿扫描，连死分支一起删；若保留，需要一个声明交付合同的测试世界，重写成 1 个 |
| 撤销验收后的读侧 | 3（e2e R8） | src 没有撤销命令，只能手写 SQL | 建议删（偏离：无覆盖）；或接受①b 后重写成 1 个 |
| 两人审批 | 2（`test_action_ledger.py` L3 两条） | 10-02 定不做、残留应删 | 随残留删 |

另：**管理纪元**两条闸门（计划侧 2、验收侧 2，已计在 E 里"改 E 待定"）：待定②已定"`manager_epoch` 在阶段 D 随提示词升版删"。建议 A′ 直接删这 4 条（提前随 D 删），不为它改写。

**⑦ 接缝脚本**（用户 10-03 定"迁到产品同形世界"）
- 有测试调用、必须迁的 7 个：`critic-format-repair`、`evidence-tools`、`executor-check`（`test_a_assurance` 调）；`final-writer`、`four-consumer`、`recovery`（`test_c_assurance` 调）；`root-gate`（`test_v_assurance` 调）。
- 没有任何调用方的 10 个：`check-binding`、`check-use`、`content-review`、`critic-runner`、`review-import`（这 5 个文档串一字不差）、`local-check-pin`、`purpose-builders`、`review-runtime`、`tick-factory`、`validity-accept`。README 自称"仅用于主体编码过程中解决具名接线问题，禁止当产品验收，不承诺皆绿"。
- 建议向用户确认：只迁 7 个，另 10 个删（它们的接线在产品同形整圈里都会走到）；若坚持全迁，约多 3～4 天。

**⑧ 需要另核的产品问题（不是测试问题）**
- 旧根审阅请求（`RootReviewCoordinator.request()`）专门给终审员看用户原话、并把主机路径换成占位符（`test_root_review_user_goal.py` 8 条随删）。**保证通道的终审包有没有做这两件事要核**，没做就是产品缺口。
- `parse_critic_verdict` 还被 `operation_outcomes.py:420` 调用；若操作结果审阅那一路也属旧审阅，`test_root_review_coordinator.py` 第 10 节 5 条 E 跟着删。
- `test_mission_sources.py::test_the_final_judge_binds_its_judgment_view_not_an_attempt`：若"终审裁判"指旧 `judge_mission`，随 `_judge` 删。

**⑨ 覆盖目录本身的更正**（子代理发现，判覆盖时已避开）
- `test_htn_end_to_end.py` 里 6 条其实是读源码 / 替身（E），4 条 `_unassembled` 是 F；`test_root_review_coordinator.py` 第 8、9 节和 `test_root_review_evidence.py` 第 5、6 节的"主循环"用例只测旧根审阅员——这些都不能当"换芯后"的覆盖。

---

## 五、汇总

**按类**（用例数；文件数 = 含该类用例的文件数，一个文件可同时计入几类）

| 类 | 清点内 225 个文件（1695 条） | 其中文件数 | 清点"不受影响"15 个文件（347 条） | 合计（2042 条） |
|---|---|---|---|---|
| A 换芯保留 | 270 | 99 | 0 | 270 |
| B 换原生运行时 | 152 | 24 | 0 | 152 |
| C 重写（原用例） | 433 | 74 | 3 | 436 |
| C 删除 | 120 | 27 | 0 | 120 |
| D 经产品组装建任务 | 133 | 25 | 21 | 154 |
| E 照常保留（含约 95 条改 E） | 402 | 66 | 323 | 725 |
| F 随删除删 | 99 | 21 | 0 | 99 |
| 随接缝迁移 | 7 | 3 | 0 | 7 |
| 待裁定（第四节） | 79 | 14 | 0 | 79 |

按"主类"（条数最多的那类）数文件：A 79、C 重写 61、B 24、E 26、D 14、F 13、待裁定 4、C 删除 3、随接缝 1，另 11 个 0 用例辅助模块。

**预计要重写**：原用例 436 条 → 约 **95 个新主循环用例**（另约 50 处补进已有 A 类用例的断言或参数），按族：

| 族 | 新用例约数 | 主要来源 |
|---|---|---|
| 八个大文件 | 20 | e2e/事件流 R1/R2/R3/R5/R6/R11、计划提交 RW-P1～P4、验收与根结论 5 个、终审 RA～RE |
| 对外操作（【代表用例 3】+ 参数化变体） | 15 | `operation_completion/` 9 个文件、`assurance_exec/test_e_assurance`、h1h 操作 5 个文件、`test_a_assurance` 的操作类 |
| 保证通道审阅 | 13 | 复审 / 问人、审阅回合重试、终审裁决、打回修复、随机小号、证据复查、技能准入 |
| 供方记账（`ProviderBudgetGuard` 与未知用量） | 10 | p35 12 个文件、`test_unknown_usage_upper_bound`、`test_protocol_failure_usage`、`test_settlement_held_not_fatal`、step06 一条 |
| 知识 / 声明（产品执行者模板要求交 claims） | 6 | step04、gap_phase1 |
| 计划 / 修复 / 其他 | 30 | h1h 授权与提交守卫、h4 图修复、根修复库、收尾端口、输入清单、嵌套组合、问题卡、建任务服务、发布来源守卫等 |
| 待裁定若都保留 | +4～6 | R7/R8/R10/R14、部署接线两条通道 |

**建议删除**：C 删除 120 + F 随删 99 = **219 条**；若第四节②⑤⑥按建议走，另删约 37 条（计价 8、H6 4、交付合同 20、撤销 3、两人审批 2），再加管理纪元 4 条。

**A 类换芯难度**：低约 68、中约 175、高约 27（种子手工建尝试 / 伪造结果，实为重写种子：`test_h4_retry_runtime_entry` 6、`test_lease_lost_is_redone` 2、`step02/test_attempt_charge_by_fault` 2、`step02/test_commit_service` 2、`test_progress_acceptance_2b_repair` 2、`test_repair_request_failure_facts` 2、`operation_completion/test_completion_data_dispatch`、`test_completion_pending_stall`、`test_h1i_deferred_repair_resume`、`test_retry_after_regeneration`、`test_planning_request_retry_scope` 1、`p32` 1、`p35/test_admission_collection_runtime` 等）。

**工作量重估的提示**：方案第 3 步原说"分诊表出来后再估"。按上表，第 2 步（换芯 270 条 A + 152 条 B + 共用构造器）和第 3 步（约 95 个新用例 + 50 处补断言 + 154 条 D + 约 95 条改 E + 删 219 条）合起来明显超过原定 A′ 6～9 天。建议按族分批：先换 h1i 种子与 `production_fixture`（h1h/h1i 门禁证据），再做对外操作族（与【代表用例 3】同批），其余族随后。

---

## 六、偏离清单

**h1h 删除（标偏离，理由都是"同一断言已在 h1i / h1h 主循环用例里"；h1i 不删）**

| 用例 | 覆盖它的用例 |
|---|---|
| `test_h1h_commit_guard.py::test_a08_replay_returns_original_receipt_after_revoke_without_new_revision` | `taskgraph_exec/test_commit_atomicity.py::test_committed_reply_replay_after_revoke_is_readonly_and_changed_bytes_conflict`、`test_h1i_decision_replay.py::test_committed_refine_replays_after_grant_revocation_without_new_mutation`（换芯后） |
| `test_h1h_commit_guard.py::test_i06_grant_change_between_preview_and_commit_is_stale` | `test_h1h_authority_matrix.py::test_a04_reply_bound_before_grant_change_is_stale_and_never_rebound`（重写后的参数化用例） |
| `test_h1h_request_binding.py::test_new_protocol_request_binding_uses_the_sealed_package_and_prompt` | `test_h1i_production_entry.py::test_the_planning_request_is_the_package_and_nothing_appended`（换芯后） |
| `test_h1h_request_binding.py::test_new_protocol_unreadable_reply_writes_new_and_legacy_events` | `test_h1i_raw_artifact_collector.py::test_malformed_decode_only_reply_is_unreadable_but_preserves_exact_raw_artifact`、`test_v14_runtime_closure.py::test_v14_malformed_planning_reply_records_unreadable_without_secondary_failure`（换芯后）。**删前核对**两条是否断言了"新旧两种拒绝账都写"，只断言其一就给它补一句，不删 |
| `test_h1h_wiring.py::test_format_retry_carries_the_original_request_id` | `test_h1i_production_entry.py::test_format_retry_keeps_opener_package_and_request_identity`（断言请求行 =（开启者，重试）且请求事实不变） |
| `test_h1h_wiring.py::test_the_retry_moves_the_intent_id_column_to_the_answering_intent` | `test_h1i_production_entry.py::test_malformed_decision_retry_reuses_the_frozen_request_package`（断言 `request_after[4] != opener.intent_id`） |
| `test_h1h_wiring.py::test_the_format_retry_is_bounded_to_one_ask` | `test_planning_request_retry_scope.py::test_later_request_retries_its_own_frozen_package_once`（换芯后） |

**覆盖目录里找不到逐条对应的删除（9 条）**

| 用例 | 理由 |
|---|---|
| `test_htn_end_to_end.py::test_a_revoked_leaf_can_be_accepted_again` | 产品不可达：src 没有撤销命令，撤销后也不再开尝试 |
| `test_htn_end_to_end.py::test_a_compound_goal_is_never_accepted_by_a_review_of_its_own` | 主循环从不对复合目标调叶子验收 |
| `test_hierarchical_event_flow.py` 两条变异自检元测试 | 见证断言与 R3 是同一断言 |
| `test_root_review_coordinator.py` 539 行（切包后叶子不能再验收）、1721 行（READY 说明文字） | 主循环到不了；后者只是措辞 |
| `test_root_review_coordinator.py` 479 行（生产者自审不能通过） | 由目录外的 E 类 `test_acceptance_rules.py` 钉住 |
| `test_root_review_evidence.py` 989 行（这种库上规划用 @2 拒 @1） | 由同文件 E 类 958 行钉住 |
| `test_nested_compound_composition.py::test_the_fixture_really_parks_the_successor_on_waiting_order` | 夹具自检，随夹具消失 |

**提前删除（随后续阶段本来要删的，第四节⑤⑥）**：H6 做法评测 4 条（随 C3）、管理纪元闸门 4 条（随 D）。
