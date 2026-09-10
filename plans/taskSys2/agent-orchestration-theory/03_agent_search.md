# 03. Agent 搜索：Best-of-N、Beam Search、Tree Search 与 MCTS

## 一句话定义

**多 Agent 的真正价值不是“人多”，而是让不同 Agent 占据搜索空间中的不同位置。**

## 1. Best-of-N

让 N 个 Agent 独立解决同一个问题：

```text
Problem
  ├── Agent 1 → Candidate A
  ├── Agent 2 → Candidate B
  ├── Agent 3 → Candidate C
  └── Agent N → Candidate N
                ↓
              Judge
                ↓
            选择最好候选
```

优点：简单、容易并行。

缺点：

- 每个 Agent 都从零开始；
- 好发现无法被后续 Agent 利用；
- 容易出现大量相似答案；
- Judge 可能把其他答案中的局部价值全部丢掉。

## 2. Beam Search

Beam Search 每轮生成多个候选，只保留最有希望的少数方向继续展开。

```text
第一轮：A  B  C  D  E
         ↓ 评分
保留：  A     C

第二轮：
A → A1 A2 A3
C → C1 C2 C3
         ↓ 评分
保留：A2、C1
```

`beam width` 就是每轮保留的方向数量。

## 3. Beam Search 的风险：过早剪枝

真正答案可能藏在第一轮看起来不好的分支里：

```text
第一轮认为 B 分数低
       ↓
删除 B
       ↓
永远失去真正答案
```

这叫 Premature Pruning。

因此不能只保留当前评分最高的方向。

## 4. Exploration 与 Exploitation

### Exploration：探索

尝试未知、奇怪或很少被研究的方向。

### Exploitation：利用

对已发现的高价值方向投入更多资源，做深做完。

```text
新方向探索
+
好方向深挖
=
健康的搜索策略
```

只做 Exploitation 容易陷入局部最优；只做 Exploration 则永远无法收敛。

## 5. 为什么 Agent 必须有差异？

给 100 个相同模型同一 Prompt，不一定会得到 100 条独立路线。它们可能高度相关。

需要主动制造 Diversity：

```text
Agent 1：直接证明
Agent 2：反证法
Agent 3：寻找反例
Agent 4：先研究特殊情况
Agent 5：做数值实验
Agent 6：搜索相关定理
Agent 7：只负责批评
Agent 8：尝试组合已有发现
```

Role 的本质不是“职位名称”，而是搜索偏置。

## 6. 常见搜索角色

### Explorer

主动寻找新方向，避免重复当前主流方案。

### Exploiter

沿着目前最有希望的方案深入推进。

### Critic

假设现有方案是错的，寻找漏洞和反例。

### Simplifier

将问题变成特殊情形、低维版本或更容易验证的形式。

### Connector

寻找不同分支之间可组合的结果。

## 7. Tree Search

Tree Search 把推理过程本身展开成树：

```text
Problem
├── Strategy A
│   ├── Step A1
│   │   ├── A1a
│   │   └── A1b
│   └── Step A2
├── Strategy B
│   ├── B1
│   └── B2
└── Strategy C
```

和一次性生成完整答案相比，它允许：

- 在中间步骤进行评估；
- 发现死路后回退；
- 对好节点增加资源；
- 保留多个候选分支；
- 让不同 Agent 处理不同节点。

## 8. MCTS 的直觉

MCTS，Monte Carlo Tree Search，核心是同时考虑：

```text
这个方向目前表现有多好？
+
这个方向是不是还没有被充分探索？
```

例如：

```text
A：试过 100 次，平均表现 8
B：试过 100 次，平均表现 3
C：只试过 2 次，平均表现 7
```

A 值得继续，C 也值得继续，因为 C 的不确定性很高。

## 9. MCTS 的四个直观阶段

### Selection

根据当前价值和探索不足，选择一个节点。

### Expansion

为该节点创建新的策略或子任务。

### Evaluation

用 Agent、模型、测试或 Verifier 评估新节点。

### Update

将结果反馈给上层节点，影响下一轮选择。

不必一开始实现完整数学公式；先掌握这一控制思想即可。

## 10. Critique Loop

```text
Generator 生成方案
      ↓
Critic 寻找漏洞
      ↓
Revision 修复
      ↓
再次验证
```

相比让同一个 Agent 说“请认真自查”，独立 Critic 更容易跳出原方案的确认偏误。

## 11. Judge 与 Synthesizer 的差别

```text
Judge：谁最好？

Synthesizer：每个候选中有什么值得留下，并能否组合？
```

Agent 搜索系统通常既需要排序，也需要知识合并。

## 12. 搜索策略的层级

```text
任务级：选择哪个 Task
分支级：选择哪条 Approach
Agent 级：为同一任务派哪些角色
步骤级：当前 Agent 下一步调用什么工具
```

不同层级可以使用不同搜索方法。

## 13. 自测问题

1. Best-of-N 为什么不是完整的协作系统？
2. Beam Search 为什么可能错过突破？
3. Role 如何制造搜索多样性？
4. MCTS 为什么同时关心高分和低访问次数？

## 本章小结

**Agent 搜索的关键是保留多样性，在探索未知方向和深挖高价值方向之间动态平衡，而不是简单复制相同 Agent。**
