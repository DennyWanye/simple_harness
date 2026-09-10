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
