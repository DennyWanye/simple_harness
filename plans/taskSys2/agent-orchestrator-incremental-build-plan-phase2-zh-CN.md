# 基础 Agent 已完成之后：Agent 编排框架逐步建设计划

版本：ORCH-BUILD-v1.0  
日期：2026-09-10  
设计依据：用户上传《Agent 编排层完整设计方案》（下称“原文”）  
代码核查基线：`DennyWanye/simple-harness-sdk@dffd13c829d42ac3df213ac0c490f3d5834a8b2b`，源码版本 `0.8.0`  
状态：实施计划；尚未实施本文新增编排模块，尚未执行本文验收测试。

## 0. 先明确交付边界

本计划接受“第一步 BaseAgent 已经完成”作为项目基线，不重新开发基础 Agent，不重新设计它的短期记忆，不把第一步完成等同于原文第 28 章的“可靠单 Manager 系统”已完成。

本轮实际读取了当前 main 引用，以及 `agents/base.py`、`runtime.py`、`contracts.py`、`config.py`、`ports.py`、`version.py` 和 `pyproject.toml` 等关键代码。确认新 `agents/` 已存在，且主版本已从之前讨论的 0.7.10 更新到 0.8.0。没有对当前 BaseAgent 做全量复验；下面的接入测试是新编排功能的回归门槛，不是要求重做第一步。

Host 仓库 `DennyWanye/simple_harness` 本轮读取仍返回 404。本文因此以已可读取的 SDK 公共接口为接入面，不编造 Host 现有文件名。第一版通过新编排包的 Python API/CLI 交付；进入产品 UI/Host 接线时，应把真实 Host commit 和装配位置登记到实施记录中。

“100% 按原文”的落实方式：保留原文术语、四阶段顺序、状态机、Proposal/Commit、验证层次及最终验收范围；每一阶段给出原文章节映射。本文的函数名、数据库表名、演示样例和阶段内实现顺序是工程落点，不是声称原文已经给出的内容。原文未定义的协议细节列入明确的实施约定，不偷偷引入另一套理论或框架。

不能把尚未编写、测试和部署的软件承诺为“100% 无故障”。本文改用可执行门槛：每步必须交付可运行完整功能、自动化回归、真实 Agent 演示和失败证据；达不到本步验收，就不进入下一步。最后阶段的学习策略也不承诺一定优于规则策略，不通过评测则不晋级。

## 1. 不改变的架构与名词

### 1.1 按原文保留的对象

| 对象 | 责任 | 第一阶段后的处理方式 |
|---|---|---|
| Mission | 根目标、成功条件、预算、权限、风险和停止条件 | 新增编排领域对象 |
| Task DAG / Task Contract | 工作目标、父任务、依赖、验收和版本 | 新增；不塞进 BaseAgent 短期记忆作为权威状态 |
| Attempt | 对 Task 的一次具体尝试 | 新增；不等于 BaseAgent 生命周期，也不等于每次模型调用 |
| BaseAgent / AgentTurn | 执行者及其本轮执行 | 复用已完成代码 |
| Result Envelope | Claim、证据、Artifact、失败、子任务建议与费用 | 将 AgentTurnResult 适配为它；不是直接改名 |
| Claim / Verified Knowledge | 候选判断与正式知识 | 按验证等级分开；不引入用户记忆 SDK |
| Proposal / Commit | 模型建议与正式变更的分界 | 所有正式编排变更走 Commit Service |
| Event / Current State | 历史与当前查询状态 | 编排层持久化；不替代 SDK 执行账本 |

不新增 TaskProgram 来代替 Mission；不把 MethodContract、AND–OR 图、ADaPT、MCTS 等上一轮讨论的扩展作为必需前置。本计划中的动态修改仍然是原文的 Task DAG Proposal → 检查 → Commit。

### 1.2 沿用原文的模块化单体

建议在当前 SDK 仓库新增独立 Python 包 `src/agent_orchestrator/`，目录职责按原文第 27 章组织。不要求现在新建第五个仓库，也不要求先部署大量微服务。

```text
src/agent_orchestrator/
  api/                  missions.py, tasks.py, approvals.py
  orchestrator/         event_handler.py, state_machine.py, commit_service.py
  planning/             planner.py, manager.py, search_controller.py
  graph/                task_graph.py, dependency_checker.py, deduplicator.py
  scheduling/           allocator.py, scheduler.py, leases.py, backpressure.py
  runtime/              agent_worker.py, model_router.py, role_templates.py, tool_gateway.py
  context/              context_builder.py, retrieval.py, compression.py
  memory/               blackboard.py, claims.py, verified_knowledge.py, summaries.py
  verification/         verifier_router.py, critics.py, deterministic_checks.py, human_review.py
  artifacts/            workspace.py, artifact_store.py, versioning.py
  governance/           budgets.py, permissions.py, policies.py, secrets.py
  observability/        logs.py, metrics.py, traces.py, replay.py, evaluation.py
```

为承载上述模块，增补 `contracts/`（序列化合同）、`storage/`（关系数据库、事务、迁移、队列）和 `__main__.py`（演示/操作 CLI）。它们是实现配套，不改变原文逻辑组件。

当前 wheel 只包含 `src/simple_harness`。第 2 步需要将 `src/agent_orchestrator` 加入 wheel 打包列表，保留原有包；添加公共类型导出、包资源、类型检查路径、来源清单和新增测试目录。不能只在源码目录测试，漏掉安装 wheel 后的导入与数据库迁移资源。

### 1.3 状态所有权只设一个

```text
原文编排对象：Mission / Task / Attempt / Claim / Budget / DAG
    → Orchestrator Commit Service 唯一逻辑写入

已有物理执行：BaseAgent / AgentTurn / Provider invocation / Tool effect
    → 现有 SDK Runtime/UoW 唯一写入

二者关联：attempt_id → runtime_profile_id + agent_id + turn_id
```

初期建议编排使用独立 SQLite 文件 `orchestrator.db`，SDK 继续独占 `execution.db`。两者是不同领域的权威记录，不是重复保存同一权威状态；这不是跨库原子事务。跨边界通过持久 dispatch intent、稳定身份、SDK 回执和结果接收去重连接。队列/outbox 可以同在编排关系库中，无需额外队列服务器；Artifact 可以先用本地不可变文件存储。其他数据库/队列属于后续部署选择，不是新阶段目标。

## 2. 已完成基础 Agent 的实际接入面

| 当前接口/字段 | 编排层如何使用 | 禁止的误用 |
|---|---|---|
| `AgentRuntime.create(config, creation_key=...)` | 为规划、执行、审阅等调用创建幂等身份 | 每次派发重试生成新 creation_key |
| `create_many(configs, batch_key=...)` | 需要批量准入时使用 | 批量创建成功就当作 Attempt 已执行 |
| `open(agent_id)` | 重连已有执行实例 | 跨 owner_scope 打开实例 |
| `BaseAgent.submit(message, input_id=...)` | 持久提交已冻结的任务包 | 用一次无身份的 HTTP/LLM 调用绕过 SDK |
| `turn_id_for(input_id)` | 提交前确定本次 turn 身份 | 相同 input_id 改写内容 |
| `get_result(turn_id)` | 采集已经提交的结果 | `COMMITTED` 直接等于 Task 完成 |
| `turn_snapshot(turn_id)` | 区分已提交、执行中、被 UNKNOWN 等阻塞 | 只看 `AgentStatus.lifecycle` 就判断业务状态 |
| `wait_turn(..., timeout=...)` | 交互层有限等待；后台采集用查询 | 超时就认定执行失败、取消或换新 Attempt |
| `cancel_turn(..., command_id=...)` | 有身份地请求取消，并核对回执 | 请求取消即认定所有副作用未发生 |
| `AgentTurnResult.public_output` | 严格解析成原文 Result Envelope | 用一句“已完成”代替证据与验收 |
| `artifact_refs / usage_refs` | 关联实际产物与真实费用账本 | 相信模型自己填写的 cost 或伪造的文件引用 |
| `AgentConfig.instructions / tool_names` | 固定角色要求和工具上限 | 让模型自行扩大权限或改编排数据库 |
| `model_profile_ref` + `AgentRuntimePorts.provider/model` | 第 6 步验证配置到物理模型的真实路由 | 只改 profile 字符串就宣称换模型成功 |
| `context_policy / tokenizer / embedding` | 复用基础 Agent 的短期 Context 管理 | 把 Blackboard 复制成所有 Agent 的全量聊天记录 |

当前 `AgentConfig` 明确没有 Mission、Task 或用户记忆字段，不应把这些业务字段塞回基础类。`BaseAgent.role` 当前含 root 等实例关系意义；Explorer/Critic 等编排角色保存于 Attempt/role template，不据此误改底层关系字段。

`AgentRuntime` 当前不接用户 Memory，维持这一边界；原文 `memory/blackboard` 是 Mission 团队知识，不是用户长期记忆。

当前运行参数还允许默认无并发上限及本地零价格估算。第 2 步必须显式绑定本次部署的并发和预算策略：本地不计费可明确标为 unpriced；付费模型必须注入实际价格，未知费用不能写成零。第 6 步才扩大规模，不等于第 2 步可以没有上限。

## 3. 从第 2 步开始，分成 8 个可用版本

| 建设步骤 | 原文第 28 章阶段 | 本步完整新功能 | 结束后用户能直接做什么 |
|---|---|---|---|
| 第 1 步：BaseAgent | 已完成底座，不等于原文第一阶段 | 基础 Agent + 独立短期记忆 | 批量创建并调用 Agent |
| 第 2 步 | 第一阶段：可靠单 Manager 系统 | 单 Task Mission 的可靠验收闭环 | 交办一件工作，得到经过验收的结果，失败可修复、重启可恢复 |
| 第 3 步 | 第一阶段：可靠单 Manager 系统 | Planner 自动拆解、并行执行静态 DAG | 交办多步骤目标，系统按依赖并行并完成整体验收 |
| 第 4 步 | 第二阶段：Blackboard 与动态搜索 | 共享已验证知识、处理冲突、综合成果 | B 分支使用 A 已验证成果，冲突不会被当作事实扩散 |
| 第 5 步 | 第二阶段：Blackboard 与动态搜索 | 根据 Worker 反馈动态改图和调配资源 | 执行中新增前置、细分任务、改变方向，而不是重做全部任务 |
| 第 6 步 | 第三阶段：规模化与安全 | 多 Mission、多模型、背压与隔离运行 | 同时运行多项任务，验证积压时自动减速，任务间不会越权 |
| 第 7 步 | 第三阶段：规模化与安全 | Human-in-the-loop 的受控真实操作 | 系统生成修改候选，经绑定版本的审批后只执行获批动作 |
| 第 8 步 | 第四阶段：从历史中学习 | Trace Replay、贡献归因和策略对照评测 | 解释一次成败，比较两个策略，并能回放关键失败 |
| 第 9 步 | 第四阶段：从历史中学习 | 离线学习、参数推荐、审批晋级与回滚 | 用历史提出更好的策略候选；不合格候选不能改变线上行为 |

每一行是一个功能版本，不是单独“写 Schema”“做数据库”“写 Prompt”。一行内部自然会涉及多个模块，但必须一起交付，才算本步完成。每一步都继续跑前面已完成版本的场景，不能引入新功能后让旧功能失效。

所有新接口、CLI、测试文件均为本文要求实现的交付物；后文命令在相应步骤实现后才可执行，不是声称当前仓库已有这些命令。

## 4. 第 2 步：单 Task Mission 的可靠验收闭环

**原文依据：§2、§4、§5、§12—18、§20—21、§24—28；对应原文第一阶段的最小端到端切片。**

### 4.1 可独立使用的新功能

用户提交“在隔离工作区实现一个字符串解析函数，并通过给定测试”，系统完成：Mission → Planner 提出一个 Task → Commit → Reserve → Attempt → BaseAgent → Result Envelope → Verifier → Commit → 最终结果。Planner 此时只需生成一个可执行 Task；完整多节点规划留到第 3 步。

第一次产物有错误时，Verifier 必须给出真实失败记录；单 Manager 根据反馈安排修复 Attempt，或在预算/尝试上限到达后停止。系统重启后能继续未完成工作，已接受结果不重跑。此时只允许 L0 只读或明确隔离的 L1 沙箱操作，不开放生产修改、付款或删除。

### 4.2 实现内容必须作为一个整体提交

| 模块 | 本步具体实现 |
|---|---|
| `contracts/` | 按原文§26定义 Mission、Task、Attempt、Result、Claim/Knowledge、Event；按§25定义状态机；具体序列化约定见§13 |
| `api/missions.py` | `create/get/cancel/events`；创建幂等；校验成功条件、预算和工具，不接受任意 Agent 自报 tenant |
| `planning/planner.py` | 调用一个 BaseAgent 产生单 Task Proposal；规划调用也记账、可恢复，不是免费的内存函数 |
| `orchestrator/commit_service.py` | 一个事务写 Proposal 应用结果、Current State、Event、待派发工作；重复 commit 返回原回执 |
| `scheduling/scheduler.py`、`leases.py` | 持久领取、租约、心跳、受控派发与失败收集；并发显式限制 |
| `governance/budgets.py` | Mission/Task/Attempt 预算账户、Reserve/Settle；规划与验证消耗也归入 Mission；未知费用不释放成可用额度 |
| `runtime/agent_worker.py` | 使用已完成 BaseAgent 公共接口，固定 creation_key/input_id，保存 attempt→agent→turn 映射 |
| `verification/*` | 格式检查、规则检查、独立 Critic 和真实代码测试；按照 Task 的 verification_policy 选择必需层；不适用的层写 NOT_REQUIRED，不伪造 PASS |
| `artifacts/*` | Attempt 隔离工作目录、产物内容 hash、只读验收副本、版本；不让 Worker 直接修改正式版本 |
| `observability/logs.py,traces.py` | mission/task/attempt/result 关联、最小事件时间线、费用来源；高级统计后做但原始关联现在写 |
| `storage/` | 编排数据库、迁移、持久队列、dispatch intent/outbox、结果 inbox、commit receipts |

BaseAgent 提交成功仅使 Attempt 进入 SUBMITTED；Verifier 结束但 verdict=FAIL 也不能完成 Task。Task 只有在要求的验证通过并被 Commit 接受后进入 COMPLETED。

### 4.3 与 SDK 的提交顺序

1. 在编排库内原子 Reserve 并创建 Attempt、dispatch intent；固定目标版本、输入包 hash、配置版本与 `creation_key`、`input_id`。
2. dispatcher 重读 intent，调用 `AgentRuntime.create(...)`；重试沿用原 key。通过 `turn_id_for(input_id)` 得到预期 turn，再调用 `submit(...)`。
3. 保存真实 SDK receipt。若第 2 步返回前后进程崩溃，下次重放同一提交，不创建新 Attempt 来掩盖派发故障。
4. collector 使用 `get_result/turn_snapshot` 观察结果。`wait_turn` 超时只影响调用方等待，不触发 LOST。
5. 将 BaseAgent 输出解析成原文 Result Envelope；核对任务与 Attempt 身份、产物 hash、输入版本、真实 `usage_refs`。解析失败进入有预算的修复/失败处理，不将自然语言猜成 PASS。
6. 保存 ResultSubmitted 与验证队列项。验证重启只继续审阅已有产物，不重跑 Worker。
7. Commit 接受有效 Review；更新 Task、Claim 的状态和最终报告，结算/关联费用，写可恢复输出。

同一个命令在两个数据库之间不存在天然原子性。dispatch intent + 稳定身份 + SDK 幂等 + 回执核对 + 接收去重必须全部具备，不能靠一个 in-memory callback 完成闭环。

### 4.4 本步演示与验收

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| S2-01 | 正常提交代码任务 | 得到真实产物、真实测试结果、TaskCompleted 和最终交付 |
| S2-02 | Worker 提交有错代码 | Task 不完成；失败反馈进入新 Attempt，修复后重新验收 |
| S2-03 | 重发 Mission/dispatch/result/commit | 不新增逻辑重复任务、不重复 Reserve、不重复交付 |
| S2-04 | Agent 创建后、SDK receipt 保存前终止编排进程 | 恢复后回到同一个 agent/turn；不存在第二次执行 |
| S2-05 | ResultSubmitted 后、Verifier 前终止 | 只恢复验证队列，Worker 不重跑 |
| S2-06 | 达到预算或 max_attempts | 停止创建 Attempt，并报告已完成部分与停止原因 |
| S2-07 | 无效 JSON、伪造 result 身份或产物 hash | 拒绝提交，保留错误记录；不改变正式 Task/Claim |
| S2-08 | 真实工具/模型 UNKNOWN | 保持阻塞并核对，不把无回执当成未执行；不把成本写零 |

拟交付命令：`python -m agent_orchestrator demo --scenario single-task --provider fixtures --evidence-dir evidence/s2`；真实 Agent 演示将 provider 换为部署配置名。测试目录 `tests/orchestrator/step02/`。

**完成标准：一件工作真正做完、验收、交付，或带原因地停止；中间关键崩溃点可恢复。只有创建了数据库或返回一个 Agent 回复，不算完成。**

## 5. 第 3 步：自动拆解并并行执行静态 Task DAG

**原文依据：§6—10、§17—20、§24—26、§28第一阶段。第 3 步结束，原文“可靠单 Manager 系统”才算完成。**

### 5.1 可独立使用的新功能

用户只提供根目标：“在隔离项目中实现两个独立功能，并生成测试与交付说明”。Planner 产生有明确依赖的初始 DAG；独立任务并行，依赖任务只在前置验收通过后启动；最终完整成果再次验收。

本版本的 DAG 在执行期间不自动改写。Worker 的 proposed_tasks 会保存并展示，但不直接生效。动态处理于第 5 步开启，不能暗中将动态搜索提前混成未验收的能力。

### 5.2 实施方式

| 模块 | 新增闭环能力 |
|---|---|
| `planning/planner.py` | 多 Task Proposal；每个任务均有 goal、rationale、dependencies、success_criteria、verification_policy、预算与版本 |
| `graph/task_graph.py,dependency_checker.py,deduplicator.py` | 版本化 DAG；校验缺失节点、自依赖、循环、重复 Task 提案和预算；初期 ID/规范化重复去重，不依赖语义猜测合并 |
| `scheduling/allocator.py` | 简单、有界分配：满足依赖的 Frontier，预算内按既定优先级；不引入学习型策略 |
| `scheduling/scheduler.py` | 多 Attempt 运行；每个 Attempt 一个执行者；同一 Task 可配置多个探索 Attempt，胜出后按规则取消冗余者 |
| `context/context_builder.py` | 从正式 Mission、Task、父任务、直接依赖、现有失败/验证反馈和权限预算构造输入包；没有 Blackboard 时不伪造相关知识 |
| `artifacts/versioning.py` | 子产物版本传递；下游输入固定 hash；最终整合任务读取实际产物而非仅复制摘要 |
| `orchestrator/state_machine.py` | 严格按§25推进 Task/Attempt；多个 Attempt 并发时，某个候选进入VERIFYING不妨碍记录其他在途 Attempt |

Planner 本身由一个 BaseAgent 角色配置承担。Manager 可以与 Planner 合并成一个逻辑管理 Agent，这是原文允许的早期形态；Orchestrator、Commit Service、Scheduler 仍是普通程序。

### 5.3 固定演示任务

```text
A：固定输入/输出合同
    ├── B：实现功能一
    └── C：实现功能二
B + C → D：集成测试
D → E：生成最终交付说明并进行 Mission 级验收
```

测试 fixture 允许产生上述固定结构以确定性检查调度。真实模型模式必须由 Planner 提出计划，不将 fixture 图冒充为自动规划成功。

### 5.4 本步验收

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| S3-01 | 正常执行有共享前置的 DAG | A 验收后 B/C 并行；D 不能提前；最终Mission满足根要求 |
| S3-02 | Planner 给出 A→B→A | Graph Manager 拒绝整份非法图，正式版本不改变 |
| S3-03 | B完成而C失败 | B不重做；C有限修复；D继续等待有效前置 |
| S3-04 | 两个 Scheduler 同时领取 | 一个 Attempt 只有一个有效 owner；禁止重复创建AgentTurn |
| S3-05 | 多Attempt候选竞争 | 只接受符合当前合同的有效结果；冗余取消需记录成本和回执 |
| S3-06 | 整合结果不满足原始要求 | 即使所有局部任务都通过，Mission 也不能报告成功 |
| S3-07 | 运行中失去编排租约 | 按原文标记LOST并产生新Attempt；旧执行先核对/隔离；不重跑已完成Task |
| S3-08 | 原文规定的Task预算不足 | 分配失败可解释；子预算总额不超过父预算 |

拟交付命令：`python -m agent_orchestrator demo --scenario static-dag --provider fixtures --evidence-dir evidence/s3`；测试目录 `tests/orchestrator/step03/`。

**完成标准：从一个目标自动得到可执行计划，并行完成多任务、整体交付、恢复不重跑已完成节点。**

## 6. 第 4 步：团队共享知识、冲突仲裁与综合成果

**原文依据：§9—11、§13—15、§20.3、§23.3、§28第二阶段。**

### 6.1 可独立使用的新功能

A 分支产生一个通过指定验证的结论，B 分支能在下一次Context中获得并复用；矛盾结论并存进入仲裁；Synthesizer使用多份有效成果生成新的候选产物，再次验收后交付。

这与已经完成的BaseAgent短期记忆不同：短期记忆是“这个Agent经历了什么”，Blackboard是“当前Mission有哪些候选、哪些知识能正式依赖”。本步不安装或重做用户记忆SDK。

### 6.2 实施方式

| 模块 | 新增闭环能力 |
|---|---|
| `memory/claims.py` | 原文Claim状态：PROPOSED、UNDER_REVIEW、SUPPORTED、VERIFIED、REJECTED、DISPUTED、SUPERSEDED；状态转换需证据与Review |
| `memory/verified_knowledge.py` | 只将VERIFIED投影成正式可依赖知识；保存来源Task/Attempt、证据、验证器、dependencies、supersedes |
| `memory/blackboard.py` | Raw Logs引用、Candidate Claims、Verified Knowledge、Summaries四层；不能让所有Agent直接写正式知识 |
| `context/retrieval.py` | 同时考虑相关性、DAG距离、可信等级、新旧、分支、复用、重复和已取代状态；权限预过滤后回读原文 |
| `context/context_builder.py` | 完成原文§10的11项输入；Worker、Explorer、Critic、Verifier采用不同可见性模板 |
| `context/compression.py,memory/summaries.py` | 组内/全局摘要保存来源、版本和不确定性；摘要不能自动升级知识可信状态 |
| `verification/*` | 冲突Claim均标DISPUTED，创建Conflict Task，派Critic/Arbiter并外部核查，再Commit |
| `planning/manager.py`与Synthesizer角色 | 组合不同Agent的有效部分，而不是只选最高分答案；生成的新候选必须再验收 |

为避免在未建立动态规划前就任意改图，本版本只开放系统定义的 Conflict Task 和固定综合任务模板；普通Worker的新增Task建议仍不自动入图。完整动态Task DAG在第5步开放。

### 6.3 验收场景

任务是比较两个实现对同一输入合同的支持程度。A报告一个可复现的边界问题；B在后续检查中使用该已验证证据；另一份Claim与A矛盾，系统仲裁；Synthesizer输出最终对比产物。

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| S4-01 | A知识VERIFIED后B继续 | B上下文包含正确知识ID/版本，used_knowledge记录复用链 |
| S4-02 | A只有自信但无验证 | Worker不能把PROPOSED/SUPPORTED显示为正式事实 |
| S4-03 | 两个相反Claim | 双方保留，DISPUTED，仲裁任务与外部依据存在；不是多数票 |
| S4-04 | 知识被SUPERSEDED | 后续请求不无标记使用旧版本；历史请求/血缘仍可追溯 |
| S4-05 | Synthesizer合并产生新错误 | 新产物未通过，不得因来源都通过而直接Commit |
| S4-06 | 同时运行两个Mission | 未授权的知识不跨Mission出现，BaseAgent自身历史也不自动共享 |
| S4-07 | 检索和摘要失败/索引未就绪 | 明确降级或阻塞；不将“未检索到”写成“没有证据” |
| S4-08 | 原始外部内容含改变权限的指令 | 标记为不可信数据，不成为编排命令或工具授权 |

拟交付命令：`python -m agent_orchestrator demo --scenario knowledge-sharing --provider fixtures --evidence-dir evidence/s4`；测试目录 `tests/orchestrator/step04/`。

**完成标准：新的任务真的使用了其他分支的已验证知识，冲突得到可追溯处理，多产物综合结果再次通过验收。只有一张向量表不算完成。**

## 7. 第 5 步：根据 Worker 返回动态修改 Task DAG

**原文依据：§6.2、§7—9、§13、§15、§17、§18.6、§19、§28第二阶段、§29.2—29.3。第5步结束即达到原文“Blackboard与动态搜索”阶段目标。**

### 7.1 可独立使用的新功能

Worker返回blocked、proposed_subtasks、failure或no_progress后，Manager观察真实进展，提出新增前置、细化工作、改变优先级、暂停路线或换角色/模型的Proposal。Graph Manager检查并Commit，Scheduler使用新的正式图继续工作。已经有效完成的任务不被全盘重跑。

### 7.2 实施方式

| 模块 | 新增闭环能力 |
|---|---|
| `planning/manager.py` | 读取结果、Verifier反馈和受影响子图，选择下一步；不在每个token或工具心跳后重新规划 |
| `planning/search_controller.py` | 初期与Manager合并；支持有界展开、路线停用、Best-of-N与探索/利用调整；不引入复杂MCTS |
| `graph/task_graph.py` | 事务性应用Task DAG Proposal；记录变更前版本、依据result_id、受影响任务与依赖 |
| `graph/deduplicator.py` | 规范化/明确ID去重；语义疑似重复只提出合并建议，不删除独立证据或未完成责任 |
| `scheduling/allocator.py` | 使用原文§29.3的起始公式，参数独立版本化；将输入尺度统一是实现约定，不伪装为已校准模型 |
| `runtime/role_templates.py` | 注册Explorer、Exploiter、Critic、Simplifier、Connector、Failure Analyst、Synthesizer、Verifier；权限由工具上限落实 |
| `governance/budgets.py` | 新分支、返工、管理与验证均受原Mission预算；不靠新Task/新Agent清空消耗 |
| `orchestrator/event_handler.py` | ResultSubmitted/VerificationFailed/知识更新/停滞等触发一次可去重的管理决策工作 |

原文的初始优先级原样保留：

```text
priority =
  0.30 × mission_importance
+ 0.20 × unlock_value
+ 0.15 × progress_signal
+ 0.15 × uncertainty
+ 0.10 × waiting_age
- 0.05 × estimated_cost
- 0.05 × duplication_score
```

风险、权限、可用预算和队列上限先作资格检查，不能被高分抵消。额外积压惩罚按§8.2/§18.5在第6步开启并记录allocator版本。

### 7.3 动态修改与原文状态机的兼容处理

原文允许动态细化，但§25没有画出ACTIVE→BLOCKED等回边。本计划不悄悄新增这些状态转换：

- 尚未执行的任务允许在其合法状态下细化/增加前置。
- 已在执行的Task需要实质改变合同时，优先保留旧Task及Attempt历史；按原文ACTIVE→CANCELLED处理被替代的执行，再创建引用旧任务的新Task版本实体及子任务。
- 新Task使用新的ID，记录替代/变更依据；旧Task的COMPLETED/FAILED/CANCELLED终态不被重写。
- 未受影响的已完成Task直接作为新Task的依赖引用，不重新执行。
- 如果后续选择扩展§25状态机，必须作为文档修订明确提出，不能把它混进“100%按原文”的本次实现。

候选路线之间是否需要全部完成，由Mission成功条件和已采用的任务/停止决策表达。不能自行给原文加一个未定义的AND–OR证明逻辑，也不能仅因所有曾创建节点未完成就永远不结束Mission。

### 7.4 端到端演示

```text
原计划：A分析输入 → B实现 → C验证
                 └→ D独立文档检查

B返回：缺少必须确认的格式定义，proposed_tasks=[确认格式]
Manager提出新前置与实现修订
Commit后：新增E确认格式 → B2实现 → C2验证
保留A/D有效成果；B旧Attempt按显式策略收敛
```

用户应该能在任务图和事件时间线上看到v1→v2、变更依据、旧产物和新增工作，不只是最后一张看似全新的图。

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| S5-01 | Worker提出必要子任务 | 只经Proposal/Commit新增，原Worker不能直接改图 |
| S5-02 | Worker返回no_progress反复出现 | Manager换角色/方法或停止；有限次数；不无限分裂 |
| S5-03 | 两个Manager基于相同旧版本提交 | 一个生效；另一个重新检查或合并，不丢失已提交修改 |
| S5-04 | 新依赖形成环或目标漂移 | 拒绝，原正式图保持；记录原因 |
| S5-05 | 修改B分支 | A/D等无关已完成成果保留，不重复扣费和执行 |
| S5-06 | 被替代Worker迟到提交 | 存历史候选但不批准当前Task；已发生费用仍归账 |
| S5-07 | 同一个建议被重复发送 | 至多一次正式修改，proposal/result/commit可关联 |
| S5-08 | 超过DAG深度/单Agent建议数/预算 | 拒绝扩展，Manager得到明确反馈而非绕限制 |
| S5-09 | 低优先级可执行Task长期等待 | waiting_age起作用，在有资源且无安全阻塞时获得执行机会 |

拟交付命令：`python -m agent_orchestrator demo --scenario dynamic-dag --provider fixtures --evidence-dir evidence/s5`；测试目录 `tests/orchestrator/step05/`。

**完成标准：系统能够根据执行证据改变正式计划并继续完成同一个Mission，同时保持版本、预算与既有成果正确。只有让模型打印一棵新树不算完成。**

## 8. 第 6 步：多 Mission、多模型、背压和隔离运行

**原文依据：§8—10、§17—21、§23、§28第三阶段、§29。**

### 8.1 可独立使用的新功能

同时启动多个Mission，按任务类型选择真实模型、给不同Mission配额；Worker过快导致验证积压时自动减速；不同Attempt在独立Workspace工作；所有动作、成本和上下文版本能在完整Trace中关联。

本步扩大的是“受控制的并发”，不是仅把max_agents改大。第一版以原文§29的10—20个Worker、2个Verifier Worker作为可配置目标拓扑；若部署承载不足，应降低实际并发并报告测试结果，不将原文建议数量视为质量保证。

### 8.2 实施方式

| 模块 | 新增闭环能力 |
|---|---|
| `scheduling/backpressure.py` | 运行、待处理、待验证、Task深度、单Task Attempt、单Agent子任务建议数上限；上下游高低水位，恢复有滞回 |
| `scheduling/allocator.py` | Verifier积压→减少Worker/增加验证资源/暂停低价值扩展；保持等待老化和探索额度 |
| `runtime/model_router.py` | profile→实际Provider/model/tokenizer/context policy/价格/能力映射；每次调用冻结实际目标并纳入Trace |
| `runtime/agent_worker.py` | 支持按路由选执行池；不能多个不兼容Runtime同时恢复同一个Agent；runtime_profile_id必须持久绑定 |
| `artifacts/workspace.py` | 独立可写目录/沙箱与只读依赖产物；合并通过新候选与再验收，不共享可写主目录 |
| `runtime/tool_gateway.py,governance/*` | Mission/Task/Role/部署政策的权限交集；工具参数、速率、资源和费用检查；外部文本不能提升权限 |
| `governance/budgets.py` | Global→Mission→Task→Attempt配额和所有真实调用费用关联；资金分配与最终计费不重复相加 |
| `verification/verifier_router.py` | 根据verification_policy运行多层验证；保留分层结果/版本；未部署的必需验证器导致阻塞而非假通过 |
| `observability/*` | 完整版本字段、队列/延迟/费用/复用/验证率、日志/指标/Trace，能从最终产物回溯来源 |

当前基础类在Runtime上注入provider/model，并不能仅凭AgentConfig.model_profile_ref就证明物理路由正确。可以按模型配置组建不同Runtime池，但必须分开SDK存储/明确恢复分区，并持久绑定每个Attempt目标池；不能盲目让不同模型的Runtime打开同一execution.db恢复全部Agent。

### 8.3 原文角色配比与动态调整

注册原文的比例起点：Explorer20%、Exploiter40%、Critic20%、Synthesizer10%、Verifier10%。这些是可调整的起始资源建议，不要求每个时刻严格凑成该比例，更不能覆盖Task所需的强制Verifier和权限限制。

早期增加探索；中期增加深化和Connector；临近交付增加Critic/Synthesizer/Verifier；积压时减少Worker、增加验证资源。多层Manager是原文的大系统可选形态，本版本优先保持单Manager/合并Search Controller；只有规模评测显示必要时再配置组级管理，不把它设为前置。

### 8.4 本步验收

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| S6-01 | 同时运行两个Mission | 均有进展，配额不互相挪用，跨Mission资料隔离 |
| S6-02 | 人为放慢Verifier | 验证队列有界；Worker并发下降；停止低优先扩展；恢复后逐渐放开 |
| S6-03 | 小模型失败后升级 | Trace中实际provider/model改变，保留先前Attempt与费用，而不是只改标签 |
| S6-04 | 两个Attempt写同名文件 | 位于不同Workspace；无覆盖；正式版本只经验证后提交 |
| S6-05 | 模型索要额外工具或读取其他工作目录 | 网关拒绝；Prompt和模型置信度不能授权 |
| S6-06 | 一个模型服务不可用 | 相应工作有界等待/明确降级；其他可执行Mission继续 |
| S6-07 | Token/工具/运行时间某一维耗尽 | 停止新分配；已发生费用和在途预留仍保留正确 |
| S6-08 | 重启多Runtime执行池 | 旧Attempt回到原目标配置，不能被另一模型静默接管 |
| S6-09 | 日志检查 | 无密钥；所有Result可追到prompt/model/retrieval/allocator/verifier版本 |

拟交付命令：`python -m agent_orchestrator demo --scenario multi-mission --provider fixtures --evidence-dir evidence/s6`；另交付真实多模型小规模负载报告。测试目录 `tests/orchestrator/step06/`。

**完成标准：多个任务能同时、安全、有上限地运行；背压和物理模型路由在可观察场景中实际生效。**

## 9. 第 7 步：Human-in-the-loop 与受控真实操作

**原文依据：§14.4、§21—22、§24、§28第三阶段。第7步结束达到原文“规模化与安全”阶段目标。**

### 9.1 可独立使用的新功能

Agent先产生一个可审阅的外部修改候选；系统根据风险发起审批；审批绑定具体动作与版本；只执行获批内容并核对真实回执。用户可以拒绝、撤权、评论或接管，所有动作入Event。

演示先使用专门的测试服务/测试环境，例如“修改测试配置项”。通过测试不等于对生产环境有任何自动授权。支付、删除等高风险连接器只有实际完成政策、幂等与核对实现后才能启用。

### 9.2 实施方式

| 模块 | 新增闭环能力 |
|---|---|
| `api/approvals.py` | 列出、批准、拒绝、评论、接管请求；认证身份不能来自模型参数 |
| `verification/human_review.py` | 把NEEDS_HUMAN和Verifier冲突送人工；保留原有分层验证，不以人类批准掩盖未通过的代码测试 |
| `governance/policies.py` | 原文L0只读自动、L1沙箱可逆自动、L2生产修改一次审批、L3付款/删除/敏感数据双重审批 |
| `governance/permissions.py` | 审批/能力绑定Mission、Task、动作参数、Artifact hash、有效期和当前版本；改内容后旧审批失效 |
| `runtime/tool_gateway.py` | 稳定业务动作ID、幂等、交接前再验证、结果核对；UNKNOWN不得触发盲目再执行 |
| `orchestrator/event_handler.py` | ApprovalRequested/Granted/Rejected、HumanOverride、HumanCommentAdded均入账并可恢复 |

L3“双重审批”的具体身份安排原文未细化。本实现采用两个独立审批记录、禁止同一个审批回执重复计数；是否要求不同自然人必须在部署政策中明确，不将原文未规定的人员安排伪称为原要求。

### 9.3 本步验收

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| S7-01 | L2候选未批准 | 没有真实修改；审批请求可查询且重启后存在 |
| S7-02 | 批准指定版本 | 只执行对应参数/hash的动作一次，得到可核对回执 |
| S7-03 | 批准后Agent改了内容 | 原批准不能用于新动作；重新请求审批 |
| S7-04 | 拒绝/撤权/过期 | 阻止后续新handoff，不把拒绝当作成功 |
| S7-05 | L3只有一次批准或同回执重放 | 不执行；满足部署定义的双重审批后才可能执行 |
| S7-06 | 外部动作执行成功但回执丢失 | 进入UNKNOWN/核对；不重复执行，不回滚数据库伪装没发生 |
| S7-07 | 用户接管停滞或Verifier争议 | 保留HumanOverride和依据；未获授权范围不扩大 |
| S7-08 | 外部文档要求自动批准 | 被当作不可信数据，不能改变审批状态 |

拟交付命令：`python -m agent_orchestrator demo --scenario approval-action --provider fixtures --evidence-dir evidence/s7`；测试目录 `tests/orchestrator/step07/`。

**完成标准：从候选、审批、执行到结果核对完整闭环可演示；不是只有一个“批准”按钮。**

## 10. 第 8 步：贡献归因、Replay 与策略对照评测

**原文依据：§23、§28第四阶段。前面的基础事件/Trace从第2步就存在，本步新增的是能够用历史解释和比较编排效果的完整功能。**

### 10.1 可独立使用的新功能

选择一次Mission，查看它为何成功或失败、最终产物依赖哪些Task/Claim/Attempt/Agent；回放关键失败；在固定任务集、相同资源条件下比较两个Allocator/Prompt/角色配置，并生成包含失败样本与成本的评测报告。

### 10.2 实施方式

| 模块 | 新增闭环能力 |
|---|---|
| `observability/replay.py` | Event/Trace重建关键过程；缺失数据必须标注；回放不调用真实外部工具、不重复收费 |
| `observability/evaluation.py` | 固定任务集、可重复执行配置、预算和结果采集；真实模型试验与确定性协议回归分开 |
| `observability/traces.py` | 产物→知识→验证→Attempt→Agent的贡献路径，记录未进入最终路径的探索消耗 |
| `governance/policies.py` | 冻结prompt/model/retrieval/allocator/verifier版本，使实验有可比基线 |
| 评测报告/CLI | 成功率、完成耗时、成本、重复率、知识复用、验证误判、故障恢复和失败原因 |

Replay不是“用新模型重新执行旧任务”。新策略重跑任务叫Evaluation；回放旧Event应重建既有事实。涉及LLM随机性时使用多次独立试验并报告分布/样本量，不以单个成功样例宣称胜出。

本步同时支持原文的A/B与Ablation：例如去掉Critic、禁用Blackboard、禁用动态调度，观察真实影响。但消融实验不得关闭必要的权限/幂等安全边界后去执行真实副作用。

### 10.3 本步验收

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| S8-01 | 指定一次已完成Mission | 可查看最终产物依赖链及真实费用归属 |
| S8-02 | 回放重复事件/崩溃记录 | 得到相同正式状态；外部调用数不增加 |
| S8-03 | 比较两个策略 | 使用相同任务集/预算边界，输出两者指标和失败案例 |
| S8-04 | 移除Critic或Blackboard做消融 | 报告关闭了什么、哪些变化可观察；不预设一定变差 |
| S8-05 | 历史Trace不完整 | 报告覆盖不足，不能伪造完整Lineage |
| S8-06 | 新模型重跑旧任务 | 标记为新Evaluation与新成本，不覆盖旧Mission事实 |
| S8-07 | 检索/Prompt/模型版本变化 | 报告明确列出版本差异，能够复现配置来源 |

拟交付命令：`python -m agent_orchestrator demo --scenario evaluate-policies --provider fixtures --evidence-dir evidence/s8`，并交付 `replay` 与 `evaluate` 子命令；测试目录 `tests/orchestrator/step08/`。

**完成标准：用户能够解释一次真实任务的成败，并凭一份有依据的比较报告选择策略，而不是只看Agent数量和消息数。**

## 11. 第 9 步：从历史中学习，并受控晋级新策略

**原文依据：§28第四阶段、§29.3、§23。第9步结束覆盖原文完整建设目标。**

### 11.1 可独立使用的新功能

系统根据历史Trace提出优先级、模型路由、角色/Prompt和参数的候选优化；离线验证、A/B比较并经批准后才上线。线上Mission固定使用一个已批准版本，可回滚，在线Agent不能直接改核心调度或安全规则。

### 11.2 实施方式

| 模块 | 新增闭环能力 |
|---|---|
| `observability/evaluation.py` | 时间/任务隔离的训练评测集、基线比较、质量/成本/风险门槛，避免把同一任务的相邻Attempt同时放训练和测试造成泄漏 |
| `scheduling/allocator.py` | 版本化优先级候选，保留原文简单规则作为fallback；学习器只排序合法Frontier，不改变预算和权限资格 |
| `runtime/model_router.py` | 从允许模型集合中提出路由策略；实际模型可用性和预算仍由确定性程序检查 |
| `runtime/role_templates.py` | 角色和Prompt可靠性/贡献统计，候选版本注册；不因“高信誉”免验证 |
| `governance/policies.py` | 候选、评测、审批、发布、回滚的策略版本库；生效范围与运行中Mission的版本锁定 |
| `scheduling/backpressure.py` | 群体稳定性控制：抑制并发/角色配比大幅震荡、保留预算与验证能力；具体参数由评测校准 |

执行顺序按原文：收集Trace → 离线训练或规则改进 → 新策略版本 → Offline Evaluation → A/B → 审批后上线。规则改进是资料不足时的正式路径；有足够数据时实现学习型候选。不能把简单的手工调权重冒充已训练的Model Router。

### 11.3 数据不足与效果不佳也是完整结果

第一版历史不足时，完整的新功能应该返回“候选未达到晋级条件/缺少足够样本”，继续使用现有规则。这是正常受控行为，不是让系统自动编造信誉分或自信地上线一个未测模型。

用人工构造的数据可以测试训练/注册/批准/回滚的机械流程，但必须标记fixture；不能据此声称真实任务质量提高。

### 11.4 本步验收

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| S9-01 | 生成一个新策略候选 | 可追溯到训练数据、代码、参数和评测版本 |
| S9-02 | 候选未通过质量/成本门槛 | 不上线，保留失败理由；旧策略继续运行 |
| S9-03 | 合格候选但未批准 | 仅shadow/测试，不改变正式Mission |
| S9-04 | 审批后上线 | 新Mission绑定新版本；在途Mission不被静默改策略 |
| S9-05 | 新策略出现异常 | 快速回滚至已批准版本，既有事件和费用仍保留 |
| S9-06 | 在线Agent提出改安全阈值 | 不能直接写核心规则；按权限拒绝或进入人工审查 |
| S9-07 | 训练数据不足/有明显污染 | 明确拒绝晋级，不输出虚假的有效提升结论 |
| S9-08 | 高负载下策略频繁调整 | 并发和角色调整有边界，无无限扩张；安全/预算上限始终有效 |

拟交付命令：`python -m agent_orchestrator demo --scenario policy-promotion --provider fixtures --evidence-dir evidence/s9`，并交付 `policy propose/evaluate/approve/promote/rollback` 子命令；测试目录 `tests/orchestrator/step09/`。

**完成标准：从历史到策略建议，再到批准、上线和回滚的闭环真实存在；是否提高真实效果由评测报告决定。**

## 12. 所有功能版本共用的实施规则

### 12.1 BaseAgent、Attempt 与恢复不能混淆

原文§16.4/§17.6要求“RUNNING且Lease过期→LOST→新Attempt”。保留该语义，但它是编排层对业务尝试的管理，不是把BaseAgent的一次请求等待超时误认为Agent已经消失。

| 情况 | 编排处理 |
|---|---|
| API等待超时，但SDK Turn仍在运行 | 查询原turn，仍绑定原Attempt，不新建 |
| 编排派发回执丢失 | 重放相同creation_key/input_id，核对同一SDK回执 |
| BaseAgent在自己的有效执行租约下恢复同一个Turn | 不自行判成新业务尝试 |
| 编排Attempt的有效执行者确实失联/租约过期 | 标记LOST，新建带retry_of的新Attempt；旧Attempt不复活 |
| 原Attempt有已提交Result | 进入/恢复验证，不新建执行Attempt |
| 旧执行仍可能产生外部副作用 | 先撤销它的新动作资格，并核对在途动作；新Attempt可排队但不得重复未知业务动作 |
| Task已COMPLETED | 不重跑；新要求必须形成新的明确工作对象 |

heartbeat必须来自执行者或实际有效进展/存活检查，不能由Scheduler自己定时更新来掩盖已死Worker。业务租约与SDK执行租约分别保存，最终提交同时检查适用身份/版本。

LOST后的旧结果可以保存为历史候选，但不能绕过当前Attempt/Task版本成为正式答案。已经发生的调用费用和工具结果即使迟到，也应归入实际发生的Attempt，不因为LOST而丢账。

### 12.2 预算预留与费用是两层，不重复计费

- 编排层Reserve是资源额度分配，SDK invocation ledger是实际模型调用的费用事实。
- 同一份预留由Mission向Task/Attempt分配时不新增资金；关系与余额在一个编排事务内检查。
- 每条usage_ref至多导入一次实际支出；模型输出的cost只能作待核对信息。
- Planner、Manager、Synthesizer、Critic、Verifier、摘要和付费embedding都要有明确预算归属，不能只统计Worker。
- 在途UNKNOWN预算保持占用，完成核对后再Settle；不能为了新Attempt腾额度而清零旧费用。
- Mission/Task/Attempt上限之外，真实Provider和Tool交接仍要有下层硬限额。无法把本次任务预算落实成真实调用限制时，不能宣称硬预算已完成。
- 当前SDK接口缺少某种外部工具费用/额度绑定时，增加一个最窄的预算或工具适配扩展，并在所属功能步骤中验收；不复制Provider调用链，也不改造整个BaseAgent。

### 12.3 副作用与候选探索分离

第2—6步仅使用获准只读或独立沙箱动作。第7步才开放经过审批和核对的真实修改。

跨Attempt的同一个现实动作需要稳定业务动作ID，不得仅用新Attempt ID来生成“全新”动作。相同命令的重放可去重，但用户确实再次要求执行相同内容时应有新的合法业务身份，不能把“参数相同”当成永远禁止重复。

不支持幂等/核对的高风险连接器保持禁用或人工处理。这里的“可靠”不意味着任意外部系统都具备exactly-once保证。

### 12.4 验证是按政策选择的分层体系

原文§14的格式→规则→独立Critic→测试/实验→形式验证→必要人工完整保留。不是每个Task都机械运行全部六层，而是Task Contract列明必须运行哪些层，Verifier Router落实并记录。

只要必需层未运行、结果未知或未通过，就不能宣称PASS。一个代码测试通过可以支持其测试覆盖范围内的结论，不能顺带把未经验证的产品性能承诺标为VERIFIED。Critic和正式Verifier的角色职责可以由BaseAgent配置承担，确定性检查是程序；不能把该分层体系改成“只让另一个LLM说通过”。

Mission停止条件里的“Verifier PASS”必须对应满足Mission根成功条件的候选；某个中间子Task PASS只完成该Task并解锁下游，不直接关闭整个Mission。

### 12.5 上下文组装与基础短期记忆不争夺权威

Context Builder提供原文§10的任务包；BaseAgent管理自己的近期窗口和历史。新Mission/Task、权限、当前依赖和正式知识来自编排当前状态；不能靠向量检索猜“当前任务状态”。

默认每个Attempt分配一个独立BaseAgent，避免将先前Task的临时会话当成当前授权上下文。将来复用Agent实例必须显式改变输入作用域并测试隔离，不能仅凭调用同一个ask继承所有历史。

Worker不直接拥有编排数据库、Commit Service内部方法或全局预算写权限。现有delegate工具若绕过Scheduler创建业务Worker，会破坏原文§15；早期任务角色的tool_names不暴露该工具，只允许在Result Envelope里提出proposed_tasks/resource_request。后续要开放委派，必须通过统一的编排准入桥，而非并存两套无预算的Agent创建路径。

### 12.6 版本、事务和发布

每个阶段都有显式编排schema迁移和策略/Prompt版本。已有SDK execution schema保持SDK管理，新增编排数据不能直接手改SDK私有表。部署新版本前备份、一致性检查、副本迁移、旧场景回归，再小范围启用。

已交付真实外部动作后，不能通过恢复旧数据库来“撤销现实”。回滚代码只改变后续执行；既有外部事实通过ledger/reconciliation继续核对。

## 13. 原文没有完全展开的地方，采用这些显式实施约定

以下不是替换原文，而是防止实现时出现两套不兼容语义。任何不同选择都要单独登记，不可宣称是原文原句。

| 问题 | 本计划实施约定 |
|---|---|
| §13 result_id 与§26 result.id命名不同 | 以§26规范schema作为序列化基线；§13示例名称只在显式兼容适配器转换。两种键同时存在且不同则拒绝，不静默取一个 |
| §26只有Mission.status:string，没有完整Mission状态枚举 | 第2步固定可序列化状态与停止reason；核心必须表达未开始、执行中、等待人工/资源、成功、失败、取消等行为；Task/Attempt状态仍严格使用§25，不能拿Mission的扩展串用 |
| §2与§14的Claim示意路径不同 | 使用§25.3完整Claim状态机，保留SUPPORTED与DISPUTED等状态；只有VERIFIED进入正式知识 |
| §25 Task终态无回边，但§6要求动态细化 | 第5步采用旧Task终态保留、新工作实体引用替代关系的方案，不悄悄加ACTIVE→BLOCKED/COMPLETED→ACTIVE |
| §25 RETRY_WAIT后注明新Attempt | retry_of记录旧尝试，创建新id；原Attempt不清零后伪装成新尝试 |
| §19某些地方说暂停/删除Task而§25无PAUSED | 在调度控制或Mission/分支政策中暂停；保留事件，不给Task私自新增PAUSED终态，不物理删除审计历史 |
| §11 Raw Logs“全部原始过程”与§21密钥不得泄漏 | 记录已授权、可公开持久化的过程及产物/账本引用；不收集隐藏推理，不将密钥写进可检索日志 |
| §29角色数量/比例/优先级是推荐起点 | 按原文配置、保留版本；部署参数必须经负载和质量测试，不强行在硬件承载不了时达到建议人数 |
| §7分层Manager、Tree Search等是可扩展能力 | 单Manager/合并Search Controller先完整实现；复杂MCTS不提前加入；可选组件不冒充必须已部署 |

Task Contract和输入、产物、Review的版本关系必须在第2步定义；第5步才能安全动态改图。实现细节可以新增结构化绑定表，但不能新增一套不同的顶层任务名词替代Mission/Task/Attempt。

## 14. 每一步都使用同一种交付和测试方式

### 14.1 一个步骤的完成包

每一步必须同时提供：可导入/安装的功能代码、可重复演示入口、自动化场景、真实Agent演示记录、事件时间线、产物/验证/费用证据、版本迁移和关闭开关。它们属于一个版本，不拆成独立的“建完表也算完成”的阶段。

每个版本的工作节奏：冻结本步输入输出和回归场景 → 从API跑到真实交付 → 注入失败与恢复 → 测试安装产物 → 才进入下一步。先交付单机模块化单体，不先造分布式集群。

### 14.2 测试分三层

1. **确定性协议测试**：用scripted BaseAgent/Provider fixture触发失败、blocked、冲突、改图、迟到结果；验证状态与账本。fixture只用于可控故障，不是对智能规划质量的证明。
2. **真实BaseAgent集成**：接入当前已完成公共API和真实SQLite；检查Agent/Turn对应、上下文隔离、实际测试工具、产物、预算和恢复。
3. **真实模型任务评测**：Planner/Manager/Worker/Verifier使用已配置模型，固定小型任务集，多次运行记录质量与成本；不会要求单次随机LLM输出与fixture逐字相同。

每一步都跑累计回归。第8步新增专门的策略评测平台，而不是前七步都不测试质量。

### 14.3 新增CLI的最小规范

第2步交付 `python -m agent_orchestrator`，后续只增加scenario/子命令；不每步另写一个一次性脚本。

```text
mission create/get/cancel/events
attempt get
artifact show

demo --scenario <name> --provider <fixtures|已配置provider名称> --evidence-dir <dir>
```

第7步增加approval操作，第8步增加replay/evaluate，第9步增加policy管理。scenario在所属阶段实现前必须报告“未实现”，不能返回假成功。

证据目录至少包含：`baseline.json`、`events.jsonl`、`final_state.json`、`artifacts/`、`verification.json`、`costs.json`、`test-report.json`。真实外部凭证不得写入证据目录。

### 14.4 累计测试命令

以下为实现后应能执行的命令；当前新增路径/CLI尚不存在。

```bash
# 第N步的确定性和实际SDK接口回归。
uv run --frozen --group dev pytest tests/orchestrator/step02 -q
# 第3步以后累计包含已交付目录，或直接运行全部编排测试。
uv run --frozen --group dev pytest tests/orchestrator -q

# 现有BaseAgent/SDK回归保持，不因编排加入而删除。
uv run --frozen --group dev pytest -q

# 打包后在干净环境验证已安装的两个package和本步演示。
uv build
```

CI继续保留项目既有provenance/REUSE、类型检查和来源清单。若SDK现有完整测试需要额外环境，报告真实跳过/缺依赖原因，不能将跳过写为通过。新增真实Provider测试复用当前项目已有的real_provider标记与启用机制，不在默认CI偷偷访问付费API。

## 15. 近期最明确的实施决策

下一步只实现本文第2步，不先写Blackboard全集、不先做动态图、不先训练Allocator。它应在当前0.8.0 BaseAgent上形成：

```text
提交Mission
 → 生成并提交一个Task
 → Reserve预算
 → 创建Attempt并派给BaseAgent
 → 接收Result Envelope和Artifact
 → 独立/确定性验证
 → 有限修复或通过
 → Commit并交付
 → 任意关键点重启仍能继续
```

第2步通过后再做第3步，此时原文第一阶段就能真正结束。第5步完成后，用户重点关心的“根据Worker返回动态拆解并修改任务结构”即可实际演示；后面继续规模、安全和学习，不要求等全部八步完成才使用系统。


## 16. 来源与当前代码阅读范围

### 16.1 主设计依据

`agent-orchestration-layer-complete-design.md`，SHA-256：`4d821e388b6c406792ec5256cfcc67fb195aab8be574f24925d012f95c3dc91b`。原文31章与第30章29项验收均已逐项映射；矩阵仅证明计划覆盖，不证明实现正确。

### 16.2 本轮读取的固定版本代码

- [C01] `src/simple_harness/version.py`：源码版本0.8.0。固定源码：https://github.com/DennyWanye/simple-harness-sdk/blob/dffd13c829d42ac3df213ac0c490f3d5834a8b2b/src/simple_harness/version.py
- [C02] `src/simple_harness/agents/base.py`：BaseAgent提交、查询、等待与取消接口。固定源码：https://github.com/DennyWanye/simple-harness-sdk/blob/dffd13c829d42ac3df213ac0c490f3d5834a8b2b/src/simple_harness/agents/base.py
- [C03] `src/simple_harness/agents/runtime.py`：AgentRuntime构建、幂等创建、关闭/恢复与短期记忆装配。固定源码：https://github.com/DennyWanye/simple-harness-sdk/blob/dffd13c829d42ac3df213ac0c490f3d5834a8b2b/src/simple_harness/agents/runtime.py
- [C04] `src/simple_harness/agents/contracts.py`：AgentTurnResult等公共合同。固定源码：https://github.com/DennyWanye/simple-harness-sdk/blob/dffd13c829d42ac3df213ac0c490f3d5834a8b2b/src/simple_harness/agents/contracts.py
- [C05] `src/simple_harness/agents/config.py`：角色配置与执行限制，不含Mission/Task。固定源码：https://github.com/DennyWanye/simple-harness-sdk/blob/dffd13c829d42ac3df213ac0c490f3d5834a8b2b/src/simple_harness/agents/config.py
- [C06] `src/simple_harness/agents/ports.py`：Provider/权限/Context/embedding/并发参数。固定源码：https://github.com/DennyWanye/simple-harness-sdk/blob/dffd13c829d42ac3df213ac0c490f3d5834a8b2b/src/simple_harness/agents/ports.py
- [C07] `pyproject.toml`：打包、测试与类型检查配置。固定源码：https://github.com/DennyWanye/simple-harness-sdk/blob/dffd13c829d42ac3df213ac0c490f3d5834a8b2b/pyproject.toml

完整连接器只读核查未覆盖全仓、未运行SDK测试；当前Host不可读取。本文没有重新评估模型最优Context长度、没有引入外部Agent框架，也没有将前一轮理论建议混入原文要求。

## 附录：原文覆盖矩阵

## 原文31章

| 原文章节 | 建设步骤 | 实施边界 |
|---|---|---|
| §1 先理解它到底是什么 | 2, 3, 4, 5, 6, 7, 8, 9 | 以完整功能版本建设，不以Agent消息数作为目标。 |
| §2 设计目标与基本原则 | 2, 3, 4, 5, 6, 7, 8, 9 | 四条铁律从第2步生效并累计回归。 |
| §3 完整架构图 | 2, 3, 4, 5, 6, 7, 8, 9 | 原逻辑组件逐步补齐，模块化单体。 |
| §4 核心运行闭环 | 2, 3, 4, 5 | 最小闭环先跑通，再加共享与动态决策。 |
| §5 Mission：定义整个任务 | 2 | 完整Mission章程和停止条件。 |
| §6 Task DAG：任务与依赖关系 | 3, 5 | 先静态DAG，再动态Proposal/Commit。 |
| §7 Planner、Manager 与 Search Controller | 3, 5 | 先单Planner/Manager，再Search Controller；多层管理为原文可选扩展。 |
| §8 Frontier、Allocator 与 Scheduler | 3, 5, 6 | 先依赖Frontier与受控调度，再规则分配和背压。 |
| §9 Role 与 Model Router | 2, 5, 6 | 独立审阅起步；搜索角色与真实模型路由随后完成。 |
| §10 Context Builder 与 Retrieval | 3, 4, 5 | 任务包、依赖与角色可见性，不重写BaseAgent短期记忆。 |
| §11 Blackboard 与知识压缩 | 4 | 四层Blackboard、血缘、摘要与综合。 |
| §12 Agent Runtime 与生命周期 | 2, 3 | 复用已完成BaseAgent；Task/Attempt由编排层拥有。 |
| §13 Agent 输出协议 | 2 | Result Envelope，不接受Agent直接完成Task。 |
| §14 Verifier 验证体系 | 2, 4, 6, 7 | 按Task政策选择真实验证层、冲突处理与人工审核。 |
| §15 Proposal 与 Commit | 2, 5 | 唯一逻辑Commit入口，版本与幂等。 |
| §16 State、Event 与 Durable Execution | 2, 3, 8 | 状态/事件从第2步持久；执行恢复和历史Replay区分。 |
| §17 并发、冲突与幂等 | 2, 3, 5, 6 | CAS、版本、幂等、Single Writer、Lease/Heartbeat。 |
| §18 Budget、Cost 与 Backpressure | 2, 3, 6 | 预算启动就有；较高并发阶段完整背压。 |
| §19 停止、停滞、死锁与目标漂移 | 2, 5, 6 | 停止、停滞、循环、老化与目标漂移。 |
| §20 Workspace 与 Artifact | 2, 3, 4, 6 | 早期只读/隔离沙箱；后续强化Workspace和产物合并。 |
| §21 工具、安全与权限 | 2, 6, 7 | 工具与权限早期启用；细粒度与真实动作审批随后开放。 |
| §22 Human-in-the-loop | 7 | 风险分级、审批、仲裁/接管事件。 |
| §23 Observability、Tracing 与 Evaluation | 2, 4, 6, 8, 9 | 基础Trace早记录；完整运营视图、归因和实验工具逐步完成。 |
| §24 一次任务从开始到结束 | 2, 3, 4, 5, 7 | 每一功能版本都覆盖适用的从Mission到交付路径。 |
| §25 状态机设计 | 2, 3, 5 | Task/Attempt/Claim状态保持；动态替代不私自增加回边。 |
| §26 核心数据契约 | 2 | 六个数据契约在第一完整闭环内冻结，不单独作为空阶段。 |
| §27 代码模块划分 | 2, 3, 4, 5, 6, 7, 8, 9 | 保留原目录职责；仅增加contracts/storage等实现配套。 |
| §28 分阶段落地路线 | 2, 3, 4, 5, 6, 7, 8, 9 | 四阶段顺序保留，拆成2—9步骤。 |
| §29 第一版推荐配置 | 3, 5, 6, 9 | 人数/比例/公式按原文作配置起点，实际能力以部署评测为准。 |
| §30 验收清单 | 2, 3, 4, 5, 6, 7, 8, 9 | 29项原始清单逐项映射见下表。 |
| §31 最终心智模型 | 2, 3, 4, 5, 6, 7, 8, 9 | 原最终心智模型不替换为另一套顶层对象。 |

## 原文第30章的29项验收清单

文字直接摘自用户上传文档，以下是逐项实施映射。

| 编号 | 原文验收项 | 步骤 | 拟新增场景 |
|---|---|---|---|
| ORIGINAL-30-01 | 系统重启后可以恢复 Mission、Task 和 Attempt； | 2, 3 | S2-04, S2-05, S3-07 |
| ORIGINAL-30-02 | RUNNING 但 Lease 过期的 Attempt 可以重新调度； | 3 | S3-07 |
| ORIGINAL-30-03 | 同一个事件重复发送不会重复执行副作用； | 2, 7 | S2-03, S7-02, S7-06 |
| ORIGINAL-30-04 | 同一个 Attempt 不会被两个 Agent 同时领取； | 3 | S3-04 |
| ORIGINAL-30-05 | 完成的 Task 不会在恢复后重新执行； | 2, 3 | S2-05, S3-03 |
| ORIGINAL-30-06 | Task DAG 不允许形成循环依赖。 | 3, 5 | S3-02, S5-04 |
| ORIGINAL-30-07 | Claim 与 Verified Knowledge 分开存储； | 4 | S4-02 |
| ORIGINAL-30-08 | 每条正式知识有来源、证据和 Verifier 信息； | 4 | S4-01, S4-08 |
| ORIGINAL-30-09 | 被取代的知识可以标记 SUPERSEDED； | 4 | S4-04 |
| ORIGINAL-30-10 | 冲突结论可以同时保存并进入仲裁； | 4 | S4-03 |
| ORIGINAL-30-11 | Agent 默认不会把未验证 Claim 当成事实。 | 4 | S4-02 |
| ORIGINAL-30-12 | Mission、Task 和 Attempt 都有预算； | 2, 6 | S2-06, S6-07 |
| ORIGINAL-30-13 | 子任务预算来自父任务； | 3, 5 | S3-08, S5-08 |
| ORIGINAL-30-14 | Attempt 启动前先 Reserve 预算； | 2 | S2-03, S2-06 |
| ORIGINAL-30-15 | 有并发和队列上限； | 2, 6 | S2-06, S6-02 |
| ORIGINAL-30-16 | Verifier 积压时会触发 Backpressure； | 6 | S6-02 |
| ORIGINAL-30-17 | 连续无进展时会降级或停止。 | 5 | S5-02 |
| ORIGINAL-30-18 | 工具调用统一经过 Tool Gateway； | 2, 6 | S6-05 |
| ORIGINAL-30-19 | Agent 只拥有完成任务所需的最小权限； | 2, 6 | S6-05 |
| ORIGINAL-30-20 | 密钥不进入模型上下文； | 2, 6 | S6-09 |
| ORIGINAL-30-21 | 外部内容被标记为不可信数据； | 4, 6 | S4-08, S6-05 |
| ORIGINAL-30-22 | 高风险操作必须人工审批； | 7 | S7-01, S7-05 |
| ORIGINAL-30-23 | 所有真实世界副作用都有审计日志和幂等键。 | 7 | S7-02, S7-06 |
| ORIGINAL-30-24 | 每个 Mission、Task、Attempt 和 Result 有 Trace ID； | 2, 6 | S6-09 |
| ORIGINAL-30-25 | 能查看每个任务的完整事件链； | 2, 6, 8 | S8-01, S8-05 |
| ORIGINAL-30-26 | 能统计成本、成功率、重复率和验证通过率； | 6, 8 | S8-03 |
| ORIGINAL-30-27 | 能找出最终结果依赖的知识和 Agent； | 4, 8 | S8-01 |
| ORIGINAL-30-28 | 能通过 Replay 复现关键失败； | 8 | S8-02 |
| ORIGINAL-30-29 | Prompt、模型、Retrieval 和 Allocator 都有版本号。 | 2, 6, 8, 9 | S8-07, S9-04 |
