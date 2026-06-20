# 01-architecture — 现状码级地图 + 目标架构

> 配套 [`00-PRD.md`](./00-PRD.md)。所有 file:line 均已读码核实（2026-06-21）。

---

## 1. 现状：三层子代理基建（精确地图）

### 1.1 Tier-1 单子代理 — `agent` 工具

**文件**: `backend/deskpet/tools/code_tools/agent_tool.py`

- `build_agent_tool(*, llm_shim, parent_tool_registry, parent_session_id_resolver)` → `(_handler, schema)`（`agent_tool.py:37`）
- `_handler(args, task_id)` **同步**（`:93`）：取 `description`/`prompt`/可选 `tools` → 构造 `_SubsetRegistryAdapter(parent_registry, tool_subset)`（`:122`）→ 2 条消息（system framing + user prompt，`:124-137`）→ `AgentLoop(llm_registry=llm_shim, tool_registry=adapter, max_iterations=_SUBAGENT_MAX_ITERATIONS)`（`:139-143`，**15 硬编码 `:33`**）→ session id = `<parent>.sub`（`:146`）
- 异步桥（`:162-173`）：若已在 event loop → `asyncio.run_coroutine_threadsafe(_run(), loop).result(timeout=600)`；否则 `asyncio.run()`
- 返回 `json.dumps({"result": final_text, "tools": tool_subset})`（`:175`）
- **递归 guard**：`_SubsetRegistryAdapter`（`:180`）在 schema 暴露（`:195`）与 execute（`:208-212`）双层剔除非允许工具；默认子集 `_DEFAULT_READONLY_TOOLS=("read_file","list_directory","glob","grep","web_search")`（`:34`）

> **要改的点（P1/P4）**：`build_agent_tool` 加 `default_max_iterations` + `default_tool_subset` 参数（现在 15 + 只读硬编码）；P4 加可选 `termination_gate`。

### 1.2 Tier-2 并行子代理 — `agent_parallel` 工具

**文件**: `backend/deskpet/tools/code_tools/agent_parallel_tool.py`

- `build_agent_parallel_tool(*, llm_shim, parent_tool_registry, parent_session_id_resolver, subagent_runner=None, parent_system_prompt_resolver=None)` → `(_handle, _SCHEMA)`（`:254`）
- `_handle(args, task_id)`（`:291`）：校验 `subagents` 是 2–4（`_MIN_SUBAGENTS=2`/`_MAX_SUBAGENTS=4`，`:50-51`）→ 归一化 → `asyncio.gather(*[_run_one(sa) for sa in normalised])`（`:405`，**真并发、扁平、无背压**）
- `_run_one(sa)`（`:355`）：算 cache_mode/hash → `_build_sprint_contract(sa)`（`:199`，注入 task_id/input_files/output_files/forbidden_files/success_criteria JSON）→ `_filter_subagent_tools` 剔除 `_FORBIDDEN_NESTED_TOOLS={"agent","agent_parallel"}`（`:53`）→ `runner(sa_for_runner, task_id)` → 进度 `_emit_progress`（`:221`，**只写 metrics.jsonl，不推 WS**）→ 返回 `{task_id, ok, output, cache_mode, system_prompt_hash}`
- 默认 runner `_make_default_runner`（`:421`）：每子代理新建 `build_agent_tool` 闭包 + sid `<parent>.par-<task_id>`（`:442`）+ `loop.run_in_executor(None, _invoke)`（`:471`，同步 handler 丢线程池不阻塞 event loop）
- envelope 返回 `{ok, elapsed_ms, count, results:[...]}`（`:408`）

> **要改的点（P1）**：`_SCHEMA` 加每子代理 `kind` 字段 + `_MAX_SUBAGENTS`→8；`build_agent_parallel_tool` 加 `scheduler`/`kind_resolver`/`progress_broadcaster` 参数；`_run_one` 经 `scheduler.run(kind=...)` 跑；`_make_default_runner` 按 KindProfile 给 iter/工具子集。

### 1.3 Tier-3 团队/任务图 — `spawn_team` + `TeamStore` + `TaskGraphStore`

**文件**: `backend/deskpet/agent/team/spawn_team.py` · `team_store.py` · `teammate_tools.py` · `backend/deskpet/agent/task_graph.py`

- `spawn_team(*, team_id, task_descriptions, num_teammates=3, store, teammate_runner=None, parent_tool_registry, llm_shim, parent_session_id, timeout_seconds=300.0, parent_goal_text=None, parent_goal_id=None, task_graph_store=None)` → `{ok, team_id, elapsed_ms, timed_out, results, aligned, flagged}`（`spawn_team.py:159`）
  1. seed 池：`store.create_task(team_id, desc)`（`:222`）
  2. 并发 N teammate：`asyncio.gather(*teammate_coros)` 包 `asyncio.wait_for(timeout)`（`:261`）
  3. 每 teammate：`_build_charter`（claim→work→update 循环指令 + 父目标锚 `:95`）+ `build_teammate_tools(store, team_id, teammate_id, task_graph_store, goal_id)` + `AgentLoop(max_iterations=30)`（`:356`）+ sid `<parent>.team-<team_id>.<teammate_id>`（`:361`）
  4. `_TeamSubsetRegistry`（`:379`）：父只读工具 ∪ 5 个 team 工具，剔除 `FORBIDDEN_TEAMMATE_TOOLS={"agent","agent_parallel","spawn_team"}`（`teammate_tools.py:42`）
- 上限 `_MIN_TEAMMATES=1`/`_MAX_TEAMMATES=8`/默认 3（`:50-52`）
- `TeamStore(base_dir)`（`team_store.py:198`）：每 team 一个 `.db`，3 表（tasks/messages/permissions），WAL + `BEGIN IMMEDIATE` 原子 `claim_task`（`:262`）；`create_task`（`:248`）/`request_permission`（`:460`）/`grant_permission`（`:485`）
- `build_teammate_tools(...)`（`teammate_tools.py:176`）：5 工具（task_create/claim/update/list + send_message）+ 可选 2（goal_task_list/update，当 task_graph_store+goal_id 都给时）
- `TaskGraphStore(db: SessionDB)`（`task_graph.py:106`）：`create`（`:109`，DFS 环检测）/`claim_ready`（`:152`，依赖全 done 才 claim）/`update`（`:166`，done 触发 goal backfill）；节点 `TaskNode`（`:29`）

> **要改的点（P2）**：新建 `spawn_team_tool.py` 工厂暴露为 LLM 工具；`registration.py` 加 slot；`main.py` lifespan 构造 `TeamStore`/`TaskGraphStore`/注入 `service_context`；`features.agent_team` flag。

### 1.4 接线现状 — `main.py`

- `register_code_tools(registry, *, todo_write_handler, todo_write_schema, agent_handler, agent_schema, agent_parallel_handler, agent_parallel_schema)`（`registration.py:47`）；两阶段调用（`main.py:366` 最小集 / `main.py:1950` 全集闭包）
- `main.py:1907` `_shim_for_agent = _ShimForAgent(provider=local_llm)`（`_ShimForAgent` = `OpenAICompatibleAgentLLM` 的 import 别名 `:1843`；`local_llm` 是 `OpenAICompatibleProvider` `:254`，**非 shim**）【R1:F8】；`:1909` `_resolve_parent_sid()`；`:1917` 构造 `agent` 工具；`:1927-1944` flag-gated 构造 `agent_parallel`；`:1950-1958` re-register 全集
- `features.agent_parallel=False`（`config.py:416`）；`FeaturesConfig`（`config.py:394`）；`[agent]` 段走 `AppConfig.raw["agent"]` 直读（`config.py:449` 注释）

### 1.5 ReAct 主循环 — `agent_loop.py`

- `AgentLoop.run()`（`backend/agent/agent_loop.py:581`）async generator；每轮 gate→budget→compaction→self-check→LLM→若 `tool_use`：`asyncio.gather(*[self._dispatch_tool(tc,...)], return_exceptions=True)`（`:2009`）→ `ToolResultEvent`；否则 4 道 finish-guard → `FinalEvent`
- `_dispatch_tool`（`:2276`）→ `self.tools.execute_tool(name, args, session_id, task_id)`（v2 envelope）

> **要改的点（P3）**：`run()` 每轮顶部 drain `SubagentRegistry.completion_queue` → 注入 completion 作 user/tool 消息（回合边界）；`/stop`/cancel 路径级联 `registry.cancel_all()`。

---

## 2. 目标架构

```
                         ┌─────────────────────────────────────────────┐
   用户请求（多事务）──▶  │            主 AgentLoop (ReAct)               │
                         │   收到含多种事务的请求 → LLM 识别 → 调用      │
                         └───┬──────────────┬───────────────┬──────────┘
            spawn_subagents  │   agent_parallel │   spawn_team │
            (P3 非阻塞)      │   (P1 异构并发)   │  (P2 同构池) │
                         ┌───▼──────────────────▼──────────────▼───┐
                         │      SubagentDriver 层（本计划新增）      │
                         │ ┌───────────────┐  ┌───────────────────┐ │
                         │ │ task_kinds.py │  │ subagent_scheduler│ │
                         │ │ KindProfile   │  │ lane semaphores   │ │
                         │ │ research/code │  │ global cap + 背压  │ │
                         │ │ /doc/web/...  │  │ 进度 emit         │ │
                         │ └───────────────┘  └───────────────────┘ │
                         │ ┌────────────────────────────────────┐   │
                         │ │ subagent_registry.py (P3)          │   │
                         │ │ run 记录 + Task 句柄 + completion_q  │   │
                         │ └────────────────────────────────────┘   │
                         └───┬────────────────────────────────────┬─┘
              复用 build_agent_tool (每子代理一个 AgentLoop)        │
                         ┌───▼──────────┐   ┌──────────────┐   ┌───▼────────┐
                         │ research 子  │   │ doc 子代理    │   │ team teammate│
                         │ (web/fetch)  │   │ (ppt/doc/xls)│   │ (claim 池)  │
                         └───┬──────────┘   └──────┬───────┘   └──────┬─────┘
                             │ summary               │ summary         │ results
                         ┌───▼───────────────────────▼─────────────────▼──┐
                         │  结果聚合 → 父上下文只回摘要 + WS 进度广播       │
                         │  (P3: 回合边界注入 completion；P1/P2: 阻塞聚合) │
                         └──────────────────────┬──────────────────────────┘
                                   WS subagent_progress (P1/P3)
                         ┌──────────────────────▼──────────────────────────┐
                         │  前端 SubagentProgressPanel (P3) + 桌宠"忙"状态   │
                         └───────────────────────────────────────────────────┘
```

---

## 3. 新模块与数据契约

### 3.1 `KindProfile`（`task_kinds.py`）

```python
@dataclass(frozen=True)
class KindProfile:
    kind: str                       # "research" | "code" | "fileops" | "doc" | "web" | "general"
    tools: tuple[str, ...]          # 该 kind 默认工具子集（已剔除 spawn 类）
    max_iterations: int             # 子代理 iter cap
    framing: str = ""               # 注入子代理 system prompt 的角色 framing
    model: str | None = None        # 可选模型覆盖（P4）
    lane_concurrency: int = 2       # 该 kind 的 lane 并发 cap
```

内置 6 个（工具名已对真实注册表核实，见 02 §P0-WI-0.1）。

### 3.2 调度契约（`subagent_scheduler.py`）

```python
class SubagentScheduler:
    def __init__(self, *, global_concurrency: int, lane_caps: dict[str,int],
                 progress_sink: Callable[[dict], None] | None = None): ...
    async def run(self, *, kind: str, run_id: str,
                  coro_factory: Callable[[], Awaitable[Any]]) -> Any:
        # acquire global sem → acquire kind-lane sem → emit "running"
        #   → await coro_factory() → emit "completed/failed" → release
    def snapshot(self) -> dict:  # {running, queued, by_lane}
```

### 3.3 进度事件契约（WS + metrics）

```jsonc
// WS broadcast type="subagent_progress"
{ "type": "subagent_progress",
  "payload": { "run_id": "...", "kind": "research", "task_id": "...",
               "status": "queued|running|completed|failed",
               "parent_sid": "...", "summary": "...", "ts": 1750000000.0 } }
```
`metrics_sink.VALID_EVENTS` 已含 `subagent_progress`（复用）+ 新增 `subagent_scheduled`。

### 3.4 run 记录契约（`subagent_registry.py`，P3）

```python
@dataclass
class SubagentRun:
    run_id: str
    kind: str
    task_id: str
    status: str                     # queued|running|completed|failed|cancelled
    task: asyncio.Task | None       # 用于 cancel 级联
    summary: str = ""
    stats: dict = field(default_factory=dict)   # iters, duration_ms, tokens
    error: str | None = None
```
`SubagentRegistry`：`register/update/get/list/cancel_all` + `completion_queue: asyncio.Queue`（完成的 run 入队，agent_loop 回合边界 drain）。

### 3.5 spawn 工具 LLM 接口（汇总）

| 工具 | flag | 入参（关键） | 返回 | 阻塞? |
|---|---|---|---|---|
| `agent`（现有） | 隐含 | description, prompt, tools? | `{result, tools}` | 是 |
| `agent_parallel`（P1 扩展） | `subagent_driver` | subagents[{task_id, prompt, **kind**, tools?, input/output/forbidden_files?, success_criteria?}], cache_mode? | `{ok, elapsed_ms, count, results[]}` | 是 |
| `spawn_team`（P2 新） | `agent_team` | task_descriptions[], num_teammates?, **kind?**, timeout_seconds? | `{ok, team_id, results[], aligned, flagged}` | 是 |
| `spawn_subagents`（P3 新） | `subagent_nonblocking` | subagents[{task_id, prompt, kind, ...}], background? | `{ok, run_ids[]}` 立即 | 否 |
| `await_subagents`（P3 新） | `subagent_nonblocking` | run_ids?（省略=全部） | `{ok, results[]}` | 结束回合等 |
