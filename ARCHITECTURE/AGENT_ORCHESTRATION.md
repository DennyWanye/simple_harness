最后更新：2026-10-06 CST（补齐第 2～4 批并行车道，合并中，待发版 SDK `opt.166`）。依据 `plans/2026-09-27-desktop-next/完成度严格评估-2026-10-06/10-第2至4批-车道说明.md`；分车道记录 `第2批-车道G～N-记录.md`。本条随各车道合并逐段补。
- **执行者上下文按分层给父目标与直接上游**（K04，车道 H）：新模块 `orchestrator/worker_context.py`；任务包多 `parent_goal` 段（目标原文、负责的要求原文、做法），`dependencies` 改为数据边生产者的目标、状态、已验收摘要与核对记录编号、交付产物；不再按平面 `dependency_ids`。
- **归因按根结论贡献链**（T10，车道 H）：`observability/traces.py` 从 `GoalResolutionCommitted` 载荷递归读贡献链（`attribution-v2`，新增 `goal_chain`，产物带 `acceptance_id`）；不再沿 `dependency_ids` 合并、不再报集成树冲突。
- **停止条件有读方，补两种停止**（H06，车道 H）：新模块 `orchestrator/stop_conditions.py`；`MissionStopReason.NO_NEW_KNOWLEDGE` / `RESULT_DUPLICATION`；`DeploymentPolicy` 三个默认阈值（5 轮无新知识、重复率 0.5、最少 4 份结果），默认开；达上限先按现有停滞通道问规划器一次，同一版计划了结又没改才停。"规划轮"以一次提交成功的规划决定为界。
- **预算等待与重算分开**（A05，车道 I1）：`assurance_tick` 的 `BudgetError` 走 `wait(reason=BUDGET_WAIT)`，退避 2 秒起翻倍、封顶 60 秒，不计入 `rechecks`、永不转 MANUAL_REQUIRED。
- **通知待办同事务建**（A20，车道 I1）：`request_assured_notification` 发事件的同一事务 `seed` NOTIFY 待办；消费者用同一个 `notification_work_target`，重放合并不冲突。
- **标记不符 / 状态文件缺失进隔离**（A02，车道 I1，原计划 §10.1）：`install_native_root` 已有回执不再写回状态文件；启动段 `root_setup` 进 `try`，`AssuranceError` → 管理模式（不装配、不派发）并记匿名阻塞码。
- **全局预算按月配额**（H11，车道 K 接 Host、车道 P 改按月，用户 2026-10-06 晚定）：全局总账每个自然月一个，编号 `budget:global:YYYY-MM`（本机本地时间，取库时钟），由 `commit_service.global_account_id(at)` 一处算、`is_global_account` 一处判；任务建立时挂到建立当月的总账，用量全记在建立当月；配额改了，当月下一个新任务建立时 `BudgetLedger.set_limits` 跟上。Host 设置 `global_monthly_max_tokens`（默认 20 亿，旧名 `global_max_tokens` 删除不认），`status().global_budget` 报月份、上限、已用、预留、剩余；设置页只读段"本月全局预算"。
- **Host 断线后按序号续读通知**（U04，车道 K）：`notices.py` 记 `last_seq`，`catch_up` 只读新序号；每次 `pending_notices` 续读；前端通道重连后作废旧请求重新拉取。
- **结果不明的模型调用先核对再重发**（A27，车道 K）：`provider_budget_guard.check_unknown_handoff` 重读同一调用键（记录状态 / 对账结论 / http 状态）分五类；结果已知只收集不重问；`resend_record` 写替代的序号与依据。真正不明的仍按用户 2026-09-28 决定（基础设施故障原地重试，A 级 #14）走原门，记录写 `ground: outcome_unconfirmed`。
- **Assurance 原计划 28 条改坏对齐现行清单**（V05，车道 M）：新补 7 条、2 条改绑定、F-M06 标 REMOVED（受管恢复已删）；27 KILLED；`assurance_findings.json` F04/F09/F12 口径如实。
- **隔离只读分支接产品**（A40，车道 I1）：Host 状态 `quarantined`（`available=False`）、`status().assurance_root`、新动词 `mission_assurance_root_diagnostic`；隔离时三个保证读动词答 `ROOT_QUARANTINED`，其余动词不开。界面显示待补。
- **重启恢复协议八步**（H01，车道 J，AER 附件 §恢复；迁移 46 `orchestrator-recovery-protocol-and-sandbox-identity`）：新模块 `orchestrator/recovery_coordinator.py`。启动先拿恢复锁（`recovery_runs`，同一拥有者进程号 + 进程启动时刻），再按序做：核对部署清单、核对数据表版本、收回孤儿子进程、对账运行时账本、恢复待派发意图、重建执行池、核对保证根、放行主循环；每步结果落 `recovery_obligations`。任一步失败不装死：状态写 `DEGRADED_RECOVERY`、记失败步骤，主循环只做只读与通知，不派发。
- **子进程身份与回收材料落库**（H04，车道 J）：`runtime/sandbox.py` 每次起子进程写 `sandbox_executions`（根进程号、进程组、会话号、进程启动时刻、命令、工作目录、所属任务/步骤/尝试）；回收时按"进程号 + 启动时刻"核身份，不符记 `identity_mismatch`、不杀；查不到记 `gone`；无法核验记 `unverifiable`。重启后凭这张表回收，不再凭旧进程号猜。
- **资源占用环的死锁分析与具名停止**（H03，车道 J，原计划 §10.5 / §24.1 第 10 条）：`scheduling/wait_for.py` 从当前网络与意图收集"谁等谁"（`collect_wait_facts`），`has_deadlock` 找环；主循环停滞路径问过规划器、它不改、等待成环时以 `MissionStopReason.DEADLOCK` 停，停机详情带 `wait_for` 事实；无环仍是"没有可派发的工作"。
- **审阅员给黑板读工具**（K01，车道 G，用户 10-06 点名）：审阅员四件工具 = 两件证据工具 + `knowledge_list` / `knowledge_read`（同一个读器，按审阅所在任务范围、只列当前有效）；绑定、交接身份核对、执行池策略同步。提示词 `assurance-review-instructions-v2` 加一段：可自查黑板、查到的不是证据、不能写进 `evidence_ids`。
- **规划器读黑板**（K03，车道 G）：规划包第十二个视图 `views.knowledge`（当前有效的已验证知识 + 审阅员核对过的步骤摘要，最新在前，上限 16，超限计入 `omitted_counts`，放不下先裁它）；不给规划器工具（一轮一个决定的协议不破）。规划器提示词 v27、规划包版本 14。知识行暂不进 `visible_refs`（`ReadItemKind` 无 knowledge，记后续）。
- **读后复核有效性**（K05，车道 G，原计划两道检查的第二道）：`knowledge_read` 正文取出后、交出前再判一次 `knowledge_standing`；不再当前的不返回正文、如实写 `standing`。只做了读工具这一道；执行者上下文推送前过滤记后续。
- **知识记录补三字段**（K02，车道 G，原计划 §11.2 / §25.1 第 5 条；迁移 45 `orchestrator-knowledge-validity-uses-level`）：`validity_interval`（证书签发/失效时刻 + 任务纪元）、`permitted_uses`、`assurance_level`；写方只在审阅确认入库时从使用证书与保证通道取值，取不到为 None（`permitted_uses` 现恒 None：审阅回复/证书都没有这项，记后续）。读工具、知识视图、规划包都展示。
- **停止条件不数系统原地重试**（主会话合并 H 后修）：`stop_conditions.knowledge_streak` 跳过 `decision_origin == system_infrastructure_retry` 的决定——执行者连续结果不明只走"非模型原因失败到上限"那条路，不会把"连续多轮没有新知识"数满去问规划器。用例 `test_batch2_h_stop_conditions.py::test_the_streak_ignores_the_systems_own_infrastructure_retries`。
- **结果重复只在同一版要求之内、且只对"判过且没通过"的结果算**（主会话合并 H 后修，第二处）：`stop_conditions.result_hashes` 给每份结果带上交到时生效的要求版本与"判过且没通过"标记，`duplicate_count` 只把这类结果当比较基准——原计划 §18/§19.1 讲的是搜索原地打转（被判不通过后又交同样的东西）；用户改要求后重做、因改要求落在核查中途而搁置重做的结果交出相同内容，不算。
- **沿用自旧计划的步骤读不到父目标时如实写不可读**（主会话合并 H 后修，第三处）：改要求后沿用的步骤，绑定仍指向旧计划的做法实例，网络快照里查不到（`KeyError`）；原先一路抛到循环边界，整轮派发中断，任务以"没有可派发的工作"停。现在 `worker_context.parent_goal` 返回 `unavailable` 事实，派发照常；处理器的兜底也收 `LookupError`。
- **重启恢复时一个任务对不上只隔离这个任务**（主会话合并 O 后修，AER 附件恢复第 3、8 条"缺件 / 哈希错隔离该流；只为核对可继续的范围开放执行"）：车道 J 原做法是任一任务重建不一致就整个部署降级，一个坏任务挡住所有任务。现在第 3 步把对不上的任务记进 `Orchestrator._recovery_isolated`；按任务干活的每个入口都读同一个判断 `Orchestrator.recovery_isolated`（主循环任务列表、`_mission_round`——含恢复第 4 步重绑意图、在途回合收尾、动作对账——、保证通道四个消费者、执行图通知、迟到用量导入、已结束任务的通知扫描），本进程不替它写任何东西；三处空闲判断（`_has_inflight` 的意图扫描、保证通道与执行图通知的 `has_pending`）也跳过它，否则它留下的在途意图让主循环永不空闲、所有任务的卡死检测失效（终核发现）；库里可读、可取消（取消走提交服务），步骤 DONE、恢复照样 READY，`recovery_status().isolated_missions` 带对不上的表；下次启动重新核对。整步事实不成立（清单对不上、未决核对失败等）仍降级。重放覆盖清单补上迁移 45 给知识表加的三列（合并漏了，恢复第 2 步会因此报清单对不上）。用例 `test_recovery_coordinator.py`（隔离一条断言被隔离任务的事件数、意图状态、保证待办在重启后几轮内都不变；降级一条），改坏"不隔离改回降级""`_mission_round` 不看隔离"都被抓。用户可见见车道 R。
- **审阅员可报全局问题**（F04，车道 Q）：回复第 5 版加 `global_findings`（`severity` + `reason`，≤16 项）；有 BLOCKER 级全局问题时必须项一律判不成立（没有必须项时挂全部准则），这次审阅不能形成"接受"、按返工走，原话随打回交规划器；规则只在 `assurance/checks.global_blocker_targets()` 一处。审阅员提示词 v3、编解码 v5。是不是安全问题由审阅员判断，系统不做关键词匹配。
- **审阅格式修复不再卡在预算等待**（车道 S，复核 7.3 第 1 条证实）：第一次调用格式错且用量说不清时，原来要求先结清——而结清要等收尾、收尾要等这次审阅，成环永久 BUDGET_WAIT。现在 `_require_format_repair` 只看第一次调用的预留仍挂着：它按上限计数（账上可见，收尾 `settle_at_upper_bound` 计入），第二次调用用自己的预留开出来。用例 `assurance_exec/test_format_repair_unknown_usage.py`。
- **降级恢复接到产品**（第 2～4 批评估必须处理第 1 条，H01 的 Host 半边）：Host 每轮 `run()` 之后读 `Orchestrator.recovery_status()`，`DEGRADED_RECOVERY` 时服务进 `degraded_recovery`（`available=False`，原因带失败步骤），不再驱动；`status().recovery` 带 SDK 的只读诊断（八步结果）；新任务、改要求具名拒绝 `orchestration_degraded_recovery`，只读与诊断动词仍开（`handlers.DEGRADED_RECOVERY_READS`）；任务页按既有"编排服务不可用：原因"显示。用例 `backend/tests/orchestration/test_recovery_degraded_host.py`。
- **保证通道读取与清理**（A08/A09/A10/A13/A15/A24，车道 I2，详见 ASSURANCE.md 同日条目）：来源过期码 `SOURCE_NOT_CURRENT`；回执核写者与种类；`ResolvedRef` 解析时核访问、`_permission` 只留 `storage/assurance_reads.py` 一处；完成范围/规格经 OCC 读者；审阅轮次按复审递增（`next_review_round`；第二意见仍是同轮 ordinal 2，待用户定）；死代码清掉（`finalize_assured_mission` 包装、`RootReviewCoordinator.request`、燃料/血缘/去重三组、`versioning.py` 四个函数，`topological` 保留）。
- **八份对外合同进 SDK**（T06/T07，车道 L，原计划附录 E）：`graph/schemas/*.schema.json` 八份 + `graph/view_contracts.py`，`api/taskgraph.py` 三种视图（快照、明细、收敛）经严格编解码返回；收敛视图合同升 v2（`schema_version=2`，加必填 `blocked_notifications`，`jobs[].row_version` 补安全整数上界）；修订记录 `graph/schemas/README.md`。测试侧 `testing/schema_oracle.py` 是 Draft 2020-12 子集核对器，生产代码不引用。
- **改要求时允许改目标**（H19，车道 L）：`requirements_amendment` 接受 `{op: "goal", statement}`——要求书 n+1 版、任务目标文本 CAS 换新、根合同 +1、根陈述与根参数换新、纪元动一次，回执/事件带 `goal: {previous, current}`；计划是否重做仍交规划器（`REQUIREMENTS_UPDATE`）。Host 入口（`chat_tool.py` 的 op 枚举、`service.py` 的条目格式检查）尚未放行 `goal`，记后续。
- **子目标跨版本沿用**（T12，车道 L，偏差单）：现状已是自下而上沿用（叶子重审 → 复合经组合验收按新版重组，叶子不重跑）；条目要的自上而下与"验收身份来自正式审阅"的保证通道口径冲突，未做，待用户定：改条文或另立任务。

最后更新：2026-10-06 CST（严格评估后补齐第 1 批：主循环秩序与收尾正确性，SDK `opt.165`）。依据 `plans/2026-09-27-desktop-next/完成度严格评估-2026-10-06/08-优先补齐.md` 第 1 批；分车道记录在同目录 `第1批-车道A～E-记录.md`。
- **执行图秩序只按类型码**（T01～T03）：共用核对的拒绝改为带码异常 `contracts/error_table.SharingRefused`（22 个 `SharingRefusalCode`，表 `SHARING_PLANNER_CODE` 全集映射到规划器拒绝码：独立需要仍在 / 保留生产者需求缺失 → `COVERAGE_GAP`，其余 → `REUSE_NOT_ALLOWED`）；`event_handler` 冻结候选图那段 `except SharingRefused`，不再读异常文字前缀。原先 11 个 `TASKGRAPH_SHARE_ACTIVE_*` 与两个覆盖缺口码按文字不被接、当库故障原地重试，现在退回规划器。改要求的拒绝码由 `deployment/root.AmendmentRefused` 带在对象上；操作员五个拒绝码改 `storage/store.CodedStoreConflict`，Host 读 `.code` 不切文字。执行图只读接口对外 9 个码登记进 `TaskGraphBoundaryCode`，`api/taskgraph._fail` 先 `classify`。
- **清单绑定按输入版本号各记一行**（T08，迁移 44 `orchestrator-manifest-binding-per-input-revision`）：`input_manifest_bindings` 主键加 `input_binding_revision`；`tg_attempt_identity_guard` 恢复原计划的 `b.input_binding_revision = NEW.input_binding_revision`（迁移 30 放宽成 `<=` 的原因消除）；`taskgraph_attempt_inputs` 读冻结输入按确切版本查。
- **执行图部署验收门**（V12，原计划 §0.3 / §16）：`scripts/acceptance/taskgraph_gate.py` 一次跑数据表守护、执行图产品同形用例、随机序列、12 条定点改坏（M01～M12 全 KILLED），写门报告；`scripts/build/taskgraph_manifest.py validate --gate` 只认同一份源码清单（`source_files_sha256` 相等）的 PASS 报告，把 `taskgraph_acceptance` 写成 `VALIDATED` 并记报告哈希（新字段 `acceptance_evidence`）。读取方 `InstalledHtnWiringAcceptance._read` 核验证据与清单一致、NOT_RUN 不得带证据；Host `service.start` 只认 `VALIDATED`，否则拒绝启动并在部署清单写 `refused: taskgraph_acceptance_not_validated`。发版脚本 `scripts/release_sdk_opt.sh` 在生成清单后跑门、validate，再建轮子；Host 接缝用例在钉版后对着新轮子跑。REAL_MODEL 与 INDEPENDENT_REVIEW 按原计划记 PENDING。
- **收尾正确性**（A01、A04、A06、A07、A17，保证通道）与车道 E 的两处修复（人裁定回执、`BLOCKED_UNKNOWN` 可达）、车道 F 的修复（过期包的终审回复作废）见 ASSURANCE.md 同日条目。空闲判定的假编排器用例补上阶段 B/C/E 新读法（`_unrecovered`、`_handoff_ground_gone`、`_requirements_unconfirmed`）。
- **老红用例**：执行过程边用例补迁移 42 的要求版本列；执行者技能用例改钉当前模板版本；根终审"切包用完"用例改到 2026-10-03 口径（不以通过的根结论收尾）。
- **核心保证测试**（V01～V03）：`product_world/test_effect_unknown_closeout.py`（结果不明不收尾）、`assurance_exec/test_review_findings.py` 补 F01/F02/F03/F08 缺的注入（`assurance_findings.json` 改为实际覆盖）、`full_target/test_terminal_writers_whitelist.py` 语法树扫描全部终态写入点对 34 条白名单。
- **真实模型验收脚本**（V10）：`backend/scripts/assurance_model_scenarios/run.py` 预算只从 `model-scenarios.json` 读，超预算判 FAIL，同局号再跑写新 attempt 不覆盖。

最后更新：2026-10-06 CST（真实模型验收中修掉的缺陷，SDK `opt.164`）。**任务级判定树带着现行资料**：每次尝试的工作区都带登记的资料（`_source_files`），模型写的测试把它们当测试数据；任务判定时另起的整合副本（`integrated_copy`）原来只放产物，`pytest:` 判据在这棵树上全红、任务被判"要求未满足"（编程题 2 局）。现在判定树同样放现行版本的资料（`event_handler._current_source_files`，产物不写在资料根下、不会覆盖）。用例 `T/product_world/test_judgment_tree_sources.py`，改坏 JDG-01。

最后更新：2026-10-05 CST（Assurance 原计划对照后的补改，SDK `opt.162`）。**对外操作在用户改要求之后**：`system_operations.pending_system_operations` 准备申请单前先读操作台账（`_earlier_operation_fact`）——同一任务、同一连接器、同一操作、同一目标已经交出去过、而现行完成映射下还没有申请单进入执行链时，不再准备新申请单：内容相同交结果审查按现行要求重审原事实（`operation_runtime.advance_operation_outcomes`），内容不同交规划器一次（`OperationNotApplied`，`published_under_earlier_requirements`）。结果审查判不通过交规划器一次（`VerifierAcceptanceRejected`，`operation_outcome_rejected`）。停滞判断：物化被拒的申请单不算合法等待（`event_handler._materialization_refusals`，停滞与停机详情里列出）。**任务判定**：`judge_mission` 只请求收尾，不写完成。详见 [ASSURANCE.md](ASSURANCE.md) 顶部。

最后更新：2026-10-05 CST（联合测试真机修补，SDK opt.158）。**时钟水位落库规则；重审的验证记录事件。**
- `orchestrator/assurance_clock.observe_assurance_clock`：最高水位在进程内精确保存（`CommitService._assurance_clock_seen`），库里那一行与回执只在发现回拨、恢复、正常前进累计满 `CLOCK_PERSIST_STEP_MS`（10 秒）时写。原来每前进 1 毫秒写一条（真机两小时 8.6 万条回执、库 162 MB）。重启后以库值为准，最多少记 10 秒。裁决见 `plans/2026-09-27-desktop-next/HTN补齐-阶段F2-联测记录.md`。
- `commit_service.record_verification_layer`：`VerificationLayerRecorded` 的键带要求版本——按新版重审时与旧版一字不差的一层也有自己的事件，从事件重建与库一致。
- 规划器提示词 `planner-hierarchical-v25`：讲“等待”这种决定时写明 payload 的两个必填字段（`wait_for`、`reason`）；守护用例 `tests/orchestrator/product_world/test_planner_prompt_fields.py`。
- `event_handler._resume_planning_services`：本任务有做法送审、结论还没入库（`method_plan_reviews.awaiting`）时不开规划轮；结论入库后再开，别的待处理请求一并交给它（opt.158）。
- 说明：opt.155 是一次空发版（合并没成功就跑了发版脚本，内容与 opt.154 相同），以 opt.156 为准。

（上一条）最后更新：2026-10-05 CST（联合测试真机修补，SDK opt.155）。**时钟水位落库规则；重审的验证记录事件。**
- `orchestrator/assurance_clock.observe_assurance_clock`：最高水位在进程内精确保存（`CommitService._assurance_clock_seen`），库里那一行与回执只在发现回拨、恢复、正常前进累计满 `CLOCK_PERSIST_STEP_MS`（10 秒）时写。原来每前进 1 毫秒写一条（真机两小时 8.6 万条回执、库 162 MB）。重启后以库值为准，最多少记 10 秒。裁决见 `plans/2026-09-27-desktop-next/HTN补齐-阶段F2-联测记录.md`。
- `commit_service.record_verification_layer`：`VerificationLayerRecorded` 的键带要求版本——按新版重审时与旧版一字不差的一层也有自己的事件，从事件重建与库一致。

（上一条）最后更新：2026-10-05 CST（联合测试真机修补，SDK opt.154）。**沿用步骤重审通过后再修复；共用核对没过退回规划器。**
- `SharedGoalEntry.earlier_acceptance_refs`（`taskgraph_plan_sources.eligible_sharing` 填）：一份结果在旧版要求下的验收。`taskgraph_sharing.validate_sharing` 把它们与现行验收同等看待——点名沿用时引的是旧版那条，重审通过后仍是同一次沿用。
- `event_handler._collect_plan_decision`：冻结执行图候选时共用秩序核对没过（`TASKGRAPH_SHARED_*` / `TASKGRAPH_REUSE_*`），记为这份决定被退回（`REUSE_NOT_ALLOWED`，说明里带原代码），不再当一轮故障原地重试。
- Host `orchestration/chat_tool.py`：`mission_start` / `mission_amend` 的说明写明 `file:` 后面只写路径。

（上一条）最后更新：2026-10-05 CST（联合测试修补，SDK opt.153）。**改要求后重审的人裁决：裁决回执的对象是那份结果。**
- `event_handler._ask_carried_ruling`：沿用步骤按新要求重审判不下来时请人裁决，裁决回执（`AssuranceReviewAdjudicated`）的对象写结果编号——内容审阅判的是结果，写新版验收时使用凭证只认对象一致的裁决。人判通过 → 新版验收；判打回 → 修复请求交规划器。
- 验收资产：`tests/orchestrator/acceptance_assets/`（来源表 24 行、崩溃切点 18 行、改坏清单 89 条），执行器 `scripts/acceptance/run_mutations.py`；联测记录 `plans/2026-09-27-desktop-next/HTN补齐-阶段F2-联测记录.md`。

（上一条）最后更新：2026-10-04 CST（TaskGraph 补全第三至六批，SDK opt.152）。**共用、改要求后重审、删钉住、逐步骤预算去向。**
- **点名共用**：选做法 / 继续分解 / 换做法三种决定可带 `reuse {步骤名: 出现编号}`，落在 `RefineOperation.reuse`；系统只核秩序（`plan_preview._named_reuse`、`grounding.named_share_refusal`、`taskgraph_sharing.validate_sharing`）。候选一份来源 `taskgraph_plan_sources.eligible_sharing`，三种状态：在跑、已按现行要求通过、按旧版通过待重审。按签名自动合并、`SharedGoalIndex`、步骤 / 类型上的复用方式、`BIND_EXISTING_GOAL`、`ChildBinding.resolution_ref` 删除。换做法只删只属于被退休做法的边；上下游都共用时不重复声明边。
- **在跑的步骤跨计划版本交结果**：范围除计划版本外没变就按现行范围验收（`OperationCompletionStore.scope_unchanged`，与已验收内容跨版本沿用同一个判断）；步骤已不在现行计划里按"范围过期"归档。
- **改要求后沿用的叶子按新要求重审**（`orchestrator/carried_review.py`、`event_handler._carried_review`）：不建尝试、不调执行者；验证记录、内容审阅记录查找、验收编号都以（结果, 要求版本）为键（迁移 42）。每次重审的结局只有三种：新版验收（`CarriedResultAccepted`）、一条修复请求交规划器（`CARRIED_RESULT_REJECTED`）、等用户裁决；连续 3 次没能得出结论也交规划器。重审在自己的故障边界里。派发细分码 `CARRIED_REVIEW_PENDING` / `CARRIED_RESULT_REJECTED` / `CARRIED_RESULT_NOT_KEPT`。
- **删"钉住旧版本"**：数据边一律跟随现行算数的验收；`SourceRevisionPolicy`、做法里的 `pin`、数据边表的同名列删除（迁移 43）。重做一律是后继步骤。
- **预算去向逐步骤**：`obligation_accounts.step_accounts`，快照 `budget_by_duty[].steps`，Host 诊断 `STEP_FIELDS`，界面可展开。
- 规划器提示词 `planner-hierarchical-v24`。方案第 3.7 版、实施记录、完成评估、阻断核验在 `plans/2026-09-27-desktop-next/TaskGraph-补全-*`。

最后更新：2026-10-04 CST（TaskGraph 补全第一、二批，SDK opt.151 已发 `58d333a2`，Host 钉版 `c52da393`）。**一轮故障只按类型码分类。** `contracts/error_table.py` 新增 `RoundFaultCode` / `RoundFaultHandling` / `ROUND_FAULTS` / `CodedFault` / `round_fault_handling`；`failure_classes.classify_round_fault` 返回 `(CORRUPT|RETRY, 码)`，`MissionRoundFault` 事件带 `code`。带码异常：历史 / 投影 / 尝试输入 / 来源完整性、`StoredResultCorrupt`、`ServiceTurnIdentityMismatch`、`PlanIntegrityError`（码按实例给、构造时须是登记过的）、`NotBoundError`、`KernelUnsupportedError`。`taskgraph_enabled` 删，`require_bound` 是唯一判断。删 `registry.suggest_for` / `suggested_method_refs`、试用次数、`TaskGraphStore.read_active_consumers`。`_plan_integrity_stop` 对"已创建"任务先进规划再停。明细见 `plans/2026-09-27-desktop-next/TaskGraph-补全-实施记录.md`。

（上一条）最后更新：2026-10-04 CST（HTN 一致性补改，SDK opt.150 已发 `63877724`，Host 钉版 `c9b5a17b`）。**对照原始计划补齐 8 处。** `orchestrator/planning_repair_requests.precondition_triggers`：还没开工的原子步骤、最新开工许可说前提为假，记一条证据失效请求（原因 `method_precondition_false`，去重键含前提摘要与纪元）；`recheck_method_instance` 删。`CommitService._note_unresolved_actions`（取消、任务级失败、步骤失败停任务三处）写 `final_report.unresolved_actions`，判定共用 `runtime/operation_reconciliation.action_outcome_unresolved`（对账 `_failed_unproven` 也走它）；`EventHandler._notice_actions_settled_after_stop` 每轮对账后读一次，有结果写 `ActionSettledAfterMissionStopped` 并经保证通道通知，Host `pending_notices` 按通知读出、主对话卡片与主 Agent 上下文写明。`RequirementClass` 只留两类、`Criterion.phase`/`is_required` 删，审阅员判据段写明轻重按原话判。`planner_views.accepted_steps` 规划包与门面快照共用，快照新增 `steps_no_longer_counting`，任务详情显示。Host 诊断费用一节加 `by_duty`；新任务默认 `context_input_tokens=524288`。义务 `authority_ref`、`satisfaction_policy` 删。明细见 `plans/2026-09-27-desktop-next/HTN补齐-实施记录.md`"一致性补改"。

（上一条）最后更新：2026-10-04 CST（HTN 补齐阶段 G，SDK opt.149 已发 `0a3d4981`，Host 钉版 `ee53ca5d`）。**全业务事件重放 v3。** 存储层每个最外层事务提交前按任务写一条 `RowsWritten`（只增表与回执账按主键 + 内容哈希点名；会改的表——业务与全局——记改前 / 改后整行哈希与改后整行；本事务本任务的领域事件）。`observability/business_replay.py`：`verify_mission`（一个通用折叠：接链、逐列精确比对、报"没有领域事件的静默改动"；别的口径建的任务报"范围外"）、`verify_library`（每行恰好点名一次 + 全局表一节）、`verify_execution_ledgers`（编排导入的每条用量在执行库里恰好一条、已知用量一致）。覆盖清单第 4 版（`rebuild` / `owner` / `silent_ok`）；迁移 41 删 7 张死表、全局触发器只唤醒没结束的任务、34 张只增表库层不许改删。旧重放 v2 整个删掉，命令行 `replay`、Host 诊断"重建结果"、界面换成 v3。守护：编排库可写连接只来自存储层（静态扫描）。收尾大语料（产品同形世界整个目录 126 条 + 随机动作 2 组 × 200 步、每 25 步关库重开，带严格审计）找到并修掉 6 处缺陷：验完提交前改要求主循环崩、释放审阅尾部额度没有事件、收敛作业"还在等"空改动、对外动作内容没变也重写、对账读不到证明不留事件，以及驱动重开不等空闲。明细见 `plans/2026-09-27-desktop-next/HTN补齐-实施记录.md` 阶段 G。

最后更新：2026-10-04 CST（HTN 补齐阶段 F1，SDK opt.148，Host 钉版 4508b45a）。**验收资产就位 + 两处小修。** SDK 测试目录 `tests/orchestrator/acceptance_assets/` 三份清单：接缝表 `seams_current.json`（24 行，缺口 0）、崩溃切点 `crash_points.json`（K01～K18，G 与联测共用）、改坏清单 `mutations.json`；守护用例 `test_acceptance_assets.py` 钉住清单与代码对得上。执行器 `scripts/acceptance/run_mutations.py [编号…]`（备份→改→重生成部署清单→只跑绑定用例→恢复核哈希，只认断言失败）；随机动作序列 `tests/orchestrator/product_world/test_random_sequences.py`（`RANDOM_SEQ_SEEDS`/`RANDOM_SEQ_STEPS` 放大）；屏障开销 `scripts/acceptance/measure_method_barrier.py`。新注入点 `before_goal_resolution`/`after_handoff_before_call`/`after_external_effect`。修：检查入账时撞上改要求不再冲垮主循环（验证放下、等新计划）；候选清单与采用闸门共用 `method_plan_reviews.adoption_refusal`，被挡的另列 `not_adoptable`，规划器提示词 v23。明细见 `plans/2026-09-27-desktop-next/HTN补齐-实施记录.md` 阶段 F1。

（上一条）最后更新：2026-10-03 CST（HTN 补齐阶段 C3+摘要，SDK opt.147 待发）。**做法跨任务复用：类型与任务无关（原话与要求只在根绑定上，类型目录哈希各任务相同）；全库做法只当先例——根终审判可复用才晋级，别的任务读目录、用只读决定读原文、写自己的新做法过本任务审阅；明确归因按任务数两次退役；摘要层 = 执行者 `summary` 经审阅员核对忠实。评测晋级与规则摘要删（迁移 40）。** 明细见 `PROJECT_STATUS.md` 顶部与 `plans/2026-09-27-desktop-next/HTN补齐-实施记录.md` 阶段 C3+摘要。

（上一条）最后更新：2026-10-03 CST（HTN 补齐阶段 E，SDK opt.146）。**用户中途改要求：主对话工具 `mission_amend` 一个入口，一次事务写要求新版本、根合同、根义务、纪元与事件；现行要求只有一个来源（最新要求修订）；验收按哪一版通过只在那一版下算数，改后旧计划停派发、按新版重排，已做完的步骤由规划器安排重做（不自动沿用、不自动重跑）。** 明细见 `PROJECT_STATUS.md` 顶部与 `plans/2026-09-27-desktop-next/HTN补齐-实施记录.md` 阶段 E。

（上一条）最后更新：2026-10-03 CST（HTN 补齐阶段 D，SDK opt.145）。**计划提交两层把关：计划修订号 + 读集（含作用域纪元、义务、前提所依据的观察）；整数执行图版本闸门删。** 桌面三样只读观察可作做法前提，同一观察器取最新读数、已知真值翻了才加纪元、世界变动时重读；输入默认跟随上游现行通过版本、`pin` 钉住；两个没有先后的步骤撞同一文件——采用时退回、运行期交规划器；义务账读时推出；删两张支持集合表（迁移 39）；规划器提示词 v20。明细见 `PROJECT_STATUS.md` 顶部与 `plans/2026-09-27-desktop-next/HTN补齐-实施记录.md` 阶段 D。

（上一条）最后更新：2026-10-03 CST（HTN 补齐阶段 C 第 0 条，删金额计价，分支 htn-c0，未发版）。**编排只记 token。** 删 `PriceTable`/`price_table`/`hard_cap_micros`、`Budget.max_cost_micros`、`OperationSpec.cost_micros_ceiling`、`governance/provider_prices.py`；供应方准入去价格比对，身份 `provider-budget-admission-v4`（只接受当前一个）；账本、尾预算、义务账、事件载荷、指标/归因、Host 诊断与详情、前端去金额；迁移 38 删 5 张表 13 个金额列并重建 `assurance_source_obligations_update`。旧编排库启动即失败，需新建编排数据目录。

最后更新：2026-10-03 CST（HTN 补齐阶段 B 第三批第 2 类 + 第 2 条，SDK opt.142）。**人拒绝发布交规划器；改计划卡住有出口。** `system_operations` 对已物化而被人拒绝（动作 REJECTED）的发布：候选字节变了替代重交，没变交 `_ask_planner_after_rejection`——修复请求来源 `RepairTriggerSource.OPERATION_NOT_APPLIED`（事件名 OperationNotApplied），context 带 target / rejection_reason / rejected_by / rejections / remaining；与 `_ask_planner_for_source` 共用 `_ask_planner_once`（同 key 只问一次；规划器提交了决定且没有待答的人工问题仍未解决即停）；拒满 `SOURCE_REPAIR_CAP` 或回应后内容未变以 APPROVAL_REJECTED/`operation_rejected` 停。规划器模板 `planner-hierarchical-v19`；规划包新段 `abandoned_plan_changes`（`planner_views.abandoned_plan_changes_for_planner`，读 TaskGraphConvergenceAbandoned 回执里的 decision_id 与 reason）。`api/taskgraph.convergence` 多 `blocked_notifications`。`TaskGraphConvergenceStore._transition` 在作业进 APPLIED/ABANDONED 时把该作业被挡的 CONVERGE 通知放回 PENDING（否则 `awaiting_sources` 让任务永远等）。Host `taskgraph.operate_taskgraph`（控制通道 `taskgraph.abandon_convergence` / `taskgraph.retry_notification`，经 `taskgraph_operator_api`，命令号每次点击生成）；诊断导出 `diagnostics.taskgraph_history`（临时副本 `replay_taskgraph`，只留报告、历史清单与相邻结构差异）。前端 `PlanChangePanel`、执行图步骤详情 `WhyNotReady`。

最后更新：2026-10-03 CST（HTN 补齐阶段 B 第三批第 1、3 类，SDK opt.141）。**发布没落地的证明与人工裁定。** `contracts/operation_payloads.NonapplicationProofKind` 加 `CONNECTOR_LEDGER_NOT_LINKED`（连接器档案可声明）与 `HUMAN_RULED_NOT_APPLIED`（只能由人工裁定写，冻结效果合约总是接受）；`contracts/operation_reconciliation` 对应两种证明字段与观察来源 `HUMAN_RULING`。`runtime/connectors_publish.FilePublishConnector` 发布在台账排他锁内执行，`ledger_record(key)` 在共享锁下给出该键的台账行与 `final_file_present`；链接返回后出错不写 ABORTED。`runtime/operation_reconciliation_file_publish.FilePublishReconciliationAdapter` 由文件发布档案登记，只在"无意图"或"末行 ABORTED 且目标文件不在/字节不对"时出权威的未生效观察。`runtime/actions.reconcile` 同时扫描失败而无证明的已交接动作（`_failed_unproven`），无运行时返回 None 不计进展；查不到时 `record_reconciliation_unavailable` 计数到非模型失败上限置 needs_human。`action_commits.override_action_outcome` 接受结果不明或失败未证明的动作，判失败时在同一事务写人工裁定证明；`propose_action` 对失败未证明的拒收（`action_outcome_unproven`），已证明未生效的同内容新版本带 `previous_attempt`。`system_operations` 对已物化而证明未生效的头按原计划重交，上限 `SYSTEM_RESUBMIT_CAP=2`，超过以 ACTION_FAILED/publish_not_applied 停。facade `resolve_unknown`；Host 控制通道 `mission_action_resolve`；前端 `ResolveOutcomeBox`。

最后更新：2026-10-03 CST（HTN 补齐阶段 B 第二批，SDK opt.140）。**等待会结束。** `TaskGraphConvergenceStore.release_for_decision` 在决定提交被拒时结束其收敛作业（`TaskGraphConvergenceAdvanced` 带 reason=decision_refused），`taskgraph_resume._ended_by_refusal` 让这一步可重放；`begin_convergence` 先以 superseded_by_new_decision 结束同任务其它活作业。`contracts/error_table.handoff_refusal_transient` 是交接拒绝"暂时性"的唯一登记（目前只有 taskgraph_target_fenced），`operation_runtime` 与 `_decide` 交接处读它。保证审阅意图（kind=plan）由 `_review_call_overdue`/`_end_overdue_review_call` 计时，到期 `assurance_review_collect.abandon_assurance_review` 写 `TURN_FAILED`/`REVIEW_CALL_ABANDONED` 分类与打断回执，交现有 TURN_RETRY 与 `review_exhausted_by_interruption` 路径；`_require_format_repair` 接受无回合引用的作废调用。规划轮在服务阻塞时限内返回"等待"不报进展；`_collect_after_stop` 对永不回来的服务回合到 `_service_blocker_limit` 关为失败。facade `_require_effects_supported` + `BuiltinOperationProfiles.require_effect_supported` 是完成效果能力的唯一条件。

最后更新：2026-10-03 CST（HTN 补齐阶段 B 第一批，SDK opt.139）。**主循环秩序。** `Orchestrator._mission_round` 是"一个任务一轮"的唯一边界：规划前、开始规划、派发、收集、判定、延后规划重试都按任务（派发/收集按意图）包进去；接住异常后回滚、首次记 `MissionRoundFault`、跳过该任务本轮，别的任务照常，下一轮原地重来不重新调模型；`StoreBusy` 与模拟崩溃 `InjectedCrash` 原样抛出。分类只查 `failure_classes.classify_round_fault`：两种完整性错误与历史/输入/来源完整性码当轮停（`_plan_integrity_stop`，散落 8 处已收进边界），其余同一处连续 `NON_MODEL_FAILURE_CAP`=6 轮且 ≥`ROUND_FAULT_MIN_SECONDS`=120 秒以 `MissionStopReason.STORE_FAULT` 停。停任务级联对缺语义绑定的执行图成员不写终止事件、报告 `tasks_without_terminal_record` 点名（`CommitService._terminal_binding` 与终止闸门共用）。收集时先关派发再结账；`accounting_recovery` 每轮重核近 15 分钟内结束的任务，结束超 15 分钟仍不明的预留按上限结清（`ReservationCountedAtUpperBound`，reason=mission_ended_usage_unknown）。Host：任务结束通知 `deskpet/orchestration/notices.py` + 控制消息 `mission_notices`/`mission_notice_ack` + 主 Agent 上下文；诊断导出加 `metrics`。记录见 `plans/2026-09-27-desktop-next/HTN补齐-实施记录.md` 阶段 B。

最后更新：2026-10-03 CST（HTN 补齐阶段 A″，SDK opt.138）。**删离线备份与受管恢复。** 删 `storage/offline_backup.py` 与受管恢复整套协议：根闸门 `assurance/root_gate.py` 只认原生安装回执（`AssuranceRootInstalled`，恢复清单哈希恒空），删恢复后身份、恢复完整性检查、`reauthorize_restored_read` 提交与门面/提交层两个入口、保证通道变更记录的离线搬迁上下文；迁移 37 重建 `assurance_source_artifacts_update` 触发器去掉离线搬迁例外（旧迁移文本不改）。隔离错误码改名 `ROOT_QUARANTINED`。接缝脚本 `root-gate-seam.py` 与验收 V13 改为三项：缺根状态主循环调模型前拒绝、只读隔离无当前授权一律拒读、原生根读取查租户与到期。记录见 `plans/2026-09-27-desktop-next/HTN补齐-实施记录.md`。

最后更新：2026-10-03 CST（HTN 补齐阶段 A′ 完成，SDK opt.137）。

**一条路**：
- `CommitService.create_mission` 在建任务事务里调部署装上的 `_mission_completer`（`UserMissionDeployment._complete`：初始化根 + `enable_taskgraph_contract`）。没装部署报 `MISSION_DEPLOYMENT_UNBOUND`，没装保证通道工厂报 `ASSURANCE_FACTORY_UNBOUND`。
- 执行图未绑定一律具名报 `TASKGRAPH_NOT_BOUND`；只有全局扫描仍跳过未绑定的任务。
- 换做法遇到在跑的工作，走执行图收敛：先取消尝试，等在途那次模型调用结束（取消信号不打断在途模型调用，避免"用量未知"），再提交新版本。旧的"延后决定、冷启动续上"整套已删。

**审阅冷却**：审阅模型因服务商连续报错进入冷却期时，打开审阅转成 `REVIEW_ROUTE_UNAVAILABLE`。终审下一轮再开；做法审阅只计服务故障宽限，不算规划器答错。

**性能**：`TaskGraphStore.read_revision` 以版本链依据行的指纹为缓存键；`validate_sources` 只比本地计划来源。

**原生执行池**：不再创建旧检索器和旧索引泵，后台泵只驱动 ARP 自己的索引与召回。

最后更新：2026-10-03 CST（HTN 补齐阶段 A′ 第 1 步，SDK opt.136）。**部署组装只留 SDK 一份。** 新增 `agent_orchestrator/deployment/`：`native_pools.py`（原生执行池拼装与按尺寸档注册，原 Host `native_plane.py`/`runtime_profile.py`）、`duties.py`（自动确认内容完成、自动授权规划、保证通道检查策略投影，原 Host `service.py`/`assurance.py`）、`root.py`（唯一一份要求书 `user_requirements`、根初始化、开工条件，原 Host `hierarchical.py`；SDK 默认要求书改用它）。Host 只保留桌面规划世界、本机资源（DeepSeek 计数器、BGE-M3、沙箱脚本执行器）和"是否自动模式"。执行池身份由 Host `test_pool_identity_bytes.py` 对搬迁前基准逐字节钉死，数据目录副本启动检查通过。记录见 `plans/2026-09-27-desktop-next/HTN补齐-实施记录.md`。

最后更新：2026-10-03 CST（HTN 补齐阶段 A，SDK opt.135）。**清残留 + 立规矩。** 删老任务迁入执行图（`CAPTURED_BASELINE`，执行图在第一份计划之前绑定，已有计划的任务启用直接拒绝；迁移 34 重写两个守卫触发器、库层拒写迁入来源）；补原 TaskGraph 计划 §10.1 两种辅助事件 `TaskGraphDispatchBound`（派发事务内）与 `TaskGraphConvergenceAdvanced`（收敛状态真正变化时），都不进重查事件集；新增错误码表 `contracts/error_table.py`（§12 九类先后、跨边界码全集登记、未知码拒绝，规划器反馈按类别排序）；删只有定义没人用的：对外操作三种状态与就绪判断"等待未知操作"门、`ReconciliationResult`、`ExecutionFeedbackV1`、`leaf_decision`、`achieve_outcome`、`refine()` 的语义判断部分（保留 `planning_frontier`/`unknown_predicates`/`evidence_requests`）、调度解锁价值打分项、`RecordVersion`/`ValidityRevision`、支持/假设/监督三类边；编码清单重写一次（开发库旧任务执行图历史读不出）。全业务重放 v3 骨架：`observability/business_replay.py` + 覆盖清单（109 张表五类归类、86 张业务表逐表写入口与事件，完全覆盖 55 / 部分 22 / 不发事件 9），守护测试钉住新表新字段，Host 诊断导出新增 `business_replay` 一节。记录见 `plans/2026-09-27-desktop-next/HTN补齐-实施记录.md`。

最后更新：2026-10-02 CST（删旧平面模式第三刀第 4、5 步，SDK opt.134）。**领域档案、提示词、判分各只留一份当前版本；严格引用的程序核对整套删除（用户选 A）。** 领域档案结构 1→2（code 6 / appworld 4 / drone-sim 2，删 `planner_floor`、冲突模板、综合策略、`adapters`），库里按旧结构冻结的任务由主循环开头的门以 `unsupported_domain_profile` 按名停掉；执行者/审阅员/AppWorld 两份提示词改为字面全文（字节与哈希不变），历史版本与文档模板删除；规划器"请求补偿"修复类型删除（规划器提示词 v17、规划包 11）。文档领域的引用定位、逐条评估、inconclusive 重试、doc5 证明、"证据不足"停机、发布前资料现行检查、引用读取接口全部删除，迁移 33 删 `criterion_assessments`（迁移 26 的导入屏障冻结为字面 SQL，校验和不变）。判分对所有领域统一：模型结论最多"有支持"，已验证知识只来自系统测试观察。"严格引用"在新建表单里变成一条交给审阅员判断的要求（必须附资料、按通用任务建）；文档面板换成通用的"参考资料"面板。逐项记录见 `plans/2026-09-27-desktop-next/删旧平面模式-第三刀实施记录.md` 第 4、5 步。

最后更新：2026-10-02 CST（补偿旁路删除，SDK opt.133）。**对外动作只有一条产生路径：已批准操作意图 → 独立审阅 → 系统物化（写操作链接）。** 门面与提交层的 `propose_compensation`（直接建不带链接的补偿动作，分层任务交接必被拒）删除；要撤销已发生的外部效果，由用户在主对话提出、主 Agent 新开任务把撤销操作写进完成要求后走正路。交接一律核对操作链接，被拒的重交接动作停在"结果不明"，只由完整的未执行证明或人工裁决收尾。规划器"请求补偿"修复类型留到第三刀第 4 步与提示词一起删。记录 `plans/2026-09-27-desktop-next/两个遗留问题修复记录.md`。

最后更新：2026-10-02 CST（删旧平面模式第三刀第 1～3、6 步，SDK opt.132）。**编排只剩分层一种模式。** `MissionSpec`、`create_mission`、门面 `create` 三处拒绝 `orchestration_semantics_version="legacy"`（写库前报错）；库里遗留的平面任务在主循环每一轮开头以 `unsupported_orchestration_semantics` 停掉（只停一次，同库分层任务不受影响）。删除：平面图提交与任务提案、任务预算下限、依赖解锁、平面规划包与平面规划器角色、平面分配器（只留按准入记录分配的 `allocate_v2`）、平面合并与验收分支、`uses_completion_protocol`（恒真）、`actions/*.json` 候选检查、`min_task_tokens` 配置、四种平面事件的回放处理、全部平面演示脚本与命令行演示。基准评测：AppWorld 旧对照臂、AgentDojo、ARE 的旧臂与工具通道、档案、模板、知识桥删除，AppWorld 分层臂保留；`memory/blackboard.py` 保留到执行图那一轮。Host 删测试场景开关、四个平面夹具与决策影子。逐项记录见 `plans/2026-09-27-desktop-next/删旧平面模式-第三刀实施记录.md`。

最后更新：2026-10-02 CST（删旧平面模式第二刀，SDK opt.131）。**冲突任务、冲突仲裁、冲突预留、最终独立综合、系统尾池、改图台账整条线删除。** 两条结论矛盾时：双方都标“有争议”并互记对方（`disputed_by` 对称），后来那条不进知识库；已入库的旧知识被反驳时照旧保留、只记反驳者。争议一律从声明本身读出（`verification/conflicts.mission_disputes`），最终报告、门面快照、证据导出都带 `disputes`，不再有冲突表。任务种类只剩 `work`；角色只剩规划器、执行者、审阅者；分层步骤池与平面规划都不再扣“系统预留”；尾预算只剩首次审阅一种。保证通道本地核验输入去掉 `require_synthesis_knowledge`。“交人裁决”只剩收尾判定那一种（选项 met/unmet）。`planning/manager.py` 删除，`inherit_limits` 迁到 `governance/budget_limits.py`，`terminal_task` 迁到 `graph/terminal.py`。迁移 32 删 `conflicts`、`graph_changes` 和两张系统池表（迁移 2、3、15 原文不动）。Host 详情改读 `disputes`；新建任务表单去掉“冲突核对预留”“最终独立综合”。领域档案里的冲突模板、综合默认策略、仲裁判据与角色键留到第三刀。逐项记录见 `plans/2026-09-27-desktop-next/删旧平面模式-第二刀实施记录.md`。

最后更新：2026-10-02 CST（删旧平面模式第一刀·第 5、6 小步，SDK opt.130）。**同一步同时只能有一次尝试；五个执行者变体与角色可见性删除。** 配置去掉“每步并行候选数”（产品一直是 1）：分配器只给没有开放尝试的步骤发 1 次，建尝试时已有开放尝试即拒绝，步骤预算下限 = 一轮 + 它的审阅（不再乘候选数），每次尝试的预留额度不再按候选数平分。“探索者/利用者/简化者/连接者/失败分析者”五个变体只有管理员会给步骤设上，管理员删后不可达：执行步骤按种类取模板（执行者/仲裁者/综合者），按角色裁剪资料的 `context/role_visibility.py` 删除。领域档案里这些角色与管理员的模板键暂留（无人读取），第三刀清理。逐项记录见 `plans/2026-09-27-desktop-next/删旧平面模式-第一刀实施记录.md`。

最后更新：2026-10-02 CST（删旧平面模式第一刀·第 2～4 小步，SDK opt.129）。**管理员改图、片段验证、候选比较与选择轮整条线删除。** 三样都只属于旧平面模式（产品的分层任务上，管理员本来就不开；候选比较要先经晋级批准，上一步已删），删后只剩一条路：执行者回“做不了 / 失败 / 没进展”时只记历史、尝试进入等待重试、按次数再试；检验失败照常重试到次数上限；执行者附带的“建议新任务”不进图——这正是原来“管理员关闭”时的行为。保留并迁出两样：每个带审阅步骤的尾预算修订号（`orchestrator/tail_revision.py`）和每次尝试建立时冻结的执行快照（`orchestrator/attempt_execution.py`，意图里的键改名 `attempt_execution`）。存储追加迁移 31 删 4 张表（迁移 12、14 原文不动，现有库照常打开）。执行图的执行策略文档里每个步骤只剩一份权限输入。部署配置去掉 8 个管理员字段，停机原因去掉“无进展”“管理轮次用尽”。Host 详情页去掉“搜索”块（候选比较、片段、改图历史），新建任务去掉“执行方式”下拉。基准评测：AppWorld 的 F 对照臂（= D 臂 + 管理员改图）现在明确报错，不再能跑。逐项记录见 `plans/2026-09-27-desktop-next/删旧平面模式-第一刀实施记录.md`。

最后更新：2026-10-02 CST（删旧平面模式第一刀·第 1 小步，SDK opt.128）。**策略评测与晋级整条线删除。** 一个编排库只有一个策略版本：建库时按部署配置写入的那一个；每个任务建立时绑定它。之后没有提议、评测、人工批准、晋级、回滚——策略接口（`api/policies.PolicyApi`）与命令行 `policy` 只剩 `list / show / status` 三个只读动作，命令行 `evaluate` 与两个演示（评测、晋级）删除。连带删除：只为评测服务的"评测库 / 钉候选版本"、"消融开关"（配置字段、检验路由里的消融分支、主循环两处读取）、晋级冷却时间配置。候选比较要先有经晋级批准的比较策略才能开，所以它从这一步起已经开不起来，源码留到第 4 小步删；Host 的"批准比较"测试场景（`native_compare.py`）已删。逐项记录见 `plans/2026-09-27-desktop-next/删旧平面模式-第一刀实施记录.md`。

最后更新：2026-10-02 CST（SDK opt.127）。**强杀后端再重启，被打断的那一步原地重做，不再让任务失败。** 真机（`mission-baddf1eb2442858e`）：执行者的模型调用进行中强杀后端，重启后新进程接着跑那一轮，准入守卫拒绝了它——这次尝试的执行权（租约、执行图的当前派发、运行时租约）还记在已经不在了的那个进程名下；主循环把这种拒绝当成"不可重试"，判步骤失败、任务以"运行环境不可用"失败。现在准入守卫给这种情况单独的原因码 `lease_lost`（`runtime/provider_budget_guard.py`），"谁的错"分类表把它算作被打断（`orchestrator/failure_classes.py`），主循环不判停、留给"非模型原因原地重做"那条已有的路（不扣次数，同一步合计 6 次上限）。别的准入拒绝（预算、身份、配置）处理不变。测试：`tests/orchestrator/full_target/test_lease_lost_is_redone.py`、`p35/test_provider_budget_guard.py` 末尾一条、`step02/test_attempt_charge_by_fault.py` 分类表。

最后更新：2026-10-02 CST（SDK opt.126）。**重启打断一次模型调用后的等待从 180 秒缩到 30 秒**（用户决定）：一次调用交出去之后结果不明（服务重启、连接断开）时，主循环等够时限才判它丢失并原地重做；这个时限原来取"多久没进展算卡住"的 180 秒，现在单独封顶 30 秒（`MAX_SERVICE_BLOCKER_SECONDS`）。真正还在进行的调用不走这条路，仍由 180 秒那个时限管。Host 层新增产品同形的脚本化场景（`backend/tests/orchestration/test_layered_scripted_lane.py`，六条：正常完成、审查判返工后重做、结果格式不对原地重做、一直不对到上限停下、调用中取消、调用中重启后接着跑）。

最后更新：2026-10-02 CST（旧分层旁路清理，SDK opt.124）。依据 `plans/2026-09-27-desktop-next/HTN-片0-实施记录.md`"新增欠账 片0-欠1"；逐步记录见同目录 `旧分层旁路清理-实施记录.md`。

**分层任务只有一个世界：带协议绑定、完成范围在计划提交时冻结。**

- **没有"不带协议绑定的分层任务"这条路了**：它生产上早已建不出来（建任务时就绑定），只剩测试夹具在走。五个建世界的测试夹具全部迁到带绑定的世界后，源码里只服务分层任务的模块（分层调度、结论提交、组合审查、最终审查、叶子验收、计划提交与步骤建行、已验收产出、执行图的结果读取与完成来源、执行图接口、做法评估）把"没有绑定"那一支全部删掉。`uses_completion_protocol` 从 104 处/22 个文件减到 35 处/9 个文件，只留在与旧平面模式共用的入口（开尝试、记结果、验收结果、操作申请、选择轮、保证层收尾评估、主循环记账），在那里它区分"分层任务"和"旧平面任务"。
- **现在的规则（都是秩序，不是判断）**：一步算不算完成，读它冻结的完成范围（叶子：已验收的准备 + 已证明的效果；中间目标：记录在案的结论），步骤行上写着"完成"不算；验收一步时判据、校验层、产出人都从已存的结果按冻结范围读回，不取调用方给的；组合审查和最终审查的判据来自冻结范围里钉住的那一版要求，不再自己另发一版；每个叶子的校验策略都带内容审阅层；一步该交哪些端口由它的冻结范围决定（没被下游消费、也没被做法链接的最后一步同样欠自己声明的端口）；还没有任何计划时，没有"已解决的目标"。
- **计划提交只有一个入口**：`HierarchicalDispatch.commit_preview_plan_proposal` 只收"预览编译出的候选 + 它被预览时的准入凭据"，交给 `commit_planning_revision`。旧入口 `apply_plan_proposal` / `compile_proposal`（旧编译壳 + 不带准入的提交 + "过期就重编、最多几次"的循环 + 撤换时复用只读步骤的索引）与预览提交里的"兼容替身"全部删除。`commit_plan_revision` 仍是带准入提交内部调用的事务本体。
- **测试侧**：`tests/orchestrator/full_target/scripted_plans.py` 的 `seed_verified_result` 按生产写入顺序造一条真实的已验证结果；`admitted_plans.py` 用编译器函数编译一处细化并经带准入的入口提交。夹具世界没有开执行图（生产一定开），这是记下的更外一层旧世界，没有动。
- **没动的**：旧平面模式整条线（待用户定）；不走保证通道的旧根审阅员与裁判 Critic；不装执行图的分层夹具世界；管理范围周期（`bump_epoch` 只有测试在调）。
- 验证：相关定向测试、变异、独立核验、真机，见实施记录。

最后更新：2026-10-02 CST（HTN 精简改造 片 D：其余越位，SDK opt.122、opt.123）。依据 `plans/2026-09-27-desktop-next/HTN-后续-方案.md` 第 4 版；逐步记录见同目录 `HTN-片D-实施记录.md`。
- **判停之前先问规划器一次**：`RepairTriggerSource.NO_DISPATCHABLE_WORK`。`Orchestrator._confirm_and_stop_stalled` 在停滞确认之后、判失败之前调用 `planning_repair_requests.request_planner_for_stall`，记一条 `PlanningRepairRequested`（幂等键 `stalled:<任务号>:<计划版本号>`，范围是整个计划），主循环回头再转一轮，由 `_resume_planning_services` 开出规划轮。请求里只有事实：`withheld`（哪些步骤被哪道闸挡住）、`admitted_not_dispatched`、`outstanding_obligations`，以及最终审查 / 组合审查没有结论或被打回时的 `final_review` / `composition_review` / `root_review`。同一版计划问过之后又停在原地才以 `NO_DISPATCHABLE_WORK` 结束，停机报告 `planner_asked` 写明请求编号与规划轮有没有开出来。计划换版本后旧请求由系统了结（`superseded_revision_requests`，与"目标还没有做法"的请求同一处理）。有名字的停机（最终审查修复次数用完）排在前面，不受影响。
- **"审查的回复用完了"两种结局都算**：`_exhausted_reviews` 同时读 `AssuranceReviewFormatExhausted`（回复一直解码不了）与 `AssuranceReviewImportRejected`（第二次回复能解码但不能采用，如引用了没展示的证据）；`_final_review_unreadable_detail` 覆盖最终审查、组合审查、操作结果审查三类。操作结果审查以后一种方式结束时不再被当成合法等待；新做法审阅以后一种方式结束时规划器直接拿到原因。
- **保证通道上文档任务以最终审查的结论为准**（`verification/mission_coverage.py`）：`mission_coverage(..., assured=True)` 对文字要求只做秩序检查——引用能解析、资料当前、声称可用、评估可用、"不确定"附说明——没过记 `FAIL`，否则 `PASS` / `INCONCLUSIVE` / `UNCLAIMED`；不确定占比不再给出 `insufficient`。`document_judgment`（这一条最终算谁的）与 `coverage_objection`（提交时的秩序）是判定、留存判定重看、提交三处共用的一条规则：秩序检查没过 → 不满足（`judge=document_order_check`）；否则取已认证根结论里的评级（`judge=assurance_review`）。`cite:` 要求仍由覆盖结果判。不走保证通道的旧平面模式（`assured=False`）行为不变：字面相等、占比阈值、"证据不足"停机只剩它在用。
- **契约里的文字不再被猜"像不像代码"**：`contracts/semantic_base.reject_executable` 只拒可调用对象；子串表（"update "、"select "、"delete from"……）删除。条件仍必须是结构化节点。该文件的内容哈希登记在 `graph/network_codec_manifest_v5.json`，已同步更新（执行图文档的编解码清单哈希随之变化）。
- 规划器提示词 `planner-hierarchical-v16`（仍只有一份）：补了"没有可派发的工作"请求各字段的说明。
- **Host**：自动模式确认完成要求时，"是不是操作"只认 `action:` 前缀，词表 `_OPERATION_WORDS` 删除（`deskpet/orchestration/service.py`）；设置项 `assurance_profile` 删除，新任务一律走保证通道（`deskpet/orchestration/assurance.py`），状态接口不再返回该字段。前端文档页补三个标签。
- 没有做：SDK 里不走保证通道的旧根审阅员（`_collect_root_review`）与裁判 Critic——几百条 SDK 测试建在"不装保证通道"的世界上，并入旧分层旁路清理；旧平面模式的文档验收口径。
- **一个任务的数据读不了只影响它自己**（opt.123，片 D 真机暴露）：上一条改了登记在执行图文档身份里的源文件，库里旧任务的执行图文档从此读不了（开发期不兼容旧数据）。保证层的收尾评估去读旧任务的执行图时抛出契约错误，原来只接"提交被拒"，异常冲出保证层轮询和主循环，所有任务每一轮都失败。现在 `AssuranceCloseoutConsumer._root_resolution` 把读不回计划（`assurance_tick.MISSION_DATA_ERRORS`：契约错误、计划完整性错误、存储错误）如实记成 `ROOT_NETWORK_UNAVAILABLE`、未就绪；`AssuranceTick.tick` 对每一项工作接住这三类错误，按原有的持久重试上限再看（原因 `MISSION_DATA_UNREADABLE`），别的任务照常。旧文档不迁移、不回落。
- 验证：相关定向测试、变异、独立核验、真机，见实施记录。

最后更新：2026-10-02 CST（HTN 精简改造 片 C：规划包与提示词收口，SDK opt.121）。依据 `plans/2026-09-27-desktop-next/HTN-后续-方案.md` 第 4 版；逐步记录见同目录 `HTN-片C-实施记录.md`。
- **方法合成器角色删除**：提示词 `method-synthesizer-v1`～`v9`、`planning/htn/synthesis.py`、各处角色清单里的名字、前端的角色显示分支全部删除。仍在用的两件事在 `planning/htn/method_proposals.py`：`build_context`（给规划器写做法的材料：真实可用的步骤类型、每条要求的原文、现有做法为什么不适用、不许写的系统字段）与 `admit_proposal`（把规划器已解码的做法交给注册协议，作者固定为模型，最多到试用）。重问反馈、"哪些拒绝值得再问"的分类表随角色一起删。
- **提示词每个分层角色只登记一份**（`runtime/role_templates.py`）：规划器 `planner-hierarchical-v15`、分层执行者 `worker-hierarchical-v5`、根审阅员 `root-reviewer-v5`，都是完整文本，不从历史版本拼接；历史版本、版本表、钉历史哈希的测试删除。配对检查只认"当前规划包版本 + 当前提示词版本"这一对；规划器模板的选用不读部署钉的版本。规划请求末尾原先追加的一段英文"协议提醒"删除——怎么写回复只在提示词里说。平面模式与各领域的提示词版本没有动。
- **"允许哪些决定"只有一张表**：`contracts/planning_decisions.ENABLED_DECISIONS`，由决定类型与修复种类两个枚举直接得出，能解码的就是能执行的。四层叠加的表与"能解码 / 能受理 / 能执行"三个开关删除。`DECLARE_BLOCKED` 与 `REPAIR/ESCALATE` 连类型、载荷、JSON Schema 条目一起删（"卡住了"只有问用户一种说法；"运行环境受阻"保留）。规划授权策略只剩一个版本，允许的种类就是这张表。
- **规划包只组装一层**：`orchestrator/planner_views.read_planner_package` 读一遍库、计划、做法库、证据快照，得到模型看到的每一行；`planning/htn/planner_package.assemble_planner_package`（纯函数）加协议字段、算可引用对象、施加条数上限与 96 KiB 上限并如实标注裁剪。旧收集器生成的映射和"映射 → 视图"的转换层（`planner_package_v1.py`）删除。规划包版本 10，包里 `package_version` 直接是这个整数。
  - 顶层字段：`planning_protocol`、`planning_subjects`、`visible_refs`、`previous_feedback`、`decision_limits`；九个视图 `views`（`goals`、`obligations`、`plans`、`methods`、`facts`、`accepted_results`、`failures`、`capabilities`、`planning_budgets`）；`repair_requests`、`human_answers`；`method_selection`、`method_proposal_contexts`；`sharing_candidates`、`successor_types`、`compensation_candidates`、`evidence_predicates`；`truncated`、`omitted_counts`。
  - 每件事只说一次：目标与步骤只在 `views.goals`（`open` / `adopted_method` / `under_repair` / 任务状态）；做法只在 `views.methods`（引用是与 `visible_refs` 相同的四元组，带步骤、参数、适用性结果、退役记录、审阅情况）；已采用的做法实例与数据绑定只在 `views.plans`；**一步失败的完整记录只在待处理的修复请求里**，`views.failures` 是索引（先列步骤的失败尝试、再列被拒的规划回复，只带各检查层的一句话摘要）。
  - 可引用对象超过 128 条或整包超过 96 KiB 时，先裁可选行（已接受结果 → 失败索引 → 观察 → 做法）并记入 `omitted_counts`；目标、计划、预算放不下才拒绝。
- 顺手：重试预算里恒为 0 的合成次数字段删除，规划预算视图里的 `synthesis_remaining` 改名 `method_proposals_remaining`；计划变更校验去掉两个无人读取的参数。
- 没有做：文档领域按版本号的三个判断（收了以后平面模式文档领域的测试一百多条变红，属于旧平面模式那条线，改动已撤回）；`parse_method_proposal` 与 `<method_proposal>` 标签（源码已无人调用，随旧分层旁路一起清）。
- 验证：相关定向测试、29 项变异、独立核验一轮（1 个阻断问题已修）、真机复验，见实施记录。

最后更新：2026-10-02 凌晨 CST（HTN 精简改造 片 B：中间目标，SDK opt.115～120）。依据 `plans/2026-09-27-desktop-next/HTN-后续-方案.md` 第 4 版与 `中间目标判据-试验结论.md`；逐步记录见同目录 `HTN-片B-实施记录.md`。
- **目标类型带层级，层数上限由类型结构保证**：`TaskTypeSpec.refinement_level`（只有目标类型能有；不带层级的类型正文与哈希不变）。做法注册检查（`planning/htn/registry._check_structure`）：做法的目标类型在第 L 层时，它里面的子目标必须是更深一层的类型，否则以 `UNBOUNDED_RECURSION` 拒收——同层、更浅、没有层级的都不行，互相嵌套出环在注册时就被拒。不另写层数计数器。Host（`deskpet/orchestration/hierarchical.planning_world`）注册 `desktop.user-goal`（0 层）、`desktop.sub-goal-1`、`desktop.sub-goal-2`；两个子目标类型不声明判据，各声明一个 `delivery` 输出端口。
- **交给中间目标的要求保持原编号**：中间目标的类型不声明判据，它负责哪几条要求由上级做法用链接交给它，编号与用户要求相同。注册检查拒收"链接到子目标步骤却换了编号"的做法。
- **中间目标的做法：覆盖完整、归属唯一**（秩序检查，两处同一口径）：提做法时（`planning_method_proposal._coverage_problems`，问题以 `SUBGOAL_COVERAGE` 开头逐条列出）与计划提交时（`planning/htn/completion_scopes.compile_completion_scopes`）。漏了交给它的要求、链接了没交给它的要求、有步骤没落到任何要求上，都拒收。
- **"目标还没有做法"是通用请求的一种触发源**：`RepairTriggerSource.GOAL_UNREFINED`；`planning_repair_requests.open_goal_triggers`（由 `collect_triggers` 每轮调用）在当前计划有目标没有做法时记一条 `PlanningRepairRequested`，幂等键 `open-goals:<计划版本号>`，请求里只有事实（哪几个目标、各是什么类型），影响范围只写"要新做的工作"。规划器在其中任一目标上提交做法即了结；计划到了别的版本，旧版本的请求由系统了结（`SYSTEM_SUPERSEDED`）。同一计划版本只问一次。主循环专用入口 `_refine_open_compounds`、事件 `HierarchicalRefinementRequested`、内存标记 `_refinement_rounds`、`CommitService.record_refinement_requested` 全部删除。
- **写做法的材料**（`HierarchicalDispatch.method_proposal_context`）：`criterion_evidence` 在类型声明的要求之外加上分给这个目标实例的要求（带用户原文）；新增 `subgoal_types`——比这个目标更深一层的子目标类型（类型引用、层级、参数）。规划器提示词（当前这一份，原地修改）补了相应的事实与秩序说明和一个带子目标的完整示例，写明"是否需要中间目标由你判断"。
- **删掉一条过时规则**：计划预检的"可约性"检查（库里没有做法的子目标 → 整份计划结构无效，`DeltaProblemKind.NOT_REDUCIBLE`）。规划器现在可以自己为目标提做法，没有库做法的子目标只是规划的前沿。
- **中间目标的产出接到后续步骤**（opt.120）：子目标类型可声明输出端口；`HierarchicalDispatch.goal_port_outputs` 把**完成的**子目标的端口对到它采用做法的收尾步骤在同名端口上已验收的产出（收尾步骤本身是子目标时再往下找），以别名形式并入 `accepted_outputs` 与 `issue_input_witnesses`——解析、见证、冻结输入沿用原规则，没有第二套数据路径。注册检查：目标类型声明了输出端口，做法必须写收尾步骤且其类型声明同名端口（`PORT_UNAVAILABLE`）。Host 的两个子目标类型声明 `delivery` 端口。一步执行时只铺通过输入端口接进来的上游产出，只排在后面拿不到文件——这是原有规则，提示词里写明了。
- **幂等键带任务号**（opt.118）：`open_goals_key(mission_id, plan_revision)` = `open-goals:<任务号>:<计划版本号>`。事件幂等键全库唯一，只写版本号时同库后来的任务全部撞键（真机第 2、3 局）。
- **中间目标出不了结论时记事件**（opt.116）：`CompositionAcceptanceAssembly.on_deferred` → `CompositionReviewDeferred`（同一目标同一原因只记一次，原因原样写入）。
- **已终止任务的收敛作业不再被唤醒**（opt.117）：`TaskGraphConvergenceWakeups.schedule` 与 `TaskGraphNotifications.has_pending` 都跳过已完成 / 失败 / 取消的任务；作业与围栏原样保留。真机库里一个已取消任务的作业被唤醒一万三千多次，把主循环拖到每轮一两分钟。
- 验证：相关定向测试 141 个文件 2420 通过；变异 27 项全部抓住；独立核验一轮；真机六局，第 6 局完成交付（中间目标 → 做法单独过审 → 组合审阅正式记录 → 中间结论 → 后续步骤其后开工并拿到它的产出）。逐局经过见实施记录。

最后更新：2026-10-01 晚 CST（HTN 精简改造 片 A：编排 Agent 判断 + 自己提做法 + 审阅闸门，SDK opt.114）。依据 `plans/2026-09-27-desktop-next/HTN-后续-方案.md` 第 4 版；逐步记录见同目录 `HTN-片A-实施记录.md`。
- **选做法、提做法都由规划器判断**：程序只按能力与类型把跑不了的做法筛掉（`planning/htn/method_selection.filter_candidates`、`HierarchicalDispatch.method_candidates`），每个还没有做法的目标都开一轮规划器。删掉两条程序代答（只有一个候选时直接替它采用、同一组候选问过一次就回"无变更"）、选择策略档位与选择调用账本、部署配置里的选择策略项。非模型故障的原地重做保留，来源如实标为 `system_infrastructure_retry`（`planning_selection.infrastructure_retry`）。
- **方法合成器的运行路径整条删除**：发起、收回复、重问、合成轮次记录、"跳过规划器"记录、"合成成功多给一次规划机会"全部删掉；"要不要新做法"的四个程序判定（需要做法的目标清单、空规划器跳过、值得合成的拒绝分类、取证是否饱和）一并删掉。做法草案的解码与结构检查（纯函数）保留，规划器这条路在用；合成器提示词模板与角色清单留到片 C。
- **规划器自己提做法**（`PROPOSE_METHOD`，`orchestrator/planning_method_proposal.py`）：准入只做秩序检查——草案是为这次规划的目标写的、编号不撞车、要发布的文件恰好由一个步骤写出、注册协议的结构检查、每个目标最多提 3 次；被拒时逐条列出问题（结构化条目，下一轮在"上次被拒"里逐条看到）。"一步承担 3 个以上文件打回重拆"不再由程序检查，改由新做法审阅员按任务要求判断。写做法的材料（`HierarchicalDispatch.method_proposal_context`）对每个还没有做法的目标都给：每条要求的原文、现有做法各自为什么不适用、可用步骤类型、新做法该用的编号与版本。
- **新做法审阅闸门**（`orchestrator/method_plan_reviews.py`、`PlanCommitsMixin._check_method_reviews`）：计划提交事务里，采用规划器在本任务里提出的做法必须有通过（或人裁决通过）的新做法审阅正式记录，否则以 `METHOD_NOT_AUTHORIZED` 拒绝。"在本任务里提出"按"本任务为这个做法开过新做法审阅"判定（登记与开审在同一事务）；登记表上的"试用"标记库做法也有，不能用来区分。只在保证通道生效。
- **审阅结论才叫醒规划器**：保证通道上"做法已提出"不再叫醒规划器；每轮循环把有了结论的提案记成 `PlanningMethodReviewed`（通过 / 打回带审阅员原话 / 人已裁决 / 没有结论及原因），这条事件是唤醒源。两位审阅员都判不下来 → 问用户裁决（与根终审、组合审阅同一通道与回执）。等审阅期间任务算"在等"。做法库条目带 `review`（审到哪一步、原话、裁决）。
- **根目标的任务行提前建**（`CommitService.materialise_planning_subject`）：新做法审阅以被规划的目标为归属任务，根目标在第一份计划之前没有任务行，开审会被数据库触发器拒绝；开审前建的就是计划提交会建的同一行，之后的提交原样复用。
- **规划次数只有一处判断**（`Orchestrator._planning_rejected`）：答错次数 `max_planning_attempts` 默认 3（自上一次提交成功起，被拒的回答不分种类累计；同一请求的格式重试计入其中；被接受的提做法、问用户不算答错），服务故障宽限 6 次。已有计划的任务只有还欠着规划（有待处理的修复请求、有步骤等重试决定、有目标还没有做法）才因答错次数用完而停，原因 `planning_attempts_exhausted`。不再出现 `planning_format_retry_exhausted`、`repair_planning_exhausted`、`method_synthesis_refused`。
- **"卡住了"只有问用户一种**：`DECLARE_BLOCKED` 与 `REPAIR/ESCALATE` 在启用表里改为只解码不可执行；`REQUEST_HUMAN` 与"运行环境受阻"保留。
- **规划器提示词重写成一份**：`planner-hierarchical-v14`，完整文本、不从旧版本拼接；规划包第 9 版（标签 `planner-package-hierarchical-v11`）只配它。只并入合成器提示词里的通用协议部分（做法字段形状、完整示例、被拒后怎么读问题清单）。保证通道审阅指令加了新做法审阅的判断口径（含拆分粒度）。
- **新测试夹具**：`tests/orchestrator/full_target/assurance_exec/_assured_loop.py`——保证通道 + 真实循环 + 脚本化模型，此前没有任何测试能在保证通道上跑完整规划循环。
- 验证：变异检查见 `.local-test-evidence/2026-10-01/sliceA/mutations/results.txt`；真机记录见实施记录。

最后更新：2026-10-01 下午 CST（HTN 精简改造 片 0：先删先并，SDK opt.113）。依据 `plans/2026-09-27-desktop-next/HTN-后续-方案.md` 第 4 版；逐步记录见同目录 `HTN-片0-实施记录.md`。
- **只剩一种规划协议**：旧的"计划提案"协议整条路径删除（文本解析器、回复收集、修复文本重放、按规划包版本号的分支）。SDK 默认值 = 分层模式 + 当前决定协议；平面模式仍可用但必须在任务规格里明写，平面任务没有规划协议、不写协议绑定。旧协议名在规格构造、请求解析、创建三处都被明确拒绝。库里按旧契约建的分层任务（没有协议绑定行，或绑的不是当前规划包版本）在主循环入口被停掉，停止原因 `unsupported_planning_package`（`Orchestrator._refuse_unsupported_contract` / `planning_protocol_binding.current_planning_protocol`）。
- **修复只有一条路**：根终审打回、只读步骤越权改文件、同一步反复同样失败三种专用修复全部并入通用修复请求（`PlanningRepairRequested`）。Harness 只报告事实，不再替规划器退掉根目标的做法、取消步骤或写结论：
  - 根终审打回（含人裁决打回）：`Orchestrator._request_root_review_repair` 按最终审查的正式记录记一条请求，带审阅员没判通过的**全部**判据与原话、这是第几次、上限；请求范围是整个计划（任何一步上的计划改动都算处理了它）。上限仍是 `max_root_review_repairs`（默认 1），用完且没有还在等规划器的请求时按 `root_review_repairs_exhausted` 停。保证通道上的打回不带"阻断级"标记，旧专用路径在这种打回上什么都不做——这个真机缺陷随之消失。
  - 只读越权：拒收保留（权限），拒收之后的升级删除；`ResultRejected` 走普通失败请求。
  - 步骤失败请求（`planning_repair_requests.collect_triggers`）新增 `occurrence`：这一步失败了几次、连续几次是同一个失败、失败指纹（`step_failure_facts` / `failure_fingerprint`）。
- **规划包**：顶层"被拒做法段"与做法库条目上的两个分原因标记删除；做法库条目改带一个 `rejected_reasons`（该做法在本计划某目标上被采用后又被哪次修复决定退役，原因 = 规划器当时写的理由 + 那次决定处理的请求，`HierarchicalDispatch.retired_methods`），是事实不是禁令。新增 `plan.refined_goals_under_repair`：修复请求所指的已细化目标，按未细化目标同样的形状列出并带当前采用的做法实例（规划器换做法要引用目标参数）。发给后续步骤的最终审查意见只含审阅员原话（`review_feedback`，版本 `final-review-feedback-v2`）。
- **删掉的开关与规则**：配置项"分层修复开关"及其关闭分支（修复恒开；评测模块的"关掉修复"对照臂遇到即报错）；注册检查里的测试关键词规则（判据出现"新增测试"等词就强制要求测试输出端口）。
- **顺手修的两处"异常逃出主循环"**：计划损坏时收集修复触发源不再拖垮循环（按计划完整性停掉该任务）；执行者结果没写必需输出端口时按"结果被拒收"（`completion_inputs_refused`）处理。
- **新增欠账**（片0-欠1）：没有协议绑定的分层任务在代码里是一整套平行旧世界——旧编译/提交入口（`apply_plan_proposal` / `compile_proposal`）与约 100 处按有无协议绑定分支的旧验收路径，生产上已不可达，但约 90 个测试文件的夹具建在其上（通过两个只存在于测试目录的接缝保持运行）。删除它需要先迁移这些夹具，单独立项。
- 验证：变异检查 12 项全部被抓住（`.local-test-evidence/2026-10-01/slice0/mutations/`）；全量回归与真机记录见实施记录。

最后更新：2026-10-01 凌晨 CST（任务级"文档覆盖"裁判：多步任务里没有评估的声称不否决、由有评估的贡献决定，SDK opt.112，`verification/mission_coverage.py`；规划器重复提问沿用已有回答、已答问题一律给规划器看（标 binding_current），SDK opt.111，`planner_views.answered_questions_for_planner` / `PlanningHumanStore.register_reusing_answer` / `event_handler._register_human_question`；同版：最终 / 组合审查包的验收候选引用改按真实验收正文（修订 0 + 正文哈希，`root_review.acceptance_ref(store, id)`），与披露给审阅员的证据一致；收尾前复查"通过的依据还有效吗"：证书按读集逐项重读、真变了收尾不放行并给规划器记证据失效请求，SDK opt.110；中间层的独立组合审阅结果真正用起来：通过 → 形成中间目标结论、判不下来 → 问人、打回 → 修复请求，SDK opt.109——真机在现有方法合成器提示词下到不了中间层，夹具级证明；均见 [ASSURANCE.md](ASSURANCE.md) 顶部；此前审阅判不下来：换新会话复审一次、仍判不下来交给人，SDK opt.104–108；规划器"等待"可以等方法实例 / 目标，SDK opt.103；此前真实模型结构修复走通：资料换版 → 后继步骤 → 第 2 版计划 → 用新版完成交付，SDK opt.94–102；此前架构方案 C：回答规划问题可登记成任务资料，SDK opt.91；此前 09-29 收口：任务执行者用技能、技能准入评估、多任务并发，SDK opt.83–86；结构修复真机补的 5 处，opt.87–90；此前：发布交给系统 + 按谁的错扣次数，SDK opt.66–82；方案 `plans/2026-09-28-system-operations/00-PLAN.md`）。
- **规划器"等待"可以等方法实例 / 目标**（SDK opt.103）：WAIT 能被唤醒的只有步骤（跑完）和已完成的记录；规划器在修复轮常写"等这一步所在的方法实例 / 目标"，此前一律判"没有可等的生产者"拒掉、白花规划次数。现在 `_expand_planning_wait` 先按规划包给出的身份核对（方法实例：已采用、版本 = max(1, 计划版本)、参数摘要；目标：内容哈希），再用 `planning_wait_targets.steps_under` 沿已采用的方法实例往下找叶子步骤，只挑正在跑的去等；`PlanningWaitRegistered.wait_for` 记换算后的步骤，`requested_wait_for` 记规划器原话。下面没有正在跑的步骤仍拒，理由写明。规划器模板不改。
- **真实模型结构修复走通**（收口第 6 项，SDK opt.94–102，2026-09-30）：第 8 局（`mission-d4615e8a9404e5d8`）资料换版 → 规划器给已通过的第一步提后继步骤 → 第 2 版计划 → 后继与第二步重做通过 → 终审 → 交付，产出引用新版资料。途中补的缺陷：①规划器模板 `planner-hierarchical-v13`（v12 + 子结构字段说明 + 两个填好的例子），格式被拒时下一轮带 `previous_feedback`（按字段路径指出错处），`goal_type_ref` 缺唯一可推断的字段时无损补全并记 `autofilled`；②执行图共享检查只拒"独立需要的工作被移除"和"仍存活的消费者还在用"，保留下来重新组合的目标放行；③输入绑定版本允许"不晚于"（迁移 V30）；④换代后的步骤，旧一代的失败不再等"原样重试"批准；⑤执行池实例上限只数没关闭的，编排器定期关闭已结束任务的执行者（只改状态、记录保留）；⑥反复失败后修方法的机会按步骤算（用户 09-30 选 A）；⑦结构修复给被换代目标记的"旧派发作废"标记，在该目标新一代结果提交时清掉（`HtnStore.clear_revoked_generation`；此前没有任何地方清，动到根目标的结构修复全都收不了尾）。证据 `.local-test-evidence/2026-09-30/struct-repair/`，过程记录 `plans/2026-09-27-desktop-next/PLAN-STATUS.md`。
- **资料换版本 / 撤销 → 重新规划**（架构方案 B，SDK opt.92）：`planning_repair_requests.source_change_triggers` 听 `SourceSuperseded` / `SourceRevoked`，只对有证据的受影响对象发一条"证据失效"修复请求——claims 引用了旧版的已通过结果；拿着旧版还在跑的尝试等它跑完再评估（opt.93：验收本来就对照当前资料，当轮发请求只会让规划器回 WAIT 又被修复轮拒绝），评估记录记下拿旧版跑过的尝试；未派发的步骤下次自动拿新版；新登记的资料不发请求；没影响到谁只记 `SourceChangeAssessed`。终审切包事件记 `source_versions_hash`，资料集变了报 `SOURCES_MOVED` 重切（此前终审不看资料版本，带资料的通用任务用旧资料会静默完成）。修复请求新增系统消费出口 `settle_addressed_requests`：影响范围没有新增工作、每个受影响叶子步骤都在请求之后重新验收通过（`AcceptanceCommitted` / `TaskCompleted`）→ `PlanningRepairAddressed(decision_type=SYSTEM_REVALIDATED)`，不再逼规划器开新轮。
- **回答规划问题可登记成任务资料**（架构方案 C，SDK opt.91）：门面 `answer_planning_question` 多一个可选 `attach_as_source`；为真时 `api/planning_answers.py` 在同一事务里回答 + `register_source("sources/answers/<问题id>.md", kind=markdown, trust=untrusted_external)`，幂等键由问题 id 推出，回执带 `source{path, version_hash}`；选项式问题、没有资料根目录的域、资料存储不可用都具名拒绝。资料按每次尝试冻结、只读挂载，所以规划器让那一步原方法重试，新尝试自动挂上这份文件；任务级备注末尾写明路径。不加决定种类、不改规划包、不改模板（"换输入"决定只能绑到步骤产出，绑不到资料）。界面文字回答默认勾选"作为资料附上"。方案与审阅记录：`plans/2026-09-27-desktop-next/架构修改-方案.md`。
- **结构修复真机补的 5 处**（收口第 6 项，SDK opt.87–90）：①系统代办发布提交前先 `ensure_operation_runtime`（只在真要提交时装配；此前进程里没人手动提交过时每轮报"操作运行时不可用"、任务空转）；②规划决定里不确定性的 `affects` 写成一段文字时当一条；③规划器 `DECLARE_BLOCKED` 且没有方法合成可接手时（`event_handler._ask_person_about_blockage`）登记阻塞式人工问题、任务停下等回答，回答后按原路重新规划，待回答期间空闲判定算"等规划"而非卡死；④规划器模板 `planner-hierarchical-v12` = v11 + 修复轮缺外部资料时用 REPAIR/ESCALATE 向人提问（中文、无选项、阻塞），第 8 版包同配 v11/v12，按任务绑定的模板版本选择，已绑 v11 的任务不变；新任务默认 v12；⑤回答规划问题时同一事务追加任务级 `HumanCommentAdded`（`planning_human_store._note_answer_for_workers`，目标为任务），每个执行尝试的反馈都带任务级用户备注，执行者能看到用户给的资料。真实模型五轮：修复请求→提案→问人→回答→再规划都走到过，未走到第 2 版计划收敛；卡点是规划器修复轮写对复杂决定格式的稳定性（证据 `.local-test-evidence/2026-09-29/e3-real/`）。
- **按谁的错扣次数**（第 1 批）：`orchestrator/failure_classes.py` 把尝试失败分成 模型做错 / 格式没写对 / 服务出错 / 被打断 四类；只有模型做错扣任务次数，其余在失败时退还（账本链与 `attempt_count` 同步退，按 `AttemptChargeReleased:<尝试>` 幂等）；同一步非模型失败合计 6 次停下（`non_model_failures_exhausted`）。
- **发布交给系统**（第 2、3 批）：确认页批准的发布效果由系统按 `AUTHORIZED_SLOT` 自动准备申请单（`orchestrator/system_operations.py`），模型不写候选；申请单引用审过的真实文件，理由标 `reason_source=system`，人仍在批准卡片上逐个批准；找不到源文件时先请规划器补步骤（最多 2 次）再明确停下。
- **后台只处理有变化的任务**（第 4 批）：主循环按"本轮开始时的全局非心跳事件游标"判断，只处理有新事件 / 满 10 秒 / 刚创建 / 总时限已到的任务；空闲返回前全量看一遍。真机采样：剩余 CPU 尖峰主要是会话向量索引（onnxruntime），编排主循环约占一核 14%。
- **步骤声明产出文件**（第 5 批）：任务入口（界面与主 Agent 共用）在每条 `action:file_publish.publish:X` 前补 `file:X`；定计划时检查每个要发布文件的 `file:` 要求恰好链接到一个步骤（否则退回合成器重写一次）；系统发布以声明产出者为锚、在它及其下游取路径完全相同的最下游一版；未被链接的步骤不再兜底承担别人的写文件要求。
- **审阅不再交白卷**：审阅循环上限 = 查看上限 32 + 余量 8，剩余 ≤8 次时查看结果附"尽快作答"，查满后工具拒绝并要求立即作答。
- **重启打断后不再挂住**（opt.74～76）：执行者一轮挂在"工具结果未知"（执行层上报工具阻塞时带上工具操作的持久状态，只认 `unknown`，正在执行的工具不误伤）与"模型调用结果未知"同样处理（等满时限按被打断放弃、不扣次数、换新尝试）；单轮墙钟超时与执行层内部异常（`base_agent_driver_exception`，重启后调用已交出、结果未知）归为被打断（原地重做、不问规划器）；发布结果审阅重试用完后不再算合法等待，交给卡死检测停下并在停止说明写 `operation_outcome_review`。
- **主 Agent 入口**：`mission_start` 结果在主对话（`PrimaryChatView`）里显示为任务卡片（进度、完成要求确认、发布批准，走任务页同一连接与消息，人亲手点）；新增只读工具 `mission_status`（进度 / 等谁 / 已发布 / 停止原因，不能代批）。确认完成要求默认预填（普通要求算内容、每条发布各配一个"哈希一致"效果），一般一次点击。
- **主 Agent 的资料与任务列表（2026-10-05，opt.160）**：`mission_start` 可带 `sources`（走任务页同一个"任务 + 资料"原子批次）；`mission_list` 列最近任务；`mission_source_update` 先核对 `mission_status` 给出的现行版本号再提交替换，变更生成一条等人批准的申请，对话任务卡片可直接批准。资料换版后的处理见 `orchestrator/planning_repair_requests.py::source_change_triggers`：挂旧版的已通过步骤连同差异交规划器判，`NO_CHANGE` 可了结，终审经 `source_change_open` 等这件事问完；审查包 `source_versions` 一节与现行版资料证据让审阅员按现行版本判。
- **合成器看得懂每条要求**（opt.77、opt.80）：方法合成请求的 `criterion_evidence` 原先给每个 `c-user-N` 配同一句总目标，模型只能按描述猜编号（真机把"写出 README.md"挂到写模块那步）；现在每个编号带任务要求里自己的原文（`HierarchicalDispatch.synthesis_request`）。`file:`/`action:` 原文后附谁负责（`synthesis_statement`：操作由系统在内容通过后执行、不设步骤、证据要求不写发布目录/落点；写文件在工作区写出即满足），只改请求数据、不改提示模板。
- **接力步骤交出全部文件**（opt.78）：输入与输出端口相同的续写步骤（如 `desktop.continue-delivery`）把通过核验的全部文件交给下一步（`overlay_attempt_inputs`，操作申请单除外）；此前只交端口文件 + 原工作区文件 + 测试文件，一步写三个文件时新写的模块被丢。
- **端口规则只有一条**（opt.79）：完成协议下"这一步有哪些输出端口"，接受侧（`read_review_origin` → `declared_output_ports(own_ports=True)`）与核对侧（`output_ports_in_revision`）都算上这一步自己声明的端口；此前没下游、没被链接的最后一步两边算法不同，验收永远被拒。
- **真机验收**：带重启的完整走通 = 第九局（任务页）；从主对话发起、对话里一次点击确认、卡片两次点击批准到完成 = 第十四局（两份发布逐字节一致，两步各一次通过）。第六～十三局各暴露并修掉一处缺陷，记录见方案 G 节。
- **卡住交给规划器**（opt.81）：步骤如实报告卡住 / 失败 / 没进展（`OutcomeRecorded`）与结果被拒、验证失败一样生成规划修补请求（`planning_repair_requests.collect_triggers`，带步骤原话），由规划器决定重排、补步骤或重试；此前没人问规划器，几秒后判"没有可派发的工作"整局失败。
- **被打断的审阅补一次机会**（opt.81）：审阅协议每个审阅只准调用 2 次。采集时若这次调用没提交、且原因不是审阅员的错（被打断：重启/墙钟超时；服务出错：`provider_*`、工具调用解析失败——opt.82，与执行尝试扣次规则一致），另记 `AssuranceReviewTurnInterrupted`（`failure_classes.record_review_interruption`，不改已有回执）。用完且第 2 次是被打断的：整局最终审查把 `REVIEW_INTERRUPTED` 作为包过期原因重切新包（新审阅，仍受每版切包上限）；发布结果审阅准备一次重审（清单加 `review_retake`，新审阅包与审阅编号，同一份回执；`outcome_retake_due` 只允许一次，重审再用完才停，`outcome_exhaustion_is_final`）。独立审阅无阻断项。
- 已知后续：规划器向人提问时的措辞可能把"系统发布"误说成步骤在发布；重审的准备工作若每轮都失败会一直算合法等待（与首次准备失败同一既有行为）。

最后更新：2026-09-28 CST（NEXT-TG-1.0 第三～五批，SDK opt.56–59）。
- **执行过程只读接口**（第三批）：SDK `api/taskgraph.py` 新增 `execution_snapshot`（严格执行图 + 执行过程同一读取时点；键集分页，游标绑定任务/调用者/计划版本/清单哈希/执行内容哈希，变了报 `SNAPSHOT_CHANGED`）与 `execution_detail`（按执行意图所在执行池精确读回合记录；白名单只出模型可见原话、整形后的工具事实、提交摘要、审阅结论理由，全部脱敏）。投影 `orchestrator/taskgraph_execution_view.py`：尝试/检查/审阅/规划/修补请求/计划修订/操作节点与 attempt_of、rework_of、review_of 等因果边，全用记录下来的身份连接，不进调度图。Host 控制通道 `taskgraph.execution_snapshot/detail`；直读 SDK 表的 `live_graph.py` 与 `mission_live_graph`/`mission_planning_decisions` 已删除。
- **产品入口**（第四批）：工具熔断与自动续跑与监工开关解耦（`[self_healing]`，监工关时用按失败原因的固定提示，会话活动记录总是注册）；删 7 个无人读取的配置；设置页「任务发布目录」（`orchestration_publish_dir_get/set`，校验后写回 config.toml 并重启编排服务）；主 Agent 工具 `mission_start`（同任务页的创建路径，幂等键由对话回合+调用派生）；前台运行没有执行入口的委派工具不再放进模型可见目录。并发上限、模型路由、多候选/冲突仲裁保持默认，原因与欠项见进度文件。
- **任务模式 Agent**（第五批 A）：编排执行池的 ARP 配置改为 MISSION（配置修订 2；修订 1 行原样保留，旧会话照常）。每个 Agent 由派发意图的按角色来源集创建（SDK `runtime/mission_sources.py` 只读编排器记录：执行者的冻结输入清单身份、审阅包、终判视图、规划/方法合成写明没有执行尝试），缺失或不符具名拒绝；来源哈希进创建命令哈希；每次新请求冻结前复核并钉进上下文清单；创建被拒只停该意图，不打断编排循环。
- **统一技能目录**（第五批 B）：SDK `arp/shared_catalogue.py`，256K 非思考池为唯一权威，其余原生池镜像（同一技能/版本/哈希/正式验收），成员池拒绝直接写，每次使用先问所有者（一次暂停所有池下一次使用即生效），缺工具的池该技能不可用；Host `skill_catalogue.py` 提供总览/本地安装/暂停恢复退役，控制通道只放行 `agent_skill_request` 与两条评估动词。**任务执行者用技能**（2026-09-29，SDK opt.83）：部署策略 `skill_tools` 声明原生池提供的三件技能工具（`skill_discover`/`skill_load`/`skill_execute`），执行者层级模板 v5（v4 原字节保留）列出它们，经原有"任务∩步骤∩角色∩部署"交集冻结进请求；旧式池不冻结。**准入评估**（SDK opt.84）：试用中的技能只能被评估派发链接指向的那个评估任务使用（每次使用复核，过期/别的任务/暂停都拒）；Host `skill_catalogue.evaluate` 一次完成"开始试用 + 以评估键建评估任务 + 挂到根步骤 `desktop-root-<任务>`"，`admit` 取根步骤的根解析证书（内容类任务，opt.86）或步骤验收证书交 SDK 核对；设置页有开始评估/准入/重新评估/刷新。**多任务并发**（opt.85）：模型调用准入身份 v3 不含名额数，旧 v2 身份只差名额数时照认；Host 默认并发 2。**方法合成宽限**（opt.85）：没回复的回合不占询问次数，宽限 6 次。
- **收尾不被被打断的审阅卡住**（第六批，SDK opt.60–61）：审阅模型调用被强制退出打断后，该审阅记为等待原调用核对、不重发；若它的费用未知，它不再阻塞已判定任务的收尾（`assurance_consumers._drain_decision`），费用按上限计入，意图仍交原核对流程。全量回归无新增失败，见 `.local-test-evidence/2026-09-28/batch6/REGRESSION.md`。
- 进度、证据与欠项：`plans/2026-09-27-desktop-next/PLAN-STATUS.md`。

最后更新：2026-09-28 CST（NEXT-TG-1.0 第二批 B：推进规则，SDK opt.53–55）。
- `orchestrator/progress.py`（纯函数）：`idle_verdict(IdleFacts)` 把空闲任务判成带具名唤醒来源的等待（收尾中、根目标已解决待判定、在跑、动作结果核对中、待人批准、操作结果待定、保障层待办、修复续接等待、规划等待、执行图来源等待）或"卡住候选"；归不了类的一律"等待 + 需系统诊断"。卡住记录与卡住确认共用它（确认仍只读一次计划）。
- 主循环 `run()`：`set_between_cycles(duty, every_seconds)` 让宿主职责在轮与轮之间按间隔执行（Host 注册自动授权/策略批准/执行图启用/自动确认，间隔 2 秒）；`durable_watermark()`（非观察类事件序号 + 意图/尝试/结果行）识别"声称有进展但无持久变化"的轮次——仍计入轮次上限，但必须睡；等待分支无写入时从轮询间隔翻倍退避到 1 秒。Host 两轮水位相同则用 20 秒长节拍。
- 其他：规划在途不计操作提案/结果审查回合；池冷却延期规划存 `scheduler_state`（重启不丢）；重规划复用同一方法实例具名拒绝 `REPAIR_NOT_ALLOWED`；审阅格式修复反馈逐码写明规则；终审回复不可读的停止写明 `final_review.reason`；执行图终态事件与记录同事务。
- 验证：定向测试（含 §7.3 验收用例 7 个）无新增失败；真机内容任务完成、无卡住误报，后台 CPU 由约 100% 降到约 10%。进度与遗留：`plans/2026-09-27-desktop-next/PLAN-STATUS.md`「第二批 2B」。

最后更新：2026-09-27 CST（保温杯任务真机跑通；收尾按上限计入、拆步打回；复杂编排跑通）。

**收尾与拆步两项用户决定（2026-09-26～27，SDK opt.26～opt.32）**：
- **审阅也只看这一步负责的要求**（opt.32）：只改执行侧后，第一步只写了自己的文件，但审阅仍按 5 份文件审——桌面步骤类型声明“覆盖全部根要求”，编译每步完成范围时把这份声明并进了审阅清单，其余 4 份判“未知”，结论永远是“无法判定”，同一步验证失败 6 次任务失败。现在被方法链接到要求的步骤只按链接审（`planning/htn/completion_scopes.py`），5 条根要求留在顶层总任务上最后统一审。测试 `operation_completion/test_completion_scope_compiler.py` 新增 1 个。
- **重启后遗留名额释放**（opt.31）：退出时仍在线路上的请求，重启后所属运行已不在当前进程，却一直占着唯一的模型名额，新任务第一个请求排队到超时、任务直接失败。恢复检查里把这种请求标为“线路已断”：释放名额，费用仍按未知保留（`runtime/provider_budget_guard.py` 的 `_owner_gone`）。测试 `p35/test_provider_budget_guard.py` 新增 1 个。
- **方法合成回合没拿到回复时重问一次**（opt.31）：以前请求失败/超时当场判任务失败；现在与“回复读不懂”一样重问一次。测试 `test_synthesis_rejection_reask.py` 新增 1 个。线路在 01:14～01:40 间歇故障时两次都失败，排查中曾误判为思考模式所致，用户追问后对照实验证实同一请求开思考也能成功。
- **真机结果**：保温杯任务（1 份参考资料、5 份交付物）26 分钟完成，5 步各一次通过、无验证失败，每步只写自己的文件；一笔未知用量按上限计入后收尾完成（`MissionCompleted`、`AssuranceMissionFinalized`），结算约 162 万 token；预算按渠道与按周合计均为 8 万元，与资料一致。编排回归 5483 通过，74 个失败在改动前版本同样失败。
- **每一步只带自己的要求**（opt.30）：真机保温杯任务拆成 5 步，但每一步的任务都带着全部 5 个 file: 检查和整个任务目标，第 1 步只好把 5 份文件全写了（拆步形同虚设，后面每步还得重写一遍）。现在生成步骤时读取方法里“哪条要求由哪一步负责”（`accepted_outputs.owned_criteria`，与审阅用的对应关系同源），被链接到要求的步骤只带自己的要求和文件检查，目标开头写明“本步骤只负责：…；其他文件由其他步骤负责，不要创建或改写”，整个任务目标放在后面供理解上下文（`occurrence_tasks.scoped_goal`）；没有链接任何要求的步骤保持原样。测试 `test_leaf_owns_linked_criteria.py`；编排回归 5479 通过，75 个失败在改动前版本上同样失败。
- **未知用量按上限计入，任务能收尾**（opt.28/29）：内容全部通过、只剩已终止尝试的“用量未知”预留时，收尾判定（`AssuranceCloseoutConsumer._upper_bound_plan`）不再永远停在“清算中”，而是由 `BudgetLedger.settle_at_upper_bound` 按“预留额与已知用量取较大者”结清（只多算不少算，用量事实本身仍记为未知），逐笔记 `ReservationCountedAtUpperBound` 后再写最终状态。任务在新代码下恢复时（`PolicyInterpreterDrift`）会重算一次收尾，旧版本留下的卡住任务也能结束。真机：保温杯任务卡在清算中的 57,024 token 按上限计入后 `MissionCompleted`。测试 `tests/orchestrator/full_target/test_unknown_usage_upper_bound.py`。
- **拆分过粗打回一次**（opt.27/28）：方法合成提示词 v9（`METHOD_SYNTHESIZER_VERSION`，v8 保留）写明“依赖只表示先后，不是合并理由；每个 file: 条件只由一个步骤产出”。仍有一步承担 3 个及以上 file: 条件时，第一次回复被打回并写明原因（`hierarchical_dispatch._coarse_file_steps`）；每轮最多问两次，所以只在第一次回复检查，第二次原样接受，绝不因此让任务失败。测试 `test_synthesis_granularity.py`。真机：保温杯任务（5 份文件）拆成 5 步。
- **已下发尝试的结果未知时有上限**（opt.26）：保证通道里一次执行尝试卡在“服务商结果未知”超过 180 秒，就把尝试记为丢失、预留保持占用（交给上面的按上限计入），释放后重新派发，不再无限等待。测试 `assurance_exec/test_assured_planner_unknown_bounded.py`。

**一个通用任务 + 可选参考资料**（用户决定 2026-09-26，SDK opt.25，后续由主 Agent 调用）：通用任务配置升到 code-v1 第 5 版（`governance/domains.py` 的 `CODE_PROFILE`），开放 `sources/` 资料目录并允许 `source` 证据；资料走与文档研究相同的机制（`_active_source_binding` 按尝试冻结版本 → `_source_files` 挂进执行者工作区 → `_protected_files` 禁止改写），但不带文档研究的严格引用检查。第 1～4 版原样保留（已有任务冻结的配置不变），无人机仿真配置改派生自第 4 版。调用入口：`mission_create_with_sources`，`mission` 里不写 `domain` 即通用任务，写 `doc-research-v1` 即严格引用模式。资料目录开放后，发布目录与证据存储重叠的安全检查对通用任务同样生效。测试 `tests/orchestrator/p33/test_general_mission_sources.py`；真机：附一份周会纪要的通用任务约 3 分钟完成，待办表与纪要逐条一致。

**纯内容完成要求自动确认**（用户决定 2026-09-26，SDK opt.24）：auto 权限模式下，`OrchestrationService._auto_confirm_content_completion` 每轮循环检查状态为 CREATED 的任务；成功条件里没有 `action:` 且完成要求仍待确认时，按页面同样的“全部必需判据归为内容”映射提交，`approval_source=HOST_AUTO_PERMISSION`，事件记为系统（`host:auto-permission-completion`，写明代谁确认），回执不变；SDK 拒绝 Host 自动确认任何带操作效果的映射。有操作要求或 manual 模式仍保留“确认上述完成要求”按钮。测试：SDK `operation_completion/test_completion_spec_approval.py` 新增 3 个，Host `tests/orchestration/test_auto_confirm_content_completion.py`。真机：重启后停在“已创建”的任务数秒内自动确认并进入规划。

当前 SDK `0.13.0.dev20260925+opt.32`，Host 钉版提交 `5faa739f`。

**结果**：真机用 7 步的“读书会首期筹备方案”任务（6 个交付文件，带依赖）做验证。前七趟各暴露一个新缺陷，逐个修复后，第八趟从规划走到 `MissionCompleted`（`verification_passed`）：
- 用时约 31 分钟，花费约 230 万 token；
- 6 步都一次做成，审阅员格式错误 5 次，全部在重试后恢复。

记录在 `.local-test-evidence/2026-09-26/live-view/bookclub*-watch.log`。

**今天的修复（SDK opt.16～opt.23，每处都带“修前失败、修后通过”的测试）**

1. **编排循环空转**（opt.16/opt.17）
   - 现象：任务在等人确认完成要求时，`_start_planning` 什么也没做却报“有进展”，循环不休眠，后台 CPU 100%，每秒写 18 条时钟回执，启动要 2 分钟。
   - 修法：该函数如实返回是否真的开始了规划。另加 `commit_receipts(kind)` 索引（迁移 28）。
   - 测试：`test_idle_cycle_does_not_spin.py`。
2. **方法合成提示词 v8**（opt.17）
   - 多个独立交付物各成一步，步骤名可读，写明依赖顺序，汇总放最后；简单目标仍拆 1～2 步。
   - v7 保留注册。
3. **规划次数按每个问题单独计数**（opt.18）
   - 修法：`event_handler._planning_attempts` 只数上一次提交决定之后被拒的次数，原先是整个任务累计，默认只有 2 次。
   - 兜底：任务额度、整体终审修复上限、卡死检查。
   - 测试：`test_planning_bound_per_round.py`。
4. **规划器回复的枚举值不区分大小写**（opt.18）
   - 修法：`planning_decisions.py` 里的本地 `enum_of`，`"high"` 按 `HIGH` 接收，规范化哈希不变。
5. **工具参数 JSON 坏了自动重发**（opt.18）
   - 触发条件：`finish_reason=tool_calls`，但工具参数不是合法 JSON（DeepSeek 约每 100 次工具调用出现 1 次）。
   - 修法：`simple_harness/agents/execution.py` 用同样的输出上限原样重发，与截断重试共用 `empty_response_retries`，默认 2 次。
   - 保证：每次调用都结算记账，坏掉的调用绝不执行。
   - 测试：`test_tool_output_length_recovery.py`、`test_protocol_failure_usage.py`。
6. **审阅员输入清单的解码上限**（opt.18）
   - 修法：`assurance_review_import.py` 两处改用 `MAX_RECORD_BYTES`（8MB）。
   - 原因：280KB 的审阅员对话撞上 256KB 的 JSON 上限，32 次复核后被转成“需人工处理”。
   - 测试：`test_review_import_large_manifest.py`。
7. **空 code_test 的说明文字**（opt.19）
   - 修法：没人点名、也收集不到测试的全目录运行，检查结果写“不适用、视为满足、不作为无法下结论的理由”；判定逻辑不变。
   - 原因：审阅员把原来的“无可证明内容”读成“必过检查什么也没证明”，两次判无法下结论，把一步额度烧光。
8. **卡死检查豁免排队中的审阅工作**（opt.20/opt.22）
   - `_has_pending_assurance_work` 在以下两种情况下，不判“无可派发工作”：
     - 保证审阅队列里还有未完成的工作（已转“需人工处理”的除外）；
     - 审阅结果类事件（`AssuranceReviewClassified`/`FormatRejected`）已经出现，但 REVIEW 读进度还没读到。
   - 周期性的保证事件不算进去，所以真卡死的任务最多约 300 秒后照常判定。
   - 测试：`test_stall_waits_for_assurance_work.py`。
9. **审阅回复可解析但无法导入时重问一次**（opt.21）
   - 适用错误：`UNEXPOSED_EVIDENCE`、`DUPLICATE_CRITERION`、`FINDING_SCOPE`、`MANDATORY_CRITERIA_INVALID`，只限第一次调用。
   - 修法：写 `AssuranceReviewInterpretationRejected` 回执（`classification=INTERPRETATION_INVALID`），走格式修复同一通道发起第二次调用，并在请求里附上具体改法（例如“只引用 complete=true 读过的标签”）。
   - 第二次仍错才终拒；`POLICY_CATALOGUE_MISMATCH` 仍直接终拒。
   - 场景脚本：`scripts/assurance_seams/evidence-tools-seam.py` 的“再错即终拒 / 改正即通过”两种情况。
10. **规划类回复丢弃多余字段**（opt.23，用户决定）
    - 修法：新文件 `planning/unknown_fields.py` 的 `decode_dropping_unknown`，按拒绝信息点名的位置删掉多余字段后重新解码，最多 16 次；位置不明时只删唯一的持有者，否则照旧拒绝。
    - 接入位置：`parse_planning_decision`（包括顶层未知键）、`parse_method_proposal`、`planning_method_proposal.prepare_method`。
    - 不变的部分：系统字段和越权声明仍先在原始回复上拒绝；审阅员回复与执行者声明不走这里，仍严格。冻结的编解码源码（`semantic_base.py`、`contracts/htn.py` 在编解码清单 v2～v5 里登记了哈希）未改动。
    - 测试：`test_planning_replies_drop_unknown_fields.py`；`test_planning_decision_codec.py` 里两条旧断言按新决定改写。

**Host 侧**
- 单步固定额度 `OrchestrationSettings.task_max_tokens` 从 100 万提到 **300 万**（用户决定），任务总上限 2000 万不变。原因：一次保证审阅要 13～27 万 token，再加 29.5 万的审阅预留。

**核验**
- 独立子代理复核第 3～9 项：无阻断级问题。
- 各项定向回归与“不带改动的版本”逐条对比，没有新增失败。已知原有失败：SDK 规划/存储类约 11 个，Host `tests/orchestration` 26 个，退回 opt.17 同样失败。

**遗留（治本项，待用户决定）**
- 规划器不该给文档步骤安排 code_test，或者空检查不该进必过清单。
- 审阅成本高：每读一次证据都重发全部上下文。
- 需要一个“7 步任务 + 故障注入”的快速端到端测试，代替逐趟真机试错。

最后更新：2026-09-25 CST（主流程优化条目 2，SDK `0.13.0.dev20260925+opt.1`）。**规划请求包与解码器对齐**：`orchestrator/planner_views.py` 现在通过 `contracts/planning_decisions.exposed_enablement()` 把内部启用矩阵翻成两个字段——`planning_protocol.enabled_decision_types`（只含 9 个 `PlanningDecisionType` 值）与 `planning_protocol.enabled_repair_kinds`（`RepairKind` 值）；准入侧 `event_handler.py` 用逆函数 `internal_enablement_keys()` 还原成 `REPAIR/<kind>` 内部键，授权行 `planning_lane_grants`、`taskgraph_policy_sources` 等仍用内部键不变。当前包版本单一：`PLANNING_DECISION_PACKAGE_VERSION = 8`、标签 `PLANNING_DECISION_PACKAGE_LABEL = "planner-package-hierarchical-v10"`、提示词由配对表推导 `PLANNING_DECISION_PROMPT_VERSION = planner-hierarchical-v11`（v10 + 一条"decision_type 不含斜杠，子类写 payload.repair_kind"硬规则）；`_planning_decision_package_version` 只认当前标签，`_hierarchical_planner_template` 只剩"当前包→v11 / 无绑定或旧协议→旧提示词"，绑定到 4–7 版包的任务派发时抛 `ContractError("unsupported planning package version")`（用户决定：开发期不兼容旧数据）。修复开关关闭时同时去掉 `REPAIR` 并清空 `enabled_repair_kinds`；格式重问提醒补一句斜杠纠正。测试 `tests/orchestrator/full_target/test_planning_decision_enablement_contract.py`。同批：条目 5 `simple_harness/agents/arp/context/recall.py` 结果页达 `MAX_RESULT_PAGES` 且游标未空时 `_skipped(RECALL_AGGREGATE_LIMIT)`；条目 6 `simple_harness/agents/background_health.py`（`BackgroundHealthBook`，循环 index/draining/recall/tool_probe/reap）接入 `AgentRuntime._index_pump`、`ArpRuntime._tick_after`（三段逐项隔离）、`SessionLifecycleService.drive_draining(on_error=)`，对外 `AgentRuntime.background_health()`。

最后更新：2026-09-24 CST（Assurance 第十段：默认开启 + 主流程跑通）。**保证机制默认开启**：SDK 单一默认选择点 `default_assurance_profile_for_new_mission()` 返回 `AssurancePolicy()`，Host `OrchestrationSettings.assurance_profile` 默认 `"on"`，`"off"` 为显式退出。新增生产环节：Host 以自身已认证 caller，在每轮编排循环后、以及 SDK 审阅准备前（部署端口 `AssuranceDeploymentPorts.check_policy_projector`），为每个冻结完成范围批准由原需求无损推导的检查策略（SDK `lossless_scope_mapping`：语义判据→SEMANTIC、具名检查→精确注册 CheckSpec，推不出就 `CHECK_POLICY_UNRESOLVED`，不猜），并为根范围批准最终审阅用途的策略（`mission_final_scope_id` + 对外接口可选 `purpose`）。保证通道下任务整体判定复述已采纳根决议上的认证等级，不另请未认证评判；根节点完成判断读根决议判据而非旧格式审阅记录。**验证**：Host 生产装配 + 真实模型（Grok Build 通道 `grok-4.6`；DeepSeek 日卡上游当晚只回空占位）run-21 从创建走到 MissionCompleted（verification_passed），收尾 FINALIZED/USABLE；途中 14 个接线缺陷逐局修复（授权键粒度、复核比较有效期、审阅预算编号、披露排序、审阅调用上限等），明细见 SDK `plans/assurance-1.1/BODY-WORK-IN-PROGRESS.md` 第十段、[HANDOFF](../HANDOFF-2026-09-23.md) §5。钉版 `0.13.0.dev20260923+assurance.14`（源 1903fbaf）。**边界**：只跑通 1 局 1 题 1 提供方；12 局真实模型、独立审阅、原生点击、Host 26 个既有失败迁移、证书签发即判 SOURCE_CHANGED 的读集粒度未做。

最后更新：2026-09-23。跨电脑源码交付：完整 HTN + TaskGraph23 + Assurance WIP SDK 位于 `sdk/simple-harness-sdk/`；最终 TaskGraph Host UI2 源码位于 `development/taskgraph-host-overlay/`，尚未覆盖根 Host。根 Host 仍固定 HTN wheel。Assurance 主体、UseCertificate/acceptance/终态/生产装配未完成；仅局部 seam，不是整体产品 PASS。实际交接见 [HANDOFF](../HANDOFF-2026-09-23.md)，下文保留历史检查点。

<!-- v14-final-integration-current -->
最后更新：2026-09-22 CST。V1.4（去除NanoJev）本阶段核心最终集成与原生完整效果闭环 PASS，TaskGraph接线资料 READY。Host已安装 `0.13.0.dev20260922+htn.1`（wheel SHA-256 `af9e273061ceeeb3204ccbb4e4d32965fb3bba568e6c7e63f1af1b18244b9800`），528包内文件逐字节一致。新Mission默认hierarchical/独立world；Mission与根合同同事务，CompletionSpec确认后才规划。真实Tauri案例 `mission-5bb7c1fef5597956` 完成内容→操作审查→界面审批→ActionExecutor发布→效果验收→根Resolution→Mission COMPLETED：12次DeepSeek调用、98229tokens、0未知、1次发布。冷恢复/只读回放前后1Attempt/8intents/102events/12calls/1action不变。旧回放语义投影仍PARTIAL（23未知事件类型/1未覆盖字段/UI账本未对齐；覆盖字段差异0），不得将此记为全部回放通过。真实模型新请求默认输出16384，上限32768；历史失败保留。SDK候选dirty源码未整体合并main、未release，Host工作树改动保留。H6大批量晋级、H8 576局对比依用户要求移出阶段并停止，原完整门禁历史保持OPEN。后文旧状态仅为历史。 [Delivery and evidence](/Users/denny/projects/simple_harness/plans/taskSys2/升级planV1/v1.4/最终集成与端到端交付-2026-09-22.md).
<!-- /v14-final-integration-current -->

最后更新：2026-09-22 18:33 CST。本阶段按用户新范围仅做最终集成与必要端到端验收；H6大批量候选晋级评测、H8四方案576局对比评测移出本阶段，功能与历史证据保留，不记为通过。H6 cohort-5在模型调用均已结算的边界停止，PID82450已退出：7个baseline已有成功回执，当前未完成案例不计PASS，停止前0未知用量；新版H8未启动，自动继续已取消。原H8为40 PASS/1 FAIL/1 INTERRUPTED。V1.4仍IN_PROGRESS，最终Host集成与完整实际操作闭环待验收；未合并、替换Host wheel或通知TaskGraph ready。

最后更新：2026-09-22 18:07 CST。V1.4（去除NanoJev）IN_PROGRESS。主体H1–H8接线已实现，正在处理真实验收故障；未合并、替换Host wheel或通知TaskGraph ready。非流式manifest-18 H8停于40 PASS/1 FAIL/1 INTERRUPTED（HTTP524，125.96秒，1未知），534局未启动。H6 source-10真实COMPLETED/oracle=true，48calls/218041tokens/0unknown；cohort-4首baseline第25调用HTTP502（1.32秒）后停止，110908已知tokens/1未知，未晋级。已实现单次SSE传输、完整工具参数组装、断流拒绝与已知用量保留；真实文本/工具两探针通过，30项适配定点、3项计量/身份、22项旧Provider兼容通过。当前stream=true已冻结manifest-19；source-11真实COMPLETED/oracle=true，44calls/199597tokens/0unknown，唯一候选已产生，cohort-5的50局配对验证已自动开始；未宣称解决上游502或验证全部长请求稳定。UI07在manifest-18源码快照真实确认CONTENT_HASH_VERIFIED效果要求，唯一审批事件、0模型调用/0发布文件；不是完整效果执行。完整H6/H8、原14局、独立核验、最终Host集成仍开放。

2026-09-22 Host 完成要求/操作请求补齐丢响应恢复：30 秒超时解除等待，保留原 command_id/idempotency_key；新请求忽略旧响应，父组件更新回调不会丢失在途请求。OperationWorkspace 单文件 4 PASS（65ms），覆盖精确重试/迟到响应；当前原生 UI 后继验证尚待，不代表 Host 或 V1.4 整体完成。证据 `.local-test-evidence/2026-09-22/v14-host/operation-lost-reply/junit.xml`，SHA-256 `34c2d5879ae6bdf3c2154cdd5c84147b513f3d801a938bd4c4ea43c09446b495`。

最后更新：2026-09-22 CST。V1.4（去除 NanoJev）仍 IN_PROGRESS。真实 H6 source-6 COMPLETED、独立 code oracle=true，48次 DeepSeek v4.1 Flash 调用/228605 tokens/0 unknown，正式生成1个候选。Selection合成已分离 DATA 与候选材料来源，精确联合校验实际挂载；两个真实 T0 intent/review/materialization 同target精确隔离，2项定点PASS/0.43s；overlay跨Mission/Task错链拒绝3 PASS/1.69s；O04/I08按权威合同3 PASS/1.05s（专用caller-tenant reader与wheel安装并非这两项必要条件）。I07冷恢复+两项旧package字节golden 3 PASS/0.55s。H1-H静态35 MATCH/1 PARTIAL（I07完整legacy收尾），不是整门35 PASS。H6 cohort-1首个baseline FAILED（26898tokens），公共测试缩进错误已修且新增冻结前AST/隔离校验；原评测集合与失败证据保留，新source-7在独立runtime继续。H6晋级/H8矩阵/最终质量门及Host整体验收仍未完成，未通知TaskGraph ready。

最后更新：2026-09-22 CST。V1.4（去除 NanoJev）仍 IN_PROGRESS。修复真实 H6 source-5 在 4 个 Task 完成后的派发中断：派发器与 completion freeze 共用 DATA-bound producer 的 accepted workspace overlay，保持精确 artifact/hash 校验及 ORDER-only/只读新增测试隔离；定点 3 PASS/1.46s。P06 参数 schema 错误以 typed ParameterBindingsError 归类 STRUCTURE_INVALID，真实在途兄弟/外来 lease 保留及合法替换冷恢复 1 PASS/1.04s。H1-H 静态映射现 32 MATCH/4 PARTIAL，非执行 32 PASS。source-5 27 次调用/119574 tokens/0 unknown，未完成，无 ready receipt；source-6 使用新冻结 manifest-12 继续真实闭环。H6 cohort、H8完整矩阵、剩余 H1 门禁和当前 Host 全链仍开放。未合并/重装 Host wheel，未通知 TaskGraph ready。

Last updated: 2026-09-22 CST. V1.4 excluding NanoJev remains IN_PROGRESS. Real DeepSeek v4.1 Flash code Mission COMPLETED with independent domain success: 27 physical calls, 105645 tokens, 102.551s, zero unknown usage (manifest-6; h8-code-scoped-content-1/probe-receipt.json). This validates scoped TASK_CONTENT Worker/Critic wiring for this scenario, not the full H8 matrix. Prior-source full_target: 4106 PASS / 1 FAIL / 5 SKIP; the outdated drone template fixture subsequently passed its targeted recheck. H1-H extraction from that JUnit: 30 PASS / 6 PARTIAL. Current follow-up fixes cover effect preparation scope and registry eligibility; H6 real cohort, complete H8 matrix, remaining H1 gates and current Host effect UI remain open. Candidate not merged, Host wheel unchanged, TaskGraph readiness notification not sent. See the Host V1.4 acceptance repair checkpoint.

最后更新：2026-09-22 CST。V1.4（去除 NanoJev）整体 IN_PROGRESS。D2原始回执/完整handoff负证明producer、D3延期冷恢复、H8真实进程强杀与AppWorld同episode重接已实现并完成具名局部验证；Host原生内容确认与冷恢复已实点通过（非完整效果Mission）。当前DeepSeek第5次最小聊天恢复200/可见输出/usage，正式Worker复验中；H6 cohort/H8完整矩阵及完整H1门禁仍未关闭。H1-H静态映射30 MATCH/6 PARTIAL不是执行PASS。候选未合并、Host wheel未重装、未通知TaskGraph ready。 [当前证据与边界](../plans/taskSys2/升级planV1/v1.4/验收修复检查点-2026-09-22.md)。下文保留历史时点。

最后更新：2026-09-22 CST。V1.4（去除 NanoJev）主体接线已写入，进入验收，整体 IN_PROGRESS。Operation T0/T1/T3、D3、H2–H8 runtime 和 Host 完成确认/操作提交/规划授权/人工回答入口已接；H6 同库真实 cohort 入口及 H8 四臂冻结配置已补。当前专项 `test_v14_runtime_closure.py` **15 PASS / 0.72s**（首次 14 PASS/1 FAIL 为旧 v8 fixture 断言，与新 v9 默认不符，已修正）；仅覆盖具名15案例，不是 H1–H8 完整门禁。证据位于 SDK 候选 `.local-test-evidence/2026-09-22/v14-closure/{pytest-fixed.log,junit-fixed.xml}`。AppWorld 16条服务规则注册已核对，未计作业务场景PASS。真实单Mission/矩阵与Host原生UI验收继续中；候选未合并、Host wheel未重装，未通知TaskGraph ready。下文为历史检查点，当前状态以本段及Host V1.4主体编码检查点为准。

最后更新：2026-09-22 CST。Operation 补遗继续实施，V1.4（去除 NanoJev）整体未完成。OC-1 Spec 批准与 OC-2 Scope/原子准备事务已接入；MIXED 保持 VERIFYING、禁止自动动作/重开 Worker。上一固定源码 full_target 为 4016 PASS / 8 FAIL / 5 SKIP（146.72s，589 文件 hash 不变）；8 项失败已修并经 231 项定向复验，后继组合相关 235 PASS（3.58s），不能合称全门通过。新增 Selection 准备/回放/回滚 2 PASS，真实非空 DATA 冻结及伪造 mount 拒绝 1 PASS，等待态不误停与内容完整性 10 PASS。完整 nested compound 与中间 local criterion 链正在实测；OC-3 payload/source reader 开始实现，T0/T1/T3 producer、D3、H1-I/完整 H1、H2–H8 收尾及当前 Host 原生 UI 仍待。无新 PlanAgent 待决；保留所有 dirty worktree，未合并/重装 Host/调用真实 Provider。

最后更新：2026-09-21 CST。V1.4（去除 NanoJev）仍未完成。后继修复 operation/action link 同身份重放假冲突（原反例 1 FAIL / 4 PASS，修复相关 28 PASS；旧 action 相邻回归 74 PASS / 1 原有条件 SKIP），并保持重复重放零写与 alias 原子拒绝。新增取消 Task 的真实在途/lease 检查，与原 repair 套件共 8 PASS；两个真实 SQLite 写事务交错与真实方法退役后的 UNKNOWN 读取均已通过（后续组合首轮另有 cycle 夹具失败，已修正）。compiler 拒绝保留 typed report，collector 不再将非四类缺陷归为 COVERAGE_GAP；未知 producer code 强制 INTERNAL_CONTRACT_ERROR。最新相关 181 PASS / 3 既有 codec SKIP（2.00 秒），3 个 preview/collector 源文件 mypy 与定向 Ruff 通过。此前 full_target 3891/5 与 H1-H 20/8/8 是前一源码检查点，尚未重新全量/矩阵汇总。Operation 上游 producer、延期恢复合同、H1-I/完整 H1、H2–H8 与 Host UI 仍待，候选未合并。架构裁定问题见 Host plan 的 PLAN-AGENT-架构裁定请求-2026-09-21.md。

最后更新：2026-09-21 CST。**V1.4（去除 NanoJev）整体未完成。** 候选 `codex/h1h-impl` / HEAD `102ad3dfa2db38d575ea929d39ec5ed1561a71da` 加保留的未提交改动，最新固定源码 full_target **3891 PASS / 5 SKIP / 137.29 秒**，1045 个 Python 源码/测试 hash 前后不变；5 个变更源码文件 mypy 通过。当前实际 H1-H matrix 为 **20 PASS / 8 PARTIAL / 8 NOT_COVERED / 0 FAIL**，exit 2，整门仍 OPEN。已完成本地修复：提交/最终decision原子恢复、UNKNOWN action保留预算、授权issuer/tenant/Mission隔离、原始reply CAS留存、两种固定decode-only先解码后拒绝；补齐A01/A03/I01/I04/P05/P08/P10等真实断言。历史8个旧fixture失败保留，补真实ArtifactStore后25定向及本次全量通过。真实 DeepSeek v4.1 Flash WAIT场景已完成（早于后继raw/授权修复）：190.609秒、40次物理串行调用全succeeded、4次WAIT注册/唤醒、4件accepted artifacts、所有reserved字段0；带测试调度/签发器，不代表Host UI或完整H1-I。Operation上游冻结身份/参数引用/物化链、其余门禁及H2–H8仍待；候选未合并、Host wheel未重装、原生UI未验。当前事实与原始证据索引见Host `plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md`，后文旧数字仅为历史。

最后更新：2026-09-21 CST。**当前 V1.4（去除 NanoJev）状态纠正：整体未完成。** SDK 候选 WAIT 固定源码回归 3812 passed / 5 skipped（138.38 秒，545 个源码/测试 hash 不变）；后续 authority/operation 定向 41 passed（0.72 秒）属于更新后的局部源码。H1-H 原“36/36”仅为测试执行数，修正规格映射后为 **5 PASS / 10 PARTIAL / 21 NOT_COVERED**，不能关闭门禁。真实 DeepSeek WAIT 注册→Worker 完成→唤醒 PASS（49.503 秒、9 次物理调用）；同 Mission 恢复完成 4 件 accepted artifacts，但在 240.089 秒/20 次新增调用边界下仍 ACTIVE，最终评审标签拼错被严格拒绝，不能报 H1-I 完成。旧模式回归 559 PASS / 1 timeout FAIL / 13 SKIP；失败文件原样复跑 6 PASS，原因未定，原失败保留。候选未合并、Host wheel 未重装、原生 UI 未验。后文旧检查点保留历史时点，不覆盖本条。详见 Host `plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md`。

最后更新：2026-09-15 11:15 CST。A96 已冻结12个dev题，小对照 D-arm smoke 超时失败（未知用量1），96次未启动。[冻结](../plans/taskSys2/testPhase1-a96-freeze-2026-09-15.md)。

最后更新：2026-09-15 10:45 CST。N5 A 轮 Qwen 干净/攻击各一例已在 69d679c 上评分；系统观察晋级，KnowledgeUsed 0。A 轮未完成。[记录](../plans/taskSys2/testPhase1-n5-blackboard-2026-09-15.md)。

最后更新：2026-09-15 08:51 CST。N5 AgentDojo黑板传播：Host 在 Task 接受后把成功官方工具回执投影为系统 VERIFIED `tool_observation`；Blackboard 只读；模型 Claim 仍最多 SUPPORTED。错误回执与伪造 knowledge id 不晋级。Runner Mission 允许 `knowledge_list`/`knowledge_read`。词面不相关时知识在库中但 `verified_knowledge` 包为空。定向 40 PASS / 7.11 秒，0 应用模型调用。[记录](../plans/taskSys2/testPhase1-n5-blackboard-2026-09-15.md)。

最后更新：2026-09-15。Host374aa70a/SDKf122b8c：共享容量、长响应及最终Mission评审后继完成限定实机验收。v60独立评审正式交付60实跑PASS；v61亲自捕获列表/详情待验证→交付和冷恢复。累计335本地归属调用7157983已知tokens下限，1早先未知另列，Flash0。最新广回归2304PASS/32SKIP/6环境FAIL，关联兼容环境49PASS。N1–N8/正式A96B96仍OPEN，未打包。

最后更新：2026-09-15 06:01 CST。N1真实三进程v1为FAIL：两次物理调用321104tokens、0新增抢占，第三路前置排队被错误释放（已知0出站）。系统校时影响psutil.create_time造成身份误判，旧源3反例FAIL；改为psutil稳定process hash（>=7.2.2），新25PASS/1.85秒，完整及实机后继待验。N1–N8仍OPEN，Flash0。

最后更新：2026-09-15 05:53 CST。N1共享容量SDK完整2302PASS/32条件SKIP/669.22秒，620hash不变。原生v55前台None输出误拒绝已定位并修复（12定向PASS，原0调用失败保留），真实多进程与新UI待验，整体N1–N8仍OPEN。 [本轮证据](../plans/taskSys2/testPhase1-shared-capacity-2026-09-15.md)。

最后更新：2026-09-15 05:40 CST。N1同机共享2槽/393216容量接纳实现，SDK40定向PASS、Host15PASS；重复绑定与PID复用问题已修复，未知出站保留。完整回归/真实多进程/当前源码UI仍待；N1–N8/正式A96B96仍OPEN、Flash0，无打包。 [当前范围](../plans/taskSys2/testPhase1-shared-capacity-2026-09-15.md)。

最后更新：2026-09-15 02:44 CST。code profile v4生产3e2792f：原分页/时间例外两个失败题自然复验均VERIFIED（16调用139927tokens/290.623秒），旧失败保留。完整v9为2275PASS32SKIP1旧版本断言FAIL，后继28PASS及官方ARE50PASS，无生产再改；当前源码UI v54冷恢复实点通过，0新调用。累计261本地调用5430502已知tokens下限/1早先未知，Flash0。N1–N8/A96B96仍OPEN，无打包。 [最新证据与边界](../plans/taskSys2/testPhase1-result-contract-followup-2026-09-15.md)。

最后更新：2026-09-15 02:25 CST。N2 v4仍FAIL（28调用/912645tokens/1557.359秒）；N3四专项2交付PASS/2非法封套FAIL，候选答案正确不计交付。新code profile v4实际ID的合法JSON输出示例定向90PASS，旧v1–v3不变；全量/新真实模型/UI待。旧源受控恢复运行中。N1–N8、A96/B96仍OPEN，Flash0、无打包。 [后继事实](../plans/taskSys2/testPhase1-result-contract-followup-2026-09-15.md)。

最后更新：2026-09-15 01:50 CST。a30639e最新完整编排2265PASS/32条件SKIP/0FAIL，673.00秒、612hash不变，源码UI v53冷恢复实点通过。N3后继运行器真实core离线3负控、分页3正10负控通过，真实未跑；N2 v4仍运行。N1–N8和正式A96/B96仍OPEN，Flash0、无打包。 [证据](../plans/taskSys2/testPhase1-budget-analysis-followup-2026-09-15.md)。

最后更新：2026-09-15 01:41 CST。当前a30639e源码原生v53冷恢复/产物/回放/支持报告实点通过，5旧调用保持、0新调用；约两分钟启动等待仍保留。新完整v8和本地N2 v4待终态；N1–N8/正式A96B96未关闭、Flash0、无打包。 [证据与范围](../plans/taskSys2/testPhase1-budget-analysis-followup-2026-09-15.md)。

最后更新：2026-09-15 01:36 CST。N2 v3真实失败保留：40调用874997tokens/1752.676秒、陈旧知识被拒绝、0有效复用。新AppWorld默认v3只读知识刷新和出站前额度终止适配定向69PASS/8.49秒，旧源决定性2FAIL；完整v8、新本地v4及新原生验收仍待。N1–N8/正式A96B96仍OPEN，Flash0、无打包。 [证据与边界](../plans/taskSys2/testPhase1-budget-analysis-followup-2026-09-15.md)。

最后更新：2026-09-15 01:07 CST。最新生产源码已完成全量v7：2259PASS/32条件SKIP/1历史hash断言FAIL（657.44秒）；仅测试断言按有意新增预算语义更新并保留旧内容hash，后继52PASS/0.71秒，无生产再改、未再全量重跑。原生v52冷恢复实点通过；N2 v3第一Task验证中，N1–N8和正式A/B96仍OPEN，Flash0、无打包。 [明细](../plans/taskSys2/testPhase1-budget-analysis-followup-2026-09-15.md)。

最后更新：2026-09-15 00:55 CST。最新N2/N7源码UI v52实点冷恢复/产物/回放/支持报告通过，5调用/4工具效果/42事件不变，0新模型调用；新完整回归v7与N2 v3仍运行。提示接线和离线分析相邻87PASS；N1–N8/正式矩阵仍OPEN、Flash0、无打包。 [本轮证据](../plans/taskSys2/testPhase1-budget-analysis-followup-2026-09-15.md)。

最后更新：2026-09-15 00:50 CST。非文档Planner新增原有累计预算语义，N7任务块统计区间已接入；旧源码决定性2FAIL，父级相邻87PASS/5.49秒、ruff/mypy通过。预算规则不改；真实N2 v3和源码UI v52仍在验收，N1–N8/正式96次未关闭、Flash0。全量2240PASS属于前一d495382源码，不能代替本次范围。 [证据及边界](../plans/taskSys2/testPhase1-budget-analysis-followup-2026-09-15.md)。

最后更新：2026-09-15 00:37 CST。最新完整编排2240PASS/32条件SKIP/0FAIL，668.77秒，645源码/测试hash不变。N2本地第二次校准429.665秒因Task预算失败，17调用236536tokens/0未知，未碰1800秒时限；真实知识复用仍OPEN。最新源码UI v51冷恢复/回放/产物/支持报告实点通过并保持运行。N1–N8与正式A/B96仍OPEN，本轮Flash0、无打包。历史偶发停滞根因不因回归通过而宣称修复。

最后更新：2026-09-15 00:09 CST。当前SDK源码UI v51冷恢复实点通过，旧Mission/Task/事件/5调用保持，3产物只重定位storage_uri且hash/VERIFIED不变，回放41事件差异0、支持报告9169bytes哈希核对，0新调用。首冷启动约102秒有未连接等待；并非全N8关闭。ARE真实动态硬判通过11调用58017tokens/275.301秒；N2官方复杂任务15分钟超时、1未知，30分钟同配置独立校准运行中。最新完整回归v5为2237PASS32SKIP1FAIL；冷恢复诊断父6PASS后v6运行中，原偶发停滞根因仍OPEN。N1–N8整体未关闭，Flash0，无打包。 [记录](../plans/taskSys2/testPhase1-two-wave-execution-2026-09-14.md)。

最后更新：2026-09-14 23:26 CST。源码UI v50 Mission已COMPLETED，实际code_test 10PASS，5次Qwen调用20708tokens/212.420616秒，3产物VERIFIED；Critic为NOT_REQUIRED。Host模型卡修复62c8ce10已push。此UI只覆盖固定N3 SDK，不覆盖后继N2/N6。最新SDK完整回归2236PASS/32SKIP/2FAIL659.01秒，失败正在定位；ARE硬判接线官方50PASS但真实动态校准尚未判分，完整Gaia2/judge仍OPEN。N1–N8仍未整体完成，0Flash、无打包。 [执行证据](../plans/taskSys2/testPhase1-two-wave-execution-2026-09-14.md)。

**最后更新：2026-09-14 22:55 CST。** 首组Qwen AgentDojo正常/注入对照官方utility均通过、攻击未成功；攻击20调用117984tokens/1046.198秒，真实Critic失败后第二attempt通过保留。新增known-zero拒绝审计/统计身份48PASS、结构化工具审计35PASS、ARE实际core32PASS；N2下游外部状态同步父审发现缺口返工。UI卡片固定GPT默认修复，13PASS＋实际源码页面正确显示本地Qwen/262144窗口，真实UI Mission仍待。N1–N8未整体关闭，0Flash，无打包。[范围与证据](../plans/taskSys2/testPhase1-two-wave-execution-2026-09-14.md)。

**最后更新：2026-09-14 22:31 CST — 两轮评测增量。** code profile默认v3修复有效知识引用提示并冻结旧v2；定向80PASS，真实本地知识分页/原文hash/VERIFIED产物通过18调用186793tokens/280.849秒。AgentDojo实际core正常校准官方utility和Mission通过7调用33945tokens/209.98秒；攻击配对运行中。独立AppWorld API观察父测17PASS/3.49秒，消费链仍在接线。先前完整编排2180PASS/21SKIP/900.33秒不覆盖所有后继代码。ARE桥接12PASS，动态core/judge仍待；原生v49只完成导航/256K表单检查。N1–N8均未整体关闭，0Flash，不打包。[四表、失败与范围](../plans/taskSys2/testPhase1-two-wave-execution-2026-09-14.md)。

**最后更新：2026-09-14 21:25 CST — 两轮后续开始执行。** Qwen256K单路/双路约21万实际输入规则与引用通过，但双路复核抢占+1，容量稳定性仍OPEN；在途token接纳正在实现。通用96次入口/困难语料/时段纯策略/执行来源回执完成局部验证，gap193PASS及回执后继53PASS、真实AppWorld来源检查通过；不将任意输出晋级可信API事实。AgentDojo官方接口5PASS，真实Orchestrator驱动仍待；Gaia2仅源码可行性核查。N1–N8均未整体关闭，0Flash调用、无本轮新UI验收或打包。[当前四表、时间与证据](../plans/taskSys2/testPhase1-two-wave-execution-2026-09-14.md)。

> 2026-09-14 21:42 两轮评测增量：在途加权接纳实测大输入串行226.644秒、小输入双路130.255秒，均0新增抢占；256K窗口未变。matrix源码身份绑定、未知用量/未评分和缓存计数父审通过；完整N1–N8、正式96次、跨客户端加权、Flash与本轮源码UI仍未验收。

**最后更新：2026-09-14 20:10 CST — testPhase1后续修复与独立Flash16次回收完成。** 当前SDK功能5406fb5，完整编排2105PASS/20SKIP；256K四题S/R各3/4、D/F各4/4，有效14/16，648调用8760086tokens，runner2234.319秒。0未知用量/网关终态缺失/工具重下发，8个D/F终态预留0；知识复用和动态图收益未得到证明。旧本地9终态/7未执行/1未知保留。用户发现白屏已通过完整源码重启与实际点击恢复，原0残留仅指受管组；当前UI有意保持运行，独立评测服务已结束。 [完整结果、耗时和后续问题](../plans/taskSys2/testPhase1-flash-final-2026-09-14.md)。以下检查点保留原时点范围。

**最后更新：2026-09-14 19:59 CST — 用户报告的源码测试窗口白屏已恢复。** 受管服务退出后另有独立carrier窗口存活、15173/18140无监听；原0残留仅覆盖旧进程组。完整源码launcher恢复后，实际点击任务与REPORT通过；12表/5产物/15旧调用保持、0新增调用。当前UI有意保持运行，未改业务源码。[原因、恢复和收尾修正](../plans/taskSys2/ui-white-recovery-2026-09-14.md)。

**最后更新：2026-09-14 19:32 CST — 当前AppWorld契约v2源码验收通过，完整Flash对照进行中。** SDK5406fb5；最新完整编排2105PASS/20SKIP/0FAIL670.45秒（runner673.922秒），原生v48冷读12表/5产物/15调用核对、0新调用278.158秒，进程均清理。独立Flash400万/120调用校准四Task＋官方评分通过，实际44调用455861tokens/226.874秒；不对参数变更作单变量归因。新四题16次Flash块采用256K/400万/120调用，仍在运行；原本地9终态/7未执行/1未知完整保留，未混入后继得分。[最新证据与限制](../plans/taskSys2/testPhase1-followup-2026-09-14.md)。

**最后更新：2026-09-14 19:11 CST — AppWorld结果契约v2修复进入验收。** 本地后继矩阵9终态/7未执行，1未知调用触发停止；进程清理完成，未知Attempt预留保留。独立Flash探针暴露Worker模板非法schema_version；新AppWorld profile默认v2修复八角色示例，旧v1原样保留。反例8FAIL→新整组143PASS/8.92秒；完整回归与新源码真实复测仍待。不扩大调用预算，不打包，上下文仍本地默认256K，512K只限Flash。[当前结果和边界](../plans/taskSys2/testPhase1-followup-2026-09-14.md)。

**最后更新：2026-09-14 18:33 CST — 上下文测试范围调整。** 用户指定后续仅测试128K/256K/512K，本地默认256K，512K仅使用DeepSeek Flash。正在运行的四题16次复测为本地256K，冻结参数未变；新增档位不提前宣称通过。单次输出和累计Mission预算独立记录。其余功能与验收状态见下一检查点。[当前范围与证据](../plans/taskSys2/testPhase1-followup-2026-09-14.md)。

**最后更新：2026-09-14 17:55 CST — testPhase1三项修复进入真实复测。** AppWorld Mission显式256K池绑定接通现有522240任务预算下限；R改为有界版本化选择且独立valid_success；网关异常/取消补终态审计。定向155PASS/11.88秒；完整编排2096PASS/20SKIP/0FAIL，686.51秒，runner687.033秒，退出无残留；当前源码原生v47冷读通过，12表/15调用精确一致、0新调用，228.444秒；原四题16次本地模型复测进行中，首题S/R有效成功，D后续多轮仍受Task硬上限停止，预算规划效果不宣称全面完成。旧16次失败保持原样；默认256K/物理1，DeepSeek0调用，不打包。 [修复边界、耗时与证据](../plans/taskSys2/testPhase1-followup-2026-09-14.md)。

**最后更新：2026-09-14 16:47 CST — testPhase1测试执行完成，3项后续修复明确保留。** 功能源码全编排2079PASS/20SKIP/0FAIL，879.28秒；原生v45可信知识两Task/真实pytest/独立Critic/人工复核与冷恢复通过，v46复制数据重开通过，0重调。正式四臂16/16完成，282调用3076186tokens，5758.78秒；S有效3/4，R有效2/4（官方终态4/4，另2次自选JSON解析失败），D/F各0/4且均任务级预算停止。D/F无已观察知识复用/动态图收益；全部终态预留0、SDK工具重下发0，保留1工具失败/1拒绝及1网关outcome缺失。剩余Planner预算可行性、R选择协议、异常网关终态3项尚未修复。默认本地256K/物理1，DeepSeek0调用；不打包、不扩大96次。 [完整结果与证据](../plans/taskSys2/testPhase1-results-2026-09-14.md)。

## testPhase1 当前Host链路（2026-09-14）

- `backend/deskpet/orchestration/service.py`部署默认工具门包含knowledge_list/knowledge_read，code_test仍按已有本地执行许可接通；新Mission会冻结该权限，旧Mission不回写扩大权限。
- 跨分支原生夹具以真实system test_observation为可复用依据，下游实际查目录、读原文并引用精确观察ID/工作区hash；用户在源码UI查看代码、测试、报告后批准，两个Task及Mission进入COMPLETED，最终产物VERIFIED。
- 当前源码验证依赖配套SDK editable checkout；旧固定wheel不包含新增实现。AppWorld本轮通过SDK独立评测入口，不是新增Host跨应用业务UI。
- 最新功能源对应SDK84c3235/Host23cb7d37；全编排2079PASS/20SKIP及原生证据各保留自己的快照身份。原效果实验失败保留；预算与R协议的SDK后继修复已通过定向测试，真实复测仍待，不能从UI交付成功外推为编排普遍有效。

**最后更新：2026-09-14 13:15 CST — testPhase1仍在执行。** 新code profile v2默认范围化pytest观察，17项新正负控通过；知识原文分页/精确引用/撤回投影通过离线检查，真实本地中英消费5调用24405tokens/113.63秒通过。AppWorld第三领域及真实保存恢复/独立评分接通；首技术探针预算失败保留，5题校准进行中。S/R实际BaseAgent身份/自选控制及计量离线通过；D/F整体、16episodes、T6、最新原生UI仍待。不覆盖历史Phase3验收，不打包。 [执行证据](../plans/taskSys2/agent-orchestrator-gap-review-testPhase1-2026-09-14.execution.md)。

**Last updated:2026-09-14 CST — local DGX source acceptance PASS.** Default local qwen38-flash-next,262144 shared total/228352 Mission input. Actual260001-input request PASS111.089s. Frozen Host922b2d7b/SDK6d4ddc7 native chat+code Mission PASS:7calls18654tokens, real pytest2PASS/CriticPASS/VERIFIED artifacts; cold rows identical0new calls. Native370.415s/cold72.388s, both clean exit. Source checks SDK105+Host81+foreground44+UI81/typecheckPASS. Prior v42 foreground failure retained; no packaging/full-model-quality claim. [Current evidence](../plans/2026-09-14-local-dgx/README.md). Earlier checkpoints below are historical.

**Last updated:2026-09-14 CST — native-discovered foreground fix.** First native-v42 chat failed before HTTP because the foreground adapter had not opted into configured LAN HTTP. Product adapter now opts in for registered endpoints; public/DNS/link-local plaintext remains rejected.44 focused checks PASS4.29s. Actual260001-token request passed111.089s with three correct markers. Native successor pending; original failure retained.

**Last updated: 2026-09-14 CST — local DGX connection checkpoint.** Source local256K total /223K input profile, exact offline HF template accounting and explicit private-LAN HTTP are implemented. SDK105PASS5.92s, Host81PASS18.33s, UI81PASS1.40s/typecheckPASS; actual short/tool/continuation counts match server. Near-window and native acceptance pending. [Current local scope](../plans/2026-09-14-local-dgx/README.md). Historical Phase3 evidence below keeps its original scope.

**Last updated: 2026-09-14 CST. Current Phase3 source acceptance:46 SOURCE PASS /0 OPEN /2 user-deferred packaging criteria.** P3.4-A04 now passes one fixed real deepseek-flash strict-profile FIRST/COMPARE pair on SDK ae8d37b, snapshot-v41:188calls1918557tokens,1029.73seconds; both strictPASS/COMPLETED, zero physical errors/unknown usage/reserve/rehandoff and zero residual test processes. Committed affected158PASS6.76seconds. [Current evidence and boundaries](../plans/2026-09-12-phase3-host-g/v15-real-pair-review.md). Host production18ff5d24 and prior native-v39/earlier evidence retain their own source scope; strict mode is explicit SDK configuration, not an automatic Host redirect or new native UI acceptance. One pair does not prove model-quality superiority. No packaging/installer/release/push; historical failed pairs retained.

**Historical checkpoints below retain their original dates and evidence boundaries; their open lists do not override the current status above.**

**Last updated: 2026-09-14 CST.** Host native-v39 functionality retains its tested18ff5d24 source and307PASS cumulative scope. SDK real pair v13: COMPARE strictPASS; FIRST delivered with one retained physical tool-parse error, so original48AC remains45SOURCE PASS/1OPEN/2packaging DEFERRED. Total157calls1543084tokens880.20s/zero residual test processes. SDK diagnostics-only successor67PASS1.90s is not a new Host/native acceptance claim. [Current evidence](../plans/2026-09-12-phase3-host-g/v13-real-pair-review.md). Earlier entries below are historical.

**Last updated: 2026-09-14 CST. Source acceptance audit:45 verified/1 strict comparison OPEN/2 packaging criteria DEFERRED.** Host307PASS248.67s;3 wheel-only checks and1 default real opt-in deselected. SDK initial22failures closed by86PASS, production unchanged from native-v39. All raw failures retained. Native code/Critic/pending-human recovery, publication and legacy context gates have scoped evidence. P34v13 running; no whole-Phase3 completion claim. [Current source audit](../plans/2026-09-12-phase3-host-g/source-cumulative-v39.md).

**Last updated: 2026-09-14 CST. Native v39 code/isolated pytest, explicit Critic evidence and pending-human cold recovery PASS within scope.** Two one-Attempt Missions; real10calls26932tokens/cache16384/0reserve. Actual four files read; pending cold selected-table hashes identical, UI approval completes both with zero new model calls. Native176.367s/cold106.073s exit clean. Current cumulative and strict P34 remain OPEN; no packaging. [Evidence and boundaries](../plans/2026-09-12-phase3-host-g/native-v39-review.md).

**最后更新：2026-09-14 CST — v37旧正常PLANNING恢复/产物换行/冷读通过限定验收；代码真测FAIL保留。** 原冻结规划一次提交，旧任务19真实调用112418tokens；旧/新报告冷读不变。代码用例因Critic先于code_test拿不到输出重复失败，UI取消后43调用172374tokens/0reserve。模型槽等待误报UNKNOWN已修复，实际kill恢复等13PASS19.84秒，新投影夹具补齐store读方法后2PASS0.09秒；新源码原生待验。 [证据与边界](../plans/2026-09-12-phase3-host-g/native-v37-review.md)。无打包发布。

**最后更新：2026-09-14 CST — 源码v37诊断重建修复20PASS/17.44秒，产物详情换行87 UI PASS与正式typecheck通过。** 列表短hash保持；最新源码原生待验。旧累计Host303PASS/5FAIL中2个实际故障已定向复验，3个wheel-only检查按源码范围暂缓，不改测试。SDK新输出扩展受预算/用量/取消约束，严格P34与整体仍OPEN。 [证据和限制](../plans/2026-09-12-phase3-host-g/source-v37-checks.md)。

**最后更新：2026-09-14 CST — LC2真旧库原生接入、新256K/512K共存及冷读通过限定验收。** 旧6360c205库由新Host2eee204e/SDKc19bbd0接入；原四Provider完整记录及旧冻结input/config不变，旧任务沿default继续；新任务各用256K/512K。21真实调用65595tokens，含父任务512K预算不足失败2879，后继默认预算交付。5Mission/25记录冷读hash保持，零新调用；不冒称原生并发压力。原生353.520/冷94.208秒正常退出。P34严格对照/最终累计仍OPEN，无打包发布。[证据与边界](../plans/2026-09-12-phase3-host-g/legacy-native-v36.md)。

**最后更新：2026-09-14 CST — 原生v36发布/复核理由与冷读切片通过。** Host2eee204e/SDKc19bbd0，真实DeepSeek14调用40392tokens，审批后本地发布正确绑定内容；复核理由直接进入重试，实际文件修订后接受。同源码冷启动两任务/选定持久表/发布文件hash一致，零新调用/重复发布。生命周期662.777+49.152秒，退出无残留。P32其余AC、LC2共存原生、P34严格对照及最终累计仍OPEN；无打包发布。 [证据与边界](../plans/2026-09-12-phase3-host-g/native-v36-review.md)。

**2026-09-14 CST: Planner/executor action contract v2 and atomic human-review reason are ready for a fixed-source native successor.** OriginalordinaryPlanner bytes match9f70 baseline; approvalcount usesactualdeploymentdecision; focused12PASS0.57s, earlier35PASS6.80s and UI117/typecheckPASS. Source-native behavior is still pending; v35FAIL retained. [Evidence](../plans/2026-09-12-phase3-host-g/journal.md). No packaging/release.

**2026-09-14 CST checkpoint: P32 nativev35 FAIL/no_progress,11calls47465tokens, no publication.** Planner source/destination clarification35PASS6.80s; Host atomic human-review note117UIchecksPASS1.47s and typecheckPASS. These successor changes are uncommitted and not yet source-native verified. Originalv35failure remains, overallPhase3 OPEN. [Evidence and limits](../plans/2026-09-12-phase3-host-g/journal.md).

**2026-09-14 CST source checkpoint:** Host now distinguishes genuine old unguarded pools from guarded pre-context pools and exposes named256K/512K pools alongside both. Pinned tokenizer plus launcher45PASS0.52s; SDK mixed-pool recovery/action/diagnostics72PASS13.07s and adjacent56PASS13.64s. This closes scoped software controls only; native publish/coexistence and final cumulative audit still pending. P34v12 strictFAIL and historical no-routing-evidence limit remain. No packaging/release/push. [Current evidence](../plans/2026-09-12-phase3-host-g/journal.md).

**2026-09-14 CST 更新：源码测试启动器新增隔离的本地发布目录显式开关，39项控制通过（0.17秒）；真实UI发布仍待。P34 v12严格FAIL：两臂交付、C合成失败后回退旧候选，167调用/1622330tokens/998.48秒。LC2共存与动作契约仍在修复测试，整体Phase3 OPEN。** 详见[当前验收记录](../plans/2026-09-12-phase3-host-g/journal.md)。无打包发布推送。

**最后更新：2026-09-14 CST — 512K真实多轮与冷读已验证。** 冻结SDKdc2f156源码两次DeepSeek Flash调用，实际输入520288/521469tokens，输出2664/664，总1045085tokens、cache0；后轮历史折叠后按新条件精确筛选，冷读零新增调用。42.987秒（runner43.35秒），合成材料范围，非原始文档质量证明。P36诊断PD4已关闭；SDK6fb5c50修复P34知识ID提示冲突和分页完整读取观察器，44PASS与独立复审通过，新真实pair/LC2兼容/最终累计及整体Phase3仍OPEN，无打包发布。见[长上下文结果](../plans/2026-09-12-phase3-host-g/long-context-results.md)。

**最后更新：2026-09-14 01:33 CST — P36 App诊断切片PD1–PD4已验证。** 当前Host `d093f55c` / SDK `dc2f156` 源码原生v33完成失败Mission诊断、重复导出及同源码冷启动重读。FAILED/34events/600tokens/0reserve保持；支持报告8205B及完整SHA一致，任务/事件/预算/执行等选定持久表hash不变，4受控调用/0重调。长路径回执已真实截图确认换行。源码UI生命周期1016.772秒（含等待），冷读62.016秒，均正常退出无残留。只关闭诊断切片，P34真实pair、P35补充与最终累计及总体Phase3仍OPEN；打包发布暂缓。证据与边界见[诊断验收](../plans/2026-09-12-phase3-host-g/p36-diagnostics-plan.md)。

**2026-09-14 CST P36 UI检查：** v32错误引用按预期拒绝，诊断/重复导出8205B且全持久表hash不变；截图发现回执长路径溢出，补自动换行后进行最终源码/冷读。PD4仍待最终验证，不提升整体完成状态。

**2026-09-14 CST原生前补充：** P36首次原生v31为FAIL_SETUP（旧夹具仅识别doc6/7，实际doc9），不能当错误引用拒绝通过；已修正已登记doc8/9兼容，并把有预留/待结算时的诊断标为记录不完整，未知数明确只计已入账记录。当前实际文档链与诊断11PASS20.69秒，UI89PASS0.921秒/typecheck/lint通过；源码提交后重测PD4，整体仍OPEN。详见p36-diagnostics-plan.md。

**最后更新：2026-09-14 CST — P36诊断与本地脱敏支持报告软件验证通过，原生验收待执行。** MissionControl鉴权后只读既有replay/attribution，逐字段投影原文/路径为hash，记录真实运行身份和未覆盖/未知/预留/未对齐用量；固定support目录内容寻址导出，不执行Provider/工具。源码6PASS10.26秒、已安装0.11.1 wheel16PASS24.37秒（身份仍version-only），UI86PASS0.941秒，正式typecheck/lint通过；SDK当前164PASS3真实opt-inSKIP214.27秒。独立审查发现的原文泄漏/能力判断/身份用量缺口已修复，PD4源码原生UI仍NOT_RUN。最新真实v11两组均完成交付但严格pair仍FAIL；P34/P35最终累计、旧pre-context兼容和总体Phase3仍OPEN。无打包发布推送。详见 plans/2026-09-12-phase3-host-g/p36-diagnostics-plan.md。

**2026-09-14 CST补充：真实256K多轮历史条件筛选与冷重开通过。** 两调用519027tokens，20.091秒；后轮历史已折叠仍正确按新条件筛选，冷重开零调用。新P34 context256-8m-out32k-v7已单独冻结8M/24等预算与256K/32K输出合同，真实执行前纯检查21PASS0.37秒、含原导出hash及实际公开策略绑定/容量floor；旧2M实验不变。子代理初版output_reserve/selected profile/floor遗漏已由主审补齐，首测试19PASS1schema形状FAIL修正；真实pair尚NOT_RUN。P34/P35-A04/旧pre-context升级与总体Phase3仍OPEN，无打包发布推送。详情见各long-context-results与SDKcontext256-pair-contract。

**最后更新：2026-09-14 CST — 256K/512K当前源码原生真调用、产物与冷读通过。** SDKdab3d44/Hoste183e400，两项真实Mission共8调用/24020tokens/0预留；各自实际容量保持，原生打开两份54B文件，独立进程冷读后任务/意图/产物/73事件/20journal/8selection/8Provider记录逐项不变，零重调。生命周期224.909+70.252秒；实际Mission9.871/11.156秒。新pinned256/512长历史旋转/原文/完整工具组/冷重放/超限零调用2PASS6.16秒，提交态相邻25PASS13.31秒。真正pre-context旧库自动共存升级及真实多轮长内容仍待；P34配对价值/最终累计和整体Phase3仍OPEN。无打包发布推送；详见long-context-results.md。

**最后更新：2026-09-14 CST — 256K默认/512K可选源码接线与真实容量探测通过，整片仍在验收。** 实际DeepSeek输入258149/520283tokens，两次各1调用正确回答首中尾记录与跨位置求和，9.296/22.855秒；输出默认8192/上限32768独立。新Mission默认总预算4M/8M（未用容量不计消耗），按任务冻结profile与预算。旧32K实际请求身份冷升级保持、全角色256/512受控完成/零调用重开4PASS2.97秒，SDK相邻26PASS10.01秒、Host相邻65PASS105秒、UI78PASS0.803秒。真正pre-context旧库自动升级仍OPEN，当前显式保留legacy；长历史及当前原生UI待验，不能宣称整片完成。旧v29原生动态预算/冷读通过，真实v10两臂仍失败（842844tokens/93调用），P34/P35-A04和整体Phase3未关闭。新探测观察器序列化返工及未知用量原FAIL完整保留。无打包/发布/推送。详情：plans/2026-09-12-phase3-host-g/long-context-results.md。

**最后更新：2026-09-13 17:50 CST — SDK动态预算修复已提交753b61a。** 已物化的综合/冲突任务预算仅计一次；初始Planner预留、取消任务已结算及在途支出仍保留。旧源码5决定性FAIL/6PASS，修复相邻214PASS/3真实opt-inSKIP71.88秒，独立Sol审查补齐公开冲突创建→动态提交→冷回执控制。新A400/F400实验总1920K，旧2080K配置在凭据与调用前静态拒绝；历史导出不改。真实v9两组均no_progress，747326tokens/95调用/564.99秒，未证明收益。最新源码原生与真实门待复验；P34/P35-A04保持OPEN，P36非打包功能继续核对，打包发布暂缓。SDK计划：plans/2026-09-12-phase3/p34/dynamic-system-budget-fix.md。

**最后更新：2026-09-13 17:13 CST — 当前源码UI复核完成，真实COMPARE门仍OPEN。** SDK生产代码043349f/Host5e186141的受控FIRST与COMPARE均从原生UI正式提交、完成验证、打开实际产物，并经新app/backend冷恢复。FIRST3600tokens/24调用/0预留/0rehandoff，170Mission事件/57journal/24selection不变，188.440+71.375秒；Host source-ui-search-v28/case-summary.json SHA2561cb657601e390d0b17b3fd1e9631ae4d801fe41aa03ff8fa46cce62fff88ba41。COMPARE2700tokens/18调用/0预留/0rehandoff，93Mission事件/40journal/18selection不变，421.115+135.708秒；仅预期deployment PolicyConfigDrift，ACTIVE未变；source-ui-compare-v28/case-summary.json SHA256094f0f6e76609644b1404bdb5eef5a6179d65f3d5080d3cabfd69cf82d6a54de。原生进程组退出，端口释放，防熄屏保留。真实v8FIRST实际COMPLETED，内层oracle错误240K与声明320K冲突，55b766e修正并以79持久请求完整离线复判PASS，零新增调用；原始FAIL保留。COMPARE实际A2budget_exhausted，未完成；真实391829tokens含27592有效失败响应用量，原测试观察器漏计，账本正确。两臂实际总1121808tokens/123调用，858.53秒；SDK pair-summary SHA256a17dec54c7afa0e4235c913912bb371dcad19449a4ca7238d36357ddbd5458bb。af1c76f修正观察器统计并另登记A480K/B480K/C400K/S240K、总2M不变实验，干净34PASS1.37秒；v9正在真实运行。产品生产代码未再改；P34/最终累计/P35-A04仍待重关，不打包/P36/推送。详细SDK计划：plans/2026-09-12-phase3/p34/v8-real-pair-review.md。

**最后更新：2026-09-13 16:50 CST — SDK Manager/selection 后继修复。** SDK043349f已提交：默认manager-v4明确严格图操作/片段wire，原历史模板不改；空COMPARE轮次允许原预算/截止时间内一次独立F验证，保留原A失败，仅F通过后正常改接C依赖。修复已完成COMPARE前驱丢失及C第二候选/新综合的冻结F输入复用；原子截止时间与旧回执错绑有负向控制。干净提交49PASS/9.72秒（runner10.04）；相关1292PASS/4旧版本断言FAIL已定向修正，后继52PASS/noSKIP/10.12秒。真实v7两臂no_progress，累计863878tokens/101调用；新同profile/material/oracle v8运行中，不能称P34完成。当前Critic修复源原生v27文档及新进程冷读PASS：7调用/1050tokens/0预留/0rehandoff，108B compare.md及46Mission事件不变；证据source-ui-critic-doc-v27b/case-summary.json SHA25615a3363707b078977e57d2886ead887eef5c91552e9bf4fa218c29b71eea297b。后继selection源码尚待当前原生验证；P34/P35-A04及总体Phase3仍OPEN。SDK详细过程见 plans/2026-09-12-phase3/p34/manager-selection-fragment-fix.md。不打包/P36/推送。

**Last updated: 2026-09-13 16:01 CST - same-Task Critic growth and typed admission fix.** Critic can transfer only its request deficit from the original protected Task hold, keeping frozen identity/price, sibling first-Critic floors and UNKNOWN usage intact. Nonretryable SDK admission now atomically rejects/stops the Task without schema retry or Worker redo. New decisive old-source7FAIL/7PASS; corrected patch63PASS6.72s, adjacent1074PASS60.52s, tokenizer11PASS0.39s and cold-library2PASS1.58s; mypy117/ruffPASS. Independent Sol/high review no concrete P0/P1/P2. Cold tests preserve actual request identities and200000 Critic usage through both failed-intent/error-layer gaps, no new handoff. Fixture and parent integration rework retained in plans/2026-09-12-phase3/p35/critic-hold-admission-fix.md. Clean committed real v7 pair and current cumulative/source-native gates remain pending; P34/P35-A04 not yet reclosed. No packaging/P36/push. SDK fix commit6c17d4d clean focused recheck76PASS8.22s/runner8.67s; actual v7 pair now running. Historical14:22 P35 completion is superseded until the reopened A04 gates pass.

**最后更新：2026-09-13 14:22 CST — P3.3/P3.5 功能范围累计验收完成。** 干净SDK f25a4de完整编排1826PASS/0FAIL/9真实Provider默认SKIP，pytest626.58秒、runner627.15秒，g-current-orchestrator-full-v10；父唯一pytest进程组已退出无残留。P33原46行/47断言/100selector已关联，本轮均非跳过；current-ac-association-v4.json SHA256 bbf8088f0f5eb279a14e86b4f6f98f12460db9e4d001feb4305ebb8a830d90f4。原始回放1265DB/1400数据库Mission身份/7670观察仍raw OPEN：116finding全归因（50负向、66直接状态/历史夹具），944诊断逐项保留（917canonical target在完成的扫描根、14原快照与搬移副本双hash一致、9负向、4非Mission SQLite夹具）；没有整测试豁免或宣称raw unknown为空。检查点间未观察删除文件及完整execution回放仍属范围限制。后继原生34DB/46Mission/80观察独立PASS零差异/错误/额外调用，0.847秒，Host replay-all-native-v5.json SHA256133846607875ee93355545f949cd461a378b25070bf5174f4cc71230f5050997。snapshot-v26含最新ed42919生产代码，COMPARE原生新建交付及冷读通过，18调用/0rehandoff/40journal/18selection/94Mission事件不变，生命周期178.704/90.287秒；部署层仅预期PolicyConfigDrift，ACTIVE未变。P35原8项均已有决定性软件/原生证据并关联此全量，8/8完成；P33按已批准源码载体范围完成，未声称安装包验收。P34固定真实pair v5两臂仍budget_exhausted，完整交付/收益门OPEN；默认FIRST不变，B240K→480K/S120K→240K且总2M不变的新对照提议待用户选择，不改旧失败实验。整体Phase3未完成，不打包/P36/推送。

原生退出补充：资源包装器所属进程组均正常退出，但后续进程盘点发现独立carrier PID71160（无backend/Vite监听）。已核对其完整路径属于source-ui-compare-v26，用SIGTERM退出并确认消失；不把进程组为空等同全部原生进程为空。额外窗口出现原因未判定，不冒称应用正常退出链已修复。Host证据 source-ui-compare-v26/detached-carrier-cleanup.json SHA256655eaf6269be6d8f2743cb1b136154b1ef66c40a652c0fd0a6b5d9fbd2b9b7a4。Mac防熄屏caffeinate仍保留。

**最后更新：2026-09-13 14:13 CST — 最新生产源码原生比较及冷读通过。** snapshot-v26（SDK ed42919，Host af8a490c；SDK f25a4de仅测试/文档后继）通过真实UI新建比较Mission `mission-78c1ee65bf2a4820`，两个候选分别实际code_test通过，独立C读取二者并正式交付。UI候选/综合状态正确，最终result.txt 45字节，SHA256 `8f6e827eda48a0d58a0722ea59494ab2273893c8e41ac2d137084f1bfabb99a7`。同源码冷启动UI重新打开全文；Mission/Task/产物/18 Provider记录逐项不变，0rehandoff，2700结算/0预留，40journal/18context selection/94Mission事件。全局事件99→100仅PolicyConfigDrift（ACTIVE candidates_per_task=2、启动配置=1），冻结ACTIVE保持，无重复调用。原生生命周期178.704秒、冷读90.287秒，正常退出且无进程组残留。受控Provider机制验证，不冒称真实模型收益。证据 `.local-test-evidence/2026-09-13/p33-g/source-ui-compare-v26/case-summary.json` SHA256 `780e52b243135f714bba7a86a00acaa3908cb28344c0846c65909894c52a04a3`。最新独立原生回放覆盖34DB/46Mission/80观察，PASS零差异/零错误、调用/效果/事件计数不变，0.847秒；`replay-all-native-v5.json` SHA256 `133846607875ee93355545f949cd461a378b25070bf5174f4cc71230f5050997`。SDK最新全量v10进行中，真实模型固定预算两臂交付门仍OPEN；不打包/P36/推送。

**最后更新：2026-09-13 13:13 CST — 全部既有原生状态回放与真实对照结果。** 当前SDK33b25b5的只读回放覆盖33个数据库、45个Mission、78次观察，0差异/0错误，Provider/执行效果/事件计数不变，0.781秒；Host原始索引 `.local-test-evidence/2026-09-13/p33-g/replay-all-native-v4.json` SHA256 `5d17f7df6d9b4903c4908d0e2b508bb3b9b1ccc6b07c09a8b0a05d9d37ce7b33`。新增Context v23、压力v24和文案v25均纳入，未产生新调用。SDK33b25b5真实deepseek-flash固定对照v4仍FAIL：FIRST270.300秒/443141 tokens/54实际调用与准入，F/B/C完成、最终S在保留首Critic额度后预算不足；COMPARE121.889秒/82812 tokens/12实际调用与准入，B首候选预算不足。原始任务、材料及预算未改变；无成功交付或质量优势声明。SDK证据 `.local-test-evidence/2026-09-13/p34-real-search-value-053c8ec4de264f18b16d36c96d816861/`，runner392.82秒。当前SDK全套回归与本次消耗归因进行中；P33/P34/P35累计门仍OPEN，不打包/P36/推送。

**最后更新：2026-09-13 12:55 CST — 候选状态文案原生复验通过。** snapshot-v23（SDK4ba53f4/Host5a939d6b）新建批准COMPARE Mission `mission-a595c73143d3275f`，两个已验证候选与独立C完成；实际UI显示“已验证的候选输入·已用于综合”和“最终综合结果·综合轮已提交”，不再显示待比较。UI打开最终result.txt，45字节/hash `8f6e827eda48a0d58a0722ea59494ab2273893c8e41ac2d137084f1bfabb99a7`，18调用/0rehandoff/40journal/94Mission事件；原生生命周期338.287秒，正常退出无残留。本次是新源码完整交付及文案复验，冷读沿用v21既有证据，不声称新一轮冷读。v21同目录跨源码resume被身份校验正确拒绝（0.449秒/无残留），改为新目录完整运行，原失败保留。Host `.local-test-evidence/2026-09-13/p33-g/source-ui-compare-v25/case-summary.json` SHA256 `48f8e049dc20d12e1264484ac6750c0dd182fd708233080c2508286de95d677c`。P34真实模型完整交付及P33/P35累计回归仍OPEN。

**最后更新：2026-09-13 12:48 CST — 完整原生验证背压。** snapshot-v23（SDK4ba53f4/Host5a939d6b）真实UI提交7个短来源Mission，2个实际Verifier有界等待。持久采样观察2RUNNING+2PENDING达到总上限4，BackpressureRaised seq140；首个验证转待人后再派发的Worker预留减为10000，随后总数降至低水位2，BackpressureCleared seq192，恢复20000，间隔20.222秒。UI读取Raised/Cleared历史、逐一打开83字节review.md并复核通过，7Mission全部COMPLETED/各900结算/0预留；42Provider记录/0rehandoff/105journal在人审前后不变。峰值未及时截屏，峰值与减速由同一次真实运行的持久事件/采样/预算证明；不是手动释放，两个Verifier均20秒自动解阻后运行真实Critic。进程组正常退出无残留，生命周期512.800秒。Host `.local-test-evidence/2026-09-13/p33-g/source-ui-pressure-v24/case-summary.json` SHA256 `616bb3deed0ca3c76a9212ec9c4fb67582ca120a4311c459a1d78c5743fdb5bd`。关闭P35-A03原生阈值/减速/排空缺口，保留最终回归与新发现的system Worker尾部增长问题；整体未完成，不打包/P36/推送。

**最后更新：2026-09-13 12:36 CST — 长Context原生交付与冷读通过。** snapshot-v23（SDK4ba53f4/Host5a939d6b）通过原生文件选择器导入原97,200字节来源，Mission `mission-cb2cd1cb76fc88ee` 正式交付，2550结算/0预留。12个8,100字节页面全部保留在真实SDK journal，8个Worker请求出现持久Context轮转，原instructions/user_input及完整工具组保留，实际Provider观察hash与selection逐一相符。UI打开380字节REPORT.md（SHA256 `c4f9c9eb9c57dca52dd45cf720f66092a0f401f335899a6ad69a263184b3c9d9`），首段/原约束/末段引用完整读取；冷启动读同报告和约束引用，51事件/17Provider记录/0rehandoff/37journal/17selections完全不变。原生/冷读生命周期299.810/271.067秒，两进程组正常退出无残留。受控Provider机制证据，不代表真实模型记忆质量。Host `.local-test-evidence/2026-09-13/p33-g/source-ui-context-v23/case-summary.json` SHA256 `7a0b1f773879e3f07b99f5f16d2d85a7f815e7427fd4a8c0df87e09a78fa1e0b`。关闭P35-A07当前原生轮转/冷读缺口，整体仍待完整压力与最新回归，不打包/P36/推送。

**Last updated: 2026-09-13 11:33 CST — native COMPARE and display correction.** Source snapshot-v21 (SDK f6115ed / Host485679e7) native UI selected approved two-candidate policy, submitted Mission27f1933fe6cf3134, and read final result.txt (45 bytes, SHA256 8f6e827eda48a0d58a0722ea59494ab2273893c8e41ac2d137084f1bfabb99a7). Both candidate code_test PASS, independent C code_test PASS and COMMITTED. Cold UI reread preserved Mission/Tasks/artifacts,18 Provider records,0 rehandoff,40 journal;94 Mission events unchanged. Global99->100 adds expected deployment PolicyConfigDrift for promoted2/config1, not a task rerun. Native/cold lifecycle186.640/151.657s,exit0,no residual. Summary Host .local-test-evidence/2026-09-13/p33-g/source-ui-compare-v21/case-summary.json SHA256 edd6cb7c64a7edd0b6f0a5c345ed13f0723b89fbb76b8e76ae1b4e0f51fd1130. Native test exposed completed candidates still labelled waiting; MissionSearch now distinguishes adopted inputs, unselected verified candidates, final synthesis, and stopped/failed states. Frontend77PASS1.17s, typecheck/lintPASS; corrected native wording pending. Long Context nativev21/v22 remains NOT_COMPLETED due CUA noWindowsAvailable after file import; no Mission/rotation claimed. Full SDK f6115ed default sweep1753PASS6missing-tiktokenFAIL12SKIP/592.51s; installed pinned test dependency0.14.0 and affected52PASS1.67s including all6 failures+3optional counters. Cumulative non-network1762PASS9real-provider default skips; actual P34 pair separately failed. Journal/context16PASS5.68s. P33/P34/P35 overallOPEN, no packaging/P36/push.

**Last updated: 2026-09-13 11:11 CST — current editable source fixtures.** Approved COMPARE now exercises two verified candidates and independent C with portable copied inputs in final artifacts. Long Context case reads twelve actual 8,100-byte pages, preserves original constraints and unresolved marker during window rotation, retains complete journal, validates three whole-unit citations, and checks cold-read invariants. Explicit test cases only. Correct editable SDK 826c0e1 and actual source attestation: g-native-current-source-integration-v8, 50 PASS / 1 SKIP in36.59s (runner37.12s). Raw evidence in SDK .local-test-evidence/2026-09-12/p33-g/. Native UI for these two new cases remains pending; no packaging/P36/push.

**最后更新：2026-09-13 10:51 CST — 源码原生负载、独立恢复与搜索链验收。** snapshot-v17（SDK0a1a050/Hostc61744d6）：三Mission/两物理槽中第三任务真实UI取消，释放前后无实际Provider调用；官方backup/restore到独立userdata后，成功/取消/待人三状态及96事件、14 Provider记录（13succeeded/1claimed）、34journal完全相同，恢复副本真实UI复核后正式交付，调用不增、rehandoff0。B2 summary SHA256 `0d4e3c76fbbf35ddc7ccda3eb5241e71147c92e234f9814ab01db25460591efb`。原生Verifier压力v19：UI显示第三Result PENDING，UI时点持久事件对应2RUNNING+1PENDING，20秒自动释放后3任务均交付；手动marker未观察，不声称pending上限4饱和。summary SHA256 `c67edef26d8360b5feb53f8c068d05904f1a2f54e2545326976a1b911611f6c7`。FIRST原生v20：保留A三次失败，F仅核选中片段，Manager改C依赖为F+B，C实际测试通过，S读取已验证C并实际测试通过；UI打开final.md，冷启动同hash `bc04ba9b12d5ab4e0729599c2cce15ca42d715152ea84e81484f0f78ac1c73c3`、26调用/0rehandoff/62journal/192全局事件不变。summary SHA256 `7ebfacc0d9cc21f1d3989598f13b320fdc97d423fc81095f37a581e7463a3479`。均为受控Provider机制验收，不冒称真实模型质量。Host证据根 `.local-test-evidence/2026-09-13/p33-g/`，对应source-ui-b2-restored-v18b/source-ui-pressure-v19/source-ui-search-v20。只读回放replay-all-native-v2.json：27数据库/35Mission/62观察，PASS零差异/错误，.730s，无调用/效果变化，SHA256 `a2cc5a33ba08f4dfe1c1c6cb3452148d37367b15dc2f003dcf829ef23f57382b`。当前SDK826c0e1五项回归修复56PASS/20.03s，新全量待；P33累计关联/P34/P35仍OPEN。真实FIRST/COMPARE原固定pair两臂预算失败已保留，测试漏接原生精确tokenizer/逐请求准入的配置正在修正，尚未重跑。长Context原生rotation与批准COMPARE UI待。不打包/P36/推送。

**最后更新：2026-09-13 10:12 CST — 原生负载测试入口。** 增加独立源码受控场景：三Mission/两物理模型槽，以及两个真实Verifier在Critic前有界等待、第三个Result排队。仅控制外部Provider和验证等待；原Planner、工具、format/rule、Critic、人审、取消、预算与实际验证结果均由正式运行栈产生。控制文件在独立userdata目录，原fixture输入不变。等待最多20秒且保留租约余量，超时记录notobserved后放行真实Critic；仅证明2个Verifier占用+1个待验证，不声称压满pending上限4。受控路由/取消/长资料/启动器/搜索38 PASS/16.03s（runner16.61s），g-native-pressure-host-v4；静态复审normal runtime不受影响。该压力场景要求新控制目录，已有trace或marker时同目录重建会拒绝，不作为压力恢复证据。实际原生负载UI尚待；N1doc9原始资料及冷恢复已通过。P33累计审计/P34/P35整体仍OPEN，不打包/P36/推送。

**N1 original-source acceptance — 2026-09-13 10:09 CST: PASS.** Source snapshot v16 (SDK aada164 / Host ba6be341), doc9, same two original files and original 400000/12 goal/budget: Mission mission-0b12722003e0b883 COMPLETED/verification_passed in291.398s, one Worker Attempt,320365 settled/0 reserved. Actual assistant journal submitted ordinal refs; canonical Claims:11 VERIFIED source attributions,3 SUPPORTED analyses,1 UNDER_REVIEW structural statement. REPORT SHA256 `812114f5b9f1252a56d43e4ff6815961a031aeec01e62da3f751103c0c9c3e52`. Parent opened report and both-source citations in native UI, including complete HA-12 row and 99-character conditional unit; full CAS report matched displayed hash. Parent and independent Terra medium review PASS: build/startup failure records acknowledged; historical verification is not current installation evidence. Same-source cold UI reread preserved artifact/citations/status and14 Provider records (11 succeeded/3 failed),0 rehandoff,60 events and SDK journal counts. Native/cold carrier lifecycles651.361/104.222s include manual inspection, both exit0/no residual. Host evidence `.local-test-evidence/2026-09-13/p33-g/source-ui-n1-v16/case-summary.json` SHA256 `195b17df5a66ee13937410abbbe59b4e75c255a49fa6766ee75808afc1972bde`. Historical failed N1 runs retained. This closes current N1 content/native/cold gate, not P33 cumulative audit or overall P34/P35. No packaging/P36/push.

**最后更新：2026-09-13 09:48 CST — 独立备份恢复的原生 UI 核对。** SDK 1f0c536 / Host 65e05252（source-snapshot-v15）的失败原始文档任务，经官方 offline backup/restore API 恢复到独立源码实例。实际 UI 显示原任务 budget_exhausted、doc8、284857 已结算/0预留、两来源原版本与无正式 Claim；两库任务/预算/45事件/SDK journal计数完全相同，14条Provider记录的状态及0 rehandoff保持，未新增调用。原生载体56.508s，退出0且无残留。证据 `.local-test-evidence/2026-09-13/p33-g/source-ui-b1-restored-v15/case-summary.json`，SHA256 `2e9fde82becffbe655ca53bd0065c1266042280260e62291c88d99732d03d137`。只关闭这个终态失败任务的恢复可见性用例；不证明成功报告、待审批或UNKNOWN恢复全矩阵。P33/P34/P35整体仍OPEN，打包/P36/推送暂停。

**Last updated: 2026-09-13 09:15 CST.**


## 2026-09-13 09:15 CST — Source synthesis checkpoint

Host doc8 compatibility plus controlled search and launcher regression: 34 PASS/12.33s (g-native-search-doc8-host-v4; wrapper12.83s). Synthesis form UI101 PASS/1.47s and typecheck remain unchanged. Native P34 and real N1 doc8 quality recheck are pending. This commits the already reviewed source feature/wiring; new native load files remain a separate unverified slice. SDK1f0c536 preserves legacy runtime controls; full regression has not yet passed after the three old-test assumption repairs (focused16PASS9.33s). No packaging, P36, release or push.

**Last updated: 2026-09-13 08:44 CST.**

Source-native checkpoint, 2026-09-13 08:44 CST. Optional final independent synthesis is now exposed in Mission creation with explicit goal, criteria and bounded token/attempt budgets. Code/document requests use the same public schema; failed retries keep identity until content changes; success clears synthesis fields. UI101 PASS/1.47s and typecheck PASS (g-ui-synthesis-v2). Controlled P34 Host/default-policy fixture preserves two failed A Attempts, independently verifies F, keeps B, retargets C, executes C and final S probes, and cold-reopens without Provider replay: 33 PASS/8.04s (g-native-search-host-v2). Independent Terra review found no P1/P2. These are software results; source-native P34/synthesis UI is still pending.

**Last updated: 2026-09-13 08:24 CST - source-native slot binding.**
**最后更新：2026-09-13 08:12 CST — doc7 原生仲裁与冷重开。** SDK aaa3593 / Host 648ad185 的源码快照v13，经真实表单导入两来源，240000/6总预算与30000冲突预留实际生效；两Worker/Arbiter/独立Critic后UI进入待仲裁，展开两份完整原句、通过UI提交contextual并打开实际245B仲裁报告。审批GRANTED、Conflict RESOLVED_BY_HUMAN；两原主张保持DISPUTED、0知识条目，Mission按claim_not_usable成为mission_criteria_unmet（不是交付成功），2850已结算/0预留。新建表单预留为空，原生复验了跨任务残留修复。相同源码/数据冷重开后原身份与状态保持，Provider19/19、0rehandoff、12工具效果不变。原始证据Host `.local-test-evidence/2026-09-13/p33-g/source-ui-arbitration-v13/case-summary.json` SHA256 `5c7946166a2593bdafd0edfb5f92a53bdf1400fec9d652c2297d232c41a9a967`。初始/冷进程组均退出0无残留，539.712s/43.737s为包含人工操作等待的载体生命周期，不是模型运行时间。此为受控原生仲裁边界通过，不是真实模型能力或Phase3整体完成。

**最后更新：2026-09-13 07:35 CST — 冲突核对预算表单。** 新建 Mission 可显式从总 Token 预算中预留冲突核对额度；留空不发送预留字段，非法值、负数、小数及超过总额会阻止提交。文档原子创建和重试保持该值，创建成功后新表单清空。Luna 独立审查发现并复验通过跨任务残留问题；主线程前端 85 PASS/1.33s、typecheck PASS。原始证据 Host `.local-test-evidence/2026-09-13/p33-g/g-ui-conflict-reserve-{green,typecheck}-v4.log`。源码启动器已登记该仲裁用例，20 PASS/0.11s（g-launcher-arbitration-v1）；当前源码原生仲裁表单仍待验，P33/P34/P35 整体 OPEN。

**最后更新：2026-09-13 07:09 CST — 已有源码原生运行全量回放。** 对16个已关闭原生运行库的全部17个Mission（含失败运行）及16个部署事件流执行只读回放，33个观察全部PASS、0差异/未覆盖/发现/读取错误，耗时0.532s；Provider调用、工具效果、Action、Context selection和事件计数前后相同。原始索引Host `.local-test-evidence/2026-09-13/p33-g/replay-all-native-v1.json`，SHA256 `376f19c68746cee73c8f186fb8b66e4aa099e3f2cb944718d2db34013b15cce5`。仅证明已存在原生运行的编排回放；执行库为清单枚举而非完整SDK执行回放，新后继UI与最终全测试观察仍待完成。P33/P34/P35整体OPEN。

**最后更新：2026-09-13 07:06 CST — 当前 doc7 正式仲裁受控链路。** 隔离 document-ui 入口新增 contextual arbitration 场景：两个实际 Worker 读取带环境限定的来源、提交有引用支持的非逐字世界候选，经正式冲突检测进入DISPUTED；实际Arbiter读取两来源及两产物、独立Critic读取仲裁报告，公开人工contextual裁决后冷重开保留原裁决且零新模型调用。14 PASS/25.55s（runner26.05s），包含真实测试路由、来源场景和隔离门；证据SDK `.local-test-evidence/2026-09-12/p33-g/g-host-doc7-route-v5.{json,log}`。首轮引用归属/世界主张混淆和后继状态断言失败保留。doc7新增Manager模板，历史doc6仍可读。此项是受控Provider软件证据，当前源码原生仲裁UI仍待验；P33/P34/P35整体OPEN，不打包/P36/推送。

**最后更新：2026-09-13 06:37 CST — 启动失败生命周期。** SDK enter失败会确定性关闭已装配runtime与Store，并保留原WorkspaceCleanupIncomplete和UNKNOWN占用；Host rebuild候选仅在enter及facade成功后发布，失败关闭候选且后续完整重试，不能直接run半初始化对象。SDK3 PASS/.32s；Host新控制4 PASS/.19s，连同生命周期/并发回归16 PASS/12.11s。首次失败恢复仍须deactivate/activate创建新service/client，不复用已关闭HTTP client；没有新增首次启动自动重试。命令与原始证据在SDK `.local-test-evidence/2026-09-12/p33-g/g-startup-failure-lifecycle-v1` / `g-host-lifecycle-affected-v2`。

**最后更新：2026-09-13 06:32 CST — 拒绝原因原生复验通过。** 源码SDK b0f8dd7 / Host d9d56461、快照v12，原生创建document-v6任务后先批准来源撤销，再批准原报告：界面明确显示结果最终未接受（FAIL/DONE）、stale_source、revoked及原版本，Claim仍UNDER_REVIEW；原layer和人审PASS历史保留。Mission mission-aa32b2525a480041，唯一Attempt，0 VERIFIED，Provider前后6/6/0，900已结算/0预留。证据 Host `.local-test-evidence/2026-09-13/p33-g/source-ui-n6-active-revoke-v12/case-summary.json` SHA-256 `964c37865575bd3d2762247b6ff58c2cfda40082873a5e51bc4740f01f771efa`。PG78917已退出/无残留。该受控原生结果不代表真实模型质量或其他Phase3门槛关闭。

**最后更新：2026-09-13 06:26 CST — 最终拒绝原因投影。** Task结果现在直接显示持久的Result verdict/state；对于DONE/FAIL，仅从完全相同的Attempt/Task/Mission投影白名单拒绝原因与来源版本，保留先前layer通过记录和UNDER_REVIEW Claim，不从人工GRANTED推导结果接受。后端46 PASS/8.36s（SDK c8e2541隔离源码环境；旧backend venv产生1项预算usage兼容失败并保留），前端79 PASS/1.38s，typecheck PASS。新源码快照原生验证待完成；此前v11撤销runtime负例保持有效，新的显示修复尚非原生PASS。

**最后更新：2026-09-13 06:15 CST — FIRST 与 N6 新证据。** SDK b0f8dd7 / Host c6beb926 源码快照 v11 原生 N6 已验证：1/2 不确定条件允许交付且明确保留不确定性；2/3 不确定条件任务PASS但Mission为 insufficient_evidence；active-revoke先批准撤销来源、再批准原报告，原结果DONE/FAIL（stale_source）、产物REJECTED、0 VERIFIED、唯一Attempt、Provider仍6次，900已结算/0预留。三例为受控原生UI，不代表真实模型质量；分别证据 `source-ui-n6-half-v11`、`source-ui-n6-two-thirds-v11`、`source-ui-n6-active-revoke-v11` 位于 Host `.local-test-evidence/2026-09-13/p33-g/`。原生最终拒绝原因未在Task验证列表直接展示的问题仍在修复。FIRST定向56 PASS仅SDK工作树，尚未纳入该UI快照；文档冲突仲裁UI、O4全量及P34/P35整体仍OPEN。

**N6 显示修正 — 2026-09-13 05:44 CST：** 原生 n6-half 验证1/2不确定条件按原策略可交付，局限与INCONCLUSIVE均保存；UI“实际判定：满足”措辞会误导，已改为保留不确定性，Mission统一显示“通过交付判定”。新增UI反例先红，修复后26项通过；新快照原生复验待完成，不能把该措辞修正算原生PASS。

**原生边界与恢复检查点 — 2026-09-13 05:40 CST：** 新冻结 SDK c8e2541 / Host b7dc4c64 综合1127 PASS/75.26s。N1v9真模型正式交付及同源冷恢复已核对；新Host显示修复在受控原生来源指令用例验证。N4来源指令归属、错误逐字引用、矛盾证据三例原生UI符合预期，独立原始证据保存在Host `.local-test-evidence/2026-09-13/p33-g/source-ui-n4-*-v10/`。实际OS SIGKILL后两库冷恢复2 PASS/9.59s：成功结果零重复Worker、独立Critic读产物；UNKNOWN保持原token/cost占用。仅覆盖该两边界，不覆盖完整Mission或P32逃逸进程恢复。FIRST新保护虽18PASS/0.91s，独立审查仍有系统hold丢cap和priced分别取整2项P1，修复中。P33剩余N6/active管理/O4、P34综合价值场景及P35其余门槛保持OPEN，不打包/P36/推送。

**源码与原生 UI 检查点 — 2026-09-13 05:25 CST：** N1v9 原始两文档、400000/12 原目标在 SDK c9a1f183 / Host 45c09756 源码环境完成：220.968s，正式 REPORT f6b192a3…f905、6 条 VERIFIED 逐字引用（两来源、完整表格行、完整限定单元），242431 tokens 已结算/预留0，13 次 Provider handoff。真实 UI 读报告、引用并冷启动重读，调用仍13/无重复；文档区“尚未判定”投影缺陷已修复，后端13 PASS/0.06s、前端25 PASS/0.912s及typecheck通过，新 UI 待验。动态新增已完成依赖的 Task 回放修复42 PASS/36.16s，原 v14 #14 历史43事件全覆盖/无差异；Python3.12空AST字段兼容35 PASS/0.29s，保持原生产基线。总体P33/P34/P35仍OPEN；进程kill测试仍在修复，FIRST请求保护仅helper7 PASS未集成；不打包/P36/推送。

**当前源码检查点 — 2026-09-13 04:49 CST：** P35 离线备份 20 PASS/17.29s；租约丢失恢复及取消 10 PASS/23.14s，受影响取消/恢复/租约回归 44 PASS/6.68s。N1v8 真模型仍失败：18 次实际调用、378113 tokens 已结算、当前预留 0；已定位 RUNNING 时终态 ordinal_to 为空造成 180s 错误超时。改读 SDK 持久进度的定向检查 8 PASS/8.98s，保持真正停滞超时控制；Host 六类文档场景及启动器 28 PASS/12.74s。上述为以 SDK e4da042 / Host 985e403 为基线的未提交修复证据；新原生 UI 待验，P33N1/P34/P35 整体 OPEN，不打包、不执行 P36、不推送。

**Source and native checkpoint — 2026-09-13 04:13 CST:** SDK protocol-error response parsing preserves independently valid Provider usage while still rejecting malformed tools (26 PASS/1.36s); missing/invalid usage stays unknown. Late-accounting automatic original-subject import/settle11 PASS/2.90s and receipt boundaries5 PASS/0.47s, independently reviewed. Citation repair retains failing claim/index/source/line identity without source-body reinlining3 PASS/0.46s. Broader integration v14 is still running/stalled in legacy recovery, not PASS. N1v7 was UI-cancelled after malformed-tool response without usage,170532 settled/108083 unknown held,13 physical handoffs, no successful value acceptance; original proof retained. Controlled source UI N2 delivered two28-Claim Missions (750 tokens each/zero reserve), actual long block290080 characters reached END_OF_LONG_TABLE, in-flight citation switching/CAS error and restored retry observed. N3 source supersede/revoke-reject/revoke-approve and historical read observed; cold verification in progress. N4/N6 boundary software27 PASS/10.67s including launcher identity, native cases not yet run. Whole Phase3 gates remain OPEN; no packaging/P3.6/push.

**Latest native/source checkpoint — 2026-09-13 03:26 CST:** N1v6 produced a rejected REPORT and9 proposed Claims; no formal acceptance. Rule failure from generated free-text quality criteria, then11 zero-call context-overflow retries.128156 tokens settled/currentreserved0, UI observed and exited cleanly. Repair5 software controls passed; broad1051 PASS2 regression FAIL3 optional skips. P34 fragment runtime2 failures; P35 priced rounding reserve review P1 open. N1–N6/O4 and overall P34/P35 remain OPEN. Details/current commands/evidence in Phase3 journals; no packaging/P3.6.

**Source checkpoint, 2026-09-13 03:12 CST:** integrated source checks:1037 PASS/2 legacy schema FAIL (48.27s); pre-schema15 reserved-attempt read compatibility fixed, targeted9 PASS/0.36s. Includes15 candidate controls, Mission system pool7, FIRST6, priced cold1 and missing-usage boundary2. COMPARE decisions and full immutable payloads now replay; frozen candidate deadline cannot dispatch new pending candidates. Controlled Host document fixture software2 PASS/6.21s proves28 formal citations and >256KiB paging; frontend search51 PASS/1.04s and typecheck pass. Fragment branch remains5 output-conflict failures (16 other controls passed); Mission-system runtime hooks, SUCCEEDED-missing-usage settlement, N1–N6/O4 and real P34/P35 gates remain OPEN. No release packaging/P3.6. This is an incomplete development checkpoint.

**Runtime checkpoint, 2026-09-13 02:50 CST:** default FIRST Critic tail reserve/consume/release is connected to actual production dispatch and passed6 controls/0.58s. Typed denial collection3/0.36s; true two-SQLite priced cold reopen1/0.40s. Scope excludes OS-kill, future Mission-level system pools and SUCCEEDED-without-usage late accounting. P34 joint run has3 FAIL/4 PASS/5 setupERROR; source inheritance defect identified and being repaired. N1 and remaining native/full audit gates remain open. See current journals; no packaging/P3.6.

**Source budget checkpoint, 2026-09-13 02:35 CST:** public Mission snapshot now carries current ledger usage in the same read transaction (39 SDK controls/5.12s); Host projection uses settled/current reserved values instead of historical Attempt totals (22 controls/8.42s; frontend49/0.941s). Provider tail/price primitives and durable typed denial:19 controls/1.00s. FIRST tail runtime wiring and native verification remain open; no release or completion claim. First-run collection/fixture failures retained in journals.

**Latest native checkpoint, 2026-09-13 02:25 CST:** N1 v5b (Host133aaa62 / SDKdfc9b7c) FAILED: sole Task90000/4 exhausted its attempts despite Mission400000/12. Both original sources were read in4 pages; no REPORT. Eight physical calls all settled, Mission86732 tokens/current reserved0. UI44494 reservation display is a confirmed projection bug; correction and typed denial stopping are in progress. N1–N6/O4 and P3.4/P3.5 remain open; no packaging or P3.6. See current Phase3 journal for immutable evidence.

**Current source checkpoint, 2026-09-13 02:08 CST.** Host source profile/projection16 controls and launcher19 controls pass. Full orchestration source run v4 observed180 PASS/5 FAIL (164.55s); three frozen-package inventory checks do not apply to editable installation and remain unpassed under the user-paused packaging gate. The document fixture omitted mandatory critic_review and a path assertion matched the runner ancestor; both corrected controls pass in v5 (2 PASS/4.78s). Remaining source modules v6:27 PASS/1 fixture-path FAIL (31.78s); the pure scenario-path oracle now supplies a path actually outside ignored evidence and passes v7 (1 PASS/0.04s). Production test gates remain unchanged. These are source software checks; new N1 native run remains open, v4b failure preserved. No release packaging or P3.6 work.

## Earlier checkpoint details

Source launcher identity controls: 19 passed, pytest 0.11s (wrapper 0.76s); native startup remains open. Resource identity covers metadata, not payload bytes. See the Host G journal for scope and evidence.

最后更新：2026-09-13。P3.3 G 仍在源码 UI 验收；P3.4/P3.5 仅必要接缝先行，未整体完成。当前新 Host source profile 将显式 editable SDK 的 Context 与逐请求 token 计数接到同一官方 tokenizer；旧执行池维持冻结配置，普通 wheel 模式保留原入口。`g-host-source-profile-summary-v3` 用独立源码 SDK 环境验证 profile 与文档摘要显示投影：16 passed / 0 skipped，pytest 0.34 秒、wrapper 0.98 秒；这是软件接线证据，不是原生 UI 或真实模型通过。源码启动器正在补恢复身份核验，新 N1 尚未开始；N1 v4b 真实预算失败仍保留。安装打包、发布和 P3.6 暂停。

最后更新：2026-09-13 01:04 CST。P3.3 G源码UI N1v4b已实际完成失败路径：Host e690bdcf、SDK e346689，Mission mission-61a22dea64fa4841为FAILED/budget_exhausted；真实flash35次成功、1次协议失败，已报告344854 tokens，不能说Mission花满400000。Worker1在90k Task预算下耗224780 tokens，Worker2耗115407，下一次Task预留时才拒绝。17页完整原文已实际可见，但模型一条引用错行、两条分析缺来源引用，rule正确拒绝。大页/逐请求预算与提交契约后继正在修复，N1/G未通过。原生正常退出PG2135无残留；不打包/P3.6。另修复Host诊断摘要读取：SDK摘要在detail内时不再显示空白；30项投影控制通过/8.31秒（wrapper8.97秒），实际UI后继重验尚待。见[Host G journal](../plans/2026-09-12-phase3-host-g/journal.md)。

最后更新：2026-09-13 00:31 CST。P3.3 G 的显式 SDK 源码身份已实际冷启动并从真实 UI 创建文档 Mission：Host d0c1ee4c、editable SDK 307 输入已核，manifest 分开 source_verified=true 与 installed_wheel_verified=false，Service pin 保持。N1 v3 业务仍 FAIL：长工具结果被 Context 截成预览，模型无法取得后文表格；主通过 UI 取消，正常退出 PG94878 无残留。另暴露 Critic 高频续租、取消及120秒外层超时的收尾缺口；SDK 修复的首批分页/实际 Context/取消控制39项通过，恢复与超时补证及原场景重测仍待完成。P3.3 未交付；P3.4/P3.5仅准备，P3.6和安装包打包暂停。证据与分层结果见 [Host G journal](../plans/2026-09-12-phase3-host-g/journal.md)。

最后更新：2026-09-12 23:44 CST：P3.3 G 源码开发接线新增显式 `editable-source` SDK 身份。正式 wheel 默认与 Service 0.3.13 pin 保持；仅非frozen进程、显式模式及独立source attestation可加载SDK源码。校验Git根/commit、两个生产包与数据全集hash（含新增文件）、editable安装metadata、版本及实际模块origin；文档修改不改变生产输入。main组装与RuntimeStack启动接线，编排manifest分别显示source_verified与installed_wheel_verified，不把源码当成旧wheel。71项定向控制通过/6.55秒，含真实composition启动前拒绝与依赖边界；主审及Ohm独立限定ACCEPT。实际源码冷启动与N1复验尚待完成；首次源码N1真实deepseek-flash任务因引用字段schema及Task预算不足FAIL，详见Host G journal。功能仍进行中，P3.4/P3.5未验收，打包/P3.6暂不执行。

# Agent 编排（任务编排视图）· 生产事实

- 最后更新：2026-09-12 23:05 CST

当前决定：用户批准直接运行源码Tauri UI完成P3.1–P3.5功能验收，暂停安装包构建与发布，不含P3.6。冻结环境探针新增明确不可执行原因，避免把后端应用当Python解释器；四项控制通过及独立限定ACCEPT，源码模式仍执行真实探针。源码开发启动已到orchestration_ready/available；共用日志过滤补URL query凭据脱敏，26项控制通过；真实UI任务尚待。此前安装包失败保留，下面为历史定位过程。

P3.3 G进行中：Host已接入原子文档创建、来源版本审批、绑定引用全文读取与系统结论展示。候选SDK0.11.1（源码a5c8fca，wheel49137655…）已钉版并安装，Service SDK保持原定0.3.13；本机旧venv的Harness0.7.2/Service0.3.12已对齐。Host实际安装组合定向49项、前端86项组件测试/typecheck通过。干净Host c3d4e227的macOS PyInstaller与Tauri构建成功，浏览器原件/许可证及构建身份核验通过；但原生首次启动因SDK公开延迟导入的workspace_binding_protocol未入包而失败，尚未发出本次真实flash请求。公开延迟导入已修复并在第二版真实启动越过；第二次lifespan因工作流handler源码未随包导致稳定manifest编译失败，源码收集修复的3项控制通过，仍待原生复验。第三次真实启动已越过workflow源码检查，但缺Tool目录JSON；资源全集修复14项控制通过，待第四版原生复验。不宣称P3.3交付。当前失败与后续修复见[Host G journal](../plans/2026-09-12-phase3-host-g/journal.md)。
- 计划与记录：`plans/2026-09-11-orchestrator-host-integration/`（plan 第 3 版、acceptance、journal）
- 方向依据：用户的 Phase3 计划 `plans/taskSys2/agent-orchestrator-phase3-plan.zh-CN.md`。本模块是其中 **P3.1 真实 App Mission 控制闭环** 的 Host 直连实现。
- SDK：`simple-harness-sdk` 的 `agent_orchestrator`（与 `simple_harness` 同在一个 wheel 里）。Host 钉版以 `backend/deskpet/sdk_adapters/sdk_candidate.py` 为唯一来源。

> 状态（2026-09-12）：P3.1 Host 直连路径已交付。
> - 自动化：`tests/orchestration` 107 passed，vitest 772 passed。
> - 真实 deepseek-flash 运行：HA-11 通过。
> - 原生 App 验收：HA-12 ①–⑥ 全部通过，用的是 verify bundle `f51ddc37`（debug .app 加源码后端），报告见计划目录的 `reports/native-ui-run1.md`。
> - 冻结打包的安装包没有验证（PyInstaller spec 仍停在 0.6.4），HA-22 ① 的 WebView 刷新也没有做原生验收，两者都列为遗留。

## 1. 装配位置

| 层 | 位置 | 职责 |
|---|---|---|
| 启动 | `backend/main.py` lifespan，紧跟 `_activate_product_sdk_runtime()` | 调 `deskpet.orchestration.wiring.activate_orchestration`。失败只让编排服务不可用，并记入 `startup_errors`；"未配置模型"不算故障。任何情况下都不会让后端启动失败 |
| 关停 | `backend/main.py` lifespan 关停段，在 SDK stack 之前 | 调 `deactivate_orchestration`。这只是有序关停路径：App 真实退出时后端收到的是 SIGKILL，编排的正确性不依赖这一步 |
| 服务 | `deskpet/orchestration/service.py` 的 `OrchestrationService` | 持有目录锁、provider 快照、`Orchestrator` 实例与驱动循环、SDK facade、部署清单，并做 Host 门口检查。<br>驱动循环失败时按指数退避，时长不超过 `backoff_max_seconds`；指数本身也有上限，失败次数再多也不会溢出。连续 3 次失败就重建运行时，连续 5 次标为 degraded；重建本身失败时循环不退出，只标 degraded 并写明原因。<br>degraded 时，读取、取消、审批决定、接管、评论照常可用，只拒绝新建 Mission（`orchestration_degraded`） |
| 协议 | `deskpet/orchestration/handlers.py`，`/ws/control` 中 `mission_*` / `orchestration_*` 消息 | 纯分发函数，响应格式为 `<type>_response {request_id, ok, data ｜ error_code, error}`，异常不会抛进 socket 循环。payload 不是对象时回 `invalid_request`，不会断开主对话共用的控制通道 |
| 推送 | `deskpet/orchestration/pump.py` 的 `MissionChangePump` | 用只读连接每秒查看各 Mission 的状态和最大 seq，每次写操作后也立即查一次；有变化就广播 `mission_changed`，并带上新事件（2026-09-26：`from_seq`/`events`/`truncated`，每次最多 50 条，字段同 `project_event`；进程首次看到的任务只标 `truncated`；查事件失败下一轮重试） |
| 运行视图 | `deskpet/orchestration/live_graph.py` | `mission_live_graph`（结构来自 `plan_memberships`→`method_instances.goal_occurrence_id`、顺序/数据依赖、`tasks.status`、最新 `CompoundPhaseChanged` 阶段）与 `mission_planning_decisions`；先校验归属再只读打开库 |
| 投影 | `deskpet/orchestration/projection.py` | 按白名单输出，长度有上限；动作参数超过 600 字符时，只给截断后的预览。<br>模型写的文本都标注 `source: "model"`：结果摘要与 claims、Task 目标、critic_review 摘要、审批摘要、动作理由。<br>事件只给 seq、type、时间、Task / Attempt id 与 actor_type，payload 一律不外传；唯一例外是评论事件，带评论文字。<br>产物只给工作区相对路径和 hash，不给 `storage_uri`；不暴露 intents。<br>`ui_state` 按 P3.1 的状态词汇统一推导（请求已接收／排队／运行／待验证／待人／UNKNOWN／正式交付，另有失败、已取消），列表与详情共用同一套 |
| 前端 | `tauri-app/src/views/MissionsView.tsx`、`stores/missionsStore.ts`、`stores/useMissionsFeed.ts`、`components/Sidebar.tsx`（入口"任务编排"，角标显示待审批数）、`components/WorkbenchShell.tsx`（视图 `missions`） | 界面只做投影，所有改动都走控制通道命令。<br>• 常驻订阅：`useMissionsFeed` 挂在 App 上，与视图是否打开无关。启动时拉取 status 和列表；收到 `mission_changed` 就更新 store，并节流重拉列表（最多 1 次/秒），所以侧栏角标和列表的状态词会实时变化。<br>• 事件游标：推送看到的 seq（`lastSeq`，只用来防倒退）与已加载事件的游标（`eventCursor`）分开保存，拉事件一律从游标往后。推送的 seq 超过游标时，连续分页补拉，每页 200 条，一次最多 20 页，超出后显示"加载更多事件"。时间线只显示最近 50 条。<br>• 状态词统一用后端的 `ui_state`。<br>• 详情里还有：产物（"查看产物"，按 id 读取并核对 hash；二进制和截断都会注明）、评论（"评论"、"发表评论"，评论对象是 Mission）、策略漂移提示、等待原因（人工复核、动作审批、仲裁，附开始时间）。<br>• 模型写的文字标注"模型生成，未核实" |
| 服务登记 | `backend/context.py` 的 `_VALID_SERVICES`：`orchestration`、`orchestration_pump` | — |

## 2. 数据

- 正式目录：`<user_data>/data/agent-orchestrator/`，内含：
  - `orchestrator.db`：编排库，只有 SDK 的 Commit Service 写；
  - `execution.db`：编排自己的 SDK 执行库，与主对话的 `execution-v6.sqlite3` 分开；
  - `workspaces/`；
  - `deployment-manifest.json`；
  - `.instance.lock`。
- 测试场景目录：`<user_data>/data/agent-orchestrator-test/`（见 §6），正式库一行不写。
- 两个目录都不能是软链，也不能解析到 user_data 之外。
- **单实例**：编排目录上有 `flock`。第二个进程拿不到锁，就标为 unavailable，不派发任何任务。持锁进程被 SIGKILL 后，锁由内核释放。
- **owner**：每个进程各自一个，形如 `deskpet-orchestrator-<pid>-<随机8位>`。不能共用：SDK 会把同一 owner 名下的有效租约都当成自己的。

## 3. 部署政策（固定）

| 项 | 值 | 理由 |
|---|---|---|
| `code_execution` | 由启动时的探针决定：`sandboxed` 或 `off` | P3.2 §4.3：问题不再是"允不允许执行"，而是"隔离在这台机器上证明过没有"。每次启动跑 SDK 的能力探针（8 项：读家目录、读他人临时目录、写越界、联网、向宿主发信号、完整 daemonize 的回收、输出截断、CPU 上限）；**全过才是 `sandboxed`**，否则 `off`。Host **永不使用 `process_only`**——不隔离的子进程只适合可信代码，而模型写的代码不是。<br>`off` 时（与 P3.1 相同）：`code_test` 不是已部署层；`pytest:` 条件在入口被拒；旧 Task 的 `pytest:` 由规则层判 FAIL；冲突转 DEFERRED；`run_tests` 被拒。<br>`sandboxed` 时：这些重新开放，`run_tests` 进入 `allowed_tools`，模型写的代码只在一次性副本里、经 seatbelt 运行。探针报告写进部署清单与 `status()` |
| `allowed_tools` | 三个工作区工具 | 编排 Agent 拿不到 Host 的任何工具（shell、MCP、文件系统、浏览器都没有） |
| `enabled_connectors` | 默认空；授权发布目录后含 `file_publish`；`test_config` 仅测试场景 | P3.2 P32-14：只有用户在配置里指明了发布目录、该目录存在、**且能承载硬链接**（连接器唯一的原子提交点）时，`file_publish` 才启用。三者缺一就不启用，于是 Mission 连带 `action:file_publish…` 的成功条件都不能提交——拒绝发生在门口，而不是等模型产出候选之后。不支持硬链接的卷（exFAT、部分网络盘）在授权时就被挡下，不会变成运行期的 UNKNOWN |
| `max_action_level` | L2 | 单机只有一个人，满足不了 L3 要求的两个不同的人 |
| 本机执行测试的开关 | 不提供，也不会有 | P3.2 用"隔离是否证明过"取代了"允不允许"。配置里没有任何开关能打开它；决定权在探针，结果在部署清单里可查 |

## 4. 身份与授权

- **Principal**：`local-user:<companion 本机身份 profile_id>`，显示为"本机用户"。单机没有认证，身份就等于能操作这台 App 的人。这一点登记为限制。
- **与 Host 的 auto / manual 模式分开**：auto / manual 只管主对话里的工具效果。编排的人工审批（原文 §22）必须由人来决定：auto 不会自动批准，manual 也不会弹出主对话的授权窗。
- **tenant**：固定为 `local-desktop`。SDK facade 按 tenant 检查每个对象的归属：不属于调用方的对象和根本不存在的对象，一律返回同一句 `not_found`。
- **密钥门口**：下列内容会同时按通用密钥模式和当前 provider 的密钥检查，命中就回 `secret_rejected`，一个字都不写进编排库。provider 密钥即使不是 `sk-` 形态也能拦下。
  - 新建请求里的每一个字符串：目标、成功条件、`stop_conditions`、`synthesis`、`workspace_seed`，连键名也查；
  - 审批的理由和备注、接管依据、评论。
- **seq 是全库自增**：一个编排库只有一个 tenant 时没有影响；多 tenant 共用一个库时，跳号会暴露其他 tenant 的事件量。

## 5. 恢复语义（App 退出 = SIGKILL）

| 被杀的时点 | 新进程里发生什么 | 界面 |
|---|---|---|
| 等人工复核（所有回合都已提交） | 旧 owner 的租约过期（`lease_seconds`）后，新 owner 继续验证**同一个** Attempt，不重做已提交的工作（P3.1-A07） | 复核通过后几秒内进入正式交付 |
| 模型调用中途 | SDK 回合停在 running；心跳里的 `liveness.blocked` 变为 true（`kind: provider`） | 详情里显示"回合结果未知"，可以接管：停止，或带说明重试（`mission_takeover`，basis 必填）。2026-09-12 实测（租约 2 s）：新 owner 启动后约 6 s 出现 blocked。不接管的话，Mission 一直停在 ACTIVE，观察 240 s 也没有自动收敛，所以接管入口是必需的 |
| 其他时点 | SDK 按原文 §16.4 恢复：已完成的不重跑，SUBMITTED 未验证的重新进入验证 | — |

## 6. 仅测试用的场景

- `DESKPET_ORCHESTRATION_TEST_SCENARIO=approval-action`，并且 user_data 位于某个 `.local-test-evidence/` 目录下。两个条件缺一个都不生效，也不依赖 DEV_MODE。
- 生效后：使用独立目录；provider 换成 SDK 的 approval-action 夹具，只用工作区工具；启用测试配置服务连接器 `TestConfigService`；只允许一个 Mission；界面标出"测试场景"。
- 这个场景只用于原生验收里的审批流程，它的通过不代表生产授权。
- 原生启动器 `scripts/native/launch_native_candidate.py` 会丢弃继承来的 `DESKPET_*` 变量，所以要用 `--orchestration-test-scenario approval-action` 把场景传给后端。`--userdata` 必须位于 `.local-test-evidence/` 下，后端才会承认这个场景。

## 7. 设置（`config.toml [orchestration]`）

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | true | 按 CLAUDE.md：测试阶段已完成的能力默认开启 |
| `max_concurrency` / `max_concurrent_model_calls` | 1 / 1 | 与主对话共用 provider 限额。**只在编排库第一次 seed 时进入 ACTIVE 策略**，之后修改只会记一条 `PolicyConfigDrift`、不会生效（2026-10-02 起策略晋级流程已删，建库后没有任何东西替换当前版本；要不要改成"改了就换新版本"待用户定） |
| `default_mission_max_tokens` / `default_mission_max_attempts` | 400000 / 12 | 代码常量（`OrchestrationSettings`），不从 config 读。**本部署不提供无上限的 Mission**：<br>• 请求里预算留空的项，由 Host 门口补上这个默认值；<br>• 用户填了的值原样保留；<br>• 0、负数、非整数一律拒绝，不会被默认值替换；<br>• 默认值在进 facade 之前补上，所以回执的 spec hash 已经包含它；<br>• `orchestration_status.mission_budget_defaults` 把默认值下发给表单占位符，详情显示实际生效的预算。<br>依据：原生验收时，留空预算的 Mission 被真实 Planner 编出 800 tokens 的 Task 预算，结果以 `budget_exhausted` 失败。裁决见计划 journal §4.4。<br>12 次的理由：Mission 级尝试次数统计的是所有 Task 的全部 Worker Attempt |
| `decision_mode` | `"existing"` | NanoJev 决策滚动模式（§11）。白名单只有 `existing`｜`shadow`；**未知值/非字符串/缺键一律 `existing`**，`nanojev` 刻意不在白名单（Primary 是后续门）。只从 config 读，环境变量无效 |
| `decision_shadow_timeout_seconds` | 空（无 Host 侧上限） | `shadow` 下观测的超时上限。只接受正数，硬上限 60s；0/负数/非数字/布尔 → 不设 Host 侧上限。超时按"观测失败"处理，plan 不受影响 |

## 8. 模型与费用

- provider 在启动时对 `get_chain()` 的第一个启用项做一次快照，之后换 provider 需要重启才生效。
- DeepSeek 官方端点把 `deepseek-v4-flash` 映射为 `deepseek-flash`，status 里同时显示两个 id。
- 编排只记 token、不记金额（2026-10-03 HTN 补齐阶段 C 删金额计价：价目表、金额预算维度、供应方准入的价格比对、账本与义务账的金额列一并删除，迁移 38 删 13 个金额列；供应方准入身份升 v4，旧编排库不兼容，需新建编排数据目录）。主对话计费与原生运行层的金额不在此列。
- **Task 预算下限**（SDK 0.9.11 起，F-ORCH-1）：
  - Graph Manager 会拒绝预算低于 `k × (base + critic)` 的 Task。k 是每个 Task 的候选数；base 是单轮最多产出的 token 数，这个部署是 8192；critic 部分只在验证政策含 critic_review 时计入，是 Critic 的预留 6000。
  - 被拒后，Planner / Manager 会收到原因（`task_budget_below_floor`）并重新规划，系统不会替它们编一个数。它们的输入里也写明了下限。
  - 下限是**预留层面**的必要条件：它只保证在预留那一刻，第一个 Attempt 和它的 Critic 都能预留得到，前提是一轮结算的用量不超过它的预留。真实模型一轮会连输入一起结算，远超 base（原生验收时一轮结算了 22003），所以预算正好等于下限的 Task，第一轮之后照样可能付不起 Critic，之后的修复和重试也不在保证之内。
  - 这与 Host 门口的默认预算（见 §7）是两道互补的保护。
- **产物的验证状态**（SDK 0.9.11 起，F-ORCH-3），在对应的提交事务里一并写入：
  - 结果被接受：VERIFIED；
  - 结果被判 FAIL：REJECTED；
  - 被取代的候选：保持 UNVERIFIED。

  注意：产物的 REJECTED 和结果的 REJECTED 意思不同。
  - 产物 REJECTED 表示它所在的结果被判了 FAIL。
  - 被取代的结果，其 `verification_state` 也是 REJECTED（verdict=superseded），但它从来没有被评判过，所以它的产物是 UNVERIFIED。

  读模型把两者并排显示时，要按上面的含义解读。
- **Attempt 的 RETRY_WAIT**：这是失败 Attempt 的终态。原文 §25.2 没有 Attempt 的 FAILED 状态，重试的时候另起一个新 Attempt。所以 Mission 结束后，个别 Attempt 停在 RETRY_WAIT 是设计如此，不是还在排队重试。

## 9. 部署清单（P3.1-A08）

`deployment-manifest.json` 与 `orchestration_status.deployment_manifest` 记录以下内容，不含密钥：
- host_commit，以及 `host_dirty`：backend、tauri-app、scripts 下有没有未提交或未跟踪的改动；打包版没有 git 时为 null；
- `simple_harness` / `agent_orchestrator` 实际导入的文件路径与版本；
- distribution 版本与钉版的 wheel sha；
- schema；
- 生效的部署政策、测试场景、设置；
- 模型。

实际导入的版本与钉版不一致时，服务标为 unavailable。这个检查在打开编排库之前做，所以版本不对的 SDK 不会去迁移编排库；这种情况下，清单只记录 `refused: pin_mismatch` 和实际导入的信息。

## 10. 限制与遗留

- 身份是自报的本机用户，没有多用户认证。
- 沙箱（P3.2）的边界，如实记下：
  - **内存与进程数只是软限制**：本机没有 cgroup，`RLIMIT_AS` / `RLIMIT_DATA` 设不进去，`RLIMIT_NPROC` 又按整个 uid 计数，所以只能采样后回收，属于事后处理；`cpu_seconds` 是**每进程**的硬限制，整次执行靠墙钟兜底。
  - **没有 uid 隔离**：没有管理员权限，同一 uid 下的残余风险靠 seatbelt 规则收敛。
  - **元数据可读**：放行 stat 才能让 venv 的软链解析正常，代价是沙箱里的代码能探测任意路径是否存在、大小与修改时间，但读不到内容。
  - **用到的是已废弃且无公开文档的接口**：seatbelt 与 `sandbox_check`（Chromium、WebKit 也在用）。每次启动由探针重新验证；探针不过就退回 `off`。
  - **回收耗时受外部工具影响**：认进程要靠 `ps`，不隔离模式还要靠 `lsof`，两者都有单次超时，但极端情况下一次回收仍可能偏慢；正确性不受影响。

## 11. NanoJev 决策接缝（PR-7，Shadow-only）

- 最后更新：2026-09-20
- 计划来源：`plans/taskSys2/升级planV1/v1.4/NanoJevAdd.md` §57 PR-7（Shadow 接入）+ 本仓任务书 `plans/taskSys2/升级planV1/v1.4/任务书-PR7-2026-09-20/pr7-impl.md`

**生产链路**

| 层 | 位置 | 职责 |
|---|---|---|
| 配置载体 | `config.toml [orchestration] decision_mode` / `decision_shadow_timeout_seconds` | Host 唯一真源。缺省在 `deskpet/orchestration/settings.py`：`decision_mode = "existing"`、超时 `None`。**只从本文件读，环境变量无效** |
| 解析 | `settings.py::_decision_mode` / `_shadow_timeout` | 白名单只有 `("existing", "shadow")`。未知值、非字符串、缺键、空串 → `existing`；`nanojev` 刻意不在白名单，故 Primary 配置写不出来。超时只接受正数，上限 60s，其余 → `None` |
| Host 接缝 | `deskpet/orchestration/decision.py::DecisionSeam` | 把设置变成显式 typed `DecisionPolicy`（`build_decision_seam`）；`ready_priority_plan()` 调既有 `allocate()`，仅在 `shadow` 且拿到 journal 时才追加观测；`DecisionSeamStatus` 供 `status()["decision"]` 投影 |
| 装配 | `deskpet/orchestration/service.py::_install_decision_seam` | 在 `_open()` 末尾一次性构建，journal 用编排器自己的 Store（`DecisionEventJournal`，写 `orchestrator.db` 的既有 `events` 表，无新表）。构建失败只记日志，不影响启动 |
| SDK 接缝 | SDK `agent_orchestrator/decision/host_integration.py` | Host 面向的调用点：`frontier_priority_candidates`（`frontier()` 顺序）、`compute_ready_task_decision_id`（`njr-` 确定性 id）、`observe_frontier_priority`、`ready_task_priority_decision`。SDK 侧不读任何配置 |
| 事件 | SDK `decision/events.py` 既有 `DecisionEventJournal` | additive 事件类型，按 `idempotency_key` 幂等；`pd-` 前缀被拒 |

**硬约束（不因本片放宽）**

- **分配器授权集合是唯一权威**：`READY_TASK_PRIORITY` 只观测 `frontier()` 顺序，观测前后 `AllocationPlan` 逐字节相同（`grants`/`scores`/`open_attempts`/`eligible`/`slots` 全部相等）。Shadow 答案永不生效——即使它"很有信心"，或指向一个被 paused/依赖挡住的 Task（该 Task 根本不在候选集里，越界答案被 SDK 结果校验拒绝）。
- **默认 `EXISTING`**：缺键、拼错、类型不对都落在既有的确定性路径上，不观测、不打事件、不调模型。
- **无 ad-hoc 环境变量**：`decision.py` 不出现 `os.environ`/`getenv`；设置只从 `config.toml` 读。
- **Primary 不可达**：Host 白名单无 `nanojev`，SDK 侧 `DecisionMode.NANOJEV` 也不被本接缝引用。
- **`RETRY_OR_ESCALATE` 未接线（blocker）**：`RetryAction` 有六个值，映射到两值契约需要单独裁定。本片不猜；`tests/orchestration/test_decision_shadow.py` 用 AST 扫描钉住"除状态位 `retry_wired`（恒 False）外，模块不引用任何 retry/escalate 标识"。
- **观测失败不是生产失败**：shadow 异常/超时/越界、journal 写失败、缺 `mission_id` 一律转成 "plan 照常返回 + `status()["decision"].failures += 1`"，不抛进驱动循环。
- **0/1 候选不调模型**：`frontier()` 少于 2 个候选（含 0 个）时直接返回，连请求都不构造——Host 接缝与 SDK 接缝各有一道同样的闸。
- **无 journal 不观测**：没有可归因的事件汇时，不调用模型（不存在"悄悄观测"这条路）。

**已知边界（如实记录，未绕过）**

- 本片观测是**结构性的、不是模型驱动的**：没有真实 NanoJev checkpoint、没有加载任何权重。它是"接缝通了、数据能收"，**不得**读作模型质量、shadow 收益或 Primary 就绪。
- `frontier()` 顺序 ≠ `allocate()` 授权顺序：`frontier()` 按 `(-priority, ordinal)`；`allocate()` 在 Task 有 §29.3 分数时按分数排，仅对无分数的 Task 回落到 `(-priority, ordinal)`。本片按 §57 的口径观测 frontier 顺序，**不声称两者一致**。
- 观测的事件落库路径只在接缝层测过（真实 `DecisionEventJournal` + 真实 `Store`），未在真实 Mission 的驱动循环里跑过端到端。

**验证状态（2026-09-20）**

- SDK `tests/orchestrator/full_target`：**3866 PASS / 5 SKIP / 0 FAIL**（5 skip 全为环境/开关性）。
- SDK 决策+分配器相邻套件：**205 PASS**（含新增接缝 30 条）。
- Host `tests/orchestration/test_decision_shadow.py`：**56 PASS**（SDK 源码环境）／**41 PASS / 15 SKIP**（vendored wheel 0.12.2——该 wheel 早于 decision 包，skip 是显式声明的"合同缺席"，不是静默通过）。
- Host `tests/orchestration` 全量：**293 PASS / 97 FAIL**；与 `git stash` 干净基线**逐条一致**（基线 237 PASS / 97 FAIL，+56 恰为本片新增；97 项为既有 SDK pin/环境失败）。
- mutation：SDK 接缝 6 个行为可区分 mutation 全部 killed（含"去掉字段分隔符"与"用 frontier 顺序替换 allocate 授权"两条语义型）；Host 接缝的 mode 短路、journal 闸、候选数闸、id 前缀由行为测试钉住。
- ruff：本片新增/修改文件全绿（`manifest.py` 的 2 条为既有）。
- **未验证**：真实模型/checkpoint 观测、真实 Mission 驱动循环内的端到端、Primary、`RETRY_OR_ESCALATE`。未打包、未发布。
  - **宿主崩溃后的逃逸进程认不出来**：金丝雀随执行目录一起删除，宿主重启时没有线索可扫（SDK journal 已登记，留待后续处理）。
- 策略只读：SDK 的策略接口与命令行也只剩 `list / show / status` 三个只读动作（提议、评测、晋级、回滚 2026-10-02 已删）。
- 编排的证据目录（workspaces）不会自动清理。
- PyInstaller 打包 spec 仍停在 0.6.4，尚未跟进。
- 模型调用中途被杀之后，需要人来接管，SDK 不会自己收敛。自动恢复"结果未知"的回合，归入 P3.5 处理。租约为默认 60 s 时，"结果未知"要多久才出现，还没有在原生 App 里测过。


Source-native slot binding (2026-09-13 08:24 CST): launcher supports explicit 1..4 logical/model slots, default 1/1; values are part of the source identity and are written only before first backend startup. Resume checks exact integer config without rewriting seeded policy. Older source runs require their recorded pre-slot launcher. Main verification: g-source-slots-search-v2, 32 PASS/2.09s (runner2.70s; includes controlled search software test). Terra rework fixed incomplete CLI oracle and TOML bool equality; native multi-Mission load remains OPEN.

Real N1 v14 (SDK4e79dac/Hoste6a1dac7) reached verification_passed in223.94s: seven literal VERIFIED,three SUPPORTED,224843tokens settled/0reserved,12Providerhandoffs (10succeeded/2failed withusage),0rehandoffs. Actual UI opened REPORT and full table citation; cold same-state12->12. Raw case-summary SHA25684d7385071c30cab20e668f5b25eef41e4f53c1f1abcbf6212649b74bd81fc04 under Host .local-test-evidence/2026-09-13/p33-g/source-ui-n1-v14/. Manual quality FAIL: REPORT3.1 says both sources lack frozen build/install/verification records, contradicted sourceAline7 historical builds/startup failures. Both Worker/Critic had read whole sources. Runtime/citation/cold PASS does not close N1 report quality. Doc8 successor guidance and original400000/12 recheck in progress. Native owned groups94307/97182 exited0,noresidual; lifecycles477.554/80.27s include UI/analysis waiting. No packaging/P36/push.

## PR-7 local runtime closure (2026-09-20)

The initial production probe found the backend environment importing SDK `0.11.1` without
`agent_orchestrator.decision`, while the Host pin is `0.12.2`. The authorized local closure
uses an explicitly attested editable SDK source (`51dbed2`, 443 source inputs) in the
development environment; it does not replace or publish the release wheel.

The live caller is `Orchestrator._decide()`'s existing `allocate()` branch. It computes the
`AllocationPlan` first and then calls an optional Host observer with that plan. The observer
cannot replace or reorder grants and callback/provider failures are isolated. A real
two-ready-Task Mission reached the observer and persisted `DecisionRequested` plus
`ShadowDecisionProduced` (`njr-` identity) in the existing `orchestrator.db` `events` table.
The run used a FakeShadowProvider because no NanoJev checkpoint is authorized in PR-7.
Default `EXISTING` remains short-circuited; Primary and `RETRY_OR_ESCALATE` remain out of
scope. Evidence is under `.local-test-evidence/2026-09-20/pr7-production-closure/`.

## PR-7 wheel runtime closure update (2026-09-20)

The local runtime now uses the non-public development candidate
`0.13.0.dev20260920` from `backend/vendor`, with candidate and lock identity bound to
wheel SHA `9687d023c3fc9bc00c990c35c2827e035c3e56fbda49659e91acefc7fcfd2ef1`. The
manifest records dirty-source provenance (`51dbed2`, 443 inputs, snapshot digest
`74b4067b…`, `release_published=false`). Backend/.venv imports this wheel without an
editable install or SDK checkout path. A real two-ready-task Mission completed in both
EXISTING and SHADOW; SHADOW persisted DecisionRequested and ShadowDecisionProduced
with FakeShadowProvider, while EXISTING emitted no decision events. The SDK caller
schedules async observation via a retained task, so provider latency cannot delay the
allocator; sync Host seam refuses `asyncio.run` from a running loop. `allocate_v2`,
Primary, real checkpoint and RETRY_OR_ESCALATE remain outside PR-7.
最后更新：2026-09-20 CST。**NanoJev/PR7 状态边界：** local Host runtime candidate 已对齐，但仍 Shadow-only；T09 真实双Task事件通过，grants six-invariance 只到 probe-level；T01 计数为 non-vacuous。R02 真实运行质量为 2/8，不能升 Primary。H1-H 仍处于候选 worktree，相关门禁未闭合；不宣称完整 H1 或 Primary。[依据：`candidate-closure-terra.md`、`t09-grants-closure-terra.md`、`t01-call-counter-sol.md`、`r02-real-deepseek.md`、`h1-gate-closure-sol.md`]
最后更新：2026-09-21 CST。SDK H1-H 候选的 WAIT 由现有持久事件关系驱动，按权威 Task semantic identity 和实际状态判断；登记+结算、唤醒+Planner package+预算+binding 分别同事务。Task 终态与 Acceptance 分开，新协议 package v6/int5 冻结状态和 occurrence outcome；legacy 包不变，历史 v5 request 继续识别为 int4。160 项定向及 48 项重叠回归通过，完整复验与真实 Worker WAIT 尚待；不代表 Host 运行版本更新或 H1-H8 完成。[验证范围](../plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md)。
最后更新：2026-09-21 CST。Operation 补遗实施中，V1.4（去除 NanoJev）整体未完成。新增完成规格批准命令 Host handler → SDK facade → 原 CommitService/Store，Spec/receipt/event 同事务；来源、租户、重放、过期拒绝及迁移定向 27 PASS（0.54 秒，后继 Task contract hash 修正仍在复验）。Host 在独立临时源码副本对齐两包版本后，真实 service/handler 接线 11 PASS（18.28 秒）；仅为派生源码接线证据，不是当前候选字节、wheel 或 UI 验收。移除启动/重建路径上已延期的 PR-7 observer 依赖，保留历史文件。Scope/Plan Commit、准备与效果区分、T0/T3、D3 及完整 H1–H8 仍待；未合并/重装 Host。当前无新增 PlanAgent 架构待决。详见 Host V1.4 的 Operation补遗实施记录-2026-09-21.md。
