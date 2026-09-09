# 事件 X-2：打开 memory 车道后的每回合内存保留（事件 X 的 X-F5 收口）

- 日期：2026-09-09
- 分支：`worktree-mem-growth-2`（未合并）
- 前置：[事件 X](DECISION-X-BACKEND-MEMORY-GROWTH.md)（已并 `87b9423d`，修的是
  `ToolRegistry._calls`），本轮针对它的 followup **X-F5**（离线复现没开 agent memory，
  ingestion 出站箱 / 分析出站箱 / 认知库 / typed recall / 短程向量 / 审计几条车道
  一条都没被覆盖），并回应 X-F3（分页缓存按条数而非字节设限）。
- 触碰文件：`backend/deskpet/tools/context_page_in_tools.py`（+46/-10）、
  `backend/tests/execution/test_primary_runtime_memory_growth.py`（+303）、
  新增诊断脚本 `backend/scripts/perf/x2_memory_lanes_growth.py`（独立进程版复现，
  用来取不受 conftest 夹具污染的 RSS / gc 普查 / 保留根数据）

## 1. 结论先行

| 问题 | 结论 |
|---|---|
| memory 车道（ingestion outbox / analysis 出站箱 / HumanMemoryV7 认知写入 / typed recall / 短程向量索引 / SDK agent memory）在**健康回合**上是否每回合泄漏？ | **否**。12–14 回合离线连驱，暖机后 tracemalloc 每回合净保留 **5.0 KiB**（其中 3.3 KiB 是测量脚本自身的 gc 普查），gc 类型普查里除有界缓存外**没有任何类型线性增长**，RSS 在 turn 6 之后走平（225 → 261 MiB）。 |
| 有没有找到新的真泄漏？ | **有一条，在冻结的 Harness SDK 里**：Run 在**未在驱动中**（parked/waiting）时被取消，`Runtime._cancel_run` 走 `_terminalize_cancelled` 后**从不归还本地权威** —— 每个这样的 Run 永久留下 1 条 `ExecutionLease` + 1 个 `CancelToken` + **一个一直活着的 heartbeat `asyncio.Task`**（连同协程栈帧、Task 复制的 contextvars `Context`、`TimerHandle`、`Event`、`deque`），而且那个 heartbeat 会**永远**按 `lease_ttl/3` 继续往 SQLite 续租。宿主侧没有任何公开 API 能归还它 → 记为 X2-F1。 |
| X-F3（分页缓存字节预算） | **本轮修掉**：`ContextPageInStore` 现在同时按条数（512）与字节（8 MiB）淘汰。 |
| 原生 250–300 MB/回合 能否由本轮结果解释？ | **不能**。离线（含全部 memory 车道、1 MiB 工具载荷、2048 维稠密向量车道）RSS 走平，看不到任何与载荷成正比的每回合保留。X-F2（带 `vmmap`/RSS 采样的原生定量复测）仍然是**唯一**能定这笔账的手段，本轮按纪律未启动原生 app。 |

## 2. 离线复现（不启动原生 app、不连真 provider）

装配（`backend/tests/execution/test_primary_runtime_memory_growth.py::test_multi_turn_memory_lanes_retain_bounded_memory`，
以及等价的独立进程脚本 `backend/scripts/perf/x2_memory_lanes_growth.py`，
用来拿不受 conftest 夹具污染的 RSS/gc 数据）：

| 车道 | 本轮用的东西 |
|---|---|
| 前台 runtime + SDK stack | `tests/execution/test_primary_foreground_runtime.build(dynamic=True)`：真实 `ProductSdkRuntimeStack`、真实 `context_route` / `tool_search` / `context_page_in` 适配器、真实 SQLite |
| SDK agent memory | `MemoryManager.build_development`（`context_provider` + `context_staging` + `ensure_embeddings` 追平） |
| Host 认知记忆 | 生产的 `deskpet.memory.runtime_composition.compose_human_memory_runtime`：真实 `HumanMemoryV7` store、`HostMemoryAnalysisExecutor`、`SemanticCorrectionAuthority`、prospective 车道、`ProcedureRuntime`、typed recall |
| 分析车道 | 生产的 `MemoryAnalysisLane`（`MemoryIngestionOutboxWorker` → `PrimaryShortIndexWorker` → `DurableMemoryJobRunner.run_once()`），每回合终态后驱到空闲 |
| analysis provider | 确定性 adapter，返回**合法 v9 提案**（`memory_analysis_proposal` 工具调用，一条 semantic operation），每回合真的写一条认知记忆 |
| embedder | 与 WeMM 同形状（**2048 维 / l2 归一**）的确定性稠密夹具 embedder。真 `WeMM-Embedding-2B`（2B 参数）超出本轮 3 GB 进程预算；hash/mock embedder 会被 `HumanMemoryV7Runtime.build_kwargs` 按生产口径丢弃，短程向量车道就整条不跑 |
| 每回合形状 | `context_route(route=memory_standalone, memory_types=[semantic,episode])` 真召回 → `tool_search`（128 KiB 结果 + 两份页引用）→ `context_page_in` → 收尾文本；4 次 provider 调用、3 次工具调用 |

### 2.1 一个必须记下来的复现陷阱

第一版复现里 **14 个回合全部 `CANCELLED`**，而不是 `COMPLETED`：打开 memory 车道后系统提示
变长，`tool_search` 的 128 KiB 结果被预算截断成 `typed_tool_result_summary`（**没有 `value`
字段**），夹具 provider 仍按 `results[-1]["value"]["page"]` 取页引用 → `KeyError` →
`provider_error_after_handoff` → 该次 provider 调用记 `unknown` → Host
`_reconcile_waiting_run` 判 `provider_unknown_exhausted` → `_ingress.cancel()`。
**在这条取消路径上量到的"泄漏"不是健康回合的泄漏**（见 §3）。夹具改成兼容截断摘要后
12/12 回合 `COMPLETED`，测量才成立。

### 2.2 健康路径的每回合保留（12–14 回合，暖机 4 回合）

| 指标 | 结果 |
|---|---|
| tracemalloc 每回合净保留（14 回合，128 KiB 工具载荷） | **5.0 KiB/回合**（其中 3.3 KiB 是测量脚本自己的 gc 类型普查 `Counter`） |
| 同上，1 MiB 工具载荷 + 2048 维稠密向量车道 | **14.2 KiB/回合**，最大一项是 **`simple_harness_memory/core/short_horizon.py:705` +7.1 KiB/回合** —— 认知向量精确扫描缓存 `_ExactVectorGenerationCache._matrix` 每回合多一行 2048×float32 ≈ 8 KiB，属于 O(记忆条数) 的**合法缓存**，但没有字节上限（X2-F3） |
| 进程 RSS（turn0 → turn13，独立进程、无 conftest） | 224.7 → 261.5 MiB，**turn 6 之后走平**，不随回合线性增长 |
| gc 类型普查（turn4 → turn13 每回合差） | 只有 `ContextPageInReference` +1.00、`list` +1.00、`dict` +0.89 —— 全部是有界缓存；`Task` / `Context` / `hamt` / `ExecutionLease` / `CancelToken` **均无增长** |
| SDK kernel 每 Run 表（`_leases` / `_fences` / `_cancels` / `_heartbeats` / `_live._tasks`） | 每回合末**恒为 0** |
| SDK 工具认领 `ToolRegistry.calls` | 恒为 0（事件 X 的修复在 memory 车道打开后仍成立） |
| ingestion outbox / 分析 | 每回合 `delivered` + `applied`，12 回合 12 条 `delivered`、12 次分析提案落地 |

> 口径说明：容器普查（`id(container) → 长度`）本身会保留约 30 万个元组，几十 MB，
> 会把 RSS 和 tracemalloc 全部污染。所以**普查与 tracemalloc/RSS 分两个进程跑**，
> 上表的 RSS 来自不做普查的那一次。

### 2.3 增长最大的分配点（1 MiB 载荷那次，9 回合窗口）

| 每回合 | 分配点 | 判定 |
|---|---|---|
| +7.1 KiB | `site-packages/simple_harness_memory/core/short_horizon.py:705` | **(c) 合法缓存**：认知向量精确扫描矩阵，O(记忆条数)，无字节上限 → X2-F3 |
| +3.3 KiB | 测量脚本自己的 gc 普查 | 脚手架 |
| +0.4 KiB | `simple_harness_memory/core/evidence.py:370` | 一次性/抖动 |
| +0.2 KiB | `deskpet/tools/context_page_in_tools.py:71/74` | **(c) 有界缓存**（本轮改成同时按字节设界） |
| +0.1 KiB | `simple_harness_memory/features/cognitive_vector.py:220` | 同 short_horizon 缓存 |
| 其余产品侧行 | `foreground_runtime.py:228/922`、`sdk_adapters/tool_authority.py:808` 各 +1 个对象/回合，量级 0.1 KiB | 抖动，非趋势 |

## 3. 新发现的真泄漏（在冻结 SDK 里）：取消一个 parked Run 不归还本地权威

**证据**（取消路径复现，14 回合全 `CANCELLED`）：

| 每回合 | 结构 | 末值（14 回合） |
|---|---|---|
| +1 | `Runtime._leases[run_id]`（`ExecutionLease`） | 14 |
| +1 | `Runtime._cancels[run_id]`（`CancelToken`） | 14 |
| +1 | `Runtime._heartbeats[run_id]`（**pending 的 `asyncio.Task`**，`hb_done` 恒为 0） | 14 |
| +2.0 | `coroutine`（heartbeat 协程栈帧 + 其内层） | — |
| +2.0 | `Context` + `hamt` / `hamt_bitmap_node`（Task 创建时复制的整份 contextvars） | — |
| +1 | `TimerHandle` / `Event` / `deque` / `Future` / `FutureIter` | — |
| +2.1 | `ExecutionLease`（每次续租新建一份） | — |

**根因**（`site-packages/simple_harness/runtime/kernel.py`）：

- 正常终态：`_drive()` 的 `finally`（第 2988–2994 行）在 Run 进入
  `COMPLETED/FAILED/CANCELLED` 后调 `_release_runtime_lease(run_id)` —— 会 pop
  `_leases`/`_cancels`/`_heartbeats` 并 cancel heartbeat。**这条路是干净的**（§2.2 已证）。
- 取消：`_cancel_run()`（第 2996–3035 行）非 workflow 分支先
  `request_run_cancel` → `token.cancel()` → **只有当 `run_id in self._live.active_run_ids()`
  才** `await self._live.cancel(value)`。驱动任务确实在跑时，取消会走进 `_drive` 的
  `except asyncio.CancelledError` → `_terminalize_cancelled` → 再落到那个 `finally`，正常释放；
  **但 Run 已经 parked（waiting，挂在 provider blocker 上）时驱动任务不在跑**，
  `_cancel_run` 自己在第 3033 行调 `_terminalize_cancelled(latest)` 就返回了，
  **没有任何一处调 `_release_runtime_lease` 或 `_drop_local_authority`**
  （`_terminalize_cancelled` 内部第 3078 行只在 **workflow** 分支调 `_drop_local_authority`，
  非 workflow 分支落到第 3095 行的 `_terminalize(...)` 直接返回）。
- 命令泵的取消入口 `_process_cancel_command()`（第 2196–2205 行）是**同一个形状**：
  同样的 `active_run_ids()` 条件、同样在第 2205 行调 `_terminalize_cancelled(latest)`
  后返回，同样不归还。两处都要修。

**为什么这在生产里会发生**：F06 的 provider 未知裁决链就是这条路 ——
provider 传输超时 → SDK 记 `unknown`、Run 进 waiting；Host
`ForegroundRuntimeExecutionAuthority._reconcile_waiting_run` 判
`provider_unknown_exhausted` → `_ingress.cancel(sdk_run_id)`，此时 Run **正是 parked 的**。
于是每一次"provider 未知耗尽 → 取消"都永久多留一个活 heartbeat 任务，它会一直
按 `lease_ttl/3` 往 `state` 库续租一条早就终态的 Run 的租约。

**为什么本轮不修**：`_release_runtime_lease` / `_drop_local_authority` / `_leases` /
`_heartbeats` 全是私有；kernel 的公开面（`start`/`cancel`/`reconcile`/`recover`/
`close`/`signal`/…）里**没有**任何一个会清扫终态 Run 的本地权威 —— `recover()` 只处理
可恢复 Run，`close()` 清全部但那是进程退出。宿主没有"不改 site-packages 就能归还"
的路径。按纪律记为 followup **X2-F1**，附精确要求（见 §6）。

## 4. 修复（Host 侧，本轮唯一代码改动）

`backend/deskpet/tools/context_page_in_tools.py`：`ContextPageInStore` 的上限
**从"只按条数"改成"条数 + 字节双预算"**（事件 X 的 X-F3）。

- 新增 `max_bytes`（默认 `8 MiB`）与 `retained_bytes` / `max_bytes` 只读探针；
- `put()` 先算 `content` 的 UTF-8 字节数，再按**同一条"按插入顺序淘汰最旧"**规则
  同时满足条数与字节预算；
- 单条大于整份预算时**不拒绝**：该引用正是本次请求刚发布给模型的那一条，淘汰其余后
  仍然收下它，下一次 `put` 会把它挤走 —— 拒绝会让 `context_page_in` 直接失效；
- 淘汰与 TTL 过期共用 `_drop()`，`_sizes` / `_bytes` / `_active` 一起收；TTL 语义不变。

**为什么需要**：生产里 `deskpet/agent/context_request_planner.py:648`
（`_bind_page_in_candidate`）会把**每一个** `trim_policy=page_in` 的召回片段整份塞进这个
进程内存储，模型最多 page-in 其中一个，其余全部常驻。只按 512 条设界时，最坏情况能在
进程里常驻几百 MB，而且没有任何上界。**没有**引入周期性 `gc.collect()`。

## 5. 测试

`backend/tests/execution/test_primary_runtime_memory_growth.py` 由 1 例增到 **2 例**
（事件 X 的原用例原样保留）。新增
`test_multi_turn_memory_lanes_retain_bounded_memory`：§2 的完整装配连驱 12 回合，
每回合发布一份 3 MiB 的"已准备但未被 page-in"的大页引用（正是生产
`_bind_page_in_candidate` 对每个 `page_in` 片段做的事）+ 一份小页给 `context_page_in`。

断言：

1. 车道真的跑了 —— `foreground_terminal_receipts` 恰好 `{"COMPLETED": 12}`、
   `memory_ingestion_outbox` 12 条 `delivered`、analysis adapter 被调 ≥ 12 次；
2. `ContextPageInStore` 常驻字节 ≤ 16 MiB；
3. tracemalloc 每回合净保留 < 512 KiB；
4. 事件 X 的结构性不变量仍成立：`ToolRegistry.calls` 不随回合线性增长。

| | 主干（页存只按条数设界） | 修复后 |
|---|---|---|
| 页存常驻 | `[3, 6, 9, 12, …, 36] MiB`，单调不降 → 红 | `[3, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6] MiB` → 绿 |
| 每回合净保留 | ≈ 3 MiB/回合 → 红 | **118.3 KiB/回合** → 绿 |

### 既有用例回归（一次）

只跑被改动的两处所触及的面：`ContextPageInStore` 的全部直接用例 + 两个新旧内存增长用例。

| 套件 | 结果 |
|---|---|
| `tests/test_context_page_in_tools.py` + `tests/execution/test_primary_page_in_sources.py` + `tests/execution/test_current_tool_pages.py` + `tests/test_deskpet_skill_remount_after_compaction.py` + `tests/execution/test_primary_runtime_memory_growth.py`（含事件 X 原例与本轮新例） | **24 passed in 148s**，无失败 |

`tests/execution/` 全目录本轮**未跑**：事件 X 已经逐条 diff 过该目录的失败集合，本轮改动
只碰 `ContextPageInStore` 与新增用例，触及面就是上表这五个文件（`ContextPageInStore`
在 backend 里的全部直接构造点）。

## 6. Followup

| 编号 | 位置 | 内容 |
|---|---|---|
| **X2-F1** | 冻结 SDK `simple_harness/runtime/kernel.py:3033`（`_cancel_run` 非 workflow 分支）与 `:2205`（`_process_cancel_command`），两处都在 `await self._terminalize_cancelled(latest)` 之后 | **要求**：取消一个**驱动任务不在跑**的 Run 后必须归还本地权威 —— 与 `_terminalize_cancelled` 内 workflow 分支第 3078 行的 `_drop_local_authority(run.run_id)` 对齐，或等价地在非 workflow 分支补 `_release_runtime_lease(run.run_id)`。当前行为下每个这样的 Run 永久留 1 条 `ExecutionLease`、1 个 `CancelToken`、1 个**永不结束的 heartbeat `asyncio.Task`**（含其 contextvars `Context` 快照），并持续对已终态 Run 续租。复现：本轮离线夹具 14 回合全走 provider-unknown-exhausted 取消，`Runtime._heartbeats` 单调涨到 14、其中 done 的恒为 0。 |
| **X2-F2** | 原生旅程 | **X-F2 仍未做**：离线（含全部 memory 车道、1 MiB 载荷、2048 维向量车道）RSS 走平，**解释不了**原生的 250–300 MB/回合。需要一次带 `vmmap -summary` + 分回合 RSS 采样的原生复测，并在采样里带上 `len(gc.get_objects())` 与 top tracemalloc 分配点，才能定位剩下的那部分。本轮按纪律未启动原生 app。 |
| **X2-F3** | Memory SDK 0.6.39：`simple_harness_memory/core/short_horizon.py:677-711`（`_ExactVectorGenerationCache`）、`backends/sqlite_v5.py:3179 / 17026`（`_cognitive_vector_cache` / `_short_horizon_cache`） | **要求**：精确扫描向量缓存要有**字节预算**。它已经有 `size_bytes`，但没有任何一处按它设界；矩阵是 O(记忆条数 × 维度 × 4B)，WeMM 的 2048 维下每 1 万条记忆 ≈ 80 MB 常驻，并且随记忆库单调增长。实测每回合 +7.1 KiB（新增 1 条认知记忆 = 1 行 2048×float32）。 |
| **X2-F4** | 真 embedder | 真 `WeMM-Embedding-2B` 未纳入本轮测量（2B 参数超出 3 GB 进程预算），用了同维度稠密夹具 embedder。真模型的**每回合**（非一次性权重）保留仍未验证，建议并入 X2-F2 的原生复测。 |
| **X2-F5** | 未覆盖车道 | 离线夹具没有 FastAPI/uvicorn/WebSocket，所以**广播缓冲**这条车道本轮完全没被覆盖；`provider_invocations` 的请求/响应体在离线小载荷下每回合 +0.1 KiB 量级，原生大载荷下的量级同样要靠 X2-F2 定。 |
| X-F4（沿用） | `backend/main.py:7595 / :7734` | `_sdk_unavailable_tool_authority_runs` 仍是只在进程退出时 `clear()` 的无界 set。它只在**恢复期工具权威失效**这条稀有路径上添一个短字符串，量级可忽略，且被当作 sticky 标记读（`main.py:10838`），收界要动语义 —— 维持事件 X 的判断，不做。 |
