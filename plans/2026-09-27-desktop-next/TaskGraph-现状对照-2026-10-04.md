# TaskGraph 现状对照（2026-10-04）

**用途**：给重写《TaskGraph 补全方案》当依据。逐条对照原始计划，查现行代码还有哪些没做、做法不同但没记录、或只在测试里。

**口径**
- 原始计划：`plans/TaskGraph/v1/simpleharness-taskgraph-code-execution-plan.zh-CN.md`（2026-09-20 代码级实施计划，下称"原计划"），条款编号沿用原计划（§5.3、附录 C 等）。
- 已定的结论不重复算缺口：旧方案 `TaskGraph-补全-方案.md`（第 2 版）第七节"接受的偏离"、`HTN补齐计划-2026-10-02.md`（第 3.23 版）第一、二节、台账 `需求与场景状态.md`、`HTN补齐-实施记录.md`。
- 用户已定口径照办：判断交给 LLM，Harness 只管约束与秩序；同一件事只留一条路径；开发期不兼容旧数据、旧路径直接删；做好的功能默认开启；暂不做的十项（多模型、两人审批、NanoJev、42 组 × 3 局、金额计价、跨领域、组级管理员、可训练学习、窗口容量对比实验、Windows / Linux 隔离）不算缺口；先写完全部代码再统一联测。
- 代码版本：worktree `simple_harness-a4`，分支 `tg-1`，与 `main` `e2d869a3` 相同（SDK opt.150）。只读代码，没有跑测试。
- 路径简写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`；`FE/` = `tauri-app/src/`。

**一句话结论**：主干（版本化结构、精确输入、原子提交、派发冻结、交接围栏、收敛、通知、只读接口、离线重建）都在产品默认路径上。真正还要写代码的集中在"共用"这一块（共用接通 → 沿用旧步骤并重审 → 跟随新版本的真实触发），外加几件小清理与错误码表第二步。验收类（42 组代表、12 个改坏、随机状态机 2000 步、真实模型四场景、独立核验）已排进联测，不属本方案。

---

## 一、总表：原计划逐条 → 现状

状态取值：**在用** / **做法不同已记录** / **做法不同未记录** / **没做** / **只在测试** / **用户定不做**。"出处"一栏写在哪份文件里记过。

### §0 范围与裁定

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| §0.1 目标：HTN 编译结果接到版本化网络 → 精确输入 → 原子提交 → 派发 → 独立验收 → 局部修复与恢复；保留 Task / Obligation / 做法实例 / 出现位置 / 先后与数据边 / 输入清单 / 计划修订 / 验收与目标结论 | 在用 | `SDK/orchestrator/plan_commits.py:355-372`；`SDK/graph/task_network.py:636`；`SDK/orchestrator/taskgraph_dispatch.py:280-367` | 各对象都在，没有另建第二套 Goal 或 Agent 生命周期 |
| §0.2 证据边界、禁止强行对齐 | 不评 | — | 过程约束，不是代码条目 |
| §0.3 三项前置完成门 | 做法不同已记录 | — | 被后来的整体验收替代（旧方案第七节第 9 条） |
| §0.3 "绑定已有目标、修复 / 补后继只解码不执行" | 做法不同已记录 | `SDK/planning/decision_adapter.py`；`SDK/planning/htn/graph_repair.py:372` | 已全部可执行（HTN 片 C，旧方案第七节第 6 条）。其中"绑定已有目标"在产品里必被拒，见 §5.3 |
| §0.4 不自动改基 | 在用 | `SDK/orchestrator/plan_commits.py:356`（`_check_plan_revision`） | 计划修订号是唯一结构闸门（HTN 阶段 D） |
| §0.4 历史结构与当前状态分开 | 在用 | `SDK/orchestrator/taskgraph_sources.py:27-98`；`SDK/api/taskgraph.py:182` | 历史读只用不可变记录与钉，当前读另带状态 |
| §0.4 只加九张扩展表 | 在用 | `SDK/storage/taskgraph_schema.py:17-224` | 没有第二份费用或操作状态表 |
| §0.4 未细化的复合目标显式挂起 | 在用 | `SDK/graph/taskgraph_validation.py:25-56` | 挂起与"没检查"分开 |
| §0.4 取消不等于结清 | 在用 | `SDK/orchestrator/taskgraph_settlement.py:25` | |
| §0.4 换做法不重发旧动作 | 在用 | `SDK/runtime/planning_operations.py`（全任务操作快照）；`SDK/orchestrator/action_commits.py:917-922` | |

### §1 可复用代码与两处静态观察

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 历史结构不能按"最新语义 + 当前做法状态"拼 | 在用（已修） | `SDK/storage/taskgraph_store.py:262-322`（`read_revision`） | 按钉读，不取最新 |
| 去掉"解析不到就给空输入清单"的回退 | 在用（已修） | `SDK/orchestrator/hierarchical_dispatch.py:2512-2522`；合法空清单只在没有声明要求时给（`:1596-1608`） | |

### §2 十六条不变量

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 第 1～4、6～9、11～16 条 | 在用 | `SDK/orchestrator/plan_commits.py:355-372`；`SDK/storage/taskgraph_schema.py`（不可改删触发器）；`SDK/orchestrator/taskgraph_dispatch.py:67-131`；`SDK/orchestrator/taskgraph_preview.py:91`；`SDK/observability/taskgraph_replay.py:157` | 与 10-02 对照结论一致，本次抽查没有发现退化 |
| 第 5 条 先后边不传文件 | 做法不同已记录 | `SDK/orchestrator/hierarchical_dispatch.py`（接续步骤） | 接续步骤会顺带交出它收到的文件（旧方案第七节第 1 条） |
| 第 10 条 共享生产者按实际需求判断 | 只在测试 | 机制：`SDK/graph/convergence.py:62`、`SDK/orchestrator/plan_commits.py:933-975`；用例 `T/full_target/test_htn_and_or_shared_goal.py` | 机制在，但产品里"共用"造不出来（见 §5.3），这条在产品上没有被走到 |

### §3 类型与来源

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| §3.1 新类型（完整读取、读取令牌、影响集） | 在用 | `SDK/graph/execution_contracts.py:275-333` | |
| §3.1 三种按用途分开的读取上下文 | 做法不同已记录 | `SDK/orchestrator/taskgraph_sources.py:27`；`SDK/orchestrator/taskgraph_plan_sources.py:70` | 做成类方法，没有"改计划读取上下文"这个同名类型（旧方案第七节第 3 条） |
| §3.2 24 个来源 | 在用 | 现行来源表 `T/acceptance_assets/seams_current.json`（一致 14、改名或挪位 8、按决定改 2、缺口 0） | HTN 阶段 F1 已逐行核过 |
| §3.2 写入目标：没有显式规则就报"写入目标规则不可用" | 做法不同未记录 | `SDK/orchestrator/hierarchical_dispatch.py:674`（`target_rules_for`） | 现行在没有固定规则时用"每步自己的工作区"这一声明过的默认策略（冻结在执行图策略里），只有读不到规则对象才报。F1 记"留到补全时核"，至今没有定为偏离 |
| §3.2 需求方：`read_active_consumers` 加对应义务 | 做法不同未记录 | `SDK/storage/taskgraph_store.py:388` | 接口还在，但生产代码一处都不调用，也没有用例；实际由本地来源读修订钉里的需求行（`SDK/orchestrator/taskgraph_execution_sources.py`）和 `SDK/orchestrator/taskgraph_demands.py`。是删除候选 |
| §3.3 四个装配函数（读结构 / 读执行 / 读改计划上下文 / 影响集 / 派发输入 / 结清事实） | 做法不同已记录 | `SDK/orchestrator/taskgraph_sources.py:64`；`SDK/graph/convergence.py:62`；`SDK/orchestrator/taskgraph_settlement.py:25`；`SDK/orchestrator/taskgraph_outcomes.py` | 改名、挪位、做成类方法，功能等价（旧方案第七节第 3 条；来源表改名表） |

### §4 内部协议

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| §4.1 网络文档编解码 | 做法不同已记录 | `SDK/graph/network_codec.py:35,379,414`；`SDK/graph/network_codec_manifest_v5.json` | 编码清单原地改写（开发期不兼容，旧方案第七节第 4 条） |
| §4.2 预览绑定，12 个来源通道各一次 | 在用 | `SDK/graph/execution_contracts.py:18-31,141-201` | |
| §4.3 视图、通知、错误三种协议 | 在用 | `SDK/graph/notification_contracts.py:92,152` | |
| §4.3 修订证书是两种的联合 | 做法不同已记录 | `SDK/graph/revision_records.py:80-109` | 只剩"普通提交"一种；老任务迁入那种已删（HTN 计划第二节） |
| §4.4 先冻结文档再求哈希、再写事件、再写记录 | 在用 | `SDK/orchestrator/taskgraph_plan_commit.py:114-157` | |

### §5 网络语义与算法

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| §5.1 执行投影（固定根、入口出口、先后与数据、悬空端点报错） | 在用 | `SDK/graph/task_network.py:636`；`SDK/graph/projection_validation.py` | |
| §5.1 已采用做法没有必需孩子 → 覆盖缺口 | 在用 | `SDK/planning/plan_preview.py:193` | |
| §5.2 部分展开 ≠ 部分检查 | 在用 | `SDK/graph/taskgraph_validation.py:15-56` | |
| §5.3 共享与槽位：类型允许复用时可以共享，规划器可以绑定已有目标 | **只在测试** | `SDK/planning/htn/grounding.py:260-300`（`may_share`，类型是"新工作"即拒）、`:689-726`；`SDK/planning/htn/registry.py:282`（类型默认"新工作"）；`Host/hierarchical.py:75-93`（桌面类型没声明允许复用）；`SDK/runtime/role_templates.py:315`（规划器"每步恰好六个键"，写不了复用策略）；`SDK/planning/htn/graph_repair.py:372`（`compile_bind_existing`，产品里必被拒）；`SDK/orchestrator/taskgraph_plan_sources.py:175-185`（共用来源只收按现行要求版本通过的验收） | 机制与函数级用例都在（`T/full_target/test_htn_and_or_shared_goal.py`），产品里走不到。HTN 阶段 D 只做了"内容步骤带固定效果身份"那一半，另一半移交本方案 |
| §5.3 取消只撤该方法自己的需求，不按旧父节点批量取消 | 在用 | `SDK/graph/convergence.py:62`；`SDK/orchestrator/plan_commits.py:364-367,933-975` | 最终取消集合用系统重算的影响目标；`_retired_children` 只用于"计划没丢成员"的核对 |
| §5.3 共享的写入目标与资源冲突 | 在用 | `SDK/planning/htn/grounding.py:745-774,824-828`；`SDK/planning/htn/compiler.py:321,1462`；`SDK/graph/projection_validation.py:805`；运行期写入冲突 `SDK/orchestrator/hierarchical_dispatch.py:249` | HTN 阶段 D 已做。一致性复核（10-04）第二节写"资源读写冲突检查没有输入"已过时 |
| §5.4 先后边按"已接受 / 已结清"放行，未知不放行 | 在用 | `SDK/orchestrator/taskgraph_settlement.py:25-97`；`SDK/graph/eligibility.py` | |
| §5.5 输入解析：缺生产者 / 等数据 / 读不到分开；歧义拒绝不选最新；合法空清单 | 在用 | `SDK/artifacts/input_bindings.py:857`、`:84`（歧义） | |
| §5.5 "跟随已授权版本"只在新绑定、新尝试前解析 | **只在测试** | 默认跟随 `SDK/planning/htn/compiler.py:748`；授权版本 `SDK/orchestrator/hierarchical_dispatch.py:1610`；钉住 `:1135`；用例 `T/product_world/test_input_revisions.py`（只验接线，上游没变） | 产品里没有"同一个已验收步骤再出一个新版本"的路径：重试只收没通过、最近一次失败的步骤（`SDK/orchestrator/planning_retry.py:33-41`），打回走一般修复、产生的是新任务。所以"跟随"和"钉住"在产品里结果没有差别 |
| §5.5 格式转换器 | 在用 | `SDK/artifacts/input_bindings.py:668,960` | 桌面格式登记表为空，只认同一格式（旧方案 2.2）；子目标端口格式不一致不出别名（HTN 阶段 D） |
| §5.6 局部影响分析 | 做法不同已记录 | `SDK/graph/convergence.py:152-173` | 改为"屏障 + 使用前重验"（用户 09-30，旧方案第七节第 5 条） |

### §6 数据模型

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| §6.1 / §6.2 九张新表与唯一写入口 | 在用 | `SDK/storage/taskgraph_schema.py:17-224`；`SDK/storage/schema.py:631-660`（迁移 34） | |
| §6.3 存储接口清单 | 在用（一处未记录） | `SDK/storage/taskgraph_store.py`、`taskgraph_attempt_inputs.py`、`taskgraph_convergence.py`、`taskgraph_followups.py` | 拆成四个存储类（已记录）；`read_active_consumers` 不用（见 §3.2） |
| §6.4 程序核对、迁移号、同事务 | 在用 | 同上 | 升级迁移不测（旧方案第七节第 12 条，开发期不兼容） |
| §6.5 显式启用、老任务迁入基线 | 做法不同已记录 | `SDK/orchestrator/taskgraph_policy.py:108`；`SDK/storage/schema.py:631`（迁移 34 删迁入分支） | 建任务即绑定执行图；迁入基线整删（HTN 计划第二节、阶段 A，提交 `e053685a`） |
| §6.5 "默认旧路径不写这九表" | 做法不同未记录 | `SDK/storage/taskgraph_store.py:46-59`（`taskgraph_enabled`）；调用处 `SDK/orchestrator/event_handler.py:1538,1685,2048`、`SDK/orchestrator/accounting_recovery.py:261`、`SDK/orchestrator/taskgraph_action_settlement.py:43`、`SDK/deployment/duties.py:245`、`SDK/api/taskgraph.py:177` | 所有新任务都绑执行图后，这 9 处"有没有绑定执行图"的判断只为开发库里的老任务服务（按名停掉、拒读、跳过）。HTN 阶段 B 补记"逐处归类留到 TaskGraph 补全"，还没做 |

### §7 预览与原子改图

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| §7.1 只经原有决定管线进入，不开裸改边接口 | 在用 | `SDK/orchestrator/taskgraph_preview.py:91`；`SDK/orchestrator/taskgraph_candidate.py` | |
| §7.2 提交顺序（同一写事务） | 在用 | `SDK/orchestrator/planning_admission_commits.py:142,259`；`SDK/orchestrator/plan_commits.py:355-372`；`SDK/orchestrator/taskgraph_plan_commit.py:114-157` | |
| §7.3 无关改图不作废在途尝试 | 在用 | `SDK/orchestrator/taskgraph_dispatch.py:77-131` | 尝试按自己的合同 / 输入 / 代次比 |
| §7.4 回执丢失按原命令号取原回执 | 在用 | `T/full_target/taskgraph_exec/test_process_recovery.py` | |

### §8 派发、冻结、并发、结果

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| §8.1 派发前短事务重读；冻结输入与派发意图同事务；按清单物化 | 在用 | `SDK/orchestrator/taskgraph_dispatch.py:280-367`；`SDK/orchestrator/taskgraph_materialization.py:22-51` | |
| §8.2 恢复用冻结记录、不重新解析最新 | 在用 | `SDK/orchestrator/taskgraph_dispatch.py:77-131`；`SDK/orchestrator/taskgraph_resume.py:34` | |
| §8.3 交接围栏（模型调用 / 工具 / 外部动作） | 在用 | `SDK/orchestrator/taskgraph_dispatch.py:67-75`；`SDK/runtime/provider_budget_guard.py:185`；`SDK/runtime/tool_gateway.py:774-782`；`SDK/orchestrator/action_commits.py:917-922` | |
| §8.4 审阅按冻结输入；组合审阅；根收尾 | 在用 | `SDK/orchestrator/taskgraph_review.py:24`；`SDK/orchestrator/composition_review.py` | |

### §9 在跑换做法

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| §9.1 收敛状态机 | 在用 | `SDK/storage/taskgraph_convergence.py:69`；库层转换触发器 `SDK/storage/taskgraph_schema.py` | |
| §9.1 放弃（"操作者 / 系统规则批准"） | 做法不同已记录 | `SDK/orchestrator/taskgraph_operator.py:91`；`Host/taskgraph.py:102-131` | 只认用户在界面上的真实点击（旧方案第 1 批，HTN 阶段 B） |
| §9.2 先立围栏、不先改正式计划 | 在用 | `SDK/orchestrator/taskgraph_preview.py:252` | |
| §9.3 最终提交只取消真实要退休的目标，核对系统重算 | 在用 | `SDK/orchestrator/plan_commits.py:364-367,933` | |
| §9.4 已生效的外部效果不被遗忘；补偿 | 在用 / 做法不同已记录 | `SDK/runtime/planning_operations.py` | 补偿入口删除（HTN 计划第二节表一 13） |

### §10 事件、通知、重建

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| §10.1 修订已记录、启用、收敛已请求 / 已推进、派发已绑定五种事件 | 在用 | `SDK/orchestrator/taskgraph_plan_commit.py:140`；`SDK/orchestrator/taskgraph_policy.py:157`；`SDK/orchestrator/taskgraph_preview.py:252`；`SDK/storage/taskgraph_convergence.py:352`；`SDK/orchestrator/taskgraph_dispatch.py:363` | 后两种在 HTN 阶段 A 补上（`e053685a`） |
| §10.2 三类通知、先有下游回执再确认、退避 1/2/4/8 秒、第 5 次挡住、租约 30 秒 | 在用 | `SDK/storage/taskgraph_followups.py:111-302`；`SDK/orchestrator/taskgraph_followups.py:54-56`；`SDK/orchestrator/taskgraph_notifications.py:184-195` | 被挡通知可见、人点"重新发送"（HTN 阶段 B） |
| §10.2 监听来源含"计划修订已提交" | 做法不同未记录 | `SDK/orchestrator/taskgraph_notifications.py:23-38`（重查事件集里没有 `PlanRevisionCommitted`，也没有 `TaskGraphRevisionRecorded`） | 影响小：主循环每轮本来就重读；但与原计划不一致且没有记录。另：同文件 `:80` 注释还写着已删的"迁入基线" |
| §10.3 离线图重建 | 在用 | `SDK/observability/taskgraph_replay.py:157`；`Host/diagnostics.py:396-441` | 接进诊断导出（HTN 阶段 B）；起点只剩首份计划（已记录） |

### §11 只读接口与界面

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 四个只读方法：快照、为什么没开工、差异、收敛 | 在用 | `SDK/api/taskgraph.py:182,425,448,463-492`；`Host/taskgraph.py:25-77` | 收敛视图含被挡通知（`:485-492`） |
| 只做有界全量快照，不开放翻页参数 | 做法不同已记录 | `SDK/api/taskgraph.py:333-365`（`execution_snapshot` 可翻页） | 用整体哈希钉住，不拼接（旧方案第七节第 11 条） |
| 历史与差异进界面 | 做法不同已记录 | `Host/diagnostics.py:396-441` | 只进诊断导出（旧方案第七节第 13 条） |
| 界面"为什么还不开工"、改计划进度、读失败保留旧画面标过期 | 在用 | `FE/views/PlanChangePanel.tsx`；`FE/views/liveGraph/LiveGraph.tsx` | |

### §12 错误分类

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 跨边界码按九类与优先级归类，未知码拒绝 | 在用 | `SDK/contracts/error_table.py` | HTN 阶段 A 框架（规划器拒绝码 + 执行图对外 7 个码） |
| 内部守卫码归类 | 没做 | 约 313 个 `TASKGRAPH_*` 字符串码散在 `SDK/` 各处 | 旧方案第 4 批第 1 条"第二步"，HTN 计划没排 |
| "不按异常文本包含做分类" | 做法不同未记录 | `SDK/orchestrator/failure_classes.py:158-175`（`classify_round_fault` 用 `code in str(error)` 判"数据损坏 / 重试"） | 一轮故障的分类是一张独立的字符串表，按异常文本包含判；和错误码表是同一件事的第二份声明 |

### §13～§16 施工、验收、完成关口

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| §13 逐文件施工 | 在用 | 模块名有变，见来源表；`planning/repair/impact.py` 在 `SDK/orchestrator/repair_impact.py` | |
| §14 施工顺序 | 不评 | — | 过程 |
| §15 42 组真实 SDK 场景 | 用户定不做（原样） | — | 只做六组代表 + 12 个改坏绑定用例，排在联测（HTN 计划第一节、F2） |
| §15 12 个定点改坏 | 没做（已排联测） | `T/acceptance_assets/mutations.json`（12 条占位，文件与用例为空） | HTN 阶段 F1 已备执行器 `scripts/acceptance/run_mutations.py`；"共用保留"那条依赖本方案的共用接通 |
| §15 随机状态机 | 做法不同已记录 | `T/product_world/random_sequences.py` | 固定种子、不用 Hypothesis（HTN 计划第二节阶段 F）；2000 步在联测 |
| §15 真实模型四场景 | 做法不同已记录；没跑 | — | DeepSeek、各 1 局（已记录）；"分支共享并换做法"依赖本方案共用接通 |
| §15 规模边界 | 没做（已排联测） | `SDK/graph/projection_validation.py:438-448`（超限报界） | |
| §16 来源表齐全 | 在用 | `T/acceptance_assets/seams_current.json`、守护 `T/acceptance_assets/test_acceptance_assets.py` | 联测开头按本方案改动刷新 |
| §16 数据表、核心、真实模型、Host 接口、独立核验 | 没做（已排联测） | `SDK/orchestrator/taskgraph_deployment_manifest.json`（`taskgraph_acceptance: NOT_RUN`） | |

### 附录

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 附录 A 新增表结构与触发器 | 在用 | `SDK/storage/taskgraph_schema.py`；迁移 34 改写两个守卫触发器 | |
| 附录 B 读取与比较交换查询 | 在用 | 各存储类 | 需求方查询只被不用的 `read_active_consumers` 使用 |
| 附录 C §1～§8（来源分类、启用、读写修订、派发绑定、收敛、通知、快照差异重建、数字与上限） | 在用 / 做法不同已记录 | `SDK/graph/network_codec.py:35`（文档上限 16 MiB）；`SDK/storage/taskgraph_followups.py:150,292-302` | 启用改为建任务即绑定（已记录） |
| 附录 D 42 组 | 同 §15 | — | |
| 附录 E 协议格式 | 在用 | `SDK/graph/execution_contracts.py`；`SDK/graph/notification_contracts.py` | |

---

## 二、还要做的

只列"没做 / 做法不同未记录 / 只在测试"，以及 HTN 计划与台账明写移交本方案的条目。共 **11 条：大 2、中 2、小 7**。验收执行类（12 个改坏、六组代表、随机状态机 2000 步、真实模型、规模、独立核验、部署验收门刷新）按 HTN 计划归联测，不在本清单，只在第 2.4 节列出依赖。

### 2.1 大

**甲. 共用（共享子目标 / 共用步骤）在产品里接通**
- **原计划要求**：§5.3 按共享签名与槽位共享，一个分支退出只撤它自己的需求，共享生产者不被取消（不变量第 10 条）；规划器可以绑定已有目标的成果。
- **出处**：HTN 计划表二第 2 条"D（一半）／TaskGraph 补全"；台账"几个目标共用的子成果只做一次"一条为"计划中"、两个场景（共用只做一次、一个分支退出不重做）没有用例；`HTN补齐-阶段E-偏差裁决-沿用旧步骤.md`；一致性复核第二节"共享前置只扣一次、共享工作由谁出钱"。
- **现在缺什么**：
  1. 桌面步骤类型仍是"新工作"（`Host/hierarchical.py:75-93`，产品同形世界 `SDK/testing/product_world.py` 同一份），`may_share` 一律拒（`SDK/planning/htn/grounding.py:294`）；"绑定已有目标"（`SDK/planning/htn/graph_repair.py:372`）同样必被拒。
  2. 规划器每步只能写六个键（`SDK/runtime/role_templates.py:315`），写不了步骤级复用策略；合同里步骤已经有可选的 `reuse_policy`（`SDK/contracts/htn.py:725`），只是提示词没开放。
  3. 共用来源只收"按现行要求版本通过"的验收（`SDK/orchestrator/taskgraph_plan_sources.py:175-185`）；规划包的共用候选要只列真能用的。
  4. 共享工作的费用归属与前置只扣一次没有定。
  5. 字面相似的共享建议 `SharedGoalIndex.suggest`（`SDK/planning/htn/grounding.py:383-405`）没有调用方，属于按字面判相似，应删。
- **建议做法**：见第四节第 1 条——共用只在规划器显式声明时发生，系统只做签名一致与秩序核对；"这一步负责写出的文件"进共用签名，作为核对条件。
- **大概改哪些文件**：`Host/hierarchical.py`、`SDK/testing/product_world.py`、`SDK/planning/htn/grounding.py`、`SDK/planning/htn/registry.py`、`SDK/planning/htn/graph_repair.py`、`SDK/graph/taskgraph_sharing.py`、`SDK/orchestrator/taskgraph_plan_sources.py`、`SDK/orchestrator/planner_views.py`、`SDK/runtime/role_templates.py`（规划器提示词升版）、费用归属在 `SDK/orchestrator/obligation_accounts.py` 与提交预算核对处。
- **随代码写的用例**：两个分支共用一个会写文件的步骤、其中一个分支换做法，共用的步骤不被取消、费用不重复（联测必须补的两条之一）；会写文件的内容步骤共用后不被判成"只读步骤还在写"；一条改坏检验。
- **注意**：改桌面类型声明会改类型哈希，全库做法按类型目录哈希会自动不再列出；改提示词会让已有执行池起不来（真机新建任务）。若要改 `SDK/contracts/htn.py`，要重写编码清单哈希，尽量和乙合并成一次。
- **大小**：大。**依赖**：无前置；乙、丙都依赖它。

**乙. 改要求后沿用已验收的步骤，审阅员按新要求重审**
- **原计划要求**：§8.4 审阅按冻结输入、§5.6 末段"需求变化要有授权的要求修订"；旧方案第 3 批第 5 条"保留还是重做由规划器判断"。
- **出处**：HTN 计划表二第 11 条（"沿用 + 审阅员重审移交 TaskGraph 补全"）、第 3.14 版；实施记录阶段 E 偏差单 10；旧方案 2.4 补第 2、3 条。
- **现在缺什么**：改要求后，已通过的步骤带不进新计划，新计划需要的一律重做（`SDK/orchestrator/taskgraph_plan_sources.py:175-185` 按要求版本过滤；已完成的叶子没有目标结论，"绑定已有目标"认不了它）。
- **要做**：规划器在新做法里显式共用旧步骤 → 系统对同一份结果按新范围切一份内容审查包交审阅员（不重跑）→ 通过写一条按新版的验收；没过记一条修复请求（暂名"沿用结果被拒"，带不通过条目）交规划器 → 每步每个要求版本最多重审一次；子目标沿用时组合审查一律重新交审阅员判（见第四节第 3 条）；规划器提示词写回"想沿用就在新做法里共用它，系统会请审阅员按新要求重审"。新事件与新拒绝码当批登记进全业务重放覆盖清单与错误码表。
- **大概改哪些文件**：`SDK/orchestrator/taskgraph_plan_sources.py`、`SDK/planning/htn/graph_repair.py`、保证通道切包与验收入口（`SDK/orchestrator/assurance_*`、`SDK/orchestrator/commit_service.py`）、`SDK/orchestrator/planning_repair_requests.py`、`SDK/orchestrator/planner_views.py`、`SDK/runtime/role_templates.py`、`SDK/observability/business_replay_inventory.json`、`SDK/contracts/error_table.py`。
- **用例**：改要求后新根做法共用旧的已验收步骤，那一步没有新尝试、按新版多一份审查包与一条验收、任务完成；变体：重审打回 → 修复请求到规划器 → 换做法后完成。各一条改坏检验。
- **大小**：大。**依赖**：甲。

### 2.2 中

**丙. "跟随已授权新版本"在产品里有真实触发路径**
- **原计划要求**：§5.5 跟随已授权版本只在新绑定、新尝试前解析，已冻结的不跟。
- **出处**：实施记录 F1-0 第 1 条、台账"改计划时核对读过的东西没变……"一条（"跟随暂无触发路径，移交 TaskGraph 补全"）；改坏清单里 F1 第 6 号空缺即此用例。
- **现在缺什么**：同一个已验收步骤不能再出第二个版本（`SDK/orchestrator/planning_retry.py:33-35` 拒绝已有接受结果的步骤）。**还要注意**：乙的"重审通过"只给同一份产物多写一条验收，产物版本不变，下游"跟过去"拿到的仍是同一份字节，测不出跟随。真正的触发是乙的"重审没过"：这一步按新要求不算数后，要允许同一步重做出新产物（新版本、新验收），下游的新尝试才会跟到新版本。
- **要做**：定下"按新要求重审没过的步骤"如何回到可重做状态（建议：由规划器用"原步骤再做一次"表达，系统只核秩序——旧验收在新版本下已不算数、没有在途尝试）；写"上游重做出第二个通过版本后下游跟过去"的功能用例（联测必须补的两条之二），并补改坏清单空缺号。
- **大概改哪些文件**：`SDK/orchestrator/planning_retry.py`、`SDK/orchestrator/hierarchical_dispatch.py`（`accepted_outputs` 已授权版本）、`T/product_world/test_input_revisions.py`、`T/acceptance_assets/mutations.json`。
- **大小**：中。**依赖**：乙。

**丁. 错误码表第二步，一轮故障改按码分类**
- **原计划要求**：§12 全部码按类型归类、全集测试、未知码拒绝；不按异常文本包含做分类。
- **出处**：旧方案第 4 批第 1 条第二步（"内部守卫码以后再归"）；`SDK/contracts/error_table.py` 文件头。
- **现在缺什么**：约 313 个内部 `TASKGRAPH_*` 码没归类；`SDK/orchestrator/failure_classes.py:158-175` 用异常文本包含判"数据损坏当轮停 / 原地重试"，是错误码表之外的第二份声明。
- **要做**：先列清单；只归"决定秩序"的码（在哪停、能不能重试、算不算规划器答错），给错误码表加"一轮故障怎么处理"一列并让 `classify_round_fault` 只读这一列；本方案甲、乙新增的码直接登记。纯内部、不改变任何秩序的码可以不归，写明理由。
- **大概改哪些文件**：`SDK/contracts/error_table.py`、`SDK/orchestrator/failure_classes.py`、抛出这些码的存储与编排模块（只改成抛带类型的码，不改判断）、`T/` 下错误码表用例。
- **大小**：中。**依赖**：无；建议排在甲、乙之前，新码直接用。

### 2.3 小

| 编号 | 事项 | 原计划要求 / 出处 | 现在缺什么 | 大概改哪些文件 | 依赖 |
|---|---|---|---|---|---|
| 戊 | 逐步骤的预算去向 | HTN 计划第 3.15 版"逐步骤去向归 TaskGraph 补全"；台账"同一件责任……不重置""完整追踪……花费归因"两条 | 桌面步骤都写"细化上级义务"，挂在同一个义务下，"预算去向"通常只有"整个任务"一行（`SDK/orchestrator/obligation_accounts.py:20-79`） | 读时推出：在义务行下按步骤（任务）再分一层，不改义务结构；`SDK/orchestrator/obligation_accounts.py`、`SDK/api/facade.py`（`budget_by_duty`）、`FE/views/MissionsView.tsx` 的 `BudgetByDuty`、`Host/diagnostics.py`（`DUTY_FIELDS`） | 共享工作的费用归属随甲定，戊在甲之后做 |
| 己 | 试用次数恒为 0 | HTN 计划第 3.17 版"移交 TaskGraph 补全一并定去向" | `SDK/planning/htn/registry.py:1254`（`note_trial_use`）没有调用方，`trial_uses` 恒 0 却进了规划来源摘要（`SDK/orchestrator/taskgraph_plan_sources.py:163`） | 按"旧路径直接删"删掉计数、字段与来源摘要里的这一项；`SDK/planning/htn/registry.py`、`SDK/orchestrator/taskgraph_plan_sources.py` | 无 |
| 庚 | 删不用的需求方读取接口 | 原计划 §6.3 接口清单；实施记录 F1-4 "删除候选" | `SDK/storage/taskgraph_store.py:388` 生产与测试都不调用 | 删接口；在来源表里把这一行的生产方写成现行两处；记为与 §6.3 的偏离 | 无 |
| 辛 | 写入目标默认规则定性 | 原计划 §3.2 / 附录 C §1.1；实施记录 F1-4 "留 TaskGraph 补全时核" | 没有显式规则时用"每步自己的工作区"默认策略，与原计划"无规则即不可用"不同，至今未定 | 核实默认策略冻结在执行图策略里、读不到才报错，然后记为偏离（这是秩序，不需要改代码）；只在核出问题时改 `SDK/orchestrator/hierarchical_dispatch.py:674` | 无 |
| 壬 | 删"有没有绑定执行图"的残留判断 | 开发期不兼容规则；实施记录阶段 B 补记"逐处归类留到 TaskGraph 补全" | 9 处调用 `taskgraph_enabled`（见总表 §6.5 一行），只为开发库老任务服务 | 逐处归类：为老任务开的分支删；真需要的改成"读不到绑定 = 数据损坏"按名报错；顺手改 `SDK/orchestrator/taskgraph_notifications.py:80` 过时注释 | 无 |
| 癸 | 重查事件集与原计划对齐 | 原计划 §10.2 监听来源含"计划修订已提交" | `SDK/orchestrator/taskgraph_notifications.py:23-38` 没有 | 二选一：加进重查事件集（注意不要自己触发自己），或记为偏离并写明理由（主循环每轮已重读） | 无 |
| 子 | 删字面相似的共享建议 | 原计划 §5.3"相似只产生候选"；核心思想"关键词匹配不写" | `SharedGoalIndex.suggest` 与它用的 `statement_similarity`（`SDK/planning/htn/grounding.py:383-405`）没有调用方 | 删 `suggest`；`statement_similarity` 若只剩做法提议那一处在用，交 HTN 侧判（见第四节第 2 条） | 随甲一起做 |

### 2.4 与联测（不在本方案）的衔接

- 12 个定点改坏的绑定用例与执行、六组代表、数据表直接测试、安全层失败断言、规模边界、随机状态机 2000 步、真实模型四场景、部署验收门刷新、独立核验——都按 HTN 计划 F2 做，本方案不再单列（HTN 计划阶段 F2 第 1 条："补全方案第 5 批即本节"）。
- 本方案对联测的输入：甲、乙、丙写完后，按改过的地方刷新来源表（`T/acceptance_assets/seams_current.json`）与改坏清单（`T/acceptance_assets/mutations.json`，"共用保留"那条和 F1 第 6 号空缺）。
- 真实模型场景"分支共享并换做法"依赖甲；"输入失效后重新规划"依赖已做的输入跟随（资料换版本走界面）。

### 2.5 建议顺序

丁（错误码表第二步，先立好新码的归处）→ 己、庚、辛、壬、癸（小清理，可并行，互不依赖）→ 甲（含子）→ 乙 → 丙 → 戊。提示词升版尽量合在甲、乙同一版；改 `SDK/contracts/htn.py` 的地方合成一次，编码清单哈希只重写一次。

---

## 三、旧方案（第 2 版）各批的去向

| 批次 | 内容 | 现状 | 在哪个阶段、哪个提交或版本 |
|---|---|---|---|
| 第 1 批 看得到、救得回 | 卡住原因与出口、放弃事实进规划请求、被挡通知读接口、两个操作动词（只认真实点击）、界面两样、读失败标过期、诊断导出加历史重建 | **已做完**（含真机点击） | HTN 阶段 B：SDK opt.142（提交 `dd5c5a97`，Host 钉版 `6f2bc750`），收尾 opt.143（`6451d008`）；真机证据 `.local-test-evidence/2026-10-03/stuck-panel/` |
| 第 2 批 2.1 已授权版本、默认跟随、声明钉住 | | **代码做完**；产品里没有触发路径（本文丙） | HTN 阶段 D：opt.145（`46f0386c`，Host `5278851a`）。"共享校验对照实际策略"改为不再比较版本策略，已登记（阶段 D 评估第 5 条） |
| 第 2 批 2.2 子目标端口格式核对 | | **已做完** | 阶段 D（同上） |
| 第 2 批 2.3 同名写入：定计划查声明、运行期写入冲突交规划器 | | **已做完** | 阶段 D（同上） |
| 第 2 批 2.4 共用可用 | 内容步骤带固定效果身份 | **一半做完** | 阶段 D：效果身份 `desktop.attempt-workspace`。类型允许复用、规划器能表达共用**没做** → 本文甲 |
| 第 2 批 2.4 补 | 共用签名带写入目标、只列能用的候选、沿用 + 重审、提示词写回 | **没做** | 阶段 E 偏差单 10 裁决移交 → 本文甲、乙 |
| 第 3 批 中途改要求 | 授权入口、一次事务四样、准则单一来源与守护测试、屏障、旧验收整版失效、"要求已更新"进重查事件集、规划器提的修订不受理 | **已做完**；"保留旧结果"那一半随沿用移交 | HTN 阶段 E：opt.146（`4b67ba20`，Host `ce7dd7d4`）。入口只在主 Agent（`mission_amend`），与旧方案"界面任务详情也能发起"不同，已记（阶段 E 开工裁决） |
| 第 4 批 第 1 条 错误码表 | 第一步：跨边界码 | **第一步做完**；第二步没做（本文丁） | HTN 阶段 A：提交 `e053685a`，随 SDK opt.135（`c2cef312`）发布 |
| 第 4 批 第 2 条 两种事件 | 派发已绑定、收敛已推进，不进重查事件集 | **已做完** | 阶段 A，`e053685a` |
| 第 4 批 第 3 条 删老任务迁入基线 | | **已做完** | 阶段 A，`e053685a`（迁移 34） |
| 第 5 批 验收资产与独立核验 | 来源表、改坏、数据表测试、六组代表、安全断言、规模、随机状态机、部署门、真实模型、独立核验 | **资产已备、执行没做** | HTN 阶段 F1：opt.148（`e92d86d1`）——来源表、崩溃切点 18 行、改坏清单与执行器、随机动作驱动。执行归联测（F2），重写方案不再单列 |
| 第 6 批 黑板可取用 | 知识进库与依据、三层读工具、审阅员看相关条目、用过的知识记版本、提示词升版 | **已做完**；摘要层也已开放 | HTN 阶段 C：opt.144（`0fae203c`，Host `e31ba3b1`）；摘要层在阶段 C3：opt.147（`2c865fe3`）。做法与旧方案不同处（审阅员不给读工具、相关条目进审查包、过时读时判定、`memory/blackboard.py` 删）已记入 HTN 计划第 3.7 版 |
| 第七节 接受的偏离 13 条 | | 继续有效 | 其中第 9 条（前置门禁被整体验收替代）、第 12 条（升级迁移不测）以外均有代码对应，本次抽查一致 |

**还剩的只有**：2.4 后一半与 2.4 补（甲、乙）、2.1 的真实触发（丙）、第 4 批第 1 条第二步（丁）；第 5 批的执行在联测。

---

## 四、"判断交给 LLM"对照：照做会让 Harness 替 LLM 判语义的条目

建议都记为偏离，不照做。

1. **按共用签名自动合并（原计划 §5.3，现行 `SDK/planning/htn/grounding.py:689-726`）**。现行实现：只要类型允许复用、两步的共用签名（类型、参数、输入版本、范围等）相同，系统在编译时就把新步骤自动绑到已有步骤上，规划器没说要共用也会并。"这两步是不是同一件工作"是语义判断——旧方案 2.4 补第 1 条要"把负责写出的文件放进签名，免得目标原话相同、负责文件不同的两步被并成一步"，正是在给自动合并打补丁。**建议**：共用只在规划器显式声明时发生（步骤上写复用策略，或用"绑定已有目标"）；签名一致、写入目标一致、先后与前提成立只作为秩序核对，不一致就拒绝并如实告诉规划器；类型默认保持"新工作"。记为与原计划"类型允许复用即可去重"的偏离。
2. **按字面相似给共享或做法建议**。`SharedGoalIndex.suggest`（`SDK/planning/htn/grounding.py:383-405`）按目标类型名的字面相似度列"看起来相关"的共享候选，没有调用方——删（本文"子"）。顺带发现（不属原计划，交 HTN 侧判）：`SDK/planning/htn/registry.py:1318` `suggest_for` 同样按字面相似，经 `SDK/planning/htn/method_proposals.py:512-524` 以"建议的做法"进规划器的提做法上下文，和阶段 C3 定的"不做按字面重合度的推荐"不一致。
3. **沿用后"组合审查要不要按新版重做"由程序判**（旧方案 2.4 补第 2 条"子目标沿用时组合审查是否自动按新版重做，先核再定"）。如果由程序按"子结果没变"决定旧的组合结论继续算数，就是程序在判"整体还合不合新要求"。**建议**：一律交审阅员按新要求判（新切一份组合审查包），程序只管切包、次数上限和"要求版本对不上就不算数"。
4. **局部影响分析由程序算最小影响（原计划 §5.6）**。已按用户 09-30 决定改为"屏障 + 使用前重验"（已记录），不要在补全时再加回"程序判哪些不受影响"。
5. **放弃改计划可由"系统规则批准"（原计划 §9.1）**。是否放弃一次改计划、恢复旧需求还是保持撤回，属于取舍判断。现行只认用户点击（已做）；**建议**把"不设系统规则自动放弃"正式写成偏离，补全时不加按超时、按次数自动放弃的规则（超时只能如实交规划器或问人）。

另：`SDK/orchestrator/failure_classes.py:158-175` 按异常文本包含分类不是语义判断，但属于原计划 §12 明令不用的"按文本包含"做法，已列入本文"丁"。
