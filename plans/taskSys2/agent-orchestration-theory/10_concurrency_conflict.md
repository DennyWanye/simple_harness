# 10. 并发与冲突控制

## 一句话定义

**允许 Agent 并行思考，但关键状态变化必须受控。**

## 1. Concurrency：并发

多个 Agent 同时处理不同任务，可以提高速度：

```text
       X
     / | \
    A  B  C
```

A、B、C 可以同时运行。

问题在于多个 Agent 可能同时读取和修改同一状态。

## 2. Race Condition：竞态条件

初始状态：

```text
Task D = PENDING
```

Agent 17 和 Agent 18 同时读取到 PENDING，然后都把它改为 RUNNING。

最终结果取决于谁先写入，而不是系统规则，这就是竞态条件。

## 3. Atomic Operation：原子操作

```text
检查任务是否空闲
+
正式领取任务
```

必须成为一次不可分割操作。

结果只能是：

```text
Agent 17：领取成功
Agent 18：领取失败
```

## 4. Compare-and-Swap，CAS

逻辑是：

```text
如果当前值仍然等于我之前看到的值，才允许修改。
```

例如：

```text
如果 Task D 仍是 PENDING
则改为 CLAIMED_BY_17
```

第二个 Agent 发现状态已变化，就必须重试或放弃。

## 5. Task 与 Attempt

有时多个 Agent 同时处理同一个 Task 是有意的：

```text
Task D
├── Attempt 1：方法 A
├── Attempt 2：方法 B
└── Attempt 3：寻找反例
```

规则应是：

> 一个 Task 可以有多个 Attempt；同一个 Attempt 不能被两个 Agent 同时领取。

## 6. 多个候选结果不要互相覆盖

两个 Agent 同时提交：

```text
Proof A
Proof B
```

系统应保存为两个 Candidate：

```text
Task D
├── Candidate A
└── Candidate B
```

然后交给 Verifier、Critic、Judge 或 Synthesizer，而不是后写入者覆盖前者。

## 7. 冲突本身可以成为新任务

```text
Claim A：方法 X 可行
Claim B：方法 X 不可行
```

不要简单投票。创建 Conflict Task：

```text
检查双方证据
寻找反例
运行实验
形式化验证
```

一致不等于正确，证据和外部验证更重要。

## 8. 知识状态

Blackboard 不应只有 True / False：

```text
PROPOSED
UNDER_REVIEW
SUPPORTED
VERIFIED
REJECTED
SUPERSEDED
DISPUTED
```

冲突知识可以暂时保持 DISPUTED，直到被正式解决。

## 9. Lost Update：丢失更新

Agent 1 为 Task A 添加 B、C；Agent 2 同时为 A 添加 D、E。

若两者都覆盖 `children` 字段，后一次写入可能让前一次消失。

## 10. Version 与 Optimistic Concurrency Control

给 Task Graph 版本号：

```text
Graph Version = 42
```

Agent 1 基于 42 修改成功，版本变成 43。

Agent 2 仍基于 42 提交，系统发现基础版本过期，要求重新读取并合并。

这叫乐观并发控制：先并行工作，提交时再检查冲突。

## 11. Lock 与 Lease

### Lock

某个 Agent 修改节点时暂时禁止其他 Agent 修改。

### Lease

锁有有效期，并依赖心跳续租，防止 Agent 崩溃后永久锁住任务。

## 12. Stale State：过期状态

Agent 工作 30 分钟期间，Task DAG 和 Blackboard 已经更新。

处理方法：

- 定期刷新上下文；
- 订阅任务完成和知识更新事件；
- 提交时携带 graph_version、knowledge_version；
- 对基于旧状态的结果重新验证。

## 13. Semantic Duplication：语义重复

```text
“证明 Lemma X”
“验证 X 这个引理”
```

文字不同，可能是同一任务。

可结合：

```text
精确匹配
数学表达式规范化
依赖关系比较
Embedding
LLM 语义判断
```

但不要过度自动合并，因为“证明 X”和“寻找 X 的反例”目标不同。

## 14. Single Writer 与 Proposal / Commit

关键状态不允许 Agent 直接修改。

```text
Agent
  ↓ 提交 Proposal
Task Graph Service / Knowledge Service
  ↓ 验证并 Commit
正式 State
```

核心原则：

```text
Agent produces proposals.
System performs commits.
```

## 15. 三种冲突处理方式

### 只允许一个成功

适合领取 Attempt、扣减预算、修改唯一状态。

### 合并

适合不同子任务、不同资料和互补知识。

### 保留冲突并等待验证

适合相互矛盾的数学结论、实验结果和路线判断。

## 16. 自测问题

1. Atomic Operation 与普通“先读后写”有何不同？
2. 为什么 Task 和 Attempt 必须分开？
3. 为什么 Agent 只能提交 Proposal？
4. 什么时候应该合并，什么时候应该保留冲突？

## 本章小结

**思考和候选结果可以高度并发，但任务领取、预算、知识真值和图结构必须通过原子操作、版本检查、租约及单写入服务来控制。**
