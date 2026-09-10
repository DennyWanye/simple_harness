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
