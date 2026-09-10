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
