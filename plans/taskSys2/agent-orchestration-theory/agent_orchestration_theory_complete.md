# 多 Agent 编排理论：完整合订版

> 本文档由各独立章节合并而成。建议使用目录文件按章节学习。

# 01. Agent 编排基础

## 一句话定义

**Agent 编排，就是决定谁做什么、谁能看到哪些结果、什么时候换方向，以及最终相信哪个答案。**

## 1. 大模型与 Agent 的区别

```text
大模型
= 一个能够理解和生成内容的大脑

Agent
= 大模型 + 任务 + 工具 + 上下文 + 持续工作循环
```

普通大模型通常完成一次问答；Agent 则可以连续执行：

```text
读取任务
 ↓
思考
 ↓
调用工具
 ↓
观察结果
 ↓
继续思考或修改方案
 ↓
提交结果
```

## 2. 为什么需要多个 Agent？

困难问题往往没有一条显而易见的路线。多个 Agent 的基本价值是并行探索：

```text
                 难题
          ┌──────┼──────┐
          ▼      ▼      ▼
       Agent A Agent B Agent C
          │      │      │
       路线 A  路线 B  路线 C
```

如果一个方向走错，其他方向仍可能成功。

## 3. 为什么“多开几个 Agent”还不够？

没有编排时，容易出现：

- 多个 Agent 重复做同一件事；
- 一个 Agent 已经证明某路线失败，其他 Agent 仍继续浪费资源；
- 重要发现无人接手；
- 产生很多候选结果，却不知道哪个可信；
- 上下文和通信量快速爆炸。

因此，真正困难的不是创建 Agent，而是组织 Agent。

## 4. 最基础的组织结构

```text
             Planner
            拆解任务
               │
       ┌───────┼───────┐
       ▼       ▼       ▼
    Worker A Worker B Worker C
       │       │       │
       └───────┼───────┘
               ▼
            Reviewer
             审核
```

这类结构适合已知目标和相对明确的任务。

## 5. 开放问题为什么更像“搜索”？

对于科研、复杂代码修复和开放式问题，一开始并不知道正确步骤：

```text
固定流程：A → B → C

开放搜索：
          ?
       /  |  \
      ?   ?   ?
     /         \
    ?           ?
```

此时 Agent 编排不再只是“公司分工”，而开始接近搜索算法：

```text
探索多个方向
 ↓
比较结果
 ↓
淘汰低价值方向
 ↓
给有希望的方向更多算力
 ↓
继续探索
```

## 6. 七个核心角色

### Planner

把大目标拆成任务或研究方向。

### Worker

实际执行任务，调用工具并产生候选结果。

### Blackboard / Memory

保存团队可复用的发现、失败、证据和已验证知识。

### Synthesizer

从大量结果中提取关键知识，去重、压缩并组合方案。

### Critic

专门寻找漏洞、反例、隐含假设和逻辑跳跃。

### Allocator

决定哪些方向值得投入更多 Agent、预算和时间。

### Verifier

使用测试、编译器、Lean、数据库规则或实验，客观判断结果是否成立。

## 7. 最基本的闭环

```text
             Problem
                │
                ▼
             Planner
                │
       ┌────────┼────────┐
       ▼        ▼        ▼
     Agent    Agent     Agent
       │        │        │
       └────────┼────────┘
                ▼
             Memory
                │
                ▼
          Synthesizer
                │
          哪条路更有希望？
                │
       ┌────────┴────────┐
       ▼                 ▼
     剪枝             增加资源
                           │
                           ▼
                        Result
                           │
                           ▼
                        Verifier
                       /        \
                    FAIL        PASS
                      │           │
                      └─继续搜索  └─完成
```

## 8. 最重要的工程原则

```text
模型负责需要理解和创造的判断。
程序负责状态、预算、权限和确定性规则。
```

不要让模型直接决定“自己已经正确”。Agent 提交候选结果，系统负责验证和正式提交。

## 9. 自测问题

1. 多 Agent 的价值为什么不等于 Agent 数量？
2. Reviewer 和 Verifier 的区别是什么？
3. 为什么开放式科研问题更像搜索，而不是固定 Workflow？
4. Blackboard 解决的是哪个问题？

## 本章小结

**Agent 编排的核心不是让多个模型聊天，而是把并行探索、知识积累、资源分配和外部验证组织成一个闭环。**

---

# 02. Task Tree、Task DAG 与 Proof DAG

## 一句话定义

**Task DAG 是大问题的依赖地图；它描述有哪些子问题、谁依赖谁，以及哪些任务可以并行。**

## 1. Task Tree：最简单的任务拆解

例如研究一家公司：

```text
研究公司
├── 财务
│   ├── 收入
│   ├── 利润
│   └── 现金流
├── 产品
│   ├── 产品 A
│   └── 产品 B
└── 竞争
    ├── 对手 A
    └── 对手 B
```

最上层是 Root Task，下面是 Subtasks。

任务树的主要价值是：

- 把一个大问题拆成小问题；
- 让多个 Agent 并行工作；
- 明确父任务和子任务之间的关系。

## 2. 为什么 Tree 不够？

多个任务可能依赖同一个结果。

例如：

```text
产品分析 ─┐
          ├── 依赖“公司总收入”
服务分析 ─┘
```

如果使用纯树结构，“公司总收入”可能被重复计算两次。

更合理的是让一个节点被多个任务共享：

```text
             公司总收入
             ↙        ↘
         产品分析    服务分析
```

这就形成了 DAG。

## 3. DAG 是什么？

DAG 是 Directed Acyclic Graph，有向无环图。

通俗理解：

> 一张描述“谁依赖谁”的任务关系图，并且依赖关系不能形成无限循环。

例如做饭：

```text
        买菜
       /    \
    洗肉    洗菜
      │       │
    切肉    切菜
       \      /
        \    /
          炒菜
```

切肉和切菜可以并行；炒菜必须等待两者完成。

## 4. DAG 为什么适合 Agent 系统？

DAG 可以直接决定：

- 哪些任务现在可以运行；
- 哪些任务仍被依赖阻塞；
- 哪些结果可被多个分支复用；
- 哪些任务完成后会解锁后续任务。

```text
A ─┐
   ├──→ C
B ─┘
```

A、B 可以同时运行，C 必须等待 A、B 完成。

## 5. 固定 DAG 与动态 Task Graph

普通业务流程的 DAG 通常由开发者提前画好：

```text
读取邮件 → 分类 → 查询库存 → 生成回复
```

科研型 Agent 不知道完整解法，图会在运行中不断生长：

```text
开始：
Problem

第一轮：
Problem
├── A
├── B
└── C

第二轮：
Problem
├── A
│   ├── A1
│   └── A2
├── B
│   └── B1
└── C
```

这叫 Dynamic Task Graph。

## 6. Proof Tree 与 Proof DAG

在数学证明中：

```text
要证明 Theorem X
       ↓
需要 Lemma A 和 Lemma B
```

```text
        Theorem X
         /      \
    Lemma A    Lemma B
```

如果 A 和 B 又共同依赖 Lemma C：

```text
        Theorem X
         /      \
    Lemma A    Lemma B
         \      /
          Lemma C
```

这就是 Proof DAG。

## 7. “先欠一个子定理”

Agent 可以先完成一个条件式方案：

> 如果 Lemma A 成立，那么 Theorem X 剩余部分可以完成。

系统先记录：

```text
Theorem X
└── depends on Lemma A
```

然后创建新任务“证明 Lemma A”。

这种设计的价值是：

- 一个 Agent 不需要独自钻到底；
- 子问题可以并行处理；
- 上下文不容易爆炸；
- 证明依赖被明确记录。

## 8. Frontier：当前可攻击的前沿节点

假设：

```text
             X
           /   \
          A     B
        / | \    \
       C  D  E    F
       ✓  ✓  ?    ?
```

当前最适合继续处理的节点可能是 E 和 F。这些节点构成 Frontier。

Frontier 通常满足：

- 尚未完成；
- 依赖已经满足或足够明确；
- 对主目标仍有价值；
- 当前可以被分配给 Agent。

## 9. Task DAG 与搜索的关系

```text
Task DAG
= 搜索空间的地图

Agent
= 在地图上探索的人

Allocator
= 决定派多少人去哪里

Blackboard
= 沿途积累的共享知识

Verifier
= 确认某条路径真的走通
```

## 10. 常见风险

### 过度拆分

任务太细，管理成本大于实际工作成本。

### 完成条件不清

“研究一下方法 A”无法判断是否完成。

### 循环依赖

```text
A 等待 B
B 等待 C
C 又等待 A
```

### 重复节点

不同 Agent 创建了语义相同的任务。

### 失去根目标

子任务本身有趣，但与主目标没有关系。

## 11. 一个简化的数据结构

```yaml
task_id: task_A17
title: prove_lemma_A
parent_tasks:
  - theorem_X
depends_on:
  - lemma_C
status: pending
priority: 0.82
success_criteria:
  - lean_pass
budget:
  max_attempts: 8
  max_tokens: 500000
```

## 12. 自测问题

1. Tree 和 DAG 的关键区别是什么？
2. 为什么 DAG 能提高并行度？
3. Dynamic Task Graph 与固定 Workflow 有何不同？
4. Frontier 为什么不是“所有未完成任务”？

## 本章小结

**Task DAG 把开放问题变成可分配、可并行、可追踪的任务网络；Proof DAG 则把一个大证明变成一组可共享、可验证的依赖节点。**

---

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

---

# 04. Tree Search、Blackboard 与知识压缩

## 一句话定义

```text
Tree Search 管“接下来往哪里找”。
Blackboard 管“团队已经知道什么”。
```

## 1. 最直观的区别

### Tree Search

像迷宫路线图：

```text
                 入口
              /    |    \
            左     中     右
            │      │      │
           左A    中A     右A
```

它记录：

- 哪些分支已探索；
- 哪些节点值得继续；
- 哪些分支应被剪枝；
- 下一个 Agent 应去哪。

### Blackboard

像团队共享笔记：

```text
1. 左侧第三个入口是死路
2. 找到一把蓝钥匙
3. 蓝钥匙可能打开右侧房间
4. 中路地图比例有误
5. 不要重复路线 X
```

它保存可复用的知识，不一定是树结构。

## 2. 为什么不能只把知识放在树节点里？

假设 Agent 在分支 A1 发现了 Lemma X，而 Lemma X 同时可以帮助 C1：

```text
A1 ──发现 Lemma X──┐
                    ├── 可复用于 C1
C1 ─────────────────┘
```

如果知识只属于 A1，C 分支可能看不到。

Blackboard 的重要价值是跨分支共享知识。

## 3. 对比表

| 问题 | Tree Search | Blackboard |
|---|---|---|
| 核心问题 | 接下来去哪搜索？ | 已经知道什么？ |
| 典型结构 | 树或图 | 数据库、知识库、索引 |
| 主要内容 | 节点、分支、分数、访问次数 | 结论、证据、失败、资料、摘要 |
| 主要动作 | select、expand、prune | read、write、retrieve、update |
| 主要用户 | Scheduler、Allocator、Agent | Agent、Synthesizer、Verifier |

## 4. Blackboard 的真正难点不是存储，而是检索

假设有 10,000 个 Agent，每个写 100 条记录：

```text
1,000,000 条信息
```

新 Agent 不可能全部读取。

系统需要根据当前任务挑选：

```text
直接依赖
相关已验证知识
相似失败案例
当前分支最新发现
跨分支可复用结果
Verifier 最近反馈
```

这叫 Retrieval。

## 5. 记忆系统的四层

### Raw Memory

完整原始日志，用于追溯，不直接全部喂给 Agent。

### Working Memory

当前 Agent 的短期上下文：

```text
当前任务
父目标
最近发现
相关知识
最近验证反馈
```

### Shared Knowledge

结构化的公共知识，是真正的 Blackboard 核心。

### Summary Memory

对一个研究组或一个阶段的大量消息进行压缩后的摘要。

## 6. Knowledge Compression

大量 Agent 产生的消息必须不断压缩：

```text
探索
 ↓
产生大量原始信息
 ↓
去重、筛选、验证、总结
 ↓
形成少量关键知识
 ↓
下一轮 Agent 使用这些知识继续探索
```

算力可以增加，但上下文窗口不能无限增加，因此知识压缩是大规模 Agent 系统的核心能力。

## 7. Judge 与 Synthesizer

### Judge

选择一个最好的候选。

### Synthesizer

从多个候选中抽取局部价值，并形成新的综合方案。

例子：

```text
Agent A：主答案错误，但发现一个重要 Lemma
Agent B：路线正确，但证明不完整
Agent C：找到了 A 的漏洞
Agent D：找到一篇关键论文
```

Judge 可能只选 B。

Synthesizer 会尝试：

```text
B 的主路线
+ A 的 Lemma
+ C 的漏洞修复
+ D 的资料
```

## 8. Memory Pollution：记忆污染

一个错误 Claim 写进 Blackboard 后，可能误导大量 Agent：

```text
错误知识
  ↓
进入共享记忆
  ↓
被数百个 Agent 当作事实
  ↓
大量后续工作全部偏离
```

因此知识不能只有文字，还必须有状态和来源。

## 9. 可信等级

```text
IDEA
仅是想法

PROPOSED
正式提出，但未验证

REVIEWED
被其他 Agent 检查

SUPPORTED
有实验或证据支持

VERIFIED
通过机器验证或可靠规则

REJECTED
已证明错误

DISPUTED
存在冲突证据
```

Agent 使用知识时，必须知道它的可信等级。

## 10. Provenance：知识来源

一个知识条目至少应记录：

```yaml
knowledge_id: K10291
type: lemma
content: "Lemma X ..."
source_task: task_A17
author_agent: agent_927
status: verified
verifier: lean
related_tasks:
  - task_C28
  - task_F11
depends_on:
  - K10002
created_at: 2026-01-01T10:00:00Z
```

这使系统可以回答：

- 谁提出的？
- 基于什么？
- 谁验证的？
- 被哪些任务使用？
- 是否已被新版本替代？

## 11. Tree Search 与 Blackboard 如何协作？

```text
Search Graph
告诉系统有哪些路线
       │
       ▼
Agent 进行探索
       │
       ▼
Blackboard
保存沿途学到的知识
       │
       ▼
Allocator / Scheduler
根据新知识改变后续搜索
```

## 12. 自测问题

1. 为什么 Tree Search 不能取代 Blackboard？
2. 为什么 Blackboard 不能把所有信息都直接给 Agent？
3. Judge 和 Synthesizer 的目标有什么不同？
4. Provenance 为什么能减少记忆污染？

## 本章小结

**Search Graph 是路线地图，Blackboard 是共享知识系统；大规模编排的关键不是保存全部消息，而是把可靠、相关、可复用的知识送给正确的 Agent。**

---

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

---

# 06. Agent 生命周期

## 一句话定义

**Agent 生命周期描述一个 Agent 从被创建、拿到任务、执行、提交，到重试、分裂、取消或完成的全过程。**

## 1. Agent 的“出生”

Scheduler 发现某个任务需要执行：

```text
Task D
  ↓
创建 Agent Instance
```

Agent 不是凭空工作，它需要一个明确的工作包。

## 2. Context Assembly：上下文组装

工作包通常包括：

```text
你是谁：Role
你要做什么：Task
为什么做：Parent Goal
团队已经知道什么：Relevant Knowledge
你能使用什么：Tools and Permissions
你能花多少：Budget
你要提交什么：Output Contract
```

示例：

```yaml
role: critic
task: inspect_lemma_D_steps_4_to_8
parent_goal: prove_theorem_X
knowledge:
  - lemma_A_verified
  - lemma_B_unverified
tools:
  - lean
  - python
budget:
  max_tokens: 50000
output:
  - failure_location
  - evidence
  - suggested_fix
```

## 3. Running Loop

Agent 进入 RUNNING 后，可能持续循环：

```text
思考
 ↓
调用工具
 ↓
观察结果
 ↓
更新局部计划
 ↓
继续工作
```

一个 Agent Instance 往往包含多次模型调用，而不是一次问答。

## 4. Agent 的多种产出

### 成功结果

候选方案通过 Verifier，任务完成。

### Candidate

Agent 认为结果成立，但尚未验证。

### Negative Result

没有解决任务，却明确证明某条路线失败，并给出原因。

### New Subtasks

Agent 发现原任务应进一步拆解。

### Partial Progress

完成了一部分，剩余问题清晰可交接。

### Timeout / Failure

预算耗尽、工具失败、上下文不足或长时间无进展。

## 5. Retry 不应只是重复同一 Prompt

聪明的重试会改变：

```text
策略
Agent Role
模型
上下文
工具
预算
子任务粒度
```

例如：

```text
第一次：直接证明
第二次：反证法
第三次：先证明特殊情况
第四次：派 Critic 检查任务假设
```

## 6. Spawn / Split：分裂任务

Agent 发现 X 依赖 A、B、C：

```text
          X
       /  |  \
      A   B   C
```

它可以提交“创建子任务”的 Proposal，由系统正式写入 Task DAG，然后派新 Agent 处理。

## 7. Cancellation：取消

Agent 应在以下情况被终止：

- 任务已被其他 Agent 解决；
- 当前方向已被证伪；
- 长时间没有新进展；
- 结果持续重复；
- 预算耗尽；
- 上游目标已经改变；
- Agent 权限或运行环境异常。

## 8. Role 与 Agent Instance

```text
Role
= 一种长期定义的工作方式

Agent Instance
= 某次具体运行
```

例如：

```text
Role: Critic

Agent #1827
  role: Critic
  task: inspect_lemma_D
  model: model_X
  budget: 50k tokens
```

一个 Role 可以创建很多 Agent Instance。

## 9. Task 与 Attempt

一个 Task 可以有多个 Attempt：

```text
Task D
├── Attempt 1：Explorer
├── Attempt 2：Exploiter
├── Attempt 3：Critic
└── Attempt 4：不同模型
```

Task 是目标，Attempt 是一次具体尝试。

## 10. 简化状态机

```text
CREATED
   ↓
READY
   ↓
RUNNING
   ↓
SUBMITTED
   ↓
VERIFYING
  /        \
FAIL       PASS
 │          │
 ▼          ▼
RETRY    COMPLETED
 │
 ├── 换策略
 ├── 换模型
 ├── 加上下文
 ├── 拆任务
 └── TERMINATED
```

还可能有：

```text
BLOCKED
TIMED_OUT
CANCELLED
LOST
```

## 11. 框架真正需要管理什么？

不仅是调用模型，还包括：

```text
任务状态
Agent 状态
上下文
工具权限
预算
超时
重试
取消
结果版本
验证
持久化
```

## 12. 自测问题

1. 为什么 Agent 的输出不一定是最终答案？
2. Retry 为什么应该根据失败原因改变策略？
3. Role 和 Agent Instance 有何区别？
4. Task 和 Attempt 为什么要分开？

## 本章小结

**Agent 是一次受预算、上下文、权限和状态机约束的执行实例；成熟编排系统必须明确管理它的创建、工作、交接、验证和终止。**

---

# 07. Planner、Manager、Allocator、Scheduler 与 Orchestrator

## 一句话定义

```text
Planner 决定做什么。
Manager 决定如何持续推进。
Allocator 决定给多少资源。
Scheduler 决定何时、在哪里运行。
Orchestrator 保证整个系统协调运转。
```

## 1. Planner：画地图

Planner 把目标拆成任务和依赖：

```text
Theorem X
├── Lemma A
├── Lemma B
└── Special Case C
```

核心职责：

- 任务拆解；
- 路线设计；
- 依赖关系；
- 成功条件；
- 新方向生成。

Planner 通常不亲自完成全部任务。

## 2. Manager：带队走地图

Manager 负责某个任务、分支或研究组的持续推进。

它观察：

```text
哪些 Agent 成功？
哪些结果重复？
哪里卡住？
是否需要拆任务？
是否应该换策略？
```

然后提出：

```text
停止重复路线
深挖新发现
增加 Critic
申请更多预算
```

## 3. Allocator：分配蛋糕

Allocator 从全局视角处理：

```text
哪个任务得到多少 Agent？
使用哪个模型？
给多少 token 和时间？
各类角色比例是多少？
```

Manager 可以提出资源需求，Allocator 决定是否批准。

## 4. Scheduler：安排执行

Scheduler 处理执行层问题：

- 哪个任务现在启动；
- 哪台机器运行；
- 谁进入等待队列；
- 依赖满足后何时解锁；
- 超时后如何重试；
- Agent 失联后如何重新分配。

```text
Allocator：A 应该得到 20 个 Agent
Scheduler：先启动 8 个，资源释放后再启动 12 个
```

## 5. Orchestrator：系统的总协调器

Orchestrator 连接所有组件并处理事件：

```text
Agent 提交结果
       ↓
Orchestrator 收到事件
       ↓
交给 Verifier
       ↓
更新 State 和 Blackboard
       ↓
检查 Task DAG 依赖
       ↓
通知 Scheduler 启动新任务
```

它像操作系统，不一定亲自做每个智能判断。

## 6. 公司类比

| 组件 | 公司类比 | 主要问题 |
|---|---|---|
| Planner | 战略规划 | 应该做哪些事情？ |
| Manager | 项目经理 / 研究组长 | 目前如何推进？ |
| Allocator | 财务与资源负责人 | 各方向给多少人和钱？ |
| Scheduler | 排班与调度系统 | 谁现在执行，在哪里执行？ |
| Orchestrator | 公司运行机制 / 操作系统 | 事件和状态如何驱动整个流程？ |

## 7. 完整关系

```text
                      Goal
                       │
                       ▼
                    Planner
                       │
                       ▼
                   Task DAG
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
          Manager             Allocator
             │                   │
             └─────────┬─────────┘
                       ▼
                   Scheduler
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
        Agent         Agent        Agent
          │            │            │
          └────────────┼────────────┘
                       ▼
            Blackboard / Verifier
                       │
                       ▼
                  Orchestrator
              更新状态并触发下一轮
```

严格来说，Orchestrator 包住整个系统。

## 8. 为什么不能有一个“超级 Manager”？

让一个模型负责所有事情，会带来：

- 上下文爆炸；
- 决策不一致；
- 单点故障；
- 无法处理大量并发；
- 状态容易遗忘；
- 模型幻觉直接影响系统控制。

大规模系统通常采用分层管理：

```text
                 Global Manager
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
    Group Manager A Group Manager B Group Manager C
        │              │              │
      Agents          Agents         Agents
```

底层汇报细节，上层只读取压缩摘要。

## 9. 模型和程序的职责边界

### 适合模型

```text
任务怎么拆？
哪条思路有希望？
失败原因可能是什么？
两个发现能否组合？
```

### 适合程序

```text
预算是否用完？
依赖是否满足？
任务是否超时？
Verifier 是否 PASS？
状态是否已写入数据库？
```

判断标准：

> 同样输入是否应该永远得到同样结果？

应该稳定一致的，尽量交给程序。

## 10. 自测问题

1. Planner 和 Manager 最大区别是什么？
2. Manager 为什么不能直接决定全局预算？
3. Scheduler 为什么不应该判断数学路线好不好？
4. Orchestrator 为什么更像操作系统而不是最聪明的 Agent？

## 本章小结

**这些角色不是不同名称的“老板”，而是规划、管理、资源、执行和系统控制五个不同层次；职责分离才能让大规模 Agent 系统稳定扩展。**

---

# 08. Workflow 与 Control Loop

## 一句话定义

```text
Workflow：按预先画好的路线执行。
Control Loop：一边观察，一边决定下一步。
```

## 1. Workflow

适合步骤相对稳定的业务：

```text
收到订单
 ↓
检查付款
 ↓
检查库存
 ↓
创建物流单
 ↓
发送通知
```

开发者提前定义：

```text
A 完成后做 B
B 完成后做 C
```

它像照菜谱做菜。

## 2. Control Loop

Control Loop 只固定一个循环：

```text
Observe
   ↓
Decide
   ↓
Act
   ↓
Evaluate
   └────→ Observe
```

它像开车：根据当前路况不断调整，而不是预先写死每一秒的动作。

## 3. Open Loop 与 Closed Loop

### Open Loop：开环

发出计划后，不根据结果改变：

```text
计划 → A → B → C → 结束
```

### Closed Loop：闭环

执行结果会反馈给系统：

```text
行动
 ↓
结果
 ↓
反馈
 ↓
改变下一步行动
```

研究型 Agent 必须以闭环为主。

## 4. 为什么科研更像 Control Loop？

一开始只有：

```text
Theorem X
```

运行后才发现：

```text
方法 A 卡在 Lemma B
方法 C 出现反例
方法 D 值得追加资源
```

于是 Task DAG、优先级和上下文都发生变化。

路线不是提前写好的，而是在运行中逐渐长出来。

## 5. Control Loop 的核心是 State

每轮决策前，系统必须知道：

```text
哪些任务完成？
哪些 Agent 正在运行？
哪些路线失败过？
Blackboard 有什么新知识？
哪些结果已经验证？
还剩多少预算？
```

没有最新 State，就无法进行可靠闭环。

## 6. Orchestrator 如何驱动闭环？

```text
Agent 提交结果
       ↓
Verifier 检查
       ↓
更新 State
       ↓
Manager 重新判断
       ↓
Allocator 调整资源
       ↓
Scheduler 启动新 Agent
```

## 7. 三层嵌套循环

### Agent 内部小循环

```text
思考 → 工具 → 观察 → 再思考
```

### 单任务循环

```text
创建 Attempt → 提交 → 验证 → 重试或完成
```

### 全局研究循环

```text
多 Agent 探索
 ↓
知识压缩
 ↓
判断方向
 ↓
重新分配资源
 ↓
下一轮探索
```

这叫 Nested Loops。

## 8. Workflow 与 Control Loop 不是二选一

成熟架构通常是：

```text
确定性的 Workflow 外壳
+
智能的 Control Loop 内核
```

例如外层固定：

```text
读取状态
调用决策组件
执行动作
验证结果
保存状态
```

但“下一步研究什么”由 Agent 动态判断。

## 9. 为什么不能让 Agent 无限自由循环？

可能出现：

```text
无限重试
任务无限分裂
反复调用工具
预算失控
永远不结束
```

所以程序必须规定：

```text
最大时间
最大 token
最大并发
最大重试
停止条件
验证通过条件
```

## 10. 停止条件

### 成功

Verifier 明确通过。

### 预算耗尽

时间、token、GPU 或费用到达上限。

### 停滞

连续多轮没有新知识、分数提升或有效节点。

### 人工升级

系统无法判断关键方向，交给人处理。

## 11. 一个简化例子

```text
第一轮：探索 A、B、C
第二轮：A 重复失败，B 发现 Lemma Y，C 未确定
第三轮：给 B 更多资源，保留少量 C
第四轮：Y 被验证，并同时解锁 B2、C3
第五轮：合并 B2、C3，Verifier PASS
```

完整路线是在反馈中形成的。

## 12. 自测问题

1. Workflow 和 Control Loop 最根本的区别是什么？
2. 为什么 Control Loop 必须依赖 State？
3. 三层嵌套循环分别控制什么？
4. 为什么仍然需要确定性 Workflow 外壳？

## 本章小结

**Workflow 负责稳定执行已知步骤，Control Loop 负责根据新状态动态调整；高级 Agent 系统通常用确定性外壳约束智能闭环。**

---

# 09. State、Event 与 Durable Execution

## 一句话定义

```text
State：系统现在是什么情况。
Event：刚刚发生了什么。
Durable Execution：系统崩溃后能从已保存进度继续。
```

## 1. State：系统存档

例如：

```text
Theorem X
├── Lemma A：completed
├── Lemma B：running
└── Lemma C：blocked

Agent 17：处理 B
剩余预算：60%
已验证知识：Lemma A
```

State 保存系统当前运行情况。

## 2. State 与 Blackboard

```text
Blackboard
= 研究团队知道什么

State
= 系统现在运行到哪里
```

Blackboard 可能记录“Lemma A 已验证”；State 还要记录哪个任务正在运行、谁拥有租约、预算剩余多少等。

## 3. Event：发生过的事实

```text
task_created
agent_started
result_submitted
verification_failed
verification_passed
task_completed
```

一个 Event 是某个时间点发生的不可变事实。

## 4. 照片与录像类比

```text
State = 某一时刻的照片
Event = 一件件发生的事情
```

例如：

```text
Event：存入 100，支出 30，支出 20
State：当前余额 50
```

## 5. Event-driven：事件驱动

多个 Agent 并发时，事件随时发生：

```text
Agent 1 完成
Agent 28 超时
Agent 7 创建新任务
Agent 53 提交候选结果
```

系统通过事件触发后续动作：

```text
result_submitted
      ↓
创建 verification task
      ↓
verification_passed
      ↓
更新 Blackboard 和 Task State
      ↓
解锁依赖任务
```

## 6. 为什么需要 Durable Execution？

如果状态只在内存：

```text
进程崩溃
机器重启
网络中断
```

系统就可能忘记全部进度。

持久化要求关键状态写入：

```text
Database
Event Log
Object Storage
Checkpoint
```

## 7. Checkpoint

关键步骤完成后保存：

```text
任务创建后
Agent 启动后
结果提交后
Verifier 通过后
Task DAG 更新后
预算变化后
```

Durable Execution 通常从最近的可靠步骤恢复，不一定从模型生成的某个 token 接着继续。

## 8. Heartbeat 与 Lease

### Heartbeat

Agent 定期报告“我还活着”。

### Lease

任务暂时租给某个 Agent：

```text
Task B → Agent 17
租约 60 秒
```

Agent 持续心跳则续租；心跳消失后租约过期，任务重新进入队列。

## 9. 更真实的任务状态机

```text
PENDING
   ↓
CLAIMED
   ↓
RUNNING
   ↓
SUBMITTED
   ↓
VERIFYING
  /        \
PASS       FAIL
 │          │
 ▼          ▼
COMPLETED  RETRY_WAIT
              │
              ▼
           PENDING
```

此外：

```text
BLOCKED
TIMED_OUT
CANCELLED
LOST
```

## 10. 重复事件与 Idempotency

网络不稳定时，同一个结果可能提交两次。

系统需要唯一标识：

```text
event_id
task_id
attempt_id
result_id
allocation_id
```

Idempotency，幂等性，表示：

> 同一个动作执行一次或多次，最终效果一致。

例如“把任务设置为 completed”是幂等的；“再创建 20 个 Agent”不是，必须用 allocation_id 去重。

## 11. Event Sourcing

只保存当前 State 很简单，但难以回答：

- 之前失败过几次？
- 谁修改了状态？
- 为什么进入当前状态？

Event Sourcing 保存完整事件历史，再从事件计算或重建状态。

实际系统通常同时保存：

```text
Current State
用于快速查询

Event Log
用于审计、恢复和重放
```

## 12. 崩溃恢复流程

```text
1. 读取所有任务状态
2. completed 任务不重跑
3. pending 任务重新入队
4. running 但 lease 过期的任务创建新 Attempt
5. submitted 但未验证的结果重新进入验证队列
6. 恢复预算、Task DAG 和 Blackboard
7. 继续执行
```

## 13. 自测问题

1. State 与 Event 的区别是什么？
2. Blackboard 与 State 为什么不能合并成一个概念？
3. Heartbeat 和 Lease 各自解决什么问题？
4. 幂等性为什么比分布式系统中的“绝不重复”更现实？

## 本章小结

**Agent 可以超时、失联和崩溃，但系统必须通过持久化状态、事件日志、检查点、租约和幂等设计保持整体可靠。**

---

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

---

# 11. Budget、Cost 与 Backpressure

## 一句话定义

```text
Budget：最多允许花多少资源。
Cost：已经花了多少资源。
Backpressure：下游处理不过来时，让上游减速。
```

## 1. Task Explosion：任务爆炸

如果一个任务拆成 5 个子任务，每个子任务再拆成 5 个：

```text
第 1 层：5
第 2 层：25
第 3 层：125
第 4 层：625
第 5 层：3125
```

没有限制时，Agent 会不断创建任务和子 Agent，导致指数增长。

## 2. Budget 不只是钱

预算可以包括：

```text
输入和输出 token
Agent 总数
并发 Agent 数
GPU 时间
墙钟时间
工具调用次数
搜索次数
真实费用
验证次数
```

示例：

```yaml
task: task_A
budget:
  max_agents: 10
  max_tokens: 2000000
  max_duration_minutes: 30
  max_search_calls: 100
  max_retries: 3
```

## 3. 分层预算

```text
Global Budget
├── Mission A Budget
│   ├── Task A1 Budget
│   └── Task A2 Budget
└── Mission B Budget
    ├── Task B1 Budget
    └── Task B2 Budget
```

分层预算防止一个局部任务吃光整个项目资源。

## 4. Agent 不能自我复制

Agent 或 Manager 可以申请资源：

```text
“这个方向值得增加 20 个 Agent。”
```

但实际流程应是：

```text
提交 Resource Request
      ↓
Allocator 检查价值和剩余预算
      ↓
批准、部分批准或拒绝
      ↓
Scheduler 实际创建 Agent
```

## 5. Cost Accounting：成本归属

每个 Attempt 应记录：

```text
task_id
attempt_id
model
input_tokens
output_tokens
tool_calls
duration
GPU time
money_cost
result_status
```

系统不仅要知道总成本，还要知道钱花在哪个方向、角色和模型上。

## 6. Activity without Progress

大量 Agent 很忙，不代表有进展。

```text
100 个 Agent
50M tokens
1000 次工具调用
```

但可能：

```text
没有新知识
没有通过验证的结果
没有新依赖节点
答案高度重复
```

因此预算分配必须参考“进展信号”。

## 7. 进展信号

- 新的非重复路线；
- 新的可验证 Lemma；
- 明确的失败原因；
- Verifier 分数提升；
- 关键依赖被解决；
- 不确定性显著下降；
- 产生可跨分支复用的知识。

连续多轮无新进展，应降低预算或停止。

## 8. Marginal Value：边际价值

```text
第 1 个 Agent：发现新路线
第 2 个 Agent：补充关键细节
第 3 个 Agent：找到漏洞
第 20 个 Agent：重复已有答案
第 100 个 Agent：仍然重复
```

任务再重要，也不代表无限增加 Agent 都有价值。

## 9. Backpressure

餐厅类比：

```text
前台每分钟接 100 单
厨房每分钟只能做 10 单
```

厨房必须要求前台减速，否则队列无限增长。

Agent 系统中：

```text
1000 个 Worker 每分钟提交 1000 个结果
Verifier 每分钟只能处理 50 个
```

继续增加 Worker 只会制造积压。

## 10. Budget 与 Backpressure 的区别

```text
Budget
= 整个任务总共最多花多少

Backpressure
= 当前这一刻最多能跑多快
```

总预算还有很多，不代表此刻可以无限并发。

## 11. Concurrency Limit 与 Queue Limit

### 并发上限

```text
最多同时运行 100 个 Agent
```

### 队列上限

```text
最多积压 1000 个等待任务
```

到达上限后，需要暂停低优先级任务或拒绝新任务。

## 12. Admission Control：准入控制

并不是 Agent 提出的每个子任务都必须进入系统。

创建新任务前应说明：

```text
为什么需要它？
它解锁什么？
是否和现有任务重复？
怎样判断完成？
预计成本是多少？
```

## 13. 过载时的降速手段

- 降低并发；
- 暂停低优先级任务；
- 暂停任务继续分裂；
- 缩小单个 Agent 的预算；
- 合并重复候选；
- 优先处理高分结果；
- 增加 Synthesizer 或 Verifier；
- 提高知识压缩频率。

## 14. 瓶颈不一定是 Worker

```text
Worker 很快
Synthesizer 跟不上
Verifier 严重积压
```

正确动作可能是：

```text
减少 Worker
增加 Synthesizer
增加 Verifier
```

系统吞吐量由最慢环节决定。

## 15. 防止任务无限分裂

可以设置：

```text
最大图深度
单节点最大子任务数
单 Agent 最大 Proposal 数
未经 Manager 审核的节点不能继续分裂
```

## 16. Budget Inheritance：预算继承

父任务有 10M token：

```text
A1：4M
A2：3M
A3：2M
保留：1M
```

子任务预算来自父任务，而不是凭空增加。

## 17. 动态追加和回收预算

当 A2 出现突破：

```text
从低价值任务回收 5M
追加给 A2
```

这类似投资组合再平衡。

## 18. 停止规则

- 已经验证成功；
- 连续多轮无进展；
- 结果重复率过高；
- 预算耗尽；
- 上游结论使任务失去意义；
- 出现明显更优替代方案；
- 边际价值低于成本。

## 19. 自测问题

1. Budget 和 Backpressure 的区别是什么？
2. 为什么增加 Worker 可能让系统更慢？
3. 什么是边际价值？
4. 为什么子任务预算必须继承父任务预算？

## 本章小结

**资源必须由系统分配，不能由 Agent 自我复制；成熟编排既要限制总成本，也要根据流水线瓶颈动态控制当前速度。**

---

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

---

# 13. 剩余知识点地图：除 Human-in-the-loop 外还要学什么？

## 一句话说明

前 12 章已经覆盖多 Agent 编排的主干。剩余内容主要分成三类：

```text
让 Agent 更聪明地协作
让系统更安全、更可靠
让系统成为可上线的工程平台
```

下面是后续知识地图。

---

## 1. Context Engineering：上下文工程

核心问题：

```text
Agent 开始工作时，应该看到哪些信息？
哪些不应该看到？
信息按什么顺序组织？
```

它通常包括：

```text
系统规则
角色定义
当前任务
父目标
相关知识
失败历史
工具说明
权限
预算
输出契约
```

Prompt Engineering 关注一段指令怎么写；Context Engineering 关注 Agent 的整个信息环境。

---

## 2. Retrieval Policy：知识检索策略

Blackboard 很大时，不能只做向量相似搜索。

还要考虑：

```text
任务图距离
知识可信等级
时间新旧
当前分支
跨分支复用价值
是否已经被新版本替代
是否来自独立证据
```

目标是把最相关、最可靠、最少冗余的信息放进有限上下文。

---

## 3. Model Routing：模型路由

并非所有任务都应该使用最强、最贵模型。

```text
简单分类 → 小模型
任务拆解 → 强推理模型
代码修改 → 代码模型
摘要压缩 → 便宜快速模型
关键最终方案 → 最强模型
外部验证 → 测试或形式化系统
```

还可以设计升级策略：

```text
便宜模型失败
 ↓
更强模型
 ↓
仍失败
 ↓
拆任务或人工介入
```

---

## 4. Heterogeneous Agents：异构 Agent

团队可以由不同模型、工具和能力构成：

```text
数学 Agent
代码 Agent
搜索 Agent
视觉 Agent
快速小模型 Agent
形式化验证器
```

需要研究：能力发现、任务匹配、成本差异和跨模型通信。

---

## 5. Communication Protocol：Agent 通信协议

不能只让 Agent 发送任意自然语言长文。

应定义结构化消息类型：

```text
TASK_PROPOSAL
CLAIM
COUNTEREXAMPLE
FAILURE_REPORT
RESOURCE_REQUEST
VERIFICATION_RESULT
KNOWLEDGE_UPDATE
CONFLICT
```

结构化消息方便自动路由、验证、存储和追踪。

---

## 6. Task Contract：任务与结果契约

不好的任务：

```text
研究一下方法 A
```

更好的任务：

```text
目标：证明或否定 Lemma A
完成条件：Lean proof 或可验证反例
允许工具：Lean、Python
预算：50k tokens
输出：claim、evidence、dependencies
```

契约要明确输入、输出、验收、预算、权限和失败报告。

---

## 7. Verifier Architecture：多层验证体系

实际验证往往是分层的：

```text
格式检查
 ↓
规则检查
 ↓
独立 Critic
 ↓
运行代码或实验
 ↓
形式化验证
 ↓
必要时人工审核
```

不同 Claim 应匹配不同验证器，而不是所有结果都由另一个 LLM 评分。

---

## 8. Uncertainty Management：不确定性管理

Agent 不应只输出结论，还应声明：

```text
哪些部分已验证？
哪些只是猜测？
最大风险在哪里？
需要什么证据才能确认？
```

可信度不能只依赖模型自报分数，还要结合：

```text
独立 Agent 一致性
外部 Verifier
反例情况
重复实验
证据独立性
```

---

## 9. Consensus 与 Arbitration：共识与裁决

多个 Agent 意见一致，不代表结论正确。

更可靠的流程：

```text
保留多个候选
 ↓
比较证据
 ↓
寻找冲突和反例
 ↓
外部验证
 ↓
仲裁或正式 Commit
```

Consensus 解决“大家是否一致”；Truth 解决“结论是否真的成立”。

---

## 10. Stopping 与 Convergence：停止与收敛

需要判断：

```text
何时继续搜索？
何时已经足够好？
何时出现停滞？
何时所有 Agent 被同一个错误带偏？
```

除了预算和 PASS，还要看边际价值、重复率、分支覆盖和知识稳定性。

---

## 11. Deadlock、Livelock 与 Starvation

### Deadlock

```text
A 等待 B
B 等待 C
C 等待 A
```

### Livelock

系统不断修改和反驳，但没有实际进展。

### Starvation

某个任务因为一直有更高优先级工作，长期拿不到资源。

需要循环依赖检测、停滞检测、最大重试和优先级老化。

---

## 12. Rollback 与 Compensation：回滚与补偿

真实世界动作可能包括：

```text
发邮件
修改数据库
创建订单
付款
删除文件
发布代码
```

可逆动作可以回滚；不可逆动作需要补偿操作，例如创建取消订单或恢复记录。

---

## 13. Workspace 与 Artifact Management

Agent 不只产出文字，还会产生：

```text
代码
文件
证明
数据集
实验结果
模型权重
数据库变更
```

需要处理：

```text
工作区隔离
文件版本
分支
测试
合并冲突
产物血缘
结果复现
```

对 Codex 类系统尤其重要。

---

## 14. Security 与 Permission：安全与权限

必须明确：

```text
哪个 Agent 能使用什么工具？
能读取哪些数据？
能修改哪些文件？
能否联网？
能否执行 shell？
能否发送邮件或付款？
能否创建新 Agent？
```

核心原则是最小权限。

---

## 15. Prompt Injection 与 Agent-to-Agent Attack

Agent 读取网页、邮件或其他 Agent 消息时，可能遇到恶意指令。

多 Agent 系统中，一个被污染的 Agent 还可能把恶意内容写进 Blackboard，继续感染其他 Agent。

需要：

```text
区分数据与指令
来源标记
消息签名或身份验证
工具权限隔离
知识写入审核
不可信内容标记
敏感信息过滤
```

---

## 16. Goal Drift：目标漂移

系统可能逐渐把大量资源放到有趣但与根目标无关的子问题。

每个任务最好记录：

```text
parent_goal
why_it_matters
expected_value
unlock_condition
success_criteria
```

并定期检查和根目标的关系。

---

## 17. Credit Assignment：贡献归因

最终结果通常由多人链式贡献：

```text
Explorer 提出方向
Critic 找到漏洞
Connector 组合知识
Worker 修复
Verifier 确认
```

需要沿知识依赖图追踪哪些角色、模型和策略真正进入成功路径。

---

## 18. Reputation：Agent 与策略信誉

Agent Instance 是临时的，但 Role、Prompt、模型和策略可以积累历史表现：

```text
成功率
真实错误发现率
误报率
成本
重复率
擅长任务类型
```

系统可以据此改进路由，但要防止早期偶然成功造成长期偏见。

---

## 19. Learning from Traces：从历史轨迹学习

Trace 可以用于学习：

```text
什么任务拆解最有效？
哪些失败信号应提前停止？
什么上下文组合最好？
什么时候升级模型？
哪些知识最容易复用？
```

先可以更新规则，进一步再训练：

```text
优先级模型
结果评分模型
模型路由器
任务拆解器
停止策略
```

---

## 20. Multi-tenancy：多租户

多个用户和项目共用平台时，需要：

```text
数据隔离
权限隔离
资源配额
公平调度
成本归属
审计日志
```

一个项目不能读取另一个项目的私有知识，也不能占光全部算力。

---

## 21. Protocol 与 Interoperability：协议互操作

未来可能接入外部 Agent、远程工具、第三方验证器和不同运行时。

需要统一描述：

```text
能力
任务格式
结果格式
权限
错误
Artifact
状态
事件
```

目标是让不同系统能安全地说同一种语言。

---

## 22. Domain-specific Orchestration：领域专用编排

### 数学证明

```text
Proof DAG、Lemma、Lean、依赖闭包
```

### 代码开发

```text
Issue、Workspace、Git Branch、Tests、Review、Merge
```

### 科学实验

```text
Hypothesis、Experiment、Dataset、统计检验、复现
```

### 企业业务

```text
审批、权限、事务、补偿、审计、人工确认
```

通用框架只能提供底座，领域规则决定真正架构。

---

## 23. Swarm Stability：群体稳定性

大量 Agent 可能出现群体行为：

```text
羊群效应
错误知识快速扩散
热门任务吸走全部预算
不同分支间来回震荡
局部最优
信息瀑布
```

需要：

```text
多样性配额
独立验证
探索预算
分组隔离
反对意见机制
受控知识传播
```

---

## 24. Architecture Patterns：常见架构模式

后续需要比较：

```text
Manager–Worker
Planner–Executor
Generator–Critic
Debate
Blackboard
Map–Reduce
Hierarchical Swarm
Peer-to-Peer
Market-based Allocation
Tree Search
Actor Model
Event-driven Orchestration
```

重点不是背名称，而是理解每种模式解决哪类问题、会产生什么瓶颈。

---

# 推荐的后续必修顺序

```text
1. Context Engineering 与 Retrieval
2. Model Routing 与异构 Agent
3. 通信协议与 Task Contract
4. Verifier、共识与不确定性
5. 停止、收敛、死锁与目标漂移
6. Workspace、Artifact、回滚与补偿
7. 安全、权限与 Prompt Injection
8. Human-in-the-loop 与治理
```

完成必修部分后，再深入：

```text
从 Trace 学习
信誉与贡献归因
群体稳定性
多租户
协议互操作
领域专用架构
```

## 本章小结

**前 12 章解决“一个多 Agent 系统如何运转”，剩余知识则解决“它如何更聪明、更安全、更可扩展，并适配真实领域”。**
