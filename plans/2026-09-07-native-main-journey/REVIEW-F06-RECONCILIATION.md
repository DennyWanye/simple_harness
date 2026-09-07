# F06 独立审查：provider 传输超时后 Run 停摆修复（提交 `4601610a`）

> 只读审查。工作树 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-m0623-adopt`（分支 `m0623-adopt`）。
> 对照：`plans/2026-09-07-native-main-journey/DECISION-PROVIDER-TIMEOUT-STALL.md`、Harness SDK 0.7.10
> （`simple-harness-sdk/src/simple_harness/execution/dispatch.py`、`execution/sqlite/uow.py`、`runtime/kernel.py`）。
> 运行：`tests/execution/test_primary_provider_timeout_reconciliation.py` +
> `tests/execution/test_primary_foreground_runtime.py` → **21 passed in 17.35s**。

## 0. 结论

**ACCEPT-WITH-FIXES。**

方案方向正确、与 SDK 契约吻合、T1–T4 是真 SDK + 真 SQLite 的强证据，F06 主症状（前台 Run 永久 RUNNING）确实被消除。
但把 `reconcile_incomplete` 从"进程启动的静默期步骤"改成"运行期随时可调"，引入了一个**跨 Run 的新缺陷**：
它会把**其它 Run 正在飞行中的 provider 调用**强行判成 UNKNOWN 并授权重发，产生重复物理发送与重复计费——
正是本次修复承诺要避免的代价。必须先加一道边界闸再合入。

---

## 1. 安全性（retry-once 是否严格限一次）

### 1.1 单 Run 路径：正确

* 策略 `ProviderUnknownRetryOncePolicy.__call__`（`backend/deskpet/sdk_adapters/reconciliation.py:82-113`）
  以 `rehandoff_count == 0` 为唯一放行条件；SDK 侧 `uow.reauthorize_provider_not_started` 的 CAS 也硬性要求
  `rehandoff_count = 0`（`simple-harness-sdk/src/simple_harness/execution/sqlite/uow.py:6851`）。**双保险，上限一致。**
* 重发确为**同一 request**：SDK `dispatch.py:519-545` 走的是同一 `invocation_id` / `request_id` 的
  `reauthorize` 分支，`handoff_attempt` 1→2。T1 直接断言
  `provider.requests[0].request_id == provider.requests[1].request_id` 且
  `provider_invocations == [{succeeded, handoff_attempt: 2, rehandoff_count: 1}]`。**通过。**
* evidence_ref 设计得当：`product-policy:provider-retry-once:{invocation_id}:a{attempt}` 是**确定性**字符串，
  因此重复 observe 会命中 `record_provider_reconciliation` 的 `outcome_hash` 幂等分支
  （`uow.py:6713-6719`）而不是抛 `UnitOfWorkConflict`。这一点值得记下来：evidence_ref 一旦改成带时间戳/随机值，
  重入就会炸。
* 二次未知一定收尾：`_reconcile_waiting_run`（`backend/deskpet/execution/foreground_runtime.py:1254-1316`）
  在 `exhausted_runs` 命中后 `_ingress.cancel` → 下一轮观察拿到 cancelled → Host CANCELLED。
  T3 断言 `provider.requests == 2`（没有第三次物理发送）、`runs.state == cancelled`、
  `foreground_run_heads == CANCELLED`、`reconciliation_resolutions COUNT = 1`。**通过。**

### 1.2 恢复路径 `waiting_runs_blocked_on_provider` / `restore_waiting_run`：正确

`reconciliation.py:130-146` 只收 `run.state == 'waiting'` 的 Run，`completed/cancelled/failed` 天然被排除；
`read_run(run_id)` 取 str、`str(RunId)` 即 `value`（`contracts/identity.py:30-31`），所以
`main.py:8770-8778` 的 `exclude` 去重键与之一致，不会重复进入
`main.py:8780-8825` 的授权恢复循环。`record is None` 的分支也被 `run is not None` 兜住（写法顺序略绕但无 bug）。
**无误恢复风险。**

### 1.3 ★ 必须修：运行期 `reconcile_incomplete` 会误伤其它 Run 的在途调用

`ProviderInvocationCoordinator.reconcile_incomplete` 是**全局扫库**、且对 `HANDED_OFF` 记录**无条件**
判 UNKNOWN：

```
# simple-harness-sdk/src/simple_harness/execution/dispatch.py:702-706
for record in self._uow.list_incomplete_provider_invocations():   # 全库 claimed/handed_off/unknown
    if record.state is ProviderInvocationState.CLAIMED: continue
    if record.state is ProviderInvocationState.HANDED_OFF:
        await self._settle_unknown(record, "recovered_after_handoff")   # ← 没有任何 run/lease/年龄过滤
```

`HANDED_OFF` 的语义就是"请求此刻正在飞行中"。链式后果：

1. `_settle_unknown` 以 `expected_version=handed_off.version` 成功 CAS → 记录变 UNKNOWN、version+1；
2. 该记录随即被 retry-once 策略观察到（`rehandoff_count == 0`）→ 写 `CONFIRMED_NOT_STARTED` 决议；
3. 那个 Run 里真实的 `await provider.invoke(...)` **成功返回**后，
   `settle_provider_invocation(succeeded, expected_version=handed_off.version)` 版本不匹配 →
   走 `dispatch.py:616-624`，`current.state` 已是 UNKNOWN（既非 SUCCEEDED 也非 HANDED_OFF）→
   抛 `ProviderInvocationUnknownError`，**一次完全正常的模型响应被丢弃**；
4. 该 Run 进 waiting，随后凭步骤 2 的决议重发同一 request → **重复物理发送 + 重复计费 + 重复副作用**。

修复前 `reconcile_incomplete` 全仓零调用，此路径不可达；SDK 自己也只在 `_start_once`
（`runtime/kernel.py:1838`，此时本进程无在途调用）和 conformance 的 `reconcile_provider` 步骤里用它。
本提交把它接到了**前台运行期**（`foreground_runtime.py:1272` → `main.py:7436-7440` →
`reconciliation.py:161-171`），静默期前提消失。

可达性不是理论问题：`main.py:10858-10860` 明确写着"Concurrent Sessions resolve their own immutable binding;
no global stack mutation"——同一 SDK 栈上多会话并发是既定设计；`_execute_sdk_run`（`main.py:12512`）与
`delegate_run` 子 Run（`deskpet/harness/runtime.py:475-478`）共享同一个 `ProviderInvocationCoordinator` 与 uow。
只要前台 Run A 因超时进 waiting 的那一刻，Run B 有一次在途 provider 调用，就会中招。

**必须改（择一，倾向 a）：**

* **(a) Host 侧加闸**：在 `ProductRuntimeReconciliation.reconcile()`
  （`backend/deskpet/sdk_adapters/reconciliation.py:161-171`）或
  `main.py:7436-7440` `_reconcile_incomplete_providers` 增加"运行期模式"参数：先读
  `uow.list_incomplete_provider_invocations()`，若存在 **任何 `handed_off` 记录**（尤其是
  `run_id != 目标 sdk_run_id` 的），则本次跳过并返回 0（前台按现有 `settled == 0` 分支
  维持 BOUND_WAITING，由重启/下一轮兜底），只有全库无在途 handoff 时才真正调用
  `reconcile_incomplete`；启动路径（`_start_once`）保持现状不变。
* **(b) 走 SDK**：向 Harness 提 0.7.11，给 `reconcile_incomplete` 加 `run_id=` / `invocation_id=` 作用域参数，
  Host 只对目标 Run 的 UNKNOWN 记录调和。（DECISION 文档"不需要 0.7.11"的判断在**只做启动路径**时成立，
  在**运行期调用**这一实现选择下不再成立。）

配套补一个回归测试：两个并发 SDK Run，B 在途（provider 慢返回）时 A 触发 waiting reconcile，
断言 B 的 `provider_invocations` 仍为 `succeeded / handoff_attempt=1 / rehandoff_count=0`、B 的物理发送次数为 1。

---

## 2. 与工具 continuation / dynamic 路由的互扰

* **不调 `kernel.reconcile()` 的决定是对的**：后者会附带 `recover()` + 统一 `_drain_resolved_waits_once`
  （`kernel.py:1970-1973`），确实会与前台自己的 continuation 交付路径抢重驱。
* **有界性充分**：`_PROVIDER_RECONCILE_DRAIN_POLLS=60 × 0.05s = 3s` 单轮上限，
  `_PROVIDER_RECONCILE_MAX_ROUNDS=3`（`foreground_runtime.py:301-304`）；且轮数只在
  `_reconcile_waiting_run` 返回 True 时才推进，返回 False 直接从 `_finish_bound` 返回，
  最坏 3s/次、不可能空转成活锁。3s 远小于租约心跳周期（300s/3），轮询期间不带心跳也安全。
* **不会吞掉工具结果**：进入 reconcile 的前置条件是 `observed == "BOUND_WAITING"`；
  权限 WAITING / 工具 continuation 的 waiting 本 Run 没有 UNKNOWN provider 记录，
  `reconcile_incomplete` 对它不做任何写入，随后按 `settled == 0` 原路返回。
  `record_reconciliation` 的 idempotency_key 含 `state_value`，多轮 BOUND_WAITING 折叠成一行
  （与 T1/T3 断言 `["BOUND_WAITING","BOUND_TERMINAL"]` 一致）。
* **一处不精确**：`settled` 是**全库**计数，其它 Run 有 UNKNOWN 时会 >0，于是工具 continuation 的
  waiting Run 也会被拖进 3s 空轮询（只是延迟，无功能损害）。这与 ARCHITECTURE 里
  "`reconcile_incomplete` 返回 0 → 维持原行为"的表述不符，见 §5。
* 进展判定用 **version 变化而不只是 state**（`foreground_runtime.py:1301-1312`），正确处理了
  "重发后立刻二次超时、waiting→running→waiting 版本 +2"的窗口。这是个到位的细节。

---

## 3. 非前台路径影响

**无回归。** 三处 `_NoopReconciliation` 的替换全部限定在 `_build_product_sdk_runtime_stack`
（`main.py:8873-8877`、`8940-8944`）与前台接线（`main.py:3284-3285`）。其余栈各自持有独立实现，未被本提交触碰：

| 路径 | 位置 | 现状 |
|---|---|---|
| s4 value adapter | `deskpet/memory/s4_value_adapter.py:597,642` | 仍 `_Noop()`；其 `ForegroundRuntimeExecutionAuthority` 不传 `provider_reconcile` → 新分支直接 `return False`，行为完全不变 |
| conformance | `deskpet/sdk_adapters/conformance.py:295` | 仍 `_Noop()`，独立栈 |
| desktop_runtime | `deskpet/sdk_adapters/desktop_runtime.py:329` | 仍 `_ProviderReconciliation()`，独立栈 |

代价是**这些车道仍保留 F06 原病灶**（真遇上 provider 超时同样停摆）。本次范围内可接受，但应记 FOLLOWUPS。
另有一个同源残留：`reconcile_incomplete` 全局扫库时会把**非前台 Run** 也标进 `exhausted_runs`，
而只有前台有 cancel 收尾逻辑 —— 非前台 Run 二次未知后仍会静默停在 waiting。建议一并记 FOLLOWUPS。

次要：`ProviderUnknownRetryOncePolicy.exhausted_runs`（`reconciliation.py:93`）是进程级、只增不减的 set，
长跑进程会缓慢增长；不影响正确性（键是唯一 run_id，无跨 Run 误判），但值得加个清理或上限。

---

## 4. 测试 T1–T4 覆盖度

| 用例 | 覆盖 | 判定 |
|---|---|---|
| T1 `..._once_retries_same_request_and_completes` | 同 request_id ×2、`succeeded/attempt=2/rehandoff=1`、决议 `confirmed_not_started` + evidence 前缀、blocker `resolution_id NOT NULL & wake_consumed=1`、Host `COMPLETED`、`RUNNING→COMPLETED`、历史文本、SDK 事件子序列 `run.activated→run.waiting→run.recovered→run.completed`、provider attempt 序列 `[(0,None),(1,None),(1,err),(1,None),(2,None),(2,None)]` | **强。** 事件子序列与 handoff_attempt=2 均为真实账本读取，不是 mock |
| T2 `..._recovers_after_restart` | 第一世 STILL_UNKNOWN → 停摆现场（head RUNNING / runs waiting / 决议空 / 发送 1 次），关栈重开 → 断言 `_start_once` 已写决议 → 再 drive → `QUERY_FOUND` + COMPLETED + 发送 2 次同 request | **强。** 精确复现 DECISION §3 的重启不续推路径 |
| T3 `..._twice_settles_cancelled_not_stuck` | 发送恰好 2 次、`unknown/attempt=2/rehandoff=1`、runs `cancelled`、head `CANCELLED`、`exhausted_runs` 命中、审计 `provider_reconciled → provider_unknown_exhausted` 子序列、决议 COUNT=1 | **强。** "二次未知一定收尾"被证死 |
| T4 `test_provider_unknown_retry_once_policy` + `test_production_wiring_no_longer_uses_noop_reconciliation` | 策略函数 `rehandoff_count=0/1` 两分支 + 默认适配器仍 fail-closed；main.py 源码断言不再有 `_NoopReconciliation` | **够用**（源码字符串断言偏脆，但作为接线回归可接受） |

**缺口**：没有任何用例覆盖 §1.3 的并发场景（全部测试都是单 Run）。这也是该缺陷能通过 21 绿的原因。

### 偶发红 `test_primary_none_routes_to_exact_task_and_writes_real_file[False]`

**与本改动无关，已用实验证据排除。** 该用例失败形态是 `wait_for(runtime._drive_once(), 20)` 超时挂起
（teardown 栈停在 `_drive_with_lease → await keeper`），不是断言失败。

| 配置 | 结果 |
|---|---|
| 原样（含 F06） | 6 次中 3 次红 |
| 注入插件把 `_PROVIDER_RECONCILE_MAX_ROUNDS` 置 0（关掉 `_finish_bound` 新分支） | 8 次中 2 次红 |
| 再把 `ProductRuntimeReconciliation.reconcile` 也改成 `return 0`（F06 运行期效果全关） | 8 次中 2 次红 |

F06 效果全部关闭后偶发率不变，说明这是既有的 dynamic 路由竞态，与本提交无因果关系。
（实现者在 ARCHITECTURE 里写"基线即偶发"的说法成立。）建议单独立项，别塞进 F06。

---

## 5. ARCHITECTURE 记录与实现的一致性

整体一致：①②③④ 四条与 `reconciliation.py` / `main.py` / `foreground_runtime.py` 的实际代码逐条对得上，
审计事件名、evidence 前缀、`handoff_attempt` 1→2、`restore_waiting_run` 都准确；
"新增 5 个单测"与新文件里的 5 个用例吻合。

**一处必须改的表述**（`ARCHITECTURE/AGENT_HARNESS.md:960-961`）：

> 权限 WAITING/工具 continuation 的 waiting（`reconcile_incomplete` 返回 0）维持原行为

`reconcile_incomplete` 是全库口径，返回值与"本 Run 有没有 UNKNOWN"无关；其它 Run 有未决记录时它会返回非 0，
此时工具 continuation 的 waiting Run 会多走一轮 3s 轮询。更要紧的是，这段记录**完全没有披露
"运行期调用 `reconcile_incomplete` 会触及其它 Run 的在途 handoff"**这一事实（§1.3），
读者会误以为它是 Run 局部操作。修 §1.3 时须同步改写这两句，并明确写出所选的边界闸策略。

---

## 6. 必须改的点（汇总）

1. **`backend/deskpet/sdk_adapters/reconciliation.py:161-171`（`ProductRuntimeReconciliation.reconcile`）**
   ／ **`backend/main.py:7436-7440`（`_reconcile_incomplete_providers`）**
   ／ 调用点 **`backend/deskpet/execution/foreground_runtime.py:1272`**：
   运行期调用 `reconcile_incomplete` 前必须加在途 handoff 闸门（或改用 SDK 侧作用域参数），
   避免把其它 Run 的 `HANDED_OFF`（`simple-harness-sdk/src/simple_harness/execution/dispatch.py:702-706`）
   判成 UNKNOWN → 丢弃正常响应（`dispatch.py:616-624`）→ 重复发送/重复计费。
   启动路径 `_start_once` 不受影响，保持原样。
2. **补并发回归测试**（`backend/tests/execution/test_primary_provider_timeout_reconciliation.py`）：
   Run B 在途时 Run A 触发 waiting reconcile，断言 B 的账本与物理发送次数不变。
3. **`ARCHITECTURE/AGENT_HARNESS.md:960-961`**：改写 `reconcile_incomplete` 返回值语义与作用域，
   并披露运行期调用的跨 Run 影响面与所加闸门。

## 7. 建议（不阻塞，记 FOLLOWUPS）

* `deskpet/memory/s4_value_adapter.py:597`、`deskpet/sdk_adapters/desktop_runtime.py:329` 仍是 Noop 车道，
  保留 F06 原病灶。
* 非前台 Run 被标进 `exhausted_runs` 后无人 cancel，仍会停在 waiting。
* `ProviderUnknownRetryOncePolicy.exhausted_runs`（`reconciliation.py:93`）进程级只增不减。
* `test_primary_none_routes_to_exact_task_and_writes_real_file[False]` 的既有挂起竞态单独立项。
* evidence_ref 的确定性是 `record_provider_reconciliation` 幂等的前提（`uow.py:6713-6719`），
  建议在策略处加一行注释锁住这个约束。
