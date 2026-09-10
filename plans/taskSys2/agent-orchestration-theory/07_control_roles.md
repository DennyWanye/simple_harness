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
