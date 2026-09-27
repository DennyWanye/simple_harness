# 第二批 2A 真机小验收（计划 §6.5）

- 环境：隔离用户目录 `.local-test-evidence/2026-09-25/opt/ui-full/`，SDK opt.41，保障层开（默认），DeepSeek 转发。
- 任务：`mission-8cb840d96b04beba`（界面新建：写 ENUMERATE.md，三句话介绍 enumerate + 可运行代码例子），**MissionCompleted**，保障层 MISSION_FINAL 审查通过并收尾。
- 链路：创建事务写入执行图要求（`taskgraph_requirements`，来源 `deployment_default`）→ 自动规划授权 → 系统启用执行图（`TaskGraphContractEnabled`，actor=system）→ 规划提交携带执行图修订（`PlanRevisionCommitted` + `TaskGraphRevisionRecorded`）→ 执行 → 根结论 → 完成。
- 四个严格只读入口（应用以开发模式启动一次，走真实控制通道，只读；读完以正常模式重启）：`taskgraph.snapshot` ✔（2 节点 1 边，CURRENT）、`taskgraph.why_not_ready` ✔（根节点 NEEDS_REFINEMENT/form_compound）、`taskgraph.diff` 1→1 ✔（0→1 返回 GRAPH_INTEGRITY：修订 0 不存在，探针参数问题）、`taskgraph.convergence` ✔（complete）。原始回复见 `taskgraph-reads.json`。
- 任务详情 `taskgraph = {required: true, waiting: false, fault: null}`；本批之前创建的任务读执行图仍为 `NOT_ENABLED`（不转换旧任务）。
- 顺带发现（不阻断，记第 2B 批）：根结论已提交、保障层收尾尚在进行时出现一次 `HierarchicalMissionStalled`（`hierarchical_no_dispatchable_work`），随后正常完成——误报卡住。
- 第一轮（opt.40）`mission-9d7dba608eb2aaf2` 暴露"规划回复必过期"缺陷，已在 opt.41 修复，见 PLAN-STATUS 2A.3a。
