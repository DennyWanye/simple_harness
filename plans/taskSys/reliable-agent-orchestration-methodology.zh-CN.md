# 复杂 Agent 编排：久经验证的方法论与设计原则

> 目标：为“长期运行、复杂任务、主 Agent + 动态子 Agent、可恢复、可审计、可验证”的 Agent Task Runtime 提供一套基于成熟软件工程思想的编排方法论。

---

## 一、总原则

如果强调“久经验证”，优先参考那些已经在以下领域被长期证明有效的思想：

- 分布式系统
- 操作系统
- 工作流引擎
- 高可用系统
- 电信系统
- 航空航天 / 工业控制
- 事务处理系统

而不是仅仅依赖新近出现的 Multi-Agent Framework。

对于一个长期个人 Agent 或复杂任务 Agent 系统，真正需要解决的是：

- 如何拆解目标
- 如何建立任务依赖
- 如何调度
- 如何恢复
- 如何验证
- 如何处理失败
- 如何记录事实
- 如何管理不可逆操作
- 如何避免重复执行
- 如何在系统崩溃后继续运行

---

# 二、推荐参考的核心方法论

## 1. HTN：Hierarchical Task Network

### 核心思想

HTN 负责把复杂目标递归拆成更小的任务。

```text
Goal
│
├── Task A
│   ├── Task A1
│   └── Task A2
│
├── Task B
│
└── Task C
    ├── Task C1
    └── Task C2
```

它解决的是：

> **“要做什么？”**

对于长期 Agent：

```text
Goal
↓
Task
↓
SubTask
↓
Atomic Task
```

任务本身可以长期存在，而具体执行它的 Worker 可以随时被替换。

---

## 2. Workflow Patterns

### 核心思想

DAG 并不只是：

```text
A → B → C
```

成熟工作流系统已经总结出大量控制流模式。

常见模式包括：

- Sequence
- Parallel Split
- Synchronization
- Exclusive Choice
- Multiple Choice
- Deferred Choice
- Milestone
- Cancel Activity
- Cancel Case
- Multiple Instances
- Discriminator

例如：

```text
等待：
├── 用户回复
├── 邮件到达
├── 截止时间到
└── 外部条件满足

谁先发生
→ 走对应分支
```

这类情况很难仅靠静态 DAG 优雅表达。

### 对 Agent 系统的价值

建议：

> DAG Scheduler 的语义设计尽量参考成熟 Workflow Patterns，而不是重新发明所有控制结构。

---

## 3. Erlang / OTP Supervision Tree

这是极其成熟的高可靠设计思想。

### 核心结构

```text
Supervisor
├── Worker A
├── Worker B
└── Worker C
```

Supervisor 不负责具体业务，而负责：

- 启动 Worker
- 停止 Worker
- 监控 Worker
- Worker 崩溃后恢复
- 决定故障影响范围

Erlang / OTP 常见 restart strategy：

```text
one_for_one
one_for_all
rest_for_one
```

### 映射到 Agent 系统

```text
MainAgent
  ↓
MainWorkA   ← Supervisor
  ↓
SubWorkerA1
SubWorkerA2
SubWorkerA3
```

真正应该考虑的不是：

> “失败以后再重试几次。”

而是：

```text
谁负责恢复？
失败影响谁？
恢复范围多大？
什么时候停止恢复？
什么时候重新规划？
```

这对于 MaxRuns 设计尤其重要。

---

# 4. Durable Execution / Temporal 思想

这是非常值得 Agent Runtime 参考的一类设计。

### 核心目标

一个任务可以运行：

```text
几分钟
几天
几个月
几年
```

即使中间发生：

```text
进程死亡
机器重启
网络故障
Worker 被杀
服务升级
```

任务仍然能够恢复。

### 核心结构

```text
Task A
│
├── Durable Workflow
│
├── Event History
│
├── Activities
│    ├── LLM
│    ├── Browser
│    ├── API
│    ├── Files
│    └── Tools
│
└── Crash Recovery
```

### 一个非常重要的边界

建议把系统分成：

```text
确定性层
────────────────
Task State Machine
DAG Scheduler
Retry Policy
Timeout
MaxRuns
Event Store
Permission
Assignment
Resource Control

非确定性层
────────────────
MainWork reasoning
HTN decomposition
Worker reasoning
Verifier reasoning
```

原则：

> **Reasoning 可以是不确定的，但 orchestration core 应尽量确定。**

---

# 5. State Machine

这是整个 Agent Runtime 最基础的约束之一。

不要允许 Agent 自己随意定义任务状态。

例如：

```text
CREATED
↓
PLANNING
↓
READY
↓
RUNNING
↓
VERIFYING
↓
COMPLETED
```

另外：

```text
BLOCKED
PAUSED
RETRYING
ESCALATED
FAILED
CANCELLED
```

所有状态变化必须通过合法 transition。

例如：

```text
RUNNING
→ VERIFYING

VERIFYING
→ COMPLETED
```

而不能随意：

```text
CREATED
→ COMPLETED
```

除非 Runtime 明确定义这种 transition。

### 核心原则

> **LLM 决定“想做什么”，State Machine 决定“是否允许这样做”。**

---

# 6. Saga / Compensating Transaction

现实世界很多操作无法真正 rollback。

例如：

```text
发邮件
支付
预订
发布内容
删除文件
提交申请
修改外部系统
签署合同
```

数据库事务可以 rollback。

现实世界通常不行。

### Saga 思想

```text
Step1 ✅
↓
Step2 ✅
↓
Step3 ✅
↓
Step4 ❌

开始补偿：

Compensate Step3
Compensate Step2
Compensate Step1
```

例如：

```text
订机票 ✅
订酒店 ✅
租车 ✅
签证失败 ❌

↓
取消租车
取消酒店
取消机票
```

### 对 Agent Task Node 的建议

可以设计：

```text
TaskNode
├── execute()
├── verify()
└── compensate()
```

但不是所有任务都必须支持 compensation。

建议分类：

```text
reversible
compensatable
irreversible
```

例如：

```text
发送邮件
支付
删除账号
签署合同
```

属于不可逆或强副作用动作。

这类操作应该有：

```text
Point of No Return
```

并在执行前进行更严格验证或用户批准。

---

# 7. Idempotency

这是 Durable Execution 必须配套的思想。

例如：

```text
Worker:
发送邮件
```

邮件其实已经发送成功。

但系统在写入：

```text
EMAIL_SENT
```

之前崩溃。

恢复后系统看到：

```text
Task 未完成
```

于是再次执行发送。

最终用户收到两封邮件。

### 解决方式

每个有副作用的动作应该考虑：

```text
Idempotency Key
```

例如：

```text
task123-step7-attempt1
```

同一个动作再次执行时：

```text
检测此前已完成
→ 不重复执行
→ 返回原始结果
```

### 原则

> **Every external side effect must consider idempotency.**

---

# 8. Retry + Circuit Breaker

MaxRuns 本身不够。

错误首先应该分类：

```text
Transient Failure
Permanent Failure
Unknown Failure
```

例如：

```text
Network Timeout
→ retry

401 Unauthorized
→ 不应该 retry

服务持续不可用
→ Circuit Breaker
```

### Circuit Breaker 状态

```text
CLOSED
↓ 连续失败
OPEN
↓ 等待一段时间
HALF_OPEN
↓ 测试调用
CLOSED / OPEN
```

### Agent 系统中的形式

```text
Tool/API 连续失败
↓
Circuit Open
↓
Task BLOCKED
↓
等待恢复
↓
Half Open
↓
试探调用
```

因此失败控制至少应该是：

```text
Retry Policy
+
Circuit Breaker
+
Escalation Policy
```

而不是只有：

```text
MaxRuns
```

---

# 9. Bulkhead Isolation（舱壁隔离）

这个思想来自船舶。

一个舱室进水：

```text
≠
整艘船沉没
```

同理：

```text
Task A 出问题
```

不能拖垮：

```text
Task B
Task C
MainAgent
整个 Runtime
```

### 建议加入 Resource Budget

```text
TaskA:
max_workers = 20
max_tokens = 5M
max_tool_calls = 1000
max_runtime = 24h
```

还可以限制：

```text
CPU
Memory
GPU
API quota
Concurrent tools
External spending
```

### 核心原则

> 一个 Task 的故障和资源失控，不能扩散到整个 Agent 系统。

---

# 10. Command / Event 分离

这是 Event Sourcing 设计中非常重要的一点。

## Command

表示：

> 请求系统做什么。

例如：

```text
START_TASK
SEND_EMAIL
CANCEL_BOOKING
```

## Event

表示：

> 已经发生了什么。

例如：

```text
TASK_STARTED
EMAIL_SENT
BOOKING_CANCELLED
```

两者绝不能混淆。

例如：

```text
SEND_EMAIL
```

不等于：

```text
EMAIL_SENT
```

因为发送可能失败。

### 推荐流程

```text
Intent
↓
Command
↓
Execution
↓
Event
```

---

# 11. Event Sourcing + CQRS

你当前设计已经非常自然地接近 CQRS。

CQRS：

> Command Query Responsibility Segregation

简单来说：

```text
写系统
≠
读系统
```

### Write Side

```text
Commands
↓
Runtime
↓
Event Store
```

### Read Side

```text
Event Store
↓
Projection
├── Blackboard
├── Task Dashboard
├── Search Index
├── Memory
└── Analytics
```

因此：

```text
Event Store = Truth
Blackboard = Projection
```

Blackboard 不应该是 Source of Truth。

它坏了以后：

```text
Replay Events
↓
重新生成 Blackboard
```

---

# 三、与当前 Agent 架构的组合

可以形成：

```text
User
 ↓
Main Agent
 ↓
Task
 ↓
Main Work
 ↓
HTN Decomposition
 ↓
Versioned DAG
 ↓
Scheduler
 ↓
Workers
 ↓
Execution
 ↓
Artifacts
 ↓
Independent Verifier Agent
 ↓
PASS / FAIL / UNCERTAIN
 ↓
Retry / Replan / Escalation
 ↓
Final Task Verification
 ↓
Completed
```

底层：

```text
Event Store
+
Artifact Store
+
State Machine
+
Blackboard Projection
+
Scheduler
+
Retry / Circuit Breaker
+
Compensation Engine
+
Resource Isolation
```

---

# 四、建议的权限边界

这是整个系统非常重要的一层。

## MainWork

```text
Plan Authority
```

负责：

- HTN 分解
- DAG 生成
- DAG 修改
- Task Assignment
- Replan
- Escalation Decision

---

## Worker

```text
Execution Authority
```

负责：

- 执行 SubTask
- 调用工具
- 产生 Artifact
- 提交 Verification
- 提出 Plan Change Request

Worker 不允许直接修改 DAG。

---

## Verifier

```text
Acceptance Authority
```

负责：

- 理解 Original User Intent
- 检查 Task Contract
- 检查 Worker Output
- 检查 Artifact
- 自主决定验证策略
- 主动收集 Evidence
- 调用测试 / API / Web / 文件等工具
- 输出：

```text
PASS
FAIL
UNCERTAIN
```

---

## Runtime

```text
Enforcement Authority
```

负责：

- State Machine
- 权限控制
- Event 写入
- 幂等控制
- Retry
- Timeout
- Circuit Breaker
- Resource Budget
- Scheduler
- Recovery

---

# 五、推荐的失败恢复层级

失败不应该统一处理成 Retry。

建议：

```text
Failure
↓
Classify
```

分类：

```text
Worker Failure
Plan Failure
Dependency Failure
Tool Failure
Environment Failure
Verification Ambiguity
Goal Impossible
Need User Input
```

对应：

```text
Worker Failure
→ Retry / Replace Worker / Change Model

Plan Failure
→ Replan DAG

Dependency Failure
→ Reopen Dependency

Tool Failure
→ Retry / Fallback / Circuit Breaker

Environment Failure
→ Block / Wait

Verification Ambiguity
→ Gather More Evidence / Stronger Verification

Goal Impossible
→ Fail Task

Need User Input
→ Escalate
```

所以：

> **Retry is not Recovery.**

真正的 Recovery 包括：

```text
Retry
Replan
Replace Worker
Fallback
Compensate
Pause
Escalate
Abort
```

---

# 六、12 条 Agent Runtime “宪法”

## 1.

> **Task is persistent, Worker is disposable.**

任务长期存在，Worker 可以随时死亡、重建。

---

## 2.

> **Event Store is truth, Blackboard is projection.**

Blackboard 是当前视图，不是真相本身。

---

## 3.

> **LLM reasons; runtime enforces.**

LLM 负责推理，Runtime 负责约束。

---

## 4.

> **No important state transition without an Event.**

任何重要状态变化，如果没有 Event，就视为没有发生。

---

## 5.

> **Commands express intent; Events record facts.**

Command 表达意图。

Event 记录事实。

---

## 6.

> **MainWork owns Plan. Worker owns Execution. Verifier owns Acceptance.**

规划、执行、验收严格分权。

---

## 7.

> **Workers propose; MainWork changes the DAG.**

Worker 可以提出 DAG Change Request，但不能直接修改全局 DAG。

---

## 8.

> **Every external side effect must consider idempotency.**

任何外部副作用都必须考虑幂等。

---

## 9.

> **Every irreversible action must have a boundary.**

不可逆操作必须明确 Point of No Return。

---

## 10.

> **Retry is not recovery.**

Retry 只是恢复策略中的一种。

---

## 11.

> **Subtask success does not imply Goal success.**

所有 SubTask 都成功，不代表最终目标成功。

所以必须有：

```text
Final Integration Verification
```

---

## 12.

> **Assume everything can crash at any moment.**

必须假设：

```text
MainWork 会崩
Worker 会崩
Verifier 会崩
机器会重启
网络会断
API 会失效
模型供应商会变化
```

如果：

```text
kill -9 MainWorkA
```

然后重启系统，

TaskA 仍然可以恢复继续执行，

这个 Runtime 才真正具有长期可靠性。

---

# 七、最值得优先深入研究的三个方向

如果不再横向收集更多 Agent Framework，而是开始真正打磨架构，建议优先深入：

## 1. Erlang / OTP Supervision Tree

研究：

- Supervisor
- Worker
- Restart Strategy
- Failure Domain
- Process Isolation
- Let It Crash

解决：

> Worker、MainWork、Verifier 崩溃以后，谁负责恢复，以及如何控制故障范围。

---

## 2. Temporal / Durable Execution

研究：

- Workflow
- Activity
- Event History
- Replay
- Determinism
- Retry
- Timer
- Signal
- Long-running Workflow

解决：

> 一个 Task 如何跨机器重启、跨进程、跨几个月甚至几年持续运行。

---

## 3. Saga / Compensating Transaction

研究：

- Saga
- Compensation
- Point of No Return
- Partial Failure
- Long-running Transaction

解决：

> Agent 操作真实世界以后，如果中间失败，如何撤销、补偿和恢复。

---

# 八、最终推荐架构

可以把整个系统理解为：

```text
                    Main Agent
                         │
                         ▼
                       Task
                         │
                         ▼
                    Main Work
                         │
                  HTN Decompose
                         │
                         ▼
                  Versioned DAG
                         │
                ┌────────┼────────┐
                ▼        ▼        ▼
             Worker   Worker   Worker
                │        │        │
                ▼        ▼        ▼
            Artifact Artifact Artifact
                │        │        │
                ▼        ▼        ▼
             Verifier  Verifier Verifier
                │
        PASS / FAIL / UNCERTAIN
                │
        Retry / Replan / Escalate
                │
                ▼
          DAG Nodes Complete
                │
                ▼
       Final Integration Verify
                │
                ▼
            Task Complete
```

系统底座：

```text
Event Store
│
├── State Machine
├── Scheduler
├── Blackboard Projection
├── Artifact Store
├── Idempotency
├── Retry Policy
├── Circuit Breaker
├── Supervision
├── Compensation
└── Resource Isolation
```

---

# 九、最终结论

真正可靠的复杂 Agent 编排系统，不应该被理解为：

```text
一群 Agent 在互相协作
```

而应该理解为：

> **一个严格的、Durable、Event-Sourced、State-Machine-Driven Task Runtime，只是其中部分 Planning、Execution 和 Verification 工作由 LLM Agent 承担。**

也就是说：

```text
Agent ≠ System

Agent = Runtime 中的一种计算资源
```

真正长期存在的应该是：

```text
Task
Event
Artifact
State
Policy
History
```

而不是具体某个 Worker Agent。

最终推荐组合：

```text
HTN
+
Workflow Patterns
+
Versioned DAG
+
MainWork / Worker / Verifier
+
State Machine
+
Erlang Supervision
+
Durable Execution
+
Event Sourcing + CQRS
+
Saga / Compensation
+
Idempotency
+
Retry / Circuit Breaker
+
Bulkhead Isolation
```

这套组合比单独依赖某个 Multi-Agent Framework 更适合作为长期 Agent Task Runtime 的底层架构。
