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
