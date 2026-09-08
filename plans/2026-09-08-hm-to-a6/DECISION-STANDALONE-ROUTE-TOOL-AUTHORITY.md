# 裁决备忘：standalone 路由下的 TaskScope 工具权威（HM-TO-A6 事故 A / B）

- 日期：2026-09-08
- 证据目录：`.local-test-evidence/2026-09-08/native-a6-b3682fe1/`
- 冻结 SDK：`simple_harness_sdk-0.7.10`（只读，不在本次改动范围内）
- 分支：`worktree-agent-a1ecf6c03352c1af9`（基线 main `933df61e`）

---

## 一、事故 A：一次 `run_shell` 打掉整个 Run

### 1. 证据

- Host 日志：`primary-ui-hv9k7ncq/native.log`，UTC 2026-09-08T05:26:18.242Z

  ```
  sdk_run_driver_failed error_type=TaskExecutionAuthorityError
                        error_message=sdk_task_execution_route_authority_missing
  run.fail → run.terminal
  ```

- SDK 库：`primary-ui-xmqudtzt/userdata/data/simple-harness-sdk/execution-v6.sqlite3`
  - Run `product-sdk-a551104a…` 状态 `failed`；`run_events` 里 `audit.runtime.v2`
    的 `name=tool.envelope` 带 `error_code=runtime_boundary_failed`。
  - `execution_effects` 只有 12 行，**没有** `run_shell` 那一行——它在任何工具
    回执产生**之前**就死了。

- 该 Run 的实际调用序列（turn 8，用户提示「再读 <文件 B> 的全文，告诉我它的第一行」）：

  | turn | tool | 参数 | 结果 |
  |---|---|---|---|
  | 1–5 | `tool_search` | `read file contents…` | 命中里 `builtin:write_file` / `builtin:pdf_export` 排在 `read_file` 前面 |
  | 6 | `tool_describe` | `builtin:run_shell` | **succeeded**，`activatable: true`、`activation_required: true` |
  | 7 | `tool_activate` | `builtin:run_shell` | **succeeded** |
  | 8–11 | `tool_search {}` / `context_route {}` | 空参 | `missing_required_argument` |
  | 12 | `context_route` | `{"route":"direct_standalone"}` | **succeeded**（`task_scope_id: null`） |
  | 13 | `run_shell` | `head -n 1 <path>` | 未落库 —— 整 Run `driver_failed` |

### 2. 根因（file:line）

三段事实合起来构成这条死路：

1. **每轮 provider 快照会隐藏 PROJECT_EFFECT 工具，但只隐藏「展示」。**
   `backend/deskpet/sdk_adapters/context_authority.py:1050`
   `_visible_provider_specs(...)`：`route_state` 不是 `ROUTED_TASK` 时把
   `effect_class is PROJECT_EFFECT` 的 spec 从**本轮 provider 规格表**里滤掉。
   注释本身就写明这样做是「整 Run 故障降概率」。
   但 Run 的**可执行暴露**（catalog 的 `direct_ids | activated_ids`）一字未动，
   而模型完全可以照着自己历史里 `tool_describe` 给的 schema 直接按名字发起调用。

2. **冻结 SDK 的路由屏障只拦 UNROUTED，不看 `task_scope_requirement`。**
   `.venv/.../simple_harness/runtime/drivers/react_loop.py:900` `_preflight_tool_batch`：

   ```python
   if (policy.route_requirement is ToolRouteRequirement.REQUIRED
           and route_state is ContextRouteState.UNROUTED):
       rejected[ordinal] = "ROUTE_BARRIER_NOT_OBSERVED"
   ```

   `ToolTaskScopeRequirement` 在整个 SDK 里**从不参与判定**（全库只出现在数据类
   定义与序列化）。因此 `routed_standalone` + PROJECT_EFFECT 顺利通过屏障。

3. **随后的 envelope 签发是「无回执可言」的一跳。**
   `.venv/.../simple_harness/runtime/drivers/react_loop.py:169`（`EffectBatchExecutor.one`）
   在 `runtime_operation(..., "tool.envelope")` 里调用
   `services.task_execution_authority.issue_envelope(...)`；Host 侧
   `backend/deskpet/sdk_adapters/task_execution.py:130-140` 判定
   PROJECT_EFFECT 且回执没有 `task_scope_id` / binding 权威 →
   `raise TaskExecutionAuthorityError("sdk_task_execution_route_authority_missing")`。
   异常逃出 react driver 即 `run.fail`。

**结论：`TaskExecutionAuthorityError` 并不是「从工具拒绝路径逃逸」——在这一跳上
根本还不存在工具拒绝路径。** `EffectGate`（`sdk_adapters/effect_gate.py`）才是那个
只返回 `ToolResult.rejected`、永不抛异常的门，但它在 `services.tools.execute` 里，
排在 envelope 签发**之后**。

### 3. 裁决

#### 3.1 为什么不能「把这一跳改成工具级拒绝」

在冻结 SDK 0.7.10 下，`issue_envelope` 必须返回一个 `TaskExecutionEnvelope`；
若返回，`EffectBatchExecutor.one` 紧接着自查：

```python
if policy.effect_class is ToolEffectClass.PROJECT_EFFECT and (
        route_receipt is None
        or route_receipt.route_state is not ContextRouteState.ROUTED_TASK
        or envelope.task_scope_id != route_receipt.task_scope_id ...):
    raise RuntimeError("project TaskExecutionEnvelope has stale TaskScope binding authority")
```

即**返回也一样杀 Run**，只是换个错误文本。逐条排除的其它出口：

| 设想 | 为什么不做 |
|---|---|
| Host 的 `execution_policy` 在 standalone 下把 PROJECT_EFFECT 报成 NON_PROJECT_EFFECT | `EffectGate._is_project_effect`（`effect_gate.py:218`）读的是**同一个** `execution_policy`，门会直接放行——真的会在无工作区约束下执行 `run_shell`。这是削弱权威检查，禁止。 |
| 路由提交为 standalone 时把 PROJECT_EFFECT 能力移出可执行暴露 | `RuntimeToolCatalog.execution_policy`（`runtime_catalog.py:1071`）对不可见工具抛 `catalog_execution_policy_unavailable`，`_preflight_tool_batch` 里抛出 = 同样整 Run 故障，只换稳定码，更差。 |
| 让 Host 的 provider 适配器丢掉「本轮未提供的工具名」 | 会凭空吞掉模型意图或伪造模型输出；SDK 侧唯一的 Host 钩子是 `prepare_snapshot`（provider 调用**之前**），response 之后没有任何 Host 钩子。 |

**因此裁决：在冻结 SDK 下，`routed_standalone` 状态下真的发起 PROJECT_EFFECT 调用
必然是整 Run 故障；修复方向是让这个状态不可达，而不是伪造一个可恢复回执。**

#### 3.2 实际做的修复

把「本轮路由状态」从快照收窄面**发布到能力披露面**，让 `tool_search` /
`tool_describe` / `tool_activate` 与 provider 规格表看到同一件事实：

| 文件 | 改动 |
|---|---|
| `backend/deskpet/sdk_adapters/run_route_state.py`（新增） | `RunRouteStateMemo`：进程内、有界（FIFO 4096）、last-writer-wins 的「Run → 本轮 ContextRouteState」备忘，与既有 `RunFaultMemo` 同构。 |
| `backend/deskpet/sdk_adapters/context_authority.py` | `ProductRunContextAuthority` 新增可选 `route_state_memo`；`prepare_snapshot` 在收窄 specs 的同一处写入 `ContextRouteState(request.route_state).value`。 |
| `backend/deskpet/sdk_adapters/tool_authority.py` | ① `SdkRunToolAuthorityRegistry` 在 `prepare_run` 冻结 `project_effect_capabilities(run_id)`（直接取自带 SDK 执行策略的 `ExecutableToolRecord.effect_class`，`mark_terminal` 一并释放）；② `SdkRuntimeCapabilityBridgeAdapter` 新增可选 `route_state_memo`，`describe`/`search`/`activate` 三处统一走 `_unavailable_with_route`；③ 新稳定原因码 `project_effect_requires_task_route` 与其 `next_action` 文案。 |
| `backend/deskpet/tools/tool_search.py` | `_activation_rejection` 为该码配上正确文案（**不能**继承 `tool_unavailable` 的「永不重试」口径——路由之后是可以重试的）。 |
| `backend/main.py` | `_ensure_run_route_state_memo()` 单例；接进 `SdkRuntimeCapabilityBridgeAdapter` 与 `_build_run_context_authority`；Run 终态时释放。 |

修复后事故序列在 **turn 6** 就改道：

- `tool_describe builtin:run_shell` → `activatable: false`、`activation_required: false`、
  `availability_reason: project_effect_requires_task_route`、
  `next_action` 点名 `context_route` 的 `continue_active` / `resume_existing` / `create_new`；
- 若模型仍调 `tool_activate` → `error_code=project_effect_requires_task_route`
  的**工具级失败**，Run 继续；
- `tool_search "read file…"` 里 `read_file` 排到 `run_shell` / 写类工具之前
  （事故里正好相反）。

#### 3.3 明确**没有**改的东西，以及为什么

- **`ProductTaskExecutionAuthority` 一行未改。** `sdk_task_execution_route_authority_missing`
  仍是 PROJECT_EFFECT + 无 TaskScope 回执的稳定码，仍写进 `RunFaultMemo`
  供终态证据使用。回归护栏：
  `tests/sdk_adapters/test_standalone_route_activation_disclosure.py::test_standalone_route_deny_semantics_unchanged`，
  以及原封不动的
  `test_s5b_acceptance_matrix.py::test_standalone_route_project_effect_is_run_fault_with_stable_code`。
- **`EffectGate`、`SDK_TOOL_EXECUTION_POLICY_OVERRIDES`、`PROJECT_EFFECT_TOOL_NAMES`、
  所有 schema 与既有错误码未改。**
- **备忘缺失时 fail-open（退回旧行为）而非 fail-closed。** 它只影响披露；
  下游每一道权威（SDK 路由屏障、Host envelope authority、EffectGate）各自独立
  fail-closed。若改成 fail-closed，一个还没准备过快照的 Run 会平白丢掉能力。
  护栏：`test_unwired_memo_degrades_to_previous_behaviour`。

#### 3.4 仍然留在模型侧 / SDK 侧的残余

**残余风险：** Run 冻结时就把 PROJECT_EFFECT 工具作为 direct 能力暴露，模型提交
`direct_standalone` 后凭记忆按名字直接调用 —— 快照虽已隐藏，冻结 SDK 仍会走到
envelope 签发并杀掉整 Run。这条路在 0.7.10 下**无法**在 Host 侧转成工具级拒绝
（见 §3.1）。真正的修法在 SDK：`_preflight_tool_batch` 应当同时判定
`task_scope_requirement is REQUIRED and route_state is not ROUTED_TASK`，
发同一个 `ROUTE_BARRIER_NOT_OBSERVED` 工具级拒绝。**建议作为 SDK 侧 followup 提出。**
在此之前，Host 能做的已经做尽：既不在快照里给、也不让它被激活、并在披露文案里
明说「Under direct_standalone or memory_standalone it can never run; do not call it
by name either」。

---

## 二、事故 B：turn 7 的工具循环（调查结论：是 Host 人机工效缺陷，已修）

### 1. 证据（与任务书描述的差异）

Run `product-sdk-cba43a68…`，`execution_effects` 逐行：

| turn | tool | 参数 | 结果 |
|---|---|---|---|
| 1–7 | `task_scope_search` | `主清单 清单核对` 等 3 种措辞 | **succeeded**，`candidates: []` |
| 8–9 | `task_scope_search` | `{}` | `missing_required_argument` |
| 10–11 | `task_scope_search` | `主清单 A 清单核对</parameter>\n` | succeeded，仍 `candidates: []` |
| 12–18 | `task_scope_search` | `{}` / 带 XML 噪声 | `missing_required_argument` ×6 |
| 19 | `context_route` | `create_new`, `title=核对主清单 A` | succeeded |
| 20–25 | `task_scope_update` | `{}` ×5 + 一次带被 XML 噪声污染的 `outcome` | `missing_required_argument` ×6 → `react_max_turns_exceeded` |

两处需要更正任务书的描述：

- 该 Run **不是**在活跃 TaskScope 内开始的，它开始时是 UNROUTED（turn 19 才
  `create_new`）。但系统里**确实**存在一个活跃任务「秋分资料整理」
  （`state.db` `task_scopes`，`created_at=1788844650`），
  `context_route(route=continue_active)` 本来就能直接续上、**根本不需要搜索**。
  模型因此白白创建了重复任务「核对主清单 A」（同一天里被创建了两次）。
- 循环的直接触发因素之一是 provider 侧把 XML 片段（`</parameter>`）漏进了参数，
  这属于模型/provider 侧，Host 无法也不该修。

### 2. 工具面能不能回答这两个问题？——不能

- **(a)「当前活跃任务不需要搜索」**：`task_scope_search` 的描述
  （`backend/main.py`）原文是「search over the caller's own **archived** task
  scopes」，通篇只提 `resume_existing` / `create_new`，**从未提到 `continue_active`**。
  零命中的返回体也只有一段与「已完成任务如何复用工作区」有关的 `note`，
  对「下一步该做什么」只字未提。
- **(b)「`goal.set` 到底要哪些参数」**：`task_scope_update` 的 schema 其实写全了
  （`operations[]` 元素必填 `operation_id/kind/value/reason_code/evidence_refs`，
  `kind` 枚举含 `goal.set`）。模型根本没走到那一步——它连顶层四个必填参数都补不齐，
  因为缺参回执只说「see the tool description for the expected values」，
  **不回显任何形状**（类型 / 枚举 / 下界 / 数组元素必填项）。

### 3. 裁决与修复（照抄 `bcd3bb15` 对 `procedure_use` 的口径：稳定码与 schema 不动，只让文案可行动）

| 文件 | 改动 |
|---|---|
| `backend/deskpet/sdk_adapters/tools.py` | 新增 `_argument_shape` / `_expected_arguments_hint`；`missing_required_argument` 的 `public_message` 改为回显**已发布 schema 里已有的**形状，例：`Expected: outcome (string, one of "mutate"\|"no_mutation"); base_revision (integer, min 1); evidence_refs (array, of string, min items 1); idempotency_key (string, min length 1).`。通用实现，全部产品工具受益；输出有界（600 字符）。 |
| `backend/deskpet/sdk_adapters/context_route.py` | `handle_task_scope_search` 零命中时补 `next_action`：明说「不要重复同一条零命中查询」，并在 Host 已知有当前活跃任务时点名 `continue_active` 与该任务的 `task_scope_id`；无活跃任务时点名 `create_new`。命中时返回体不变。 |
| `backend/main.py` | `task_scope_search` 描述补一句：续做当前活跃任务直接 `context_route(route=continue_active)`，不需要本工具；零命中的查询不要重复。 |

### 4. 明确**没有**改的东西，以及为什么

- **不改任何 schema、不改任何错误码。** `missing_required_argument`、
  `invalid_tool_arguments`、`task_scope_search_query_invalid` 等一律保持。
- **不回显活跃任务的标题。** Host 手上直接拿得到的是
  `latest_task_route_decision()` 里的 `task_scope_id`；标题只在
  `_scope_disclosure_reader`（一道披露权威）后面。而 `task_scope_id` 本来就已经
  在 `context_route_active_scope_mismatch` 的拒绝里回给模型过，属既有口径；
  为了一句提示去绕开披露权威不划算。**任务书里「说出 <title>」这一条被降级为
  「说出 task_scope_id + 该调哪个路由」。**
- **不改 `task_scope_update` 的描述。** 它已经写清了「只在真实项目效果之后调用一次」
  与 `operations` 的用途；本次缺的是形状回显，不是描述。
- **不动 `tool_search.py` 里那份独立的 `_missing_argument_rejection`。**
  它服务 `tool_search`/`tool_describe`/`tool_activate` 三个工具，已经各自带了
  具体示例（"copy the three top-level fields returned by tool_describe"），
  不属于本次事故的失效点。
- **provider 侧的 XML 噪声不修。** 属模型/provider 输出质量，Host 侧已由
  `invalid_tool_arguments` 稳定拒绝且可恢复。

---

## 三、测试

新增/改动的测试（全部为确定性单测，不依赖真实 provider）：

- `backend/tests/sdk_adapters/test_standalone_route_activation_disclosure.py`（新增，13 例）
  - 冻结分类正确（`run_shell` 是 PROJECT_EFFECT，`read_file` / `context_route` 不是）
  - `unrouted` / `routed_standalone` 两态下 `bridge.activate` 抛稳定码
  - `tool_describe` 在花掉一次 activate 之前就说明白（要求 2）
  - `tool_activate` 是可恢复的工具级拒绝、Run 继续、暴露未被污染（要求 1、3）
  - `tool_search` 把可激活的读工具排到被路由挡住的工具之前
  - `routed_task` 下三跳完全不变
  - 备忘未接线时退回旧行为（证明这是披露层而非权威层）
  - **deny 语义不变**：`ProductTaskExecutionAuthority` 仍抛
    `sdk_task_execution_route_authority_missing` 并写入 `RunFaultMemo`
  - `prepare_snapshot` 确实发布了本轮 route_state（用 `s5b_effect_gate_harness` 跑真链路）
  - `RunRouteStateMemo` 的有界 / last-writer-wins / release / 入参归一
- `backend/tests/sdk_adapters/test_missing_argument_shape_echo.py`（新增，6 例）
- `backend/tests/sdk_adapters/test_context_route_tool.py`（+4 例）
- `backend/tests/sdk_adapters/s5b_effect_gate_harness.py`：接线 `route_state_memo`
  （与生产装配一致），`GateEnv.route_memo` 暴露给用例。

---

## 四、自审要点

1. **没有削弱任何权威检查。** 新增判定只在披露/激活面「提前说不」，
   且是在既有 deny 之上再加一层；`_route_blocked_capabilities` 在备忘缺失时返回空集，
   即回到改动前的行为。
2. **分类来源是冻结的。** `project_effect_capabilities` 取自 `prepare_run` 当场构造的
   `ExecutableToolRecord.effect_class`，与 `execution_policy` 同源，
   不会因为进程里换了新 manifest 而漂移；WAITING 重启走 `restore_run` → `prepare_run`，
   与 `_runtime_exposures` 同生共死（`mark_terminal` 三个字典一起释放）。
3. **备忘的生命周期。** 有界 FIFO + Run 终态监听器释放，两道保险；进程内、不落盘，
   丢失只退化为旧行为。
4. **文案不泄露私有数据。** 只出现 capability_id、路由名与工具名；
   测试断言工作区路径不出现在模型可见消息里。
5. **形状回显有界。** 600 字符上限 + 枚举最多 6 项，宽 schema 不会灌爆工具消息。
