# 05. Allocator 与 Scheduler

## 一句话定义

```text
Allocator 决定“给谁多少资源”。
Scheduler 决定“谁在什么时候、在哪里运行”。
```

## 1. 为什么不能平均分配 Agent？

假设有 100 个 Agent 和六个任务：

```text
A1：接近突破
A2：重要但困难
B1：已经证明是死路
B2：一般
C1：几乎没探索过
C2：价值较低
```

平均分配会把资源浪费在 B1 和 C2 上。

Allocator 的职责是把更多资源放到值得的地方。

## 2. 判断一个任务是否值得的四个维度

### Importance：重要性

任务完成后，对根目标能产生多大影响？

### Promise：有希望程度

目前是否已经出现可验证进展？

### Uncertainty：不确定性

这个方向是否尚未被充分探索？

### Cost：成本

需要多少 token、时间、GPU、工具调用和验证资源？

## 3. 概念性的优先级公式

```text
Priority
≈ 重要性
+ 有希望程度
+ 未探索价值
- 执行成本
- 重复风险
```

它不是必须使用固定数学公式；重点是显式考虑这些因素，而不是凭感觉平均分配。

## 4. Allocator 不只分配 Agent 数量

更完整的资源方案包括：

```text
task
agent_role
agent_count
model
context_budget
token_budget
time_limit
tool_limit
verification_budget
```

例如：

```yaml
task: lemma_B
allocation:
  explorers: 4
  exploiters: 12
  critics: 3
  verifiers: 2
  max_tokens: 5000000
```

## 5. Scheduler 的工作

Allocator 已经决定：

```text
Task A → 20 个 Agent
Task B → 10 个 Agent
```

Scheduler 再处理：

- 哪一批先启动；
- 哪台机器执行；
- 哪个任务进入等待队列；
- 超时后是否重试；
- Agent 失联后由谁接管；
- 依赖满足后何时解锁。

## 6. FIFO 与 Priority Queue

### FIFO

先来的任务先执行。适合简单、同质任务。

### Priority Queue

优先运行高价值任务：

```text
Priority 10 → Task D
Priority 8  → Task A
Priority 4  → Task F
```

研究型 Agent 通常需要动态优先级队列。

## 7. Dynamic Scheduling

任务优先级会随新知识变化。

例如 Blackboard 新增：

```text
Lemma X 已经通过验证
```

而 A2、C7、F3 都依赖 X：

```text
A2、C7、F3 被解锁
优先级立即上升
```

Verifier 失败也可能触发：

```text
降低原路线优先级
创建修复任务
增加 Critic
```

## 8. Allocator 与 Manager 的区别

```text
Manager：
从自己负责的任务出发，提出“这里需要更多资源”。

Allocator：
从全局预算出发，决定实际能分配多少。
```

## 9. Diversity-aware Allocation

多个 Agent 研究同一任务时，不应全部使用同一策略。

```text
Task D
├── Explorer × 4
├── Exploiter × 8
├── Critic × 3
└── Simplifier × 2
```

对于已尝试很多次的低价值路线，可以保留少量“反常规”Agent，而不是彻底删除，以避免过早剪枝。

## 10. Starvation：任务饥饿

如果系统永远只运行高优先级任务，低优先级任务可能永远得不到资源。

常见处理方法：

- 保留固定探索预算；
- 等待时间越长，优先级逐渐提高；
- 为不同任务类别设置最低资源份额；
- 定期检查长期未运行的节点。

## 11. 一个完整例子

```text
D：重要性高，有明显进展，探索仍不充分
E：尝试 50 次，大量重复失败
C：几乎未探索，但可能带来全新路线
```

可能分配为：

```text
D → 50% 资源
C → 30% 资源
E → 5% 资源
其他任务 → 15% 资源
```

其中 E 的少量资源可以交给与之前不同的 Critic 或 Explorer。

## 12. 自测问题

1. Allocator 与 Scheduler 的职责边界是什么？
2. 为什么“当前分数最高”不等于“应该拿走全部资源”？
3. Blackboard 和 Verifier 如何影响调度？
4. 为什么分配策略还要考虑 Agent Role？

## 本章小结

**Allocator 决定算力投向，Scheduler 负责可靠执行；高级编排的关键是动态、全局并兼顾探索多样性的资源调度。**
