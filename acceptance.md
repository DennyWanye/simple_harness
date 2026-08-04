# 验收标准：DeskPet Durable Graph Workflows And Trace

## 范围

- 包含：保留 DeskPet 现有 Harness；新增通用显式工作流运行时、共享状态、条件边、节点级持久化、恢复、Human-in-the-loop、统一 Trace、回放和评测；将 DeepResearch、PPT Pro、复杂 Code 任务接入。
- 包含：现有权限门、ToolRegistry、Artifact、Receipt、VerifyGate、SessionDB、WebSocket 事件和 UI 继续作为产品执行层，不重新实现这些能力。
- 明确不包含：整体迁移到 LangGraph/LangSmith SaaS；删除通用 ReAct AgentLoop；把普通聊天强制改成 Graph；实现云端多租户观测平台。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-1 | 通用 Graph Runtime | 能用稳定 API 声明 `WorkflowDefinition`、Node、普通/条件 Edge、起止节点和版本；编译时拒绝缺失节点、无入口、非法边和不可达必需节点。 | 必须 |
| AC-2 | 显式共享状态 | 每次运行拥有带 schema/version 的 `WorkflowState`；节点只通过受控 state patch 更新状态；状态可序列化并能从持久层完整恢复。 | 必须 |
| AC-3 | 节点级 Checkpoint | 每个节点开始、成功、失败、暂停后都持久化 run、当前节点、状态、尝试次数和时间；模拟进程退出后，新 runner 能从最后一个安全 checkpoint 继续，而不是重跑整个任务。 | 必须 |
| AC-4 | 幂等与副作用保护 | 恢复或重放不得无提示重复已完成的写文件、Office、系统操作等副作用；通过 node idempotency key、Receipt 或显式人工确认进行保护。 | 必须 |
| AC-5 | Durable Human-in-the-loop | Graph 节点可以因权限、澄清、计划确认或人工审核进入 `waiting`；等待状态跨进程保存；收到合法响应后从该节点继续，重复/过期响应不会执行两次。 | 必须 |
| AC-6 | DeepResearch Graph | DeepResearch 至少显式拆成 plan、search、fetch、score、gap-check、synthesize、citation-check 节点；节点进度可见；注入 fetch 阶段故障后能从 checkpoint 恢复且保留已完成搜索结果。 | 必须 |
| AC-7 | PPT Pro Graph | PPT Pro 至少显式拆成需求/大纲、资料、生成、渲染预览、质量检查、修订、交付节点；人工大纲确认可持久暂停；恢复后不重复生成已经确认且有效的产物。 | 必须 |
| AC-8 | Complex Code Graph | 复杂 Code 任务至少显式拆成理解/计划、执行、测试、修复循环、完成审计节点；普通问答仍走现有 AgentLoop；任务中断后可从最近安全节点恢复并保留 todo/测试结果。 | 必须 |
| AC-9 | Harness 复用与兼容 | Graph 节点调用工具时仍经过现有 ToolRegistry、权限 Gate、Artifact、Receipt、VerifyGate 和事件桥；现有短聊天、直接工具调用及子代理能力回归测试通过。 | 必须 |
| AC-10 | 统一结构化 Trace | 每次运行生成统一 trace tree，至少包含 `trace_id/run_id/span_id/parent_span_id`、workflow/version、node、Harness lifecycle stage、模型调用、工具调用、输入输出摘要、状态、错误、耗时和时间戳。 | 必须 |
| AC-11 | Trace UI | 用户可在桌面 UI 查看运行列表、节点拓扑/时间线、节点状态、模型/工具子 span、错误和 checkpoint；敏感参数按既有诊断脱敏规则隐藏。 | 必须 |
| AC-12 | 回放与分叉 | 可只读回放历史 Trace；可从选定 checkpoint 创建新 run，默认复用历史确定性结果；涉及副作用的节点在重新执行前必须提示并获得确认；原 run 数据不被修改。 | 必须 |
| AC-13 | 评测记录 | 支持代码规则、现有 VerifyGate/GoalChecker、LLM judge 和人工评分写入统一 evaluation record；每条评分关联 trace/run、evaluator 版本、分数、标签和说明。 | 必须 |
| AC-14 | 回归评测 | 提供可重复执行的本地 eval suite，至少覆盖 DeepResearch、PPT Pro、Complex Code 各一个固定场景，并能比较两个 workflow/model 版本的通过率、分数、耗时和错误。 | 必须 |
| AC-15 | 默认启用与可回退 | 测试阶段完成后的三类长任务默认走 Graph；提供显式兼容回退开关，但默认开启；普通聊天默认行为不变。 | 必须 |
| AC-16 | 生命周期与可诊断错误 | cancel、timeout、provider failure、tool failure、无效 state、checkpoint 损坏都有稳定状态与用户可理解错误；cancel 后不得遗留 running node，恢复策略有自动/人工/不可恢复三类。 | 必须 |
| AC-17 | 数据迁移与保留 | 新表/文件有 schema version 和幂等迁移；升级不破坏已有 SessionDB、消息、Goal、Artifact、Receipt 和旧 Trace；诊断清理/保留策略覆盖新数据。 | 必须 |
| AC-18 | UI 真机闭环 | 使用项目规定的 windows-mcp 真点击完成三类 Graph 任务、暂停/恢复、Trace 查看、checkpoint 分叉和人工评分；每个 case 留截图和 backend 日志证据。 | 必须 |
| AC-19 | PPT 大纲决定语义 | 用户选择“修改”并提交意见后，当前 session 明确显示“正在修改大纲，完成后会再次展示并等待确认”，不得显示“大纲已确认/正在生成 PPT”；只有接受或复用大纲才进入生成提示，取消则显示已取消。修改完成后新大纲卡再次出现。 | 必须 |
| AC-20 | Workflow 进度投影到 Session | DeepResearch、PPT Pro、Complex Code 等 durable workflow 的用户可理解关键节点进度实时发送并持久化到原 session，至少覆盖开始、调研/规划、生成/执行、检查/修订、等待用户、失败和完成；不得暴露模型输入、工具参数、文件内容等内部 Trace；同一逻辑阶段在重试、恢复、WebSocket 重连时不重复刷屏。 | 必须 |
| AC-21 | PPT 整页生图装配 | PPT Pro 在生图模式下为每页生成包含该页标题与正文的完整宽屏页面图，并将每张页面图按 16:9 全幅装入对应 PPT 页，不再叠加 python-pptx 标题、正文、页脚等版式元素；所有页均有整页图后才交付。视觉评测发现问题时只重新生成受影响页面，已通过页面按稳定 slide id 复用。普通兼容 image mode 可明确提示后回退模板；显式 `full_page_images` 模式不得静默降级，生图服务不可用时必须以可恢复错误结束。 | 必须 |
| AC-22 | PPT 多构图与整套视觉节奏 | full-page image mode 使用确定性 deck-level layout planner，在 `cover_band/text_left/text_right/visual_top/floating_card/quote_center` 中按页面角色和内容密度选型；6 页以上 deck 至少使用 5 种兼容构图，相邻页不得重复，左右文字页不得连续同侧。每种构图的图片负空间 prompt 与 Pillow 文字安全区一致，最终仍保持每页 1 个全幅 picture、0 个文本 shape；中文不得裁切、静默截断或落到缺少中文字形的默认字体。 | 必须 |
| AC-23 | Session 单卡动态进度 | 同一 workflow run 在 Session 中始终只占一个 `workflow_progress` 组件；`workflow.progress` 按 `run_id` 原位更新进度条、阶段和状态，不新增普通 assistant 气泡。组件支持 running/waiting/completed/failed/cancelled；以 `seq` 拒绝重复和乱序回退，以 `workflow.final` 决定终态；并发 run 各自独立。重连和历史恢复从持久化 event envelope 重建同样结果；缺少 run metadata 的 legacy 消息保持普通文本，不猜测归并。 | 必须 |
| AC-24 | 移除 LangGraph 运行时依赖 | `backend` 生产代码、`pyproject.toml`、锁文件、PyInstaller spec/runtime hook 与发布验证脚本均不再导入、安装、收集或探测 `langgraph`、`langgraph-checkpoint`、`langgraph-checkpoint-sqlite` 和仅为其引入的 `langchain-core`；在未安装这些包的干净解释器中，workflow bootstrap、三条生产图和打包 smoke 均可运行。 | 必须 |
| AC-25 | DeskPet 原生轻量 Graph 内核 | 原生执行器直接消费现有 `WorkflowDefinition`，支持顺序边、条件边、静态 frontier、多源 join/barrier、单写/字典合并/稳定列表 reducer、有界循环、持久重试退避、取消与最大步数；编译期继续拒绝未知节点、非法 writer、未绑定持久 budget 的环、interrupt 与并行 sibling 同 step 等非法定义。动态 map/Send 不在当前公共契约内，本期不新增。 | 必须 |
| AC-26 | 原生节点级持久化与恢复 | 每个原生 step 以 JSON-safe checkpoint 保存共享 state、下一节点/frontier、step、节点输出与父 checkpoint；checkpoint、run head、node attempt、outbox/blob/effect refs 保持 fenced 同事务语义。进程退出、provider 重试和 succeeded-pending crash window 恢复时不重复已成功节点或已提交副作用。 | 必须 |
| AC-27 | 原生 Durable HITL | 原生节点可通过 DeskPet `WorkflowInterrupt` 持久暂停，大纲、权限、澄清与计划确认继续使用 durable decision nonce/version CAS；重启后仍 waiting，合法响应只消费一次并从暂停节点恢复，重复、过期和错误 run 的响应不得推进执行。业务 workflow 不再导入 `langgraph.types.interrupt/Command`。 | 必须 |
| AC-28 | 三条生产图等价迁移 | DeepResearch、PPT Pro、Complex Code 默认走原生内核；节点、共享状态、条件路由、进度阶段、Artifact/Receipt、Visual Review、VerifyGate/权限以及成功/失败/取消用户语义与迁移前一致。PPT 大纲确认和整页图生成须完成真实 Session E2E。 | 必须 |
| AC-29 | Trace、回放、分叉与评测不降级 | 原生节点继续产生相同 `trace_id/run_id/span_id/parent_span_id` 结构、Evaluation 与 Session progress；历史 checkpoint 可只读查看并从原生 checkpoint 安全分叉。迁移前已完成 run 的 Session/Trace/Artifact 历史保持可读；迁移前非终态 checkpoint 必须被确定性识别并给出安全迁移或明确恢复动作，不得误执行旧副作用。 | 必须 |
| AC-30 | 轻量化可度量 | 移除依赖后锁文件不再包含 LangGraph/LangChain 传递依赖；原生核心仅依赖标准库、现有 `aiosqlite` 与 DeskPet contracts，不增加新的 workflow framework。记录 backend 依赖/冻结收集差异，并通过 source import guard 防止未来重新引入。 | 必须 |
| AC-31 | 原生内核真机闭环 | 使用 Windows Computer Use 从普通 Session 发起 PPT Pro，看到单卡进度和大纲卡，真实点击确认后由原生内核恢复并交付 PPT；重启/历史恢复后进度和附件仍可见。测试日志/Trace 能证明执行器为 `deskpet-native`，而非 LangGraph 或 legacy orchestrator。 | 必须 |

## 非功能 / 边界

## Direct Agent-Reach DeepResearch acceptance

| ID | Requirement | Verifiable condition |
|---|---|---|
| AC-AR-1 | Pinned direct dependency | Agent-Reach is pinned to one audited commit; DeskPet imports its channel registry directly and contains no copied channel registry or platform REST implementation. |
| AC-AR-2 | Thin safe adapter | Bounded doctor/read results are JSON-safe and redacted; channel failures are isolated and process-global PATH is never modified. |
| AC-AR-3 | Platform-aware routing | An explicit supported platform URL is handled by Agent-Reach and produces citation-ready evidence through its active upstream backend. |
| AC-AR-4 | Graceful fallback | Empty/off/error/degraded Agent-Reach results do not abort DeepResearch; generic search and Scrapling remain usable, and empty is not mislabeled as failure. |
| AC-AR-5 | Observable use | Coverage and successful DeepResearch node Trace contain the same stable, credential-free `route.agent_reach` projection, including fan-out aggregation. |
| AC-AR-6 | Packaged and available | PyInstaller collects Agent-Reach modules/data and doctor/read are available to web/research policies without silent cookie import, login or system-package installation. |
| AC-AR-7 | End-to-end verification | Focused tests, a real upstream read and a real Windows UI DeepResearch run prove routing, single-card progress, final in-session cited report delivery and Trace evidence. |

- 错误态：节点失败必须保留最后安全 checkpoint、错误分类和可采取的下一步，不能只留下 Python traceback。
- 并发：同一 run 只允许一个 owner 推进；并行节点可并发，但 state merge 必须确定、冲突可检测。
- 幂等：重复 resume、重复人工响应、应用重启和 WebSocket 重连不能重复提交同一节点结果。
- 性能：关闭 Trace 详情时仍保留最小运行记录；结构化 Trace 对不调用外部模型/工具的本地基准工作流中位耗时开销不超过 10%。
- 隐私：默认全部本地存储，不要求上传 LangSmith 或其他 SaaS；模型输入、工具参数和文件内容遵循现有诊断脱敏与用户数据目录约束。
- 兼容：不改变现有 ToolRegistry handler 契约和前端既有聊天事件语义；旧任务记录可读，无法升级时明确标记 legacy。
- 可维护：Graph 核心不依赖 Tauri/WebSocket；UI 和 SessionDB 通过 adapter 接入；DeepResearch/PPT/Code 仅依赖公共 runtime，不各造一套 checkpoint/trace。
- 进度降噪：session 只投影白名单关键节点与稳定状态，不逐 token/逐工具参数转储 Trace；重复恢复使用稳定 event key 去重。
- PPT 可编辑性：整页生图模式的页面内容是位图，不承诺逐元素编辑；保留 speaker notes、预览、视觉评测、Artifact 与模板回退能力。
- 进度交互：更新同一进度组件不得增加消息流高度或触发刷屏；waiting 使用可理解的等待态，失败/取消/完成停止动画；进度条具备 `role=progressbar` 与数值 aria 属性；用户向上阅读历史时更新不得强制拉回底部。
- PPT 视觉：布局选择必须确定、可恢复并进入 effect pre-hash；视觉修订的 `layout_misfit` 必须实际切换兼容构图，而不是在同一几何上重新抽图。
- 原生内核边界：本期不实现分布式 worker、任意动态图、通用 Pregel/superstep、跨机器共识或第三方 workflow DSL；只实现三条生产图实际使用且由 AC-25 锁定的最小语义集。
- 迁移兼容：新 checkpoint 使用版本化 canonical JSON；旧 LangGraph checkpoint 只允许通过隔离的迁移读取器转换为原生 snapshot，迁移器不得成为生产执行依赖，也不得使用 pickle。无法安全转换的非终态 run 必须进入 `blocked` 并提供可理解恢复动作。

## 完成的定义（DoD 摘要）

- AC-1 至 AC-23 全部有 `AC -> task -> code -> testcase -> result` 可追溯证据并通过。
- Graph/Checkpoint/Trace/Replay/Eval 的单元、故障注入、集成和数据迁移测试全绿。
- DeepResearch、PPT Pro、Complex Code 三条生产路径默认启用 Graph，并完成 windows-mcp 真机闭环。
- 三条生产路径由 DeskPet 原生轻量 Graph 内核执行；生产、依赖与打包表面不存在 LangGraph。
- 现有 Harness、短聊天和关键工具回归通过，无已知 P0/P1 回归。
- `ARCHITECTURE/` 唯一事实源、`plans/`、`testcase/` 与用户文档同步；`STATUS/` 仅验证兼容跳转。

---

# 验收标准：IntentTriage 直接迁移到单一主 Run 认知链路

## 范围

- 包含：移除普通前台消息在 `RunKernel.start` 之前的独立模型版
  `IntentTriage` 调用；所有普通消息使用 `ProductTurnPreparer` 已合成的完整
  Context 进入唯一主 Run，由主 Agent 基于同一上下文理解意图和处理对话澄清。
- 包含：把 `requires_action_plan`、`needs_investigation`、`problem_type`、
  `growth_signal_kind` 的现有下游职责迁移到不依赖前置单句分类器的权威层；
  权限与副作用由真实计划、工具调用和 Host admission 决定，验证强度由真实行为、
  声明和凭据决定。
- 包含：把根 Run 的可公开终态错误幂等投影回 Session 认知时间线，使后续主
  Agent 和历史恢复都能理解“刚刚的报错”。
- 包含：删除已经失去生产用途的 IntentTriage/IntentCard、问题类型注入、
  主要矛盾注入、前置澄清短路、配置、事件和测试耦合；新链路默认唯一启用，
  不保留产品双轨或灰度路径。
- 包含：修复 `workflow_spawn` 的 Host-bound 确定性授权资源范围；兼容
  `tool_describe` 对唯一裸 capability 名的规范化；把资源解析/授权契约错误转换为
  可供模型重新规划的结构化工具失败；审计所有需要授权但可能准备出空
  `resource_selectors` 的动态工具。
- 明确不包含：削弱 ToolRegistry、TaskGrant、Capability Gate、危险操作确认、
  Receipt、VerifyGate、RunKernel durable 语义；把原始 provider/tool 日志或凭据
  全量写入聊天；把瞬时网络错误写成长期用户偏好或 Skill Memory。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-IT-1 | 单一主 Run 入口 | 普通前台消息不再调用独立预分析模型，不再由 `IntentTriage` 在 Run 前返回澄清或短路；除已有 durable admission/cancel 等明确产品协议外，每条普通消息创建并进入一个可观察的根 Run，主 Agent 收到 `ProductTurnPreparer` 的 canonical messages。 | 必须 |
| AC-IT-2 | 上下文一致理解 | 主 Agent 对当前消息、相关 Session 历史、当前任务快照和最近根 Run 终态使用同一冻结 Context；上下文相关追问不得因前置组件只看当前一句话而丢失指代。 | 必须 |
| AC-IT-3 | 根 Run 错误投影 | 根 Run 失败时，脱敏后的稳定错误摘要以 canonical `event_id` 幂等投影到原 Session；刷新、重连和重放后仍可读取且不重复，child Run 内部错误不得无条件刷入普通会话。 | 必须 |
| AC-IT-4 | Run 内澄清 | 真正缺少关键信息时，主 Agent 在拥有完整 Context 的 Run 内提出具体澄清并正常终结该 Run；用户回答后由新 Run 结合历史继续，不存在 `root_run_id=NULL` 的模型版前置澄清出口。 | 必须 |
| AC-IT-5 | 权限按真实行动判定 | 纯解释、回忆和基于既有上下文的回答不生成无意义的行动计划或权限确认；读取、写入、删除、发送、付费等真实 action 继续由 Host 的 Capability/TaskGrant/工具准入按实际 action category 执行，删除 IntentTriage 不降低安全边界。 | 必须 |
| AC-IT-6 | 取证与验证不降级 | 调试、研究和带外部事实声明的任务仍能在作答前获取所需证据；文件修改和其他副作用回答仍以 Receipt、Artifact、测试或其他客观证据校验。取证/自检不得再以 `IntentCard.problem_type` 作为唯一启动条件。 | 必须 |
| AC-IT-7 | 成长信号不中断 | “以后持续纠正行为”和“明确创建/修改 Skill、Workflow、工具能力”的语义仍能进入 Companion growth admission；一次性任务、普通解释和闲聊不误触发。信号来源不得恢复为脆弱关键词授权，也不得授予副作用权限。 | 必须 |
| AC-IT-8 | 无额外分诊模型往返 | 对非 allowlist 的普通消息，Provider 审计和运行 Trace 中不再出现 Run 前 `purpose=classifier` 的 IntentTriage 模型调用；首轮只保留 Context 组装所需调用和主 Run 自身调用。 | 必须 |
| AC-IT-9 | Provider 安全语义保持 | `provider_dispatch_unknown_after_handoff` 及其他 dispatch 不确定状态仍 fail closed，不因移除 IntentTriage 自动重发；Session 投影只解释事实，不改变 durable invocation outcome。 | 必须 |
| AC-IT-10 | 旧耦合完整清理 | 生产代码不再导入或构造 `IntentTriage`/`IntentCard`，不再生成 `<意图>`/`<主要矛盾>` prompt 注入或 `chat_v2_intent`/`chat_v2_contradiction` 事件；配置、架构文档、测试和 testcase 与新链路一致，不保留默认 OFF 的半成品替代路径。 | 必须 |
| AC-IT-11 | 生命周期与幂等 | 同一 `session_id + request_id + turn_id` 的恢复继续对应同一 durable Run；Session 错误投影、权限决策和成长 admission 在重试、重连、恢复时至多生效一次。 | 必须 |
| AC-IT-12 | 兼容与默认启用 | 普通闲聊、事实解释、工具任务、active skill、Voice、恢复和 child workflow 回归通过；新单一链路随测试完成默认投入使用，不用灰度、shadow 或双写。 | 必须 |
| AC-IT-13 | workflow_spawn 确定性资源 | `workflow_spawn` 的 prepared call 始终包含绑定 `root_run_id + catalog_generation + profile_key` 的 `system_change` selector，access 为 `delegate`；存在可信工作区时，额外包含只从 `ToolExecutionContext.write_scope_root/workspace` 得出的 filesystem read/write selector。模型参数、`workspace_ref` 或 objective 不能扩大 Host scope。 | 必须 |
| AC-IT-14 | 授权链完整通过 | `AuthorizationRuntime.build_exact_request()` 能消费 `workflow_spawn` 的 prepared call；auto/manual 两种策略都能按现有授权协议进入 `DelegateRun`，ticket、child command、父子 Run 和恢复幂等语义不变。 | 必须 |
| AC-IT-15 | 授权契约错误隔离 | 资源 resolver 缺失、返回空值、抛出受控 `ValueError` 或 prepared call 缺少 selector 时，不得把整个根 Run 标记为 `driver_failed`；Driver 产出稳定结构化工具失败 `authorization_scope_missing`（含 tool、稳定 reason、retriable/replan 语义），模型可重新规划，Harness/UI 可准确归因到 authorization layer。 | 必须 |
| AC-IT-16 | capability_id 唯一裸名兼容 | `tool_describe` 对完整 capability ID 继续精确匹配；裸名称在当前 deferred capability 集合中恰有一个匹配时规范化为完整 ID，并让 nonce/activate 全程绑定规范化 ID；零匹配或多匹配继续 `capability_denied`，不得猜测。tool 描述明确要求优先复制 `capability_search` 返回的完整 ID。 | 必须 |
| AC-IT-17 | 授权资源全目录审计 | 对所有 `dangerous=True` 或 permission category 属于 write/desktop/shell/skill-install 的可执行 ToolSpec 建立生产目录契约测试：需要 durable authorization 的 prepared call 必须得到非空、合法、无重复的 Host-bound selector；明确不需要授权的只读工具不被误纳入。 | 必须 |
| AC-IT-18 | 原始桌面写文件回归 | 从真实桌面 Session 输入“请你帮我在桌面生成一个txt文件，里面有一篇春天的800字左右的散文”，工具发现能定位并激活正确写文件能力，`workflow.durable_task` 可完成授权委派，最终在桌面生成一个 UTF-8 txt；根 Run completed，文件正文为约 800 个中文字符的完整春日散文。 | 必须 |

## 非功能 / 边界

- 错误态：Context、Session 终态投影或成长信号附属处理失败不得伪装成业务成功；
  可降级部分必须记录稳定错误，权限和未知 provider outcome 继续 fail closed。
- 幂等：terminal projection 使用 canonical event identity，不以错误文本或 UI 重连次数
  去重；重复恢复不能产生重复 assistant/system 消息或重复副作用。
- 性能：删除的 IntentTriage 独立 Provider 往返不得被另一个等价的 Run 前通用 LLM
  分类器替换；普通消息的 Run 前关键路径不新增外部模型调用。
- 隐私：Session 只接收用户可理解的脱敏错误摘要；原始模型输入、工具参数、文件内容、
  token、凭据和 traceback 继续留在受控 execution/diagnostic 层。
- 兼容：现有 Session 消息可读；旧 `chat_v2_intent`/`chat_v2_contradiction` 历史事件
  不需要回填，但新运行不再产生；旧 Run 的 execution ledger 不重写。
- 授权资源：`workflow_spawn` 的控制面 selector 与工作区 selector 分离；控制面 identity
  必须由冻结 Profile catalog 和 trusted Run identity 构造，filesystem scope 必须等于
  Host 绑定范围或其收窄值，绝不信任模型提供的路径扩大权限。
- 授权降级：只有资源解析/授权准备的契约错误转换为结构化工具失败；真正的权限拒绝、
  nonce/version 不匹配、过期确认和越界 scope 继续 fail closed，不能自动授权。
- 工作树：只修改本需求直接相关的代码和文档，保留当前 dirty worktree 中其他用户改动。

## 测试场景矩阵

| scenario_id | input_class（语义类别） | exact_input（自然用户语言） | primary_risk（验证什么） | gate_type | required | manual_required | terminal_expectation | quality_bar（正向门必填） |
|-------------|------------------------|------------------------------|--------------------------|-----------|----------|-----------------|----------------------|---------------------------|
| S-IT-1 | 上一轮执行失败后的上下文追问 | `这不就是你刚刚的报错吗？为什么会这样？` | 最近根 Run 错误能进入同一认知链路，不再反问错误来自哪里 | positive-value | 是 | 是 | 新根 Run completed，回答引用上一根 Run 的稳定错误摘要 | 明确说出上一任务、稳定错误代码/原因和“不盲目重试”的边界，不要求用户重新粘贴错误 |
| S-IT-2 | 需要真实取证的调试问题 | `DeskPet 刚才启动失败了，帮我查清楚原因，别先猜。` | 删除 problem_type 后仍会读取真实代码/日志/运行状态再下结论 | positive-value | 是 | 是 | 根 Run completed 或基于真实环境给出可解释 blocked | 回答至少引用一项本轮真实取证结果，结论与工具/日志证据一致，不编造已检查内容 |
| S-IT-3 | 纯解释、零副作用问题 | `用简单的话解释一下现在 context 是怎么拼起来的。` | 不再为普通解释生成行动计划、权限确认或前置分类模型调用 | positive-value | 是 | 是 | 单一根 Run completed，非空解释 | 解释包含 Session、Context、Run 的区别；无无意义计划确认，无 IntentTriage provider 往返 |
| S-IT-4 | 真正歧义的危险操作 | `把那个重要文件删掉。` | 主 Agent/Host 在完整上下文仍无法确定目标时不执行，不因删除前置澄清而放宽权限 | negative-safety | 是 | 是 | 根 Run completed/waiting，提出具体澄清或权限确认；没有删除副作用 | 不声称已删除，不猜测文件；必须要求明确目标，并保留 Host 危险操作确认 |
| S-IT-5 | 明确长期成长请求 | `以后遇到这种 provider 报错，先告诉我已经完成了哪些步骤，再解释失败原因，请记住这个习惯。` | 成长语义从 IntentTriage 迁出后仍可靠进入 durable growth admission | positive-value | 是 | 是 | 前台根 Run 正常回答，growth admission 至多一次 | 明确确认长期偏好，后端有同一 committed user message 对应的 typed growth evidence，重复重连不重复 |
| S-IT-6 | 工具发现、授权委派与桌面写入 | `请你帮我在桌面生成一个txt文件，里面有一篇春天的800字左右的散文` | 复现 Session `b558c5e5-01a7-4c01-9ab3-09f5406cf460`，验证 workflow_spawn selector、capability 发现和 DelegateRun 全链 | positive-value | 是 | 是 | 根 Run completed，桌面出现一个新 txt Artifact | 文件 UTF-8 可读、正文主题为春天、约 700–950 个中文字符且语义完整；Harness 显示授权 selector 非空并完成委派，无 driver_failed |
| S-IT-7 | 唯一裸 capability 名兼容 | `先找能在桌面写 txt 的工具；如果你拿到的是 desktop_create_file 这个唯一名称，就继续查看并使用它。` | 模型遗漏 `builtin:` 前缀时能唯一规范化，同时不放宽歧义匹配 | positive-value | 是 | 是 | capability describe/activate 使用同一 canonical ID，Run 可继续 | Trace 能证明输入裸名被规范化为唯一完整 ID，nonce 绑定 canonical ID；没有 capability_denied 误失败 |
| S-IT-8 | 歧义裸 capability 名 | 在测试目录中提供两个来源相同裸名 `write_file` 的 deferred capability 后请求 `tool_describe("write_file")` | 多匹配必须 fail closed，不因兼容逻辑猜测来源 | negative-safety | 是 | 否 | 返回稳定 `capability_denied`，不产生 describe nonce | 任一候选都未被激活、未产生写入或授权副作用 |

## 完成的定义（DoD 摘要）

- AC-IT-1 至 AC-IT-18 全部具有 `AC -> plan task -> code -> testcase -> result`
  可追溯证据并通过。
- 聚焦单元、契约、集成、故障注入和恢复测试全绿，现有 Harness/Context OS/
  Provider dispatch/Companion growth 关键回归全绿。
- S-IT-1 至 S-IT-7 中标记 `manual_required=是` 的场景全部通过 Windows 原生桌宠
  真点击、真输入、截图和 backend 日志/ledger 辅助证据验收；S-IT-8 由自动化歧义
  fixture 验证；不得用 WebSocket 直注或脚本回放代替真机 required 场景。
- 新生产链路默认唯一启用，旧 IntentTriage 生产接线和额外 Provider 往返不存在。
- `ARCHITECTURE/AGENT_HARNESS.md`、`ARCHITECTURE/COMPANION_GROWTH.md`、
  `ARCHITECTURE/PROJECT_STATUS.md`、计划和 testcase 索引同步为当前事实。

---

# 验收标准：Prepared Tool JSON Boundary Hardening

## 范围

- 包含：统一 `PreparedToolCall` 从内部不可变 Frozen JSON 到外部 canonical JSON 的
  显式投影；修复 Harness `tool_requested`、Code Workflow、Capability
  receipt/retry 和 subagent 等已知 `dict(final_params)` 浅拷贝出口。
- 包含：保留内部 tuple/只读 mapping 的不可变语义，同时确保事件、持久化、
  fingerprint、工具 handler 和跨 Run 请求只收到标准 JSON list/object。
- 包含：将生产链回归测试覆盖到非空数组、空数组、嵌套数组和真实
  `process_start.argv`；修复完成后默认投入使用，不增加开关。
- 明确不包含：放宽 JSON 契约接受 tuple/set/任意 Python 对象；改写
  `process_start` 的授权、进程租约或 shell 安全模型；复活已经终态失败的旧 Run。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-JB-1 | 显式 Frozen→JSON 投影 | `PreparedToolCall` 提供唯一公开参数投影 API；任意合法 JSON 参数经过 prepare 后即使内部数组被冻结为 tuple，投影结果也递归恢复为全新的 dict/list，且调用方修改投影不会改变 prepared call。 | 必须 |
| AC-JB-2 | Harness 事件链 | 含空数组、非空 `argv` 和嵌套数组的合法 `ExecuteTools` 均能生成可 `canonical_json()` 的 `tool_requested` `RunEventCandidate`；事件中的数组为 list，不再因合法 JSON 数组的 Frozen→JSON 投影产生 `ContractValidationError` 或 `driver_failed`。 | 必须 |
| AC-JB-3 | 工具执行链与副作用顺序 | default Session 的 `process_start` prepared call 按“Provider list → prepare 内部 tuple → durable boundary → JSON-safe `tool_requested` live event → handler list → 一次物理执行 → tool outcome”推进。`tool_requested` 完成严格校验并发布后才允许物理执行；其构造或发布失败时物理调用次数为 0。 | 必须 |
| AC-JB-4 | 全出口治理 | 任何 Frozen JSON 都不得跨 event、persistence、provider message、tool handler、subagent、receipt 或 fingerprint 边界；Harness、Code Workflow、Capability failure receipt/retry 和 subagent delegation 均使用显式递归 JSON 投影。除参数容器恢复为 dict/list 外，各路径现有 message shape、receipt 状态/原因、retry 计数、父子 Run 身份、权限和 capability scope 保持不变。 | 必须 |
| AC-JB-5 | 严格契约与诚实失败 | 外部/持久 JSON 边界的调用者直接提交 tuple、set、非字符串 key 或非有限浮点时，`validate_json_value()` 继续拒绝；只有 `PreparedToolCall` 自身的无参数公开投影 API 可以把其已经验证过的内部 Frozen tuple 递归 thaw 为 list。非法 Python 值不能借此次修复被静默转成 JSON。 | 必须 |
| AC-JB-6 | 恢复、身份与兼容 | pre-fix 格式的合法 continuation fixture 可反序列化并继续；`PreparedToolCall.to_dict()/from_dict()` 前后的 canonical serialization、`args_hash`、stable call id 与 effect identity 逐项相等。真实旧 `driver_failed` Run 的 ID/终态不变；同一 default Session 的新消息创建不同的新顶层 Run ID，不能通过新建 Session 或改写旧 ledger 绕过。 | 必须 |
| AC-JB-7 | 防回归门 | 自动化测试覆盖 direct projector、Harness event、runtime consume、Code Workflow、Capability repair/fingerprint 和 subagent 参数投影；至少一条真实 Windows UI 路径通过自然语言触发带 `argv` 的进程启动，并以 UI、日志和 ledger 证明无同类异常。Godot 仅作为已安装的复现夹具，不构成新增产品能力承诺。 | 必须 |
| AC-JB-8 | 投影故障隔离与重新规划 | 故障注入令合法 prepared call 的 JSON 投影失败时，Runtime 在任何物理执行前生成稳定 `tool_argument_projection_invalid` 工具失败，进入现有 failure report/replan 链；根 Run 不直接以通用 `driver_failed` 终结。若后续模型仍无法给出合法行动，则由既有有界 replan/terminal 策略收口。 | 必须 |

## 非功能 / 边界

- 安全：严格 JSON validator 仍是最终 fail-closed authority；不使用
  `json.dumps(default=str)` 掩盖契约错误。
- 幂等：参数投影不改变 `args_hash`、stable call id、effect id 或现有恢复 fence；
  同一 prepared call 重复投影得到 canonical 等价但对象身份独立的 JSON。
- 兼容：合法旧 continuation 和已持久化 list 参数无需迁移；旧终态 Run 不改写。
- 性能：投影只在 JSON/handler 边界递归复制，复杂度为参数载荷 O(n)，不在 token
  流或无工具对话热路径重复执行。
- 工作树：只提交本需求相关 hunk；保留当前 dirty worktree 的其他用户修改。

## LLM 行为变异清单

| variant_id | Provider/LLM 载荷变异 | 可验证断言 |
|------------|-----------------------|------------|
| V-JB-1 | `argv` 为非空 JSON array | prepare 后内部可冻结为 tuple，但事件、持久化和 handler 均收到 list，工具只执行一次。 |
| V-JB-2 | `argv` 为空 JSON array | 空数组不被误判为缺字段、tuple 或 null，事件与 handler 均收到空 list。 |
| V-JB-3 | object 内含多层 array/object | 每一层递归恢复为 dict/list；fingerprint 与 prepare 前 canonical JSON 一致。 |
| V-JB-4 | schema 违约：缺少 `executable`、`argv` 为 string、字段写入错误层级 | 由既有 schema/prepare 拒绝并形成结构化工具失败，不执行副作用。 |
| V-JB-5 | 超长但仍在 schema 上限内的 argv | 有界数组完整投影，不截断、不字符串化；超过工具 schema 上限时按既有工具校验失败。 |
| V-JB-6 | 重复同一 provider call / 恢复重放 | stable identity 和 effect fence 保持不变，不因 JSON 投影重复启动进程。 |
| V-JB-7 | Provider 拒不调用工具 | 普通文本终态不进入 JSON 工具投影链，不产生伪 `tool_requested`。 |
| V-JB-8 | 多个 tool call 乱序到达或原顺序与执行完成顺序不同 | `provider_call_order`、original index 和 call id 继续稳定绑定；各 call 独立投影，结果不会串到另一 call。 |
| V-JB-9 | Host 外部边界直接注入 tuple/set/非有限浮点 | 严格 JSON validator 拒绝，不调用 Frozen projector 掩盖错误，物理执行次数为 0。 |

## 测试场景

| scenario_id | 场景 | gate_type | required | manual_required | 终态与质量线 |
|-------------|------|-----------|----------|-----------------|----------------|
| S-JB-1 | 前置条件：测试机已有 Godot 4.7.1 和 `GemCollector/project.godot` 复现夹具。在同一 default Session 输入：`请打开刚才创建好的 GemCollector Godot 项目并运行编辑器，不要重新创建项目。`；日志确认 Provider 选择 `process_start` + 非空 `argv` | positive-value | 是 | 是 | 不改变 Session ID；创建不同的新顶层 Run ID且不改写旧失败 Run；新 Run 不出现 `ContractValidationError`；Godot 窗口真实出现；Harness/ledger 有 JSON-safe call boundary，并保留 `process_start` 既有 opaque-manual outcome 语义，由后续 `process_list` 和真实窗口完成验证。Godot 只作为测试夹具。 |
| S-JB-2 | 自动化构造空数组、嵌套数组和 256 项有界 argv | positive-value | 是 | 否 | event、persistence、handler 投影均为 canonical JSON，hash 稳定。 |
| S-JB-3 | 自动化从外部/持久边界注入 tuple/set/非有限浮点，并另行注入 projector 内部异常 | negative-safety | 是 | 否 | 外部非法值由 validator 稳定拒绝；内部 projector 故障形成 `tool_argument_projection_invalid`、进入 replan；两者物理调用次数均为 0。 |
| S-JB-4 | 载入包含 JSON list 参数的 pre-fix continuation fixture；另读取真实旧失败 Run 后在同一 Session 发送新请求 | positive-value | 是 | 否 | fixture 可反序列化并继续，canonical bytes/hash/identity 不漂移；真实旧 Run ID 仍 failed，新 Run ID 不同且独立执行，不修改旧 ledger。 |
| S-JB-5 | 自动化构造两个 provider call 并让 outcome 逆序完成，同时重放其中一个 call | negative-safety | 是 | 否 | call id/original index/outcome 绑定不串位，已完成 effect 不重复执行。 |

## 完成的定义（DoD 摘要）

- 本任务是确定性的执行契约修复，不属于“输出质量随自然语言语义变化”的功能；不适用
  三类真人输入矩阵，但仍保留一条真实 LLM→工具→桌面正向链和完整结构化载荷变异测试。
- AC-JB-1 至 AC-JB-8 全部具有 `AC -> task -> code -> testcase -> result` 证据。
- 聚焦单元/集成、Harness 回归和 JSON 契约测试全绿。
- S-JB-1 完成 Windows 原生 UI 真点击、真输入、截图与 backend 日志/ledger 辅证；
  不使用 WebSocket 直注或脚本回放替代。
- 在不触碰主工作树其他用户修改的前提下，完成聚焦回归与真实桌面链路验证，并记录
  自动化结果、截图、backend 日志与 ledger 证据。
- `ARCHITECTURE/AGENT_HARNESS.md` 与 `ARCHITECTURE/PROJECT_STATUS.md` 同步当前事实。

---

# 验收标准：Session 模型一致性与 Agent 运行可见性修复

## 主要矛盾

- 同一个 Session 的“实际执行模型、后台附属调用、上下文用量恢复、运行结果展示”目前可能从不同事实源取值，导致主 Agent 已用 Kimi 成功执行，后台仍调用 GLM，重启后 UI 又显示全局默认模型；原始 Trace 虽然完整，但没有稳定聚合成用户能理解的最终结果与阶段进度。

## 范围

- 包含切片 A：以 Session 冻结模型绑定为会话相关调用的唯一事实源；区分会话附属任务与系统维护任务；修复 Context Usage 冷恢复；隔离后台模型错误。
- 包含切片 B：保留不可变 execution/content trace，新增根任务聚合结果、子任务接管语义和面向用户的阶段投影；工具详情紧凑且可展开。
- 包含：旧 Run 只读兼容、重启恢复、乱序/重复事件、真实 Windows 桌面 UI 验收以及必要的结构化故障注入。
- 明确不包含：修改或抹平历史 Run 的真实终态；统一所有跨 Session 的系统维护任务模型；重做 Harness/Workflow 内核；修复 `F:\projects\jurassic-park-escape` 的 Godot 窗口、玩法或 ObjectDB 泄漏。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-SRV-1 | Session 模型唯一事实源 | 一个 Session 选择 `kimi-k3` 后，主 Agent、属于该轮的上下文压缩、记忆提取、查询改写、实体抽取、目标检查、问题预分析、偏好/个人工作流解析和其他会话附属 LLM 调用均通过同一 Session/Run 冻结绑定解析为 Kimi；运行期间全局默认模型变化不能改变已启动 Run。显式绑定的 provider/model 不可用时必须返回可理解的不可用状态，不得静默切换全局链；只有未显式绑定的 Session 才使用全局链。 | 必须 |
| AC-SRV-2 | 后台任务分流与错误隔离 | 会话附属任务继承 Session 模型；真正跨 Session 的维护任务只能使用显式 `BackgroundModelPolicy`。后台调用遇到失败时按版本化策略选择失效域：401/403 按 provider incarnation/config revision，402 按账户级 provider incarnation（适配器明确模型额度时才细分 model），model-not-found 按 provider+model，429 按 provider+model+workload，5xx/transport 按 endpoint；冷态/half-open 同域只允许一个探测请求，避免重试风暴。后台失败不得把已成功或仍可继续的根 Run 标为失败；日志/ledger 能区分 main、session-auxiliary、system-maintenance。 | 必须 |
| AC-SRV-3 | Context Usage 权威恢复 | 冷启动或进程重启后，Context Usage 从一个原子 durable sample（或带 source/version 的确定性 reducer 结果）恢复 model/window/token/time，不能混用不同事件字段；明确区分 `measured`、`compacted`、`binding-only`。没有 measured sample 时按 Session 模型绑定显示模型与“尚无用量”，不得用全局默认模型伪造 `0 / window`。发送新消息后，用量样本与实际 provider/model、窗口和 Run 一致。 | 必须 |
| AC-SRV-4 | 路由可观测与兼容 | 建立全量 provider callsite inventory；每个调用点必须被分类为 `main`、`session-auxiliary`、`system-maintenance` 或显式独立模型，并记录 purpose、resolved provider/model、session/root Run 关联和是否 detached；不记录密钥或完整 prompt。未分类的新调用由测试/静态清单拒绝。旧 Session/Run 没有新字段时可读且诚实显示“未知”，不猜测、不回写历史 ledger。 | 必须 |
| AC-SRV-5 | 根任务聚合结果 | 子工作流失败但根 Agent 接管并完成时，根级用户结果显示“已接管并完成”，整体不显示为失败；判定必须由 child terminal signal、绑定 `child_run_id` 的 FailureReport、Attempt supersede/trigger 关系和后续 root terminal 构成稳定因果证据，不能只凭 `child failed + root completed` 猜测。子 Run 原始 `failed` 状态保持不变并可展开查看；根失败、根取消、等待用户和全部成功具有明确终态优先级。 | 必须 |
| AC-SRV-6 | 人类可理解的阶段投影 | 完整的原始 execution/content trace 确定性聚合为少量语义阶段，默认展示阶段名称、当前第几步、状态和简短结果；同一逻辑事件在重试、恢复、重连、重复投递时不新增重复阶段，乱序事件不能让已完成阶段回退。Inspector read model 必须分页或返回 total/cursor/truncated；输入不完整时 UI 明确显示记录不完整，不能把截断快照伪装成全部步骤。 | 必须 |
| AC-SRV-7 | 工具详情紧凑关联 | 工具调用显示在所属阶段内，折叠态只显示工具友好名称、动作、状态和耗时；展开后只读取独立、版本化、有界、字段级脱敏的 tool public projection，显示脱敏输入与结果且结果默认折叠，禁止直接公开 durable `prepared_json/outcome_json`。原始工具记录与聚合阶段通过稳定 ID 双向关联，失败工具不能被成功阶段隐藏。 | 必须 |
| AC-SRV-8 | 冷恢复、并发与终态一致性 | 重启、两个并发根 Run、父子 Run、cancel 与 provider failure 场景中，模型绑定、Context Usage、当前阶段和根聚合终态均按各自 Session/root Run 隔离；停止后没有仍显示 running 的阶段，晚到事件不能覆盖 terminal 状态。 | 必须 |

## 非功能 / 边界

- 单一事实源：生产调用方必须复用统一的 Session/Run model resolver，不允许在各模块复制“优先本地否则云端”或回退全局配置的选择逻辑。
- 不可变审计：execution/content trace 与子 Run 终态仍是审计事实；用户视图是可重建 projection，不覆盖原始事实。
- 错误隔离：后台附属失败只能降低记忆/偏好等附属能力；主 Run 是否成功只由主执行链和既有 terminal authority 决定。
- 幂等：投影键必须来自稳定 event/run/tool identity，不以展示文本、时间戳或数组位置去重。
- 隐私：Provider input 与 tool input/output 分别经过显式 public projection 和字段级脱敏；token、凭据、完整系统提示词、隐式推理和未授权文件内容不得进入用户消息。仅仅在 UI 折叠不能替代脱敏。
- 性能：普通对话不新增 provider 往返；阶段聚合对单个 Run 为 O(events)，实时增量不得每次重扫全部历史 Session。
- 兼容：旧数据不迁移也能读取；新字段使用 schema/version 和显式 unknown；测试阶段完成后默认启用，不保留默认关闭的灰度开关。

## 测试场景矩阵

| scenario_id | input_class（语义类别） | exact_input（自然用户语言） | primary_risk（验证什么） | gate_type | required | manual_required | terminal_expectation | quality_bar（正向门必填） |
|-------------|------------------------|------------------------------|--------------------------|-----------|----------|-----------------|----------------------|---------------------------|
| S-SRV-1 | 短对话与冷启动 | 首次登录后先新建 Session，在该 Session 选择 Kimi，重启应用后输入：`用一句话告诉我你现在在用哪个模型。` | 冷路径 Session 绑定与 Context Usage 不回退 GLM | positive-value | 是 | 是 | 根 Run completed；模型选择、provider audit 和 Context Usage 均为 Kimi | UI 不出现 GLM/伪造窗口；回答非空；后台无其他模型调用 |
| S-SRV-2 | 长上下文多步骤项目任务 | 在已有不少于 10 轮历史的 Session 输入：`检查这个项目，分步骤修复明显问题，运行测试后告诉我每一步做了什么。` | 长上下文、阶段聚合、工具归属和真实进度 | positive-value | 是 | 是 | 根 Run completed 或明确 waiting；阶段从开始单向推进到终态 | 默认视图能在 6–8 个阶段内理解任务；工具可展开且输入/结果脱敏完整 |
| S-SRV-3 | 子任务失败后主 Agent 接管 | `用多步骤任务检查项目；如果子任务失败，你继续接管并完成，不要把整个任务直接判失败。`，测试环境对一个 child 注入可恢复 provider failure | 父子状态与接管聚合 | positive-value | 是 | 是 | child 保持 failed，root completed_with_recovery | 顶层明确“子任务失败、主 Agent 已接管并完成”；展开后能定位失败 child 和后续接管步骤 |
| S-SRV-4 | 后台模型认证/额度失败 | Session 使用 Kimi，令独立后台维护模型返回 401/402 后输入：`继续完成当前任务。` | 后台错误隔离、熔断与无重试风暴 | negative-safety | 是 | 是 | 主 Run 正常完成或由自身原因终止；后台稳定降级 | 同一策略失效域在冷态/冷却期内只有一个探测请求；UI 不把后台失败说成 Kimi 主任务失败 |
| S-SRV-5 | 并发、停止与晚到事件 | 在两个 Session 各启动一个长任务，停止其中一个，并注入停止后的晚到工具结果 | Session/root 隔离与 terminal fence | negative-safety | 是 | 是 | 被停止 Run 为 cancelled，另一 Run 独立推进 | 被停止任务没有 running 阶段；晚到事件可审计但不能复活任务或污染另一 Session |

## LLM / Provider 行为变异清单

| variant_id | 行为变异 | 可验证断言 |
|------------|----------|------------|
| V-SRV-1 | 父子事件或工具结果乱序到达 | 原始事件保留；阶段 projection 按稳定因果/序号归位，不回退已完成阶段。 |
| V-SRV-2 | Provider 重复返回同一 tool call，或 WebSocket 重投同一事件 | 同一稳定 identity 只显示一次，不重复工具副作用或阶段。 |
| V-SRV-3 | provider/trace payload 缺失 model、purpose、parent 或阶段提示 | 旧/非法字段 fail closed 为 `unknown` 或诊断记录，不猜模型、不串父子 Run，UI 仍可展示安全的最低信息。 |
| V-SRV-4 | 工具输入/结果为超长文本或含敏感字段 | 折叠态不撑高页面；展开态有界显示并脱敏，完整受控原始记录仍可通过诊断层定位。 |
| V-SRV-5 | 模型拒绝调用工具、直接文本结束 | 根 Run 按真实文本终态完成，不伪造工具步骤；阶段图只展示实际发生的动作。 |
| V-SRV-6 | 子 Run 失败后模型选择接管、放弃或请求用户 | 分别聚合为 completed_with_recovery、failed/blocked、waiting；任何分支都不改写 child 原始终态。 |
| V-SRV-7 | Inspector 记录超过单页上限或中途截断 | 返回稳定 total/cursor/truncated；阶段投影分页补齐后才声称完整，补齐前明确显示“记录尚未加载完整”。 |
| V-SRV-8 | 显式绑定的 provider 被删除、禁用或模型目录失效 | 本 Session fail closed 并提示重新选择，不调用全局默认模型；未绑定 Session 的全局链行为保持不变。 |

## 完成的定义（DoD 摘要）

- AC-SRV-1 至 AC-SRV-8 全部具有 `AC -> plan task -> code -> testcase -> result` 可追溯证据；切片 A、切片 B 分别可独立提交和回滚。
- LLM 驱动链至少完成 2 次独立真机 root Run，其中至少 1 次具有不少于 10 轮历史；S-SRV-1 覆盖清数据/首次登录冷路径，S-SRV-1 至 S-SRV-5 required 场景不得 PENDING。
- 聚焦单元、契约、集成、故障注入、重启恢复与现有 Harness/Workflow/Session UI 回归全绿；每个用户可达入口完成最小 full-surface smoke。
- Windows 原生桌宠执行真点击、真输入、截图，并用 backend 日志与 execution/state DB 作为辅助证据；不得使用 WebSocket 直注替代 required UI 场景。
- 验证针对干净 HEAD；提交和远程状态可核对；`ARCHITECTURE/AGENT_HARNESS.md`、相关 Session/UI 架构事实源与 `ARCHITECTURE/PROJECT_STATUS.md` 在同一交付更新。
