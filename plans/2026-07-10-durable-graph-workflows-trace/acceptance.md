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

### AC-14 验收证据

- 默认 `scripts/eval_workflows.py` 通过内置 adapter 真实执行三张 v1 图；fixture 的旧 `output` 字段不参与默认评测。
- DeepResearch、PPT Pro、Complex Code 各有一个 required 场景，每个版本每个场景运行 3 次，并校验输出确定性。
- JSON 报告保留版本 hashes、通过率、分数、耗时、错误 taxonomy 和 pairwise 差异；required 失败或候选回归均返回非零 exit code。
- 自动化覆盖 fixture-output 隔离、三图 trace 投影、三次确定性漂移、网络错误排除、坏 fixture 和 `graph-v2-regression` 回归门禁。

## 非功能 / 边界

- 错误态：节点失败必须保留最后安全 checkpoint、错误分类和可采取的下一步，不能只留下 Python traceback。
- 并发：同一 run 只允许一个 owner 推进；并行节点可并发，但 state merge 必须确定、冲突可检测。
- 幂等：重复 resume、重复人工响应、应用重启和 WebSocket 重连不能重复提交同一节点结果。
- 性能：关闭 Trace 详情时仍保留最小运行记录；结构化 Trace 对不调用外部模型/工具的本地基准工作流中位耗时开销不超过 10%。
- 隐私：默认全部本地存储，不要求上传 LangSmith 或其他 SaaS；模型输入、工具参数和文件内容遵循现有诊断脱敏与用户数据目录约束。
- 兼容：不改变现有 ToolRegistry handler 契约和前端既有聊天事件语义；旧任务记录可读，无法升级时明确标记 legacy。
- 可维护：Graph 核心不依赖 Tauri/WebSocket；UI 和 SessionDB 通过 adapter 接入；DeepResearch/PPT/Code 仅依赖公共 runtime，不各造一套 checkpoint/trace。

## 完成的定义（DoD 摘要）

- AC-1 至 AC-18 全部有 `AC -> task -> code -> testcase -> result` 可追溯证据并通过。
- Graph/Checkpoint/Trace/Replay/Eval 的单元、故障注入、集成和数据迁移测试全绿。
- DeepResearch、PPT Pro、Complex Code 三条生产路径默认启用 Graph，并完成 windows-mcp 真机闭环。
- 现有 Harness、短聊天和关键工具回归通过，无已知 P0/P1 回归。
- `ARCHITECTURE/`、`plans/`、`testcase/`、`STATUS/status.md` 与用户文档同步。
