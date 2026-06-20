# 02-implementation-plan — 码级实施细节（P0–P4）

> 配套 [`00-PRD.md`](./00-PRD.md) / [`01-architecture.md`](./01-architecture.md)。
> 每个 WI 给出 **改哪个文件、哪个函数、什么签名、什么逻辑**。所有现有 file:line 已读码核实。
> 纪律：每 WI 先红后绿（TDD，见 [`03-TDD.md`](./03-TDD.md)）；所有新行为 flag-gated，OFF=BC 字节级。

---

## 阶段总览与依赖

```
P0 (新模块, 零接线) ──┬──▶ P1 (agent_parallel 路由) ──┬──▶ P3 (非阻塞 + queue + UI)
                     └──▶ P2 (spawn_team 暴露+接线) ──┘
                                                         └──▶ P4 (守门平价 + 模型路由)
```

P0 必须先做（P1/P2 依赖）。P1 与 P2 可并行（不同文件）。P3 依赖 P1+P2。P4 收尾。

---

# P0 — 基础模块（新增，零行为变更，独立单测）

> 验收：三 flag 仍全 OFF；新模块只被单测引用，不进任何 live 路径 → `agent_parallel` 行为字节级不变（V4）。

## WI-0.1 — `task_kinds.py` 事务分型注册表

**新建** `backend/deskpet/agent/task_kinds.py`：

```python
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""事务分型（task-kind）注册表 — 子代理并发驱动的「多种事务」路由地基。"""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Any

# 递归守门：任何 kind 的工具子集都不得含这些（与 agent_parallel._FORBIDDEN_NESTED_TOOLS 对齐）
_FORBIDDEN_IN_KIND = frozenset({"agent", "agent_parallel", "spawn_team",
                                "spawn_subagents", "await_subagents"})

@dataclass(frozen=True)
class KindProfile:
    kind: str
    tools: tuple[str, ...]
    max_iterations: int
    framing: str = ""
    model: str | None = None
    lane_concurrency: int = 2

# 工具名已对真实注册表核实（registration.py / os_tools / ppt_tools / research_tools）：
# read_file write_file edit_file glob grep list_directory run_shell web_search
# web_fetch ppt_create doc_create excel_create deepresearch generate_image skill_invoke
_BUILTIN_KINDS: dict[str, KindProfile] = {
    "general":  KindProfile("general",
        ("read_file", "list_directory", "glob", "grep", "web_search"), 15,
        framing="你是通用只读子代理，专注调查并返回简洁结论。", lane_concurrency=2),
    "research": KindProfile("research",
        ("web_search", "web_fetch", "deepresearch", "read_file"), 12,
        framing="你是调研子代理：检索权威来源、交叉验证、给带依据的结论。", lane_concurrency=2),
    "code":     KindProfile("code",
        ("read_file", "write_file", "edit_file", "glob", "grep", "run_shell",
         "list_directory"), 20,
        framing="你是编码子代理：读现状→改代码→自检（读回/跑测试）。", lane_concurrency=2),
    "fileops":  KindProfile("fileops",
        ("read_file", "write_file", "glob", "grep", "list_directory"), 12,
        framing="你是文件操作子代理：按契约读写文件，不越界。", lane_concurrency=3),
    "doc":      KindProfile("doc",
        ("ppt_create", "doc_create", "excel_create", "read_file", "web_search",
         "generate_image"), 15,
        framing="你是文档生成子代理：产出 PPT/Word/Excel，必报完整产物路径。", lane_concurrency=1),
    "web":      KindProfile("web",
        ("web_search", "web_fetch"), 8,
        framing="你是联网快查子代理：快速找事实/网址，不深挖。", lane_concurrency=3),
}
_DEFAULT_KIND = "general"

def _strip_forbidden(tools: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(t for t in tools if t not in _FORBIDDEN_IN_KIND)

def resolve_kind(name: str | None, *, overrides: dict[str, KindProfile] | None = None) -> KindProfile:
    """名字 → KindProfile。未知 kind → general（只读安全回退，R5）。
    返回的 profile 工具子集已剔除 spawn 类（递归守门，模式 6）。"""
    table = overrides or _BUILTIN_KINDS
    prof = table.get((name or "").strip().lower()) or table.get(_DEFAULT_KIND) or _BUILTIN_KINDS[_DEFAULT_KIND]
    return replace(prof, tools=_strip_forbidden(prof.tools))

def known_kinds(overrides: dict[str, KindProfile] | None = None) -> list[str]:
    return sorted((overrides or _BUILTIN_KINDS).keys())

def load_kind_overrides(raw_agent_cfg: dict[str, Any] | None) -> dict[str, KindProfile]:
    """从 config.raw['agent']['subagent_kinds'] 合并覆盖到内置默认。
    缺省/格式错 → 返回内置默认（不抛，防 config 单例陷阱 R2）。"""
    merged = dict(_BUILTIN_KINDS)
    section = ((raw_agent_cfg or {}).get("subagent_kinds") or {}) if isinstance(raw_agent_cfg, dict) else {}
    for name, spec in (section.items() if isinstance(section, dict) else []):
        try:
            base = merged.get(name) or _BUILTIN_KINDS[_DEFAULT_KIND]
            merged[name] = replace(
                base, kind=name,
                tools=tuple(spec.get("tools", base.tools)),
                max_iterations=int(spec.get("max_iterations", base.max_iterations)),
                framing=str(spec.get("framing", base.framing)),
                model=spec.get("model", base.model),
                lane_concurrency=int(spec.get("lane_concurrency", base.lane_concurrency)),
            )
        except Exception:  # noqa: BLE001 — 单条坏覆盖不污染整表
            continue
    return merged
```

**测试**（03 §TG-0.1）：`resolve_kind("research")` 工具对；未知→general；spawn 类被剔；`load_kind_overrides` 合并 + 坏覆盖跳过。

## WI-0.2 — `subagent_scheduler.py` 有界并发调度器

**新建** `backend/deskpet/agent/subagent_scheduler.py`：

```python
# SPDX headers...
"""Lane-aware 有界并发调度器（OpenClaw 模式偷师，纯 asyncio）。"""
from __future__ import annotations
import asyncio, logging, time
from typing import Any, Awaitable, Callable
log = logging.getLogger(__name__)

class SubagentScheduler:
    def __init__(self, *, global_concurrency: int = 4,
                 lane_caps: dict[str, int] | None = None,
                 progress_sink: Callable[[dict], None] | None = None) -> None:
        self._global = asyncio.Semaphore(max(1, global_concurrency))
        self._lane_caps = dict(lane_caps or {})
        self._lanes: dict[str, asyncio.Semaphore] = {}
        self._progress = progress_sink
        self._running = 0
        self._queued = 0

    def _lane(self, kind: str) -> asyncio.Semaphore:
        if kind not in self._lanes:
            cap = max(1, self._lane_caps.get(kind, 2))
            self._lanes[kind] = asyncio.Semaphore(cap)
        return self._lanes[kind]

    def _emit(self, payload: dict[str, Any]) -> None:
        if self._progress is None:
            return
        try:
            self._progress(payload)
        except Exception as exc:  # noqa: BLE001 — 进度永不阻断调度
            log.debug("scheduler progress emit failed: %s", exc)

    async def run(self, *, kind: str, run_id: str, task_id: str,
                  parent_sid: str, coro_factory: Callable[[], Awaitable[Any]]) -> Any:
        lane = self._lane(kind)
        self._queued += 1
        self._emit({"run_id": run_id, "kind": kind, "task_id": task_id,
                    "parent_sid": parent_sid, "status": "queued", "ts": time.time()})
        # 全局背压 → kind-lane 背压（双闸，超 cap 自然排队等待）
        async with self._global:
            async with lane:
                self._queued -= 1; self._running += 1
                self._emit({"run_id": run_id, "kind": kind, "task_id": task_id,
                            "parent_sid": parent_sid, "status": "running", "ts": time.time()})
                t0 = time.time()
                try:
                    out = await coro_factory()
                    self._emit({"run_id": run_id, "kind": kind, "task_id": task_id,
                                "parent_sid": parent_sid, "status": "completed",
                                "duration_ms": int((time.time()-t0)*1000), "ts": time.time()})
                    return out
                except Exception:
                    self._emit({"run_id": run_id, "kind": kind, "task_id": task_id,
                                "parent_sid": parent_sid, "status": "failed",
                                "duration_ms": int((time.time()-t0)*1000), "ts": time.time()})
                    raise
                finally:
                    self._running -= 1

    def snapshot(self) -> dict[str, int]:
        return {"running": self._running, "queued": self._queued}
```

**测试**（03 §TG-0.2）：6 个 coro 全局 cap=2 → 同时 running ≤2，全完成（计数器探针）；lane cap 独立；progress_sink 收到 queued→running→completed 序列；coro 抛 → failed + 异常上抛。

## WI-0.3 — 配置：flag + 并发段 + kind 覆盖段

**改** `backend/config.py`：

1. `FeaturesConfig`（`:394`）加 3 个布尔（默认 False，docstring 同风格）：
   ```python
   subagent_driver: bool = False      # 总开关：分型路由+调度接入 agent_parallel/子代理
   agent_team: bool = False           # 暴露 spawn_team + 构造 TeamStore/TaskGraphStore
   subagent_nonblocking: bool = False # P3 暴露 spawn_subagents/await_subagents + completion queue
   ```
2. `[agent.concurrency]` / `[agent.subagent_kinds]` **不**加 dataclass，沿用 `[agent]` 段既有 `AppConfig.raw["agent"]` 直读模式（`config.py:449` 注释证 `[agent]` 已是 raw-read）。提供 helper：
   ```python
   def get_subagent_concurrency(cfg) -> tuple[int, dict[str,int]]:
       raw = (getattr(cfg, "raw", None) or {}).get("agent", {})
       conc = raw.get("concurrency", {}) if isinstance(raw, dict) else {}
       glob = int(conc.get("global_concurrency", 4))
       lanes = dict(conc.get("lane_caps", {})) or {
           "research":2,"code":2,"fileops":3,"doc":1,"web":3,"general":2}
       return glob, lanes
   ```
   （放 config.py 或 task_kinds.py；**必须** `getattr(cfg,"raw",None)` 兜底，不复刻 `config.config` 单例 bug R2。）
3. 把新 key 加进 `config.py` 的「已知额外字段」白名单（`:485` 区），避免启动 warn 刷屏。

**测试**（03 §TG-0.3）：默认 False；真 config.toml 写 `[features] subagent_driver=true` → 读到 True；写 `[agent.concurrency] global_concurrency=6` → helper 读到 6（**仿 `b05823b` 回归测试：证开关真生效**）。

---

# P1 — agent_parallel 路由进分型+调度（异构并发）

> 验收：flag ON 时 `agent_parallel` 子代理按 kind 跑、受调度器背压、WS 有进度；flag OFF 字节级 BC（V4）。

## WI-1.1 — `build_agent_tool` 支持 iter/工具子集参数

**改** `backend/deskpet/tools/code_tools/agent_tool.py`：

- `build_agent_tool(*, llm_shim, parent_tool_registry, parent_session_id_resolver, default_max_iterations: int = _SUBAGENT_MAX_ITERATIONS, default_tool_subset: tuple[str,...] = _DEFAULT_READONLY_TOOLS)`（加两参，**默认值=现状常量** → BC）
- `_handler`（`:93`）：`tool_subset` 无 `args["tools"]` 时用 `list(default_tool_subset)`（原硬编码 `_DEFAULT_READONLY_TOOLS` → 改用参数）；`AgentLoop(max_iterations=default_max_iterations)`（原 `_SUBAGENT_MAX_ITERATIONS` → 改用参数）
- `framing`：可选 `default_framing: str = ""` 参数，拼进 system 消息（`:128`）末尾

**测试**：默认调用与现状字节一致；传 `default_max_iterations=20` → sub_loop 用 20。

## WI-1.2 — `agent_parallel` schema 加 `kind` + 提 fan-out 上限

**改** `agent_parallel_tool.py`：

- `_MAX_SUBAGENTS = 4` → `8`（`:51`）
- `_SCHEMA.parameters.properties.subagents.items.properties` 加：
  ```python
  "kind": {"type": "string",
           "enum": ["general","research","code","fileops","doc","web"],
           "description": "事务类型：决定子代理工具集/迭代上限/并发 lane。默认 general（只读）。"}
  ```
- `_SCHEMA.description` 补：「每个子任务可指定 kind（research/code/doc/web/fileops），不同类型并发受 lane 调度。」

## WI-1.3 — `build_agent_parallel_tool` 接调度器 + 分型

**改** `agent_parallel_tool.py`：

- 签名加：`scheduler: Any = None, kind_overrides: dict | None = None, progress_broadcaster: Callable[[dict], Any] | None = None`
- `_run_one(sa)`（`:355`）改造：
  ```python
  from deskpet.agent.task_kinds import resolve_kind
  prof = resolve_kind(sa.get("kind"), overrides=kind_overrides)
  run_id = f"{parent_session_id_resolver()}.par-{sa_task_id}"
  # 工具子集：子任务显式 tools（剔 forbidden）优先，否则用 kind 默认
  req_tools = _filter_subagent_tools(sa.get("tools"))
  effective_tools = req_tools if req_tools is not None else list(prof.tools)
  sa_for_runner = {**sa, "prompt": full_prompt, "tools": effective_tools,
                   "_kind": prof.kind, "_max_iter": prof.max_iterations,
                   "_framing": prof.framing, ...cache hints...}
  async def _do():
      return await runner(sa_for_runner, sa_task_id)
  if scheduler is not None:
      output = await scheduler.run(kind=prof.kind, run_id=run_id, task_id=sa_task_id,
                                   parent_sid=parent_session_id_resolver(), coro_factory=_do)
  else:
      output = await _do()   # BC：无调度器 = 现状扁平 gather
  ```
- `_emit_progress`（`:221`）扩展：保留 metrics，额外 `if progress_broadcaster: schedule broadcast`（best-effort）。**或**统一让 scheduler 的 `progress_sink` 兼做 metrics+WS（推荐：单一进度出口，见 WI-1.5）。
- `_make_default_runner`（`:421`）：`build_agent_tool(..., default_max_iterations=sa_for_runner["_max_iter"], default_tool_subset=tuple(sa_for_runner["tools"]), default_framing=sa_for_runner["_framing"])` — 即每子代理按 kind profile 构造。

> 注：`_make_default_runner` 当前在闭包构造期就建 `build_agent_tool`；需改为**每次 `_runner` 调用内**按该子任务的 `_kind`/`_max_iter` 重建（因不同子任务 kind 不同）。这是 P1 主改动点。

## WI-1.4 — main.py 构造调度器 + 注入 agent_parallel

**改** `backend/main.py`（`:1927-1944` 块）：

```python
if bool(getattr(getattr(config,"features",None),"subagent_driver",False)):
    from deskpet.agent.subagent_scheduler import SubagentScheduler
    from deskpet.agent.task_kinds import load_kind_overrides
    from config import get_subagent_concurrency  # WI-0.3 helper
    glob_cap, lane_caps = get_subagent_concurrency(config)
    kind_overrides = load_kind_overrides((getattr(config,"raw",None) or {}).get("agent"))
    _subagent_scheduler = SubagentScheduler(
        global_concurrency=glob_cap, lane_caps=lane_caps,
        progress_sink=_subagent_progress_sink)   # WI-1.5
    service_context["subagent_scheduler"] = _subagent_scheduler
else:
    _subagent_scheduler = None; kind_overrides = None
```
`agent_parallel` 构造（同块，flag 改为 `subagent_driver or agent_parallel` 兼容旧 flag）传 `scheduler=_subagent_scheduler, kind_overrides=kind_overrides, progress_broadcaster=...`。

> **兼容旧 flag**：现有 `features.agent_parallel` 仍可单独开（不带调度，扁平路径）；`subagent_driver` 开则带调度。两者 OR 决定是否注册工具。

## WI-1.5 — WS 进度广播（单一进度出口）

**改** `backend/main.py`：仿 `_todo_broadcaster`（`:1882`）新增：

```python
def _subagent_progress_sink(payload: dict) -> None:
    # 1) metrics（盘，复用 VALID_EVENTS 的 subagent_progress）
    try:
        from observability.metrics_sink import record
        record("subagent_progress", {k: payload.get(k) for k in
               ("run_id","kind","task_id","status","duration_ms")})
    except Exception: pass
    # 2) WS 广播给所有 control 连接（fire-and-forget，非阻塞）
    msg = {"type": "subagent_progress", "payload": payload}
    for ws in list(_control_connections.values()):
        try: asyncio.create_task(ws.send_json(msg))
        except Exception: pass
```
`metrics_sink.VALID_EVENTS` 确认含 `subagent_progress`（已有）；新增 `subagent_scheduled` 入集（`observability/metrics_sink.py`）。

**测试 + 真机**：V1 进度卡片可见；V3 背压（6 派 4 跑 2 排队日志）。

---

# P2 — spawn_team 暴露为 LLM 工具 + 接进 main.py（同构池）

> 验收：`features.agent_team` ON → LLM 真能调 `spawn_team` → N teammate 啃共享池 → 聚合返回（V2）。

## WI-2.1 — `spawn_team_tool.py` 工厂

**新建** `backend/deskpet/tools/code_tools/spawn_team_tool.py`（仿 `build_agent_parallel_tool`）：

```python
def build_spawn_team_tool(*, llm_shim, parent_tool_registry, parent_session_id_resolver,
                          team_store, task_graph_store=None, scheduler=None,
                          kind_overrides=None, goal_text_resolver=None,
                          goal_id_resolver=None):
    _SCHEMA = {"name": "spawn_team", "description":
        "派 N 个同构 teammate 子代理从共享任务池里 claim→work→update，直到池空。"
        "适用于：一批同类任务（翻译12个文件/批量改造）。异构独立任务请用 agent_parallel。",
      "parameters": {"type":"object","properties":{
        "task_descriptions": {"type":"array","items":{"type":"string"},
            "minItems":1,"description":"初始任务池，每条一个待办。"},
        "num_teammates": {"type":"integer","description":"1-8，默认 3。"},
        "kind": {"type":"string","enum":[...],"description":"全队事务类型（决定 teammate 工具集/模型）。默认 general。"},
        "timeout_seconds": {"type":"number","description":"全队墙钟上限，默认 300。"}},
      "required":["task_descriptions"]}}

    async def _handle(args, task_id=""):
        import uuid as _uuid  # 注意：Workflow 脚本禁 random，但这是生产代码，uuid OK
        from deskpet.agent.team.spawn_team import spawn_team
        from deskpet.agent.task_kinds import resolve_kind
        prof = resolve_kind(args.get("kind"), overrides=kind_overrides)
        team_id = f"team-{int(time.time())}-{_uuid.uuid4().hex[:6]}"
        # team-level kind → 通过自定义 teammate_runner 给 AgentLoop 套 prof.tools/iter（见 WI-2.2）
        res = await spawn_team(
            team_id=team_id,
            task_descriptions=[str(d) for d in args.get("task_descriptions",[])],
            num_teammates=int(args.get("num_teammates", 3)),
            store=team_store,
            parent_tool_registry=parent_tool_registry,
            llm_shim=llm_shim,
            parent_session_id=parent_session_id_resolver() or "default",
            timeout_seconds=float(args.get("timeout_seconds", 300.0)),
            parent_goal_text=(goal_text_resolver() if goal_text_resolver else None),
            parent_goal_id=(goal_id_resolver() if goal_id_resolver else None),
            task_graph_store=task_graph_store,
            teammate_runner=_kind_aware_runner(prof, scheduler, ...) if scheduler else None,
        )
        return json.dumps(res, ensure_ascii=False)
    return _handle, _SCHEMA
```

## WI-2.2 — team-level kind 注入（teammate 工具集/iter 按 kind）

**改** `backend/deskpet/agent/team/spawn_team.py`：

- `_make_default_runner` 的 `AgentLoop(max_iterations=30)`（`:356`）→ 接受 `max_iterations` 参数（来自 kind profile，默认仍 30 → BC）
- `_TeamSubsetRegistry._DEFAULT_READONLY`（`:390`）→ 改为接受 `allowed_tools` 注入（来自 `prof.tools`），默认仍是现有只读集 → BC
- `spawn_team(...)` 加可选 `teammate_tool_subset: tuple|None=None`、`teammate_max_iterations: int=30`，透传给 `_make_default_runner`/`_TeamSubsetRegistry`
- WI-2.1 的 `_kind_aware_runner` = 包一层把 `prof.tools`/`prof.max_iterations` 注入

> 这样 spawn_team 整队是一个 kind（D6：team=同构）；异构走 agent_parallel。

## WI-2.3 — `registration.py` 加 spawn_team slot

**改** `backend/deskpet/tools/code_tools/registration.py`：

- `register_code_tools(..., spawn_team_handler=None, spawn_team_schema=None)`
- 末尾加（仿 `agent_parallel` 块 `:182-197`）：
  ```python
  if spawn_team_handler is not None and spawn_team_schema is not None:
      registry.register(name="spawn_team", toolset="control",
          schema=spawn_team_schema, handler=spawn_team_handler,
          permission_category="read_file", source="builtin",
          timeout_seconds=600.0, replace_allowed=False)
  ```

## WI-2.4 — main.py lifespan 构造 store + 注入

**改** `backend/main.py`：

1. lifespan startup（`_session_db` 构造之后）：
   ```python
   if bool(getattr(getattr(config,"features",None),"agent_team",False)):
       from deskpet.agent.team.team_store import TeamStore
       from deskpet.agent.task_graph import TaskGraphStore
       _team_store = TeamStore(base_dir=Path(user_data_dir) / "teams")
       _task_graph_store = TaskGraphStore(db=_session_db)
       service_context["team_store"] = _team_store
       service_context["task_graph_store"] = _task_graph_store
   ```
2. 工具注册块（`:1946`）：flag ON 时 `build_spawn_team_tool(...)` 并传给 `_register_code_tools_full(..., spawn_team_handler=..., spawn_team_schema=...)`
3. `goal_text_resolver`/`goal_id_resolver`：从 `service_context["goal_store"]`（若 goal_mode ON）取，否则 None

## WI-2.5 — team permission WS + 前端 approve（补 Tier-3 缺口）

**改** `backend/main.py` + `tauri-app/src`：

- `TeamStore.request_permission`（`team_store.py:460`）已存数据；新增 control WS 消息 `team_permission_request`（后端→前端）+ `team_permission_grant`（前端→后端→`grant_permission`）
- 前端：`tauri-app/src/code-panel/` 加最小 approve 卡（仿现有 permission 卡）。**首发可降级**：若工期紧，team 工具默认只读（无需 permission 升级）→ 本 WI 标 P2-optional。

---

# P3 — 非阻塞 spawn + completion queue + 取消级联 + 前端面板

> 验收：`spawn_subagents(background=true)` 立即返回；结果在下一回合边界冒泡；`/stop` 级联取消（V5）。

## WI-3.1 — `subagent_registry.py`

**新建** `backend/deskpet/agent/subagent_registry.py`：`SubagentRun` dataclass + `SubagentRegistry`（`register/update/get/list/cancel_all` + `completion_queue: asyncio.Queue`）。run 完成时入队（含 summary/stats）。`cancel_all()` 遍历活 run 的 `asyncio.Task` 调 `.cancel()`。

## WI-3.2 — `spawn_subagents` / `await_subagents` 工具

**新建** `backend/deskpet/tools/code_tools/spawn_subagents_tool.py`：

- `spawn_subagents(subagents=[{task_id,prompt,kind,...}], background?)`：每子任务 `asyncio.create_task(scheduler.run(...))` → `registry.register(run_id, task)` → **立即**返回 `{ok, run_ids}`（不 await）
- `await_subagents(run_ids?)`：`asyncio.wait` 指定 run（省略=全部活跃）→ 收集 → 返回 `{ok, results}`。这是「结束当前回合等结果」的 yield 语义（模式 4）。
- 注册：`registration.py` 加 2 slot；flag `subagent_nonblocking`

## WI-3.3 — agent_loop 回合边界 drain completion queue

**改** `backend/agent/agent_loop.py` `run()`（`:581`）：

- 每轮循环顶部（gate 检查前后）：
  ```python
  if self._subagent_registry is not None:
      while not self._subagent_registry.completion_queue.empty():
          done = self._subagent_registry.completion_queue.get_nowait()
          # 作为新 user/tool 消息注入（回合边界，模式 2）—— 保 role 交替合法
          messages.append({"role":"user","content": f"[子代理完成] {done.task_id}({done.kind}): {done.summary}"})
          yield SubagentCompletionEvent(...)   # 可选事件供前端
  ```
- `AgentLoop.__init__` 加可选 `subagent_registry=None`（BC：None=不 drain）
- `/stop`/cancel 路径（找现有 cancel 处理）：调 `self._subagent_registry.cancel_all()`（D10/V5）

## WI-3.4 — 前端 `SubagentProgressPanel`

**新建** `tauri-app/src/code-panel/SubagentProgressPanel.tsx` + store slice：

- 订阅 control WS `subagent_progress` → 按 run_id 维护 `{kind,status,summary}` 列表 → 渲染并发子代理进度（queued/running/completed 图标 + kind 徽章）
- 桌宠「忙」状态：复用现有 supervisor working 视觉（多个子代理 running 时桌宠 working 气泡）
- 独立 store slice，不碰 ChatRow 派生（R7）

---

# P4 — 子代理质量守门平价 + 每 kind 模型路由

> 验收：高后果子代理有 TerminationGate 兜底；kind.model 指定时子代理用对应模型。

## WI-4.1 — 子代理 TerminationGate 平价

**改** `agent_tool.py` / `_make_default_runner`：

- `build_agent_tool(..., termination_gate_factory: Callable|None=None)`；构造 `AgentLoop` 时若给了 factory 则 `gate=termination_gate_factory()` 传入
- main.py：`subagent_driver` ON 时给一个轻量 TerminationGate（防复读/卡死，复用现有 `backend/agent/...` 的 gate 实现 — 见 STATUS/AgentLoop.md「TerminationGate」节）

## WI-4.2 — 每 kind 模型路由（shim factory）

**改** `backend/main.py`：

- `_make_shim_for_model(model: str) -> OpenAICompatibleAgentLLM`：按 model 名建 shim（复用 `local_llm` provider，换 model 参数 — 参考 deep-research reranker 的「同 base+key 换 model」桥，`2a8a4cb`）
- `build_agent_parallel_tool`/`spawn_team_tool` 的 runner：`prof.model` 非空 → 用 `_make_shim_for_model(prof.model)` 替代默认 `_shim_for_agent`
- 默认 `prof.model=None` → 用父模型 → BC

---

## 改动文件清单（总）

| 文件 | P0 | P1 | P2 | P3 | P4 |
|---|---|---|---|---|---|
| `backend/deskpet/agent/task_kinds.py` | ✚新 | | | | ✎model |
| `backend/deskpet/agent/subagent_scheduler.py` | ✚新 | | | | |
| `backend/deskpet/agent/subagent_registry.py` | | | | ✚新 | |
| `backend/config.py` | ✎flag+helper | | | | |
| `backend/deskpet/tools/code_tools/agent_tool.py` | | ✎参数 | | | ✎gate |
| `backend/deskpet/tools/code_tools/agent_parallel_tool.py` | | ✎schema+调度 | | | ✎model |
| `backend/deskpet/tools/code_tools/spawn_team_tool.py` | | | ✚新 | | |
| `backend/deskpet/tools/code_tools/spawn_subagents_tool.py` | | | | ✚新 | |
| `backend/deskpet/tools/code_tools/registration.py` | | | ✎slot | ✎slot | |
| `backend/deskpet/agent/team/spawn_team.py` | | | ✎kind 注入 | | |
| `backend/deskpet/agent/team/team_store.py` | | | (P2-opt perm) | | |
| `backend/agent/agent_loop.py` | | | | ✎drain+cancel | |
| `backend/main.py` | | ✎调度+WS | ✎store+工具 | ✎registry | ✎shim |
| `backend/observability/metrics_sink.py` | | ✎event | | | |
| `tauri-app/src/code-panel/SubagentProgressPanel.tsx` | | | | ✚新 | |
| `tauri-app/src/.../subagentStore.ts` | | | | ✚新 | |

---

## 实施纪律（codingsys）

- **3+ 文件改动已 spec-first**（本计划即 spec）。
- **每 WI 先红后绿**（03-TDD），编辑后必跑 `cd backend && python -m pytest <相关 test>`。
- **可并行**：P0 三 WI 独立文件 → codex gpt-5.5 并行；P1 与 P2 不同文件 → 并行。同文件串行。
- **flag OFF 字节基线守**：每阶段末跑 BC 回归（agent_parallel OFF diff=0，V4）。
- **真机收口**：P1/P2/P3 各自 windows-mcp E2E（04），不接受单测/协议层替代（feedback_real_e2e）。
- **STATUS 纪律**：每阶段验收 → 同步更新 `STATUS/status.md` §3 + 里程碑。
