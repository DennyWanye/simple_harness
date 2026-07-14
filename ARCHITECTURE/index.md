# ARCHITECTURE 索引

本目录记录 DeskPet 关键模块的架构基线，用于给实现计划和回归测试提供当前代码事实。

| 文档 | 范围 |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | DeskPet 全局长任务架构基线：Harness、持久化、DeepResearch/PPT/Code、HITL、恢复、Trace/Eval 与升级边界 |
| [AGENT_HARNESS.md](AGENT_HARNESS.md) | Agent harness 请求生命周期、状态/恢复边界、service wiring、subagent sidecar、LangGraph/CrewAI 对比 |
| [ARCHITECTURE.md §14](ARCHITECTURE.md#14-native-workflow-engine-replacement-baseline) | LangGraph 调度/身份/生命周期/HITL/评测/打包耦合、原生执行器替换边界、checkpoint 与旧 run 兼容策略 |
| [DEEP_RESEARCH_AGENT_REACH.md](DEEP_RESEARCH_AGENT_REACH.md) | DeskPet 保留原生 DeepResearch 编排，平台识别、doctor 与读取后端选择交给固定版本的 Agent-Reach。 |
| [SEARCH_GATEWAY_DEEPRESEARCH.md](SEARCH_GATEWAY_DEEPRESEARCH.md) | 快速搜索、SearXNG-like Search Gateway、DeepResearch 原生并行、抓取抽取与聊天进度投影的当前代码基线。 |
