# Task 13 生产接线复核与第一阶段修复

> 日期：2026-07-26
> 状态：生产编排与恢复修复已完成自动化验证；真实 provider progression 被 HTTP 402 阻断
> 生产提交锚点：`e8290719`

## 为什么重新打开

Task 15 的真实主消息页 S-1 发现，既有 Task 13 自动化证明了 Store、Runtime、Candidate、
Evaluation、Activation 等组件本身，却没有证明普通产品消息真的进入成长链。旧生产路径仍
调用普通 `append_message()`，没有消费 `companion_ingress_outbox`，也没有把 native
terminal delivery 注册到 Product Harness。

## 本阶段生产修复

- 主消息改用 `append_user_message_with_growth_outbox()`。
- `CompanionIngressOutboxDispatcher` 读取已提交消息、校验 SHA-256、写
  `MESSAGE_INGRESS`，并为 blocking correction 幂等创建零工具 reflection job。
- Product Harness 支持 native delivery registrations，并注册
  `GrowthTerminalDeliveryContributor` / `GrowthTerminalDeliverySink`。
- contributor 跳过 reflection/evaluation 内部 Run；delegated task 只有显式
  `capture_growth` 才采集，避免递归成长。
- dispatcher 进入 `ServiceContext` 受控白名单；shutdown 会取消并回收异步 drain task。

## 自动化证据

命令：

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_context.py `
  backend/tests/companion/test_growth_signals.py `
  backend/tests/companion/test_growth_delivery.py `
  backend/tests/test_memory_companion_projection.py -q
```

结果：`56 passed`。

此前相邻生产组合回归：workflow execution seams 与 legacy authority absence
`36 passed`；growth signal/delivery/session projection `34 passed`；changed production
files `py_compile` 通过。

## 真人主消息页证据

- launch：`s1/cg-s1s8-main-l19`
- backend：18100
- Vite：15173
- 日志确认：
  `Dev python=F:\projects\deskpet\backend\.venv\Scripts\python.exe`
  与 `backend_dir=F:\projects\deskpet\backend`
- Computer Use 真点击输入并发送：
  `以后每日总结只保留最重要的两件事，并为每件附一个下一步；不要单列待跟进。`
- UI 显示用户气泡，真实 provider 返回前台回复。
- 只读数据库核对：
  - SessionDB user message `id=25`
  - outbox `message:default:25`：`delivered`、attempt=1
  - `message_ingress` GrowthEvent：`explicit_user_correction`
  - 同 root `run.final` GrowthEvent：`root_terminal_outcome`
  - reflection job：
    `reflection:growth_f03e40c219d918597c35d2babc580723`

## 后续生产编排与恢复修复

- `ReflectionPostprocessor` 校验 typed result、owner/source/target/hash，不接受普通文本直接
  形成 activation。
- `GrowthProductionPipeline` 已在 production composition root 接通
  reflection → candidate build → immutable composition → frozen evaluation →
  RiskPolicy/ActivationDispatcher；后台任务继续复用同一 RunKernel/Driver。
- Companion background Run 使用零工具 frozen context；provider handoff unknown 稳定
  fail closed，失败进入 durable retry/failure-replan。
- scheduler 按 exact `build_id` claim，避免一个 job 误认领更旧过期 build；startup manifest
  读取允许 helper 原子替换期间的共享读。
- Companion 全量后续复核为 `588 passed`；生产 pipeline/composition/scheduler 聚焦回归
  也已通过。

## 仍未完成的真实价值边界

2026-07-26 l44 在真实主消息页发送两条新纠正，两项都形成 committed GrowthEvent 并进入
production reflection Run，但真实 Relay 连续返回 HTTP 402
`account balance insufficient`。两个 job 都在 attempt 3 后
`failed/background_run_failed`，因此这两个事件没有产生 candidate/evaluation/activation/
notification。此结果证明生产入口、错误吸收和有界重试，不证明 S-1 的正向价值链；Task 15
继续 BLOCKED，不能用 fake provider、协议注入或旧诊断 candidate 冒充。
