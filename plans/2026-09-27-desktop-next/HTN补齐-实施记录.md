# HTN 补齐 — 实施记录

依据：`HTN补齐计划-2026-10-02.md`（第 3.3 版）。执行会话："TaskGraph与原始Plan的差异"（持有发版与真机锁）。用户 2026-10-02 深夜批准开工："先开始 HTN 补齐计划，做完之后再重新补齐 TaskGraph 部分的计划，并让子代理评估修改达到可以执行以后再处理"。

## 开工前裁决（2026-10-03）

裁决子代理（Opus）定待定②⑥并核对阶段 A 删除清单，记录：`HTN补齐-开工前裁决-2026-10-02.md`，结论已写回计划第二节、第五节与阶段 A 第 1 条。要点：
- **有效性纪元**：用作用域纪元。`bump_epoch` 保留为它唯一的写方，生产写方接在阶段 D 和阶段 E。
- **未归类的 8 条**：都不补，作为有意偏离记录。
- **旧的 2000 条窗口检索**：挪到阶段 A′，和旧执行池一起删。

## 阶段 A　清残留 + 立规矩

### A-1　删老任务迁入执行图（补全方案第 4 批第 3 条）— e053685a

- **删除**：
  - `orchestrator/taskgraph_baseline.py`、`taskgraph_baseline_proof.py`；
  - 迁入证书 `CapturedBaselineCertificate`，以及只为它服务的 `BaselineProofContext`、外部引用核验器（`RefVerifier`）及其在历史存储、离线重建、执行来源中的参数；
  - 修订事件与历史存储里的 `CAPTURED_BASELINE` 分支；
  - 输入绑定"出处可为空"的规则：`AttemptInputBinding.admission_check_id` 改为必填；
  - 离线重建的起点类型只剩"首份计划"。
- **启用执行图**：任务已有计划修订时直接拒绝（`TASKGRAPH_MISSION_ALREADY_PLANNED`），不再迁入。执行图在第一份计划之前绑定，这一点由"新任务建立时写执行图要求"那条路保证。
- **迁移 34**：重写两个守卫触发器（修订记录来源守卫、尝试输入身份守卫），去掉迁入分支，并在库层拒绝写入迁入来源。迁移 25 的 CHECK 原文不动：SQLite 要去掉表上的 CHECK，得重建整张表和挂在它上面的触发器。
- **测试**：`taskgraph_exec/test_captured_baseline_removed.py`（2 条）；另有 4 处库版本号钉住随迁移 34 更新。
- **改坏检验**：去掉"已有计划拒绝"的检查 → 测试失败；去掉触发器里的来源检查 → 测试失败。

### A-2　补两种事件（补全方案第 4 批第 2 条；原 TaskGraph 计划 §10.1）— e053685a

- **两种事件**：
  - `TaskGraphDispatchBound`：在派发事务里写，绑定尝试的输入时写一条，身份 = 尝试编号。
  - `TaskGraphConvergenceAdvanced`：收敛作业状态真正变化时写，身份 = 作业加新行版本；状态没变的重复推进不写（否则每 5 秒一次的唤醒会刷屏）。
- 两种事件都不进重查事件集，用测试钉住，防止自己触发自己。
- **测试**：
  - `taskgraph_exec/test_convergence_advanced_event.py`（2 条）；
  - `test_process_recovery.py` 新增断言：每个绑定的尝试恰有一条"派发已绑定"事件。
- **改坏检验**：去掉"状态没变不写"、去掉收敛事件写入、去掉派发事件写入，三项都被测试抓住。

### A-4　错误码表框架（原 TaskGraph 计划 §12）— e053685a，核验后收窄

- **新增** `contracts/error_table.py`：
  - §12 的九类及其报告先后；
  - 跨边界码全集登记：规划器拒绝码 36 个，执行图对外报的码 7 个（§12 左栏）。内部守卫码以后再归。
- **用途**：
  - 规划器反馈里的码按类别先后排，同一类别内保持发现顺序；
  - 库里存的拒绝码不认识时直接报错，不再改写成"格式错"。
- **核验后收窄**：初版还有两列，"停在哪里"和"算不算格式错"。核验员指出生产代码不读这两列：格式重问实际走的是"回复读不成决定"那条路径，表里再写一份就是同一件事的第二份声明。按"同一件事只留一条路径"删掉这两列；以后哪道秩序要按类型码决定，就在表里加一列并让生产代码去读。
- **测试**：`test_error_table.py`（7 条），覆盖全集登记、未知码拒绝、报告顺序、库里存的未知码。
- **改坏检验**：改回发现顺序、未知码改回"格式错"、漏登记一个码，三项都被测试抓住。

### A-5　全业务重放 v3 骨架与覆盖清单（"G0"）

- **覆盖清单** `observability/business_replay_inventory.json`：库里 109 张表全部归类，逐表列字段。
  - 业务表 86 张：要能由事件重建。
  - 派生表 6 张：执行图钉住投影、计划成员、保证通道依赖索引、摘要。
  - 运行表 9 张：调度游标、派发意图、后续通知队列、有效性待重算、保证通道待办与游标与全局时钟、模型调用令牌、迁移记录。
  - 全局表 7 张：做法表、做法评测、策略五张表。
  - 日志表 1 张：事件表本身。
- **时间字段口径**：以 `_at`、`_at_ms` 结尾的字段只核对与事件序号的先后一致，不按值比较。
- **骨架** `observability/business_replay.py`（`business-replay-v3`）：
  - `check_inventory`：库结构与清单逐表逐字段比对；
  - `coverage_report`：报告一个任务每张业务表的行数，以及哪些表还不能由事件重建（状态标 SKELETON，不重放、不补猜）；
  - 写明 v3 与 v2、与执行图历史重建的关系，并列出 v2 的四个使用方。
- **诊断导出**：Host `diagnostics.py` 新增 `business_replay` 一节。
- **守护测试**：`test_business_replay_inventory.py`（3 条），新表或新字段没归类就失败。
- **改坏检验**：去掉字段比对 → 测试失败。
- **写入口与事件对照**：审计子代理逐表核对后已填好，明细见 `HTN补齐-G0覆盖清单审计.md`。
  - 一共 275 个写入口：Python 函数 128 个，保证通道屏障触发器 147 个。
  - 86 张业务表里，完全覆盖 55 张、部分覆盖 22 张、完全不发事件 9 张。
  - 不写事件的入口逐个记在清单的 `gaps` 字段里；`coverage_report` 把有 `gaps` 的表报为"部分覆盖"。
  - 大块缺口集中在四处：组合审阅、根审阅、操作结果审阅这几条路径；规划决定与修复续作；只发"来源变了"信号的四张表；保证通道只记回执的事实。这些按计划在阶段 B～F 各自接上，阶段 G 收尾。
  - 审计员提出的归类疑问留给阶段 G 定：`commit_receipts` 是否更像第二本日志，`assurance_blob_pins` 和 `workspaces` 是否更像运行表，修复续作表里的租约字段，Host 独写的执行图要求表。

### A-1′　删只有定义没人用的（计划阶段 A 第 1 条；子代理执行，小结 `HTN补齐-阶段A删除-小结.md`）

- **删除**：
  - 对外操作三种状态，连同就绪判断里的"等待未知操作"门（保留 `operation_range_revision`）；前端删一个标签；
  - `ReconciliationResult`；
  - `ExecutionFeedbackV1` 一组；
  - `leaf_decision` 一组，连同 `low_risk`；
  - `achieve_outcome` 一组；
  - `refine()` 的语义判断部分：`refinement.py` 从约 1090 行减到约 260 行，保留 `planning_frontier`、`unknown_predicates`、`evidence_requests` 等 6 样；
  - 调度的解锁价值打分项；
  - `RecordVersion`、`ValidityRevision`；
  - 支持、假设、监督三类边和两个视图。
- **测试**：
  - 保护"或"分支和共享逻辑的用例，改用新增的 `ground_draft` 辅助造草稿，走真实编译路径（`assess_method → ground_method → compile_refinement_bundle`），没有为测试保留 `refine`；
  - 只测被删代码自身的用例删除，逐条理由见小结。
- **编码清单**：只重写了一次（`htn.py` 7 个条目、`task_network.py` 1 个条目）。**此后开发库旧任务的执行图历史读不出。**
- **复核**：
  - `refine` 删除后，燃料不足由计划提交路径兜底，`test_plan_commits.py::test_an_opening_the_parent_cannot_fund_is_refused` 仍在，保护没有丢。
  - `MethodRegistry.note_trial_use` 唯一的调用方是被删的 `refine`，试用次数现在恒为 0。记入阶段 C3，和新晋级路一起处理。
- **定向测试**：SDK 相关测试约 2200 个通过（相关目录、`taskgraph_exec`、引用被改模块的 49 个文件），前端 18 个 vitest 和 typecheck 通过。

### 阶段 A 核验（Opus 只读，只报阻断）

- **阻断 1 项**：Host 诊断导出引用的 SDK 新模块，不在 Host 当前钉住的 SDK 包里。代码本身没错，是提交先后的问题：Host 这处改动必须和新 SDK 发版、Host 钉版一起提交。按此处理。
- **其余 6 个核对点无阻断**：迁移 34 只去掉了迁入分支，产品路径不受"已有计划就拒绝"影响，两种事件的位置正确，码的顺序变化不改变行为，删除没有碰生产在用的东西，守护测试有效。
- **非阻断，已处理**：错误码表里生产不读的两列已删（见 A-4）。
- **非阻断，记录**：
  - 真机跑之前先取消开发库里未结束的旧任务，因为它们的执行图历史已读不出；
  - `test_htn_deployment_wiring.py` 自己复制了一份证据轮的走法，原来就如此，不算这次退化；
  - 试用次数恒为 0，留到阶段 C3 处理。

## 阶段 A′　产品同形测试世界 + 删两条旧路

方案：`HTN补齐-阶段A撇-方案.md` 第 3 版（039f547b）。

### A′-0　准备（2026-10-03）

**清点**（`HTN补齐-阶段A撇-清点.md`）
- 碰到旧路的 SDK 测试有 225 个文件、约 1700 个用例。
- `taskgraph_enabled` 实际调用 48 处。

**一次性探针**（`.local-test-evidence/2026-10-03/waiting-probe.json`，不留成测试）
- 在 Host 产品组装上跑了四个场景：不授权空转、授权后撤销、等待期取消、等待期重启。
- 等待期实际会走到 10 处，其中 1 处是全局扫描。

**用户裁定**：AppWorld 分层臂与单代理对照臂删除、底层零件保留；命令行建任务删除；接缝脚本迁移；原生池拼装搬进 SDK；执行图在建任务时绑定。

**裁决与复审**：`HTN补齐-阶段A撇-裁决.md`。

### A′-1　部署组装搬进 SDK（第 1 步）

- **身份基准**（8b656b2c，搬迁前提交）：用 Host 当时的代码对固定输入拼出全部执行池，覆盖两档尺寸 × 思考/非思考 × 有无向量模型，把配置行、根标记、准入相关字段、授权策略号、所有者合同、调用方回执取出来存成 `pool_identity_baseline.json`。随机的根化身号不算身份，排除在外。
- **SDK 新增 `agent_orchestrator/deployment/`**：
  - `native_pools.py`：原生执行池拼装，`NativePools` 与 `pool_options`；`calibrated` 也搬进来；
  - `duties.py`：`DeploymentDuties`，含自动确认内容完成、自动授权规划、检查策略投影（各范围、根终审、做法计划）；
  - `root.py`：唯一一份要求书 `user_requirements`、根初始化、开工条件与分层安装。
- **SDK 默认要求书**：`assurance_assembly` 的默认构造改调 `user_requirements`，删 `mission_spec_requirements`。只有一条成功条件时，SDK 默认构造的哈希会变；Host 一直显式传入自己的那份，产品不受影响。
- **Host 变薄**：
  - `native_plane.py` 只剩 BGE-M3 向量模型和 `build_native_pools`（把本机资源交给 SDK）；
  - `runtime_profile.py` 只剩 DeepSeek 计数器；
  - `service.py` 只读权限模式，其余交 `DeploymentDuties`（一个服务一份，重建时重新 bind，已做记录跨重建保留）；
  - `hierarchical.py` 只剩桌面规划世界和根的三个名字；
  - `assurance.py` 删掉投影函数。
- **字节钉死**（`test_pool_identity_bytes.py`）：
  - 4 种组合下的执行池身份逐字节等于基准；要求书在 1、2、3 条成功条件下等于搬迁前 Host 的值；
  - 自动确认的命令号、做法计划检查策略的批准回执号也由测试钉住；
  - 改坏检验：改一个 `host-…` 字串、改配置修订号，4 条全部失败。
- **数据目录副本启动检查**（一次性）：
  - 在开发数据目录的 APFS 克隆上，用源码 SDK 启动应用，编排服务正常起来（`orchestration_ready state=available`），已有执行池照常加载，随后删克隆、恢复原目录。
  - 克隆必须放在原路径：保证通道根身份绑在绝对路径上。
- **定向测试**：
  - Host 受影响文件，以及产品同形脚本化通道整圈（13 条）；
  - SDK 保证通道测试目录 206 条，原因是默认要求书变了，这个目录直接受影响。
- **核验**（Opus 只读）：无阻断。非阻断风险：
  - 钉死测试没有直接取出上下文身份旁挂文件、准入指纹，也没有带沙箱执行器的组合。这几项是由已钉住的字段推出来的，核验员也逐行确认没有变化。
  - SDK 里还有第二份要求书 `root_review.root_requirements` 和测试辅助 `decision_loop.auto_grant`，第 2 步删。
  - 思考计数器的创建时机提前了，无害。
  - 读不到权限模式时，每轮会多打一条警告。
- **发布**：SDK opt.136（34168b49），Host 钉版 5869fdd7，已推送。用装好的 opt.136 安装包再跑一遍字节钉死（7 条），全部通过。

### A′-2　产品同形测试世界（第 2 步，进行中；工作分支 htn-a-prime，ed967095）

- **建任务路径并为 SDK 一份**：`deployment/assembly.py` 的 `UserMissionDeployment`，负责安装（分层世界与开工条件、执行图、保证通道、根安装）、建任务、每轮职责。
  - 建任务在同一事务里经门面建任务、初始化根、**绑定执行图**（用户定：建任务时绑定）。
  - Host 改为调用它；删掉 Host 的 `strict_taskgraph` 设置、"要求→等授权→启用"协调器、任务详情里的等待状态。
- **执行图策略不再带授权引用**（`planning_delegation_ref`，没有代码读它），删掉 `_delegation`。
  - 内核字串不升档：库表上的 CHECK 把它钉住了，改字串要重建表；旧绑定读出时按 `TASKGRAPH_POLICY_FIELDS_INVALID` 具名拒绝，已经能说清原因。
- **测试计数器、脚本化回复只留 SDK 一份**：`testing/word_counter.py`、`testing/scripted_replies.py`。Host 的 25 处引用都改为直接引 SDK，Host 原文件删除。
- **产品同形测试世界** `testing/product_world.py`：
  - 部署组装、原生执行池、保证通道、建任务即绑定执行图，都和产品同一份；
  - 替身只有三样：脚本化回复、测试计数器、通用"用户目标"规划世界。
- **代表用例**：
  - `tests/orchestrator/product_world/test_full_circle.py`：整圈；
  - `test_sub_goal.py`：子目标；
  - 对外操作那条由子代理在写。
  - Host 新增 `test_taskgraph_bound_at_creation.py`，删掉只测等待机制的 `test_strict_taskgraph_default.py`。
- **产品缺陷（产品同形世界首局子目标就发现）**：
  - 症状：根做法把一条要求交给子目标、另一条交给收尾步骤，而且所有要求都是 `file:` 时，子目标那一行的完成条件为空，整份计划被拒。
  - 原因：建任务行时，只把链接给普通步骤的要求算作"负责的要求"；子目标落到兜底分支，兜底分支又把已被认领的 `file:` 要求全部排除。
  - 修复：`occurrence_tasks.py` 改为子目标步骤也计入链接给它的要求。
  - 改坏检验：恢复成只算普通步骤，子目标用例失败。
- **脚本化规划器**：子目标还没细化时，系统会发"目标未细化"的修复请求，现在按普通规划处理；中间目标负责的要求从 `criterion_evidence` 读。
