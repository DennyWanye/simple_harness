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
