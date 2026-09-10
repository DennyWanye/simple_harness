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
