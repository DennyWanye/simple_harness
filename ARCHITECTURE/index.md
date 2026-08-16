# ARCHITECTURE 索引

本目录是 DeskPet **当前生产架构与项目状态的唯一事实源**。实现计划记录“如何做”，本目录记录“现在实际怎么运行、完成到哪里、有哪些边界与风险”。

维护规则：功能通过测试后，同一次交付必须更新对应模块架构；完成度、里程碑、worktree 与项目级已知问题同时汇总到 `PROJECT_STATUS.md`。`STATUS/` 只保留历史链接兼容，禁止继续双写。

| 文档 | 范围 |
|---|---|
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | 全局模块完成度、活跃 worktree、最近里程碑、项目级已知问题与验证入口；新任务接手的第一站 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | DeskPet 全局长任务架构基线：单主 Session Harness、模型驱动 Profile、持久化、DeepResearch/PPT、通用行动/能力包、HITL、恢复、Trace/Eval 与升级边界 |
| [UI.md](UI.md) | 当前暗色优先 UI 主题、共享语义样式、页面覆盖范围、业务边界与真实 Windows 验证状态 |
| [COMPANION_GROWTH.md](COMPANION_GROWTH.md) | Companion 长期成长当前事实：唯一 Store/Router、可信 owner inbox、durable GrowthEvent、同一 RunKernel 的 reflection/candidate/evaluation 生产编排、Manager activation receipt、V2 Reminder、legacy writer 退休边界与真实 provider 阻塞状态 |
| [AGENT_HARNESS.md](AGENT_HARNESS.md) | 当前 Agent Harness 事实源：固定 `agent.general` root、`workflow_spawn` ticket/Driver、TaskGoal/Attempt 失败闭环、running-root FIFO、Manual/Auto 与可执行能力目录 |
| [SDK_EXTRACTION.md](SDK_EXTRACTION.md) | Simple Harness SDK 提取与消费事实源：v0.1.1 本地 immutable candidate、产品旧 Harness 仍持有的生产 authority、T6 Adapter/cutover 缺口、三个官方 Workflow 与双消费者边界 |
| [Harness R7 历史流程图](../plans/2026-07-20-agent-harness-simplification/target-architecture.md) | R7 时点的“一个产品准备入口、一个薄 Kernel、两个 Driver、一套 Effect/UoW 底座”证据；其中主线程/Code 工作台产品边界已被 2026-07-24 单主 Session 多 root 架构取代，当前口径以 `AGENT_HARNESS.md` 为准 |
| [AgentLoop.md](AgentLoop.md) | ReAct 主循环、工具注册/分发、完成守门、ContextManager 与 main 装配 |
| [ARCHITECTURE.md §14](ARCHITECTURE.md#14-native-workflow-engine-replacement-baseline) | LangGraph 调度/身份/生命周期/HITL/评测/打包耦合、原生执行器替换边界、checkpoint 与旧 run 兼容策略 |
| [DeepResearch.md](DeepResearch.md) | DeepResearch 模块专项架构、能力边界、历史缺口与演进记录 |
| [DEEP_RESEARCH_AGENT_REACH.md](DEEP_RESEARCH_AGENT_REACH.md) | 历史方案记录；当前 DeepResearch v7 不依赖 Agent-Reach，不能作为生产链路事实源。 |
| [SEARCH_GATEWAY_DEEPRESEARCH.md](SEARCH_GATEWAY_DEEPRESEARCH.md) | 快速搜索、SearXNG-like Search Gateway、DeepResearch 原生并行、抓取抽取与聊天进度投影的当前代码基线。 |
| [PPT.md](PPT.md) | PPT 生成端到端链路、渲染路径、模板/图片/视觉评审、验证状态与已知短板 |
