# 事件 X：后端进程每回合内存增长（离线复现 + 根因修复）

- 日期：2026-09-09
- 分支：`worktree-mem-growth`（未合并）
- 触碰文件：`backend/deskpet/sdk_adapters/tools.py`（+65 行）、
  新增 `backend/tests/execution/test_primary_runtime_memory_growth.py`

## 1. 现象与已有证据

原生旅程里 `backend/.venv/bin/python main.py`（Tauri 的子进程）每个 provider 回合稳定长胖：

| 证据 | 观测 |
|---|---|
| Manual 旅程 run3（7 回合） | RSS 0.94 → 1.23 → 1.73 → 2.41 → 2.61 → 2.86 GB，暖机后约 +250 MB/回合 |
| 07:30 a6 旅程（24 回合）+ 语料批次 | app 家族 17.4 GB，整机被拖垮 |
| `vmmap -summary`（后端 2.7 GB 时） | **MALLOC_SMALL 2.1 GB 常驻**、MALLOC_LARGE 仅 31 MB、映射 `__TEXT/__LINKEDIT` ~400 MB；38 线程、444 打开文件 |

`vmmap` 已经把方向钉死：**是 Python 对象堆积，不是模型权重、不是大块 mmap。**

## 2. 离线复现（不启动原生 app、不连真 provider）

用与生产同一套装配（`tests/execution/test_primary_foreground_runtime.py::build` 的 `dynamic=True`
分支：真实 `context_route` / `tool_search` / `context_page_in` 适配器、真实 SDK stack、真实 SQLite），
把**同一个** runtime + stack 连驱 N 个回合，每回合 4 次 provider 调用、3 次工具调用
（`context_route` → `tool_search`（附带一份 Host 发布的分页引用）→ `context_page_in` → 收尾文本）。

测量三件事：每回合 `tracemalloc` 净保留、`gc` 对象普查（按类型与容器长度做差）、
以及对可疑结构的显式长度探针。retention root 用 `gc.get_referrers` 反查持有者。

### 2.1 每回合增长（离线 14 回合，工具结果载荷 128 KiB）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| SDK `ToolRegistry._calls` 条数（回合末） | 2 → 28，**+2/回合，单调不降** | **恒为 0** |
| `gc` 对象数（turn3 → turn13） | 149517 → 149737（**+22/回合**） | 149330 → 149118（**≈0，无趋势**） |
| 进程 RSS（turn0 → turn13） | 180.3 → 189.7 MiB | 180.5 → 189.7 MiB |
| `tracemalloc` 每回合净保留（回归用例：驱 12 回合，暖机后 8 回合窗口） | **151.5 KiB/回合** | **13.2 KiB/回合** |

RSS 在这个夹具上两边一样，**这不是"没修好"**：离线夹具的工具载荷只有 128 KiB 量级，
每回合泄漏 ~256 KiB，落在 macOS malloc arena 的抖动里；RSS 曲线在两边都被 SQLite
页缓存与一次性暖机（turn 3→4 的 +6.4 MiB）主导。真正区分修复前后的是**保留量**指标
（`_calls` 条数、gc 对象数、tracemalloc 净保留），三者都从"每回合线性增长"变成"平"。
增长量与工具结果载荷成正比，所以原生旅程（整份 `tool_search` 目录、分页 effect page、
文件读取，且每回合工具调用数远多于 3）才会放大到几百 MB/回合。

### 2.2 增长最大的分配点（修复前，tracemalloc `lineno`，12 回合窗口）

| 每回合增长 | 分配点 | 说明 |
|---|---|---|
| 42.0 KiB | `python3.12/json/decoder.py:354` | 被保留的工具结果 JSON |
| 32.1 KiB | `site-packages/simple_harness/contracts/json.py:72` | `freeze_json` 冻结映射 |
| 19.0 KiB | `site-packages/simple_harness/contracts/json.py:82` | 同上（tuple 分支） |
| 3.7 KiB | `site-packages/simple_harness/execution/provider_invocations.py:350` | provider 请求/响应体 |
| 3.2 KiB | `site-packages/simple_harness/contracts/json.py:74` | 同上 |
| 3.0 KiB | `site-packages/simple_harness/providers/base.py:23` | `ProviderToolSpec` |
| 1.3 KiB | `site-packages/simple_harness/tools/registry.py:194` | `_CallRecord` + dispatch Task |

（该次带 `tracemalloc` 的复现跑用的是小载荷、且夹具 provider 自己留了 `requests` 列表，
所以 `contracts/json.py` 与 `providers/base.py` 两行含夹具自身的保留；`json/decoder.py`
与 `tools/registry.py` 两行是产品侧的。后续把夹具改成不留请求后，`gc` 普查里
产品侧每回合仍有 +3 `_CallRecord`/`Task`/`ToolResult`，见 2.3。）

### 2.3 保留根（`gc.get_referrers` 反查）

| 每回合 | 容器 | 持有者 | 判定 |
|---|---|---|---|
| +3 条 | `dict[CallId, _CallRecord]` | **`deskpet.sdk_adapters.tools.ProductToolsAdapter`**（即 SDK `ToolRegistry._calls`） | **(b) 泄漏** |
| +3 个 | `_asyncio.Task` / `coroutine` / `ToolContext` / `ToolResult` / `TaskExecutionEnvelope` | 同上（经 `_CallRecord.task`） | **(b) 泄漏（同一根）** |
| +6.6 个 | `_contextvars.Context` + `hamt` | 同上（Task 复制的 contextvars 快照） | **(b) 泄漏（同一根）** |
| +1 个 | `_ForegroundEffectBinding` | 同上（协程栈帧局部变量）——网关自己已 `release()`，`effect_gate_bindings` 探针恒为 0 | **(b) 泄漏（同一根的次级保留）** |
| +3 个 | `WeakSet` of Task、`loop._scheduled` 的 `TimerHandle` | asyncio 内部，随上面的 Task 一起消失 | **(b) 泄漏（同一根的次级保留）** |
| +1 条 | `ContextPageInStore._records` / `_active` | `deskpet.tools.context_page_in_tools.ContextPageInStore` | **(c) 合法有界缓存**（512 条 / 300s TTL） |
| 0 | `SdkRunToolAuthorityRegistry._records` / `_runtime_exposures` / `_unavailable_capabilities` / `_project_effect_capabilities`、`ToolCapabilityScopeStore._records/_locks/_pins`、`ForegroundEffectAdmissionGate._bindings` | — | 终态已正确释放，探针恒为 0 |
| — | embedding / torch | 本次离线夹具未启用 agent memory，无此项 | **(a) 一次性，不在本轮范围** |

## 3. 根因

冻结 SDK 的 `simple_harness/tools/registry.py`：

- `ToolRegistry.invoke()` 第 196 行 `self._calls[call.call_id] = _CallRecord(RUNNING, task)`；
- 只有 `allow_confirmed_not_started(call_id)`（第 243–250 行）会 `del self._calls[call_id]`，
  且它只在"外部持久证据确认未启动"这一条路径上被调用；
- 结算成功的调用**永远留在表里**。

产品侧 `ProductToolsAdapter` 继承 `ToolRegistry`，由 `ports_factory` 每个 SDK runtime stack
建一次（`main.py:8663 tools_adapter.bind_run_authorities(tool_authorities)`），
**生命周期 = 后端进程生命周期**。于是进程每做一次工具调用就永久多留一条 `_CallRecord`，
而每条 `_CallRecord` 吊着：

- 已完成的 `asyncio.Task` → 协程栈帧（局部变量含整份工具结果、`_ForegroundEffectBinding` 等）；
- Task 创建时**复制的整个 contextvars `Context`**（hamt 树，钉住 `ToolContext`、
  当前 Run 的工具上下文等）；
- `ToolResult.value` 的完整载荷（`tool_search` 结果、分页内容、文件读取结果……）。

这正是 `vmmap` 看到的 MALLOC_SMALL 形状：大量中小 Python 对象，随回合线性增长，永不回落。

## 4. 修复

不改 site-packages。修复落在 Host 自己的 `ProductToolsAdapter`
（`backend/deskpet/sdk_adapters/tools.py`），用的是 SDK **公开**接口：

1. `invoke()` 里记住 `call_id → sdk_run_id` 归属（`self._run_calls`）；
2. `bind_run_authorities()` 里向 `SdkRunToolAuthorityRegistry.add_terminal_listener` 注册监听器
   —— 这是产品里既有的 Run 终态钩子（`ProductPreparedAuthorizationFacts._release_run`、
   main.py 的 route-state memo / project-binding 释放都挂在同一处）；
3. Run 到终态时 `release_run_calls(run_id)`：对该 Run 的每个 call，读公开属性
   `ToolRegistry.calls`（`MappingProxyType[CallId, ToolCallState]`），**只对非 RUNNING 的记录**
   调用公开的 `allow_confirmed_not_started(call_id)` 归还本地认领；
4. `_trim_run_calls()` 兜底上限 `_MAX_TRACKED_RUNS = 64`：不经 Tool authority 终态的历史聊天
   入口不会触发监听器，这里按插入顺序只归还更早 Run 的**已结算**认领，任何入口都不再无界。

**为什么安全**：`_calls` 完全是进程内状态，不参与任何持久化、指纹、回执或重放；
终态时该 Run 的终态回执已落库，本地认领不再有仲裁价值。RUNNING 记录一律不动
（取消仍走 SDK 自己的 `close_call`，`allow_confirmed_not_started` 也会再拦一道）。
没有加周期性 `gc.collect()`。

## 5. 测试

新增 `backend/tests/execution/test_primary_runtime_memory_growth.py`（1 例）：
离线连驱 12 回合、每回合 3 次工具调用（含 `tool_search` 与分页 + `context_page_in`），
按 tracemalloc 断言每回合净保留 < 48 KiB，并断言工具认领数不随回合线性增长。

| | 主干（未修复） | 修复后 |
|---|---|---|
| 每回合净保留 | **151.5 KiB** → 红 | **13.2 KiB**（三连跑 13.3 / 13.1 / 13.2）→ 绿 |
| 每回合末认领数 | `[2,4,6,…,24]` → 红 | `[0]*12` → 绿 |

测量口径两点说明（都写进了用例注释）：

- 工具结果的填充串**每次新建**。复用同一个常量对象的话，被保留的 `ToolResult` 只是多一个
  引用，tracemalloc 量不到增长（实测只有 17.5 KiB/回合，测不出泄漏）。
- 快照按 `tracemalloc.Filter` 排除 `aiosqlite/` / `threading.py` / `_weakrefset.py` /
  `tests/conftest.py` / `tracemalloc.py`。这些是**测试脚手架**噪声：conftest 的 autouse
  夹具 `_track_aiosqlite_connections` 会把每个 aiosqlite 连接（连同工作线程）钉到用例结束，
  每回合 ~2 个已关闭连接被算成增长（不加过滤时残留 269 KiB/回合，全部来自这里）。
  独立进程复现（无 conftest）里这些分配点是**负增长**，说明产品自己按时关闭了它们。
  泄漏本身分配在 `json/decoder.py`、`simple_harness/contracts/json.py`、
  `simple_harness/tools/registry.py`，一个都不在排除表里。

### 既有用例回归（一次）

| 套件 | 结果 |
|---|---|
| `tests/sdk_adapters/` 的 7 个 tool/catalog/disclosure 用例文件 | **115 passed** |
| `tests/execution/test_primary_foreground_runtime.py` | **19 passed** |
| `tests/sdk_adapters/test_product_host_ports.py` + `tests/test_delivery_sink.py` | **45 passed** |
| `tests/test_execute_sdk_run.py` + `tests/test_provider_runtime_refresh.py` + `tests/quality/test_nullable_installed_main.py` | 45 passed / **3 failed（主干同样红）** |
| `tests/execution/` 全目录 | 修复后失败集合 50 项、主干失败集合 51 项，**逐条 diff：修复未引入任何新失败**（主干多出的 1 项 `test_scope_disclosure_runtime.py::…[False-search]` 是抖动） |

这些既有失败与本改动无关：`test_provider_runtime_refresh` / `test_nullable_installed_main`
都停在 `RuntimeError: Memory SDK candidate installed version mismatch`（本 worktree 钉的
Memory SDK 版本与共享 venv 里装的 0.6.34 不一致），`tests/execution/test_current_tool_megabyte.py`
在主干同样红。

## 6. Followup（**不在本轮修改**）

| 编号 | 位置 | 内容 |
|---|---|---|
| X-F1 | 冻结 SDK `simple_harness/tools/registry.py:196`（写入）/ `:243-250`（唯一删除路径） | `ToolRegistry` 自身应在 Run 终态或结算后回收 `_calls`，而不是要求每个宿主自己兜。本轮按纪律只在 Host 侧释放，未改 site-packages。 |
| X-F2 | 原生旅程 | 本轮不允许启动原生 app，因此**未验证**这一处修复能覆盖 250 MB/回合的多大比例。需要一次带 `vmmap`/RSS 采样的原生复测来定量。 |
| X-F3 | `backend/deskpet/tools/context_page_in_tools.py:56` | `ContextPageInStore` 按**条数**（512）而非**字节**设上限；分页内容可以很大，最坏情况仍能占几百 MB。建议改成字节预算。 |
| X-F4 | `backend/main.py:7561` / `:7700` | `_sdk_unavailable_tool_authority_runs: set[str]` 只在进程退出时 `clear()`，每个"工具授权不可用"的 Run 永久留一条 id。量很小（每 Run 一个短字符串）但确实无界；它被当作 sticky 标记读（`main.py:10778`），收界要动语义，未做。 |
| X-F5 | 离线夹具 | 本轮复现未启用 agent memory（`memory=None`），所以 embedding 追平批次、分析出站箱状态、审计行这几条车道**没有被覆盖**。需要一轮 `MemoryManager.build_development` 打开后的同样测量。 |
