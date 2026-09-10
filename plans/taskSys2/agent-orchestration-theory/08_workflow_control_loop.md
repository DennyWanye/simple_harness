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
