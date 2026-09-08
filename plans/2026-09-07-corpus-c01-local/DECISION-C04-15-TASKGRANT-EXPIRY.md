# C04-15 `TaskGrant expired before activation` 根因裁定

> **独立子代理分析，主代理复核后执行。**
> 只读分析，未改动任何代码。
> 证据：`.local-test-evidence/2026-09-07/corpus-batch/run-01e/C04-15/` 与 `run-01f/C04-15/`（Host main + Memory 0.6.25，gpt-5.6-luna 主 Provider）。

---

## 0. 结论速览

- **不是负载/超时问题**，是 **Host 组合期的"时钟接缝注入不完整"**：TaskGrant 的 `expires_at` 由**真实墙钟** `time.time()` 铸造，而激活时用的是**跑道注入的语料场景时钟**；两者混用。
- C04-15 是**整批唯一 `scenario_clock` 不等于 `2026-09-06T10:00+08:00` 的用例**（它是 `2026-09-30T18:00+08:00`，比真实时间**超前约 23 天**），远超 TaskGrant 的 8 小时 TTL，所以它是唯一稳定触发者；其余用例的场景时钟比真实时间**早约 1 天**，于是"恰好"侥幸通过检查。
- 定性：**产品侧缺陷（时钟接缝注入不完整）**，不是"过期窗过短"，也不是跑道误用。唯一推荐最小修复：把已有的 `clock=` 注入补齐到铸造侧（约 6 行、2 个文件），**不得**放宽 `task_grants.py` 的过期判定。

---

## 1. 输入/种子形状与模型实际路由

### 1.1 输入与种子

`run-01e/C04-15/scoring/C04-15/input.json:1-9`

```
current_user_message: 三季度清点发现什么，下一季度首项提醒是什么？
recent_messages: []            # 空，无多轮
scenario_clock: 2026-09-30T18:00:00+08:00 (Asia/Shanghai) → epoch 1790762400
unresolved_source_text: null
```

`run-01e/C04-15/scoring/C04-15/setup.json:1-16`

- `setup_source_text`：`E：2026年9月30日16:00三季度清点缺备份索引，17:00已入账；P：2026年10月2日10:00提醒补索引，pending。`
- `clock_source_text` 明确要求："Host clock、public SDK as_of/evaluated_at 及 Provider 可信日期上下文必须由这同一个 fixture clock 适配；无法统一则记设置缺口，不运行成另一时刻。"
- `blocking_reasons` 已含 `THREE_LAYER_CLOCK_ADAPTER_NOT_IMPLEMENTED` —— 本次故障正是该缺口的实证。
- **种子里没有任务/工作区（task/workspace）路由要素**：`oracle.json:2-11` 的 `requires_task_scope_search=false`、`required_types=["episode","prospective"]`；`state.db` 的 `task_workspace_binding_grants` 计数为 0。

种子落库正常（非种子问题）：
`runtime/userdata/data/human_memory_v7.db` → `cognitive_memory_heads` 两条，`episode` + `prospective`，`created_at=1790758800.0`（= 2026-09-30T17:00+08:00，摄入时刻早于场景时刻，符合 C04 fixture 约定）。

### 1.2 模型实际路由

来自 `runtime/userdata/data/simple-harness-sdk/execution-v6.sqlite3` → `provider_invocations.response_json`（唯一一次 Provider 调用，`finish_reason=tool_calls`）：

```json
{"name": "context_route",
 "arguments": {"route": "memory_standalone",
               "memory_types": ["episode", "prospective"],
               "include_short_horizon": true,
               "query": "三季度清点发现了什么，以及下一季度的首项提醒是什么",
               "goal": "检索用户关于三季度清点结果和下一季度首项提醒的已存记录"}}
```

- 路由判断**完全正确**：`memory_standalone` + `episode/prospective`，与 oracle 的 `required_types` 完全一致（`batch-summary.jsonl` 中 C04-15 的 `predicted_types=["episode","prospective"]`）。
- 系统提示里的 Host trusted clock 已正确写成 `2026-09-30T18:00:00+08:00`（见 `provider_invocations.request_json` 的 system 段），即**语料时钟已经正确送达模型**。
- 但该 `context_route` 工具在授权激活阶段即被打回：`execution.json` 的 `trace.operation_audit` 中 `tool | context_route | requested → failed`，`runtime.driver | failed`；`route_audit=[]`、`route_effects=[]`，`state.db` 的 `context_route_decisions` / `context_route_tool_invocations` 均为 0 行。**Run 死在"工具已提议、尚未执行"的授权关口，不是模型问题。**

---

## 2. 抛出点、两侧时钟、混用证据

### 2.1 抛出点（Host 侧，Harness SDK 侧无此字符串）

- `backend/deskpet/product_state/task_grants.py:95-97`
  ```python
  if current.grant.expires_at is not None and current.grant.expires_at <= now:
      self.expire(...)
      raise TaskGrantConflict("TaskGrant expired before activation")
  ```
  异常类型 `TaskGrantConflict(RuntimeError)` 定义于同文件 `:15-16`；因此 worker.log 记为 `error_type: RuntimeError` / `"TaskGrantConflict"`。
- 调用者：`backend/deskpet/sdk_adapters/authorization.py:136-141`（`ProductAuthorizationAdapter.prepare` 中 ALLOW 分支的 `activate(..., now=now)`，`now` 来自 `:102` 的 `self._clock()`）。
- 全仓 `grep "TaskGrant expired before activation"` 仅此一处；Harness `simple-harness-sdk/src` 无匹配（该目录在本机不存在于工作树，SDK 以安装态提供，且错误串确证来自 Host）。

### 2.2 `expires_at` 用的是**真实时间**

- `backend/deskpet/permissions/runtime.py:169-182`：`PreparedAuthorizationRuntime.__init__(..., clock=time.time, task_grant_ttl_seconds=8*60*60)`。
- `backend/deskpet/permissions/runtime.py:370`：`plan_prepared_call` 内 `now = float(self._clock())`。
- `backend/deskpet/permissions/runtime.py:249`（`_new_task_grant`）与 `:311`（`_expanded_task_grant`）：`expires_at = now + self._task_grant_ttl`。
- **组合点没有注入 clock**：`backend/main.py:2912`
  ```python
  authorization_runtime = PreparedAuthorizationRuntime(store)     # ← 无 clock=，退回 time.time
  ```
  它位于 `backend/main.py:2661 async def _initialize_capability_runtime()`，该函数**没有 clock 形参**。
  同址 `backend/main.py:2913-2916` 的 `AdmissionTaskGrantRuntime(store, capability_managed_root=...)` 同样漏注（其构造器 `backend/deskpet/permissions/admission.py:52-67` 是支持 `clock=` 的，TTL 同为 8h，`:198`/`:217` 同样 `expires_at = self._clock() + ttl`）。
  另外 `backend/main.py:8623-8628` 的 `SdkPreparedAuthorizationPolicy(...)` 也未传 `clock`（默认 `time.time`，见 `backend/deskpet/sdk_adapters/tool_authority.py:1681`、`:1929` 的 `expires_at=float(self._clock())+300.0`），尽管此处 `clock` 局部变量就在作用域内。

### 2.3 激活时比较的是**场景时钟**

- `backend/main.py:8631-8644`：`ProductAuthorizationAdapter(..., clock=clock, ...)` —— **唯一被注入的一侧**。
- `clock` 由 `backend/main.py:11091-11092 _activate_product_sdk_runtime(*, clock=time.time, ...)` 经 `:11150` 传入 `_build_product_sdk_runtime_stack(..., clock=clock)`。
- 跑道注入：`backend/deskpet/quality/corpus_scoring_session.py:215-216`
  ```python
  scenario_time = datetime.fromisoformat(authored["scenario_clock"]["instant"]).timestamp()
  clock = lambda: scenario_time
  ```
  `:407` `await main._activate_product_sdk_runtime(clock=clock, ...)` —— 注入到激活侧；
  而 `:319` `await main._initialize_capability_runtime()` —— **没有也无法注入**（函数无该形参），铸造侧因此保持真实墙钟。

### 2.4 混用的直接物证

TaskGrant 行**不在** `state.db`，而在 `runtime/userdata/data/sdk-product-state.db` 的 `task_grants` 表（`state.db` 只有 foreground/route 系列表）：

| run | status | prepared_at / created_at | expires_at | revoked_at |
|---|---|---|---|---|
| run-01e | `expired` | `1790762400.0` | `1788830870.257` | `1790762400.0` |
| run-01f | `expired` | `1790762400.0` | `1788843475.092` | `1790762400.0` |

- `1790762400 = 2026-09-30T18:00+08:00` = **场景时钟**（激活侧 `now`，也是 `prepare` 写入的 `prepared_at`）。
- `1788830870 = 2026-09-07T17:27:50Z + 28800s`，`1788843475 = 2026-09-07T20:57:55Z + 28800s` = **真实墙钟 + 8h TTL**（铸造侧）。
- 因此 `expires_at (≈1.7888e9) <= now (1.7908e9)` 恒成立，差值约 **1 931 530 s ≈ 22.4 天**，`task_grants.py:95` 必然命中。

时间线（`run-01e/.../worker.log:39-53`，与 run-01f `:40-52` 同构）：
`17:27:08 run.start` → `17:27:08 provider_attempt.started` → `17:27:50 provider succeeded (elapsed_ms=41922)` → `17:27:50.2498 tool.invoked` → `17:27:50.2589 sdk_run_driver_failed / TaskGrantConflict` → `run.fail`。Provider 耗时 42 s（run-01f 为 35.5 s）与失败**无因果关系**：即便 Provider 瞬时返回，22 天的时钟差依然过期。57.1 s / 48.1 s 的总耗时只是 Provider 时延 + 启动开销。

---

## 3. 为什么只有 C04-15 触发；定性与最小修复

### 3.1 唯一触发者的原因

对 run-01e 全批 92 个用例逐个读取 `input.json.scenario_clock`：

- **91 个**用例是 `2026-09-06T10:00:00+08:00`（epoch `1788660000`）；
- **仅 C04-15** 是 `2026-09-30T18:00:00+08:00`（epoch `1790762400`）。

对应 `sdk-product-state.db.task_grants` 全批状态：

- 所有其它用例的 grant 一律 `status=active`，`expires_at` 全部落在 `1.78882e9 ~ 1.78864e9` 区间（真实墙钟 + 8h）；
- 由于它们的场景时钟 `1788660000` **早于**真实墙钟约 1 天，`expires_at = 真实now + 8h > 场景now` 恰好成立，检查侥幸通过；
- C04-15 的场景时钟**超前**真实墙钟约 23 天 ≫ 8h TTL，检查必然失败。

批级验证：`grep -rl "TaskGrant expired before activation" run-01e run-01f` 只命中 C04-15 的两份 `worker.log`（`run-01e`/`run-01f` 各一次），run-01e 其余 12 个 `EXECUTION_FAILED`（C03-03、C04-14/16/17/20、C05-10/11、C06-09/11/14/20、C11-02）均**非** TaskGrant 类。C04-15 在 01e / 01f 之外的批次未被调度，故"两跑两失败"即 100% 复现率。

**因此这是一颗全批潜伏的定时炸弹**：只要语料场景时钟设在真实时间之后超过 8 小时，任何用例都会以同样方式炸；C04-15 只是第一个把它踩响的。

### 3.2 定性

**产品缺陷 —— Host 组合期的时钟接缝注入不完整**，具体是"同一个 TaskGrant 的铸造侧与激活侧使用了两个不同的时钟源"。理由：

1. **不是"过期窗过短"**：8 h TTL 完全合理，把它改长只是掩盖症状；真正的差值是 22 天，任何合理 TTL 都挡不住。
2. **不是跑道注入误用**：跑道已经在能注入的地方全部注入了（`corpus_scoring_session.py:385` fixture manager、`:407` product SDK runtime）；铸造侧 `_initialize_capability_runtime()`（`backend/main.py:2661`）**根本没有暴露 `clock=` 形参**，跑道无法在不改产品代码的前提下修复。缺口属于产品组合。
3. **产品自身也有残余风险**（虽小）：真实部署中若系统墙钟在铸造与激活之间向前跳跃超过 TTL（NTP 大幅校正、休眠唤醒后跳时），同一路径会出现同样的误过期。统一时钟接缝顺带消除该风险。
4. 语料自身早已把这条记为 `THREE_LAYER_CLOCK_ADAPTER_NOT_IMPLEMENTED`（`setup.json:2-6`）并要求"无法统一则记设置缺口，不运行成另一时刻"；本次是**带着缺口硬跑**，才把它变成 Run 级崩溃。

### 3.3 唯一推荐的最小修复（约 6 行 / 2 个文件）

**原则：只补注入，绝不放宽 `task_grants.py:95` 的过期判定**（那是授权安全边界，放宽等于取消 TaskGrant 过期语义）。

| # | 文件:行 | 改动 |
|---|---|---|
| 1 | `backend/main.py:2661` | `async def _initialize_capability_runtime(*, clock=time.time) -> None:` |
| 2 | `backend/main.py:2912` | `PreparedAuthorizationRuntime(store, clock=clock)` |
| 3 | `backend/main.py:2913-2916` | `AdmissionTaskGrantRuntime(store, capability_managed_root=..., clock=clock)` |
| 4 | `backend/main.py:8623-8628` | `SdkPreparedAuthorizationPolicy(..., clock=clock)`（`clock` 已在 `_build_product_sdk_runtime_stack` 作用域内） |
| 5 | `backend/deskpet/quality/corpus_scoring_session.py:319` | `await main._initialize_capability_runtime(clock=clock)` |

生产调用点 `backend/main.py:5698` 保持 `await _initialize_capability_runtime()` 不变（默认 `time.time`），**生产行为零变化**；跑道从此单一时钟源。

已排除的替代方案：
- 放宽/取消 `task_grants.py:95` 与 `:124` 的过期比较 —— 授权回归，禁用。
- 调大 `task_grant_ttl_seconds` —— 治标不治本，且 22 天差值需要荒谬的 TTL。
- 把语料 C04-15 的场景时钟改到过去 —— 篡改语料，且掩盖全批潜伏缺陷。
- 让 `ProductAuthorizationAdapter` 改用 `time.time` 激活 —— 方向相反，会把已正确注入的一侧也污染回真实时间，破坏 C04/C11 的时间语义。

### 3.4 单测设计

**T1（回归主测，必须）—— 时钟一致性不变式**
在真实组合路径上（`_initialize_capability_runtime(clock=F)` + `_activate_product_sdk_runtime(clock=F)`，`F = lambda: time.time() + 30*86400`）走一次 `context_route` 类工具授权：
- 断言 `sdk-product-state.db.task_grants` 中该行 `expires_at == F() + 8*3600`（即由注入时钟派生，而非 `time.time()`）；
- 断言 `status == "active"`、无 `TaskGrantConflict`；
- 未修复版本上此测必红（`expires_at < F()`，抛 `TaskGrant expired before activation`）。

**T2（参数化边界）—— 时钟偏移矩阵**
对 `offset ∈ {-30d, -1d, 0, +8h-60s, +8h+60s, +30d}` 参数化 T1，全部要求 `active`。此测直接覆盖"其它用例只是侥幸通过"这一事实：未修复版本在 `offset ≥ +8h` 全红、在负偏移全绿，正是当前全批的真实分布。

**T3（单元级，快）—— 注入贯通性**
不启动完整 Runtime，仅断言 `_initialize_capability_runtime(clock=F)` 后 `service_context.get("authorization_runtime")` 与 `"admission_task_grant_runtime"` 两者铸造出的 TaskGrant 的 `expires_at` 均以 `F()` 为基准（通过公开的 `plan_prepared_call` / `authorize` 返回值断言，不触碰私有 `_clock`）。

**T4（语料级冒烟）**
以 `-m real_provider` 重跑 C04-15 两次，要求 `execution.json.execution_status != "EXECUTION_FAILED"`、`trace.operation_audit` 中 `context_route` 为 `completed`、`route_audit` 非空、`state.db.context_route_decisions` 有 1 行。

---

## 4. 关键证据文件清单（绝对路径）

- `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C04-15/scoring/C04-15/worker.log:39-53`
- `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01f/C04-15/scoring/C04-15/worker.log:40-52`
- `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C04-15/scoring/C04-15/runtime/userdata/data/sdk-product-state.db`（表 `task_grants`）
- `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C04-15/scoring/C04-15/runtime/userdata/data/simple-harness-sdk/execution-v6.sqlite3`（表 `provider_invocations`、`run_events`）
- `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/deskpet/product_state/task_grants.py:95-97`
- `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/deskpet/permissions/runtime.py:169-182, 249, 311, 370`
- `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/deskpet/permissions/admission.py:52-67, 198, 217`
- `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/deskpet/sdk_adapters/authorization.py:102-105, 136-141`
- `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/deskpet/sdk_adapters/tool_authority.py:1681, 1929`
- `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/main.py:2661, 2912, 2913-2916, 5698, 8623-8628, 8631-8644, 11091-11092, 11150`
- `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/deskpet/quality/corpus_scoring_session.py:215-216, 319, 385, 407`
