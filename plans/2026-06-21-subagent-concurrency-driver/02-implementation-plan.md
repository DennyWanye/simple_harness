# 02-implementation-plan — 码级实施细节（P0–P4）

> 配套 [`00-PRD.md`](./00-PRD.md) / [`01-architecture.md`](./01-architecture.md)。
> 每个 WI 给出 **改哪个文件、哪个函数、什么签名、什么逻辑**。所有现有 file:line 已读码核实。
> 纪律：每 WI 先红后绿（TDD，见 [`03-TDD.md`](./03-TDD.md)）；所有新行为 flag-gated，OFF=BC 字节级。
>
> **版本 v0.2** — 已吸收[第 1 轮对抗评审](#附录-评审修订记录) 全部 4 BLOCKING + 9 MAJOR/MINOR + 7 缺口（F1–F14 + gap1-7）。修订点在每 WI 标 `【R1:Fx】`。

---

## 关键事实基线（评审核实，写码前必须遵守）

| 事实 | 真相（file:line） | 影响 |
|---|---|---|
| `service_context` 不是 dict | `ServiceContext` dataclass 实例（`context.py:75`），用 `.register(name, x)`/`.get(name)`，**校验 `_VALID_SERVICES`**（`context.py:121-132`），未知名 raise `ValueError` | 新服务名必须先加进 `_VALID_SERVICES` + dataclass 字段（WI-0.4），再 `register/get` |
| goal 服务槽名 | `session_goal_store`（`context.py:51,109`），非 `goal_store`；取目标文本用 `SessionGoalStore.get_goal_text(session_id)`（`goal_store.py:195`）；**无 goal_id 裸 getter** | WI-2.4 goal resolver 按此写 |
| 用户数据目录 | `_paths.user_data_dir()`（`main.py:74` import as `_paths`，`:99`/`:149` 用法），**无 `user_data_dir` 局部变量** | WI-2.4 用 `Path(_paths.user_data_dir())` |
| LLM shim 不能换 model | `_ShimForAgent`（=`OpenAICompatibleAgentLLM` 别名，`main.py:1843`）`__init__(provider)` **忽略 model**；`OpenAICompatibleProvider` 的 model ctor 期锁定不可变（`main.py:254` 用法） | WI-4.2 必须**新建 provider**（带 model）再包 shim |
| metrics 键白名单 | `_ALLOWED_DETAIL_KEYS`（`metrics_sink.py:90`）含 `task_id`/`status`，**不含 `run_id`/`kind`/`duration_ms`** | WI-1.5 必须把这 3 键加进白名单 |
| 中断/取消路径 | `chat_v2_interrupt` handler（`main.py:4577`）→ `_chat_inflight[sid].cancel()`（`:4589`）；AgentLoop **无 cancel/stop 方法**；`spawn_subagents` 的 detached task 不属 chat task | WI-3.3 取消级联必须挂进 `:4589` 后 |
| `config.config` 单例不存在 | `config` 是 module-level（`main.py:94`）；`[agent]` 段纯 raw-read（无 `_load_section`）；`[features]` 走 `_load_section`（`config.py:1070,1096`）✓ | WI-0.3：3 个 bool 进 dataclass；`[agent.*]` 走 `cfg.raw["agent"]` + getattr 兜底（防 b05823b R2）。**无须改白名单** |
| metrics 已有 `subagent_progress` | `VALID_EVENTS`（`metrics_sink.py:71`）已含 | 只需新增 `subagent_scheduled` |

---

## 阶段总览与依赖

```
P0 (新模块+context槽, 零接线) ──┬─▶ P1 (agent_parallel 路由) ──┬─▶ P3 (非阻塞+queue+取消+UI)
                               └─▶ P2 (spawn_team 暴露+接线) ─┘
                                                              └─▶ P4 (守门平价+模型路由)
```
P0 先做。P1 与 P2 不同文件可并行。P3 依赖 P1+P2。P4 收尾。

---

# P0 — 基础模块（新增 + context 槽位，零行为变更）

## WI-0.1 — `task_kinds.py` 事务分型注册表

**新建** `backend/deskpet/agent/task_kinds.py`：`KindProfile` dataclass + 6 内置 kind（general/research/code/fileops/doc/web）+ `resolve_kind`/`known_kinds`/`load_kind_overrides`。

```python
_FORBIDDEN_IN_KIND = frozenset({"agent","agent_parallel","spawn_team","spawn_subagents","await_subagents"})

@dataclass(frozen=True)
class KindProfile:
    kind: str; tools: tuple[str,...]; max_iterations: int
    framing: str = ""; model: str | None = None; lane_concurrency: int = 2

_BUILTIN_KINDS = {  # 工具名全部对真实注册表核实（见 §关键事实 / 评审 verified）
  "general":  KindProfile("general", ("read_file","list_directory","glob","grep","web_search"), 15, "通用只读子代理。", lane_concurrency=2),
  "research": KindProfile("research", ("web_search","web_fetch","deepresearch","read_file"), 12, "调研子代理：检索权威源、交叉验证。", lane_concurrency=2),
  "code":     KindProfile("code", ("read_file","write_file","edit_file","glob","grep","run_shell","list_directory"), 20, "编码子代理：读现状→改→自检。", lane_concurrency=2),
  "fileops":  KindProfile("fileops", ("read_file","write_file","glob","grep","list_directory"), 12, "文件操作子代理。", lane_concurrency=3),
  "doc":      KindProfile("doc", ("ppt_create","doc_create","excel_create","read_file","web_search","generate_image"), 15, "文档生成子代理：必报产物路径。", lane_concurrency=1),
  "web":      KindProfile("web", ("web_search","web_fetch"), 8, "联网快查子代理。", lane_concurrency=3),
}
_DEFAULT_KIND = "general"
# resolve_kind(name, overrides=None) → 未知回退 general（只读安全 R5）；返回 profile.tools 已 _strip_forbidden（递归守门=工具剥离=保证 depth-1，见 00-PRD §5.2【R1:gap3】）
# load_kind_overrides(raw_agent_cfg) → 合并 config.raw['agent']['subagent_kinds']；坏覆盖逐条 try/except 跳过；None→内置默认（不抛，R2）
```
**测试** TG-0.1。

## WI-0.2 — `subagent_scheduler.py` 有界并发调度器

**新建** `backend/deskpet/agent/subagent_scheduler.py`：`SubagentScheduler(*, global_concurrency=4, lane_caps, progress_sink=None)`，`async run(*, kind, run_id, task_id, parent_sid, coro_factory)` 双闸 `async with self._global: async with self._lane(kind):`，发 queued→running→completed/failed 进度，`snapshot()`。

**【R1:F9】** `run()` 在「running」转换处加日志锚点（manual test 可 grep）：
```python
log.info("subagent_scheduled kind=%s run_id=%s task_id=%s", kind, run_id, task_id)
```
**【R1:F10】** 见 WI-1.3：scheduler 跑的 coro 必须是**原生协程子代理**（不再 thread-bounce），否则每子代理占满一个线程池 worker 整个生命周期（最长 600s），global cap=4 + fanout=8 会与进程内其它 `run_in_executor` 抢线程。

**测试** TG-0.2。

## WI-0.3 — 配置 flag + 并发段读取

**改** `backend/config.py`：
1. `FeaturesConfig`（`:394`）加 3 bool（默认 False）：`subagent_driver`/`agent_team`/`subagent_nonblocking`（走 `_load_section`，自动生效，无须白名单）。
2. helper `get_subagent_concurrency(cfg) -> (int, dict)`：`raw = (getattr(cfg,"raw",None) or {}).get("agent",{})` → 读 `concurrency.global_concurrency`(默认4)/`lane_caps`(默认 6 kind)。**必须 getattr 兜底**（防 b05823b R2）。
3. **【R1:F12】** ~~加白名单~~ 删除 — `[agent]` 纯 raw-read 不触发 warn，`[features]` 是 dataclass 字段，均无须改白名单。

**测试** TG-0.3（含 0.3.3 真 config 读到 lane_caps = b05823b 回归；0.3.4 无 `.raw` stub 不抛）。

## WI-0.4 —【R1:F1/F6】context.py 注册新服务槽

**改** `backend/context.py`：
- `_VALID_SERVICES` frozenset（`:9`）加 4 名：`"subagent_scheduler"`, `"subagent_registry"`, `"team_store"`, `"task_graph_store"`
- `ServiceContext` dataclass（`:75`）加 4 字段：`subagent_scheduler: Any|None=None` 等
- 注释说明（仿 `session_goal_store` 注释 `:48`）：flag OFF 时不 register，但加进 whitelist 防 register(None) 占位时 raise

**测试** TG-0.4：`ServiceContext().register("subagent_scheduler", obj)` 不抛；`.get("subagent_scheduler")` 返回 obj；未知名仍 raise。

---

# P1 — agent_parallel 路由进分型+调度（异构并发）

## WI-1.1 — `build_agent_tool` 支持 iter/工具子集/framing 参数

**改** `agent_tool.py`：`build_agent_tool(..., default_max_iterations=_SUBAGENT_MAX_ITERATIONS, default_tool_subset=_DEFAULT_READONLY_TOOLS, default_framing="")`（默认=现状常量→BC）。`_handler`（`:93`）无 args.tools 时用 `default_tool_subset`；`AgentLoop(max_iterations=default_max_iterations)`；framing 拼进 system 消息（`:128`）。**测试** TG-1.1（1.1.1 默认调用字节级一致 BC）。

## WI-1.2 — `agent_parallel` schema 加 `kind` + 提 fan-out 上限

**改** `agent_parallel_tool.py`：`_MAX_SUBAGENTS=4`→`8`（`:51`）；`_SCHEMA` subagents.items.properties 加 `kind` enum（6 值，默认 general）；description 补 kind 说明。

## WI-1.3 — `build_agent_parallel_tool` 接调度器 + 分型 +【R1:F10】原生协程 runner

**改** `agent_parallel_tool.py`：
- 签名加 `scheduler=None, kind_overrides=None`。
- `_run_one(sa)`（`:355`）：`prof = resolve_kind(sa.get("kind"), overrides=kind_overrides)`；run_id = `<parent>.par-<task_id>`；effective_tools = 显式 `_filter_subagent_tools(sa.tools)` 否则 `list(prof.tools)`；包 `coro_factory=_do`；`if scheduler: output = await scheduler.run(kind=prof.kind, run_id=..., task_id=..., parent_sid=..., coro_factory=_do)` else `output = await _do()`（**scheduler=None → 现状扁平 gather BC**）。
- **【R1:F10】 新增 `_make_async_native_runner`**（scheduler 路径专用）：不再 `loop.run_in_executor` 调 agent_tool 同步 handler，而是**直接在本协程内构造并 await 子 AgentLoop**：
  ```python
  from agent.agent_loop import AgentLoop, FinalEvent, ErrorEvent
  from .agent_tool import _SubsetRegistryAdapter
  async def _runner(sa_for_runner, sa_task_id):
      adapter = _SubsetRegistryAdapter(parent_tool_registry, sa_for_runner["tools"])
      sub = AgentLoop(llm_registry=llm_shim, tool_registry=adapter,
                      max_iterations=sa_for_runner["_max_iter"])
      msgs = [{"role":"system","content": _framing(sa_for_runner)},
              {"role":"user","content": sa_for_runner["prompt"]}]
      final = ""
      async for ev in sub.run(msgs, session_id=f"{parent_sid}.par-{sa_task_id}"):
          if isinstance(ev, FinalEvent): final = ev.content or ""; break
          if isinstance(ev, ErrorEvent): return f"[subagent error] {ev.reason}: {ev.detail}"
      return final or "[subagent finished without final text]"
  ```
  这样调度器 semaphore 是**唯一**并发闸，不再 pin 线程池 worker。**BC 路径**（scheduler=None）仍用现有 `_make_default_runner`（thread-bounce）不变。
- `_make_default_runner`（`:421`）保持现状（BC），但需支持每子任务不同 iter/tools（见 WI-1.1 参数，按 `sa_for_runner["_max_iter"]` 重建闭包）。

**测试** TG-1.2/1.3（1.3.2 scheduler=None 字节级 BC；1.3.5 6→4 背压）。

## WI-1.4 —【R1:F1】main.py 构造调度器 + 注入

**改** `backend/main.py`（`:1927` 块）：
```python
_subagent_scheduler = None; kind_overrides = None
if bool(getattr(getattr(config,"features",None),"subagent_driver",False)):
    from deskpet.agent.subagent_scheduler import SubagentScheduler
    from deskpet.agent.task_kinds import load_kind_overrides
    from config import get_subagent_concurrency
    glob_cap, lane_caps = get_subagent_concurrency(config)
    kind_overrides = load_kind_overrides((getattr(config,"raw",None) or {}).get("agent"))
    _subagent_scheduler = SubagentScheduler(global_concurrency=glob_cap, lane_caps=lane_caps,
                                            progress_sink=_subagent_progress_sink)  # WI-1.5
    service_context.register("subagent_scheduler", _subagent_scheduler)   # ★F1：register 不是下标
```
`agent_parallel` 构造（同块，flag 改 `subagent_driver or agent_parallel`）传 `scheduler=_subagent_scheduler, kind_overrides=kind_overrides`。

## WI-1.5 —【R1:F5/F9】WS 进度出口 + metrics 键白名单

**改** `backend/observability/metrics_sink.py`：`_ALLOWED_DETAIL_KEYS`（`:90`）加 `"run_id"`, `"kind"`, `"duration_ms"`；`VALID_EVENTS`（`:71`）加 `"subagent_scheduled"`。
**改** `backend/main.py`：新增 `_subagent_progress_sink(payload)`（仿 `_todo_broadcaster` `:1882`）：①`record("subagent_progress", {run_id,kind,task_id,status,duration_ms})` ②对 `list(_control_connections.values())` 逐个 `asyncio.create_task(ws.send_json({"type":"subagent_progress","payload":payload}))`，逐个 try/except 不互累。
**测试** TG-1.5（1.5.4 metrics 键真留存 run_id/kind/duration_ms）。

## WI-1.6 —【R1:gap1】结果聚合/合成（明确，非新代码）

**说明**（写进计划，避免「只做传输不做合成」）：
- **阻塞路径（P1/P2）**：`agent_parallel`/`spawn_team` 返回 LLM-友好 envelope `{ok, results:[{task_id, kind, ok, output(=子代理摘要), error?}]}` 作 `ToolResultEvent`。父 ReAct loop **下一轮 LLM call** 看到该 envelope，自然合成给用户的回复——**这就是合成步，复用现有 ReAct，无须新代码**。要求：子代理只回**摘要**（不回原始大块，模式 2），envelope output 字段截断（复用现有 tool_result_truncator）。
- **非阻塞路径（P3）**：completion 注入后同理由下一轮 LLM 合成（WI-3.3）。
- 验收点（V1）：父最终消息确实综合了 N 份结果（真机肉眼 + 日志 N 个 completed）。

---

# P2 — spawn_team 暴露为 LLM 工具 + 接进 main.py（同构池）

## WI-2.1 — `spawn_team_tool.py` 工厂

**新建** `backend/deskpet/tools/code_tools/spawn_team_tool.py`：`build_spawn_team_tool(*, llm_shim, parent_tool_registry, parent_session_id_resolver, team_store, task_graph_store=None, kind_overrides=None, goal_text_resolver=None, goal_id_resolver=None)` → `(handler, schema)`。schema：`task_descriptions`(required) / `num_teammates` / `kind` / `timeout_seconds`。

`_handle(args, task_id="")`：
```python
import uuid, time
prof = resolve_kind(args.get("kind"), overrides=kind_overrides)
team_id = f"team-{int(time.time())}-{uuid.uuid4().hex[:6]}"
res = await spawn_team(
    team_id=team_id, task_descriptions=[str(d) for d in args.get("task_descriptions",[])],
    num_teammates=int(args.get("num_teammates",3)), store=team_store,
    parent_tool_registry=parent_tool_registry, llm_shim=llm_shim,
    parent_session_id=parent_session_id_resolver() or "default",
    timeout_seconds=float(args.get("timeout_seconds",300.0)),
    parent_goal_text=(goal_text_resolver() if goal_text_resolver else None),
    parent_goal_id=(goal_id_resolver() if goal_id_resolver else None),
    task_graph_store=task_graph_store,
    teammate_tool_subset=tuple(prof.tools),          # ★F7：走参数注入，不传自定义 runner
    teammate_max_iterations=prof.max_iterations,
    teammate_runner=None,                            # ★F7：用 _make_default_runner 消费上面两参
)
return json.dumps(res, ensure_ascii=False)
```
**【R1:F7】** 决定：**只用「参数注入」一条路**（teammate_tool_subset + teammate_max_iterations），**不**再写 `_kind_aware_runner` 自定义 runner（否则 `_make_default_runner` 被旁路、两参失效，互斥矛盾）。

## WI-2.2 — team-level kind 注入参数

**改** `backend/deskpet/agent/team/spawn_team.py`：
- `spawn_team(...)` 加 `teammate_tool_subset: tuple|None=None, teammate_max_iterations: int=30`（默认=现状→BC）。
- `_make_default_runner` 透传：`AgentLoop(max_iterations=teammate_max_iterations)`（原硬编码 30 `:356`→参数）；`_TeamSubsetRegistry(parent_registry, team_tools, allowed_tools=teammate_tool_subset)`。
- `_TeamSubsetRegistry.__init__`（`:394`）：`_DEFAULT_READONLY`（`:390`）→ 用注入的 `allowed_tools`（None→现有只读集 BC）；仍剔 `FORBIDDEN_TEAMMATE_TOOLS`。

**测试** TG-2.2（2.2.3 默认 max_iter=30/只读集 BC）。

## WI-2.3 — `registration.py` 加 spawn_team slot

**改** `registration.py`：`register_code_tools(..., spawn_team_handler=None, spawn_team_schema=None)`；末尾仿 agent_parallel 块（`:182-197`）None-gated 注册（toolset="control", permission_category="read_file", source="builtin", timeout_seconds=600.0, replace_allowed=False）。

## WI-2.4 —【R1:F1/F2/F3】main.py lifespan 构造 store + 注入

**改** `backend/main.py`：
1. lifespan startup（`_session_db` 构造后）：
   ```python
   if bool(getattr(getattr(config,"features",None),"agent_team",False)):
       from deskpet.agent.team.team_store import TeamStore
       from deskpet.agent.task_graph import TaskGraphStore
       _team_store = TeamStore(base_dir=Path(_paths.user_data_dir()) / "teams")  # ★F3
       _task_graph_store = TaskGraphStore(db=_session_db)
       service_context.register("team_store", _team_store)            # ★F1
       service_context.register("task_graph_store", _task_graph_store) # ★F1
   ```
2. 工具注册块（`:1946`）：flag ON 时 `build_spawn_team_tool(...)`，传 `_register_code_tools_full(..., spawn_team_handler=..., spawn_team_schema=...)`。
3. **【R1:F2】 goal resolver**：
   ```python
   _sg = service_context.get("session_goal_store")   # ★F2：槽名 session_goal_store
   goal_text_resolver = (lambda: _sg.get_goal_text(_resolve_parent_sid())) if _sg else None
   goal_id_resolver = None   # SessionGoalStore 无裸 goal_id getter；DAG goal 绑定 P2b 再补
   ```

## WI-2.5 —【R1:F13】子代理写工具权限态 + team permission（P2-optional UI）

- **【R1:F13】 写权限态（必做）**：`doc`/`code`/`fileops` kind 子代理调 `ppt_create`/`write_file`(`permission_category="write_file"` `ppt_tools.py:3848`) 会撞权限门、无人替子代理点。**posture**：子代理执行复用父会话的 auto-mode/permission 状态（子代理 sid 是 `<parent>.par-*`，权限解析挂父）。验收前确认 `permissions_auto_mode` ON 时子代理写工具放行（仿 goal-completion FP 真机用法）。
- **team permission 升级 UI（P2-optional）**：`TeamStore.request_permission`（`:460`）数据已存；新增 control WS `team_permission_request`/`team_permission_grant`→`grant_permission`（`:485`）+ 前端 approve 卡。工期紧则 team 工具限只读、跳过本项。

## WI-2.6 —【R1:gap6/R6】TeamStore `.db` 清理

**改** `team_store.py` 或 lifespan：`<user_data>/teams/` 加 LRU/TTL 清理（仿 tool_refs spill 400 文件自清模式，`1228eed` 既有范式）——boot 时删 >7 天或 >200 个 team db。小 WI，避免无限堆积。

---

# P3 — 非阻塞 spawn + completion queue + 取消级联 + 前端面板

## WI-3.1 —【R1:gap4】`subagent_registry.py`

**新建** `backend/deskpet/agent/subagent_registry.py`：`SubagentRun`（run_id/kind/task_id/status/task: asyncio.Task|None/summary/stats/error）+ `SubagentRegistry`：
- `register(run)` **必须在 spawn 返回前同步调用**（持 Task 强引用，防 GC——Python 仅对 running task 持弱引用，文档安全模式是显式存引用）。
- `update/get/list`；`completion_queue: asyncio.Queue`（完成 run 入队）。
- **【R1:F9】** `cancel_all()`：遍历活 run 调 `run.task.cancel()`，`log.info("subagent_cancel_all n=%d", n)`。

## WI-3.2 —【R1:gap4/gap7】`spawn_subagents`/`await_subagents` 工具

**新建** `backend/deskpet/tools/code_tools/spawn_subagents_tool.py`：
- `spawn_subagents(subagents=[{task_id,prompt,kind,...}], background?)`：每子任务 `t = asyncio.create_task(scheduler.run(...))` → **先** `registry.register(SubagentRun(..., task=t))`（强引用，gap4）→ 立即返回 `{ok, run_ids}`。
- `await_subagents(run_ids?)`：`asyncio.wait` 指定/全部活 run → 收集 → 返回 `{ok, results}`（yield 语义，模式 4）。
- 注册：`registration.py` 加 2 slot。**【R1:gap7】** 同 agent_parallel 走两阶段 None-gated（`registration.py` docstring `:61-64`）；flag `subagent_nonblocking`。

## WI-3.3 —【R1:F6/F11/gap5】agent_loop 回合边界 drain + 取消级联

**改** `backend/agent/agent_loop.py` `run()`：
- `AgentLoop.__init__` 加 `subagent_registry=None`（BC：None=不 drain）。
- **【R1:F11】** drain 放进 `for iteration in range(1, max+1):`（`:709`）**循环体顶部**（gate `:724` 之后），用 `working_messages`（`:604`）**不是** `messages`：
  ```python
  if self._subagent_registry is not None:
      while not self._subagent_registry.completion_queue.empty():
          done = self._subagent_registry.completion_queue.get_nowait()
          # 【R1:gap5】role 交替守门：仅当上一条是 assistant（干净回合边界）才注入；否则下一轮再 drain
          if working_messages and working_messages[-1].get("role") == "assistant":
              working_messages.append({"role":"user","content": f"[子代理完成] {done.task_id}({done.kind}): {done.summary}"})
              yield SubagentCompletionEvent(run_id=done.run_id, ...)
          else:
              self._subagent_registry.completion_queue.put_nowait(done); break
  ```
- **【R1:F6】 取消级联（关键）**：在 `main.py` 的 `chat_v2_interrupt` handler（`:4577`），`_t.cancel()`（`:4589`）**之后**加：
  ```python
  _reg = service_context.get("subagent_registry")
  if _reg is not None: _reg.cancel_all()
  ```
  （registry 在 lifespan 构造并 `register("subagent_registry", ...)`，handler 通过 `service_context.get` 拿到——跨 scope 解决，F6。）

## WI-3.4 — 前端 `SubagentProgressPanel`

**新建** `tauri-app/src/code-panel/SubagentProgressPanel.tsx` + `subagentStore.ts` slice：订阅 control WS `subagent_progress` → 按 run_id 维护 `{kind,status,summary}` → 渲染并发进度（图标+kind 徽章）；桌宠「忙」复用 supervisor working 视觉；独立 store slice 不碰 ChatRow 派生（R7）。`tsc --noEmit` 0 err + vitest。

---

# P4 — 子代理质量守门平价 + 每 kind 模型路由

## WI-4.1 — 子代理 TerminationGate 平价

**改** `agent_tool.py` / runner：`build_agent_tool(..., termination_gate_factory=None)`；构造 AgentLoop 时若给 factory 则传 `gate=factory()`（`AgentLoop` 已有 `self._gate` TerminationGate，`agent_loop.py:433-486` 支持 None-默认参，评审确认可加）。main.py：`subagent_driver` ON 时给轻量 gate。BC：factory=None 不变。

## WI-4.2 —【R1:F4/F8】每 kind 模型路由（新建 provider，不是换 shim）

**改** `backend/main.py`：
- **【R1:F4】** shim 不能换 model、provider model 不可变 → 必须**新建 provider**：
  ```python
  def _make_shim_for_model(model: str):
      from llm.openai_compatible import OpenAICompatibleProvider   # 同 main.py:254 用法
      prov = OpenAICompatibleProvider(base_url=local_llm.base_url, api_key=local_llm.api_key,
                                      model=model, temperature=local_llm.temperature)  # 字段名以 :254 实参为准
      return _ShimForAgent(provider=prov)   # ★F8：别名 _ShimForAgent，非 OpenAICompatibleAgentLLM
  ```
  （缓存 per-model shim 防重复建连。）
- runner：`prof.model` 非空 → 用 `_make_shim_for_model(prof.model)` 替默认 `_shim_for_agent`（`main.py:1907`）；`prof.model=None` → 父 shim（BC）。
- **测试** TG-4.2（4.2.1 model 非空建新 provider；4.2.2 None 用父 shim BC）。

---

## 改动文件清单（总，含 R1 新增）

| 文件 | P0 | P1 | P2 | P3 | P4 |
|---|---|---|---|---|---|
| `deskpet/agent/task_kinds.py` | ✚新 | | | | ✎model |
| `deskpet/agent/subagent_scheduler.py` | ✚新+log | | | | |
| `deskpet/agent/subagent_registry.py` | | | | ✚新 | |
| `context.py` | ✎4 服务槽(F1) | | | | |
| `config.py` | ✎flag+helper | | | | |
| `deskpet/tools/code_tools/agent_tool.py` | | ✎参数 | | | ✎gate |
| `deskpet/tools/code_tools/agent_parallel_tool.py` | | ✎schema+调度+原生runner(F10) | | | ✎model |
| `deskpet/tools/code_tools/spawn_team_tool.py` | | | ✚新 | | |
| `deskpet/tools/code_tools/spawn_subagents_tool.py` | | | | ✚新 | |
| `deskpet/tools/code_tools/registration.py` | | | ✎slot | ✎slot(两阶段) | |
| `deskpet/agent/team/spawn_team.py` | | | ✎kind 参数(F7) | | |
| `deskpet/agent/team/team_store.py` | | | ✎db清理(R6) | | |
| `agent/agent_loop.py` | | | | ✎drain(F11)+取消(F6) | |
| `main.py` | | ✎调度+WS(F1/F5) | ✎store+工具(F1/F2/F3) | ✎registry+取消(F6) | ✎provider(F4/F8) |
| `observability/metrics_sink.py` | | ✎键+event(F5) | | | |
| `tauri-app/src/code-panel/SubagentProgressPanel.tsx` | | | | ✚新 | |
| `tauri-app/src/.../subagentStore.ts` | | | | ✚新 | |

---

## 实施纪律（codingsys）

- 3+ 文件改动已 spec-first（本计划即 spec）；每 WI 先红后绿（03-TDD），编辑后必跑相关 pytest。
- 可并行：P0 各 WI 独立文件 → codex gpt-5.5 并行；P1 与 P2 不同文件 → 并行；同文件串行。
- flag OFF 字节基线守：每阶段末跑 BC 回归（agent_parallel OFF diff=0，V4）。
- 真机收口：P1/P2/P3 各 windows-mcp E2E（04），不接受单测/协议层替代（feedback_real_e2e）。
- STATUS 纪律：每阶段验收 → 同步更新 `STATUS/status.md` §3 + 里程碑。

---

## 附录: 评审修订记录

**第 1 轮对抗评审（2026-06-21，general-purpose 子代理，47 工具调用，读真源码）** — 结论「NOT 100% executable」，4 BLOCKING + 9 MAJOR/MINOR + 7 缺口，全部已吸收：

| ID | 严重 | 修订 |
|---|---|---|
| F1 | BLOCKING | service_context 是校验型 dataclass 非 dict → WI-0.4 加 4 服务槽进 `_VALID_SERVICES`+dataclass；全改 `register()`/`get()` |
| F2 | BLOCKING | goal 槽名 `session_goal_store`（非 goal_store）+ `get_goal_text(sid)`；无 goal_id getter → WI-2.4 resolver |
| F3 | BLOCKING | `user_data_dir` 局部不存在 → `_paths.user_data_dir()`（WI-2.4） |
| F4 | BLOCKING | shim 忽略 model + provider model 不可变 → WI-4.2 新建 provider 带 model |
| F5 | MAJOR | metrics `_ALLOWED_DETAIL_KEYS` 缺 run_id/kind/duration_ms → WI-1.5 补 |
| F6 | MAJOR | AgentLoop 无 cancel；取消挂 `chat_v2_interrupt:4589` 后 `registry.cancel_all()`（WI-3.3） |
| F7 | MAJOR | spawn_team kind 注入二选一 → 只用参数注入、删自定义 runner（WI-2.1/2.2） |
| F8 | MAJOR | 类名 `_ShimForAgent`（非 OpenAICompatibleAgentLLM）；local_llm 是 provider（01-arch/WI-4.2） |
| F9 | MAJOR | 加日志锚点 subagent_scheduled/spawn_team/subagent_cancel_all（WI-0.2/2.x/3.1）；04 锚点对齐 |
| F10 | MAJOR | thread-bounce 占满线程池 → scheduler 路径用原生协程 runner（WI-1.3 `_make_async_native_runner`） |
| F11 | MINOR | drain 用 `working_messages` + 放 for-iteration 体顶（WI-3.3） |
| F12 | MINOR | 删 WI-0.3 白名单步（[agent] raw-read 不触发 warn） |
| F13 | MINOR | doc/code/fileops 写工具权限态明确（子代理复用父 auto-mode，WI-2.5） |
| F14 | MINOR | 外部仓库 URL 实施前先验证可达（05 已注；模式独立成立） |
| gap1 | 缺口 | 结果合成=父 ReAct 下一轮 LLM（WI-1.6 明确，非新代码） |
| gap3 | 缺口 | depth guard = 工具剥离保证 depth-1；删「depth 计数器」过度承诺（00-PRD §5.2） |
| gap4 | 缺口 | spawn task 强引用：registry.register 在返回前同步存 task 句柄（WI-3.1/3.2） |
| gap5 | 缺口 | completion 注入 role 交替守门（仅 assistant 后注入，WI-3.3） |
| gap6 | 缺口 | TeamStore .db LRU/TTL 清理 WI-2.6 |
| gap7 | 缺口 | P3 工具两阶段 None-gated 注册（WI-3.2） |

P0（task_kinds/scheduler/config）评审判定「sound，可照做」。
