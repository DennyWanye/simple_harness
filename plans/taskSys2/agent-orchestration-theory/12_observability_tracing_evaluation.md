# 12. Observability、Tracing 与 Evaluation

## 一句话定义

```text
Observability：系统现在发生了什么。
Tracing：一个任务从头到尾经历了什么。
Evaluation：这套系统到底好不好。
```

## 1. Observability：可观测性

当有数百个 Agent 并发时，至少要看见：

```text
运行中 Agent 数
等待任务数
失败和超时数量
待总结与待验证队列
token 和费用消耗
最昂贵任务
长期无进展任务
```

它回答：系统当前健康吗？

## 2. Logs：日志

日志记录单个事件：

```text
10:01 Task A created
10:02 Agent 17 claimed Task A
10:07 Agent 17 submitted Result R
10:08 Verifier failed Result R
10:09 Task A entered retry
```

它适合调查具体发生了什么。

## 3. Metrics：指标

指标把大量事件压缩成数字：

```text
任务成功率
平均任务耗时
每个成功任务平均成本
验证通过率
结果重复率
Agent 超时率
队列长度
```

```text
Logs：Agent 17 超时
Metrics：过去一小时超时率 7%
```

## 4. Trace：完整旅程

Trace 把相关 Event 串起来：

```text
Theorem X
 ↓
Planner 创建 A、B、C
 ↓
Agent 17 研究 A
 ↓
提出 Lemma D
 ↓
Agent 28 证明 D
 ↓
Agent 41 找到漏洞
 ↓
Agent 52 修复
 ↓
Synthesizer 合并
 ↓
Verifier PASS
```

Event 是一个点，Trace 是一条路径。

## 5. Lineage：知识血缘

最终结果通常来自多人协作：

```text
Agent A 找方向
Agent B 找反例
Agent C 修复
Agent D 完成正式证明
```

Blackboard 中的知识应记录：

```text
来源任务
作者 Agent
依赖知识
修订者
批评者
Verifier
被哪些任务使用
```

这可以解释突破从哪里来。

## 6. Evaluation 的多个维度

### 结果质量

是否正确、是否通过测试、是否真正解决任务。

### 成本

token、费用、GPU、工具调用和人工时间。

### 效率

重复工作多少、从开始到突破多久、无价值任务占比多少。

### 稳定性

相同任务重复运行时，成功率是否稳定。

### 可扩展性

从 10 个 Agent 增加到 100 个，效果是否真的提高。

## 7. Runtime Metrics 与 Intelligence Metrics

### Runtime Metrics

```text
并发数
队列长度
超时率
工具报错率
平均延迟
资源消耗
```

### Intelligence Metrics

```text
任务成功率
有效新路线数
验证通过率
重复结果率
知识复用率
关键依赖解锁率
```

系统可以非常稳定地浪费资源，所以两类指标都要看。

## 8. Offline Evaluation

使用固定任务集进行可重复测试：

```text
50 道数学题
100 个代码修复任务
30 个研究问题
```

每次修改编排后重新运行，用于版本比较。

## 9. Online Evaluation

观察真实运行中的：

```text
真实用户任务成功率
用户是否接受结果
真实费用
处理时间
人工介入次数
线上错误率
```

## 10. A/B Test

将相似任务分别交给两种策略：

```text
策略 A：Beam Search
策略 B：MCTS 风格分配
```

比较质量、成本、延迟和稳定性。

## 11. Ablation：消融实验

分别去掉某个组件：

```text
完整系统
vs
去掉 Critic
vs
去掉 Blackboard
vs
去掉动态 Allocator
```

观察效果变化，判断每个组件的真实贡献。

## 12. Replay：重放

因为保存了 Event 和 Trace，可以在修改规则后重放历史失败案例：

```text
同一个任务
相同初始条件
新的检索或验证规则
```

用于复现问题和验证修复。

## 13. 一个 Attempt Trace 应记录什么？

```text
task_id
attempt_id
parent_task_id
agent_role
model_version
prompt_version
allocator_version
retrieval_version
读取了哪些知识
完整工具调用
输入输出 token
运行时间
提交结果
Verifier 结果
创建的子任务
最终状态
```

没有版本信息，实验往往无法复现。

## 14. 监控面板的四块

### 系统健康

并发、队列、失败率、超时率。

### 成本

总成本、每任务成本、每角色成本、每模型成本。

### 研究进展

完成节点数、新知识数、已验证知识数、关键路径剩余任务。

### 质量

验证通过率、结果重复率、错误知识率、最终成功率。

## 15. Goodhart’s Law

当指标变成目标后，Agent 可能刷指标而不完成真正目标。

例如：

```text
以“创建任务数量”为目标
→ Agent 疯狂拆任务

以“知识条目数量”为目标
→ Agent 产生大量低质量笔记
```

因此应更关注：

```text
是否验证
是否复用
是否解锁关键任务
是否提高最终成功率
```

## 16. Credit Assignment：贡献归因

不能只把功劳给最终提交者。

```text
Final Result
├── Explorer 提出路线
├── Critic 找到漏洞
├── Connector 建立联系
├── Worker 完成修复
└── Verifier 最终确认
```

可以沿知识依赖图追踪最终成功路径。

## 17. 自测问题

1. Logs、Metrics、Trace 各回答什么问题？
2. Runtime Metrics 为什么不能代表智能质量？
3. Ablation 与 A/B Test 有什么区别？
4. 为什么所有 Prompt、模型和检索策略都必须版本化？

## 本章小结

**没有可观测性，你只是启动了很多 Agent；有了 Trace、Lineage 和 Evaluation，系统才可以被复盘、比较、调试和持续改进。**
