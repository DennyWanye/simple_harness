# F06 决策：provider 传输超时后 Run 停摆 —— 根因与最小修复

> **独立子代理分析，主代理复核后执行。** 只读分析，未改代码、未运行原生应用。
> 对象：Host `simple_harness` main 030222cc + Harness SDK 0.7.10（源 031fdc68）。
> 证据：`.local-test-evidence/2026-09-07/native-a8734fbf/{primary-ui-ouryr2xf,primary-ui-h1m07zwa}/native.log`、
> `primary-ui-00vc5gul/userdata/data/state.db`、`…/data/simple-harness-sdk/execution-v6.sqlite3`（SDK 执行库真实路径；`sdk-product-state.db` 只有 capability/saga 表，不含 provider 记录）。

## 0. 一句话结论

这不是 await 挂死，而是**双方都在"按契约等对方"**：Harness 把超时后的 provider 调用记为 UNKNOWN、把 Run 置为 `waiting` 并挂一个 provider wait-blocker，等 Host 通过 `ProviderReconciliationPort` 给出可判定结论；Host 生产接线给的是 `_NoopReconciliation`（永远 `STILL_UNKNOWN`），且**没有任何代码路径调用 `reconcile_incomplete`**，前台循环看到 SDK `waiting` 就记 `BOUND_WAITING` 后退出、只靠租约心跳每秒轮询"是否终态"。blocker 永不解决 → SDK 永不重驱 → Host 永远"无事可做"。重启后 `recover()` 只捞 `running/queued/created` 的 Run，`waiting` 不在其列，所以只打一行 `reconcile.recovered` 就结束。**唯一推荐：Host 侧修，不需要 Harness 0.7.11。**

## 1. 停摆现场（数据库）

| 库 / 表 | 关键行 |
|---|---|
| SDK `runs` | `product-sdk-66e36c6a…` `state=waiting version=2 updated_at=1788794669.27`（=15:24:29Z，与 `reconcile.unknown_settled` 同秒） |
| SDK `provider_invocations` | `1d50d16e…` request `…:provider-turn:3` `state=unknown handoff_attempt=1 rehandoff_count=0 error_code=provider_error_after_handoff version=3` |
| SDK `run_wait_blockers` | `7e7f5576…` `kind=provider ledger_identity=1d50d16e… handoff_attempt=1 resolution_id=NULL wake_consumed=0` |
| SDK `reconciliation_resolutions` / `wait_activation_receipts` | **空** |
| Host `foreground_run_heads` | `52ca9c27…` `current_state=RUNNING`，重启后 `owner=deskpet-foreground:86616 generation=2`，心跳续到 1788796052 |
| Host `foreground_execution_reconciliations` | 同一 host_run 两行 `BOUND_WAITING`：g1 @1788794669.29（停摆进程）、g2 @1788795652.16（重启进程） |
| Host `foreground_lease_receipts` | g1 heartbeat #9/#10/#11 间隔 100s（lease 300s/3），g2 `reclaim` + heartbeat #1 —— 证明前台循环退出后只剩租约保活在跑 |
| Host `foreground_execution_start_observations` | g2 `QUERY_FOUND sdk-run:…:v2`（重启只查到 waiting 的既有 Run，未再 start） |

## 2. 问题 1：Harness 期望 Host 做什么；Host 停在哪一步；为什么零事件

### 2.1 Harness 侧链路（期望）

1. `httpx.TimeoutException` → `ProviderTimeoutError`（`retryable=True`）：SDK `src/simple_harness/providers/openai_compatible.py:151-152`，`providers/errors.py:62-66`。Host 包装层只记日志后原样 `raise`：`backend/deskpet/sdk_adapters/provider.py:745-762`（`stage=transport_timeout` 就是这里算出来的，`provider.py:130-131`）。
2. `ProviderInvocationCoordinator.invoke`：`ProviderTimeoutError` **不在** `_DEFINITE_PROVIDER_FAILURES`（`execution/dispatch.py:217-224`，只有 auth/payment/protocol/ratelimit/rejected/server），落入 `except BaseException`（`dispatch.py:576-578`）→ `_settle_unknown(handed_off, "provider_error_after_handoff")`（`dispatch.py:668-690`，打 `reconcile.unknown_settled`、发 `provider_attempt.degraded`）→ 抛 `ProviderInvocationUnknownError`（`retryable=False`，`dispatch.py:187-195`）。**注意 `retryable=True` 没有进账本**，账本只有 `provider_error_after_handoff`。
3. ReAct driver 捕获后返回 `DriverResult(WAITING, {"raw_failures":[{"error_code":"provider_outcome_unknown"}]}, wait_blocker=WaitBlockerSpec(PROVIDER, invocation_id, handoff_attempt=1, version=3))`：`runtime/drivers/react.py:249-262`。
4. kernel `_drive` 提交 `commit_runtime_wait_with_blocker` → `runs.state='waiting'` + `run_wait_blockers` 一行：`runtime/kernel.py:2891-2909`。Run 离开 live 索引。
5. **Harness 期望的下一步**：有人调用 `ProviderInvocationCoordinator.reconcile_incomplete(provider_reconciliation=port)`（`dispatch.py:692-769`）——对每个 UNKNOWN 记录调 `port.observe(record)`：
   - `COMPLETED`（带 `ProviderResponse`）→ 账本改 succeeded、blocker 解决（`execution/sqlite/uow.py:6651-6795`，blocker 更新在 6782-6790）；
   - `CONFIRMED_NOT_STARTED` → 只写 resolution + 解决 blocker（`uow.py:6755-6790`）；
   - `STILL_UNKNOWN` → 什么都不做（`dispatch.py:711-712`）。
6. blocker 一旦有 `resolution_id`，kernel `_wake_drain`（每 ≤50ms，`kernel.py:2059-2066`）→ `_drain_resolved_waits_once`（`kernel.py:2415-2445`）→ `consume_resolved_wait_and_claim_activation` 把 `runs.state` 改回 `running`（`uow.py:2014-2110`，UPDATE 在 2101-2106）→ `_schedule(_drive)` → driver 从 checkpoint 重放 → 再次 `invoke` 同一 `request_id`：`dispatch.py:519-545` 读到 UNKNOWN + `CONFIRMED_NOT_STARTED` → `reauthorize_provider_not_started`（`uow.py:6797-6868`：state 回 claimed，`rehandoff_count+1`，**只允许一次**，CAS 条件 `rehandoff_count=0` 在 6851）→ 重新 hand off（`handoff_attempt=2`，`execution/provider_invocations.py:630`）→ 真正重发。
7. SDK 自己在两处调用 Host 的 `RuntimeReconciliationPort.reconcile()`：启动 `_start_once` 第一步（`kernel.py:1835-1842`，紧接 `recover` 与 `_drain_resolved_waits_once`）和公开的 `runtime.reconcile()`（`kernel.py:1967-1971`，之后同样 `_drain_resolved_waits_once` + `recover`）。**SDK 内核本身不调用 `reconcile_incomplete`**（grep：`src/` 只有定义，调用全在 tests），它期望 Host 在 `reconcile()` 端口里做——SDK conformance 测试的标准接线正是如此：`tests/conformance/test_full_runtime_seam.py:433-437`（`reconcile_provider` step 调 `provider_coordinator.reconcile_incomplete(provider_reconciliation=…)`），`:689-760` 验证重开后 provider unknown 被观察一次且不重放。

**回答"重试同一 attempt 还是新 attempt"**：同一 `invocation_id` / 同一 `request_id`，由 `CONFIRMED_NOT_STARTED` 授权**一次**重新 hand off（`handoff_attempt` 1→2，`rehandoff_count` 0→1）；不是新 attempt、不是 Host 自己重试，而是 Host 先给"可判定结论"再由 driver 重放。

### 2.2 Host 侧停在哪

| 环节 | 位置 | 实际行为 |
|---|---|---|
| 生产 RuntimePorts | `backend/main.py:8834`、`:8901` | `reconciliation=_NoopReconciliation()`、`provider_reconciliation=_NoopReconciliation()`；`_NoopReconciliation.reconcile()` 直接 `return None`（`main.py:8628-8630`） |
| 已有但未接线的适配器 | `backend/deskpet/sdk_adapters/reconciliation.py:52-67`（`ProductProviderReconciliationAdapter`，默认 `STILL_UNKNOWN`）、`:70-77`（`ProductStartupReconciliationAdapter`） | 全仓无生产调用（只有 `tests/sdk_adapters/test_product_host_ports.py:1301-1306` 断言"默认 unknown"） |
| `reconcile_incomplete` 调用点 | 全仓 grep | **零**。`SdkRuntimeIngress.reconcile()`（`ingress.py:406-409`）、`ProductSdkRuntimeStack.reconcile()`（`composition.py:459-460`）也无人调用 |
| 前台观察 | `backend/deskpet/execution/foreground_runtime.py:1238-1268` `_finish_bound` | `_observe_with_heartbeats` → `SqliteSdkTerminalObserver.observe`（`:1553-1575`）→ `ingress.wait_idle` → kernel `_live.wait`（`kernel.py:2509-2520`）在 Run 离开 live 时立刻返回 → 查到 `waiting` → `resolve_host_terminal` 返回 None（`:300-323`）→ 记 `BOUND_WAITING` 后 **return** |
| 驱动循环 | `:604-684` `_drive_once`；`:570-603` `_run_driver` | `_drive_once` 末尾按 Host head 判进展：`RUNNING` 不在 `{COMPLETED,FAILED,STOPPED,CANCELLED}` → 返回 False → `_run_driver` 判"无进展"退出、`_driver=None` |
| 租约保活 | `:732-771` `_maintain_lease` | 每 ≤1s 查一次 SDK 状态，**只有** `completed/failed/cancelled` 或有 pending 控制信号才 `after_enqueue`（`:754-761`）；`waiting` 不算 → 永远只续租（每 100s 一次 heartbeat，与 DB 吻合） |

**为什么零事件**：没有任何协程在 await provider 或 SDK；SDK 的 Run 不在 live 集合，Host 驱动任务已退出，剩下的只有一个每秒读一次快照的租约保活循环，它认为"没到终态，无事可做"。这是**设计上的等待**，不是挂起；等待对象（blocker resolution）永远不会出现。

## 3. 问题 2：重启后 `reconcile.recovered` 做了什么、为何不续推

`kernel._start_once`（`kernel.py:1835-1842`）顺序：`_ports.reconciliation.reconcile()`（Host Noop）→ `recover(_startup=True)` → `_drain_resolved_waits_once()`。

- `recover()` 只打 `reconcile.recovered`（日志格式不带 extra，所以看不到 roots/children 计数，`kernel.py:1878-1885`）然后遍历 `list_recoverable_root_runs()`——SQL 只选 `state IN ('created','queued','running','cancel_requested')`（`uow.py:6934-6942`），**`waiting` 被排除**（这是对的：waiting 的 Run 靠 blocker/continuation 唤醒，不能盲目重驱）。所以本次 roots=0，没有 `recovery.observed_state`。
- `_drain_resolved_waits_once()` 查 `run_wait_blockers WHERE resolution_id IS NOT NULL`（`uow.py:1988-2012`），本例为 NULL → 空。
- Host 侧：`_drive_once` 看到快照 owner 不同 → `reclaim_expired`（g2）→ `_drive_claimed`（`foreground_runtime.py:773-826`）走"已绑定 SDK Run"分支：`ingress.query` 查到 waiting 记录 → 记 `QUERY_FOUND` → 因 head 不是 CLAIMED 不再 `record_sdk_started` → `_finish_bound` → 再次 `BOUND_WAITING`（g2）→ 同第 2 节退出。

所以重启只是把同一个死等换了个 generation 再等一次。

## 4. 唯一推荐的最小修复（Host 侧；不发 Harness 0.7.11）

原则：SDK 契约完整、conformance 测试已示范；Host 缺的是 `consumer_reconciles` 那一半。对 OpenAI 兼容 chat completion，Host **无法**向服务端查询"那次请求有没有完成"（无请求级查询 API），所以 `COMPLETED` 不可达；可行结论只有 `CONFIRMED_NOT_STARTED`（授权重发一次）。产品语义："客户端没有收到任何响应，该次调用对用户不产生可消费效果"，可接受的代价是最坏情况服务端计费一次未被使用的补全。

### 4.1 改动点（3 个文件，约 90 行生产代码）

**A. `backend/deskpet/sdk_adapters/reconciliation.py`（+~35 行）**
1. 新增策略 `provider_unknown_retry_once(invocation)`：`rehandoff_count == 0` → `ProviderReconciliationObservation(CONFIRMED_NOT_STARTED, f"product-policy:provider-retry-once:{invocation_id}:a{handoff_attempt}")`；否则 `STILL_UNKNOWN`（evidence `…:retry-exhausted:…`）并把 `invocation.run_id` 记入适配器的 `exhausted_runs`。用 `ProductProviderReconciliationAdapter(observer=provider_unknown_retry_once)` 承载（不新造类）。
2. 新增 `ProductRuntimeReconciliation(ports_getter)`（或直接用现成 `ProductStartupReconciliationAdapter` 包一个 step）：`reconcile()` = `await ports_getter().provider.reconcile_incomplete(provider_reconciliation=<上面的适配器>)`。`ports_getter` 沿用 `main.py:8842-8852` `_ProductionToolCatalogProxy` 的惰性取 `production_ports["ports"]` 模式（因为 `ProductionRuntimeConfig` 构造时 ports 还没建）。

**B. `backend/main.py:8834,8901`（改 2 行 + 约 10 行 proxy）**
`reconciliation=<ProductRuntimeReconciliation proxy>`，`provider_reconciliation=<retry-once 适配器>`（两处都改，保持 `RuntimePorts` 与 `ProductionRuntimeConfig` 一致；SDK `production.py:196` 只用 config 里的那份，但 `main.py:8834` 那份是 `_production_ports_for` 缓存里的，`ProductRuntimeReconciliation` 从它取 `provider`）。
效果：**重启路径立刻修好**——`_start_once` 第一步就会解决 blocker，随后 `_drain_resolved_waits_once` 把 Run 改回 running 并重驱。

**C. `backend/deskpet/execution/foreground_runtime.py:1249-1268` `_finish_bound`（+~25 行）**
把"记 `BOUND_WAITING` 后 return"改成循环：
```
while True:
    terminal = await self._observe_with_heartbeats(...)
    if terminal is not None: break
    record_reconciliation(BOUND_WAITING)          # 现有代码
    await self._ingress.reconcile()               # kernel.reconcile(): Host step → reconcile_incomplete → _drain_resolved_waits_once（同步重驱）
    record = self._ingress.query(sdk_run_id)
    if state(record) != "waiting": 
        self._record_audit("foreground.runtime.provider_reconciled", host_run_id=…, sdk_run_id=…)
        continue                                   # Run 已回到 live，再进 wait_idle
    if sdk_run_id in provider_reconciliation.exhausted_runs:
        self._record_audit("foreground.runtime.provider_unknown_exhausted", …)
        await self._ingress.cancel(sdk_run_id)     # 二次未知：SDK 只允许一次 rehandoff（uow.py:6851、provider_invocations.py:556）
        continue                                   # 下一轮 observe 拿到 cancelled → Host CANCELLED 终态
    self._notify_state_changed(); return           # 真正的权限 WAITING 等，维持现状
```
`kernel.reconcile()` 在 `_drain_resolved_waits_once` 里已同步 `_schedule`，所以 `continue` 后 `wait_idle` 会真正阻塞到下一次 idle；即使竞争失败（Run 未 live），循环也只会再记一次 `BOUND_WAITING` 后按原路径退出，随后 `_maintain_lease` 的每秒轮询（`:754-761`）会在终态时 `after_enqueue`，无死锁。
`_ingress.reconcile()`（`ingress.py:406`）不检查 `_accepting`，关闭入口期间也可调用。
运行时 `reconcile()` 顺带 `recover()` 会多打一行 `reconcile.recovered`，可接受。

**不改 Harness 的理由**：(1) 把 `ProviderTimeoutError` 加进 `_DEFINITE_PROVIDER_FAILURES` 会让 Run 直接 FAILED、无重试，且违背 SDK "发出后即未知"的账本原则；(2) SDK 已提供完整路径与测试（`test_h13_provider_recovery.py:200-240` 证明 not-started → 第二次 hand off）。可选后续（非 r12 必需，记 FOLLOWUPS）：Harness 在 `_settle_unknown` 的 `error_code` 里保留原始 `exc.code`（`dispatch.py:576-578`），让 Host 策略能区分超时与其他异常；以及允许 N 次 rehandoff。

### 4.2 可复现单测设计（Host，`tests/execution/test_primary_foreground_runtime.py` 体系：真 SDK ReAct/SQLite + 假 provider）

前提：`build()`（`test_primary_foreground_runtime.py:92-278`）里 `ports()` 现在写死 `reconciliation=noop, provider_reconciliation=noop`（`:188`），需改为使用 A 中的生产适配器（这样测试与生产同一接线）。

**T1 `test_primary_provider_timeout_once_retries_same_request_and_completes`**
```
class TimeoutOnceProvider(Provider):
    async def invoke(self, request, *, cancel):
        self.requests.append(request)
        if len(self.requests) == 1:
            raise ProviderTimeoutError()          # simple_harness.providers.errors
        return await super().invoke(request, cancel=cancel)
```
步骤：`enqueue_turn` → `assert await asyncio.wait_for(runtime._drive_once(), 15)`（一次 drive 内走完）。
断言：
- `len(provider.requests) == 2` 且 `requests[0].request_id == requests[1].request_id`（同一 `…:provider-turn:1`）；
- SDK 库 `provider_invocations`：1 行 `state=succeeded handoff_attempt=2 rehandoff_count=1`；`reconciliation_resolutions`：1 行 `outcome=confirmed_not_started`，`evidence_ref` 以 `product-policy:provider-retry-once:` 开头；`run_wait_blockers`：1 行 `resolution_id IS NOT NULL AND wake_consumed=1`；
- Host `foreground_run_heads.current_state == COMPLETED`；`foreground_execution_reconciliations` 按 `recorded_at` 为 `[BOUND_WAITING, BOUND_TERMINAL]`；`foreground_run_transitions` 末尾 `RUNNING→COMPLETED`；
- `PrimaryHistoryStore.read` 最后一组 `terminal_state=="COMPLETED"`、assistant 内容 `"Actual response 2"`；
- 事件序列（`caplog` 取 `simple_harness.*` + Host audit sink）：`provider_attempt.started → reconcile.unknown_settled → provider_attempt.degraded → foreground.runtime.provider_reconciled → provider_attempt.started → provider.invoked → provider_attempt.succeeded → run.completed`（用子序列断言，允许穿插 tool/memory 事件）。

**T2 `test_primary_provider_timeout_recovers_after_restart`**（覆盖第 3 节路径）
用一个可切换的策略：首次 `observe` 返回 `STILL_UNKNOWN`（模拟修复前/或注入 `fault`），`_drive_once` 返回 False、head 停在 RUNNING、SDK `waiting`；`await stack.close()`；用同一 `state.db`/SDK 库重新 `build()`（策略恢复 retry-once）；断言 stack 启动后 SDK 库 `reconciliation_resolutions` 已有 1 行（`_start_once` 第一步完成），再 `await runtime._drive_once()` → `foreground_execution_start_observations` 有 `QUERY_FOUND`，最终 head `COMPLETED`，`provider.requests` 总数 2。

**T3 `test_primary_provider_timeout_twice_settles_cancelled_not_stuck`**
`TimeoutTwiceProvider`（前两次抛 `ProviderTimeoutError`）：断言 `_drive_once` 在 15s 内返回 True，`provider.requests == 2`（不会第三次发送），SDK `provider_invocations.state=unknown rehandoff_count=1`，Host head `CANCELLED`，audit 含 `foreground.runtime.provider_unknown_exhausted`，`foreground_run_heads` 不再 RUNNING。

**T4（现有测试回归）** `tests/sdk_adapters/test_product_host_ports.py:1301` "默认 unknown"保留（无 observer 时仍 `STILL_UNKNOWN`）；新增 `test_provider_unknown_retry_once_policy` 直接断言策略函数对 `rehandoff_count=0/1` 的两种输出。

### 4.3 需要发 Harness 0.7.11 吗

**不需要。** 全部改动在 Host；SDK 0.7.10 的 `reconcile_incomplete`、`reauthorize_provider_not_started`、`_wake_drain` 已具备且有测试。r12 继续用已安装 H0.7.10。

## 5. 风险与接受项

- `CONFIRMED_NOT_STARTED` 是策略断言而非事实：极端情况服务端已完成并计费，Host 重发会二次计费；SDK 预算账本对第一次保留仍按 `record.budget_charge` 记（`dispatch.py:755-763`），不会失真。可接受。
- 一次 rehandoff 上限来自 SDK（`provider_invocations.py:556`、`uow.py:6851`）；第二次未知只能取消，用户看到 CANCELLED 而非 FAILED（`resolve_host_terminal`，`foreground_runtime.py:314-323`）。r12 不依赖此路径。
- `_finish_bound` 循环对权限 WAITING 无副作用（`reconcile_incomplete` 无 unknown 记录时返回 0，Run 仍 waiting → 走原 return）。
- 分析车道（`post_turn_invoker`）同样把 ProviderTimeout 变成 `sent_unknown`（`provider.py:576-586` 注释）；本修复通过 SDK 启动 reconcile 同样覆盖其 UNKNOWN 记录，但其 Run 生命周期由 `wait_for` 截断，暂不在本决策范围。

## 6. 执行清单（主代理）

1. A/B/C 三处改动 + 更新测试 `build()` 接线；跑 `tests/execution/test_primary_foreground_runtime.py`、`tests/execution/test_foreground_runtime.py`、`tests/sdk_adapters/test_product_host_ports.py` 保持绿。
2. 新增 T1–T4。
3. 原生 r12 重跑步 5；证据目录沿用 `.local-test-evidence/2026-09-07/native-a8734fbf/`，比对 SDK 库 `reconciliation_resolutions` 非空、Host `foreground_execution_reconciliations` 出现 `BOUND_TERMINAL`。
4. FOLLOWUPS F06 状态改为"已修（Host）"，并追加可选 Harness 后续（error_code 保真、N 次 rehandoff）。
