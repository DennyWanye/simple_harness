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
