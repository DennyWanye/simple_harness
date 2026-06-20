# 03-TDD — 测试组（每 WI 红→绿）

> 配套 [`02-implementation-plan.md`](./02-implementation-plan.md)。新建测试文件放 `backend/tests/`。
> 命令：`cd backend && python -m pytest tests/test_<name>.py -v`。
> 纪律：先写失败的测试（红）→ 实现 → 绿。所有 flag-OFF 测试守 BC 字节基线。

---

## TG-0.1 — `task_kinds.py`（`tests/test_task_kinds.py`）

| # | 用例 | 断言 |
|---|---|---|
| 0.1.1 | `resolve_kind("research")` | `.tools` 含 web_search/web_fetch/deepresearch；`.max_iterations==12` |
| 0.1.2 | `resolve_kind(None)` / `resolve_kind("不存在")` | 回退 general（只读集），不抛 |
| 0.1.3 | 任一 kind 的 profile.tools | 不含 agent/agent_parallel/spawn_team/spawn_subagents/await_subagents（递归守门） |
| 0.1.4 | `load_kind_overrides({"subagent_kinds":{"research":{"max_iterations":20}}})` | research.max_iterations==20，其余字段保留 |
| 0.1.5 | `load_kind_overrides` 含坏覆盖（tools=非 list / max_iter="x"） | 坏条跳过，整表仍可用（不抛） |
| 0.1.6 | `load_kind_overrides(None)` | 返回内置默认全集 |
| 0.1.7 | `known_kinds()` | 含 6 个内置 |

## TG-0.2 — `subagent_scheduler.py`（`tests/test_subagent_scheduler.py`）

| # | 用例 | 断言 |
|---|---|---|
| 0.2.1 | 6 个 sleep-coro，`global_concurrency=2` | 峰值并发探针 ≤2；6 个全完成 |
| 0.2.2 | lane cap：kind=A cap=1 起 3 个 | A 串行（峰值 1）；kind=B 不受 A 阻 |
| 0.2.3 | progress_sink 注入 | 收到 queued→running→completed 有序（按 run_id） |
| 0.2.4 | coro 抛异常 | sink 收 failed；`run()` 重新抛出 |
| 0.2.5 | progress_sink 自身抛 | 不影响调度（吞掉，coro 仍跑完） |
| 0.2.6 | `snapshot()` | 跑中 running>0；全完成后 running==0 queued==0 |

## TG-0.3 — 配置（`tests/test_subagent_config.py`）

| # | 用例 | 断言 |
|---|---|---|
| 0.3.1 | 默认 AppConfig | `features.subagent_driver/agent_team/subagent_nonblocking` 全 False |
| 0.3.2 | 真 config.toml 写 `[features] subagent_driver=true` 后 `load_config()` | 读到 True |
| 0.3.3 | 写 `[agent.concurrency] global_concurrency=6` + lane_caps | `get_subagent_concurrency(cfg)` 返回 `(6, {...})` ★（仿 b05823b 回归：证开关真生效，非靠默认侥幸） |
| 0.3.4 | `get_subagent_concurrency` 传入无 `.raw` 的 stub | 不抛，返回默认 `(4, {...})`（防 config 单例陷阱 R2） |

## TG-0.4 —【R1:F1】context 服务槽（`tests/test_subagent_services.py`）

| # | 用例 | 断言 |
|---|---|---|
| 0.4.1 | `ServiceContext().register("subagent_scheduler", obj)` + `.get(...)` | 返回 obj，不抛 |
| 0.4.2 | 同上 team_store / task_graph_store / subagent_registry | 4 槽全可 register/get |
| 0.4.3 | `register("不存在的服务", x)` | 仍 raise ValueError（白名单未被破坏） |
| 0.4.4 | `_VALID_SERVICES` | 含新增 4 名 + 原有 `session_goal_store` 等不丢 |

## TG-1.1 — `build_agent_tool` 参数（`tests/test_agent_tool_params.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1.1.1 | 默认调用（不传新参） | 与现状字节级一致：默认只读集 + max_iter=15（mock AgentLoop 捕获 ctor 参数）★BC |
| 1.1.2 | `default_max_iterations=20` | sub_loop 用 20 |
| 1.1.3 | `default_tool_subset=("read_file","run_shell")` 且 args 无 tools | adapter allowed == 该集 |
| 1.1.4 | `default_framing="X"` | system 消息含 "X" |

## TG-1.2/1.3 — agent_parallel 分型+调度（`tests/test_agent_parallel_kinds.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1.2.1 | schema | subagents.items 含 kind enum；`_MAX_SUBAGENTS==8` |
| 1.3.1 | 注入 mock runner + mock scheduler，2 子任务 kind=research/doc | scheduler.run 被调 2 次、kind 参数对；runner 收到 kind 对应 tools（research/doc 集） |
| 1.3.2 | scheduler=None（BC） | 走原 gather，行为与现状字节级一致 ★V4 |
| 1.3.3 | 子任务显式 tools=["read_file","agent"] | agent 被剔，read_file 保留（forbidden 过滤 + kind 默认被显式覆盖） |
| 1.3.4 | 未知 kind="xyz" | 回退 general 只读集，不崩 |
| 1.3.5 | 6 子任务 + 真 scheduler global=4 | 全完成；progress sink 见 queued 出现（背压触发）★V3 |
| 1.3.6 | 单子代理抛异常 | 该 result ok=false，其余正常（错误隔离不回归） |
| 1.3.7 |【R1:F10】scheduler 路径用 `_make_async_native_runner`（mock AgentLoop） | 不调 `loop.run_in_executor`（无 thread-bounce）；子 AgentLoop 作协程 await |
| 1.3.8 |【R1:gap1】envelope `results[].output` 是子代理摘要、含 task_id+kind | 父可据此合成（output 经截断不超长） |

## TG-1.5 — WS 进度出口（`tests/test_subagent_progress_sink.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1.5.1 | `_subagent_progress_sink` 注入 fake control_connections | WS payload type=="subagent_progress"，字段齐 |
| 1.5.2 | metrics record 调用 | event=="subagent_progress" |
| 1.5.3 | 某 ws.send 抛 | 不影响其它 ws + 不抛 |
| 1.5.4 | `metrics_sink.VALID_EVENTS` | 含 subagent_progress + subagent_scheduled |

## TG-2.1/2.2/2.3 — spawn_team 工具（`tests/test_spawn_team_tool.py`）

| # | 用例 | 断言 |
|---|---|---|
| 2.1.1 | schema | name=="spawn_team"，required==["task_descriptions"]，含 kind/num_teammates/timeout |
| 2.1.2 | handler + mock spawn_team（monkeypatch） | 用 team_id 前缀 team-、透传 task_descriptions/num_teammates |
| 2.2.1 | spawn_team `teammate_max_iterations=10` 透传 | _make_default_runner 的 AgentLoop 用 10 |
| 2.2.2 | spawn_team `teammate_tool_subset` 注入 | _TeamSubsetRegistry allowed == 该集 ∪ team 工具 |
| 2.2.3 | 默认（不传新参） | AgentLoop max_iter==30、只读集 ★BC |
| 2.3.1 | register_code_tools(spawn_team_handler=h, schema=s) | registry 有 "spawn_team"，toolset=="control" |
| 2.3.2 | 不传 spawn_team_handler | 不注册（BC） |

> 真 store 集成：复用现有 `scripts/manual_team_smoke.py` fake runner 跑通池清零（spawn_team.py 已有）。

## TG-3.1/3.2/3.3 — 非阻塞 + queue（`tests/test_subagent_nonblocking.py`）

| # | 用例 | 断言 |
|---|---|---|
| 3.1.1 | `SubagentRegistry.register/update/get/list` | 状态机正确 |
| 3.1.2 | `cancel_all()` | 活 run 的 Task.cancel 被调；已完成的不动 ★V5 |
| 3.2.1 | `spawn_subagents` 2 任务 | 立即返回 run_ids（不 await）；registry 有 2 条 running |
| 3.2.2 | `await_subagents(run_ids)` | 等到完成、返回 results；completion_queue 入队 |
| 3.3.1 | agent_loop 注入 registry + 预置 completion_queue 1 条 | run() 某轮顶部 drain → messages 追加 [子代理完成] user 消息 |
| 3.3.2 | registry=None（BC） | run() 不 drain，行为与现状一致 ★BC |
| 3.3.3 | cancel 路径调 cancel_all | 活子代理被取消 |

## TG-4.1/4.2 — 守门 + 模型路由（`tests/test_subagent_guard_model.py`）

| # | 用例 | 断言 |
|---|---|---|
| 4.1.1 | `termination_gate_factory` 给定 | AgentLoop ctor 收到 gate |
| 4.1.2 | factory=None（BC） | 无 gate，行为不变 |
| 4.2.1 | kind.model="gpt-5.4-mini" | runner 用 `_make_shim_for_model("gpt-5.4-mini")` 建的 shim |
| 4.2.2 | kind.model=None | 用父 shim ★BC |

---

## 回归门控（每阶段末）

- **BC 字节基线**：三 flag OFF 跑 `agent_parallel`/`agent`/spawn_team 现有测试套，diff=0（V4）。
- 全量：`cd backend && python -m pytest`（当前基线 ~2300+ passed，新增不破旧）。
- 前端：`cd tauri-app && npm run test`（vitest）+ `tsc --noEmit` 0 err（P3 SubagentProgressPanel）。
- 准入硬条件冒烟：新建 `scripts/acceptance/subagent_driver_smoke.py`（仿 `last_mile_smoke.py`）— 裸进程 boot + 调度器 6→4 背压 + kind 路由 + flag-OFF BC，期望 `DECISION: SHIP`。
