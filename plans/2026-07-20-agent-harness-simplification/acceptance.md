# 验收标准：DeskPet Agent Harness 提炼与简化

## 主要矛盾

同一个用户任务跨越 ReAct、durable workflow、Subagent/Team 和不同 venue 后，当前没有共享一套可信身份、Effect/Outcome 与生命周期契约；继续逐点修补会保留多个状态 owner 和旁路，使跨会话泄漏、并发写竞态、失败被投影成成功、取消/恢复不一致等问题反复出现。

本次目标不是把所有任务塞进一个万能引擎，而是建立**一个薄控制面、ReAct/Workflow 两种 Driver、Team/Subagent ChildRun 化、Voice Adapter 化**的结构，并实际删除重复 owner 与 legacy 新请求路径。

目标图与职责边界见 [`target-architecture.md`](./target-architecture.md)。

## 范围

- 包含：文字、Code、DeepResearch、PPT、Voice、Subagent、Team 的统一 Run 身份、路由入口、粗生命周期、父子关系、取消与 typed event 契约。
- 包含：ReAct Driver 与 Workflow Driver 保留各自调度语义，共享 ToolRegistry V2 与一个 SqliteExecutionUnitOfWork；effect/outbox 复用既有算法与 sink handler，但新 run 不允许各自打开第二条写连接。
- 包含：可信 `RunContext` 与 LLM/tool 参数彻底分离；模型不能覆盖 session、workspace、capability、effect 或 trace 控制字段。
- 包含：修复 unsafe 工具并发、同步工具超时后 late effect、失败结果投影、历史 receipt 错配、跨 session Subagent 消费/取消等已确认缺陷。
- 包含：新请求退出旧 Registry、legacy dispatch、全局 waiter/queue、Voice 自建 Harness、旁路 AutoResume 和分散路由；历史 workflow/checkpoint 只读/恢复兼容。
- 包含：真实运行 trace、degraded capability health、架构事实源、测试契约与真人 UI 验收同步更新。
- 明确不包含：把所有普通聊天改造成 durable graph；替换 Native Workflow Engine、Context OS 或 ToolRegistry V2；引入 Temporal/LangGraph/OpenAI Agents SDK 作为新生产运行时。
- 明确不包含：把 Goal、Plan、TeamTask、WorkflowRun 合并为万能任务表；删除用户历史数据；破坏公开工具名、现有用户数据格式或可见产品能力。
- 明确不包含：用长期双写、shadow 或默认关闭的新路径做灰度；完成能力必须默认开启，新请求只走新架构。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| AC-1 | 单一 Run 入口 | Text、Code、Voice 和后台 handoff 创建执行时均调用同一公开 Run API；Kernel 公开控制操作不超过 `start/observe/signal/cancel/recover/close`，新请求不存在绕过入口的 `_run_chat`/Voice/产品专用启动旁路。 | 必须 |
| AC-2 | 薄且产品无关的 Kernel | Kernel 只拥有身份、可信上下文、唯一路由调用点、粗生命周期、父子关系、signal/cancel 与 typed event；Kernel 源码中产品名称 import/条件分支为 0，产品策略由注册的 Router/Profile/Driver 提供。 | 必须 |
| AC-3 | 双 Driver 边界 | 普通对话/短任务进入 ReAct Driver；DeepResearch、PPT、复杂 Code 进入 Workflow Driver；不得把普通 token 流强制写成 graph，也不得让 durable 任务静默降级为不可恢复 ReAct。 | 必须 |
| AC-4 | 可信执行上下文 | `session_id/root_run_id/parent_run_id/request_id/turn_id/venue/workspace/capability_hash/provider_plan` 由宿主创建并与模型参数分离；模型传入保留字段时被拒绝且留下安全日志，handler 永远接收到宿主可信值。 | 必须 |
| AC-5 | ChildRun 所有权 | 所有非阻塞 Subagent、Team worker 和 workflow child 都能按 session/root/parent 查询；A 会话不能观察、await、注入或取消 B 会话 child；blocking 短并行可作为父 Run trace span，但不能进入全局无主队列。 | 必须 |
| AC-6 | Run-scoped 取消 | `/stop`、UI cancel 和内部取消都作用于明确 run scope；attached child 按策略级联，detached child 不因父 controller 正常 handoff 而误杀；Workflow、Subagent、Team 均产生 `cancel_requested → acknowledged/late-reconciled → cancelled` 可对账状态。 | 必须 |
| AC-7 | 单一工具执行边界 | ReAct 与 Workflow 均通过 ToolRegistry V2 与同一个 PreparedToolCall/Outcome 契约；写工具遵守调用顺序和 unsafe barrier，两个 `concurrency_safe=false` 工具不得并发；legacy `dispatch()` 不再服务新请求。 | 必须 |
| AC-8 | Effect 完整性 | 可恢复/可重试写调用在执行前获得稳定 effect id；grant consume 与 effect claim/start 同一 UoW 事务，外部执行在事务外，settle 再入事务；超时或崩溃窗口标记 `unknown` 并 reconcile，故障注入下不可逆 Effect 重复提交数为 0。 | 必须 |
| AC-9 | 统一 Outcome/Event | Tool 和 Run 使用 typed success/failed/accepted/waiting/cancelled/unknown 语义；Registry、AgentLoop、Workflow、SessionDB、WS、Voice 与前端对同一事件结论一致，`ok:false` 不得显示绿色成功，accepted 不得显示 completed。 | 必须 |
| AC-10 | 当前请求验证关联 | completion/Verify 证据必须绑定当前 run/turn、tool call/effect id、参数或目标摘要和 artifact；旧 session 中同名工具 receipt 不能使新请求通过，无法验证时使用显式 `unknown` 策略而非静默 pass。 | 必须 |
| AC-11 | 单一路由决策 | Code、DeepResearch、PPT、普通 ReAct 和 read-only/否定指令在同一 Router 生成一份可 trace 的 RouteDecision；“不要修改，只解释”等否定表达不得进入可写 Code workflow；Code venue 中 Research/PPT 不得落入无相应能力的 ReAct。 | 必须 |
| AC-12 | Voice Adapter 化 | Voice 只拥有 ASR/TTS/VAD/barge-in/PCM transport，Agent 执行通过同一 Run API；Voice 能正确处理 tool failure、approval、workflow handoff 和 final，且不改变全局 permission source。 | 必须 |
| AC-13 | 持久决策与恢复 | Plan、permission、clarification 等等待状态具有 run-scoped 持久 decision id；重启后 UI 响应可以恢复原 run，重复响应幂等；不得依赖仅存在于进程内的 Future 作为唯一 owner。 | 必须 |
| AC-14 | Goal 与终态衔接 | ReAct final、Workflow terminal 和 ChildRun join 都通过统一 finalization contract 更新关联 Goal/projection；一次根请求只有一个最终业务终态和一个 terminal delivery owner。 | 必须 |
| AC-15 | 显式健康与降级 | 默认开启的 Harness/Driver/Tool 能力初始化失败时，启动 health 和 UI/diagnostic 能指出缺失能力；不得在 broad exception 后把 durable、tool 或 child 能力静默替换为语义更弱路径。 | 必须 |
| AC-16 | 删除重复机制 | 新请求路径中旧 Registry/ToolUsingAgent、legacy dispatch、全局 Subagent queue、Voice 自有 AgentLoop bridge、旁路 AutoResume 和旧 waiter owner 均不可达；兼容代码只能读取/恢复历史版本，且有明确删除条件。 | 必须 |
| AC-17 | 可执行架构事实源 | Harness lifecycle/manifest 可从真实阶段、Driver 和事件注册表导出或由架构测试动态验证；`ARCHITECTURE/` 准确记录当前 Native engine、默认 workflow 版本、Driver 边界与验证证据，不再出现文档声称串行而生产全 gather 等漂移。 | 必须 |
| AC-18 | 功能与 UI 不缩水 | 原有文字、Code、DeepResearch、PPT、Voice、Artifact、approval、progress、history 等用户能力继续可用；完成后的新架构默认开启，不使用长期双路径或默认 OFF。 | 必须 |

## 非功能 / 边界

- 简化度：Kernel 目标不超过 450 LOC；Kernel 公开控制操作不超过 6 个；`execution/ + harness/ + execution_uow.py` 合计不超过 2,800 LOC；全部计数文件总 LOC 不超过 17,250 且相对 phase-0 基线净减少至少 20%；全局 registry/task-map 数量至少减少 50%；旧 owner 必须删除，不能只包一层 Facade。
- 回炉约束：首次 WI-12 已证明“删除旧 `_run_chat` 再补功能”不可接受。生产切换前必须对历史/Persona/Memory/Skill/MCP、附件、Problem Pipeline、Plan/Preference、Supervisor/summary、reasoning/context/pipeline UI 事件、billing/SessionActivity、Skill Codify 建立逐项 parity 证据；任一缺失即 AC-18 FAIL。
- 机械防漏：从旧生产 span/event/WS/SessionDB/vector/file/waiter/cancel/codify/permission 调用点自动生成 census，逐项映射新 owner 与 golden testcase；`unmapped_count` 必须为 0，不能仅依赖人工清单。
- 单 authority 约束：现有 `SqliteExecutionUnitOfWork` 是新 durable Run 的唯一事务 authority；effect/outbox 只能暴露同 connection 的 `*_tx` 原语；不得再并存独立 Ledger、Decision SQLite、Effect SQLite、Continuation SQLite 或第二套 durable delivery worker。
- Durable promotion：ephemeral ReAct 首次等待 Decision、写 Effect 或 ChildRun 时，durable run、continuation boundary、首个 durable command intent 与 waiting event 必须形成可恢复边界；Decision/Effect batch 同事务创建，Child saga 则先提交完整 command intent，再由 schedule 同事务创建 child+link 并 CAS scheduled。故障注入下不能留下“有 run 无 boundary”“有 decision 无 continuation”或“有 child 无 command”。
- 跨进程恢复：durable recover/reconciler 必须持有数据库 lease 与递增 fence epoch；进程内 active map 不能替代锁，过期 worker 的写入必须 CAS 失败。
- 测试态不走后门：R1～R5 的新路径测试必须在隔离 v7 DB 上调用真实 activation CAS 到 generation 1；生产 DB 保持 `legacy/0`。禁止 test-only bypass flag 或 legacy phase 下创建 kernel-owned row。
- 依赖方向：`deskpet.execution` 不得 import `deskpet.workflows`；PreparedToolCall/NormalizedToolOutcome 由 Driver/ToolRegistry 直接复用 workflow primitive，UoW 只接受中立 identity/JSON，不保留 `tools.registry → harness.tool_executor` 反向依赖。
- 原子切换：v7 additive schema 在 R1 以 `legacy` 默认态先落地并完成全套预演；R6 关入口、等待 ephemeral legacy=0、同事务持久化 durable drain manifest 并进入 `draining`，排空后再写 activation generation、切 start/recovery/delivery owner 并开入口。新 execution row 写入 `owner_kind/owner_generation`，首条新 row 产生后只允许 fail-closed/roll-forward。
- 计数反规避：ProductTurnPreparer、RunPresenter、venue adapter、Team reconciler 等迁移后的 orchestration 文件全部计入 LOC，不能通过移动出 `main.py` 或 `harness/` 逃避 20% 门槛。
- LOC 机械口径：锁定 phase-0=21,563 与 rollback=33,228 两份逐文件 manifest。manifest 内的 harness/orchestration 文件始终按当前全文计；BASE 时不存在的新 production `backend/**/*.py` 按全文计；BASE 已存在但不属于 manifest 的共享底座文件（例如 schema/effect/outbox）只计相对锁定 BASE path/hash 的 Git 正向 added-lines，删除行永不抵扣 harness LOC。迁移/复制的 manifest 内容仍按全文计，未知分类或基准 hash/总数不符直接 FAIL。只有 tests、生成代码、vendored 固定 path rule 可排除且先做 manifest 相似度归属；`.venv/.uv-cache/.uv-python/dist*` 明确是本地解释器/包缓存/冻结构建产物的硬非源码边界，不进入生产 LOC。R1 合并门必须运行 `harness_baseline.py --loc-only --r1-gate`，超过 33,228 非零退出。
- 单一所有权：route、cancel、effect commit、retry、terminal delivery 各有一个明确 authority；架构测试禁止 Kernel 与 Driver 同时拥有同一权能。
- 性能：普通 chat 的 Kernel 本地增量开销 p95 ≤10ms，端到端 TTFT p95 回归 ≤5%；每个 token delta 产生 0 次 SQLite 写；20 并发 session 下 event-loop lag p99 ≤20ms、吞吐回归 ≤10%。
- Durable 成本：每个 workflow node 的事务数和序列化字节数不高于 phase-0 基线 10%；accepted 与 terminal durable delivery 各恰好一次。
- 隔离：至少并行运行 2 个 session、覆盖 ReAct/Workflow/ChildRun 三类控制器并注入单一 driver/WS 故障；其他 session/driver 成功率 100%，无跨会话 event、await 或 cancel。
- 内存：排除消息/Artifact 内容后，Kernel metadata ≤128KB/active run；完成 10,000 个测试 Run 后无 completed-run 强引用，registry/RSS 回到基线 ±5%。
- 兼容：现有 AgentEvent 前端序列和 payload 保持兼容，明确批准修正的 ToolResult outcome 除外；历史 workflow v1–v7 checkpoint fixture 100% 可读取、恢复且不重写 immutable manifest。
- 数据迁移：所有 schema/event 迁移可重复执行并可从中断处继续；不删除用户历史；新写路径只能有一个 delivery owner。
- Crash 完整性：UoW/Team saga 导出的全部 fault hook 必须与参数化 fault matrix 一一相等；每个窗口都要有 restart actor、idempotency key 和逐表/外部写计数 oracle，不能只以 prose 声称覆盖。
- Delivery/Reconcile owner：新 run 只有一个 `ExecutionDeliveryDispatcher` 经 UoW claim/complete 并校验 owner generation；legacy dispatcher 只处理无 execution row 的历史 run。unknown effect 只有 Workflow/effect service reconciler，Kernel/Driver 不另起 supervisor。
- 默认启用：测试通过后新 Run Kernel 与 Driver 路径立即成为默认生产路径，不做 shadow、分批或默认关闭。

## 测试场景矩阵

| scenario_id | input_class | exact_input | primary_risk | gate_type | required | manual_required | terminal_expectation | quality_bar |
|---|---|---|---|---|---|---|---|---|
| S-1 | 只读解释与否定指令 | 不要修改任何文件，只帮我看看 deskpet 现在为什么这个测试会失败，并告诉我原因。 | read-only 路由、可信 scope、ReAct 流式输出 | positive-value | 是 | 是 | ReAct completed；0 个写 Effect | 给出基于真实仓库的非空原因说明，不进入 Code workflow，不产生文件修改 |
| S-2 | 可写复杂 Code 任务 | 请在测试工作区修复这个失败用例，修改前先给计划，执行后跑相关测试并告诉我改了什么。 | Code workflow、approval、Effect 幂等、Artifact/结果 | positive-value | 是 | 是 | Workflow accepted 后 terminal completed；测试通过 | 计划、批准、文件修改、测试证据和最终摘要完整；只交付一次终态 |
| S-3 | 长时调研任务 | 帮我调研 2026 年本地 AI 编程助手的主要技术路线、隐私取舍和适用团队，最后给出选型建议。 | DeepResearch 路由、ChildRun、重启恢复、进度与引用 | positive-value | 是 | 是 | accepted 后 durable completed；唯一报告/Artifact | 至少 3 个有效子方向、关键结论有可追溯来源、建议说明取舍而非资料堆叠 |
| S-4 | PPT 生成任务 | 帮我做一份介绍 DeskPet 当前架构和下一步优化方向的 PPT，先给我确认大纲再生成。 | PPT handoff、持久 decision、恢复、文件交付 | positive-value | 是 | 是 | waiting 可确认并恢复，最终 completed + 可操作 PPT Artifact | 大纲确认绑定同一 run；PPT 非空可打开，结构与主题一致，终态只投递一次 |
| S-5 | Voice 短任务与长任务 handoff | 通过语音问“先解释 DeskPet 的 Agent 架构，再帮我启动一份深入调研”。 | Voice Adapter、TTS、handoff、permission source 隔离 | positive-value | 是 | 是 | 短回答可听；长任务 accepted 并继续投递进度/终态 | 语音链路不静默结束、不串到文字会话；长任务最终可在同一 session 找到 |
| S-6 | 多会话并行与停止 | 在两个 session 同时启动一个 Subagent 任务和一个 DeepResearch，然后只停止其中一个。 | session ownership、cancel tree、跨会话泄漏 | negative-safety | 是 | 是 | 目标 run cancelled/late-reconciled；另一 run 正常完成 | 无跨会话结果注入、await、取消或 UI 卡片；停止结果与后台实际状态一致 |
| S-7 | 工具失败与 late effect | 触发一个受控超时写工具和一个权限拒绝工具，然后观察 UI 和恢复行为。 | typed failure、unknown/late reconcile、禁止重试重复写 | negative-safety | 是 | 是 | 不显示绿色成功；late effect 被对账；重复写为 0 | 用户能看懂失败/不确定状态；最终文件状态、receipt、effect journal 与 UI 一致 |

## 完成的定义（DoD 摘要）

- AC-1 至 AC-18 全部有自动化、故障注入、性能或真实 UI 证据；任何必须项 PENDING/PARTIAL 均不得完成。
- 先跑 2–5 个自然语言 value smoke；主要矛盾未解决时立即停止昂贵回归和打包步骤。
- S-1 至 S-7 全部走真实生产入口；原生 UI 场景使用 computer-use 真人点击/输入，不能用协议注入或脚本回放替代。
- unsafe 并发、跨 session ChildRun、tool failure 投影、timeout late effect、cancel/restart、历史 checkpoint 兼容均有专项自动化回归。
- 新旧架构切换不保留新请求双写/双 owner；历史 compatibility reader 有 fixture 和删除条件。
- 同步更新相关 `ARCHITECTURE/*.md`、`ARCHITECTURE/PROJECT_STATUS.md`、testcase 和计划证据。
