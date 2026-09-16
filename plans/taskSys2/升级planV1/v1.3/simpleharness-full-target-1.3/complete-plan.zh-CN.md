# SimpleHarness 完整目标架构与差距闭合计划

**版本：FULL-TARGET-1.3｜修订日期：2026-09-16（v1.2 → v1.3：并入 TaskGraph 专项附件，见 §0 C18–C24 与 §24）｜核查日期：2026-09-16｜基线：SDK `61a85eb7e8c003fa894497090f5de893419ebbd1` / 源码版本 `0.11.1`**

> 交付目标不是一个新的 MVP，而是：把原始完整设计、已经明确的对话补充要求和最新代码重新统一为一份具有约束力的最终能力规范。实施可以分批，但最终能力不能再因批次划分而消失。
>
> 本文是架构、协议、代码修改与验收规范；不是已经合并的补丁，也不是任意开放任务都能成功或软件零缺陷的承诺。所有新增接口、表、Schema 和测试均标注为拟新增。正式业务与安全验收必须由实现和实际实验提供证据。

## 阅读路线

- §1–4：为什么发生范围漂移、当前确切差距、必须保留的已有能力与设计裁决。
- §5–12：完整的 HTN / 方法 / 世界状态 / AND–OR / 动态修复 / 搜索与资源 / 知识 / Context 设计。
- §13–17：独立验证、跨领域执行、长期任务、事件重建、安全与对外协议。
- §18–21：逐文件实施落点、九个完整工作包、兼容迁移和最终验收。
- 附录：原始 31 章覆盖表、60 项需求追踪、代码与研究来源。

## 0. v1.2 相对 v1.1 的变更

v1.2 不改变最终范围、需求 ID（R01–R60）、验收场景 ID（T001–T090）或工作包终点。它只做三类事：修正 v1.1 评估中确认的缺陷、把实现时会被迫补的定义钉死、补一份可执行的收益与 Grok 验收协议。修订依据：`升级planV1/v1.1/评估-v1.1-与执行步骤-2026-09-16.zh-CN.md` 与用户 2026-09-16 的确认。

| 编号 | 变更 | 落点 |
|---|---|---|
| C1 | 新增 ADR-13：语义 read-set 与现有整数图版本号乐观锁的关系（附加校验，不替换） | §4 ADR-13、§9.4、§18.5 |
| C2 | 三条兼容硬约束写入 P1 门槛：语义版本默认 legacy；allocator 保留旧 READY 入口；旧事件 canonical JSON 字节不变 | §18.5、§19 P1、work-packages.json |
| C3 | 新增 §21.5 收益假设、Grok 验收协议与退出规则 | §21.5 |
| C4 | §18.2 补 commit_service.py、mission_tail_commits.py、role_templates.py、fixtures_provider.py 落点；P1 指名接入的预算路径 | §18.2、§19 P1 |
| C5 | Host 表述改为本机事实（可读、54 个 py 文件 import 编排内核、UI 在 tauri-app/src）；Host 映射成为 P9 前置切片 | §1.3、§3 G13、§17.2、§19 P9 |
| C6 | 试点域定为 code 与 AppWorld；P2 交付种子方法库与最小谓词观察器集 | §7.3、§19 P2 |
| C7 | PANDA 改为"可用则必须跑，不可用显式 unsupported"，不作为 P2 阻塞门槛 | §7.4、§19 P2 |
| C8 | LLM 侧合同补入：method_proposal / plan_revision_proposal 标签块、有界修复、fixture provider 扩展 | §18.2、§18.5、§19 P2 |
| C9 | §21.4 新增规则：某包场景细化先于该包开工 | §21.4 |
| C10 | §23 改为已确认的执行顺序（HTN 优先，按 P 级小片编号） | §23 |
| C11 | 五处定义漏洞以测试钉死：NOT(CONFLICT)/NOT(UNKNOWN) 保守语义；混合世界求值不得用于授权；快照见证与执行前复检不一致的裁决；递归燃料按 obligation 计；方法状态机补 SUSPENDED 边且 P2 只到 TRIAL_ADMITTED | §6.4、§6.6、§7.3 |
| C12 | 不补工时估计；每片记录实际耗时 | §23 |
| C13（审阅后） | ADR-13 判定顺序写进 §9.4 伪代码并同步 §10.4 | §9.4、§10.4 |
| C14（审阅后） | 执行顺序按 P 级编号：P0 → P1.1 → P2.1 → P2.2 → P1.2 → P1.3 → P2.3 → P5.1 → P3.0，使 P2 依赖 P1 成立、P2.3 的预算守恒不变量可判定；P0 含 P1/P2 场景细化 | §19 P2、§23、acceptance-scenarios.json |
| C15（审阅后） | P1.1 补 obligations.py、knowledge/predicates.py 注册表类型、contracts/resolution.py 的 GoalSignature/GoalResolution 类型；统一 ProposedPlanDelta 与 PlanProposal 的含义 | §23、§18.3 |
| C16（审阅后） | §21.5 补统计口径、code 题评分器、费用口径、INCONCLUSIVE 效力与机制触发构造题、F 臂重跑、N7 归因输入 | §21.5 |
| C17（审阅后） | R07 拆分验收：注册/检索/试用在 P2，淘汰边与离线晋级在 P8；标签块在 §18.5 补述 | §7.3、§18.5、requirements.json |
| C18（v1.3） | 新增 §24 附件 TG：TaskGraph 专项实现约定，两份 TG 文档作为规范附件（实现约定级）挂在 `annex/`，受 ADR 约束，不携带自己的编号体系与施工顺序 | §24、annex/ |
| C19（v1.3） | ADR-13 补充：新模式提案不走 allow_rebase 自动重放；整数闸门与 touched-overlap rebase 的代码路径不改 | §4 ADR-13 |
| C20（v1.3） | 前提检查三阶段 SELECT / MAINTAIN / ACCEPT；五类版本轴分离；`MethodInstance.goal_id` 钉死为类型化 TaskRef | §6.4、§6.6 |
| C21（v1.3） | §18.1 新增 graph/task_network.py、graph/eligibility.py、graph/demand.py、graph/projection_validation.py、artifacts/input_bindings.py；§18.2 补 artifacts/versioning.py、artifacts/workspace.py、graph/dependency_checker.py、graph/deduplicator.py、observability/traces.py、observability/evaluation.py；GraphIntegrityError 只在执行/物化路径抛 | §18.1、§18.2 |
| C22（v1.3） | §18.5 第 4 条硬约束：新模式不重定义旧 READY 语义，allocator 的拦截门是 form=compound | §18.5 |
| C23（v1.3） | P2 再切为 P2.1 / P2.1b / P2.1c / P2.2 / P2.2b / P2.3a / P2.3b / P2.3c；P3 预切 P3.1–P3.4；TG 三个接线目标降为终局验收目标 | §19 P2/P3、§23、work-packages.json |
| C24（v1.3） | TG 文档引用的 taskgraph-tests.json（TG01–TG32）、requirement-map.json、source-index.json 本机缺失，标 MISSING_ATTACHMENT；其场景 A/B/C 与七个崩溃切点并入现有 T 编号（tg_annex 字段），不引入第二套编号 | §24、acceptance-scenarios.json |

---

## 1. 先纠正“阶段完成”与“最终目标完成”的混淆

### 1.1 已找到的范围漂移

原始《Agent 编排层完整设计方案》明确要求动态拆解、多个方向并行搜索、跨分支知识复用、独立验证、预算、恢复、可观测以及持续改进。[D1 §2, §6–11, §18–19, §28]

对话后来进一步提出：参数化 Method、通用动态 HTN、AND–OR 关系、ADaPT 式按需递归、依据 Worker 返回进行局部计划修复、MethodContract / ExecutionFeedback / GraphPatch。这些并不是原始 31 章已经写完的实现细节，而是明确讨论过的扩展要求。早期个人助手框架还定义了稳定工作义务、可失效的 Acceptance、持久等待、长期周期和完整事件投影重建。[D2：任务模型、计划调整、验收、长期演化章节；对话补充]

后来的增量计划却写道：

> 不把 MethodContract、AND–OR 图、ADaPT、MCTS 等上一轮讨论的扩展作为必需前置。[D3 §1.1]

“不作为前置”本来可以是实施顺序决定，但后来这些要求没有继续出现在最终必达项和验收表中。于是动态 DAG 的局部修改、固定候选选择和有限回放，逐渐被用来代表更大的最终目标。**这属于需求追踪和完成度表述的偏差，不能归咎于用户没有重复说明，也不能通过再加一个高级术语掩盖。**

本文件把这些要求恢复为独立需求 ID。以后必须同时报告：原始需求覆盖、实现接线状态、行为测试证据、真实模型效果；不得再用某个阶段的通过条数代替完整目标完成度。

### 1.2 依据顺序与证据规则

| 来源 | 在本文件中的作用 |
|---|---|
| 本轮用户要求 | 最终目标不得缩减；需要通用动态 HTN 和架构一致的完整方案 |
| D1 原始 31 章完整设计 | 保留 Mission / Task / Attempt、控制闭环、知识、预算、安全等总体结构 |
| 已明确的对话补充 | BaseAgent 身份、短期记忆、Workflow 在工具层、HTN 与独立 Verifier 等 |
| D2 早期个人助手完整框架 | 恢复稳定义务、要求版本、验收有效性、长期承诺与事件重建；与 D1 冲突处通过 §4 明确裁决 |
| T 理论资料包 | 解释搜索、多样性、知识复用、稳定性等目标；不是实现证明 |
| D3–D6 后续工程计划 | 说明为什么发生收缩及哪些实现可复用；不能自动覆盖最终目标 |
| S01–S24 固定源码 | 说明本轮确实看到了什么，不将注释、目录名或接口定义当成完整接线证据 |
| W01–W15 外部一手资料 | 为技术方案提供依据；论文效果和形式化保证不得直接转移到本项目 |

冲突不能靠一句“兼容所有历史讨论”糊过去。§4 的裁决是本次提出的新规范，必须写入项目 ADR，不能悄悄变成旧记录的新含义。

### 1.3 核查边界

- SDK 固定到上面的 commit；源码版本为 0.11.1。[S01,S02]
- Host `simple_harness` 在 v1.1 编写时远端不可访问；v1.2 已在本机核对：仓库可读（`d107ba10`），54 个 Python 文件直接 import `agent_orchestrator`，后端装配在 `backend/deskpet/orchestration/{service,projection,local_capacity}.py`，UI 在 `tauri-app/src`（`stores/missionsStore.ts`、`views/MissionsView.tsx` 等）。Host 不再是未知量；P9 的 Host 映射在开工时按真实文件做，见 §19 P9。
- Service 取得 main ref `47f372adc641d8d3516599dd21cb94cf5955d6a7`；这不证明 App 实际使用该版本。
- 本轮读取了任务合同、改图、规划、分配、候选、知识、检索、摘要、验证、主运行入口、回放、DDL 和学习规则等关键路径；不是逐行全库安全审计。
- 没有运行项目测试、mypy、真实模型或原生 UI；仓库里的 PASS 仅按其记录范围引用。
- 没有搜索到某个名字不是缺失的充分证据。本文的 HTN 差距依据实际任务合同和主调用链没有表达相应语义，而不是仅靠搜索 `HTN` 是否命中。

## 2. 当前代码事实：已经实现的不能被抹掉

当前 HANDOFF 明确写了 `46 SOURCE PASS / 0 OPEN / 2 user-deferred packaging criteria`，同时明确没有宣称整个更大设计完成。[S24]

已有基础包括：

1. BaseAgent 的稳定实例、有限 AgentTurn、短期 Journal / 检索和执行回执；例子与源代码不能替代本轮运行证明。[S19,S20,S21]
2. LLM 初始 Task DAG 提案、严格解析、依赖检查、动态 TaskGraphChange、正式 Commit、预算和版本控制。[S03–S05,S21]
3. 独立 Critic、实际代码检查、来源证据、候选选择、综合和失败片段复用等限定路径。[S07,S11,S18,S21]
4. 新代码领域已使用 `scoped-observation-v2`：自然语言 Claim 不会仅因引用测试就直接成为 VERIFIED；真实来源回执解析也已补强。旧宽松逻辑仍在，是历史兼容，不应误写为当前默认策略。[S11,S17]
5. `handler_for()` 已替代旧的“非代码都算文档”路由；已有 AppWorld、AgentDojo、ARE 领域配置。配置存在不等于全部官方基准已完整运行，但不能再说这些入口完全没有。[S17,S18]
6. Agent 短期检索已有 exact / words / trigram / vector / RRF，不是全系统都没有向量检索。编排 Blackboard 是另一层。[S19]
7. 来源依赖已经有版本传播、环检测、共享菱形依赖和当前性检查；缺的是推广为任意事实、方法和验收的统一有效性机制，不是“完全没有失效传播”。[S13]

**因此：不重做 BaseAgent，不更名替换 Mission，不增加第二套预算账本，不把项目整体迁移到 TypeScript，不重新发明 Provider / Effect 执行内核。**

## 3. 与最终设想的主要差距

以下是架构能力核查，不是按行数计算的百分比。

| # | 最终能力 | 当前实际边界 | 需要补齐的语义 |
|---|---|---|---|
| G01 | 通用层次化任务分解 | 初始 `TaskGraphProposal` 主要是 `tasks[]` 及依赖；改图已有 `parent_task_ids`，但无 Method 实例语义 [S04,S05] | compound/primitive、参数化 Method、适用前提、细化证据 |
| G02 | 替代方法与共同必要步骤 | 当前候选选择以同 Task 的候选为中心，最多 3 候选、1 次综合 [S07] | AND 必需子成果、OR 替代方法、当前被采用的满足关系 |
| G03 | 语义级局部修复 | 可加任务、替换、改依赖、改角色；新图检查可靠，但不是完整 HTN 方法修复 [S05,S21] | Method 失效、最小必要影响闭包、计划读集、共享目标持有关系 |
| G04 | 共享子目标与预算继承 | DAG 共享依赖已有；工作身份主要仍是 Task / Attempt [S04,S16,S23] | 稳定 Obligation、共享结果适用证明、一次付费和责任不丢 |
| G05 | 通用搜索与分层管理 | 有 FIRST/COMPARE、管理调用、固定分配启发式 [S07,S08,S21] | 可恢复 plan-space frontier、细化/取证/执行动作竞争、多级 scoped Manager |
| G06 | 条件化知识与验收有效性 | 有 Claim/Knowledge/来源血缘及失效诊断 [S11–S13] | 条件谓词、多份理由、反证、知识→方法→产物→Acceptance 的完整依赖 |
| G07 | 全历史动态 Context | Agent 混合检索候选局限于最新 `search_window=2000` 条；旧数据需分页回读 [S19] | 对所有保留历史进行索引候选查找，候选数量有界但时间范围不被默默截断 |
| G08 | 高质量共享 Context | Blackboard 仍词面重叠＋规则；摘要为 120/200 字符截断投影 [S09,S10] | 混合召回、相关条件、分层语义摘要、输入覆盖与效果评测 |
| G09 | 通用跨领域验收 | 新领域 handler 已有；`formal_check` 仍明确未部署 [S18] | Task 级领域组合、真正形式化证据、独立语义审阅与确定性检查一致 |
| G10 | 完整业务事件重建 | Replay 只覆盖选定字段；预算/intent/租约等明确排除，图变更不投影完整结构 [S15] | 重建完整业务事实；恢复新租约和重新授权，不能误复活旧执行 |
| G11 | 长期个人承诺 | 未在已读 Mission 入口/核心模型确认完整 Commitment / Cycle / durable wake 语义 [S21–S23] | 长期义务、有限周期、时区、错过触发、用户要求变化、通知闭环 |
| G12 | 学习型分配与路由 | `rules-v1` 明确是启发式规则改进，不是训练模型 [S14] | 真实数据、可训练策略、校准、离线准入、方法库进化 |
| G13 | Host 全功能表达 | Host 源码本机可读（v1.2 核对）；现有 UI 只表达 Task DAG，无方法/目标/失效语义 | 实际 UI/协议映射与验收，按真实文件路径做，不再标为未知 |

这些差距中，G01–G06 是核心语义升级，不是“把参数调好一点”。G07–G12 则使它能长期使用、真正理解过去的工作并在更广任务中执行。修了几个 bug 不等于补齐了这些能力。

---

## 4. 本次必须冻结的架构裁决（拟议 ADR）

### ADR-01：保留现有实体，增加缺少的语义

`Mission` 继续表示一项可结束的用户任务；`Task` 是工作单元；`Attempt` 是内容尝试；`BaseAgent` 是逻辑执行者；`AgentTurn` 是其一次有界处理。

新增 `Obligation`、`MethodContract`、`MethodInstance`、`PlanRevision`、`GoalResolution`、`Fact/Justification`、`Commitment/ExecutionCycle`。这些不是替代现有 Mission 的另一套 TaskProgram，而是明确此前藏在字符串和约定里的语义。

### ADR-02：长期主 Agent 与临时执行并不冲突

D1 的“Agent 最好可替换”指执行占用不能承担唯一事实存储；对话确认的 BaseAgent 稳定身份指逻辑实例。采用稳定 BaseAgent＋有限 AgentTurn＋可替换物理执行者。主 Agent 是单一用户交互入口；有需要时创建多个 scoped Manager/Worker/Verifier，逻辑数量可扩展，实际模型/工具并发始终有界。

### ADR-03：一份规范网络，四类关系，唯一 Commit

任务细化关系、执行依赖、证据支持和 Agent 监督是不同关系。它们使用统一身份与版本绑定，并由同一逻辑 Commit Service 改变正式状态；只是不同查询投影，不是四套可以互相覆盖的权威图。

### ADR-04：独立 Verifier 是语义裁判，程序守住协议与明确检查

保留 D1 分层检查，也保留 D2/对话中“开放内容由独立 Verifier 判断”。统一为：

- 程序校验结构、身份、预算、权限、版本、证据回执与明确约束，不依赖 LLM 放行。
- 独立 Verifier 决定开放语义是否满足原始要求，能主动选择获准的检查工具。
- 用户明确要求的程序测试、数学内核证明、外部状态确认是必需证据，失败不能被 Verifier 一句 PASS 覆盖。
- 领域检查器是取证/核验能力，不要求在建库前为每种未知业务手写一个“万能内容 Verifier”。

该解释是本次裁决；不能假装两个旧方案一直没有差异。新语义下，开放内容的正式接受必须有独立语义Review；程序可以在格式/权限/必需检查明确失败时直接拒绝，不必为已确定无效的结果再花费一次LLM调用。

### ADR-05：历史完成不可改，当前适用性可失效

旧 Task 的 COMPLETED 与过去 Review 是历史事实。新需求或依据变化时，更新 `AcceptanceValidity` / `GoalResolutionValidity`，必要时创建后继 Task/Attempt，而不是把历史 Task 改回 ACTIVE。UI 同时显示“当时完成”和“当前依据已过期”。这解决 D1 终态不回退与 D2 接受后可失效之间的冲突。

### ADR-06：全部业务事实可重建，不声称重建模型内部状态

新模式事件必须能重建正式任务网络、义务、验收、预算承诺与结算、方法采用关系、定时义务和动作事实。Token 生成现场、正在运行的进程和活租约必须重新核对；不得从历史事件复活权限。完整恢复还需要 Artifact/CAS 与执行账本。

### ADR-07：未知前提不等于假，也不等于真

开放世界使用 TRUE / FALSE / UNKNOWN / CONFLICT 的证据结果，另带 CURRENT / STALE / REVOKED 有效性。未能找到记录不能推导不存在。推测只用于获准探索，不可授权真实高风险行为。

### ADR-08：完整设计不等于无界执行

取消固定“所有场景最多 32 节点/6 层/3 候选/1 次综合”的通用语义限制，替换为按部署和任务版本化的容量、预算与搜索策略。保留硬资源上限、单批变更上限和递归燃料；超限报告 `BOUND_REACHED`，不声称任务无解，也不自动追加无限资源。

### ADR-09：保持 Python/SQLite，协议允许替换实现

核心继续 Python，关系数据库仍是现有 SQLite。重计算、求解器和模型在事务外。PostgreSQL 可作为需要多主机控制服务时的后端，不是完成 HTN 的前提。TS 仅处理 Host 对外协议、UI 和确有需要的工具适配；不复制业务状态机。

### ADR-10：Workflow 在工具层，用户长期 Memory 暂不接入

Method 描述分解知识；Workflow 是某个原子 Task 可以调用的获准工具过程，两者不与 Agent 实例混同。Agent 短期历史、Mission Blackboard、方法库不是用户 Memory；本轮不自动调用用户记忆 SDK。

### ADR-11：研究方案是参考，不是未经证明的系统保证

采用 HTN/HDDL、部分序规划、执行监控、局部修复和有证据的评测。ChatHTN、ADaPT、RAE/UPOM、AdaPlan-H 提供方法依据，但不会使未建模的真实世界任务自动获得形式化完备性。[W01–W09]

### ADR-12：最终范围只可显式变更

每项必需需求都必须有代码落点、完整行为测试和证据。接口存在、记录 PASS、测试被跳过、工具未安装都不能算完成。改变必需需求必须形成用户批准的范围变更，不能通过“先做基础版”从总表移除。

### ADR-13：语义 read-set 是现有整数图版本号之上的附加校验，不替换它

现状：`orchestrator/commit_service.py` 的图变更提交以单个整数 `base_graph_version` 做乐观并发控制，并带 `allow_rebase` 重放路径（同文件 1284 行附近）。v1.1 在 §9.4、§10.4、§18.4 要求多键语义 read-set，但没有说明两者关系。

裁决：

1. 整数图版本号继续是所有 Mission（旧模式与新模式）的粗粒度并发闸门，语义不变，`allow_rebase` 路径不改。
2. 新模式提案额外携带语义 read-set（要求版本、Goal/MethodInstance 修订、观察版本、Acceptance 修订、manager epoch、预算授权修订）。提交时先过整数闸门，再逐项核对 read-set；任一项过期即拒绝，不做自动 rebase。
3. read-set 只在 `hierarchical` 语义版本的 Mission 上生效；旧 Mission 的提交路径不读、不写 `plan_read_sets`。
4. 是否在后续版本用 read-set 取代整数闸门，由 P3 完成后的 ADR 决定；本版不预占。

这条裁决在 P3.0 用可丢弃 spike 复现 rebase 路径后定稿；spike 不进入生产代码。

v1.3 补充（C19）：现有 `_commit_graph_change` 除整数闸门外还有按 touched/affected_task_ids 重叠判断的安全 rebase（commit_service.py 1285–1300 行）。该代码路径对旧模式原样保留；新模式（hierarchical）的 PlanRevision 提案不走 allow_rebase 自动重放，read-set 任一项过期即拒绝，由提案方在新快照上重新编译。附件 TG（§24）中「touched-task rebase 保留在 legacy」的表述按本条解释，不得读成删除该路径。

## 5. 完整目标架构

```text
用户 / 单主 Agent / Host UI
              │ 版本化命令、要求、授权
              ▼
Mission / 长期 Commitment 与 ExecutionCycle
              │
              ▼
唯一 Orchestrator + Commit Service
     │                 │                       │
     ▼                 ▼                       ▼
HTN Planner       Manager / Search         Allocator / Scheduler
Method 检索       观察/取证/局部修复         角色/模型/配额/物理槽位
Method 生成       替代路线/剪枝/合成        非公平饥饿防护/验证尾部
     │                 │                       │
     └─────────────────┼───────────────────────┘
                       ▼
          TaskNetwork / MethodInstances / PlanRevision
          细化结构 + ORDER/DATA + 条件依据 + GoalResolution
                       │
             ContextComposer / ScopePolicy
        ┌──────────────┼─────────────────┐
        ▼              ▼                 ▼
  BaseAgent A      BaseAgent B       独立 Verifier
  独立 Journal     独立 Journal      审阅指定产物与要求
        │              │                 │
        └──────────────┼─────────────────┘
                       ▼
          现有 Provider / Tool / Effect / Budget Guard
                       ▼
       代码、文件、浏览器、SQL、API、形式化/实验工具

正式事实：Event + 同步 Current State + Receipts + Outbox
证据字节：Artifact/CAS + 输入/检查/交付 manifest
派生使用：Blackboard 搜索、分层摘要、UI、统计、训练数据
```

所有写入型组件必须有唯一所有者：HTN 可以产提案，求解器可以产计划，Verifier 可以产审阅；它们都不能直接写 Task/Knowledge/Budget 表。现有 execution.db 继续拥有实际模型和工具调用事实；orchestrator.db 拥有业务计划与接受事实。

## 6. 一个目标到底怎样表示？

### 6.1 稳定工作义务 Obligation

`Obligation` 表示需要履行的责任，而不是某次拆出来的节点名称。字段至少包括：

| 字段 | 意义 |
|---|---|
| obligation_id / mission_id | 稳定身份 |
| requirement_refs | 来自哪些当前用户要求 |
| goal_signature / parameters | 类型化目标与参数 |
| scope / authority_ref | 允许工作的对象范围，不是自授权凭证 |
| requiredness | 必需、获准可选，或某个方法的条件性需求 |
| budget_lineage_ref | 费用/重试从哪里继承 |
| satisfaction_policy | 满足该责任需要哪些证据与组合条件 |
| lifecycle / resolution_ref | 未满足、满足、取消或被授权替代；当前有效 Resolution |

拆任务、换方法、换 Agent 不自动创建一份新的重试额度。语义上确实新增的责任由显式命令创建，并记录与父责任的关系。

共享子目标可以被不同 MethodInstance 引用，但默认有一个明确 funding owner；不同消费者承担费用比例是账本分摊视图，不是每人重新预留全价。删除一个消费者只减少一份需求引用，不能取消其他消费者需要的共享工作。

**执行依赖是DAG，不表示预算账户也要变成多父DAG。**预算账户保留唯一归属树；其他分支共担成本通过有回执的额度转移或归因视图表达。额度授权、实际调用预留、未转出的受保护尾额与已消费账是不同状态，同一笔额度不能被父子表重复计入消费。所有转移检查原账户链和未决用量；计划退出不自动释放UNKNOWN对应预留。

### 6.2 Task 的两个独立分类

保留 `kind=work/conflict/synthesis` 等用途；新增 `form=compound/primitive` 描述是否还要分解。这两个轴不能复用同一个字段。

- **compound**：通过 MethodInstance 细化，不直接进入普通 Worker 队列；它本身可以有分析/分解 AgentTurn，但不是已经完成业务执行。
- **primitive**：在当前能力、输入和预算下，可由一个 Worker 做一次有界尝试；内部可以有多轮模型和工具调用。
- 叶子是相对于当前执行能力定义的，不是永远固定。若尝试揭示复杂性，它可通过受控细化生成后继结构。

### 6.3 MethodContract：可复用的“怎样完成目标”

方法定义是不可变、版本化数据，不是一段拥有任意 Python 执行权的代码。完整字段：

```text
method_id / version / content_hash
任务类型与参数 Schema 引用
输出 Schema 引用
类型化适用前提、允许探索的假设
步骤模板（目标类型、参数绑定、compound/primitive、能力要求）
部分序 ORDER 约束
数据来源引用（从步骤输出绑定到后续参数，编译产生 DATA 边）
资源冲突与预期作用模型
父要求→子标准覆盖映射
最终组合/集成任务与整体判据
历史来源、适用范围、失败与过期条件
注册状态及准入回执（由系统填写，模型不能自填 ADMITTED）
```

Method 的预期作用只是规划模型。没有实际观察或验收，不能把 `expected_effect` 写进 FactStore 当成已经发生。

### 6.4 MethodInstance 与计划身份

方法复用时生成实例：`instance_id + goal_id + method_ref + grounded_parameters + world_snapshot + precondition_witnesses + assumption_refs + child_bindings + plan_revision`。

允许Planner提出新的**复合GoalSignature**（参数/产物Schema、目标含义、覆盖判据），经类型与独立语义检查后仅在获准scope内使用。未知目标不必已经有预写业务模板；但新的复合签名不创造新的底层工具/Operator权限。原子Operator必须真实已注册且可执行；自然语言目标可由通用、受控的 `achieve_outcome` 原子能力处理，仍需明确输入输出与独立验收，不能把这一路径当作规避必要HTN分解的万能捷径。

方法本身可以递归；有限实例展开应形成无环执行依赖。每次展开记录参数和递归燃料，禁止无限地把同一未变化目标重新展开。不能通过提升 hardcoded depth 把逻辑不终止伪装成完整 HTN。

**递归燃料的计量口径（v1.2 钉死）**：燃料按 Obligation 计，不按"目标签名 + 参数"计；同一 Obligation 下任何方法实例的展开都消耗同一份燃料，改参数、换方法名、换 Agent 不重置。燃料耗尽的终态是 `BOUND_REACHED`，报告已展开结构和未决义务，不是 `UNSOLVABLE`。通用原子能力 `achieve_outcome` 不能在燃料耗尽后作为逃逸口自动接管未展开的复合目标；它只能由 Planner 在燃料未耗尽时显式选择，并同样受独立验收约束。以上以 `test_htn_recursion_fuel` 测试钉死（P1.1）。

**`MethodInstance.goal_id` 的含义（v1.3 钉死）**：它是被细化的 compound Task 的类型化引用（TaskRef），不是 ObligationRef，也不是独立 GoalNode；Obligation 通过 `obligation_id` 单独引用。名义类型在 P1.1 的 codec 红测试中锁定，字符串前缀不能换取授权。

**五类版本轴（v1.3，来自附件 TG）**：`contract_revision/hash`（目标、成功条件、输入合同变化）、`record_version`（原子状态 CAS）、`dispatch_generation`（执行权或输入被替代/取消）、`plan_revision`（采用方法、成员、ORDER/DATA 变化）、`validity_revision`（观察、支持、验收的当前可用性变化）。心跳、UI 位置、无关分支进展不得推动任何一轴。

### 6.5 四类关系的唯一含义

| 关系 | 用途 | 不能被误用成 |
|---|---|---|
| refinement / satisfies | 子任务如何组成某一方法及父目标 | 普通先后依赖 |
| ORDER | 某项工作必须先发生 | 自动传播内容失效 |
| DATA | 消费指定输出、版本和 Acceptance | 仅仅“这两个任务相关” |
| ASSUMPTION / JUSTIFICATION | 方法或判断依赖哪些当前证据 | 执行许可、绝对事实 |
| supervision | 主/组 Manager 与执行者的协调范围 | 自动继承全部权限和 Context |

“多个图”是这些关系的查询视图，不是相互竞争的状态库。

### 6.6 前提表达式与并发作用的执行约定

Predicate由可信注册表提供类型/观察能力/开放或封闭世界解释。解释器只接受结构化AST，不执行模型传入的代码。单个predicate的有效证据有冲突时返回CONFLICT，缺证据或证据已过期返回UNKNOWN；只有注册域明确提供完整封闭世界快照时，才允许按其规则由缺失推导FALSE。

组合策略固定版本：NOT交换TRUE/FALSE，保留UNKNOWN/CONFLICT；ALL遇到FALSE为FALSE，否则依次考虑CONFLICT、UNKNOWN、TRUE；ANY遇到TRUE为TRUE，否则依次考虑CONFLICT、UNKNOWN、FALSE。空ALL为TRUE、空ANY为FALSE，但不能借空表达式绕过系统另外强制的权限/风险/根要求检查。这是本系统的保守操作语义，不宣称等于所有四值逻辑体系。

PrimitiveOperator还需声明资源读写集合、外部副作用类型、可核对方式及输入版本条件。两个无Task依赖的动作并不因此必可并行：同对象写写/读写冲突需要锁/排程/原子接口，观察不足时保守串行。执行前重新检查真实对象ETag/版本和权限，计划时快照不等于执行时事实。

**v1.2 钉死的三条求值规则**（以真值表测试锁定，实现者不得"顺手修正"）：

1. `NOT(CONFLICT) = CONFLICT`、`NOT(UNKNOWN) = UNKNOWN`。由此 `ALL(p, NOT p)` 在 p 为 UNKNOWN 或 CONFLICT 时不为 FALSE。这是刻意的保守语义。
2. 封闭域由缺失推出的 FALSE 与开放域的 UNKNOWN 混合组合后，结果无论为何值都不得用于授权判定或高风险动作前提；授权判定只接受全部由封闭域或有效观察支持的 TRUE。
3. MethodInstance 上快照的 `precondition_witnesses` 与执行前复检结果不一致时，裁决为"该方法实例失效"，进入 §9.1 决策表的"方法前提被推翻"分支；不记为 CONFLICT，也不直接触发整体重规划。

**前提检查阶段（v1.3，来自附件 TG §9）**：每条前提声明检查阶段之一：`SELECT/START`（选用或进入该步骤时成立）、`MAINTAIN`（执行范围内必须维持）、`ACCEPT`（接受结果时仍需成立）。方法消费某项资源后 START 前提不再为真，不自动使已完成结果失效；MAINTAIN 被破坏需中止或核对；ACCEPT 失败阻止当前 GoalResolution。每个前提的支持见证与检查点持久保存。未声明阶段的前提默认 SELECT，并在 ACCEPT 时再检查一次。

## 7. 通用动态 HTN 的完整算法

### 7.1 什么才算实现，而不是换个 Prompt 名称

必须同时支持：参数化目标和方法；不依赖具体任务名字的实例化；适用条件与未知状态；部分序子任务；递归细化；可替代方法；共享子目标；执行反馈后的局部修复；最终组合验收；持久计划与恢复。[W01,W04–W06]

只返回 `tasks:[...]`、有一层 parent 字段或给节点加 `htn=true`，均不满足本要求。

### 7.2 推进策略

```text
读取当前有效目标与要求
    ↓
检查已有效满足的 Resolution
    ├── 可复用 → 绑定引用，不重做
    └── 未满足
           ↓
查询获准 Method 库 / 原子能力
           ├── 前提已证真 → 形成一个或多个候选 MethodInstance
           ├── 前提未知 → 取证任务或受限假设探索
           ├── 前提已证假/冲突 → 拒绝该方法或进入仲裁
           └── 没有适用方法 → MethodSynthesizer 提出候选方法
                                  ↓
                         结构/类型/安全/语义检查
                                  ↓
选择本轮可展开的目标与方法
    ↓
局部展开，构造计划变更与覆盖证据
    ↓
唯一 Commit 原子接受
    ↓
Scheduler 派发当前 READY 原子 Task
    ↓
WorkerFeedback / Verifier / 外部事件
    ↓
更新事实和方法适用性，继续或局部修复
```

能在有限尝试内完成且风险受控的任务可以直接执行；明显多部分任务先细化；高风险不可逆动作不能通过“先试一次”决定是否需要规划。这是采用 ADaPT 思想后的安全执行约束，不是论文原样实现。[W04]

### 7.3 新方法从哪里来？

采用四种来源，但都经过同一注册协议：人工/工具作者提供；已验证历史抽象；检索已有 Method；LLM 针对缺失方法提出新候选。

新方法依次经过：

1. JSON/类型/引用/范围检查；禁止任意 eval、网络路径、SQL 片段成为条件解释器。
2. 对已有 Predicate/Operator 注册表进行类型检查；缺少真实能力时明确不可执行。
3. 结构检查：有界展开、部分序无环、数据端口可用、根要求覆盖完整。
4. 独立规划审阅：子成果是否足够达到父要求，是否隐藏改变范围、漏了集成或真实动作。
5. 已建模域可做 HDDL 编译与求解/计划校验；开放域使用明确的语义审阅与实际执行证据，不附加“形式化证明通过”标签。
6. 只对当前 Mission 获准试用，或者经离线多实例验证后晋级方法库；成功一次不自动升级成全局通用方法。

方法状态采用 `DRAFT → STRUCTURALLY_VALID → TRIAL_ADMITTED → EVALUATED → ADMITTED`，也可 REJECTED/RETIRED。状态由 registry service 写入，Planner 不得自行晋级。

v1.2 补两条边并限定阶段：已 `ADMITTED` 的方法在前提被反证或产生反例后转 `SUSPENDED`，保留历史实例，不再被新的方法检索命中；`SUSPENDED` 可经审阅回到 `ADMITTED` 或转 `RETIRED`。`TRIAL_ADMITTED` 的作用域是当前 Mission，试用计数随 Mission 持久化，重启和 PlanRevision 不重置。P2 只实现到 `TRIAL_ADMITTED`；`EVALUATED → ADMITTED` 的离线晋级在 P8 交付，P2 阶段该路径显式返回 `PROMOTION_NOT_AVAILABLE`。相应地，R07「生成、验证、注册、检索和淘汰闭环」拆为两段验收：生成/验证/注册/检索/试用（T007 前半、T061）在 P2；`SUSPENDED`/`RETIRED` 淘汰边与离线晋级（T088）在 P8。requirements.json 的 R07 保留 P2 归属并加注。

**试点域与种子方法库（v1.2）**：通用性验收的两个试点域固定为 `code` 与 `appworld`，两者环境都在本机可用。P2 交付一份种子方法库：每域至少 3 个 MethodContract（含一个递归方法、一对 OR 替代方法）、至少 5 个谓词观察器（只读取证，不改状态）。种子方法由人工编写并走 §7.3 同一注册协议；它们是验收夹具，不是全局方法库。谓词观察器的最小集从 P6 前移到 P2，P6 再扩展到 SQL/浏览器/形式化。

**完整目标中必须实现方法泛化和学习产物，不止保存“上次同一句话的答案”。**至少要求变量化、常量与前提提取、负例测试、版本、效果统计、可退役和部署审批。[W07,W09]

### 7.4 精确建模域：PANDA / HDDL

使用独立适配器调用 PANDA 的 parser/grounder/solver/verifier，保存模型、problem、分解见证、动作计划和二进制版本 hash。HDDL 是表达层次任务的方法，不把普通自然语言任务强行说成已经完整形式化。[W01–W03]

选用的后端必须真实支持声明的部分序能力；不把任意部分序偷偷线性化，再给出“等价”的证明。未支持数值、时间、不确定性或动作效果子集时拒绝导出，并返回不支持的语义项。

求解器 `UNSOLVABLE` 只在对应问题/模型/求解模式确实支持该结论时使用；超时、预算结束、模型不完整分别返回，不能共同解释成现实目标不可能。

**PANDA 的门槛地位（v1.2）**：PANDA 是外部二进制。适配器在环境可用时必须对已建模样本运行 parser/grounder/verifier 并保存见证；环境不可用时返回显式 `SOLVER_UNAVAILABLE`，验收记录为 UNSUPPORTED 而不是 PASS，也不阻塞 P2 的其他门槛。P2 的 done_gate 因此写为"可用则必须通过，不可用则显式 unsupported"。

### 7.5 搜索状态与业务状态分开

一个 `SearchNode` 表示候选计划的选择状态，不等于现实执行的 Task。不同搜索节点可以共享一个已经确认的业务成果；模拟中的动作结果不能写入真实业务事实。

保存每个搜索节点的父候选、未细化目标集合、方法选择、上下界或评分、真实证据引用、已花搜索预算与排序版本。重新启动可以恢复 frontier，不需要 LLM 从聊天里重建。

## 8. AND–OR、组合验收和共享子目标

### 8.1 满足关系

对同一目标 G，方法 M1 与 M2 可以是替代方案：

```text
G 满足
    = 至少存在一个当前被接受且有效的方法实例 M
      AND M 的整体组合条件满足
      AND G 的必需要求被独立验收
```

方法内的 AND 表示所有必需子义务都满足；它不要求串行。ORDER/DATA 决定执行先后。

一个候选方法全部叶子通过，并不自动满足父目标。如果用户要求“实际发送报告”，只生成报告的分解缺少发送与回执，必须被计划审阅或最终验收拒绝。

### 8.2 GoalResolution（拟新增）

系统提交的 Resolution 至少绑定：目标/Obligation、要求版本、Task 合同版本、MethodInstance、子 Acceptance 集合、输入与产物 hash、整体标准结果、独立 Reviewer、未决事项、当前有效性。

`ACCEPT` 与 `GoalResolution` 是两个动作：Verifier 提交审阅，Commit 复核其在当前世界与计划中是否适用，再生成 Resolution。

对多份有效支持，只要一份足以支持结论且不存在未解决的反证冲突，仍可保持有效。所有理由失效才失效其结论。不能仅按“最早引用的来源撤回了”机械否定仍有其他完整支持的结论。

### 8.3 共享目标去重

候选语义相似只能产生合并建议。自动共享需要匹配：类型化参数、对象 scope、输入版本、要求/证明标准、授权边界、时效和期望副作用身份。

重复读取同一公开文件可以共享；两个“向不同人发送同一报告”不是同一操作；相同收件人但不同用户意图也不自动合并。

共享 Goal 有显式消费者集合；最后一个消费者退出且没有独立责任、未决动作或保留要求时，才能收敛可取消工作。

**任务出现位置（task occurrence）仍保留独立身份。**跨方法共用一份执行/证据是本运行时的复用扩展，不能假定标准HTN允许随意删除重复出现的原子动作。只有方法明确允许 `reuse` 且满足参数、状态、时效和副作用条件，才能让多个出现位置绑定同一份结果；否则分别执行。导出HDDL时要用模型中明确的“已有结果可用”方法/Operator表达复用，并保留出现位置到分解见证的映射；不得把缩短后的执行列表伪装成原标准任务网络的有效计划。

## 9. Worker 返回以后怎样动态改树

### 9.1 ExecutionFeedback 合同

固定字段包括：`task_id / attempt_id / contract_revision / input_manifest_hash / outcome / observations / diagnosis / evidence_refs / artifact_refs / proposed_refinements / unresolved_operations / cost_receipt_refs`。

Observed 是工具或环境观察；Diagnosis 是解释；Proposed Refinement 是建议。LLM 自报费用不进入预算事实；实际费用来自既有账本。

| 诊断 | 计划动作 |
|---|---|
| 输入缺失或前提未知 | 先取证、澄清或请求合法输入 |
| 内容缺陷 | 原义务的新内容尝试；保留具体缺陷 |
| 粒度过大 | 为原义务选用更细方法 |
| 方法前提被推翻 | 暂停该 MethodInstance，选择替代方法 |
| 基础设施暂时失败 | 原阶段有界恢复，不直接改方法 |
| 缺权限 | 等授权或改变获准范围，不通过拆小绕过 |
| 外部结果 UNKNOWN | 优先核对同一个 Operation |
| 重复无进展 | Failure Analyst、换策略或停止；计数不因换名重置 |

### 9.2 PlanRevisionProposal

提案必须包含：触发事实、语义读集、基于的计划版本、拟采用/退出的 MethodInstance、修改的 Task/输入/关系、共享目标影响、要求覆盖、预算影响、在途工作处理、理由与未确定项。

系统计算影响范围，而不是相信模型自报“只有一个节点”。

### 9.3 影响分析

- 用户要求变化：命中相关义务与验收，再沿实际使用关系传播。
- 数据输入版本变化：影响真实消费者；纯 ORDER 后继不自动内容失效。
- 方法前提失效：影响依赖该前提的方法与由其仍待使用的 Resolution。
- 独立分支增加：原未受影响运行继续；不因全局 graph revision 变化使所有结果作废。
- 删除/替换运行中任务：先撤销后续提交/动作资格并请求停止；迟到结果仍记录，但不直接接受。
- 外部操作已发生：保留真实后果；需要补偿时生成明确、获准的新操作。

本方案追求最小必要修改范围，不宣称每次计算全局最优的最小编辑距离。无法判定读依赖完整时采取保守闭包，并记录保守原因。

### 9.4 原子切换

候选计划、图校验、语义审阅、求解器调用在事务外；提交时重新检查涉及的要求、方法、事实/Acceptance 版本、预算和执行权限。

```text
BEGIN
  验证命令与payload hash，重复则返回原回执
  获取Mission控制/写入互斥；按统一顺序处理共享资源
  先过整数 base_graph_version 闸门（旧模式与新模式共用；allow_rebase 语义不变）
  再逐项核对语义 read_set（仅 hierarchical 语义版本的 Mission）；任一项过期即拒绝，不自动 rebase（ADR-13）
  核对活跃计划版本
  重新验证当前授权、预算、UNKNOWN与共享目标引用
  写入PlanRevision与方法实例变更
  写入需要失效的Resolution/缓存依赖标记
  撤销被替代工作的dispatch generation
  写入Task/依赖投影、预算转移、Event与Outbox
  保存命令回执
COMMIT
```

大型图使用持久 PREPARED 版本和激活屏障，最终短事务切换 active revision；不允许一半新图、一半旧图继续派发。冻结请求不能在切换中悄悄替换内容。

### 9.5 一条端到端例子

```text
目标G：交付满足R1/R2/R3的结果
    ├── 方法M1（AND）: 共享证据C + 方案A + 集成I1
    └── 方法M2（AND）: 共享证据C + 方案B + 集成I2
```

C 完成只执行一次。A 报告所依赖的接口 E 不存在，工具回执确认 FALSE。系统拒绝继续 M1 的后续实操，保留 C；B 已形成的产物不重做。Manager 为 M2 增加一个此前未知的取证/适配子目标 D，验证 D 后让 I2 综合，独立 Verifier 对照 R1/R2/R3 完成根验收。

如果 M1 中已有一个外部操作结果未知，M2 的相关动作先阻塞核对，不因选择另一方法再执行一次。若后来 C 的证据撤回，所有真实消费 C 的当前 Resolution 重新评估；历史“当时完成”的记录不改写。

验收必须在未见过的任务参数、另一种方法分解与不同外部反馈上重复成立，不能把这个示例写成 if/else 后声称通用。

## 10. Search Controller、Allocator 与多层 Manager

### 10.1 真正竞争的是多种行动

Frontier 不只包含 READY Worker Task，还包括：需要分解的 compound goal、可继续的 MethodInstance、必要取证、复审/核对、候选比较与综合、待人工事项。

Allocator 决定本轮投入什么，Scheduler 决定何时在哪个已获准执行资源上运行。现有七项启发式保留为可比较基线，不能再把 `0.5**tries` 叫作已校准不确定性。[S08]

### 10.2 搜索策略完整集合

最终实现至少包含：

- greedy/first verified 作为低成本对照；
- best-first 与 beam 的持久计划空间搜索；
- Best-of-N 参数化候选与多轮综合，而不是仅一个固定N上限的特殊模式；
- 在具备正确 Simulator/transition model 的注册域中实现有预算 lookahead（可用 UPOM/UCT/MCTS 策略），模拟环境与真实工具完全分离；
- 失败记录与反例驱动的剪枝、恢复及方法切换。

**实现模拟策略并不要求每个任务默认使用它。**没有相应可模拟环境时，这一策略明确不可用；不能以 LLM 想象的效果替代现实执行。论文方案只是实现依据。[W05]

### 10.3 预算与评分

评分分开记录预计质量增益、信息价值、解锁价值、费用、时延、重复度、风险与验证负担。数值必须带来源（规则/学习模型/人工/未知）和版本；没有校准数据时报告不确定，使用获准基线。

所有规划、检索模型、摘要、Worker、Verifier、综合和失败尝试计入统一消费。共享前置仅扣一次真实成本。先保护必要验证、最终集成与安全核对资源；不能开足 Worker 后没有额度验收。

Task Budget 是责任/分配上限；Provider 每次调用的 reservation 按实际可用 Context/输出和计价合同决定。不得将“配置256K窗口”错误地当成每次必然消费256K，也不能删除对未知实际费用的保守处理。

### 10.4 分层 Manager

单主 Agent 负责用户关系，Mission Manager 负责整体，Group Manager 负责明确 scope。每个 scope 有 coordinator epoch、允许修改的目标集合和额度。它们只提 Proposal，共享唯一 Commit。

父协调者更换不自动撤销仍合法子执行；依赖的是子 Task/输入/授权版本是否改变。两个 Manager 同时修改同一语义对象，按 read_set/OCC 冲突处理（顺序按 ADR-13：先整数闸门，再语义 read-set），不凭消息先到后到决定权威。

### 10.5 并发与背压

Provider 物理资源 ID 是容量合并键，多个 profile 指向同一服务不能各自获得全额槽位。独立模型服务和工具线程/进程按自己的资源维度限流。

积压超过高水位时降低新执行/新分裂、保护验证与核对；恢复低水位后渐进开放。保留探索额度和优先级老化，但它们不能绕过权限、硬预算或必要依赖。对调度震荡、重复提案、不断改名及零进展建立具名停止原因。

## 11. 世界状态、知识与证据的统一有效性

### 11.1 Fact 与 Claim 不同

FactRecord 保存经过允许方式确认的事实或观察，包含对象、谓词、参数、观察时间、有效区间、来源版本与检查范围。Claim 是候选自然语言/形式化命题；可能支持某个 Fact，但必须经过适当审阅/检查。

TruthValue 的 TRUE/FALSE/UNKNOWN/CONFLICT 表示在当前允许证据下的结论，不表示哲学上的绝对真理。一个引用被核实，只能证明对应来源这样写了，不能扩大成真实世界已经被验证。

### 11.2 条件化命题与 Justification

Knowledge 不再只有 content 和 dependencies，还需要：`claim_scope`、`assumptions`、`validity_interval`、`justification_sets`、`contradictions`、`assurance_level`、`permitted_uses`。

多份证据可能分别或联合足够支持一个结论：用“若干个 AND 支持集合之间 OR 选择”表达。撤回一份证据时重新评估所有支持集合，不把所有有过引用的知识无差别删除。

证据图允许共享支持，但禁止没有外部锚的循环自证。检测互相引用形成的强连通分量；不能 A 引 B、B 引 A 后两者都 VERIFIED。

### 11.3 统一失效服务

扩展已有 source_dependencies，而不是另起一套冲突系统。依赖索引覆盖：Fact→Claim→Knowledge→MethodApplicability→TaskInput→Acceptance→Summary/Context Cache。

检索前过滤与读取后复核都要执行。历史推理记录保留旧依据，新的动作前再次检查授权和时效。不同领域可提供自己的事实观察器，但所有有效性与来源格式经过统一协议。

### 11.4 独立审阅与事实使用

新代码 scoped observation 路径必须保留。通用语义 Claim 能晋级何种 assurance，需要明确审阅规则和使用范围；不能为了“只有 VERIFIED 能复用”而给所有有用想法伪造 VERIFIED。

Explorer 可以读取标注的候选/争议内容；执行安全前提和最终根验收只能使用满足其所需 assurance 的材料。默认 Worker 的事实区继续只放获准可依赖信息，候选区独立标识。

## 12. Context 与短期记忆：完整目标不再收缩

### 12.1 两套用途，共享来源原则

BaseAgent SessionMemory 管理该 Agent 真实经历；Mission Blackboard 管理团队成果、判断和方法相关知识。它们不是一份全局聊天，也不自动进入用户 Memory。

保留完整增量 Journal/Artifact，模型看到的是按预算编译的投影。每个新 Provider turn 都检查预算和必要材料；已冻结请求在恢复时不重新检索。

### 12.2 修复最新2000条范围限制

当前 `SessionRetriever.search_sync()` 先读取最近 search_window 条记录，再把 FTS/vector 的命中限制在这些记录内。必须改成：

```text
精确序号/ID → 对完整获准历史直接定位
词面索引 → 在完整保留范围查有界候选ID
向量索引 → 在完整保留范围/分区查有界候选ID
按RRF/重排合并 → 只回读候选正文和邻近完整组
当前权限、hash、索引版本、删除状态核对 → 预算装配
```

限制每次返回/扫描成本，而不是静默丢掉旧时间范围。库很大时按分区索引和段摘要路由，覆盖不足返回明确范围及 `index_partial`；“没有命中”与“没有搜到那段历史”必须不同。

### 12.3 Blackboard 检索与分层摘要

扩展现有 exact / lexical 基线，增加向量候选和可插拔重排，再按图距离、当前有效性、来源可信等级、分支相关性与去重筛选。已知道的引用使用精确查询，不用向量重新猜。

摘要分两种：确定性控制状态摘要始终从权威状态生成；语义工作摘要由模型在预算内生成并绑定源片段、条件、反证和未知项。禁止用200字符截断代替语义完整性；也禁止只保存“摘要的摘要”而无原文链接。

每个 Group Manager 只需要分支摘要、接口和问题；根 Manager 获得全局义务/方法视图与必要 evidence-on-demand，不加载全部子Agent聊天。

### 12.4 窗口配置

`effective_input_budget = min(configured_working_input, model_input_cap, total_window - output_reserve - safety_margin)`。

角色、系统规则、工具Schema、当前输入、状态、近期工具交互、召回和摘要都计入输入。保留最近完整交互按 token，而不是固定轮数。未闭合工具调用不可拆；超大工具结果先保存引用并提供有界观察。

256K/512K 是配置与部署能力，不是普适最佳工作点。最终完成要求至少在实际支持的多个工作容量上做任务质量/费用/耗时比较，冻结按任务类选择的配置策略。[W10]

### 12.5 上下文来源清单

保存 `agent_id,turn_id,request_id,requirements_version,method_binding,input_manifest,source_revisions,query,index_generation,summary_version,tokenizer/template/model_fingerprint,actual_token_count,selection_hash`。

Context 摘要不能授权调度；Planner 未召回某个任务不能导致未完成责任丢失。未完成目标、计时器、UNKNOWN 都通过结构化查询发现。

## 13. 通用独立 Verifier 与组合证据

最终 review 合同应为 `ReviewPackage → ReviewRecord → Acceptance/GoalResolution`。

ReviewPackage 锁定：原始要求、当前标准、具体 Task/MethodInstance、指定产物与输入版本、外部操作/工具回执、此前缺陷、允许读取与隔离检查的能力。

Reviewer 不是 Worker 的可写 Context 副本；不能修改被审产物再直接批准自己的版本。不同模型不是所有任务的必选，但关键风险可配置模型多样性；多个相同模型同意也不能替代证据。

对每个必需标准记录 PASS/FAIL/UNKNOWN、证据、理由和检查范围。全局结论 ACCEPT/REWORK/INCONCLUSIVE/REJECTED 与 Reviewer 自己的 AgentTurn 是否正常完成分开。

实际实现通用检查能力：

| 域 | 必须交付的代表性能力 | 不能冒充 |
|---|---|---|
| 代码 | 固定工作树/输入下的编译、测试与真实回执 | 测试文件存在就当测试通过 |
| 文档 | 来源版本、引用完整性、覆盖、推论范围、独立研究审阅 | 来源声称就当外部事实 |
| SQL | 隔离数据库、输入数据版本、事务约束、结果断言 | 语法正确就当业务正确 |
| 网页/API | 受控浏览器/API状态观察、业务动作身份与回执 | 页面文案“完成”就当操作确认 |
| 形式化 | 固定 Lean 工具链、依赖、命题身份、内核检查、未批准公理/占位检查 | 代码能编译一个不同命题就当原定理已证 |
| 实验/科学 | 实验配方、输入/随机性、统计或模拟检查、适用假设 | 单次输出等于普遍结论 |
| 企业流程 | 声明的规则与审批、指定对象版本、实际效果核对 | 内部任务完成等于外部付款/发送完成 |

不用同时实现 Lean、Coq、Isabelle 的所有功能；至少一个完整形式化适配必须实现，其他通过明确能力注册支持/不支持。不能只留下 `formal_check` enum 后标完成。[S18]

Lean适配的具体交付要包括：受保护的原始目标声明、固定 `lean-toolchain`/依赖、隔离环境中的实际构建/内核验证、目标类型核对、实际公理依赖清单和检查回执。默认拒绝sorryAx，允许哪些标准公理或编译器信任必须在policy里显式列出；官方资料明确指出公理依赖可以被审计，单看构建成功不够。[W15]

## 14. 工具、领域与平台

### 14.1 Task级能力而不是整Mission二选一

Mission 规定总体权限、风险和成功条件；Task 绑定领域能力子集。一个 Mission 可同时有文档调查、SQL分析、代码实现和浏览器验证，所有子权限不超过父授权。

扩展已存在 domain_handlers 和 benchmark domains，保留旧 profile 的冻结语义，不再扩建“每增加一种域就复制整个 Orchestrator”的分支。

### 14.2 能力状态

注册、已配置、可达、健康、获准、与当前环境兼容分别记录。Planner 获得的能力清单来自部署事实，而不是模型猜测。执行前 Tool Gateway 仍复核。

### 14.3 外部操作与补偿

同一现实意图使用独立于 Task/Attempt 的 OperationId；方法替换与模型重试不能重新生成同一个副作用的业务身份。流程为准备→审阅→授权→派发→确认/UNKNOWN→核对。

不支持可靠幂等/核对的接口，发生不确定结果时要求人工，不自动重发。补偿是新的受控操作，也可能失败；计划树回退不撤销现实。

### 14.4 平台与执行器

保留平台无关的 BaseAgent/HTN/Commit；进程监督、文件、凭据、通信和沙箱分别有适配层。Windows/Linux/macOS 在目标执行能力上各自实测；缺必要隔离时拒绝相应操作，不能静默落到可信代码执行器。

模型生成的代码不进入主控进程；子进程身份、沙箱归属和回收材料持久化，不能仅凭旧PID终止陌生进程。跨机器Executor通过受认证的TaskPackage和回执关联，状态权威仍唯一。

## 15. 长期承诺与有限周期

为了保留现有 Mission 终态，引入 `Commitment` 表示长期个人责任，`ExecutionCycle` 引用一次有限 Mission。单次任务只有一个周期，不要求用户理解额外层级。

Commitment 保存稳定 goal/owner、要求版本、周期规则、截止/关注时间、累计费用、权限/证据续期规则和关闭条件。每个周期有独立身份、预算、结果和通知；一次周期成功不关闭长期承诺。

定时器、外部事件等待和澄清等待全部持久化。进程计时器只负责及时性；重启后查询持久 due 状态。迟到周期策略明确为 skip、latest-once、bounded-catchup、ask-user。保存用户时区、本地规则和实际UTC时刻，测试夏令时重复/不存在时间。

暂停停止新的业务派发与新外部动作，但允许接收已发生结果及安全核对。取消不能删除已有后果；若 UNKNOWN 仍存在，留在取消中或显式移交后续核对义务并通知用户。

用户要求变更产生新版本，仅经授权入口改变；Agent 不能把难以达到的要求改简单来完成任务。

## 16. 完整事件重建与跨库执行

### 16.1 不再以局部Replay代替全部Event Sourcing

新模式每一次正式变更记录足够 payload 或受保留保护的不可变引用，纯 reducer 重建以下内容：要求、义务、计划/方法实例、Task当前投影、Acceptance/有效性、知识及理由、预算授权/预留/结算、等待/周期/通知义务、外部操作和审批事实。

现有旧 `replay-v2` 保留；新增 `business-replay-v3` 完整模式，而不是修改旧覆盖定义后宣布旧历史也全覆盖。[S15]

### 16.2 一次提交的约束

事件、同步写侧投影、待派发Outbox与命令回执在同一 orchestrator 数据库事务中提交。Reducer 不调用LLM、不读取当前网页、不根据当前时钟重新判过去。时间到期判定先形成事实事件。

执行数据库已有 Provider/Effect 事实不重复复制成另一份可竞争账本；通过稳定引用、消费回执和幂等导入加入业务事实。两库边界不能宣称天然ACID，必须测试每个接收/提交/确认崩溃点。

### 16.3 旧库迁移

- 保持所有旧事件、intent config、冻结请求和 canonical bytes。
- 对可重建的旧字段运行原覆盖审计；缺失的历史事实不能补写为“当时发生”。
- 新模式切入时允许建立经审批的一致性 GenesisSnapshot：明确这是迁移时可验证状态，带源库hash、事件水位、完整性和未覆盖项，不是假造过去事件。
- 新事件从明确边界开始完整记录；未知且影响状态的事件停止该流自动推进。
- 活租约、外部授权与时效在恢复时重新核对，不能从Snapshot直接恢复有效执行权。

### 16.4 完整恢复与删除

恢复 manifest 同时覆盖 orchestrator、execution、CAS、来源、工具连接器账本、策略和密钥恢复材料。先进入 `SIDE_EFFECTS_DISABLED`，核对相关已派发操作与删除清单，再恢复新动作。

用户删除敏感证据后保留非敏感 tombstone，旧索引、缓存和备份恢复不能将其复活。删除导致不能复验的历史必须显示这个限制，不声称永久逐字可复现。

## 17. 学习、评测与产品界面

### 17.1 完整学习目标

实现三个真正可训练产物：方法检索/适用性排序、任务/方法投入优先级、模型路由与成本质量预测。数据来自全角色真实Trace，带任务族、工具/模型版本、可观测反馈、成本、最终外部评分与失败原因。

不以“API返回成功”作为业务成功；不混用fixture和真实轨迹；不把本次用过的测试题反复训练后报告泛化。

训练后产物包含特征Schema、训练数据manifest、模型hash、校准报告、适用域与基线比较。没有证明提升时仍可交付学习能力，但候选不得晋级默认策略；不能把“暂不晋级”偷换成“从未实现训练”。保留 rules-v1 作为审计友好的fallback。[S14]

训练实现具体采用可复现Python pipeline：以逻辑回归为质量概率基线、梯度提升为非线性候选，费用/时延另建回归器；校准集与拟合集分开，按任务族切分。可使用scikit-learn的校准与梯度提升组件，但选用算法的收益仍由保留集判断，不把输出0.8自动解释成真实80%成功率。[W13,W14] 方法泛化则需变量化/类型一致、条件提取和负例验证，不只是模型路由训练。

保存每次选择时实际可选集与策略版本；若策略随机，记录选择概率。只有存在支持和可靠概率时才做相应离线反事实评估；没有这些记录时不能从成功路径相关性宣称因果收益，改用受控对照。分布变化触发漂移报警与回退，不能自动扩大权限。

### 17.2 UI必须表达语义而非只有节点动画

需要显示：目标与方法层次、备选路线/采用路线、未细化目标、共享子目标、当前要求与证据版本、被替代但保留的历史、为什么局部改图、失效验收、预算去向、真实等待原因和未知外部操作。

后端输出权威投影；TS 前端使用同一公开Schema验证，不从事件文本猜Task状态。非法消息显示协议问题并保留标stale的旧画面，不映射为业务UNKNOWN。

H0 输出必须是实际文件/函数映射（v1.2：Host 本机可读，H0 不再受阻）：命令入口、Mission service装配、projection、事件传输、store、任务图/审批/证据组件与原生测试。未获得这些映射不能虚构现存路径，但SDK部分可独立建设。

### 17.3 现在是否开始测试？

开始，而且以两条线并行：已有系统的强基线与故障回归；新HTN语义的合同/模型检查/真实行为验收。测试不能替代缺失的HTN，也不需要等全部功能完成才发现不相容。

正式能力证明见§21：规划有效、系统可靠、端到端有效三者分别测。

---

## 18. 代码层面具体如何落地

以下全部按当前Python包结构组织。新目录明确为拟新增，不调用尚未存在的函数。每次变更的命令和外部参数通过版本化Schema进入内部强类型对象。

### 18.1 新增核心目录（拟议）

```text
src/agent_orchestrator/
  contracts/
    htn.py                  # TaskForm / Method / Binding / PlanProposal
    obligations.py          # Stable obligation / funding and retry lineage
    evidence_state.py       # Fact / Justification / validity
    feedback.py             # observation / diagnosis / proposal
    resolution.py           # Review binding / GoalResolution
    commitments.py          # Commitment / Cycle / WakeSpec
  planning/
    htn/
      registry.py           # Versioned Method registry; no direct model promotion
      applicability.py      # Safe predicate AST evaluator
      grounding.py          # Parameters and data binding
      synthesis.py          # LLM method proposal through existing Agent dispatch
      refinement.py         # Expand compound task
      compiler.py           # Typed network → materialized Task DAG
      validation.py         # decomposition / coverage / constraints
      backends/panda.py     # Pinned HDDL parser/solver/validator process adapter
    search/
      contracts.py          # SearchState / Action / Decision receipt
      frontier.py           # Plan-space actions, not only READY Tasks
      best_first.py
      beam.py
      simulated_uct.py      # Simulator port required, no live tools
      controller.py
    repair/
      impact.py             # Actual read/dependency closure
      proposal.py
      planner.py
  knowledge/
    justifications.py
    validity.py
    predicates.py
  context/
    semantic_retrieval.py
    scoped_summary.py
    selection_manifest.py
  commitments/
    service.py
    cycles.py
    wakes.py
  graph/
    task_network.py         # Immutable TaskNetwork snapshot + typed relation views (v1.3)
    projection_validation.py# Pure checks over the execution projection: gates, ports, cycles (v1.3)
    eligibility.py          # evaluate_readiness + EligiblePrimitiveTask construction (v1.3)
    demand.py               # Shared-subtask consumers / DemandRef convergence (v1.3, P3)
  artifacts/
    input_bindings.py       # DataRequirement → BoundInput → InputManifest (v1.3)
  storage/
    htn_store.py
    obligation_store.py
    validity_store.py
    commitment_store.py
  orchestrator/
    plan_commits.py         # Same Store transaction; no second writer authority
    resolution_commits.py
    requirement_commits.py
  observability/
    business_replay.py
    reconstruction_manifest.py
  learning/
    datasets.py
    method_generalization.py
    priority_model.py
    route_model.py
    calibration.py
```

目录不是微服务清单。不要创建一组无调用方的空类后宣称交付；每个模块必须进入后面的完整功能场景。

### 18.2 现有文件修改表

| 当前文件/入口 | 修改方式 | 必须保留 |
|---|---|---|
| `planning/planner.py` | 保留旧task_graph解析；新 `planning_contract_version` 解析 Method/Plan提案 | 旧冻结提示与旧JSON含义 |
| `graph/task_graph.py` | 保留旧扁平DAG检查；新编译器调用其依赖/OCC能力；新上限由冻结capacity profile提供 | 无环、预算、权限和输出冲突约束 |
| `graph/changes.py` | 不直接往旧OPERATIONS无限堆语义；新PlanProposal经plan_commits编译成受控图变更及方法/义务更新 | 原事务与迟到结果边界 |
| `planning/manager.py` | 固定模板作为legacy/default方法来源；不当作全功能HTN引擎 | conflict/synthesis实际路径 |
| `planning/candidate_selection.py` | v1继续读取；v2用Method/Goal scope支持可配置N、多个综合轮次和完备回执 | 不放松鉴权、预算与受检产物绑定 |
| `scheduling/allocator.py` | frontier区分compound规划行动与primitive执行；接入义务/方法预算、已校准信号 | 真实槽位、验证背压、老化 |
| `orchestrator/event_handler.py` | 仅装配/调用新planning/repair/commit模块，不继续堆所有算法；WorkerFeedback事件进入scope Manager | 现有BaseAgent、晚到费用、取消/恢复逻辑 |
| `memory/claims.py` | 保留scoped-observation新默认；新增条件/理由与语义审阅结果路径 | 旧domain version解释、无关测试不得任意晋级 |
| `memory/verified_knowledge.py` | 新版本增加条件/理由/适用范围，不将旧不存在条件默认为已证真 | 真实来源、结果/Attempt归属 |
| `memory/source_dependencies.py` | 成为通用validity服务的来源适配，现有特例不删 | 原始血缘不改写、diamond复用 |
| `context/retrieval.py` | exact/lexical作为基线，新增语义候选和重排版本；最终仍合法回读 | Mission/scope过滤、stale排除 |
| `context/compression.py` | 控制状态投影保留，语义摘要新增版本和证据范围，不再用简单截断代表完整摘要 | 来源与未知、不升级可信等级 |
| `simple_harness/agents/memory/retrieval.py` | 先全scope索引查有界ID，再回读；精确引用不受最新2000窗口限制 | Journal唯一原文、embedding fingerprint、降级可见 |
| `governance/domains.py` / `verification/domain_handlers.py` | 支持Task级域能力绑定，按当前已存在handler扩展 | Code/Doc/AppWorld/AgentDojo/ARE冻结快照 |
| `verification/verifier_router.py` | 统一ReviewPackage与证据能力；增加实际formal适配，保留必需检查veto | NOT_REQUIRED与PASS区别、独立Critic |
| `observability/replay.py` | v2保留，新增全业务投影模式和覆盖清单 | 不执行Provider、不按现值补历史 |
| `storage/schema.py` / `storage/store.py` | 新增下一未占用迁移及Store方法；所有业务变更同一事务 | 旧DDL/checksum、canonical JSON |
| `api/facade.py` | 在新协议版本开放查看/提议方法、修订要求、长期周期、Resolution查询；权限由服务端决定 | tenant/幂等/回执/只读Artifact身份 |
| `governance/learning.py` | 保留rules-v1；新训练/校准产物经现有promotion策略门接入 | 样本不足不伪造提升，在线不自提升 |
| `orchestrator/commit_service.py`（v1.2 补） | 引入显式 planning binding；新增 `PlanCommitsMixin` 作为第 9 个 mixin；整数 `base_graph_version` 闸门与 `allow_rebase` 路径不改（ADR-13） | 旧 Mission 的提交路径、事件 canonical JSON、幂等 key |
| `orchestrator/mission_tail_commits.py`（v1.2 补） | P1 接入的唯一预算路径：替代任务沿同一 Obligation 继承失败计数与已消费额度 | 独立 Mission Judge 预算增长规则、错误账户拒绝 |
| `runtime/role_templates.py` / `runtime/output_blocks.py`（v1.2 补） | 新增 `method_proposal`、`plan_revision_proposal` 标签块及其 codec；格式错误的有界修复复用现有 BlockError 路径 | 现有四个标签块及冻结提示原文 |
| `tests/orchestrator/fixtures_provider.py`（v1.2 补） | 扩展脚本化输出，使 HTN 端到端测试不依赖真实模型 | 现有 fixture 协议 |
| `artifacts/versioning.py`（v1.3 补） | 新模式按 InputManifest 物化，不再汇入所有祖先接受产物；ORDER 不授予读取与覆盖权；执行/物化路径遇拓扑不完整抛 GraphIntegrityError | legacy `merge_accepted`/`collect_upstream_inputs` 逐字节不变；`ArtifactConflict` 语义不变；诊断路径（traces、evaluation）继续容错 |
| `artifacts/workspace.py`（v1.3 补） | 只物化受控 InputManifest；输出映射/合并显式 | CAS 校验、保护文件、隔离副本 |
| `graph/dependency_checker.py`（v1.3 补） | 抽出可复用的迭代拓扑/环证据，只对单一执行投影调用 | 现有 Kahn 校验与稳定排序 |
| `graph/deduplicator.py`（v1.3 补） | legacy 文本签名保留；新模式区分幂等重放、slot 重复、相似候选、共享执行四种情形 | `find_duplicates` 旧行为 |
| `observability/traces.py` / `observability/evaluation.py`（v1.3 补） | 只读消费 `merge_accepted` 的诊断路径保持容错，不受 GraphIntegrityError 影响 | 现有输出 |

### 18.3 核心函数合同（拟新增）

```python
# 接口规范，不是已经可直接导入的实现；所有参数由边界codec构造。
def assess_method(
    task: TaskSpec,
    method: MethodContract,
    snapshot: EvidenceSnapshot,
    capabilities: CapabilitySnapshot,
) -> ApplicabilityReport:
    """区分适用、不适用、需取证、冲突、能力不可用；不执行工具。"""


def ground_method(
    task: TaskSpec,
    method: MethodContract,
    bindings: ParameterBindings,
    assessment: ApplicabilityReport,
) -> MethodInstanceDraft:
    """类型检查、变量绑定、节点/端口身份；无数据库副作用。"""


def compile_refinement(
    draft: MethodInstanceDraft,
    current: TaskNetworkSnapshot,
) -> ProposedPlanDelta:
    """输出部分序、数据绑定、义务覆盖和所读语义版本。"""


def analyze_impact(
    current: TaskNetworkSnapshot,
    delta: ProposedPlanDelta,
    validity: ValiditySnapshot,
) -> ImpactReport:
    """计算真实影响闭包及未知覆盖，不相信LLM自报无影响。"""


def commit_plan_revision(
    store: Store,
    command: CommitPlanCommand,
) -> PlanCommitReceipt:
    """在现有短事务中检查并提交；不调用模型/网络。"""


def apply_goal_review(
    store: Store,
    command: SubmitGoalReview,
) -> GoalResolutionReceipt:
    """审阅已记录不等于接受；当前绑定全部有效后才形成Resolution。"""
```

命名约定（v1.2）：`PlanProposal`（§18.1 `contracts/htn.py`）是 LLM 侧提出的、未经检查的提案对象；`ProposedPlanDelta`（§18.3）是 compiler 输出的、已通过结构/覆盖检查、可交 Commit 的计划增量。二者不可互换。

允许数据库读取的边界与纯算法分开；所有 `Any/Mapping` 外部输入只在codec，内部 use dataclass/Protocol/NewType/enum；类型检查不替代事务归属检查。沿当前契约加固专项推进，而不是发起另一次全仓类型重写。

### 18.4 拟新增表与索引

```text
obligations                 (mission_id, obligation_id, current_revision)
obligation_relations        (parent, child, kind, active_revision)
requirement_revisions       (mission_id, version)
method_contracts            (method_id, version, hash, registry_status)
method_admission_receipts   (method_hash, scope, policy_version, verdict)
method_instances            (mission_id, instance_id, goal_id, plan_revision)
plan_revisions              (mission_id, revision, state, snapshot_hash)
plan_bindings / typed_edges (plan_revision, endpoints, kind, payload)
plan_read_sets              (proposal_id, subject_type, id, semantic_revision)
facts / observations        (scope, predicate, entity_key, observed_at, validity)
justifications              (claim_or_resolution, support_set, member_ref)
goal_resolutions            (obligation_id, resolution_id, requirements_version, validity)
search_states / decisions   (mission_id, scope_id, version, policy_hash)
commitments / cycles        (owner, commitment_id, cycle_key, mission_id)
durable_wakes               (due_at, status, generation, unique_wake_identity)
notification_outbox         (completion_id, recipient, state)
reconstruction_baselines    (aggregate, watermark, hash, coverage_version)
```

实际迁移号必须在实施时读取当前最高版本后分配，不能修改旧编号。不能为每张表单独追加一个提交而丢掉原子性。共享任务关系的唯一约束、消费引用和幂等命令与领域身份一致；索引避免全历史扫描。

---

### 18.5 具体兼容接线与必须拒绝的旁路

新增 `task_semantics` / `TaskSemanticBindingV1`（同orchestrator库）将 form、Obligation、GoalSignature、MethodInstance 与已有 task_id 关联；组合读取返回强类型TaskView。这样旧 `Task.json` 不需要因为内存重构而重序列化。新Mission全部Task必须有语义绑定；缺失绑定是损坏而不是legacy fallback。未来版本可合并物理表，但不得形成两个写入权威。

Scheduler的输入改为 `EligiblePrimitiveTask`：只有当前计划采用、语义绑定完整、要求/前提/输入/权限有效且预算可用的原子任务，才能构造这一类型。旧 `allocate()` 的READY判断不能直接绕过该门。复合节点不得因为现有status是READY就创建普通Attempt。

`MethodSynthesizer`、规划审阅、根目标Verifier继续通过现有持久dispatch intent、BaseAgent与费用通道调用；新增role用途不得伪装TaskCritic而进入错误Task预算。它们的typed context与输出codec纳入契约加固，模型错误输出有界修复，超时/UNKNOWN不另开新请求洗掉原身份。

PlanCommit的产生事件和投影应用应成为单一调用：写事件、按对应版本reducer更新写侧投影并写Outbox；不允许先手工update新语义状态再补一个可能漏掉的event。涉及旧execution库仍使用原有cross-db回执协议，不在数据库事务内等待模型、solver或工具。

**三条兼容硬约束（v1.2，写入 P1 门槛）**：

1. `orchestration_semantics_version` 的服务端默认值硬编码为 `legacy`，由单独测试锁定；只有显式请求 `full-target-v1` 的新 Mission 才要求语义绑定。
2. `scheduling/allocator.py` 保留旧的 READY 入口；`EligiblePrimitiveTask` 是新模式的附加门，不是 `allocate()` 的唯一入参类型。旧 Mission 在无语义绑定时照常派发。
3. 旧事件的 canonical JSON 字节级不变，由对比测试锁定；"PlanCommit 事件与投影单一调用"只适用于新模式事件类型。
4. （v1.3）新模式不重定义旧 `TaskStatus.READY` 的含义；compound Task 的 READY 只是可重建的展示索引。allocator 拦截 compound 的门是 `form=compound`（来自语义绑定），不是语义版本号，也不是状态字符串；旧入口拿到新 Mission 的 compound 时必须拒绝派发并返回 NEEDS_REFINEMENT。

LLM 侧合同（C8）：Planner 与 MethodSynthesizer 通过 `runtime/role_templates.py` 新增的 `method_proposal`、`plan_revision_proposal` 标签块输出，经 `output_blocks` 严格解析进入 §18.3 的 codec；格式错误走现有 BlockError 有界修复，不另开请求；`tests/orchestrator/fixtures_provider.py` 扩展脚本化这两种块，使 P2.1/P2.3 的确定性测试不依赖真实模型。

旁表期间 typed edges 与现有 dependencies 是两份关系事实，必须有跨表 invariant 测试，不能只靠"不得形成两个写入权威"这句话。

### 18.6 建议立即添加的真实测试路径（拟新增）

```text
tests/orchestrator/full_target/
  test_obligation_conservation.py
  test_htn_grounding_partial_order.py
  test_htn_novel_method_admission.py
  test_htn_and_or_shared_goal.py
  test_htn_feedback_local_repair.py
  test_resolution_invalidation.py
  test_scoped_manager_concurrency.py
  test_search_restart_and_simulator_guard.py
  test_mixed_domain_goal_review.py
  test_full_business_replay.py
  test_commitment_durable_wake.py
  test_policy_training_and_promotion.py
tests/agents/full_target/
  test_history_beyond_2000.py
  test_context_scope_and_freeze.py
```

这些路径是开发目标，当前仓库不存在也不影响本计划的诚实性。实施后才填写 `acceptance-scenarios.json.runner_node_id`；本资料包不会预填一个命令并声称可运行生产测试。

## 19. 九个完整工作包：顺序不缩减终点

这些是同一最终设计的依赖排序，不是每做一点再决定最终架构。P9通过前不能宣称FULL-TARGET-1.0完成。

### P1：语义契约、义务与证据有效性基础

交付一个完整场景：同一用户要求的Task被替代后，预算/失败次数继承，条件化事实正确阻止不合法复用，两个评审身份走正确账户。

新增 typed IDs/Obligation/Fact/Justification/typed edge 与存储；接上现有Commit/预算/评审，不只写Schema。保留最新严格Claim和契约加固成果。依赖：无。

v1.2 切片：P1 拆为 P1.1（HTN 所需的纯内存契约与四值谓词求值器，零 DB 零 commit）、P1.2（obligation_store 与迁移）、P1.3（只接 `mission_tail_commits.py` 的"替代任务失败计数/预算继承"一条路径）。P1 门槛追加 §18.5 的三条兼容硬约束。P1.1 先于 P2.1 交付，P1.2/P1.3 在 P2.3 之前补齐。

### P2：真正的 HTN 分解到实际执行

交付：参数化Method、AND/OR、部分序、共享子目标、空库方法建议、Registry审阅、HDDL适配、显式GoalResolution。Planner生成完整局部细化，现有BaseAgent执行真实叶子并验收根目标。

验收：一个方法换参数可复用；一个新目标需要新方法且不是关键词硬编码；已建模样本通过独立分解/计划验证；开放样本不冒充形式化证明。依赖P1。

v1.2 补充：P2 依赖 P1 的 P1.1、P1.2、P1.3 三片（纯内存契约、obligation_store、预算继承单路径），执行顺序见 §23。P2 交付物增加种子方法库与最小谓词观察器集（§7.3）、LLM 侧标签块与 fixture provider 扩展（§18.2）；PANDA 不阻塞门槛（§7.4）；方法状态机只到 `TRIAL_ADMITTED`。P2 的 Grok 验收按 §21.5 执行。

v1.3 补充（C23）：P2 按附件 TG 再切为 P2.1（compiler 含 entry/exit 门与部分序）、P2.1b（task_network 快照与投影验证）、P2.1c（纯 evaluate_readiness）、P2.2（PANDA）、P2.2b（input_bindings 纯解析）、P2.3a（旁表 + plan_commits mixin + planner 标签块）、P2.3b（event_handler 与 versioning.py 接线）、P2.3c（allocator form 门 + resolution_commits 根验收 + Grok 验收）。附件 TG §17 的三个接线目标是 P2/P3/P7 的终局验收目标，不改变本顺序。

### P3：运行反馈、局部修复与要求变更

交付：Worker结构化反馈、取证、方法失效切换、PlanRevision read-set、current validity、在途控制和共享目标保留。

验收：C为A/B共享输入，A路线失败仅替换A；用户改变一项要求只使相关接受失效；旧Reviewer迟到不可批准新计划；费用不重置。依赖P1,P2。

v1.3 补充（C23）：P3 预切 P3.0（ADR-13 spike）、P3.1（read-set 集合谓词与不存在性读取）、P3.2（DemandRef 与共享消费者收敛）、P3.3（GraphIntegrityError 与七个崩溃切点）、P3.4（compound reducer 的 Hypothesis 状态机，需先交付参考解释器，成本单列）。附件 TG 场景 B、C 是 P3 的终局验收场景（见 acceptance-scenarios.json 的 tg_annex 字段）。

### P4：计划空间搜索、分层管理和资源闭环

交付：持久best-first/beam、模拟lookahead适配、有预算Best-of-N/多轮综合、scoped Manager、真实物理资源分配、受保护验证尾额和探索/背压。

验收：不同方法竞争，保留可证有效的失败片段、最终组合再验；两个Manager合法并发提案；低分支探索不刷预算；无Simulator不能调用真实工具做rollout。依赖P2,P3。

### P5：全历史 Context 与分层知识使用

交付：修复search_window范围限制、全scope索引查询、混合Blackboard检索、带来源条件的层次摘要、每请求选择manifest、工作窗口配置实验。

验收：超过2000条后的旧关键事实能被精确/词面/语义找回；多轮压缩条件不丢；冻结请求重启后不换材料；跨scope不可读。依赖P1，可与P2/P3并行。

### P6：独立语义验收、跨领域工具与真实动作

交付：Task级能力域；代码/文档/SQL/浏览器或API/形式化/实验/流程的代表适配；统一Review/GoalResolution；稳定Operation、补偿、审批和平台执行边界。

验收：同Mission跨域组合；Lean确实检查指定命题；外部回执丢失不重复动作；换方法不改变业务幂等身份；受控平台不足时拒绝。依赖P1,P2。

### P7：长期承诺、完整业务重放与恢复

交付：Commitment/Cycle、持久wake和通知义务、纯业务reducer、genesis兼容、双库恢复、冷热归档、删除tombstone和恢复禁副作用。

验收：停机跨过多个周期按政策触发；清空新模式投影可从事件/基线重建；未知外部操作核对后才启动；旧权限/租约不复活。依赖P1,P3,P6。

### P8：可训练策略与方法库改进

交付：真实轨迹数据集、可训练priority/router/method-ranking、参数化方法泛化、校准与离线对照、版本晋级和回滚。实现全部机制；是否启用新策略取决于证据。

验收：相同数据/config可重建训练产物；保留任务族不泄漏；错误奖励信号被拒；无收益返回不晋级而不是编造提升。依赖P4,P5,P6。

### P9：完整 Host 体验、外部能力与总验收

交付：真实Host文件映射、前后端共享Schema、方法/目标/依赖/失效展示、用户修订/澄清/审批/周期管理、完整IPC与端到端评測报告。

验收：所有59项必需能力各有接线与行为证据，R55用户长期Memory明确不接；不存在“接口已定义但实际handler未部署”冒充完成；性能/收益与机械正确分开报告。依赖P1–P8。

v1.2 补充：Host 本机可读，"Host 映射"从 P9 内部拆为独立前置切片，在 P4 之后、P9 之前完成，产出实际文件/函数到公开规划契约的映射表；P9 的工作量按该映射重估，不再按"未知"估。

## 20. 兼容与切换策略

1. 首先固定当前legacy测试输入、序列化、请求/产物hash和模型配置，不因新语义改旧身份。
2. 通过新的 `orchestration_semantics_version=full-target-v1` 对新Mission启用HTN，不由客户端任意注入数据库字段；参数经公开Schema和服务端授权。
3. 旧Mission继续按旧domain/selection/contract解释。迁移活跃Mission必须显式暂停、核对UNKNOWN、导出已确认事实和未完成义务，再形成有来源的新PlanRevision；不能直接把旧Task猜成复合节点。
4. 空字段不能默认解释成“所有前提为真”；旧任务缺少方法见证时标记legacy direct method / unmodeled，而不是补造过去HTN。
5. 同一Mission只有一条正式执行控制权。shadow比较用保存输入/回执或隔离模拟环境，不能两套计划各自执行一次现实操作。
6. 新表/Schema迁移在副本演练，验证当前预算、任务、外部动作和CAS引用一致。当前最高迁移/版本在开工前重新核查，本文不预占数值。
7. 对外Schema由Host公开协议管理，SDK内部Method/预算表不直接暴露；未知关键协议版本明确失败，不能容错成UNKNOWN业务。
8. CI/打包不是本次主要建设项目，但不能不运行本地严格类型、协议和实际行为验证。用户延期发布不影响源码级测试。

## 21. 完整验收：用什么证明不是换名字的动态 DAG

### 21.1 三层证据

**形式规划有效性：**固定IPC HTN/HDDL问题与求解器版本，记录解、分解见证、动作顺序/前提校验、超时/无解/不支持区别。不能只比较任务树文本。[W01–W03,W11]

**运行时可靠性：**真实SQLite与进程故障注入；计划提交/预算/接受/派发/通知每个切点验证。数学或模型计划正确并不自动保证真实工具执行正确。

**用户任务有效性：**使用已经出现的AppWorld/AgentDojo/ARE集成基础，核查实际adapter与官方evaluator连接后运行；用同模型同总预算强单Agent、静态多Agent、完整系统及必要消融比较。公开基准的版本/轨迹/规则由当次官方要求固定，不把自定义故障集冒充官方成绩。

### 21.2 必须覆盖的反例

- 目标没有可用预写模板，但能提出可检查的新方法；不能仅按coding/research分类输出固定步骤。
- 方法有多个参数与任意部分序，同一Agent数不影响语义正确性。
- 一个OR方案成功，不要求所有未选择历史方案成功。
- 三层以上按需展开；未知前提通过真实查询变成已知，不以猜测替代。
- 共享子目标只执行一次；一个分支退出仍服务其他消费者。
- 替代方法产生相同现实动作时稳定幂等；未知结果先核对。
- 无关图修改不失效原产物；相关输入/要求改变使旧Review不适用。
- 已接受知识有两份独立充分理由，撤一份不误删；全部理由失效必须传播。
- 经过长历史、压缩和重启后还能检索早期资料；不能把2000条窗口外当没有历史。
- SQL/浏览器/代码/文档跨域组合；formal_check真正运行且检查目标身份。
- 多级Manager不能越scope或重置预算；多个profile共享物理容量。
- 新模式全业务投影可重建；旧历史不足显示coverage，不补造权威事实。
- 长期承诺的周期、时区、授权到期、通知和删除策略都能恢复。
- 所有现实统计能区分任务失败、正常拒绝、未知结果、评分器故障、调用配置缺失。

### 21.3 完成标准

本资料包为60项需求建立90项建议验收场景，其中R55是明确排除规则，其余是59项功能/语义/安全必需项。场景是规范，不是已执行结果。

各项必须具有：实现入口、测试node ID、固定输入/环境、预期结果、实际状态与证据、失败报告、源码版本。不能用总体“测试全绿”掩盖某个模块没有部署；也不能用Benchmark平均分抵消重复付款、越权或预算双花。

通用性至少以多个不同领域、不同参数和未见过的方法组合证明；有限测试不能证明任意任务必成功。最终报告分开列：需求实现覆盖、机械与安全正确性、规划有效性、实际任务能力、编排相对基线的收益。

### 21.4 需求永不丢失的规则

`requirements.json` 是本次最终能力清单，不是运行时策略；每个ID必须映射到工作包和测试。

需求状态只能从 planned → implemented → integrated → behavior_verified → externally_evaluated（最后一项按适用性），或经用户批准明确变更范围。`not_needed_in_first_phase` 不是完成状态。每次交接必须同时列出当前工作包状态与整个目标尚未满足的ID。

---

**v1.2 新增规则**：某个工作包开工前，必须先把该包的模板化合同层场景细化为逐条前置、步骤、证据要求，并作为该包红测试的来源；细化未完成不得开工。

### 21.5 收益假设、Grok 验收协议与退出规则（v1.2 新增）

**为什么要有这一节**：v1.1 只要求"分开报告"，对照结果不影响任何投入决策，计划不可证伪。本节把收益假设、测试协议和中止条件预登记。

**收益假设**：完整 HTN（H 臂）相对现有动态 DAG（F 臂）的收益不在官方通过率上预设，而在三件事上：方法切换与共享子目标真实触发、失败可归因到方法层、分解见证可独立校验。通过率与费用作为配对对照报告，门槛见下。

**协议**：

| 项 | 规定 |
|---|---|
| 模型 | grok-4.6，reasoning_effort=medium，走现有 SuperGrok 订阅通路（`scripts/grok_build_runtime.py`），跑完必须 restore；runner 侧 usage 归一与模型回显映射复用 A96 适配 |
| 时机 | P1.1、P2.1、P2.2 是纯函数，不调模型，由真值表、golden 分解、PANDA 独立验证器证明；P2.3 起每个调模型的小片交付后执行本协议；等当前 A96 两遍跑完再开，不与其争抢 Grok 通路和 2 个物理槽；另一 session 的 N7 归因报告作为失败归因的基线输入，未完成时本协议的归因项记 PENDING_N7 |
| 题集 | A96 冻结的 12 道 AppWorld dev 题（instruction hash 已核）+ 3 道 code 题 + 3 道机制触发构造题；每题 2 次重复，共 36 局配对。code 题在 P2.3 前冻结并记 hash，评分器固定为每题附带的隐藏 pytest 目标（按 hash 冻结，Verifier 不可读），官方通过 = 隐藏目标全绿，与 SDK 内部 `scoped-observation-v2` 分级无关。机制触发构造题为 code 域：预置一对 OR 方法、其中一条的前提被谓词观察器否定、一个被两条方法共享的只读子目标，按构造必然触发方法切换、共享复用和 UNKNOWN 取证 |
| 臂 | H（完整 HTN）与 F（当前动态 DAG）同题同预算同模型配对，**F 臂在同一 build、同一时段重跑**，不复用 A96 旧 F 结果；S/R/D 沿用 A96 已有结果只作背景，不进入配对。温度与采样用 provider 默认并记录；重复是独立新局 |
| 硬性不变量 | 分解见证 100% 通过独立校验；缺根要求时根 Resolution 形成次数为 0；预算守恒等式在每局结束时成立；旧 Mission 回归零变化 |
| 机制触发 | 在 3 道构造题上方法切换、共享子目标复用、UNKNOWN 前提取证必须各触发 ≥1 次，未触发记 FAIL（按构造应触发）；在 15 道自然题上未触发记 INCONCLUSIVE。INCONCLUSIVE 不阻止阶段按硬性不变量放行，但阻止「收益已证明」的任何表述，收益门槛在下一阶段继续开放 |
| 效果门槛（预登记） | 口径为配对不一致局（McNemar 口径）：36 局配对中，H 通过而 F 失败的局数减去 F 通过而 H 失败的局数 ≥ 2，同时附不一致局的精确二项 p 值作为信息项（36 局无显著性判别力，本门槛是筛选门不是显著性结论）；或不一致局差为 0 且每成功归一 token 不高于 F 的 0.8 倍。费用口径：Grok 订阅通路无按 token 计价，费用统一定义为 runner 归一后的总 token（input + output 含推理），来源为 A96 已有的 usage 审计；两臂通过数同为 0 时记 INCONCLUSIVE_ZERO，不套用比值。未达门槛记 NO_GAIN，不降低标准凑 PASS |
| 执行者 | 一个独立子代理（Opus）负责跑协议、写报告，不参与该阶段的实现；报告只写文字结论、run id 前缀、SHA，原始收据留 `.local-test-evidence/` |

**退出规则**：P4 完成后，若 H 未达效果门槛，且失败归因不指向方法适用性缺失、分解错误或缺局部修复，则 P4–P8 降为记录基线，不继续建设；P1–P3 已交付的部分保留。这条规则改变的是投入顺序，不删除 R01–R60 任何需求。

## 22. 技术选择：确定采用什么，为什么，不把新词当最佳实践

| 问题 | 本方案选择 | 依据与边界 |
|---|---|---|
| 层次规划表达 | 参数化Task/Method + HDDL兼容编译 + 自身typed语义网络 | PANDA/HDDL提供实际方法、部分序和计划验证能力；不声称能形式化所有开放世界 [W01–W03] |
| 模型辅助分解 | 受约束LLM方法提案 + 符号结构检查 + 独立语义审阅 | ChatHTN可参考混合思路；其定理只适用自身建模条件 [W06] |
| 动态粒度 | 按执行反馈/未知前提触发递归细化与局部修复 | ADaPT、RAE、较新的AdaPlan-H支持这类方向，不照搬研究分数 [W04,W05,W08] |
| 方法学习 | 参数化轨迹抽象、检索、适用性学习与离线批准 | Online Learning of HTN Methods / HCL-GP为研究参考；预印本不等于久经生产证明 [W07,W09] |
| 搜索执行 | best-first/beam作为可解释实现；有Simulator域提供UCT | 没有模型支持时不模拟现实，不要求每个任务MCTS [W05] |
| 数据与事务 | 现有Python/SQLite + 单一Commit + Outbox + 不可变CAS | 当前代码兼容；并发/跨库事实必须真实验证，不为了“先进”强换栈 [S16,S21] |
| Context | 增量原文、结构化状态、完整scope混合检索、来源摘要 | 按需取材与压缩组合，不追求输入永久填满 [W10] |
| 可训练决策 | 轻量统计/梯度提升基线、独立校准、持出任务族对照 | 训练可执行且可审计，复杂模型只有证实收益再晋级 [W13,W14] |
| 形式化核验 | 固定Lean环境、命题身份和公理依赖审计 | 不以任意文件编译成功代替原命题证明 [W15] |

当前没有一项公开研究证明某个完整Agent OS架构对所有目标都最优。这里选择的是匹配你要求的可验证组合，保证技术边界清楚；不是将每个最新框架都拼在一起，也不是以“没有统一最佳”回避给出明确设计。

## 23. 现在最该启动什么（v1.3：按 P 级工作包编号的执行顺序）

用户 2026-09-16 确认：先做 HTN 递归分解（P2），P1 只做 P2 所依赖的部分并在 P2 接线前补齐；每个调模型的小片交付后由独立子代理用 grok-4.6 medium 按 §21.5 验收，等 A96 批次跑完再开。v1.3 按附件 TG 把 P2 再切，把图层落点补齐。小片编号 = 所属工作包 + 序号；P0 是开工前置。

每片纪律：先写红测试；独立全量回归（编排范围 `tests/orchestrator`）；旧模式零回归；完成即推 main 并更新 HANDOFF 与 ARCHITECTURE；最多 3 个子代理并行且写入范围不重叠；热文件（commit_service、event_handler、versioning）只允许单代理；不补工时估计，每片记录实际耗时。

### 执行顺序

```text
P0（完成）
→ P1.1
→ P2.1 → P2.1b → P2.1c → P2.2 → P2.2b
→ P1.2 → P1.3
→ P2.3a → P2.3b → P2.3c（Grok 验收）
→ P5.1 → P3.0 → P3.1 → P3.2 → P3.3 → P3.4 → P3 其余
→ P5 → P6 → P4 → P7 → P8 → P9
```

### 小片定义

| 片 | 属于 | 范围 | 红测试 | 门槛 | 调模型 |
|---|---|---|---|---|---|
| P0 | 前置 | v1.2/v1.3 修订、独立审阅、P1/P2 共 19 条场景细化、编排基线锁定 | 无 | 已完成：基线 2133 PASS / 47 SKIP（P0 记录） | 否 |
| P1.1 | P1 契约 | `contracts/htn.py`（PlanProposal、MethodContract、MethodInstanceDraft、Binding、TaskSemanticBindingV1、Occurrence、端口、typed 关系、read-set）、`contracts/obligations.py`、`contracts/evidence_state.py`（TruthValue、Validity、前提阶段）、`contracts/resolution.py`、`knowledge/predicates.py`、`planning/htn/applicability.py` | `test_predicate_truth_table`、`test_htn_recursion_fuel`、`test_obligation_conservation`、`test_semantic_binding_codec`（goal_id 名义类型、五类版本轴） | 零 store/commit import；diff 不碰 orchestrator/、scheduling/、artifacts/ | 否 |
| P2.1 | P2 核心 | `planning/htn/{registry,grounding,refinement,compiler,validation}.py`；compiler 产出 compound entry/exit 门、ORDER→`P.exit→Q.entry`、DATA 端口、部分序；产出 ProposedPlanDelta 不提交；种子方法库；脚本化 method_proposal | `test_htn_grounding_partial_order`、`test_htn_and_or_shared_goal`、`test_htn_novel_method_admission`、`test_compound_gate_no_pseudo_cycle` | 两域只靠数据注册即可分解；event_handler.py 零改动 | 否 |
| P2.1b | P2 图层 | `graph/task_network.py`（不可变快照 + 四类关系视图）、`graph/projection_validation.py`（门、端口、环、缺边、重复 slot、规模） | `test_projection_integrity`（固定纯图反例、ID 置换不变） | 零 DB import；只对执行投影查环 | 否 |
| P2.1c | P2 图层 | `graph/eligibility.py`：纯 `evaluate_readiness` 与 `EligiblePrimitiveTask` 构造 | `test_readiness_reasons`（NEEDS_REFINEMENT / WAITING_ORDER / WAITING_DATA / WAITING_EVIDENCE / WAITING_APPROVAL / STALE_BINDING / READY_CANDIDATE） | 不接 allocator；不调账本 | 否 |
| P2.2 | P2 | `planning/htn/backends/panda.py` | 已建模样本分解见证校验 | 不可用返回显式 SOLVER_UNAVAILABLE | 否 |
| P2.2b | P2 输入 | `artifacts/input_bindings.py`：DataRequirement → BoundInput → InputManifest 纯解析 | `test_input_manifest_resolution`（单值端口唯一、集合端口有序、schema 精确匹配、PINNED/FOLLOW 策略） | `artifacts/versioning.py` 零 diff | 否 |
| P1.2 | P1 存储 | `storage/obligation_store.py`、`storage/htn_store.py`（task_semantics、method_instances、child bindings、plan_revisions、order_constraints、data_bindings、input_manifests）与下一号迁移 | store 往返 + transaction() 回滚一致性 | 新表不被旧路径读写；迁移在副本演练 | 否 |
| P1.3 | P1 接线 | `orchestrator/mission_tail_commits.py` 单路径：替代任务沿 Obligation 继承失败计数与已消费额度 | T069、T025 | 旧回归零变化；旧事件字节不变 | 否（单代理） |
| P2.3a | P2 接线 | `TaskSemanticBindingV1` 旁表、`orchestrator/plan_commits.py`（第 9 个 mixin，走 ADR-13）、`planner.py`、`role_templates.py` 标签块、`task_graph.py` v2 门 | §18.5 四条硬约束测试、T036 | 旧模式零回归 | 否（单代理） |
| P2.3b | P2 接线 | `event_handler.py` 装配新模式 list/ready/terminal 投影；`artifacts/versioning.py` 新模式走 manifest、执行路径抛 GraphIntegrityError | legacy `merge_accepted` 逐字节不变；T015、T066 的 ORDER/DATA 区分 | 单代理；诊断路径容错不变 | 否 |
| P2.3c | P2 接线 | `scheduling/allocator.py` form 门、`orchestrator/resolution_commits.py` 根验收、最小谓词观察器（code、appworld）、`synthesis.py` | 新 Mission 走 HTN 完成真实叶子 + 旧 Mission 全回归；T001 T012 T061 T063 | 旧模式零回归；§21.5 硬性不变量通过 | 是（单代理实现；**独立子代理按 §21.5 用 Grok 验收**，等 A96 跑完） |
| P5.1 | P5 | `simple_harness/agents/memory/retrieval.py` 突破 2000 条窗口 | `test_history_beyond_2000` | 旧 search_window 配置下行为逐字节不变 | 否 |
| P3.0 | P3 前置 | ADR-13 spike，复现 rebase 路径 | spike 测试（不进生产） | 审阅通过 | 否 |
| P3.1 | P3 | read-set 集合谓词、"不存在某条边/写者"的读取事实、两提案合并成环拒绝 | T016、T068 | 在当前事务状态上完整再验证 | 否 |
| P3.2 | P3 | `graph/demand.py`：DemandRef、消费者集合版本、最后消费者退出收敛 | T065 | 资金唯一归属；不退回 UNKNOWN 费用 | 否 |
| P3.3 | P3 | GraphIntegrityError 恢复、七个崩溃切点（验证后 Commit 前 / 事件投影预算中途 / Commit 后派发前 / SDK 已收未回执 / 产物已提交 Review 前 / Review 后 Resolution 前 / 方法替代但旧动作 UNKNOWN） | 每个切点一条故障注入测试 | 共同回滚或精确恢复；不重复外部动作 | 否 |
| P3.4 | P3 | compound reducer 的 Hypothesis 状态机测试，先交付参考解释器 | 随机动作序列 vs 参考模型 | 每步无环、无越权、无重复执行、预算守恒 | 否 |

并行编队：第一批 P1.1 单代理；第二批 P2.1 ∥ P2.1b ∥ P2.2b；第三批 P2.1c ∥ P2.2 ∥ P1.2；P1.3、P2.3a、P2.3b、P2.3c 串行单代理；P5.1 与 P3.0 可与 P2.3c 的 Grok 验收并行；P3.1–P3.4 之间 P3.1 ∥ P3.2，P3.3 ∥ P3.4。

P1 关闭条件 = P1.1 + P1.2 + P1.3 全部通过且 T001 T003 T006 T015 T025 T029 T036 T057 T069 有真实运行证据。P2 关闭条件 = P2.1–P2.3c 全部通过、§21.5 硬性不变量通过、附件 TG 场景 A 通过、T004 T005 T007 T008 T010 T011 T012 T061 T062 T063 有真实运行证据；§21.5 效果门槛未达只记 NO_GAIN，不阻止 P2 关闭。P3 关闭条件追加附件 TG 场景 B、C。

其后 P5、P6、P4、P7、P8、P9 按 v1.1 拓扑序推进；每包开工前先细化该包场景（§21.4）。

第一个完整交付仍是：

> 相同BaseAgent执行底座上，系统用参数化方法分解一个新目标；支持替代路线和共享子目标；根据真实前提选择/取证；完成叶子后用指定方法与要求形成可检查的根Resolution。

**不再用"先把现有代码测好"替代缺失HTN设计，也不再用"加一个HTN类"替代完整知识、预算、验收与恢复协议。**

## 24. 附件 TG：TaskGraph 专项实现约定（v1.3 新增）

两份文档作为本计划的规范附件，实现约定级：`annex/simpleharness-taskgraph-design.zh-CN.md`（设计与实现约定）与 `annex/simpleharness-taskgraph-implementation-design.zh-CN.md`（完整语义、动态修改与执行连接）。它们细化 P1/P2/P3 的图层实现，不改变 60 项需求、90 条场景、P1–P9 编号或 §23 顺序。两稿引用的是 v1.1；凡与本文 ADR 或 §23 不一致处，以本文为准。

### 24.1 采纳的裁决（v1.2 未写、实现时必然要做）

| # | 裁决 | 落点 | 包 |
|---|---|---|---|
| 1 | ORDER 只表示释放条件：`accepted`（默认）/ `settled_terminal`（仅清理/收敛合同）；UNKNOWN 不满足结算；不授予读文件与覆盖权；不自动传播内容失效 | compiler、eligibility | P2 |
| 2 | compound 编译为 entry/exit 门；门不是 Agent、不计费、不伪造 Attempt；外部 ORDER `P before Q` 编译为 `P.exit → Q.entry`；组合 Review 等孩子而不是等父 | compiler | P2.1 |
| 3 | DATA 走显式端口：DataRequirement（约定）→ 派发前 BoundInput → 不可变 InputManifest；单值端口唯一 binding、集合端口有序；schema 精确匹配或注册兼容声明；PINNED / FOLLOW_AUTHORIZED_REVISION 分开 | input_bindings、versioning v2 | P2.2b、P2.3b |
| 4 | 不再"汇入所有祖先文件"；产物身份 = (namespace/workspace, normalized-path)；同名不同 hash 必须显式选择/综合并重新验证；旧 exact-path 保护在新路径接通前不得删 | versioning、workspace | P2.3b |
| 5 | 前提检查三阶段 SELECT / MAINTAIN / ACCEPT | §6.6 | P1.1 |
| 6 | 三层 frontier（Planning / Execution / AdmittedDispatch）；`evaluate_readiness` 返回结构化原因；READY 降为可重建索引；`EligiblePrimitiveTask` 不是安全令牌，handoff 前仍复查 | eligibility、allocator | P2.1c、P2.3c |
| 7 | 五类版本轴分离；`goal_id` = 类型化 TaskRef | §6.4 | P1.1 |
| 8 | read-set 必须含集合谓词与"不存在"事实；两个提案各加一条边合并后可能成环，须在当前事务状态上完整再验证 | plan_commits | P3.1 |
| 9 | 四种去重分开（幂等重放 / slot 重复 / 相似候选 / 共享执行）；活跃共享用 DemandRef；退出分支只删自身采用关系 | demand、deduplicator | P3.2 |
| 10 | 只对执行投影查环；方法定义图可递归，已实例化细化关系不允许自祖先，证据图查无锚 SCC，资源 wait-for 另做死锁分析 | projection_validation | P2.1b |
| 11 | 拓扑不完整时执行/物化路径抛 GraphIntegrityError 并停止该 scope 派发；诊断路径（traces、evaluation）继续容错展示 | versioning、dependency_checker | P2.3b、P3.3 |
| 12 | 七个崩溃切点逐一有故障注入测试 | plan_commits、恢复 | P3.3 |

### 24.2 对附件的限定

- **ADR-13 优先**：附件"touched-task rebase 保留在 legacy"按 C19 解释；整数闸门与 rebase 代码路径不改，新模式提案不走自动重放。
- **PANDA 按 C7**：附件把形式验证列为验收方法之一，本文规定不可用时显式 unsupported、不阻塞。
- **附件 §17 三个接线目标**（类型化网络到真实执行 / 动态方法与共享成果修复 / 完整重建与产品解释）是 P2、P3、P7/P9 的终局验收目标，不是施工顺序；施工按 §23。附件中"不能标成可选优化"的措辞不改变 ADR-12 的范围变更程序。
- **compound 状态**：附件 §4.1a 把 READY 解释为"存在可采用的合法方法"，只作为 compound 的 phase 展示；旧 `TaskStatus.READY` 语义不变，拦截门是 `form`（§18.5 第 4 条）。
- **"最小影响"口径**：以实现稿 §8.3 为准，不宣称已证最小；设计稿 §16 第 3 点按此解释。
- **文件名定案**：`graph/task_network.py`、`graph/projection_validation.py`（避免与 `planning/htn/validation.py` 重名）、`graph/eligibility.py`（吸收设计稿 readiness.py）、`graph/demand.py`（P3.2）、`artifacts/input_bindings.py`；废弃 `graph/projections.py`（投影并入 task_network，UI 视图归 api/）。
- **覆盖语义的准确描述**：现有 `collect_upstream_inputs` 只取祖先的 accepted 产物；依赖链后者覆盖前者，独立分支同路径异内容为 `ArtifactConflict`。设计稿"无记录地读取全部工作区"是夸大表述，以实现稿 §2 为准。
- **Hypothesis 状态机**需要一个参考解释器交付物，成本在 P3.4 单列。

### 24.3 缺失附件（MISSING_ATTACHMENT）

附件引用的 `taskgraph-tests.json`（TG01–TG32）、`requirement-map.json`、`source-index.json` 本机不存在。处理：不重建、不引入第二套编号；附件 §14 场景 A/B/C 与实现稿 §11.3 七个崩溃切点并入 `acceptance-scenarios.json` 的现有 T 条目（`tg_annex` 字段）：A → T061/T063（P2），B → T064/T065/T066/T067（P3），C → T068（P3）与 T083/T084/T086（P7），崩溃切点 → T083/T086 与 P3.3。`source-index.json` 由实现稿 §18 的 S01–S12 表就地代替（已加入 sources.json 为 D7/D8 的引用范围）。若用户提供缺失文件，按 §21.4 规则并入，不改编号。

## 附录A：原始31章对应的最终落点

（以下由生成脚本补充，完整逐项需求在 requirements.json。）

| 原章节 | 最终工作包 | 保留与补齐方式 |
|---|---|---|
| 1. 定位与边界 | P1,P9 | D1原文定位；不要把已定义模块当成完成。 |
| 2. 目标与原则 | P1,P9 | R01–R60范围及唯一Commit、不伪造事实。 |
| 3. 总体架构 | P1,P2,P4,P6,P7 | 保留控制/状态/执行/治理，新增关系非第二权威。 |
| 4. 控制闭环 | P2,P3,P4 | 观察、细化/取证、执行、验收、Commit。 |
| 5. Mission | P1,P3,P7 | 要求、预算与长期承诺分层，不更换Mission名称。 |
| 6. Task DAG | P2,P3 | 原文动态DAG+对话新增HTN/Method/AND–OR。 |
| 7. Planner/Manager/Search | P2,P3,P4 | 计划空间frontier与分层scope。 |
| 8. Frontier/Allocator/Scheduler | P4,P8 | 资源选择与物理派发分开。 |
| 9. 角色与模型路由 | P4,P8 | 角色搜索偏置、真实路由/可训练候选。 |
| 10. Context/检索 | P5 | 有界输入，全保留历史可查；权限/版本/覆盖。 |
| 11. Blackboard/压缩 | P1,P3,P5 | 条件理由、失效、分层摘要、跨分支综合。 |
| 12. Agent生命周期 | P1,P4 | 稳定BaseAgent、可替换执行，Task/Attempt/Turn分离。 |
| 13. 结果协议 | P1,P3 | 观察/诊断/建议/真实成本与产物引用。 |
| 14. Verifier | P6 | 独立语义审阅和强制实际检查，非单一硬编码业务函数。 |
| 15. Proposal/Commit | P1,P3 | 方法、图、预算、事件一条逻辑写入。 |
| 16. State/Event/Durable | P7 | 全新模式业务投影重建；旧历史不补造。 |
| 17. 并发冲突幂等 | P1,P3,P7 | 语义read-set、generation、共享责任。 |
| 18. 预算与背压 | P1,P4,P8 | 稳定责任预算、全角色成本、实际资源压力。 |
| 19. 停止/停滞/死锁/漂移 | P3,P4 | 有限搜索燃料、反复改图检测、授权要求版本。 |
| 20. Workspace/Artifact | P6,P7 | 真实输入/输出身份、共享消费、再次验收。 |
| 21. 工具安全 | P6 | 现实时效、稳定Operation、平台能力与注入隔离。 |
| 22. 人工介入 | P3,P6,P9 | 授权/不确定性/仲裁/接管都成事件与回执。 |
| 23. 观测评估 | P8,P9 | trace、规划证据、外部结果和全角色成本。 |
| 24. 完整任务时序 | P2,P3,P6,P9 | 真实闭环、显式根Resolution、不要求所有替代分支完成。 |
| 25. 状态机 | P1,P3,P7 | 旧终态不改；当前有效性单独表达；新语义版本化。 |
| 26. 核心数据契约 | P1,P2,P3 | 扩展不是替换六个合同，typed codecs保护历史。 |
| 27. 模块组织 | P1,P2,P4,P6,P7 | 模块化单体+独立能力端口，无强制微服务。 |
| 28. 阶段路线 | P1,P2,P3,P4,P5,P6,P7,P8,P9 | 九工作包是排程，最终能力全部必达，学习不再无限推后。 |
| 29. 初始配置 | P4,P8 | 初始公式为基线，不等于最终最优；容量明确冻结。 |
| 30. 验收 | P9 | 原29条保留并扩展90场景，不以计数代替充分性。 |
| 31. 最终心智模型 | P1,P2,P3,P4,P5,P6,P7,P8,P9 | Agent探索不确定性，程序守确定性；方法与证据闭环。 |

## 附录B：60项需求逐项差距与去向

状态是本轮定向审查结论，不是加权完成百分比。R55是明确范围排除，其余59项必须有实现与适用验收。

| ID | 需求 | 来源 | 当前核查状态 | 工作包 |
|---|---|---|---|---|
| R01 | 稳定 BaseAgent 身份，多轮输出不等于关闭 | D4;对话确认 | 已有基础 | P1 |
| R02 | 单用户主Agent，子Agent弹性创建但物理并发有界 | D2对应主题;D1§18 | 部分实现 | P4 |
| R03 | Task、Attempt、AgentTurn、外部操作身份分离 | D1§12;D2对应主题 | 已有基础需扩展 | P1 |
| R04 | 复合/原子Task语义，不把抽象目标当Worker工作直接派发 | 对话HTN;D2对应主题 | 缺少一等语义 | P2 |
| R05 | 版本化参数化MethodContract与适用前提 | 对话HTN;D2对应主题 | 缺少一等语义 | P2 |
| R06 | 世界状态区分已知真/假/未知/有争议 | D1§11,14;对话HTN | 部分实现 | P1 |
| R07 | 方法生成、验证、注册、检索和淘汰闭环 | 对话HTN;D1§28 | 缺少一等语义 | P2 |
| R08 | 递归HTN与任意部分序；展开后的执行图无环 | 对话HTN;D1§6 | 缺少一等语义 | P2 |
| R09 | 按能力与风险决定直做/分解/取证/重规划 | 对话ADaPT;D1§7 | 部分实现 | P3 |
| R10 | AND子成果与OR方法路线分离 | 对话AND–OR;D1§6,7 | 缺少一等语义 | P2 |
| R11 | 共享子目标及适用性证明 | D1§6.1;D2对应主题 | 部分实现 | P2 |
| R12 | 父目标组合判据与显式GoalResolution | D1§5,24;对话composition | 缺少一等语义 | P2 |
| R13 | 结构化观察、诊断与建议分别存储 | D1§13;对话ExecutionFeedback | 部分实现 | P3 |
| R14 | 局部计划修复，尽量保留有效执行与产物 | D1§6,7;D2对应主题 | 部分实现 | P3 |
| R15 | DATA/ORDER/ASSUMPTION边分离 | D2对应主题 | 缺少一等语义 | P1 |
| R16 | 计划读集、原子激活、在途工作generation | D1§17;D2对应主题 | 部分实现 | P3 |
| R17 | 完成历史不改写，当前验收有效性可失效 | D1§25;D2对应主题 | 部分实现 | P3 |
| R18 | 真正的Plan-space搜索、frontier与可恢复搜索状态 | D1§7.3;T03 | 部分实现 | P4 |
| R19 | Best-first/Beam与有模型时的模拟lookahead | D1§7.3;T03 | 部分实现 | P4 |
| R20 | 分层Manager、独立scope与权限代次 | D1§7.3;D2对应主题 | 缺少一等语义 | P4 |
| R21 | 失败片段复用与跨方法综合再验证 | D1§11.3,20.3 | 部分实现 | P4 |
| R22 | 角色作为搜索偏置而非名称，支持多样性约束 | D1§9;T13§23 | 部分实现 | P4 |
| R23 | 真实进展、剩余不确定性与边际价值信号 | D1§18.6,19 | 启发式替代 | P8 |
| R24 | 按Task/Method分配角色、模型、候选与预算 | D1§8,9,18 | 部分实现 | P4 |
| R25 | 稳定Obligation阻止拆分/改名/换角色刷重试与预算 | D2对应主题;D1§18 | 缺少一等语义 | P1 |
| R26 | 现实Provider容量、上下文上界与预算尾部一致 | D1§18;当前契约加固 | 已有基础需扩展 | P4 |
| R27 | 验证积压触发真实背压、探索保底、抗振荡 | D1§18,19;T13§23 | 部分实现 | P4 |
| R28 | 独立语义Verifier与结构/权限检查分工 | D1§14;D2对应主题;对话Verifier | 已有基础需扩展 | P6 |
| R29 | 条件化Knowledge及多重Justification | D1§11;T02§7 | 部分实现 | P1 |
| R30 | 依赖失效贯通知识、摘要、方法和验收 | D1§11.2;D2对应主题 | 部分实现 | P3 |
| R31 | Agent短期记忆按token预算取最近完整交互 | D4,D5;对话固定Context | 已有基础需扩展 | P5 |
| R32 | 所有保留历史均可检索，不能只搜最近2000条 | D5;对话历史召回 | 部分实现 | P5 |
| R33 | 共享知识混合检索与带条件的分层摘要 | D1§10,11 | 部分实现 | P5 |
| R34 | Context按scope/用途隔离，已冻结请求不可重组 | D1§10;D4,D5 | 已有基础需扩展 | P5 |
| R35 | 父子任务交接、typed inbox、澄清与回执 | 对话子代理Context;D2对应主题 | 部分实现 | P4 |
| R36 | Proposal/Commit唯一逻辑写入 | D1§15,17 | 已有基础 | P1 |
| R37 | 所有关键业务投影可由事件与受控基线重建 | D2对应主题;D1§16 | 部分实现 | P7 |
| R38 | 重放不执行工具，恢复不复活租约或已批准旧权限 | D1§16;D2对应主题 | 已有基础需扩展 | P7 |
| R39 | execution/orchestrator双库有可靠收据与去重 | D1§16,17;D2对应主题 | 已有基础需扩展 | P7 |
| R40 | 外部Operation跨Attempt稳定、UNKNOWN先核对 | D2对应主题;D1§21 | 已有基础需扩展 | P6 |
| R41 | 补偿是独立授权任务，不是回滚模型搜索树 | T13§12;D2对应主题 | 部分实现 | P6 |
| R42 | 长期Commitment与有限ExecutionCycle | D2对应主题;对话陪伴多年 | 未在已读主链确认 | P7 |
| R43 | 持久定时、时区、错过周期策略 | D2对应主题 | 未在已读主链确认 | P7 |
| R44 | 要求版本变更与独立重新验收 | D2对应主题;对话版本变更 | 部分实现 | P3 |
| R45 | 单主助手跨Mission通知Outbox与收执 | D2对应主题;对话个人助手 | Host未核验 | P9 |
| R46 | 同一Mission内不同Task使用不同领域能力 | D1§14.2,21;T13§22 | 部分实现 | P6 |
| R47 | 实际形式化检查，不只formal_check枚举 | D1§14,20;T02 | 接口在/执行未部署 | P6 |
| R48 | 能力注册/可达/健康/授权彼此分开 | T13§4,21;近期讨论 | 部分实现 | P6 |
| R49 | Windows/Linux/macOS执行语义隔离 | 近期跨平台讨论;D1§20,21 | Host未核验 | P6 |
| R50 | 最小权限、证据来源与提示注入隔离 | D1§21,22 | 已有基础需扩展 | P6 |
| R51 | 完整Trace/贡献链/版本/费用归因 | D1§23 | 已有基础需扩展 | P8 |
| R52 | 独立外部评测而非内部Verifier自报 | D1§23.4;前次测试计划 | 已有适配基础/本轮未运行 | P9 |
| R53 | 学习型路由/分解/优先级的可训练实现与校准 | D1§28第四阶段;T13§19 | 启发式替代 | P8 |
| R54 | 策略离线评测/审批/回退，禁止在线自提升 | D1§28;D2对应主题 | 已有基础需扩展 | P8 |
| R55 | 用户长期记忆保持当前排除范围 | 用户明确暂缓 | 明确不纳入本次 | P9 |
| R56 | Workflow仍是工具层可调用能力 | 用户明确架构约定 | 设计约束 | P6 |
| R57 | 关键契约类型/边界校验/事务归属检查 | 用户提出关键契约加固 | 待与最新专项实施对接 | P1 |
| R58 | Host与UI共享协议/后端唯一状态投影 | 关键契约加固;近期TS讨论 | Host未核验 | P9 |
| R59 | 停止、反复重规划、死锁和饥饿治理 | D1§19;T13§11 | 部分实现 | P4 |
| R60 | 隐私、冷热归档、恢复manifest与删除不复活 | D2对应主题;D1§16,21 | 部分实现/Host未核验 | P7 |

## 附录C：一手研究与固定源码来源

正文[Sxx]均对应本轮固定源码；[Wxx]为外部研究/官方文档；[Dxx]/[T]为用户材料或先前计划。详尽阅读范围、hash与证据等级保存在 sources.json。

- **[S01] SDK main**：https://github.com/DennyWanye/simple-harness-sdk/commit/61a85eb7e8c003fa894497090f5de893419ebbd1；读取：Git ref
- **[S02] SDK version**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/simple_harness/version.py；读取：full
- **[S03] Planner parser**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/planning/planner.py；读取：full
- **[S04] Initial Task DAG contract**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/graph/task_graph.py；读取：1–320
- **[S05] Dynamic graph change contract**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/graph/changes.py；读取：1–300
- **[S06] System task templates and terminal selection**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/planning/manager.py；读取：full
- **[S07] Candidate selection**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/planning/candidate_selection.py；读取：full
- **[S08] Frontier and allocation**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/scheduling/allocator.py；读取：1–270
- **[S09] Blackboard retrieval**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/context/retrieval.py；读取：1–260
- **[S10] Blackboard summaries**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/context/compression.py；读取：full
- **[S11] Claim grading**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/memory/claims.py；读取：1–300
- **[S12] Knowledge contracts**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/memory/verified_knowledge.py；读取：1–220
- **[S13] Source lineage**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/memory/source_dependencies.py；读取：1–230
- **[S14] Rule learner**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/governance/learning.py；读取：1–245
- **[S15] Replay coverage**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/observability/replay.py；读取：1–260
- **[S16] Storage DDL**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/storage/schema.py；读取：1–165
- **[S17] Domain registry**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/governance/domains.py；读取：1–370 and 420–710
- **[S18] Verifier dispatch**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/verification/verifier_router.py；读取：1–390
- **[S19] Agent-scoped short-term retrieval**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/simple_harness/agents/memory/retrieval.py；读取：1–260
- **[S20] BaseAgent delegation example**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/examples/base_agent_delegation.py；读取：full; example not execution proof
- **[S21] Actual orchestration entry**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/orchestrator/event_handler.py；读取：full response retrieved; targeted prefix and management sections inspected, not a full audit of every branch
- **[S22] Product facade**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/api/facade.py；读取：1–245
- **[S23] Core contracts**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/src/agent_orchestrator/contracts/models.py；读取：1–190
- **[S24] Phase3 scope and historical results**：https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/plans/2026-09-12-phase3/HANDOFF.md；读取：1–140; newest scope block distinguished from historical log
- **[W01] HDDL / PANDA and plan validation**：https://panda-planner-dev.github.io/；读取：official page/abstract/docs；Formal task/method representation and decomposition witnesses; not a universal real-world correctness guarantee.
- **[W02] pandaPIparser**：https://github.com/panda-planner-dev/pandaPIparser；读取：official page/abstract/docs；HDDL parsing and plan validation; JSHOP2 export does not preserve arbitrary partial orders.
- **[W03] pandaPIengine**：https://github.com/panda-planner-dev/pandaPIengine；读取：official page/abstract/docs；Actual HTN solving backend; fix binary, mode and input semantics.
- **[W04] ADaPT, NAACL Findings 2024**：https://aclanthology.org/2024.findings-naacl.264/；读取：official page/abstract/docs；As-needed recursive decomposition; adapting the idea does not import benchmark gains or worker self-certification.
- **[W05] RAE/UPOM, deliberative acting**：https://arxiv.org/abs/2010.01909；读取：official page/abstract/docs；Interleaved refinement/acting; simulated model-based lookahead must remain separate from real side effects.
- **[W06] ChatHTN, PMLR 288 (2025)**：https://proceedings.mlr.press/v288/munoz-avila25a.html；读取：official page/abstract/docs；Combines symbolic HTN with LLM-proposed decompositions; formal guarantees depend on the represented model.
- **[W07] Online Learning of HTN Methods (2025 preprint)**：https://arxiv.org/abs/2511.12901；读取：official page/abstract/docs；Parameterized reusable methods; limited-domain research, not permission to promote online policies automatically.
- **[W08] AdaPlan-H, ACL Findings July 2026**：https://aclanthology.org/2026.findings-acl.77/；读取：official page/abstract/docs；Coarse-to-fine adaptive granularity; reference for refinement policy, not replacement for formal contracts.
- **[W09] HCL-GP, May 2026 preprint**：https://arxiv.org/abs/2605.06957；读取：official page/abstract/docs；Reuse parameterized policy components; evaluation/data protocol must be checked before comparing numbers.
- **[W10] Context engineering**：https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents；读取：official page/abstract/docs；Just-in-time retrieval, compaction, structured notes and scoped subagents; no universal optimal context size.
- **[W11] IPC HTN benchmark sets**：https://ipc2020.hierarchical-task.net/benchmarks/benchmarks；读取：official page/abstract/docs；Formal planning benchmark/validator; dynamic perturbations must be reported as extensions.
- **[W12] Unified Planning HierarchicalPlan**：https://unified-planning.readthedocs.io/en/v1.2.0/api/plans/HierarchicalPlan.html；读取：official page/abstract/docs；Action plan plus explicit decomposition; cited version is 1.2.0, not claimed latest.
- **[D1] agent-orchestration-layer-complete-design(1).md**：本会话已提供材料
- **[D3] agent-orchestrator-incremental-build-plan.zh-CN.md**：本会话已提供材料
- **[D4] base-agent-phase1-plan.zh-CN.md**：本会话已提供材料
- **[D5] run-dynamic-context-256k-design.zh-CN.md**：本会话已提供材料
- **[D6] mission-critical-contract-hardening-plan.zh-CN.md**：本会话已提供材料
- **[T] agent-orchestration-theory.zip**：本会话已提供材料
- **[D2] personal-agent-task-orchestration-framework-v1.zh-CN.md**：file_00000000f58c82118590c8f533b47185；Read via Files; no local byte/hash assertion. Conflicts with D1 resolved explicitly in ADRs; not silently treated as latest authority.
- **[H0] Host simple_harness**：https://github.com/DennyWanye/simple_harness；No conclusion about absence of Host functionality; actual UI file mapping is a blocking implementation gate.
- **[SVC] Service SDK main**：本会话已提供材料；No claim that this public commit is the version loaded by the current App.

**本轮没有对远端仓库写入，没有运行SimpleHarness集成、真实模型或UI测试。资料包校验只验证文件引用、需求/测试映射与示例Schema，不代表软件实现通过。**

### 本轮补充的一手工具依据

- **[W13] scikit-learn probability calibration**：https://scikit-learn.org/stable/modules/calibration.html；Use held-out calibration data; model probability is not automatically calibrated.
- **[W14] HistGradientBoostingClassifier**：https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html；Concrete candidate implementation, not a claim of superiority for this project.
- **[W15] Lean proof validation and axiom dependencies**：https://lean-lang.org/doc/reference/latest/ValidatingProofs/；Check expected theorem and permitted axiom dependencies, not compilation alone.
