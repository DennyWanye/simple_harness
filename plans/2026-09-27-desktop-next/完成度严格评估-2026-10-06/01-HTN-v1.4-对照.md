# HTN 升级原始计划（FULL-TARGET-1.4）严格对照（2026-10-06）

## 结论摘要

1. 基线：`complete-plan.zh-CN.md`（§4、§5～§18、§24、§25、附录 B）、两份 Operation 补遗、NanoJev 范围变更。代码：main `b4f1d314` / SDK opt.162。只读，没跑测试。
2. 共 **200 条**：ADR 13、§5～§18 81、附件 TG 12、附件 AER 11、补遗与范围变更 23、R01～R60 60。
3. 判定计数：按计划在用 66；部分 66；做法不同 29；已删 15；A 级排除 15；未做 7；做了没接上 1；只在测试 1。
4. **C 级无记录偏离 9 条**。第三节列了 42 条"保持现状 / 有意偏离"：其中 31 条是真正的 B 级（26 条没有任何用户原话，5 条只有部分原话）；另外 11 条有用户原话，其中 4 条（★）没列进 00 口径的 A 级清单。
5. 最严重的 C①：AER 恢复协议整套没做，也没有任何决定（RECOVERY_LOCKED、清单核对、DEGRADED_RECOVERY、孤儿回收、P7.1 `recovery_coordinator`）。10-02 评估把它算进"09-30 暂缓"，但用户原话只说到"迁移快照、归档、删除标记"。
6. 最严重的 C②：知识记录缺 `validity_interval`、`permitted_uses`、`assurance_level` 三个字段，没有任何登记；台账 R29 仍写"已接入"。
7. 最严重的 C③：界面没有展示证据版本、备选与采用路线、执行图上的共享子目标。10-04 复核点过名，用户只定了加"不再算数"一行，其余没有裁决。
8. 台账里"已接入"的 34 条，有 13 条按严格口径应是"部分"。R52 用户原话是"暂不做、补齐后重建"，应算 D 级（未完成），不是"不做"。

路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`RT/` = `sdk/simple-harness-sdk/src/simple_harness/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`BE/` = `backend/deskpet/orchestration/`；`FE/` = `tauri-app/src/`。"补齐计划" = `HTN补齐计划-2026-10-02.md`（第 3.23 版）；"台账" = `需求与场景状态.md`；"TG 补全方案" = `TaskGraph-补全-方案.md`。A 级编号指 `00-评估口径与排除清单.md` 第三节的编号。

---

## 第一节：逐条对照大表

### 1.1 §4 架构裁决（ADR-01～13）

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| ADR-01 保留现有实体；新增 Obligation、MethodContract、MethodInstance、PlanRevision、GoalResolution、Fact/Justification、Commitment/ExecutionCycle | §4 | 部分 | `SDK/contracts/obligations.py:75`、`SDK/contracts/htn.py:895` `MethodContract`、`:1700` `MethodInstanceDraft`、`SDK/contracts/resolution.py:1418` `GoalResolution`、`SDK/contracts/evidence_state.py:175` `ObservationRecord`、`SDK/knowledge/justifications.py:235` `JustificationSet` | 全库搜不到 Commitment、ExecutionCycle（只有 `event_handler.py` 一处注释）。§15 用户 09-30 说暂不做（见第三节 B-37）。Justification 只在保证通道的证书里用，知识记录不用 |
| ADR-02 稳定 BaseAgent + 有限 AgentTurn；单主 Agent；可建多个分范围的 Manager/Worker/Verifier，并发有界 | §4 | 按计划在用 | `RT/agents/arp/session_lifecycle.py`、`lifecycle.py`；主对话入口 `BE/chat_tool.py:113` `start_mission`；物理名额 `SDK/orchestrator/event_handler.py:668` `ProviderBudgetGuard` | 分范围 Manager 不做（A 级 #1，组级管理员）；任务级 Manager 随平面模式删（A 级 #3） |
| ADR-03 一份规范网络、四类关系、唯一 Commit | §4 | 按计划在用 | `SDK/orchestrator/commit_service.py` `CommitService`；`SDK/graph/task_network.py:131-142`；表级守护 `T/product_world/table_guards.py` | 支持/假设/监督三类边删了（B-10） |
| ADR-04 独立 Verifier 判语义，程序守协议；用户要求的程序测试失败不能被 PASS 覆盖 | §4 | 按计划在用 | 保证通道审阅：`SDK/orchestrator/leaf_acceptance.py`、`composition_review.py`、`root_review.py`；本地检查 `SDK/assurance/local_checks.py`、`executor_checks.py`；`CheckExecution` 与判定分开（`SDK/contracts/resolution.py:610`） | 审阅员只有"找""读"两件工具，不能自己选检查工具（D 级：审阅员信息缺口） |
| ADR-05 历史完成不改，当前适用性可失效 | §4 | 按计划在用 | 验收按要求版本算数 `SDK/orchestrator/planner_views.py:38` `accepted_steps`；门面 `SDK/api/facade.py:766` `_steps_no_longer_counting`；界面 `FE/views/StepsNoLongerCounting.test.tsx` | — |
| ADR-06 全部业务事实可重建；不复活租约和权限 | §4 | 部分 | `SDK/observability/business_replay.py:165` `verify_mission`、`SDK/storage/source_records.py:218` `record_written_rows` | 周期、定时这类业务事实没有（§15 没做）。"不复活旧授权"没有专门的实现和用例。重建靠存储层统一记行变化，不靠纯领域事件（B-30） |
| ADR-07 四值真值 + CURRENT/STALE/REVOKED；找不到 ≠ 不存在 | §4 | 按计划在用 | `SDK/contracts/evidence_state.py:45,54`；`SDK/knowledge/predicates.py:304` `closed_world_denial_admissible`；`SDK/planning/htn/applicability.py:335` `authorization_gate` | — |
| ADR-08 不设固定的通用语义上限，改为版本化容量；超限报 BOUND_REACHED | §4 | 做法不同 | 结构预算 `SDK/orchestrator/plan_commits.py:856` `_check_budget`；`SDK/contracts/htn.py:373` `StructureBudget` | 层数硬性固定为三层（Host 只注册了 3 个层级，`BE/hierarchical.py` `levels`），每一步同时只有一次尝试。这正是 ADR-08 要取消的那类固定上限（B-2、B-4） |
| ADR-09 继续用 Python/SQLite | §4 | 按计划在用 | `SDK/storage/store.py` | — |
| ADR-10 Workflow 在工具层；不接用户长期记忆 | §4 | 按计划在用 | 规划只产出步骤和做法（`SDK/contracts/planning_decisions.py:99-106`）；记忆端口是空实现（CLAUDE.md 架构说明） | — |
| ADR-11 研究方案只作参考，不当系统保证 | §4 | 按计划在用 | 原则性条款，代码里没有宣称形式化保证；PANDA 适配器在 `SDK/planning/htn/backends/panda.py` | 没有可检验的代码要求 |
| ADR-12 最终范围只能显式变更，改范围要用户批准 | §4 | 部分 | 台账 `需求与场景状态.md` 有维护 | 有 42 处偏离只经子代理裁决或计划自拟，没有用户逐条批准（第三节），本身就违反本条 |
| ADR-13 整数 `base_graph_version` 闸门不变；语义读集是附加校验；是否取代要等 P3 之后另立 ADR | §4，C19、C29 | 已删 | 全库搜不到 `base_graph_version`、`graph_version`、`allow_rebase`；唯一的结构闸门是 `SDK/orchestrator/plan_commits.py:638` `_check_plan_revision` | 补齐计划待定③由裁决子代理定"删"（阶段 D 开工裁决），没有另立 ADR，也没找到用户原话（B-13） |

### 1.2 §5～§18 架构各章

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| Planner、Manager、Allocator、Verifier 只产出提案，正式事实都经 Commit | §5 原则 1 | 按计划在用 | `SDK/orchestrator/plan_commits.py`、`resolution_commits.py:589` `accept_review`、`:960` `commit_goal_resolution` | — |
| TaskNetwork 不是真相中心；操作台账、证据有效性、验收各有独立的表 | §5 原则 2 | 按计划在用 | 操作：`SDK/storage/operation_seams_schema.py`、`operation_completion_schema.py`；有效性：`SDK/storage/assurance_schema.py`；验收：`SDK/storage/htn_schema.py` | — |
| Validity 接入五处：规划、输入、上下文、交接、接受准入 | §5 阅读约定 | 部分 | 规划：`SDK/orchestrator/planning_repair_requests.py:168` `precondition_triggers`；交接：`SDK/orchestrator/action_commits.py:924,1111` `_validity_refusal`；上下文：`event_handler.py:5462` `knowledge_standing`；接受：保证通道证书 | 输入（`InputManifest`）没有核对见证或纪元，只看验收是否现行。上下文用的是知识的读时判定，不是 ValidityWitness |
| Obligation 字段：requirement_refs、goal_signature、scope、authority_ref、requiredness、budget_lineage_ref、satisfaction_policy、lifecycle/resolution_ref | §6.1 | 部分 | `SDK/contracts/obligations.py:75-95` | `authority_ref`、`satisfaction_policy` 删了（A 级 #9）。产品建根义务时只填编号、要求、目标类型（`SDK/deployment/root.py:198-201`） |
| 拆分、换做法、换执行者都不能刷出新的重试和预算；共享工作有唯一出资方 | §6.1 | 部分 | 读时推出的义务账 `SDK/orchestrator/obligation_accounts.py:24,51` | 义务账只记账，不卡上限（B-19）。修做法的机会按步骤算（用户 09-30 选 A，见 B-15） |
| 预算账户是唯一归属树；授权、预留、受保护尾额、已消费分开 | §6.1 | 按计划在用 | `SDK/governance/budgets.py`、`tail_budget.py`、`provider_budget_guard.py` | — |
| `kind` 与 `form=compound/primitive` 两个轴分开；复合任务不进普通派发 | §6.2 | 按计划在用 | `SDK/contracts/htn.py:157` `TaskForm`；`SDK/scheduling/allocator.py:201-262` 形态门；`event_handler.py:10063` | — |
| MethodContract 完整字段（content_hash、Schema、前提、假设、步骤、ORDER、DATA、资源与作用、覆盖映射、组合、来源/适用范围/失败与过期、注册状态与回执） | §6.3 | 部分 | `SDK/contracts/htn.py:895-930`、`:1038` `MethodRegistration`；哈希在 `MethodRef` 上 | 适用范围、失败条件、过期条件没有字段（B-6）；没有资源冲突模型字段 |
| 预期作用不写进事实库 | §6.3 | 按计划在用 | 观察表只有观察器在写（`SDK/storage/htn_store.py:1279` `insert_observation`） | — |
| MethodInstance 绑定 goal_id（TaskRef）、参数、世界快照、前提见证、子绑定、plan_revision | §6.4 | 按计划在用 | `SDK/contracts/htn.py:1700` `MethodInstanceDraft`、`:1529` `PreconditionRef`、`:1583` `ChildBinding` | — |
| 规划器可以提出新的复合 GoalSignature | §6.4 | 做法不同 | 决定类型里没有"提出新目标类型"（`SDK/contracts/planning_decisions.py:99-106`）；Host 注册了通用子目标类型（`BE/hierarchical.py` `desktop.sub-goal-1/2`） | 用"通用子目标类型 + 参数"代替（用户 10-01 裁定变更第五节） |
| 递归燃料按义务算；耗尽报 BOUND_REACHED；`achieve_outcome` 不能当逃生口 | §6.4（v1.2 钉死） | 只在测试 | `SDK/contracts/obligations.py:511` `consume_fuel` 只有 `T/full_target/test_htn_recursion_fuel.py` 等测试在调；生产只在开子义务时扣（`SDK/orchestrator/plan_commits.py:903-914`）；`achieve_outcome` 已删 | 按义务扣燃料的生产路径删了（B-3）。`consume_fuel` 成了只给测试用的死代码 |
| 五类版本轴分开 | §6.4（v1.3） | 部分 | 合同修订、派发代号、计划修订三轴在用 | `RecordVersion`、`ValidityRevision` 删了（B-7） |
| 四类关系：细化/满足、ORDER、DATA、ASSUMPTION/JUSTIFICATION、监督 | §6.5 | 部分 | `SDK/contracts/htn.py:164` `RelationKind`（细化、满足、ORDER、DATA、出资、取代） | 假设、支持、监督三类边没有（B-10） |
| 只用注册的谓词和结构化 AST；冲突 → CONFLICT，缺证据 → UNKNOWN；ALL/ANY/NOT 按优先级算；空 ALL 为真 | §6.6 | 按计划在用 | `SDK/planning/htn/applicability.py:88-120,196`；`SDK/knowledge/predicates.py:220` `PredicateRegistry`；桌面谓词 `SDK/planning/htn/observers/workspace.py:54,124`，装配 `BE/hierarchical.py:24` | — |
| 三条求值规则：NOT 保留 UNKNOWN/CONFLICT；混合求值不用于授权；快照与复检不一致时做法失效 | §6.6（v1.2） | 部分 | 前两条：`applicability.py:88,335`。第三条改成派发前发现前提为假就交规划器（`planning_repair_requests.py:168,216` `method_precondition_false`） | 第三条做法不同（A 级 #9） |
| 前提检查分三个阶段 SELECT/MAINTAIN/ACCEPT | §6.6（v1.3） | 做法不同 | `SDK/planning/htn/grounding.py:480,695` 写死 `PreconditionPhase.SELECT`；`SDK/graph/eligibility.py:859` 只查 SELECT | 只留派发前一个时点（A 级 #9）。`phase_check_points` 还留在合同里，生产只读 SELECT |
| 原子操作声明资源读写集；执行前复检 ETag/版本 | §6.6 | 部分 | 运行期同路径写冲突 `SDK/orchestrator/hierarchical_dispatch.py` `write_conflicts`；文件发布条件写 `SDK/runtime/operation_reconciliation_file_publish.py` | 计划期的资源冲突检查没有输入（10-04 复核已移交 TG 补全，那边的评估不在本文范围） |
| 证据合并用正负计数 (t,f)；组合以优先级表为准；不做 negation-as-failure | §6.6（v1.4） | 按计划在用 | `SDK/contracts/evidence_state.py:130` `SupportCount`、`:373` `from_observations`；`SDK/knowledge/predicates.py:304,339` | — |
| §7.1 必须同时支持的十项能力（参数化、实例化、未知状态、部分序、递归、替代做法、共享、局部修复、组合验收、持久恢复） | §7.1 | 部分 | 见本表和 R04～R17 | 替代做法不能并存（B-4）；递归限三层（B-2）；共享只限叶子（B-24） |
| 推进策略：复用已有结论 → 查做法 → 前提未知先取证 → 没有就提新做法 → 局部展开 → 唯一 Commit → 派发 → 反馈 | §7.2 | 做法不同 | `SDK/contracts/planning_decisions.py:99-106`（REFINE、PROPOSE_METHOD、REQUEST_EVIDENCE、REPAIR、READ_METHOD_LIBRARY 等）；取证 `SDK/orchestrator/planning_evidence.py` | 每一步都交规划器判断，没有程序判定的分支（A 级 #8 与用户 10-01 裁定变更） |
| 新做法来源四种 + 六步注册协议；独立规划审阅 | §7.3 | 做法不同 | 提新做法并审阅：`SDK/orchestrator/plan_commits.py:381` `_check_method_reviews`；跨任务读做法库 `SDK/orchestrator/method_library.py:131,183` | 合成器角色删了，由规划器自己提（用户 10-01 裁定变更）；"已验证历史抽象"这一来源没有；HDDL 编译这一步不跑 |
| 方法状态机 DRAFT→…→ADMITTED、SUSPENDED、RETIRED；规划器不能自己晋级 | §7.3 | 做法不同 | `SDK/contracts/htn.py:252`；`SDK/planning/htn/registry.py:565` `PROMOTION_NOT_AVAILABLE`、`:1130-1166`；产品的晋级与退役走 `SDK/orchestrator/method_library.py:131,251` | 产品走"交付成功且根终审通过即进库、归因失败 2 次退役"（A 级 #5）。registry 里的 SUSPENDED 状态边没有产品调用方 |
| 种子方法库：code、appworld 两域各 ≥3 个做法、≥5 个观察器 | §7.3（v1.2） | 按计划在用 | `SDK/planning/htn/seed_methods/{code,appworld}`、`SDK/planning/htn/observers/{code,appworld}.py` | 按计划只是验收夹具，产品不装（`BE/hierarchical.py:24` 用 `domains=()`），这符合计划 |
| 方法泛化与学习产物（变量化、前提提取、负例、效果统计、退役、审批） | §7.3 末段 | 部分 | 只有退役（`method_library.py:251`） | 全库做法只当先例、不做泛化（B-26）；可训练学习不做（A 级 #1） |
| PANDA/HDDL：装了就必须跑并保存见证；没装显式报 SOLVER_UNAVAILABLE | §7.4 | 做了没接上 | `SDK/planning/htn/backends/panda.py`、`backend_port.py`（`SOLVER_UNAVAILABLE`）；Host 不传 `planning_backend`（`SDK/runtime/assembly.py:99`，BE 里搜不到） | 产品从不调用适配器，也就不会记 UNSUPPORTED；本机没装 PANDA。"已建模样本验收"属 §21 验收，见 D 级 |
| 搜索状态与业务状态分开；SearchNode 与可恢复的前沿 | §7.5 | 已删 | 全库搜不到 `SearchNode`、`search_states` | B-4 / R18 |
| AND–OR 满足关系；一份 OR 路线成功即可，不要求别的路线成功 | §8.1 | 部分 | `SDK/graph/task_network.py:283`；组合审阅 `SDK/orchestrator/composition_review.py` | 同一目标同时只采用一个做法，换路线要先撤旧路线（B-4）。"或"由审阅员按原话判（A 级 #9） |
| GoalResolution 绑定完整字段（含独立 Reviewer、未决事项、当前有效性）；ACCEPT 与 GoalResolution 是两个动作 | §8.2 | 部分 | `SDK/contracts/resolution.py:1418-1441`；`resolution_commits.py:589,960` | 没有"未决事项"字段（B-8） |
| 多份支持，一份足够即仍有效；全部失效才失效 | §8.2 末 | 已删 | 知识只记一组依据 `SDK/orchestrator/commit_service.py:1271` `_knowledge_support`；`SDK/memory/knowledge_standing.py:85` | A 级 #9（单组依据记为有意改动） |
| 共享去重：类型化参数、scope、输入版本、标准、授权、时效、副作用身份全匹配；消费者集合；最后一个消费者退出才收敛 | §8.3 | 做法不同 | 点名共用 `SDK/planning/htn/grounding.py:208,536`；候选 `SDK/orchestrator/taskgraph_plan_sources.py:78`；收敛 `SDK/graph/convergence.py` | 只有规划器点名时才共用、只限普通步骤（B-24） |
| ExecutionFeedback 合同十二个字段；观察、诊断、建议分开存；按诊断查决策表 | §9.1 | 已删 | 全库搜不到 `ExecutionFeedbackV1`；失败作为事实写进修复请求 `SDK/orchestrator/planning_repair_requests.py` | B-9 |
| 重复无进展的计数不因换名字而重置 | §9.1 表末行 | 做法不同 | 停滞问规划器的计数 `planning_repair_requests.py` `stall_asks_since_new_work` | 修做法机会按步骤算（用户 09-30 18:15 选 A，B-15） |
| PlanRevisionProposal 字段（触发事实、读集、基础版本、采用/退出、修改、共享影响、要求覆盖、预算影响、在途处理、理由） | §9.2 | 部分 | `SDK/contracts/htn.py:2510` `PlanProposal`、`:2902` `ProposedPlanDelta`、`:2832` `ObligationCoverage` | 没有显式的"共享影响""预算影响"字段（B-8，裁决认定由系统计算） |
| 影响分析：系统算影响范围，读依赖不完整就保守闭包 | §9.3 | 做法不同 | `SDK/planning/htn/repair_decision.py` `analyze_impact`；`SDK/graph/convergence.py` | 屏障加使用前重验，取代脏标记加局部重算（用户 09-30 拍板，B-14） |
| 原子切换：先过整数闸门，再核语义读集；撤销被替代工作的派发代号；大图走持久 PREPARED + 激活屏障 | §9.4 | 部分 | `SDK/orchestrator/plan_commits.py:638,1174,1217`、`_revoke_running_work`；`SDK/orchestrator/_read_set.py:196-212` | 整数闸门删了（B-13）；读集缺预算授权修订、支持集合修订、manager epoch（B-12）；大图分阶段激活没做（C-3） |
| 新路线中，旧路线结果不明的外部操作先阻塞核对，不重新执行 | §9.5 | 部分 | 核对 `SDK/runtime/operation_reconciliation.py:160` | 台账自己承认崩溃切点 K10 绑的用例不涉及换做法（见第六节） |
| Frontier 不只有 READY 叶子，还包括分解、取证、复审、比较、人工事项 | §10.1 | 做法不同 | `SDK/graph/eligibility.py:1612-1646`（规划/执行/准入三层）；分配器只排已准入的叶子（`allocator.py:392` `allocate_v2`） | 规划、取证都由主循环直接唤醒规划器，不和执行争名额（B-4、B-21） |
| 搜索策略全集：greedy、best-first/beam、Best-of-N、模拟前瞻、失败剪枝 | §10.2 | 已删 | 全库没有 | B-4 |
| 评分逐项记录并注明来源；校准；统一消费；共享前置只扣一次；先保护验证和尾额 | §10.3 | 部分 | 尾额 `SDK/governance/tail_budget.py`；共用步骤只算一份（`obligation_accounts.py:24`） | 逐项带来源的评分删了（B-21） |
| 分层 Manager：scope、coordinator epoch、额度 | §10.4 | A 级排除 | `bump_epoch` 只作作用域纪元（`SDK/storage/htn_store.py:1371`） | A 级 #1（组级管理员） |
| 按物理资源合并容量；高低水位；老化；对震荡、重复提案、改名、零进展有具名停止原因 | §10.5 | 部分 | `SDK/runtime/provider_budget_guard.py:239-250`；`SDK/scheduling/backpressure.py`；`SDK/contracts/state_machines.py:28-59` | 停止原因只有 `no_dispatchable_work`、`store_fault` 等，没有"震荡/重复提案/改名"（B-20） |
| FactRecord：对象、谓词、参数、观察时间、有效区间、来源版本、检查范围 | §11.1 | 按计划在用 | `SDK/contracts/evidence_state.py:175` `ObservationRecord`；观察表迁移 39 | — |
| Knowledge 七个字段：claim_scope、assumptions、validity_interval、justification_sets、contradictions、assurance_level、permitted_uses | §11.2 | 部分 | `SDK/memory/verified_knowledge.py:31-53`（有 `support`、`disputed_by`、`evidence_trust`） | 适用条件（B-27）、多组依据（A 级 #9）有记录。**`validity_interval`、`permitted_uses`、`assurance_level` 没有记录（C-2）** |
| 无锚循环不能自证 | §11.2 | 按计划在用 | `SDK/knowledge/justifications.py:878` `grounded_closure`，生产经 `SDK/knowledge/assurance_sources.py:363` → `SDK/orchestrator/assurance_validity.py:44`；知识读时判定也防环（`knowledge_standing.py:85` 的 `_seen`） | — |
| 统一失效服务：扩展 `source_dependencies`；依赖索引覆盖到 Context Cache；检索前过滤、读后复核 | §11.3 | 做法不同 | `memory/source_dependencies.py` 已在 opt.134 删；读时判定 `knowledge_standing.py:85`、`context/knowledge_tools.py:54,64` | 不是统一的依赖索引，是各处读时判定；做法适用性由类型目录哈希承担（B-28） |
| 独立审阅与事实使用分级；候选区与事实区分开 | §11.4 | 按计划在用 | `SDK/context/knowledge_tools.py` 分层目录；`commit_service.py:1238` `_review_confirmations` | — |
| ValidityWitness 七字段；同步 epoch 屏障 + 异步重算；消费者重算结果分五类；VALIDITY_RECHECK_PENDING | §11.5 | 做法不同 | `SDK/contracts/evidence_state.py:588`；`SDK/graph/eligibility.py`（`VALIDITY_RECHECK_PENDING`）；五分类 `SDK/knowledge/justifications.py:1244` `reevaluate_consumer` 没有生产调用方 | 屏障加使用前重验（用户 09-30，B-14）；纪元只有 `mission` 一个作用域（B-17） |
| 带极性正规则的最小不动点；超限报 EVALUATION_INCOMPLETE | §11.5 | 按计划在用 | `SDK/knowledge/bounded_closure.py:73`；`SDK/assurance/grounding.py:42` | 只用在保证通道 |
| SessionMemory 与 Blackboard 分开；增量原文；冻结请求恢复时不重新检索 | §12.1 | 按计划在用 | `RT/agents/arp/retriever.py`（`start_frozen`/`resume_frozen`） | — |
| 全历史检索：精确、词面、向量在完整范围上取有界候选；覆盖不全如实报出 | §12.2 | 按计划在用 | `RT/agents/arp/search.py`；`RT/agents/arp/retrieval_status.py:27` `status_for`（`PARTIAL`）；旧 2000 条窗口在 a23ad799 删除 | 覆盖不全报 `PARTIAL`，不叫 `index_partial`，语义相同 |
| Blackboard 加向量候选和重排；语义摘要绑定原文；禁止截断摘要 | §12.3 | 部分 | 词面打分 `SDK/context/retrieval.py`（`event_handler.py:64` 引用）；核对过的摘要层 `knowledge_tools.py:64` `step_summaries`；规则截断的 `context/compression.py` 已删 | 混合检索不做（B-25）；Group Manager 分支摘要不做（A 级 #1） |
| 窗口公式；按 token 保留最近完整交互；未闭合的工具调用不拆；多档容量做对比实验 | §12.4 | 部分 | `RT/agents/arp/rules.py:44-95` `Budget.capacity`、`:176` `allocate` | 多档对比实验不做（A 级 #9） |
| 上下文来源清单十三项字段 | §12.5 | 部分 | `RT/agents/arp/context/composer.py:447-460` | 缺要求版本、做法绑定、摘要版本三个字段（B-11） |
| ReviewPackage → ReviewRecord → Acceptance/GoalResolution；审阅包锁定的内容；审阅员不能写被审对象 | §13 | 按计划在用 | `SDK/contracts/resolution.py:808` `ReviewPackage`、`:591` `WorkspaceAccess`（不允许 WRITE）、`:1081` `ReviewRecord` | — |
| 逐准则 PASS/FAIL/UNKNOWN；全局 ACCEPT/REWORK/INCONCLUSIVE/REJECTED；执行状态分开记录 | §13 | 按计划在用 | `resolution.py:604-628` | — |
| 跨域检查能力（代码、文档、SQL、网页/API、Lean、实验、企业流程）；至少一个完整的形式化适配 | §13 表 | A 级排除 | 只有代码（沙箱 pytest）和文档 | A 级 #1（跨领域验证） |
| 六种 ReviewPurpose 与账户归属表 | §13（v1.4） | 部分 | `resolution.py:554-588`；交易处 `SDK/orchestrator/assurance_review_transport.py:184-195` | 组合审阅实际记在任务总账，合同表仍写"父复合任务"，两处说法不一致（B-22） |
| 成功表达式是受限 AST；硬约束一律 AND；四个事实分开 | §13（v1.4） | 按计划在用 | `resolution.py:307-343,434`；`SDK/verification/acceptance_rules.py`；`DeliveryReceipt` 在 `:1584` | — |
| Task 级能力域：一个 Mission 里不同 Task 用不同领域 | §14.1 | A 级排除 | 领域仍按整个任务冻结 | A 级 #1 |
| 能力状态：注册、已配置、可达、健康、获准、兼容分开记录 | §14.2 | 已删 | `SDK/planning/htn/applicability.py:398-454` `CapabilityRecord` | "可达""兼容"不预先探测（B-27） |
| OperationId 跨 Task/Attempt 稳定；准备 → 审阅 → 授权 → 派发 → 确认/UNKNOWN → 核对；不能可靠核对就交人工 | §14.3 | 按计划在用 | `SDK/orchestrator/operation_materialization.py:42`、`operation_proposal_review.py:324`、`action_commits.py`、`SDK/runtime/operation_reconciliation_file_publish.py` | — |
| 补偿是新的受控操作 | §14.3 | 已删 | 补偿入口删了（a23e7660） | B-16 |
| OperationEnvelope 冻结；同一身份不同信封报 OPERATION_PAYLOAD_CONFLICT；三轴状态；交接三段；能力矩阵 | §14.3（v1.4） | 部分 | `resolution.py:1670`；冲突码在 `SDK/storage/htn_store.py`；能力 `SDK/runtime/operation_profiles.py:95` | 三轴状态删了（B-9）；连接器只有文件发布一种 |
| 平台无关；三种系统各自实测；缺隔离就拒绝；子进程身份持久化 | §14.4 | 部分 | `SDK/runtime/sandbox.py:14,55,326,393`（macOS Seatbelt） | 只做 macOS（A 级 #9）。**子进程身份和回收材料不持久化，跨机器 Executor 没有（C-5）** |
| Commitment、ExecutionCycle、持久 wake、时区、迟到周期策略、暂停/取消语义 | §15 | 未做 | 全库搜不到 | 用户 09-30 暂不做（B-37，有用户原话，A 级清单没列）；取消时写明结果不明的操作已做（A 级 #9） |
| 要求变更产生新版本，只能经授权入口 | §15 末 | 按计划在用 | `BE/chat_tool.py` `mission_amend` → `BE/service.py:1133` → `SDK/orchestrator/requirements_amendment.py` | — |
| 纯 reducer 重建要求、义务、计划、验收、知识、预算、等待/周期、操作 | §16.1 | 做法不同 | `SDK/observability/business_replay.py:165,261`；`SDK/storage/source_records.py:218` | 用存储层统一记行变化的通用折叠（B-30）；周期没有；v2 删掉，没有保留（A 级 #17） |
| 事件、投影、Outbox、回执在同一事务；两库之间有崩溃点测试 | §16.2 | 按计划在用 | `SDK/orchestrator/commit_service.py`；`T/acceptance_assets/crash_points.json` K01～K18 | — |
| 旧库迁移：GenesisSnapshot、旧事件字节不变 | §16.3 | A 级排除 | 搜不到 `GenesisSnapshot` | A 级 #17（开发期不兼容旧数据）。迁移快照也在用户 09-30 暂不做之列 |
| 恢复 manifest、SIDE_EFFECTS_DISABLED、删除 tombstone | §16.4 | 未做 | 全库搜不到 | 离线备份与受管恢复删了（A 级 #4）；删除标记属用户 09-30 暂不做（B-37）。恢复时禁止副作用这一层没有归属（C-1） |
| 可训练的方法检索、优先级、路由；校准；离线晋级 | §17.1 | A 级排除 | `governance/learning.py` 在 2c505d57 删 | A 级 #1（可训练学习）。§18.2 要求的"保留 rules-v1"也一起删了（A 级 #3） |
| 界面显示目标与方法层次、备选/采用路线、未细化目标、共享子目标、要求与证据版本、被替代历史、为何改图、失效验收、预算去向、等待原因、未知外部操作 | §17.2 | 部分 | `FE/views/MissionsView.tsx` `BudgetByDuty`、`StepsNoLongerCounting`；`FE/views/PlanChangePanel.tsx`；`FE/views/liveGraph/`；`FE/views/ChatMissionCard.tsx`（显示要求版本） | 历史只进诊断导出（B-23）；**证据版本、备选路线、执行图上的共享子目标没有展示，也没有裁决（C-4）** |
| 后端输出权威投影；前端按公开 Schema 校验；收到坏消息标协议错，旧画面标"已过期" | §17.2 | 部分 | `FE/views/PlanChangePanel.tsx` 读失败标"已过期" | 前端没有统一的 Schema 校验（没有 zod/ajv 一类）；只有改计划面板有"已过期"处理 |
| §18.1 新目录全部落地，且每个模块都进完整场景 | §18.1 | 部分 | 存在：`contracts/{htn,obligations,evidence_state,resolution}.py`、`planning/htn/*`、`knowledge/{justifications,validity,predicates}.py`、`verification/acceptance_rules.py`、`graph/{task_network,projection_validation,eligibility}.py`、`artifacts/input_bindings.py`、`storage/{htn_store,obligation_store}.py`、`orchestrator/{plan_commits,resolution_commits}.py`、`observability/business_replay.py` | 不存在：`contracts/{feedback,commitments}.py`、`planning/search/`、`planning/repair/`（拆进了 `planning/htn/repair_*`）、`planning/htn/synthesis.py`、`knowledge/validity_projection.py`、`verification/review_coordinator.py`（由保证通道承担）、`runtime/operation_protocol.py`、`context/{semantic_retrieval,scoped_summary,selection_manifest}.py`、`commitments/`、`graph/demand.py`（由 `convergence.py`/`revision_pins.py` 承担）、`storage/{validity_store,commitment_store}.py`、`orchestrator/{requirement_commits,evidence_commits,recovery_coordinator}.py`、`observability/reconstruction_manifest.py`、`learning/` |
| §18.2 现有文件改法 | §18.2 | 做法不同 | `planning/planner.py`、`graph/changes.py`、`graph/task_graph.py`、`planning/manager.py`、`planning/candidate_selection.py`、`graph/dependency_checker.py` 随平面模式删（A 级 #3）；`memory/source_dependencies.py` 在 opt.134 删；`orchestrator/mission_tail_commits.py` 不存在（义务账改成读时推出，B-19） | "保留旧解析/旧冻结提示"按 A 级 #17 不再适用 |
| §18.3 核心函数合同：`assess_method`、`ground_method`、`compile_refinement`、`analyze_impact`、`commit_plan_revision`、`apply_goal_review` | §18.3 | 部分 | 前五个都在（`planning/htn/applicability.py:493`、`grounding.py`、`compiler.py:186`、`repair_decision.py`、`plan_commits.py`） | `apply_goal_review` 由 `resolution_commits.py:589` `accept_review` + `:960` `commit_goal_resolution` 两段代替，同义 |
| §18.4 拟新增的表 | §18.4 | 部分 | 有：obligations、requirement_revisions、method 表、method_instances、plan_revisions、typed_edges、plan_read_sets、observations、goal_resolutions | 没有：justifications（两表在迁移 39 删）、search_states、commitments/cycles、durable_wakes、notification_outbox（通知走保证通道消费者）、reconstruction_baselines |
| §18.5 语义绑定：缺绑定算损坏；`EligiblePrimitiveTask` 门；四条兼容硬约束 | §18.5 | 做法不同 | `SDK/storage/taskgraph_store.py` `require_bound`；`allocator.py:201-262` | 兼容约束 1～3（默认 legacy、旧 READY 入口、旧事件字节）按 A 级 #3/#17 删；约束 4（按形态拦截）在用 |
| §18.5 LLM 侧合同：标签块经 output_blocks 严格解析，格式错误走有界修复 | §18.5 | 按计划在用 | `SDK/runtime/role_templates.py`、`output_blocks.py`；`SDK/planning/decision_codec.py` | — |
| §18.6 测试路径 | §18.6 | 部分 | `T/full_target/` 下有 `test_obligation_conservation.py`、`test_htn_recursion_fuel.py`、`test_htn_and_or_shared_goal.py`、`test_htn_novel_method_admission.py` 等 | 没有 `test_search_restart_and_simulator_guard.py`、`test_commitment_durable_wake.py`、`test_policy_training_and_promotion.py`（对应功能已删或不做） |

### 1.3 §24 附件 TG（采纳的 12 条裁决）

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| ①ORDER 只表示放行：accepted / settled_terminal；UNKNOWN 不算结清；不给读写权 | §24.1-1 | 按计划在用 | `SDK/contracts/htn.py:175` `ReleaseCondition`；`SDK/graph/eligibility.py` | 改坏 M04、M09 守住（台账 R15） |
| ②复合任务编译成入口门和出口门；`P.exit→Q.entry`；组合审阅等孩子 | §24.1-2 | 按计划在用 | `SDK/graph/task_network.py`（COMPOUND_ENTRY/EXIT 共 8 处） | — |
| ③DATA 走显式端口：DataRequirement → BoundInput → InputManifest；PINNED 与 FOLLOW_AUTHORIZED_REVISION 分开 | §24.1-3 | 做法不同 | `SDK/artifacts/input_bindings.py`；搜不到 `PINNED`/`FOLLOW_AUTHORIZED` | "钉住"整套删了，一律跟随现行验收（TG 补全偏离 20/30，B-24） |
| ④不再汇入所有祖先文件；产物身份 = (工作区, 路径)；同名不同 hash 必须显式选择 | §24.1-4 | 按计划在用 | `SDK/artifacts/versioning.py`；运行期写冲突 `hierarchical_dispatch.py` `write_conflicts` → 修复请求 | — |
| ⑤前提检查三阶段 | §24.1-5 | 做法不同 | 同 §6.6 | A 级 #9 |
| ⑥三层前沿；`evaluate_readiness` 给结构化原因；READY 降为索引；交接前复查 | §24.1-6 | 按计划在用 | `SDK/graph/eligibility.py:1222,1612-1646` | — |
| ⑦五类版本轴；`goal_id` 是 TaskRef | §24.1-7 | 部分 | 同 §6.4 | 两轴删了（B-7） |
| ⑧读集含集合谓词与"不存在"；两个提案合并后要在当前事务状态上完整再验证 | §24.1-8 | 部分 | `AbsenceRead` 只在就绪见证里用（`eligibility.py:1191`）；提交时重验 `_read_set.py` | 规划提案读集不含"不存在"和集合（B-12） |
| ⑨四种去重分开；DemandRef；退出分支只删自己的采用关系 | §24.1-9 | 做法不同 | `SDK/graph/convergence.py`、`revision_pins.py`；`SDK/planning/htn/compiler.py` `_merge`/`retires` | 相似候选建议删了（B-24）；`graph/deduplicator.py` `find_duplicates` 没有产品调用方 |
| ⑩只对执行投影查环；无锚 SCC；资源 wait-for 另做 | §24.1-10 | 部分 | `SDK/graph/projection_validation.py:232` | 资源 wait-for 死锁分析没找到 |
| ⑪拓扑不完整时抛 GraphIntegrityError，停该 scope 的派发；诊断路径容错 | §24.1-11 | 按计划在用 | `SDK/graph/projection_validation.py:110`；`event_handler.py`（计划完整性停止） | — |
| ⑫七个崩溃切点逐一做故障注入 | §24.1-12 | 部分 | `T/acceptance_assets/crash_points.json` K01～K18 | 台账承认 K10 绑的用例不对题（第六节） |

### 1.4 §25 附件 AER（采纳的 11 条裁决）

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| ①共同不变量 I01～I20 | §25.1-1 | 部分 | 分散在 `resolution.py`、`applicability.py:335`、`operation_reconciliation.py`、`commit_service.py` | I12（跨库不假定 ACID）有切点；I15/I16 中"恢复时"那一半依赖 C-1 |
| ②RequirementsRevision 与 Criterion 字段（origin、requirement_class、evaluation_kind、required_evidence_policy、phase、temporal_use、amendment_policy）；受限 AST | §25.1-2 | 部分 | `resolution.py:191-204,434-446` | `phase` 删了，要求类别只剩两类（A 级 #9） |
| ③六种审阅用途；ReviewPackage 是不可变锚；Verifier 七步；独立性；不重复抽样直到 PASS；冲突不投票 | §25.1-3 | 按计划在用 | `resolution.py:554,808`；判不下来先复审一次再问人 `SDK/orchestrator/assurance_review_import.py:324` | 判不下来的处理按用户 09-30 定（A 级 #20） |
| ④四个事实分开；发送不等整个 Mission 完成；CarryForwardReceipt | §25.1-4 | 部分 | `resolution.py:1081,1252,1418,1584` | 搜不到 `CarryForwardReceipt`，由计划提交里的 `resolution_reuses` 记（B-8） |
| ⑤证据四维；(t,f)；条件 AST 不用 eval | §25.1-5 | 部分 | `SDK/contracts/evidence_state.py:45,54,62,130` | 知识记录没有"使用权"维（C-2） |
| ⑥最小不动点；不做 negation-as-failure；`was_used` ≠ `supports_for_use` | §25.1-6 | 部分 | `SDK/knowledge/justifications.py:878,1285` `LineageRecord` | `LineageRecord` 没有生产调用方；知识侧只有一组依据 |
| ⑦ValidityWitness；同步屏障 + 异步重算；消费者五分类 | §25.1-7 | 做法不同 | 同 §11.5 | B-14 |
| ⑧OperationOccurrenceId、Envelope、三轴、交接三段、条件写、ReconciliationResult、能力矩阵 | §25.1-8 | 部分 | 同 §14.3 | 三轴和 `ReconciliationResult` 删了（B-9） |
| ⑨跨库 Outbox 去重；九个崩溃切点；关键未知事件停止该流；GenesisSnapshot | §25.1-9 | 部分 | crash_points K01～K18；两库对照 `business_replay.py` `verify_execution_ledgers` | GenesisSnapshot 不做（A 级 #17） |
| ⑩取消不是结果；RecoveryObligation 事务化移交；补偿是新操作；费用按原身份核对 | §25.1-10 | 部分 | 取消时列出结果不明的操作 `commit_service.py:1471,1526` `_note_unresolved_actions`；迟到用量 `SDK/orchestrator/accounting_recovery.py` | 报告加通知（A 级 #9）；补偿删了（B-16） |
| ⑪完整恢复协议八步：RECOVERY_LOCKED → 清单 → 重建 → inbox/outbox → 未决核对 → fence → 孤儿回收 → READY/DEGRADED_RECOVERY | §25.1-11 | 部分 | 只有启动恢复 `SDK/orchestrator/event_handler.py:2118` `recover()` | 没有 RECOVERY_LOCKED、DEGRADED_RECOVERY、孤儿回收，也没有任何决定（C-1） |

### 1.5 正式补遗与范围变更

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| Operation 完成合同：四个 codec（Spec、Scope、Contribution、OutcomeBinding） | 完成合同补遗 §2 | 按计划在用 | `SDK/contracts/operation_completion.py:319,423,566,715` | — |
| Spec 唯一写方 `approve_operation_completion_spec` + `bind_requirement_authority` | 补遗 §3 | 按计划在用 | `SDK/api/operation_completion.py:23,110`；`SDK/orchestrator/operation_completion.py:154`；产品调用 `SDK/deployment/duties.py:105` | — |
| Scope 和计划同事务冻结 | 补遗 §2.2、§4 | 按计划在用 | `SDK/orchestrator/operation_completion.py:396` `freeze_plan_completion_scopes` | — |
| 准备产物被接受 ≠ 效果完成；唯一读服务 `OperationCompletionReader` | 补遗 §5 | 按计划在用 | `operation_completion.py:302`；读方 `SDK/orchestrator/completion_inputs.py:187,369`、`operation_outcomes.py:56`、`assurance_check_import.py:65` | — |
| T3：`prepare_operation_outcome_review` / `accept_operation_outcome`；DeliveryReceipt 是投影 | 补遗 §6 | 按计划在用 | `SDK/orchestrator/operation_outcomes.py:137,552` | — |
| 四张窄表 | 补遗 §8 | 按计划在用 | `SDK/storage/operation_completion_schema.py:12,29,55,90` | — |
| 具名拒绝码 OP_* | 补遗 §11 | 按计划在用 | `OP_REQUIREMENT_MAPPING_*`、`OP_COMPLETION_SCOPE_UNRESOLVED` 等出现在 `operation_completion.py`、`operation_intent_sources.py`、`completion_inputs.py` 等文件 | — |
| `SubmitOperationIntentV2` + `completion_slot` | 补遗 §4 | 按计划在用 | `SDK/contracts/operation_intents.py:78` | — |
| OCC-01～12 决定性验收 | 补遗附录 A | 部分 | `T/full_target/operation_completion/` 有 `test_publish_variants.py` 等 | 没有逐条核对 OCC 编号与测试名的对应（本次不跑测试） |
| D1：接受候选不物化；经 facade 提交意图 → ACTION_PROPOSAL 审阅 → T1 一次事务物化 | D1-D3 补遗 §2～3 | 做法不同 | `SDK/api/operation_intents.py:15`；`SDK/orchestrator/operation_materialization.py:42`；`operation_proposal_review.py:324`；Host 入口 `BE/handlers.py:265`、`BE/service.py:1183`；产品主路 `SDK/orchestrator/system_operations.py:534` | 产品里的发布由系统挂步骤提交意图（A 级 #14） |
| D2：三个专用 payload、白名单 resolver、connector profile、三种 hash、未应用证明 | 补遗 §4～8 | 按计划在用 | `SDK/runtime/operation_payloads.py`、`operation_ref_resolver.py`、`operation_profiles.py:95`；`SDK/contracts/operation_payloads.py` `NonapplicationProofKind` | 连接器只有文件发布一种 |
| D3：内部延期 continuation、停止闸门、原决定冷恢复 | 补遗 §9 | 已删 | 迁移 36 删表（`SDK/storage/schema.py:684,1412`）；15e56cfb 删 `planning_repair_continuations.py` | 实施记录 A′-3 的偏离裁决认定执行图绑定后这条路走不到，由执行图收敛代替（B-34，裁决子代理，没有用户原话） |
| D1-D3 拒绝码（`OP_INTENT_SOURCE_UNRESOLVED`、`REPAIR_MANUAL_REQUIRED` 等） | 补遗 §12 | 部分 | `OP_*` 在用 | `REPAIR_SOURCE_UNAVAILABLE`、`REPAIR_MANUAL_REQUIRED` 随 D3 删 |
| 范围变更：移出 NanoJev（运行时、影子比较、PR-7、`RETRY_OR_ESCALATE`） | NanoJev 范围变更 | A 级排除 | 全库搜不到 NanoJev 运行接线 | A 级 #1 |
| §21.5 收益假设与 Grok 验收协议（H/F 配对 36 局、硬性不变量、机制触发题） | §21.5 | 未做 | — | 用户 10-03："§21.5 配对对照与 R52 外部评测暂不做，HTN 补齐后在产品路径上重建"（补齐计划表一 20），记 D 级 |
| §21.3 需求状态阶梯与台账 | §21.3/§21.4 | 做法不同 | 台账另建（不改冻结包） | 补齐计划表二 23 |
| 小片纪律：每片先写红测试、独立全量回归、旧模式零回归 | §23 | 做法不同 | — | 用户 10-01 定"不许自行跑全量回归"，10-03 定"先写完再测"；旧模式已删 |
| P1～P9 关闭条件里的 T 场景真实运行证据 | §23 | 部分 | 台账：90 个场景里 13 个"通过"，45 个"未运行" | — |
| 附件 TG 场景 A/B/C 终局验收 | §24.3 | 部分 | 台账 T061～T068、T083～T086 大多"未运行" | — |
| 附件 AER 闭环 A/B/C/D 终局验收 | §25.2 | 部分 | — | 闭环 D 依赖恢复协议（C-1） |
| 48 条 AER 场景挂到 T 条目 | §25.3 | 部分 | 台账场景表 | 多数"未运行" |
| 附件文件名与目录归属（knowledge/ 为准；graph/eligibility.py） | §24.2、§25.2 | 按计划在用 | `SDK/knowledge/`、`SDK/graph/eligibility.py` | — |
| 风险项：P3.6 开工前做支持集合 CAS 微基准 | §25.2 | 已删 | 支持集合两表删了（迁移 39） | 前提消失 |

### 1.6 附录 B：R01～R60

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| R01 稳定 BaseAgent 身份 | 附录 B | 按计划在用 | `RT/agents/arp/session_lifecycle.py`、`lifecycle.py` | 台账"已接入"属实 |
| R02 单主 Agent；子 Agent 按需建；物理并发有界 | 附录 B | 按计划在用 | `BE/chat_tool.py:113`；`event_handler.py:668` | 属实 |
| R03 Task、Attempt、AgentTurn、Operation 身份分开 | 附录 B | 按计划在用 | `resolution.py:1670`（operation_id、occurrence）；Attempt/Turn 在 contracts/models | 属实；K10 证据不对题 |
| R04 复合/原子语义 | 附录 B | 按计划在用 | `event_handler.py:10063`；`allocator.py:201-262` | 台账"行为已验证"属实 |
| R05 版本化、参数化的 MethodContract 与前提 | 附录 B | 部分 | `htn.py:895`；`applicability.py:196` | 失败/过期/适用范围没有字段（B-6）；前提只查一个时点（A 级 #9）。台账"已接入"偏高 |
| R06 真/假/未知/冲突 | 附录 B | 按计划在用 | `applicability.py:88-120`；`evidence_state.py:373`；`BE/hierarchical.py:24` | 属实 |
| R07 方法生成、验证、注册、检索、淘汰闭环 | 附录 B | 做法不同 | `plan_commits.py:381`；`method_library.py:131,183,251` | 晋级口径用户定（A 级 #5）；泛化不做（B-26） |
| R08 递归 HTN、任意部分序、无环 | 附录 B | 做法不同 | 三层（`BE/hierarchical.py` `levels`）；`consume_fuel` 只在测试 | B-2、B-3 |
| R09 按能力与风险决定直做/分解/取证/重规划 | 附录 B | 做法不同 | `planning_decisions.py:99-106`；`planning_evidence.py` | 交规划器判断（A 级 #8） |
| R10 AND 与 OR 分开 | 附录 B | 部分 | `task_network.py:283`；审阅员提示词 `SDK/assurance/review_input.py:77-80` | OR 路线不能并存（B-4） |
| R11 共享子目标与适用性证明 | 附录 B | 做法不同 | `grounding.py:208,536`；`taskgraph_plan_sources.py:78` | 只限普通步骤、规划器点名（B-24） |
| R12 父目标组合判据 + 显式 GoalResolution | 附录 B | 部分 | `composition_review.py`；`resolution_commits.py:960` | 没有"未决事项"（B-8） |
| R13 观察、诊断、建议分开存 | 附录 B | 已删 | 搜不到 `ExecutionFeedbackV1` | B-9 |
| R14 局部修复，尽量保留有效成果 | 附录 B | 做法不同 | `T/product_world/test_repair_replace_method.py`；`SDK/orchestrator/carried_review.py:54` | 保守复检（B-14） |
| R15 DATA/ORDER/ASSUMPTION 边分开 | 附录 B | 部分 | `task_network.py:131-142` | ASSUMPTION 边删了（B-10）。台账"行为已验证"只证明了 DATA/ORDER 那一半 |
| R16 计划读集、原子激活、在途 generation | 附录 B | 部分 | `plan_commits.py:638,1217`；`_read_set.py:196-212` | 整数闸门删（B-13）；读集缺项（B-12） |
| R17 完成历史不改，当前验收可失效 | 附录 B | 按计划在用 | `planner_views.py:38`；`requirements_amendment.py:126`；`htn_store.py:1327` | 属实 |
| R18 Plan-space 搜索、可恢复的搜索状态 | 附录 B | 已删 | 搜不到 | B-4（用户原话只说到"同一步多次尝试择优"） |
| R19 Best-first/Beam 与模拟前瞻 | 附录 B | 已删 | 搜不到 | B-4 |
| R20 分层 Manager | 附录 B | A 级排除 | — | A 级 #1 |
| R21 失败片段复用与跨方法综合再验证 | 附录 B | A 级排除 | 2db8df6e 删片段验证；3d218b14 删综合 | A 级 #3 |
| R22 角色是搜索偏置；多样性 | 附录 B | A 级排除 | 只剩规划器、执行者、审阅者 | A 级 #3（平面角色随平面删） |
| R23 真实进展与边际价值信号 | 附录 B | 已删 | `allocator.py` 里没有对应项 | B-21（F 阶段裁决） |
| R24 按 Task/Method 分配角色、模型、预算 | 附录 B | A 级排除 | Host 不传 `routing` | A 级 #2 |
| R25 稳定 Obligation 防止刷重试和预算 | 附录 B | 部分 | `obligation_accounts.py:24,51` | 只记账不卡上限（B-19）；按步骤算修做法机会（B-15） |
| R26 现实 Provider 容量、上下文上限、尾额 | 附录 B | 按计划在用 | `provider_budget_guard.py:239-250`；`BE/settings.py:55` | 属实 |
| R27 背压、探索保底、抗振荡 | 附录 B | 部分 | `backpressure.py` | 抗振荡没有具名停止原因（B-20） |
| R28 独立语义 Verifier 与程序检查分工 | 附录 B | 按计划在用 | 保证通道六个入口；`SDK/assurance/checks.py:393` | 属实；审阅员工具窄（D 级） |
| R29 条件化 Knowledge 与多重 Justification | 附录 B | 部分 | `verified_knowledge.py:31-53` | 条件（B-27）、多组（A 级 #9）、三字段（C-2）。台账"已接入"偏高 |
| R30 依赖失效贯通知识、摘要、方法、验收 | 附录 B | 做法不同 | `knowledge_standing.py:31,85`；`knowledge_tools.py:64` | 各处读时判定，不是统一索引（B-28） |
| R31 按 token 取最近的完整交互 | 附录 B | 按计划在用 | `RT/agents/arp/rules.py:44-95,176` | 属实 |
| R32 全部历史可检索 | 附录 B | 按计划在用 | `RT/agents/arp/search.py`、`retrieval_status.py:27` | 属实 |
| R33 共享知识混合检索 + 带条件的分层摘要 | 附录 B | 部分 | `SDK/context/retrieval.py`（只按词面）；`knowledge_tools.py:64` | 混合检索不做（B-25）。台账"已接入"偏高 |
| R34 按 scope/用途隔离 Context；已冻结的请求不重组 | 附录 B | 按计划在用 | `RT/agents/arp/retriever.py`、`context/composer.py` | 来源清单缺三字段归在 §12.5 那一行 |
| R35 父子交接、带类型的收件箱、澄清、回执 | 附录 B | 部分 | `BE/notices.py`；`FE/views/PlanningQuestions.tsx` | 带类型的收件箱不建（B-11）。台账"已接入"偏高 |
| R36 Proposal/Commit 唯一写入 | 附录 B | 按计划在用 | `commit_service.py`；`T/product_world/table_guards.py` | 属实 |
| R37 关键业务投影可由事件重建 | 附录 B | 做法不同 | `business_replay.py:165,261`；`source_records.py:218` | B-30 |
| R38 重放不执行工具；恢复不复活租约和旧权限 | 附录 B | 部分 | `event_handler.py:2118` `recover()`；重放只读 | 恢复协议缺失（C-1）；"旧授权不复活"没有用例。台账"已接入"偏高 |
| R39 两库之间有可靠收据和去重 | 附录 B | 按计划在用 | `accounting_recovery.py`；`verify_execution_ledgers` | 属实 |
| R40 外部 Operation 跨 Attempt 稳定；UNKNOWN 先核对 | 附录 B | 部分 | `operation_reconciliation.py:160`；`operation_materialization.py:42` | 三轴删了（B-9）；K10 证据不对题 |
| R41 补偿是单独授权的任务 | 附录 B | 已删 | 补偿入口在 a23e7660 删 | B-16（独立裁决员） |
| R42 长期 Commitment 与有限 ExecutionCycle | 附录 B | 未做 | 搜不到 | 用户 09-30 暂不做（B-37） |
| R43 持久定时、时区、错过的周期 | 附录 B | 未做 | 搜不到 | 同上 |
| R44 要求版本变更与独立重新验收 | 附录 B | 按计划在用 | `requirements_amendment.py`；`carried_review.py:54`；`commit_service.py:2800` | 台账"行为已验证"属实 |
| R45 跨 Mission 通知 Outbox 与收执 | 附录 B | 按计划在用 | `BE/notices.py`；`BE/service.py:1238` `pending_notices`；SDK 保证通道通知消费者 | 属实 |
| R46 同一 Mission 内不同 Task 用不同领域能力 | 附录 B | A 级排除 | — | A 级 #1 |
| R47 真实的形式化检查 | 附录 B | A 级排除 | — | A 级 #1 |
| R48 能力注册、可达、健康、授权分开 | 附录 B | 已删 | `applicability.py:398-454` | B-27（F 阶段裁决） |
| R49 Windows/Linux/macOS 各自隔离 | 附录 B | A 级排除 | `sandbox.py`（只有 macOS） | A 级 #9 |
| R50 最小权限、证据来源、提示注入隔离 | 附录 B | 按计划在用 | `SDK/runtime/tool_gateway.py:338` `is_untrusted`；`SDK/governance/permissions.py` | 属实（证据偏弱，F2 没有执行证据） |
| R51 完整 Trace、贡献链、费用归因 | 附录 B | 按计划在用 | `SDK/observability/traces.py`；`BE/diagnostics.py:49-52,372` | 金额不记（A 级 #1） |
| R52 独立外部评测 | 附录 B | 未做 | 评测臂已删 | D 级（用户 10-03：暂不做、补齐后重建）。台账写"不做（用户决定）"不准确 |
| R53 可训练的学习型策略 | 附录 B | A 级排除 | — | A 级 #1 |
| R54 策略离线评测、审批、回退；不许在线自我晋级 | 附录 B | A 级排除 | `SDK/governance/promotion.py`（只剩建库时那一版） | A 级 #3（策略评测随平面删）；用户 09-27 已关闭产品内策略晋升 |
| R55 用户长期记忆保持排除 | 附录 B | 按计划在用 | 记忆端口是空实现 | 计划自己定的排除规则。台账标"已接入"用词不当 |
| R56 Workflow 仍是工具层能力 | 附录 B | 按计划在用 | 编排里没有工作流入口 | 属实 |
| R57 关键契约的类型、边界校验、事务归属 | 附录 B | 按计划在用 | `SDK/contracts/error_table.py`；严格 codec | 属实 |
| R58 Host/UI 共享协议，后端是唯一状态投影 | 附录 B | 部分 | `FE/views/*`；`BE/projection.py` | 没有统一的 Schema 校验；语义展示缺口（C-4）。台账"已接入"偏高 |
| R59 停止、反复重规划、死锁、饥饿治理 | 附录 B | 部分 | `planning_repair_requests.py`；`state_machines.py:28-59` | 只有停滞计数；资源死锁分析没有（B-20） |
| R60 隐私、冷热归档、恢复 manifest、删除不复活 | 附录 B | 未做 | 搜不到 | 恢复 manifest 随受管恢复删（A 级 #4）；归档、删除标记属用户 09-30 暂不做（B-37） |

---

## 第二节：C 级无记录偏离清单

无记录：代码和原计划不一致，而且计划、裁决、台账、补遗里都没有登记或裁决过。按影响从大到小排。

| 编号 | 计划原文 | 现状 | 为什么算无记录 |
|---|---|---|---|
| C-1 | §25.1 第 11 条：完整恢复协议八步（RECOVERY_LOCKED → 清单核对 → reducer 重建 → inbox/outbox → 未决核对 → fence 收敛 → 孤儿回收 → READY/DEGRADED_RECOVERY）。§16.4：恢复时先进入 SIDE_EFFECTS_DISABLED。§23 P7.1：`recovery_coordinator` | 只有启动恢复 `SDK/orchestrator/event_handler.py:2118` `recover()`，会按任务重新核对已交出的操作。全库没有恢复锁、降级恢复状态、`RecoveryObligation`、孤儿进程回收，git 历史里也从没出现过 | 用户 09-30 的原话（PLAN-STATUS:476）是"P7 迁移快照/归档/删除标记暂不做"，没提恢复协议。A 级 #4 删的是保证通道的离线备份受管恢复，不是这一套。10-02 评估第 44 项把它算进"09-30 暂缓"，是扩大解读 |
| C-2 | §11.2：知识要有 `validity_interval`、`assurance_level`、`permitted_uses`。§25.1 第 5 条：证据四维（结论、有效性、保障范围、使用权） | `SDK/memory/verified_knowledge.py:31-53` 没有这三个字段 | F 阶段的裁决只说"适用条件"（claim_scope/assumptions）不加；一致性补改只说"多组依据"不做。这三个字段没有任何登记。台账 R29 写"已接入" |
| C-3 | §9.4：大型图使用持久的 PREPARED 版本加激活屏障，最后用短事务切换 | `SDK/orchestrator/plan_commits.py:1174,1217`：PREPARED 和 ACTIVE 在同一个事务里，没有大图分阶段准备 | 没有偏差单 |
| C-4 | §17.2：界面要显示证据版本、备选与采用路线、共享子目标 | 任务详情有预算去向（共用步骤在里面标"几个分支共用"）、还没细化的目标、不再算数的步骤；对话卡片显示要求版本。证据版本、备选路线、执行图上的共享子目标都没有 | 10-04 复核第 7 处点过名，建议"计划里补一句其余不做，或者补"。用户只定了加"不再算数"一行（A 级 #9），其余没有裁决、也没有归属 |
| C-5 | §14.4：子进程身份、沙箱归属、回收材料持久化，不能凭旧 PID 终止陌生进程；跨机器 Executor 用受认证的 TaskPackage | `SDK/runtime/sandbox.py:326,393`：只在内存里记 `root_pid`，靠进程组 kill，重启后没有回收材料；没有跨机器 Executor | 一致性补改只把"三种系统隔离"定为只做 macOS，没有覆盖持久化回收和跨机器 |
| C-6 | §17.2：前端用同一份公开 Schema 校验，坏消息标协议错 | 前端没有统一的 Schema 校验；只有 `FE/views/PlanChangePanel.tsx` 处理"已过期" | 台账 R58 写"已接入"，差距只写了"只有前端单元用例"，没登记缺统一校验 |
| C-7 | §10.5、§24.1 第 10 条：资源 wait-for 另做死锁分析；对死锁有具名治理 | 没找到资源 wait-for 分析；停止原因里没有死锁 | 补齐计划表二 20 只讨论了"反复提交同样计划"，没有讨论死锁 |
| C-8 | §11.3：依赖索引从事实一直覆盖到摘要和 Context Cache，"检索前过滤 + 读取后复核"两道都做 | 执行者上下文只在推送前过滤一次（`event_handler.py:5462`）；BaseAgent 侧召回的历史片段没有有效性复核 | 阶段 C 只记了"装上下文前过滤过时知识"，没说读后复核不做 |
| C-9 | §24.1 第 9 条：legacy 文本签名去重保留，新模式区分四种去重 | `SDK/graph/deduplicator.py` `find_duplicates` 没有产品调用方，只剩 `normalise_goal` 被分配器引用 | TG 补全偏离 18 只删了相似度建议，没说 `find_duplicates` 的去留，代码成了死代码 |

不算 C 级、但要知道的残留（和原计划不冲突，只违反"同一件事只留一条路"）：
- `SDK/contracts/obligations.py:511` `consume_fuel` 只给测试用。
- `SDK/knowledge/justifications.py:1244` `reevaluate_consumer`、`:1285` `LineageRecord` 没有生产调用方。
- `phase_check_points` 保留在合同里，生产只读 SELECT。
- 合同 `REVIEW_PURPOSE_ACCOUNTS` 仍写 COMPOSITION 记在父复合任务，实际记在任务总账（`assurance_review_transport.py:184-195`）。
- `SDK/storage/operation_seams_schema.py:77` 还留着已删表的建表文本（迁移 36 删表，是旧迁移文本，属正常）。

---

## 第三节：B 级子代理裁决偏离清单

规则：补齐计划、台账、TG 补全方案、各阶段裁决里写成"保持现状 / 有意偏离 / 接受的偏离"，由子代理裁决或计划自拟，没有用户逐条原话的，都算 B 级。"用户原话"一栏写"有"的，是查到了用户拍板的出处，但 00 口径第三节的 A 级清单没有列入，建议补入口径（标 ★）。

| 编号 | 计划原文 | 现状 | 裁决出处 | 用户原话 |
|---|---|---|---|---|
| B-1 | §7.3：独立的 MethodSynthesizer 提新做法；单一候选由程序直接用 | 规划器自己提，并过独立审阅；合成器角色删除 | `HTN裁定变更-2026-10-01.zh-CN.md`（决定人：用户） | 有 ★ |
| B-2 | §6.4、§7.1：任意深度递归，用燃料限制 | 最多三层（根、两层子目标、步骤） | 补齐计划表一 2；片 B 实施记录；裁定变更第五节"层数有上限" | 部分有（用户定"有上限"，"三层"由实施定） |
| B-3 | §6.4（v1.2 钉死）：燃料按义务计，每次展开扣燃料 | 只在开子义务时扣；`refine()` 删除；`consume_fuel` 只在测试 | 补齐计划第二节"本计划新增的偏离" | 无 |
| B-4 | R18/R19、§7.5、§10.2：计划空间搜索、best-first/beam、Best-of-N；OR 路线并存 | 都删了；同一目标同时只采用一个做法 | 补齐计划表一 4；删旧平面模式方案 | 部分有：用户 10-02 原话只说"同一步同时开两次尝试、择优随候选比较一起删"，没有说到 HTN 计划空间搜索 |
| B-5 | §10.4：任务级 Manager 观察、改图 | 删 | 补齐计划表一 5 | 有（随平面删，A 级 #3） |
| B-6 | §6.3：做法的失败与过期条件、适用范围字段 | 不加字段 | 开工前裁决（`HTN补齐-开工前裁决-2026-10-02.md`） | 无 |
| B-7 | §6.4：五类版本轴 | 只留三轴，`RecordVersion`/`ValidityRevision` 删 | 开工前裁决 | 无 |
| B-8 | §8.2、§9.2、§25.1-4：GoalResolution 的"未决事项"、提案的"共享影响/预算影响"、CarryForwardReceipt | 不单列，靠推导和 `resolution_reuses` | 开工前裁决 | 无 |
| B-9 | R13、§9.1：ExecutionFeedbackV1；§14.3：三轴状态、`ReconciliationResult` | 删 | 补齐计划第二节"本计划新增的偏离"、表二 13 | 无 |
| B-10 | R15、§6.5：支持、假设、监督三类边 | 不设 | 开工前裁决 | 无 |
| B-11 | §12.5 来源清单三字段；R35 带类型的父子收件箱 | 不加 / 不建 | 开工前裁决 | 无 |
| B-12 | §24.1-8、ADR-13 C29：读集含"不存在"、集合、支持集合修订、预算与授权修订 | 计划提案读集不含；预算和授权在提交事务里现读 | 阶段 D 开工裁决偏差单 1、2（补齐计划第 3.11 版） | 无 |
| B-13 | ADR-13：整数闸门保留，另立 ADR 才能取代 | 整数闸门删，计划修订号是唯一结构闸门 | 补齐计划待定③（裁决子代理，"结论报用户"） | 无（没找到用户回复） |
| B-14 | §9.3、§11.5：脏标记加局部重算；消费者五分类 | 屏障加使用前重验 | PLAN-STATUS:476 | 有 ★（用户 09-30 拍板） |
| B-15 | §9.1：重复无进展计数不因换名重置 | 修做法机会按步骤算 | PLAN-STATUS:510 | 有 ★（用户 09-30 18:15 选 A） |
| B-16 | R41、§14.3：补偿是新的受控操作 | 补偿入口删除 | `两个遗留问题修复记录.md`（独立裁决员） | 无 |
| B-17 | §11.5：按 scope 的 validity_epoch | 只有 `mission` 一个作用域 | 补齐计划待定②（裁决子代理） | 无 |
| B-18 | §18.5：新任务启用执行图前先过门禁、拿规划授权 | 建任务时直接绑定 | 补齐计划"阶段 A′ 新增的偏离" | 有（"用户采纳"） |
| B-19 | §19 P1.3、R25：沿义务继承失败计数与已消费额度 | 只记账、读时推出，不改上限 | 补齐计划表二 19；阶段 D 开工裁决 | 无 |
| B-20 | §10.5、R27、R59：震荡、重复提案、改名、零进展的具名停止 | 只有"停滞问 3 次后停" | 补齐计划表二 20；阶段 B 收尾裁决 | 无 |
| B-21 | R23、§10.1、§10.3：进展、不确定性、边际价值评分及来源；前沿竞争 | 删，交规划器 | 阶段 F 开工裁决（偏差单 F-8） | 无 |
| B-22 | §13 v1.4：COMPOSITION 记父复合任务账户 | 记任务总账 | 补齐计划表一 22；片 B 实施记录 | 无 |
| B-23 | §17.2：界面显示被替代但保留的历史 | 只进诊断导出 | 补齐计划表一 23；TG 补全偏离 13 | 部分有（用户要求界面简洁） |
| B-24 | §8.3、§24.1-3/9：按签名共享、共享子目标、相似候选、PINNED/FOLLOW 两种策略 | 只有规划器点名才共用，只限普通步骤；钉住删除 | TG 补全偏离 14～20、28、30、33 | 无 |
| B-25 | §12.3、R33：Blackboard 混合检索（向量 + 重排） | 不做 | 补齐计划表二 12b | 部分有（用户 10-02 说"审阅员通过黑板自取"，没说不做向量） |
| B-26 | §7.3 末段：方法泛化（变量化、前提提取、负例、效果统计） | 全库做法只当先例 | 补齐计划第二节；阶段 C3 开工裁决 | 无 |
| B-27 | R29 适用条件；R48 能力"可达/兼容" | 不加、不探测 | 阶段 F 开工裁决（F-8） | 无 |
| B-28 | §11.3：统一依赖索引；做法适用性失效 | 各处读时判定；做法靠类型目录哈希 | 阶段 C3 裁决、补齐计划第二节 | 无 |
| B-29 | §21.2、§23：随机状态机用 Hypothesis | 固定种子的随机序列 | 阶段 F 开工裁决 | 无 |
| B-30 | §16.1：领域事件 + 纯 reducer 重建；保留 v2 | 存储层统一记行变化、通用折叠；v2 删 | 阶段 G 开工裁决、偏差裁决 1 | 无（v2 删可由 A 级 #17 覆盖） |
| B-31 | §7.3：做法晋级要离线多实例验证 | 交付成功且根终审通过即晋级 | 补齐计划待定④ | 有（A 级 #5） |
| B-32 | §13：审阅员回复严格格式 | 剥围栏、忽略空的多余字段 | 待定① | 有（A 级 #6） |
| B-33 | 原 TG 计划：老任务迁入执行图（CAPTURED_BASELINE） | 删 | 补齐计划第二节 | 有（A 级 #17 覆盖） |
| B-34 | D1-D3 补遗 D3：延期 continuation、停止闸门、冷恢复 | 整套删（15e56cfb），由执行图收敛代替 | 实施记录 A′-3"偏离裁决" | 无 |
| B-35 | §21.4：需求状态写进 requirements.json | 另建台账 | 补齐计划表二 23 | 无 |
| B-36 | §11.2/§8.2：多组依据任一成立即当前 | 单组依据 | 一致性补改 | 有（A 级 #9） |
| B-37 | §15 长期承诺与周期；§16.3/16.4 迁移快照、归档、删除标记 | 不做 | PLAN-STATUS:476；补齐计划表一 10 | 有 ★（用户 09-30："暂不做"），属"暂不做"，建议在口径里明确算 A 还是 D |
| B-38 | §21.5：F 臂重跑、§21.1 外部评测 | 暂不做，补齐后在产品路径上重建 | 补齐计划表一 20 | 有（用户 10-03），按 D 级处理 |
| B-39 | TG 补全偏离 21～27（放弃只认点击、删需求方接口、写入目标默认、重查事件、错误码只归秩序码、重审放宽、老任务跳过） | 已按偏离实施 | TG 补全方案第 7 节 | 部分有：偏离 25 后半有（用户 09-28），其余无 |
| B-40 | TG 补全偏离 31、32 | 只在单元层测；退回原因口径不同 | TG 补全方案第 7 节 | 无 |
| B-41 | 补齐计划阶段 E：改要求只能增、改、删条目，不能改目标 | 照此实施 | 阶段 E 开工裁决 | 无 |
| B-42 | §7.3：TRIAL_ADMITTED 试用计数随 Mission 持久化 | `note_trial_use` 删除 | TG 补全第二批（补齐计划 3.17 移交） | 无 |

---

## 第四节：A 级排除命中清单

| A 级编号 | 命中的计划条目 | 证明 |
|---|---|---|
| #1 多模型、NanoJev、42 组、金额、跨领域与按步骤选领域、组级管理员、可训练学习 | R20、R46、R47、R53；§10.4；§13 跨域表；§14.1；§17.1；NanoJev 范围变更；R51 的金额部分 | 口径第三节第 1 条原文逐项对得上；Host 不传 routing，没有 SQL/网页/Lean 适配器 |
| #2 模型路由 | R24 | PLAN-STATUS:508"用户决定（2026-09-30 16:58）：模型路由暂不考虑" |
| #3 旧平面模式整条线删 | R21、R22、R54；§18.2 平面文件；§18.5 兼容约束 1～3；任务级 Manager | `删旧平面模式-方案.md` 第二节边界表；提交 2c505d57、2db8df6e、3d218b14、ec6f33fa |
| #4 离线备份与受管恢复删 | §16.4 恢复 manifest（R60 的一部分） | 提交 6cf1643a；补齐计划阶段 A″ |
| #5 做法评测晋级删、交付即晋级 | R07 晋级口径 | 补齐计划待定④（用户 10-02） |
| #6 审阅员回复格式 | §13 回复解码 | `SDK/assurance/checks.py:393` |
| #8 判断交给 LLM 与 HTN 精简四处 | R09；§7.2 程序判定删除 | `HTN-后续-方案.md` 第六节 |
| #9 一致性复核 8 处 | 前提三阶段（§6.6/§24.1-5）；要求类别（§25.1-2）；单组依据（§8.2）；义务死字段（§6.1）；只做 macOS（R49）；窗口对比实验（§12.4）；取消时的未决操作（§25.1-10） | `HTN补齐计划` 第一节、第二节"一致性补改记的偏离"；代码 `grounding.py:480`、`resolution.py:88`、`commit_service.py:1526` |
| #14 发布由系统挂步骤 | D1 物化的发起方 | `SDK/orchestrator/system_operations.py:534` |
| #17 开发期不兼容旧数据 | §16.3 GenesisSnapshot；§20 兼容策略；replay v2 删 | CLAUDE.md 硬约束 |
| #20 判不下来复审再问人 | §25.1-3 | `assurance_review_import.py:324` |

判定为"A 级排除"的共 15 行：§ 表 5 行（§10.4、§13 跨域表、§14.1、§16.3、§17.1）；补遗表 1 行（NanoJev）；R 表 9 行（R20、R21、R22、R24、R46、R47、R49、R53、R54）。

---

## 第五节：D 级待办

| 待办 | 出处 | 本文相关条目 |
|---|---|---|
| §21.5 Grok 配对验收、R52 外部评测、PANDA 已建模样本验证 | 用户 10-03（补齐计划表一 20） | R52、§21.5、§7.4 |
| 审阅员信息缺口 11 条（审阅员不能自己选检查工具、找资料不给文件名等） | 用户 10-02（`审阅员信息缺口-检查记录.md`） | ADR-04、R28 |
| F2 联测待用户定两件：资料换版本的产品入口、主对话列任务工具 | 用户 10-05 | R16、R17、R30 的真机证据都卡在"资料换版本没有入口" |
| Blackboard 批（暂停，未取消） | 口径第三节 D | R33、§12.3 |
| 90 个场景里 45 个"未运行" | 台账 | §23 关闭条件、§24.3、§25.3 |

---

## 第六节：与此前对照文档不一致的地方

| # | 文档与原结论 | 本文结论 | 谁对 |
|---|---|---|---|
| 1 | 10-02 评估第 44 项："恢复锁、只读恢复状态、删除标记、归档……属于 09-30 暂缓" | 用户 09-30 原话只有"P7 迁移快照/归档/删除标记暂不做"，恢复锁和降级恢复没提 | 本文对（C-1） |
| 2 | 10-02 评估第三节把 25 条都标成"有意偏离（用户决定）" | 其中第 3、13、15、22、23 等条，依据是实施记录或独立裁决员（13、22），没有用户原话；R18/R19 用户原话只覆盖"同一步多次尝试择优" | 本文对（B-4、B-16、B-22） |
| 3 | 10-02 评估的多条"未做 / 做了没接上"：整数闸门恒等、观察器为空、ExecutionFeedback 只有合同、2000 条窗口、全业务重放仍是 v2、中途改要求未做、知识库为空、黑板没人用、`strict_taskgraph` 能关 | 都已变化：整数闸门删（B-13）、观察器已注册（`BE/hierarchical.py:24`）、ExecutionFeedback 删、窗口删（a23ad799）、v3 在用、改要求在用、知识进库在用、黑板模块删并改成知识读工具、开关删 | 10-02 已过时 |
| 4 | 10-04 复核第三节：PANDA"没装时报不支持，原计划允许" | 产品从不调用适配器（Host 不传 `planning_backend`），所以根本不会报 UNSUPPORTED；原计划要求"可用则跑、不可用显式 unsupported" | 本文对（判"做了没接上"，验收那一半归 D 级） |
| 5 | 10-04 复核：整数闸门删除"和裁决一致" | 和裁决一致，但违反 ADR-13 第 1、4 条，没有用户原话 | 两者都对，本文补上 B 级定性 |
| 6 | 台账 R29"已接入" | 缺 `validity_interval`、`permitted_uses`、`assurance_level`，这一点没有登记 | 本文对（应为"部分"） |
| 7 | 台账 R38"已接入" | 恢复协议缺失，"旧授权不复活"没有实现证据 | 本文对（应为"部分"） |
| 8 | 台账 R05、R10、R12、R16、R25、R27、R29、R33、R35、R38、R40、R58、R59 共 13 条写"已接入"；R15 写"行为已验证" | 这些条目都缺计划要的字段或分支：做法字段、OR 并存、未决事项、读集缺项、上限不按义务、具名停止、知识三字段、混合检索、收件箱、恢复协议、三轴、Schema 校验、死锁治理、ASSUMPTION 边。这些缺口只是计划自拟或子代理裁决的偏离，有的连登记都没有 | 台账的口径是"计划第二节记过的偏离不拖低状态"；按 00 口径"少一个就算部分"，本文对 |
| 9 | 台账 R52"不做（用户决定）" | 用户原话是"暂不做，HTN 补齐后在产品路径上重建" | 本文对（D 级，算未完成） |
| 10 | 台账 R13、R23、R41、R48"删除（有意偏离）"，读起来像用户决定 | 都是子代理裁决或计划自拟，没有用户原话 | 本文对（B 级） |
| 11 | 台账 R55"已接入" | 这是计划自己定的排除规则，不是功能 | 用词问题，本文判"按计划在用"（保持排除） |
| 12 | 台账 R03、R40 引用崩溃切点 K10 | 台账自己已说明 K10 绑的用例测的是模型调用次数上限，不是换做法时的外部操作 | 台账已如实说明，本文同意 |
| 13 | 10-04 复核结论"除用户已定事项和已登记改动外只剩 8 条 + 1 条待定" | 本文另找到 C-1～C-9 共 9 条无记录偏离；其中 C-4 是复核第 7 处的剩余部分 | 本文对 |
| 14 | 10-02 评估第 9 项：搜索、择优"有意偏离（删除）" | 删除属实，但只有"同一步多次尝试择优"有用户原话 | 本文对（B-4） |
| 15 | 10-02 评估第 36 项："旧 `retrieval.py` 还留着 2000 条窗口，应删" | 已删（a23ad799） | 10-02 已过时 |
| 16 | 10-02 评估把 D3（延后替换、冷恢复）算进"在用"范围 | 已在 15e56cfb 整套删除，只在实施记录里有偏离裁决 | 本文对（B-34） |
