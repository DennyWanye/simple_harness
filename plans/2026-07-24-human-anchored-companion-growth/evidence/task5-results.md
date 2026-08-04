# Task 5 验证结果：可关闭、可恢复的 CompanionRuntime

> 日期：2026-07-25
> 结论：Clock、Store scheduler 原语、dormant Runtime、identity lifecycle 与 shutdown 自动化门
> 通过；生产 authority 仍为 legacy，Companion scheduler 为 0。

## 实现事实

- 新增 `ClockPort/SystemClock/DevFrozenClock`。只有非 frozen build 且 Tauri 进程同时设置
  `DESKPET_DEV_MODE=1` 和严格 absolute UTC `DESKPET_E2E_CLOCK_UTC` 才选择冻结时钟；
  非 DEV、打包和非法值不能激活。没有 chat/WS/tool tick API。
- 新增单 scheduler `CompanionRuntime`，生命周期为
  `recover/start/pause/switch/close/diagnostics`。child 数有界，pause 先停止新 claim，再
  drain/cancel，取消后的 safe reflection lease 可恢复。
- `ForegroundActivityGate` 每次 claim 前从 durable execution UoW 重算未终态 root，
  `root_run_id` 去重，background venue 排除。缺 port、字段异常或读取失败均 fail closed。
- `CompanionStore.claim_next_job_with_budget()` 在同一个 `BEGIN IMMEDIATE` 内计算 budget
  window 并 claim。leased 计 reservation，succeeded/failed 计 actual usage；并发 claim
  不能超预算。
- lease recovery 只允许 typed policy 中的 reflection 重试；过期 reminder 标 expired，
  delegated/external/unknown job 标 failed/not-safely-retryable，禁止盲重发。
- canonical owner key 统一为 `companion:<profile>:<generation>`。切号在等待 cleanup 前
  先 unready；同 owner 重绑不暂停；新进程首次 bind 会调用被注入 lifecycle 的 recover。
- `main.py` 注册 process-owned `companion_clock/companion_runtime`，但不把 Runtime 注入
  当前 profile coordinator；Runtime 的 authority 默认 false，所以 Task 13 前 recovery writer
  和 scheduler 都不会抢 production authority。
- legacy memory reflection 仍按原配置运行，只改为 lifespan-owned tracked task。shutdown
  顺序为 Runtime/legacy reflection → Harness → Capability/Workflow launcher → execution UoW。

## 计划冲突的实施裁决

- Task 5 计划同时要求“生产 Runtime 到 Task 13 才启动”和“本 Task 完成首次 bind 后
  recover/start”。实现选择安全边界：本 Task 只注册 dormant Runtime，Task 13 在唯一
  authority gate 内注入 coordinator、补 execution port 并启动。
- 中文“每周五下午”解析和 reminder occurrence exactly-once 依赖 Task 11 的
  `companion/reminders.py` 与 occurrence claim/settle，本 Task 只提供 Clock seam；相关
  验收保留到 Task 11，未以 fake occurrence 冒充完成。
- 当前 execution ledger 没有 durable activity cursor。本 Task 明确采用每次 claim 前一致
  重算；不声称实现了不存在的增量订阅。Task 13 接线前缺 execution port 时保守视为 busy。

## 自动化证据

```text
python -m pytest backend/tests/companion/test_dev_clock_seam.py \
  backend/tests/companion/test_runtime_scheduler.py \
  backend/tests/companion/test_runtime_shutdown.py \
  backend/tests/companion/test_profiles.py \
  backend/tests/companion/test_growth_signals.py \
  backend/tests/companion/test_store_transactions.py \
  backend/tests/companion/test_window_control_credentials.py -q
59 passed in 9.79s

python -m pytest backend/tests/companion backend/tests/test_context.py \
  backend/tests/harness_simplification/test_runtime_activation.py \
  backend/tests/harness_simplification/test_execution_write_lane.py \
  backend/tests/harness_simplification/test_product_venue_chain.py \
  backend/tests/test_agent_harness_docs.py -q
182 passed in 29.07s
```

Python compile 与 `git diff --check` 通过。

## 真人 E2E 边界

Task 5 不改变主消息页可见行为：生产 Runtime authority closed、scheduler=0，legacy
reflection 保持原功能。真实启动与后台行为只有 Task 13 cutover 后才存在可点击验收目标；
Task 14/15 必须在真实源码 Tauri 主消息页验证 foreground 优先、quiet hours、暂停/恢复、
切号和 shutdown，不得以协议注入替代。

## 进程与端口清理

- 最终 Task 5 pytest/py_compile matching process survivor=0。
- `8100/5173` listener=0。
- 一次额外 main-import 合并回归在 120 秒超时；按精确命令行、PID 和 creation time 验证后
  停止 PID `20280`（child）与 `10620`（parent），survivor=0，释放 private memory
  `1,080,774,656` bytes。拆分后的 venue/docs 测试分别 `12 passed`、`1 passed`。
