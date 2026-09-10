# 多 Agent 编排理论学习资料

这套资料把我们前面讨论过的每个主题整理成一个独立的 Markdown 文档，适合按顺序学习、做笔记和后续对照不同框架。

## 总体结构

```text
目标
 ↓
Planner 规划与拆解
 ↓
Task DAG 表示任务和依赖
 ↓
Allocator 分配预算与角色
 ↓
Scheduler 安排实际执行
 ↓
多个 Agent 搜索与产出候选结果
 ↓
Blackboard 保存共享知识
 ↓
Synthesizer 压缩和合并知识
 ↓
Verifier 验证结果
 ↓
更新 State、Task DAG 和优先级
 ↓
进入下一轮 Control Loop
```

## 文档目录

| 顺序 | 文件 | 核心问题 |
|---:|---|---|
| 1 | [01_agent_orchestration_basics.md](01_agent_orchestration_basics.md) | Agent 编排到底是什么？ |
| 2 | [02_task_tree_dag_proof_dag.md](02_task_tree_dag_proof_dag.md) | 大问题如何拆成动态任务图？ |
| 3 | [03_agent_search.md](03_agent_search.md) | 多个 Agent 怎样形成真正的搜索？ |
| 4 | [04_tree_search_blackboard_memory.md](04_tree_search_blackboard_memory.md) | Tree Search 和 Blackboard 有何区别？ |
| 5 | [05_allocator_scheduler.md](05_allocator_scheduler.md) | 算力投哪里，任务何时运行？ |
| 6 | [06_agent_lifecycle.md](06_agent_lifecycle.md) | 一个 Agent 从创建到终止经历什么？ |
| 7 | [07_control_roles.md](07_control_roles.md) | Planner、Manager、Allocator、Scheduler、Orchestrator 有何区别？ |
| 8 | [08_workflow_control_loop.md](08_workflow_control_loop.md) | 固定工作流与自适应闭环有何区别？ |
| 9 | [09_state_event_durable_execution.md](09_state_event_durable_execution.md) | 系统如何保存进度并在崩溃后继续？ |
| 10 | [10_concurrency_conflict.md](10_concurrency_conflict.md) | 多 Agent 同时工作时如何避免状态混乱？ |
| 11 | [11_budget_cost_backpressure.md](11_budget_cost_backpressure.md) | 如何限制成本、任务爆炸和系统过载？ |
| 12 | [12_observability_tracing_evaluation.md](12_observability_tracing_evaluation.md) | 如何看清系统、复盘路径并科学评估？ |
| 13 | [13_remaining_knowledge_map.md](13_remaining_knowledge_map.md) | 除 Human-in-the-loop 外还要学什么？ |

## 推荐学习方式

1. 先读每章开头的“一句话定义”。
2. 手动画出本章最重要的结构图。
3. 用自己的业务问题替换文中的数学例子。
4. 每读完一章，尝试回答文末的“自测问题”。
5. 学完第 12 章后，再开始比较 LangGraph、AutoGen、Symphony、Prove2Me 等具体框架。

## 学习时最重要的分界

```text
需要语义理解、判断和创造的事情
→ 适合交给模型或 Agent

必须稳定、可复现、可审计的事情
→ 适合交给普通程序和数据库
```

这套资料聚焦通用理论，不依赖某一个具体框架。
