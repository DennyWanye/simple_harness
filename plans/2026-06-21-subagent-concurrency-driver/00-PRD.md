# 00-PRD — 桌宠子代理并发驱动（Subagent Concurrency Driver）

> **版本**: v0.1（待子代理对抗评审迭代 → v1.0 LOCKED）
> **日期**: 2026-06-21
> **状态**: 📋 规划中（未动代码）
> **关联状态档**: [`STATUS/AgentLoop.md`](../../STATUS/AgentLoop.md) · [`STATUS/AgentImprovements.md`](../../STATUS/AgentImprovements.md)
> **本目录**: `plans/2026-06-21-subagent-concurrency-driver/`

---

## 0. 一句话

给桌宠（deskpet）的 agent 一个**驱动子代理的能力**：当一个用户请求天然包含**多种事务（task-kinds）**或可拆成多个独立子任务时，主 agent 能把它们**路由到不同类型的子代理并发执行**（调研 / 编码 / 文件操作 / 文档生成 / 联网快查 …），用有界并发调度（lane-aware bounded concurrency）跑完、聚合结果、向用户实时反馈进度——而**不烧爆主 agent 的上下文窗口**。

---

## 1. 背景 — 现状盘点（来自架构勘探，全部已读码核实）

DeskPet 后端**已经有三层子代理基建**（详见 [`01-architecture.md`](./01-architecture.md) §1）：

| 层 | 入口 | 现状 | 能力 | 缺口 |
|---|---|---|---|---|
| **Tier-1 单子代理** | `agent` 工具 | ✅ 生产可用（flag 隐含 ON，code toolset） | 15 iter cap、只读默认子集、recursion guard、阻塞返回 1 个 string | 无写能力默认、无质量守门、单模型 |
| **Tier-2 并行子代理** | `agent_parallel` 工具 | ✅ 实现+测试，但 `features.agent_parallel=False` 默认关 | 2–4 个 hub-and-spoke、`asyncio.gather` 真并发、Sprint Contract 注入、错误隔离、G4 prompt-cache fork | **阻塞**、无事务类型路由（所有子代理同构、同模型）、无 WS 进度、并发只有 2–4 硬上限无背压、子代理无质量守门 |
| **Tier-3 团队/任务图** | `spawn_team()` + `TeamStore` + `TaskGraphStore` | 🟡 写好+单测过，但**未暴露为 LLM 工具、未接进 `main.py`** | 共享任务池原子 claim、mailbox、permission queue、DAG 依赖排序、最多 8 teammate | **不可用**：非 LLM-callable、无生产 wiring、无 WS 进度、无 permission approve UI |

**核心诊断**（`STATUS/AgentImprovements.md` 早已自标）：

> "并行子代理框架在、但缺调度/结果聚合/协作，半成品。"

也就是说——**地基已经打好，缺的是「驱动层」**：事务分型、有界调度、结果回流、实时反馈、生产接线。本计划**不重写**已测好的 `agent_tool.py` / `agent_parallel_tool.py` / `spawn_team.py`，而是**加性扩展**它们 + 新建一个调度/分型层 + 补齐接线与 UI。

---

## 2. 业界对标（子代理调研，全部确认真仓库 + 读源码）

调研三个开源 agent 项目的子代理并发实现（详见 [`05-external-research.md`](./05-external-research.md) 全文）：

| 维度 | **Hermes**（Python，`NousResearch/hermes-agent`） | **OpenClaw**（TS，`openclaw/openclaw`） | **OpenHuman**（Rust/Tokio，`tinyhumansai/openhuman`，桌面宠物相邻） |
|---|---|---|---|
| 生成模型 | hub-and-spoke，`max_spawn_depth>1` 可递归（leaf vs orchestrator） | hub-and-spoke，`maxSpawnDepth`≤5 | hub-and-spoke，tier 门控递归，depth 3 |
| 并发机制 | daemon `ThreadPoolExecutor`，超 cap **拒绝** | **lane-aware FIFO promise 队列**，每 lane 独立 cap，超 cap **排队**（背压） | Tokio tasks + task-local 父上下文 |
| cap 默认 | `max_concurrent_children=3` | `subagent` lane=8，`maxChildrenPerAgent=5` | `MAX_SPAWN_DEPTH=3` |
| 事务路由 | 按 `toolsets` 参数 | 按 `agentId`/model | **typed role registry** → `delegate_{role}` 一类一工具 |
| 隔离 | 全新对话、独立 session/terminal/file-cache、blocked-tool 集 | `context:isolated\|fork`、session 工具剥离、sandbox 档 | `ToolScope` 过滤 + `omit_*` flags |
| 聚合 | 结构化 `results[]`；async 走 `completion_queue` 在回合边界注入 | **push** 完成 → 触发父 session 内部回合；`sessions_yield` 等待 | 空闲门控、批量、回合边界注入 |
| 取消 | per-record `interrupt_fn` + `interrupt_all()` | `/stop` 级联子代理 | 可 steer + cancel + tokio shutdown |
| LLM 接口 | `delegate_task(goal/tasks, toolsets, role, background)` | `sessions_spawn({task, agentId, context})` + `sessions_yield()` | `delegate_{role}(...)` 按定义合成 |

**8 个「值得偷」的模式**（按对「单进程 Python asyncio ReAct agent」的适配度排序）：

1. **Lane-aware 有界 async 队列（OpenClaw，最高 ROI）** — 每种事务一个 `asyncio.Semaphore` lane + 全局 cap，纯 asyncio 零外部依赖，同时拿到「类型路由 + 背压 + 按类公平」。→ **本计划 P0 `SubagentScheduler`**。
2. **只回摘要 + 回合边界注入（三家共有）** — 子代理跑自己的 loop，父上下文只看 `{status, summary, stats}`，async 结果在**下一回合边界**作为新 tool/user 消息回流，绝不中途插入 → 保护父上下文窗口 + prompt cache + 严格 role 交替。→ **本计划 P3 completion queue**。
3. **cap 策略显式：拒绝 vs 排队（Hermes vs OpenClaw）** — 用户面向 lane 用排队背压（不丢用户请求），fire-and-forget 后台用拒绝快失败。→ **P0 调度器双策略**。
4. **spawn + yield/collect 拆两个工具（OpenClaw/Hermes）** — spawn 立即返回 run_id，单独 `await` 工具结束回合等结果，asyncio 天然（`create_task`+`wait`）。→ **P3 `spawn_subagents`/`await_subagents`**。
5. **typed role registry + 一类一 `delegate_<role>` 工具（OpenHuman）** — 比泛型 `spawn(toolsets=...)` 多花 prompt token，但 LLM 路由远更可靠，tier 廉价强制「谁能 spawn 谁」。→ **P0 `task_kinds.py` 事务分型**（本计划取折中：泛型工具 + `kind` 字段，而非一类一工具，理由见 §4 D2）。
6. **递归守门 = 剥工具 + depth/tier** — 默认 `max_spawn_depth=1`（扁平 fan-out），子代理工具集剔除 spawn 类工具。deskpet 现有 `_FORBIDDEN_NESTED_TOOLS` 已做。→ **沿用 + 加 depth 计数**。
7. **每子代理隔离：全新上下文 + 工具集 ∩ 父（Hermes）** — 不提权。deskpet `_SubsetRegistryAdapter` 已做。→ **沿用**。
8. **取消级联 + 空闲门控交付（OpenClaw/OpenHuman）** — 一个 `/stop` 取消父 + `task.cancel()` 所有活子；后台结果空闲时批量交付，不打断用户。→ **P3 取消级联**。

**最低优先级（不偷）**：OS 线程池（Hermes）/ Tokio JoinSet（OpenHuman）——asyncio 用 `create_task`+`Semaphore`/`Queue` 即等价，无需线程安全开销。借 Hermes 的**注册表/生命周期数据模型** + OpenClaw 的 **lane 队列**，不借它们的线程。

---

## 3. 目标用户故事

1. **多事务并发**：用户说「帮我**调研 2025 钠离子电池现状**，同时**把上次那个会议纪要做成 PPT**，再**查一下小米 SU7 的最新售价**」→ 主 agent 识别这是 3 个不同事务（research / doc / web），`spawn_subagents` 并发派 3 个不同类型子代理，各用各的工具集，进度实时冒泡，3 份结果聚合回桌宠一条消息。
2. **同构任务池**：用户说「把 `src/` 下这 12 个文件的中文注释都翻译成英文」→ 主 agent `spawn_team(num_teammates=4, kind="code")` 派 4 个同构 teammate 从共享池里 claim → 翻 → update，直到池空。
3. **非阻塞后台**：用户说「后台帮我调研一下竞品，我们先聊别的」→ `spawn_subagents(background=true)` 立即返回，桌宠继续闲聊；调研完在下一个空闲回合冒泡「竞品调研好了，要看吗？」。

---

## 4. 关键决策（D1–D10）

> 这些决策是评审迭代的焦点。每条给了理由与被否方案，便于子代理挑战。

- **D1 — 加性扩展，不重写**：复用 `agent_tool`/`agent_parallel_tool`/`spawn_team`，新增 `task_kinds.py` + `subagent_scheduler.py` + `subagent_registry.py` 三个新模块 + 接线。理由：三个底座都有单测覆盖 + 真机用例，重写是负价值（feedback_real_e2e）。
- **D2 — 事务分型用「泛型工具 + `kind` 字段」而非 OpenHuman 的「一类一 `delegate_<role>` 工具」**：`agent_parallel`/`spawn_subagents` 的每个子任务带一个 `kind` 枚举字段，由 `task_kinds.py` 解析成 `KindProfile`（工具子集 + iter cap + framing + 可选模型 + lane cap）。理由：deskpet 的事务种类（6 个）会增长，一类一工具会撑爆 schema；`kind` 字段保持单一 spawn 工具、schema 稳定，路由可靠性靠 enum + 描述补足。被否：OpenHuman 全 typed registry（太重，deskpet 工具总数有限）。
- **D3 — 并发用 lane-aware `asyncio.Semaphore`，纯 asyncio**：全局 cap（默认 4）+ 每 kind lane cap（research/code=2、fileops/web=3、doc=1）+ 每 session lane=1（可选）。超 cap **排队**（背压，不丢用户请求）。理由：OpenClaw 验证纯 promise/semaphore 够用；deskpet 已重度用 asyncio.gather，不引线程/队列中间件（feedback_no_sandbox_constraints 精神：单机桌宠不过度工程）。
- **D4 — 全部 flag-gated，出厂默认全关，BC 字节级**：新 flag `features.subagent_driver`（总开关，含分型+调度）、`features.agent_team`（暴露 spawn_team）、`features.subagent_nonblocking`（P3 非阻塞）。OFF 时新代码 short-circuit，`agent_parallel` 退回现状 `asyncio.gather` 路径。理由：项目铁律（所有新功能默认 OFF）。
- **D5 — 提高 `agent_parallel` fan-out 上限到由调度器管**：`_MAX_SUBAGENTS` 从 4 提到 8，但真实并发由 `SubagentScheduler` 全局 cap 背压（多的排队）。理由：用户可能一次列 5–6 个事务；硬卡 4 太死，调度器背压更优雅。
- **D6 — `spawn_team` 暴露为 LLM 工具 + 接进 main.py**：新建 `spawn_team_tool.py` 工厂（仿 `build_agent_parallel_tool`），`register_code_tools` 加 slot，main.py lifespan 构造 `TeamStore`（落 `<user_data>/teams/`）+ `TaskGraphStore`（复用 `_session_db`）+ 注入 `service_context`。`agent_parallel`=异构独立任务；`spawn_team`=同构共享池。理由：两者职责互补，team 的共享池/mailbox/DAG 是「N worker 啃 backlog」最佳模型。
- **D7 — 非阻塞 spawn + completion queue 走 P3 独立阶段**：`spawn_subagents`（立即返 run_ids）+ `await_subagents`（结束回合等结果）+ `SubagentRegistry`（run 记录）+ agent_loop 回合边界 drain completion queue 注入。理由：这是行为面最大改动（改 ReAct 循环），单独阶段降风险；P1/P2 的阻塞路径先落地见效。
- **D8 — WS 进度流式 + 前端卡片**：现状 `subagent_progress` 只写 `metrics.jsonl`（盘），用户全程看不到。新增 WS 广播（仿 `_todo_broadcaster`）+ 前端 `SubagentProgressPanel`。理由：用户体验缺口（D8 是产品价值核心，桌宠要「可见地忙」）。
- **D9 — 子代理质量守门平价（P4）**：子代理 `AgentLoop` 现在裸构造（无 verify_gate/TerminationGate）。P4 至少给一个 `TerminationGate`（防卡死/复读）。理由：高后果并发任务需要兜底，但非首发必需（15/20 iter cap 已是硬上界），故 P4。
- **D10 — 取消级联绑现有 `/stop` 通道**：`SubagentRegistry` 存活 `asyncio.Task` 句柄，`/stop` 时 `task.cancel()` 全部活子代理。理由：OpenClaw/OpenHuman 都这么做；防用户停了主对话但子代理还在烧钱。

---

## 5. 范围

### 5.1 In scope（不可少做，对应用户「不可以少做功能」）

- **P0** 事务分型 `task_kinds.py` + 有界调度 `subagent_scheduler.py` + 配置（新模块，零行为变更，单测独立）
- **P1** `agent_parallel` 路由进分型+调度 + 提 fan-out 上限 + `build_agent_tool` 支持 iter/工具子集参数 + WS 进度广播
- **P2** `spawn_team` 暴露为 LLM 工具 + `TeamStore`/`TaskGraphStore` 接进 main.py + team permission WS + 前端 approve
- **P3** 非阻塞 `spawn_subagents`/`await_subagents` + `SubagentRegistry` + completion queue 回合边界注入 + `/stop` 取消级联 + 前端 `SubagentProgressPanel`
- **P4** 子代理 `TerminationGate` 质量守门平价 + 每 kind 模型路由（shim factory）

### 5.2 Out of scope（明确不做，避免镀金）

- 跨进程 / 分布式子代理（Redis/celery）——单机桌宠不需要（D3 精神）
- 子代理间任意 mesh 通信——`spawn_team` 的 mailbox 已够；`agent_parallel` 子代理保持全隔离
- 递归深度 > 1 的 orchestrator 树——首发只做扁平 fan-out（D2/模式 6），depth 计数器留扩展位
- 子代理 git worktree 物理隔离——deskpet 是单机，用 per-task 临时目录 + 工具集隔离即可（非本计划）

---

## 6. Flag 表（出厂默认全 OFF，BC 字节级）

| flag | 位置 | 出厂 | 作用 | OFF 时行为 |
|---|---|---|---|---|
| `features.subagent_driver` | `[features]` | `false` | 总开关：分型路由 + 调度器接入 `agent_parallel`/子代理 | `agent_parallel` 退回现状 `asyncio.gather` 扁平路径；无 kind 路由 |
| `features.agent_team` | `[features]` | `false` | 暴露 `spawn_team` LLM 工具 + 构造 TeamStore/TaskGraph | `spawn_team` 工具不注册；store 不构造 |
| `features.subagent_nonblocking` | `[features]` | `false` | 暴露 `spawn_subagents`/`await_subagents` + completion queue | 工具不注册；queue 不 drain |
| `[agent.concurrency]` `global_concurrency` | `[agent.concurrency]` | `4` | 调度器全局并发 cap | — |
| `[agent.concurrency]` `lane_caps` | 同上 | `{research=2,code=2,fileops=3,doc=1,web=3,general=2}` | 每 kind lane cap | — |
| `[agent.subagent_kinds]` | TOML 段 | （内置默认）| 覆盖/新增 KindProfile（工具子集/iter/model/framing） | 用内置默认 |

> ⚠️ 配置读取必须走 `load_config()`/`AppConfig.raw["agent"]` 安全兜底模式——**不可**复刻 `config.config` 单例 bug（2026-06-21 `b05823b` 真机才揪出该单例不存在导致 `[research]` 开关全失效）。`features.*` 三个布尔加进 `FeaturesConfig` dataclass；`[agent.concurrency]`/`[agent.subagent_kinds]` 通过 `config.raw["agent"]` 直读 + 缺省兜底（沿用 §449 注释里 `[agent]` 段已是 raw-read 的既定模式）。

---

## 7. 风险登记

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 子代理并发把 relay/中转站打爆（429/504），尤其 4×子代理×多轮 | 调度器全局 cap=4 背压；kind lane 进一步限；relay 错误沿用现有重试链；P4 给 TerminationGate |
| R2 | `config.config` 单例陷阱重演——配置开关静默失效 | D6 注脚：强制 `load_config()` 兜底；TDD 加「真 config 读到 lane_caps」单测（仿 `b05823b` 回归测试）|
| R3 | 阻塞 `agent_parallel` 长任务卡死 recv loop / 触发 chat-cancel 竞态（参考 WI-7 H1 教训） | P3 非阻塞路径根治；P1/P2 阶段沿用现有 300s timeout + 在独立 task 跑（不阻塞 ws recv）|
| R4 | 子代理无质量守门 → 复读/卡死烧 token | iter cap（15/20）是硬上界；P4 加 TerminationGate；调度器 per-run 超时 |
| R5 | 事务路由 LLM 选错 kind → 工具子集不对、任务失败 | kind enum + 描述充分；KindProfile 工具子集是「kind 默认 ∪ 子任务显式 tools」并集再 ∩ 父注册表；未知 kind 回退 general（只读安全）|
| R6 | TeamStore 多 `.db` 文件堆积 `<user_data>/teams/` | team_id 用时间+随机；加 LRU/TTL 清理（沿用 tool_refs spill 的 400 文件自清模式）|
| R7 | 前端 WS 进度卡片与现有 tool_use_event 渲染冲突 | 新 WS event type `subagent_progress`，独立 store slice + 独立组件，不碰 ChatRow 派生 |
| R8 | DAG `TaskGraphStore` 与 goal_tasks 表耦合（marking done 触发 goal backfill） | 仅 `features.agent_team` + 显式 `goal_id` 时启用 goal-task 工具；无 goal 时纯 DAG（BC）|

---

## 8. 验收准入（一票否决，详见 04-manual-test-cases）

- **V1**（多事务并发）：真机 windows-mcp，桌宠收到含 3 种事务的请求 → 后端日志铁证 3 个不同 kind 的子代理并发跑（`subagent_scheduled kind=research/doc/web`）→ 3 份结果聚合回一条消息 → 进度卡片真机可见。
- **V2**（同构池）：`spawn_team` LLM 真调用 → N teammate 从共享池 claim → 池清零 → 聚合返回。
- **V3**（背压）：一次派 6 个子代理，全局 cap=4 → 日志证 4 跑 2 排队 → 全部完成不丢。
- **V4**（BC 字节级）：三 flag 全 OFF → `agent_parallel` 行为与现状字节级一致（回归测试 diff=0）。
- **V5**（取消级联）：派后台子代理 → `/stop` → 日志证所有活子代理 `task.cancel()` 收割。

---

## 9. 文档导航

| 文件 | 作用 |
|---|---|
| [`00-PRD.md`](./00-PRD.md) | 本文档 — 愿景/对标/决策/范围/flag/风险/准入 |
| [`01-architecture.md`](./01-architecture.md) | 现状三层架构精确码级地图 + 目标架构 + 新模块 + 数据契约 |
| [`02-implementation-plan.md`](./02-implementation-plan.md) | **实施细节** — P0–P4 每个 WI 的 file:function 精确改动 + 签名 + 伪码 |
| [`03-TDD.md`](./03-TDD.md) | 测试组（每 WI 红→绿用例） |
| [`04-manual-test-cases.md`](./04-manual-test-cases.md) | windows-mcp 真机 E2E 用例（V1–V5 + 边角） |
| [`05-external-research.md`](./05-external-research.md) | 子代理调研全文（Hermes/OpenClaw/OpenHuman 码级 + 8 模式 + 对比表） |
