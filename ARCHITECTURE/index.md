# ARCHITECTURE 索引

本目录是 DeskPet **当前生产架构与项目状态的唯一事实源**。实现计划记录“如何做”，本目录记录“现在实际怎么运行、完成到哪里、有哪些边界与风险”。

维护规则：功能通过测试后，同一次交付必须更新对应模块架构；完成度、里程碑、worktree 与项目级已知问题同时汇总到 `PROJECT_STATUS.md`。`STATUS/` 只保留历史链接兼容，禁止继续双写。

| 文档 | 范围 |
|---|---|
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | 全局模块完成度、活跃 worktree、最近里程碑、项目级已知问题与验证入口；新任务接手的第一站 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | DeskPet 全局长任务架构基线：Harness、持久化、DeepResearch/PPT/Code、HITL、恢复、Trace/Eval 与升级边界 |
| [AGENT_HARNESS.md](AGENT_HARNESS.md) | Agent harness 请求生命周期、状态/恢复边界、service wiring、subagent sidecar、LangGraph/CrewAI 对比 |
| [AgentLoop.md](AgentLoop.md) | ReAct 主循环、工具注册/分发、完成守门、ContextManager 与 main 装配 |
| [ARCHITECTURE.md §14](ARCHITECTURE.md#14-native-workflow-engine-replacement-baseline) | LangGraph 调度/身份/生命周期/HITL/评测/打包耦合、原生执行器替换边界、checkpoint 与旧 run 兼容策略 |
| [DeepResearch.md](DeepResearch.md) | DeepResearch 模块专项架构、能力边界、历史缺口与演进记录 |
| [DEEP_RESEARCH_AGENT_REACH.md](DEEP_RESEARCH_AGENT_REACH.md) | DeskPet 保留原生 DeepResearch 编排，平台识别、doctor 与读取后端选择交给固定版本的 Agent-Reach。 |
| [SEARCH_GATEWAY_DEEPRESEARCH.md](SEARCH_GATEWAY_DEEPRESEARCH.md) | 快速搜索、SearXNG-like Search Gateway、DeepResearch 原生并行、抓取抽取与聊天进度投影的当前代码基线。 |
| [PPT.md](PPT.md) | PPT 生成端到端链路、渲染路径、模板/图片/视觉评审、验证状态与已知短板 |
