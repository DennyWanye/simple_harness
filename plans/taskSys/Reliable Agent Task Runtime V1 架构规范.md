# Reliable Agent Task Runtime V1

## 1. 目标

构建一个可以长期运行的 Agent Task Runtime，用于支持：

- 一个长期存在的 Main Agent
- 多个 Task
- 每个 Task 一个 MainWork
- 动态创建任意数量 Worker
- 独立 Verifier Agent
- HTN 任务分解
- Versioned DAG 执行计划
- 动态 Replan
- Durable Execution
- Event Sourcing
- Blackboard
- Retry / Recovery / Escalation
- Policy / Permission / Guardrail
- Artifact 管理
- 长期运行和故障恢复

系统设计目标不是让 Agent “更聪明”，而是让 Agent：

> **在不可靠、非确定性的 LLM 基础上，运行在一个可靠、可恢复、可审计的 Runtime 中。**

---

# 2. 核心设计原则

V1 固定以下原则，不再随意变化。

## Principle 1

```text
Task is persistent.
Worker is disposable.
```

Task 是长期对象。

Worker 是一次执行资源。

Worker 可以崩溃、重启、替换、销毁。

---

## Principle 2

```text
Event Store = Source of Truth
```

任何重要状态变化：

```text
必须产生 Event
```

没有 Event：

```text
视为没有发生
```

---

## Principle 3

```text
Blackboard = Projection
```

Blackboard 只负责：

```text
当前状态
```

不负责：

```text
历史真相
```

Blackboard 丢失后，可以通过 Event Replay 重建。

---

## Principle 4

```text
MainWork owns Plan
Worker owns Execution
Verifier owns Acceptance
Runtime owns Enforcement
```

四种 Authority 严格分离。

---

## Principle 5

```text
LLM reasons.
Runtime enforces.
```

Agent 可以提出：

```text
我要执行 X
```

但 Runtime 决定：

```text
X 是否合法
X 是否有权限
X 是否超过预算
X 当前是否允许执行
```

---

## Principle 6

```text
Worker cannot complete a Task.
```

Worker 只能：

```text
SUBMIT
```

Verifier 才可以：

```text
PASS / FAIL / UNCERTAIN
```

---

## Principle 7

```text
Subtask success != Goal success
```

所有 DAG 节点完成以后：

```text
必须执行 Final Verification
```

---

## Principle 8

```text
Retry != Recovery
```

Recovery 包括：

```text
Retry
Replan
Reassign
Replace Worker
Fallback
Wait
Compensate
Escalate
Abort
```

---

## Principle 9

```text
Every external side effect must consider idempotency.
```

外部副作用：

```text
发邮件
支付
删除
提交
发布
修改外部系统
```

必须考虑幂等。

---

## Principle 10

```text
Assume everything can crash.
```

必须假设：

```text
MainWork 会崩
Worker 会崩
Verifier 会崩
机器会重启
网络会断
模型会失效
Tool 会失败
```

---

# 3. 系统核心对象

V1 只保留 10 个核心对象。

```text
Goal
Task
Plan
TaskNode
Work
Execution
Artifact
Verification
Event
Lease
```

不要继续增加大量抽象。

---

# 4. Goal

Goal 表示用户真正希望实现的目标。

例如：

```text
“帮我部署一个适合双 DGX Spark 的本地模型。”
```

结构：

```yaml
Goal:
  goal_id:
  user_intent:
  created_at:
  status:

  constraints:
  preferences:

  root_task_id:
```

Goal 一般不会频繁改变。

---

# 5. Task

Task 是系统最核心的持久化对象。

```yaml
Task:
  task_id:
  goal_id:

  parent_task_id:

  title:
  description:

  type:

  priority:

  status:

  success_criteria:

  active_plan_id:

  owner_work_id:

  created_at:
  updated_at:
```

---

# 6. Task Status State Machine

V1 固定 Task State Machine。

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

辅助状态：

```text
BLOCKED
PAUSED
RETRYING
ESCALATED
FAILED
CANCELLED
```

合法状态转换必须由 Runtime 控制。

例如：

```text
CREATED → PLANNING

PLANNING → READY

READY → RUNNING

RUNNING → VERIFYING

VERIFYING → COMPLETED
```

Agent 不能直接修改状态字段。

Agent只能发出：

```text
Command
```

Runtime 验证后产生：

```text
Event
```

Projection 更新 Task Status。

---

# 7. Main Agent

Main Agent 是唯一长期存在的顶层 Agent。

职责：

```text
理解用户
建立 Goal
创建 Task
启动 MainWork
接收 Task 最终结果
处理用户级 Escalation
向用户汇报
```

Main Agent 不负责：

```text
直接管理所有 Worker
直接执行所有子任务
直接维护 DAG
```

---

# 8. MainWork

每一个复杂 Task 对应一个 MainWork。

例如：

```text
TaskA
↓
MainWorkA
```

MainWork 生命周期：

```text
Task Start
↓
Spawn MainWork
↓
Plan
↓
Supervise
↓
Replan
↓
Finalize
↓
Task End
↓
Terminate
```

MainWork 是：

```text
Task-scoped Supervisor
```

---

# 9. MainWork 权限

MainWork 是：

```text
Plan Authority
```

允许：

```text
创建 Plan
创建 TaskNode
修改 DAG
批准 Plan Change
分配 Worker
决定 Retry
决定 Replan
决定 Escalation
触发 Final Verification
```

不允许：

```text
直接修改 Event History
直接伪造 Worker Result
自己决定自己的 Task 完成
```

---

# 10. HTN

MainWork 首先执行：

```text
Goal
↓
HTN Decomposition
```

例如：

```text
TaskA

├── A1
├── A2
│   ├── A2.1
│   └── A2.2
└── A3
```

HTN 解决：

```text
What needs to be done?
```

---

# 11. Plan

HTN 结果进一步形成 Plan。

重要：

```text
Task != Plan
```

Task 是目标。

Plan 是当前执行方案。

一个 Task 可以存在：

```text
Plan v1
Plan v2
Plan v3
```

结构：

```yaml
Plan:
  plan_id:
  task_id:

  version:

  status:

  created_by:

  created_at:

  node_ids:

  supersedes_plan_id:
```

---

# 12. DAG

Plan 内部使用 DAG 表达执行依赖。

例如：

```text
A1 ──────┐
         ↓
        A3
         ↑
A2 ──────┘
```

TaskNode：

```yaml
TaskNode:
  node_id:
  task_id:
  plan_id:

  title:
  description:

  dependencies:

  execution_mode:

  status:

  max_runs:

  timeout:

  resource_budget:

  side_effect_type:
```

---

# 13. Execution Mode

不是所有节点都强制 Planner → Executor → Verifier。

V1 支持：

```text
ATOMIC
SIMPLE
COMPLEX
ADAPTIVE
```

### ATOMIC

```text
Tool
↓
Verifier
```

例如：

```text
读取文件
获取 API
```

### SIMPLE

```text
Worker
↓
Verifier
```

### COMPLEX

```text
Worker Planner
↓
Execute
↓
Verifier
```

### ADAPTIVE

```text
Plan
↓
Execute
↓
Observe
↓
Replan
↓
Execute
↓
Verifier
```

---

# 14. Worker

Worker 是临时执行 Agent。

职责：

```text
接收 TaskNode
读取 Context
执行任务
调用 Tool
产生 Artifact
提交 Verification
```

Worker 不拥有 Task。

Worker 不允许修改 DAG。

Worker 发现计划问题，只允许：

```text
PLAN_CHANGE_REQUESTED
```

---

# 15. Structured Agent Protocol

Agent 之间尽量不使用自由聊天作为系统控制机制。

V1 固定以下消息类型：

```text
TASK_ASSIGNED

PROGRESS_REPORTED

QUESTION_RAISED

BLOCKER_REPORTED

ARTIFACT_SUBMITTED

VERIFICATION_REQUESTED

VERIFICATION_RESULT

PLAN_CHANGE_REQUESTED

RESOURCE_REQUESTED

ESCALATION_REQUESTED
```

自然语言可以存在于 payload。

但协议类型必须结构化。

例如：

```json
{
  "type": "PLAN_CHANGE_REQUESTED",
  "task_id": "task_A",
  "node_id": "A2",
  "reason": "missing_dependency",
  "proposal": {
    "add_node": "A2.1"
  }
}
```

---

# 16. Verifier Agent

Verifier 是：

```text
Acceptance Authority
```

Verifier必须独立于 Worker。

Verifier输入：

```text
Original User Intent
Goal
Task
TaskNode
Success Criteria
Current Plan
Worker Submission
Artifacts
Relevant Events
External Context
Tools
```

Verifier自主决定：

```text
如何验证
```

不是 MainWork 预先写死 Verification Method。

---

# 17. Verifier 能力

Verifier 可以：

```text
检查输出
读取文件
执行代码
执行测试
浏览网页
调用 API
查询外部状态
检查数据库
重新执行部分动作
比较 Artifact
查证来源
进行 semantic reasoning
```

核心原则：

```text
Verifier is the decision maker.
Tools are evidence providers.
```

---

# 18. Verification Result

统一输出：

```yaml
Verification:
  verification_id:

  task_id:
  node_id:

  verifier_id:

  decision:
    PASS
    FAIL
    UNCERTAIN

  confidence:

  reason:

  evidence:

  feedback:

  contract_insufficient:

  created_at:
```

---

# 19. Verification Loop

```text
Worker
↓
SUBMIT
↓
Verifier
├── PASS
│
├── FAIL
│    ↓
│  Feedback
│    ↓
│  Retry
│
└── UNCERTAIN
     ↓
 Gather More Evidence
```

Retry 必须携带：

```text
Original Task
Previous Output
Verifier Feedback
Failure Evidence
Attempt Summary
```

不是简单重新运行。

---

# 20. MaxRuns

每个 TaskNode 定义：

```text
max_runs
```

达到 MaxRuns：

```text
MAX_RUNS_REACHED
↓
MainWork
```

MainWork必须进行：

```text
Failure Classification
```

---

# 21. Failure Classification

V1 固定以下分类：

```text
WORKER_FAILURE

PLAN_FAILURE

DEPENDENCY_FAILURE

TOOL_FAILURE

ENVIRONMENT_FAILURE

VERIFICATION_AMBIGUITY

GOAL_IMPOSSIBLE

NEED_USER_INPUT
```

---

# 22. Recovery Policy

对应策略：

```text
WORKER_FAILURE
→ Retry / Replace Worker / Change Model

PLAN_FAILURE
→ Replan

DEPENDENCY_FAILURE
→ Reopen Dependency

TOOL_FAILURE
→ Retry / Fallback / Circuit Breaker

ENVIRONMENT_FAILURE
→ Block / Wait

VERIFICATION_AMBIGUITY
→ Gather More Evidence

GOAL_IMPOSSIBLE
→ Fail

NEED_USER_INPUT
→ Escalate
```

---

# 23. Dynamic Replanning

DAG 不是一次生成以后永远不变。

MainWork持续维护：

```text
Task Ledger
```

结构：

```yaml
TaskLedger:
  goal:

  current_plan_version:

  completed_nodes:

  running_nodes:

  blocked_nodes:

  failed_nodes:

  open_questions:

  unresolved_risks:

  failed_attempts:

  pending_change_requests:

  next_candidates:
```

MainWork根据 Ledger 进行：

```text
Observe
↓
Evaluate
↓
Replan
```

---

# 24. Plan Change

Worker不能直接修改 Plan。

流程：

```text
Worker
↓
PLAN_CHANGE_REQUESTED
↓
MainWork
↓
Review
├── APPROVED
└── REJECTED
```

批准：

```text
PLAN_REVISED
↓
Plan v2
```

旧 Plan 不删除。

---

# 25. Final Verification

所有 DAG Node PASS：

```text
≠ Task Completed
```

必须：

```text
ALL_REQUIRED_NODES_COMPLETED
↓
FINAL_VERIFICATION
```

Final Verifier 检查：

```text
Original User Intent
Goal
Success Criteria
All Artifacts
Cross-task Consistency
Integration
Unresolved Risk
Missing Requirement
```

只有：

```text
FINAL_VERIFICATION_PASSED
```

以后：

```text
TASK_COMPLETED
```

---

# 26. Event Sourcing

所有重要状态变化写入统一 Event Store。

不要建立：

```text
MainWork Event Tree
Worker Event Tree
Verifier Event Tree
```

底层统一：

```text
Append-only Event Stream
```

逻辑上通过关联字段形成 Graph / Tree View。

---

# 27. Event Schema

V1：

```yaml
Event:
  event_id:

  global_sequence:

  event_type:

  entity_type:
  entity_id:
  entity_version:

  actor_type:
  actor_id:

  occurred_at:
  recorded_at:

  correlation_id:
  causation_id:

  task_id:
  work_id:
  execution_id:

  payload:

  metadata:

  schema_version:
```

---

# 28. Event 分类

V1 使用：

```text
INTENT

TASK

PLAN

EXECUTION

TOOL

ARTIFACT

VERIFICATION

POLICY

SYSTEM
```

---

# 29. 典型 Event

```text
USER_REQUEST_RECEIVED

TASK_CREATED

MAIN_WORK_STARTED

PLAN_CREATED

PLAN_REVISED

TASK_NODE_CREATED

TASK_ASSIGNED

EXECUTION_STARTED

TOOL_CALLED

TOOL_COMPLETED

ARTIFACT_CREATED

SUBMISSION_CREATED

VERIFICATION_STARTED

VERIFICATION_PASSED

VERIFICATION_FAILED

VERIFICATION_UNCERTAIN

MAX_RUNS_REACHED

ESCALATION_REQUESTED

FINAL_VERIFICATION_STARTED

FINAL_VERIFICATION_PASSED

TASK_COMPLETED
```

---

# 30. Blackboard

Blackboard 是：

```text
Current Task Projection
```

包含：

```yaml
Blackboard:
  task_status:

  current_plan_version:

  dag_state:

  active_workers:

  node_status:

  retry_count:

  blockers:

  artifacts:

  verification_status:

  pending_requests:

  unresolved_risks:

  escalation_state:
```

Blackboard：

```text
所有 Agent 可读
```

但不允许 Agent 随意写。

更新方式：

```text
Event
↓
Projection Engine
↓
Blackboard
```

---

# 31. Artifact Store

不要把大内容直接塞 Event Store。

Artifact包括：

```text
report
file
code
patch
dataset
browser snapshot
tool result
test report
image
research result
```

Event只记录：

```yaml
artifact_id:
hash:
uri:
type:
created_by:
```

---

# 32. Command / Event 分离

Command：

```text
请求做什么
```

Event：

```text
真正发生了什么
```

例如：

```text
SEND_EMAIL
```

是 Command。

只有实际成功后：

```text
EMAIL_SENT
```

才是 Event。

标准流程：

```text
Intent
↓
Command
↓
Policy Check
↓
Execution
↓
Event
```

---

# 33. Policy Engine

Policy Engine 和 Verifier 分开。

Verifier回答：

```text
做成功了吗？
```

Policy Engine回答：

```text
允许做吗？
```

执行流程：

```text
Agent
↓
Action Proposal
↓
Policy Engine
├── ALLOW
├── DENY
└── REQUIRE_APPROVAL
```

---

# 34. Policy 类型

V1 至少支持：

```text
Permission Policy

Tool Policy

Cost Policy

Resource Policy

Security Policy

Irreversible Action Policy

User Approval Policy
```

---

# 35. Side Effect Classification

TaskNode：

```text
READ_ONLY

REVERSIBLE

COMPENSATABLE

IRREVERSIBLE
```

IRREVERSIBLE：

```text
必须经过更严格 Policy
```

必要时：

```text
REQUIRE_USER_APPROVAL
```

---

# 36. Saga / Compensation

对于 COMPENSATABLE：

```yaml
TaskNode:
  execute:
  compensate:
```

例如：

```text
订酒店
↓
后续任务失败
↓
取消酒店
```

Compensation 本身也是 Execution。

必须：

```text
记录 Event
允许失败
允许 Retry
```

---

# 37. Idempotency

所有有外部副作用的 Execution：

```text
必须有 idempotency_key
```

例如：

```text
taskA-node5-execution3
```

Runtime 执行前检查：

```text
是否已经执行成功？
```

如果是：

```text
直接返回已有 Result
```

---

# 38. Lease

Task assignment 不是永久 ownership。

Worker得到的是：

```text
Execution Lease
```

例如：

```yaml
Lease:
  lease_id:
  node_id:
  worker_id:

  fencing_token:

  acquired_at:
  expires_at:

  heartbeat_at:

  status:
```

Worker必须续租。

---

# 39. Fencing Token

每次 Lease：

```text
token++
```

例如：

```text
Worker A:
token = 18
```

Lease失效。

Worker B接管：

```text
token = 19
```

Worker A复活以后提交：

```text
token 18
```

Runtime：

```text
REJECT
```

这样防止 Zombie Worker。

---

# 40. Supervision

MainWork 是 SubWorker 的 Supervisor。

但 Runtime 是 MainWork 的 Supervisor。

结构：

```text
Runtime Supervisor
↓
MainWork A
├── Worker A1
├── Worker A2
└── Verifier
```

Worker crash：

```text
Runtime
↓
MainWork决定恢复策略
```

MainWork crash：

```text
Runtime
↓
根据 Event + Blackboard 恢复 MainWork
```

---

# 41. Durable Execution

所有关键执行状态必须持久化。

MainWork 不允许只依赖：

```text
LLM Context Window
```

恢复时：

```text
Event Store
+
Snapshot
+
Blackboard
+
Artifacts
+
Task Ledger
↓
Rebuild Context
↓
Resume
```

---

# 42. Snapshot

为了避免几十年后 replay 数十亿 Events：

```text
定期 Snapshot
```

例如：

```text
每 1000 events
或
Task checkpoint
```

恢复：

```text
Latest Snapshot
+
New Events
```

---

# 43. Retry Policy

Retry 需要：

```text
retry_count

retry_reason

backoff

previous_failure
```

支持：

```text
Fixed

Linear

Exponential Backoff
```

---

# 44. Circuit Breaker

用于持续失败的 Tool / API。

状态：

```text
CLOSED
OPEN
HALF_OPEN
```

例如：

```text
Tool连续失败
↓
OPEN
↓
停止调用
↓
等待
↓
HALF_OPEN
↓
测试
```

---

# 45. Resource Budget

每个 Task / MainWork 可以设置：

```yaml
ResourceBudget:
  max_workers:
  max_tokens:
  max_tool_calls:
  max_runtime:
  max_cost:
  max_concurrent_execution:
```

达到预算：

```text
RESOURCE_LIMIT_REACHED
↓
MainWork
```

MainWork：

```text
Replan
Reduce Scope
Escalate
```

---

# 46. Bulkhead Isolation

每个 Task 有独立资源隔离。

例如：

```text
Task A异常
```

不能无限占用：

```text
Worker
Token
CPU
GPU
API
Memory
```

影响其他 Task。

---

# 47. Trace

Event Store 不保存所有 Debug 细节。

Trace Store 保存：

```text
LLM call
tool latency
token usage
network timing
stack trace
model response
execution spans
```

区别：

```text
Event Store
= Business Truth

Trace Store
= Diagnostics
```

---

# 48. Memory

Memory 不是真相。

Memory 是：

```text
Derived Knowledge
```

来源：

```text
Events
Artifacts
User History
Task Outcomes
```

经过 Memory Builder：

```text
Facts
Preferences
Patterns
Knowledge
```

Event Store永远优先于 Memory。

---

# 49. Eval System

系统必须长期评估：

```text
Worker success rate

Verifier disagreement rate

Average retry

Average cost

Average latency

Failure category

Plan revision rate

Model performance

Prompt version performance
```

例如：

```text
Worker v3:
pass rate = 78%

Worker v4:
pass rate = 91%
```

未来 Scheduler可以优先选择：

```text
Worker v4
```

---

# 50. 一个 TaskA 的完整生命周期

```text
User
↓
USER_REQUEST_RECEIVED

Main Agent
↓
TASK_CREATED

Runtime
↓
MainWorkA Started

MainWorkA
↓
HTN Decomposition

↓
PLAN_CREATED v1

↓
DAG

Scheduler
↓
查找 READY Nodes

↓
Acquire Lease

↓
Spawn Worker

Worker
↓
EXECUTION_STARTED

↓
Tool / Reasoning

↓
ARTIFACT_CREATED

↓
SUBMISSION_CREATED

Verifier
↓
VERIFICATION_STARTED

├── PASS
│
│   ↓
│ NODE_COMPLETED
│
├── FAIL
│   ↓
│ Feedback
│   ↓
│ Retry
│
└── UNCERTAIN
    ↓
  More Evidence
```

如果：

```text
MaxRuns
```

则：

```text
MAX_RUNS_REACHED
↓
MainWork
↓
Failure Classification
↓
Retry / Replan / Reassign / Escalate
```

如果Worker发现 DAG 问题：

```text
PLAN_CHANGE_REQUESTED
↓
MainWork
↓
PLAN_REVISED
↓
Plan v2
```

最终：

```text
ALL_REQUIRED_NODES_COMPLETED
↓
FINAL_VERIFICATION_STARTED
↓
PASS
↓
TASK_COMPLETED
↓
Main Agent
↓
User
```

---

# 51. V1 推荐模块

真正实现时建议只做这些模块：

```text
TaskService

PlanService

Scheduler

ExecutionRuntime

WorkerManager

VerifierManager

EventStore

ProjectionEngine

Blackboard

ArtifactStore

PolicyEngine

LeaseManager

RecoveryManager

TraceService
```

不要一开始拆成几十个微服务。

可以先做 Modular Monolith。

---

# 52. V1 数据库建议

初期：

```text
PostgreSQL
```

表：

```text
goals

tasks

plans

task_nodes

works

executions

verifications

artifacts

events

snapshots

leases

blackboard_projection
```

Object Storage：

```text
MinIO / S3
```

保存大 Artifact。

---

# 53. V1 不做什么

这是控制复杂度非常重要的一点。

V1 不做：

```text
Agent自由Group Chat

无限Agent自治

复杂Swarm

Agent社会关系

多层递归Supervisor

自动生成无限Skill

完全自治Memory修改

所有东西都LLM化

复杂分布式微服务
```

这些以后需要再加。

---

# 54. V1 最核心的运行闭环

最终整个系统只需要记住这条：

```text
Goal
↓
Task
↓
MainWork
↓
HTN
↓
Plan / DAG
↓
Schedule
↓
Lease
↓
Worker
↓
Artifact
↓
Verifier
↓
PASS / FAIL / UNCERTAIN
↓
Retry / Replan / Escalate
↓
Final Verification
↓
Complete
```

整个过程中：

```text
Command
↓
Policy
↓
Runtime
↓
Event
↓
Projection
```

不断运行。

---

# 55. 最终系统定义

这个系统不应该被定义成：

```text
Multi-Agent System
```

更准确的定义是：

> **Reliable Agent Task Runtime**

或者：

> **Durable Agent Task Operating System**

它的本质：

```text
LLM Intelligence
+
Workflow Orchestration
+
Distributed Systems Reliability
+
Event-Sourced State
+
Independent Verification
```

Agent 只是其中的 reasoning / execution component。

系统真正长期存在的是：

```text
Goal
Task
Plan
Event
Artifact
State
Policy
History
```

这就是 V1 应该固定下来的核心。