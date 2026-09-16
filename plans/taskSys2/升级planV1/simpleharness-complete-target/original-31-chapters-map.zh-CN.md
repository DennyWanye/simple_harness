# 原始31章覆盖映射

此表表示需求映射，不表示这些需求已通过测试。

| 章 | 原始标题 | 目标需求 | 解释 |
|---|---|---|---|
| 1 | 先理解它到底是什么 | FULL-26, FULL-31, FULL-46, FULL-54, FULL-60 | 产品系统定义，不以角色数量衡量完成 |
| 2 | 设计目标与基本原则 | FULL-01, FULL-02, FULL-07, FULL-16, FULL-36, FULL-46, FULL-48, FULL-60 | 所有原则继续成立 |
| 3 | 完整架构图 | FULL-01, FULL-02, FULL-03, FULL-04, FULL-05, FULL-06, FULL-07, FULL-08, FULL-09, FULL-10, FULL-11, FULL-12, FULL-13, FULL-14, FULL-15, FULL-16, FULL-17, FULL-18, FULL-19, FULL-20, FULL-21, FULL-22, FULL-23, FULL-24, FULL-25, FULL-26, FULL-27, FULL-28, FULL-29, FULL-30, FULL-31, FULL-32, FULL-33, FULL-34, FULL-35, FULL-36, FULL-37, FULL-38, FULL-39, FULL-40, FULL-41, FULL-42, FULL-43, FULL-44, FULL-45, FULL-46, FULL-47, FULL-48, FULL-49, FULL-50, FULL-51, FULL-52, FULL-53, FULL-54, FULL-55, FULL-56, FULL-57, FULL-58, FULL-59, FULL-60 | 四平面保持；HTN新增不是另一任务权威 |
| 4 | 核心运行闭环 | FULL-06, FULL-07, FULL-10, FULL-11, FULL-46 | 保持control loop，不退化为固定workflow |
| 5 | Mission：定义整个任务 | FULL-05, FULL-14, FULL-26, FULL-43 | 原始要求和版本是计划根 |
| 6 | Task DAG：任务与依赖关系 | FULL-01, FULL-02, FULL-05, FULL-07, FULL-08 | 原文DAG＋后来HTN语义分别映射 |
| 7 | Planner、Manager 与 Search Controller | FULL-01, FULL-04, FULL-06, FULL-07, FULL-13, FULL-27 | 模板或bounded graph不视为全部完成 |
| 8 | Frontier、Allocator 与 Scheduler | FULL-06, FULL-11, FULL-12, FULL-14, FULL-15 | 分解frontier与执行frontier区分 |
| 9 | Role 与 Model Router | FULL-11, FULL-13, FULL-27, FULL-30, FULL-52, FULL-53 | 角色必须形成真实搜索差异 |
| 10 | Context Builder 与 Retrieval | FULL-21, FULL-22, FULL-23, FULL-24, FULL-25, FULL-49 | 保留完整工作材料而非只扩大窗口 |
| 11 | Blackboard 与知识压缩 | FULL-16, FULL-17, FULL-18, FULL-19, FULL-20, FULL-55 | 知识条件/有效性/来源/综合闭合 |
| 12 | Agent Runtime 与生命周期 | FULL-21, FULL-26, FULL-28, FULL-29, FULL-30 | 临时物理worker与稳定逻辑BaseAgent兼容 |
| 13 | Agent 输出协议 | FULL-10, FULL-16, FULL-28, FULL-56 | 观察/诊断/建议明确区分 |
| 14 | Verifier 验证体系 | FULL-05, FULL-16, FULL-17, FULL-20, FULL-31, FULL-32, FULL-34, FULL-35, FULL-48 | 确定性检查为工具/硬门，独立语义验收不消失 |
| 15 | Proposal 与 Commit | FULL-07, FULL-09, FULL-14, FULL-46, FULL-47 | 单逻辑writer继续控制新所有模块 |
| 16 | State、Event 与 Durable Execution | FULL-36, FULL-37, FULL-38, FULL-39, FULL-41, FULL-42, FULL-45 | D1事件+State与D2完整业务重建区别明确 |
| 17 | 并发、冲突与幂等 | FULL-07, FULL-08, FULL-09, FULL-14, FULL-15, FULL-28, FULL-29, FULL-38, FULL-39, FULL-43, FULL-47 | 不是全局graph_version一变就重做 |
| 18 | Budget、Cost 与 Backpressure | FULL-11, FULL-12, FULL-14, FULL-15, FULL-50, FULL-55 | 共享目标不能导致双计费 |
| 19 | 停止、停滞、死锁与目标漂移 | FULL-03, FULL-06, FULL-07, FULL-10, FULL-12, FULL-13, FULL-14, FULL-43 | 无解/搜索耗尽/未知/缺权限区分 |
| 20 | Workspace 与 Artifact | FULL-08, FULL-09, FULL-17, FULL-18, FULL-34, FULL-39, FULL-40 | 新产物组合再审阅 |
| 21 | 工具、安全与权限 | FULL-09, FULL-31, FULL-32, FULL-33, FULL-34, FULL-40, FULL-46, FULL-47, FULL-49 | 工具层Workflow，不同领域、平台的明确边界 |
| 22 | Human-in-the-loop | FULL-20, FULL-29, FULL-39, FULL-44, FULL-48 | 用户裁决不改历史事实，审批绑定实际内容 |
| 23 | Observability、Tracing 与 Evaluation | FULL-36, FULL-37, FULL-54, FULL-55, FULL-58, FULL-59 | 质量/可靠性/覆盖独立，不用自报完成评分 |
| 24 | 一次任务从开始到结束 | FULL-01, FULL-05, FULL-07, FULL-11, FULL-18, FULL-23, FULL-44, FULL-46, FULL-48 | 使用目标→方法→执行→证据的闭环 |
| 25 | 状态机设计 | FULL-05, FULL-06, FULL-07, FULL-10, FULL-17, FULL-21, FULL-38, FULL-43 | 旧Task终态不逆转，当前适用性独立 |
| 26 | 核心数据契约 | FULL-01, FULL-03, FULL-05, FULL-10, FULL-14, FULL-17, FULL-28, FULL-56 | 保持旧六合同，新增一等规划合同而不偷塞context |
| 27 | 代码模块划分 | FULL-01, FULL-26, FULL-33, FULL-36, FULL-40, FULL-46, FULL-56 | 保持模块化单体和明确组件边界 |
| 28 | 分阶段落地路线 | FULL-51, FULL-52, FULL-53, FULL-54, FULL-60 | 施工依赖顺序不得变成最终删减依据 |
| 29 | 第一版推荐配置 | FULL-11, FULL-12, FULL-13, FULL-15, FULL-25, FULL-53 | 示例限额/权重非最终最优值，政策化仍有安全上限 |
| 30 | 验收清单 | FULL-14, FULL-16, FULL-17, FULL-20, FULL-36, FULL-37, FULL-39, FULL-45, FULL-46, FULL-48, FULL-49, FULL-54, FULL-55, FULL-58, FULL-59 | 原29条逐项保留并扩展边界 |
| 31 | 最终心智模型 | FULL-01, FULL-02, FULL-11, FULL-17, FULL-23, FULL-46, FULL-60 | Agent探索不确定，软件守住确定性 |
