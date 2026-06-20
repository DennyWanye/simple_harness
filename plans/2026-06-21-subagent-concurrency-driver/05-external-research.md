# 05-external-research — 子代理并发开源对标（码级）

> 调研日期 2026-06-21。三个目标仓库均确认存在，源码自 `raw.githubusercontent.com` / GitHub / DeepWiki 索引读取。
> 结论喂入 [`00-PRD.md`](./00-PRD.md) §2 与 [`02-implementation-plan.md`](./02-implementation-plan.md) 的设计。
> **相关性**：deskpet = 单进程 Python asyncio + ReAct。**Hermes（Python，线程+完成队列）与 OpenClaw（单进程纯 promise lane 队列）几乎 1:1 映射；OpenHuman（Rust/Tokio）模式可借、代码不可搬。**

---

## 1. Hermes — `NousResearch/hermes-agent`（Python，agent 编排，非同名模型）

读的源文件：`tools/delegate_tool.py`（阻塞委派工具）、`tools/async_delegation.py`（非阻塞后台派发 + 注册表）、`tools/process_registry.py`（后台进程/完成队列）、`agent/conversation_loop.py`（ReAct loop）、docs。

- **生成模型**：默认 hub-and-spoke，显式可递归。子代理 `role="leaf"`（默认，不能再委派）/ `role="orchestrator"`（可 spawn，受 `delegation.max_spawn_depth` 默认 **1** 约束）。递归靠结构阻断：
  ```python
  DELEGATE_BLOCKED_TOOLS = frozenset([
      "delegate_task", "clarify", "memory", "send_message", "execute_code"])
  ```
- **并发机制**：同步路径 `ThreadPoolExecutor.submit()+.result(timeout)`；异步路径 `_DaemonThreadPoolExecutor`（daemon 线程不挡退出）立即返回。cap 在派发处单锁检查，**超 cap 拒绝不排队**：
  ```python
  with _records_lock:
      running = sum(1 for r in _records.values() if r["status"]=="running")
      if running >= max_async_children:   # _DEFAULT_MAX_ASYNC_CHILDREN = 3
          return {"status": "rejected", ...}
  ```
- **事务路由**：无独立 planner；父 LLM 拆分。路由**按 toolset 选择**（`toolsets=["web"]`→研究子代理；`["terminal","file"]`→编码子代理），单/批：
  ```python
  delegate_task(goal, context, toolsets=[...])          # 单
  delegate_task(tasks=[{goal, toolsets}, {...}])        # 并行批
  ```
- **隔离**：强。每子代理全新对话（无父历史）、独立 session/terminal/file-cache、受限 toolset（∩ 父可用 + 永剥 BLOCKED）。**父上下文只见委派调用 + 最终摘要**——子代理中间 tool/推理不入父（关键上下文窗口收益）。
- **聚合**：结构化 `results[]`（task_index/status/summary/api_calls/duration/tokens/tool_trace/error）。异步走 `_finalize()→_push_completion_event()→completion_queue`，在父**下一回合**作为新消息 drain——刻意保 role 交替合法 + prompt cache。
- **生命周期**：每 record 有 `interrupt_fn`，`interrupt_all(reason)` 级联；`child_timeout_seconds:0`（默认无墙钟）；`ProcessRegistry` 单独管后台进程（`proc_xxx`，200KB 滚动 buffer，`MAX_PROCESSES=64`，JSON checkpoint 崩溃恢复）。
- **LLM 接口**：`delegate_task(goal?, context?, toolsets?, tasks?, max_iterations?, role?, background?)`。默认 `max_concurrent_children=3, max_spawn_depth=1`。

## 2. OpenClaw — `openclaw/openclaw`（TypeScript，单 Node 进程，无 worker 线程）

读的源文件：`src/agents/tools/sessions-spawn-tool.ts`（LLM 工具，TypeBox schema）、`src/agents/subagent-spawn.ts`（`spawnSubagentDirect()`）、`docs/tools/subagents.md`、`docs/concepts/queue.md` + `src/gateway/server-lanes.ts`（`applyGatewayLaneConcurrency()`）。

- **生成模型**：hub-and-spoke，opt-in 嵌套。`sessions_spawn` **非阻塞**，立即返 `{status:"accepted", runId, childSessionKey}`。session key 编码深度（`agent:<id>:subagent:<uuid>:...`），depth vs `maxSpawnDepth`（默认 1，硬上限 5），活子代理数 vs `maxChildrenPerAgent`（默认 5，1–20），超则 `forbidden`。
- **并发机制（亮点）**：**lane-aware FIFO 队列，纯 TS+promise，无外部依赖无 worker 线程**。命名 lane 各有 cap：

  | lane | 含义 | 默认 cap |
  |---|---|---|
  | `session:<key>` | 每 session 至多 1 活 run | 1 |
  | `main` | 入站用户消息 + 心跳 | 4 |
  | `subagent` | `sessions_spawn` 全部 | **8** |
  | `nested` | 回合内嵌套 tool | 1 |
  | 未配置 | fallback | 1 |

  run 先入 `session:<key>` lane（每 session 串行）再入全局 lane（保总并行）。背压真实：per-session 队列 `cap:20`、`debounceMs:500`。**这是对 asyncio agent 最直接可移植的设计——就是一个按类别的有界 async 队列。**
- **事务路由**：父 LLM 拆分；按 `agentId`（白名单）+ model/thinking 覆盖。`taskName`（`[a-z][a-z0-9_-]{0,63}`）作跨回合稳定句柄。
- **隔离**：`context:"isolated"`（默认空）vs `"fork"`（branch 父 transcript）；session 工具默认从子代理移除；`sandbox:"inherit"|"require"`；`cleanup:"keep"|"delete"`；独立 token 预算；可配更便宜子代理模型。
- **聚合**：**push 非 poll**。子代理完成触发父 session 内部 `agent` 回合，带 Result+Status+stats。等待原语 = **`sessions_yield`**（结束当前模型回合、等 runtime 完成事件，而非轮询）。
- **生命周期**：`/stop` 中止父 + **级联活子代理**；`runTimeoutSeconds:0`、`archiveAfterMinutes:60`；hook `subagent_spawned`/`subagent_ended`；`registerSubagentRun()` 集中追踪。
- **LLM 接口**（TypeBox）：`sessions_spawn({task, taskName?, agentId?, model?, context?, cleanup?, sandbox?, ...})` + `sessions_yield()`。默认 `maxSpawnDepth:1, maxChildrenPerAgent:5, maxConcurrent:8`。

## 3. OpenHuman — `tinyhumansai/openhuman`（Rust61%/TS36%，Tauri 桌面宠物相邻 — 架构最像 deskpet）

来源：GitHub `src/` 树 + DeepWiki 源分析页 4.1/4.2（DeepWiki 索引真 Rust 源）。命名文件：`src/openhuman/agent/harness/session/turn.rs`、agent 定义注册表、`src/core/{runtime,dispatch,event_bus,shutdown}.rs`。

- **生成模型（唯一带 typed role registry 的）**：agent 是 `AgentDefinitionRegistry` 条目，来自 (a) 内置 `orchestrator/researcher/planner/code_executor/critic/archivist/...` (b) 用户 TOML `agents/*.toml`。每个 `AgentDefinition` 有 id/system_prompt/model/`ToolScope`/`omit_*` flags/`subagents` 列表。agent 带 tier（chat/reasoning/worker）强制层级（chat 不能 spawn chat）。
- **事务路由**：orchestrator 拿运行期合成的 per-definition 委派工具（`collect_orchestrator_tools()`）：`delegate_{agent_id}`（如 `delegate_researcher`）、`delegate_to_integrations_agent(toolkit=...)`。**路由到「事务种类」是显式 typed 的（一类一工具）**，而非 Hermes/OpenClaw 的「泛型 spawn + toolset 参数」。`render_delegation_guide` 把可用名册注入 orchestrator prompt。
- **并发&隔离**：Tokio runtime + `dispatch.rs` + `event_bus`。spawn 流程读 Tokio task-local `PARENT_CONTEXT`，`filter_tool_indices` 限工具，`PromptBuilder` 追加 `SUBAGENT_ROLE_CONTRACT_SUFFIX`。递归靠剥 `spawn_subagent`/`delegate_*`。`MAX_SPAWN_DEPTH=3`。子代理共享 provider 连接但 `omit_*` 控记忆/技能省 token。
- **回合执行&聚合**：`Agent::turn()` 从盘 resume（KV-cache 复用）→ prompt build → MemoryLoader → bounded provider loop（ContextManager compaction + ToolDispatcher）→ 流 `text_delta`/`tool_call`/`subagent_spawned`。子代理上下文满可自动 compact 摘要；async 子代理**可运行中 steer + 干净 await**，在「Background tasks」面板呈现，结果**空闲门控 + 批量**交付（不中途打断）。
- **诚实 gap**：DeepWiki 未暴露精确 Tokio 原语（JoinSet vs spawn+mpsc）与每父最大并发子代理数。已确认：task-local 隔离、`MAX_SPAWN_DEPTH=3`、depth/tier 门控、空闲门控批量完成。

## 参考（简）

- **Claude Code Task 工具**：hub-and-spoke，**严格不可递归**；并发 ~10 上限分批；社区建议 API 档位 4–6；每子代理全新隔离上下文、只回最终摘要。印证主流模式 = **扁平 fan-out + 只回摘要**。
- **OpenHands**：`AgentDelegateAction` 父交控制给具名子代理，历史上更偏**顺序 hand-off** 而非宽并行。typed 委派参考（类 OpenHuman）。
- **Cline**：单 agent VS Code loop，子代理/并行非其核心。并发相关性最低。

---

## 跨项目对比表

| 维度 | Hermes (Py) | OpenClaw (TS) | OpenHuman (Rust) | Claude Code |
|---|---|---|---|---|
| 生成模型 | hub-spoke；leaf/orchestrator 递归 | hub-spoke；嵌套≤5 | hub-spoke；tier 门控 depth3 | hub-spoke 不递归 |
| 并发机制 | daemon ThreadPool，超 cap **拒绝** | **lane FIFO promise 队列**，超 cap **排队** | Tokio + task-local 父ctx | 分批 ~10 |
| cap 默认 | `max_concurrent_children=3` | subagent lane=8，maxChildren=5 | MAX_SPAWN_DEPTH=3 | ~10 |
| 事务路由 | `toolsets` 参数 | `agentId`/model | **typed registry** `delegate_{role}` | prompt/agent file |
| 隔离 | 全新对话/独立 session/blocked 集/∩父 | isolated\|fork/剥 session 工具/sandbox | ToolScope+omit_* | 全新/只回摘要 |
| 聚合 | 结构化 results[]；async completion_queue 回合边界 | **push** → 父回合；`sessions_yield` | 空闲门控批量回合边界 | 摘要回父 |
| 取消 | interrupt_fn/interrupt_all；sync result(timeout) | `/stop` 级联子；runTimeout | steer+cancel+shutdown | 父中止 |
| LLM 接口 | `delegate_task(goal/tasks,toolsets,role,background)` | `sessions_spawn({...})`+`sessions_yield()` | `delegate_{role}(...)` | `Task(desc,type)` |

---

## 值得偷的 8 个模式（按 deskpet 适配度排序）→ 映射到本计划

| # | 模式 | 来源 | 本计划落点 |
|---|---|---|---|
| 1 | **Lane-aware 有界 async 队列**（最高 ROI） | OpenClaw | P0 `SubagentScheduler`（global+per-kind+per-session semaphore） |
| 2 | **只回摘要 + 回合边界注入** | 三家 | P3 completion_queue（agent_loop 回合顶 drain）；P1/P2 阻塞聚合也只回 summary |
| 3 | **cap 策略显式：拒绝 vs 排队** | Hermes vs OpenClaw | P0 调度器用户面向 lane 排队背压；后台 fire-forget 可拒绝快失败 |
| 4 | **spawn + yield/collect 拆两工具** | OpenClaw/Hermes | P3 `spawn_subagents`/`await_subagents` |
| 5 | **typed kind + framing/工具子集/模型** | OpenHuman（折中：kind 字段非一类一工具） | P0 `task_kinds.py` |
| 6 | **递归守门 = 剥工具 + depth** | 三家 | 沿用 `_FORBIDDEN_NESTED_TOOLS` + kind 剥 spawn 类；depth 计数留扩展位 |
| 7 | **每子代理隔离：全新上下文 + 工具集 ∩ 父** | Hermes | 沿用 `_SubsetRegistryAdapter` |
| 8 | **取消级联 + 空闲门控交付** | OpenClaw/OpenHuman | P3 `SubagentRegistry.cancel_all()` 绑 `/stop` |

**不偷**：OS 线程池（Hermes）/Tokio JoinSet（OpenHuman）——asyncio `create_task`+`Semaphore`/`Queue` 等价无线程开销。借 Hermes 的**注册表/生命周期数据模型** + OpenClaw 的 **lane 队列**，不借线程。

---

## 来源链接

- [tinyhumansai/openhuman](https://github.com/tinyhumansai/openhuman) · [DeepWiki 4.2](https://deepwiki.com/tinyhumansai/openhuman/4.2-agent-definitions-and-sub-agent-delegation) / [4.1](https://deepwiki.com/tinyhumansai/openhuman/4.1-agent-session-and-turn-execution)
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) · [delegate_tool.py](https://github.com/NousResearch/hermes-agent/blob/main/tools/delegate_tool.py) · [async_delegation.py](https://raw.githubusercontent.com/NousResearch/hermes-agent/main/tools/async_delegation.py)
- [openclaw/openclaw](https://github.com/openclaw/openclaw) · [sessions-spawn-tool.ts](https://github.com/openclaw/openclaw/blob/main/src/agents/tools/sessions-spawn-tool.ts) · [queue.md](https://github.com/openclaw/openclaw/blob/main/docs/concepts/queue.md)

> ⚠️ **校验提醒**（评审子代理请复核）：仓库名 openhuman/hermes/openclaw 由调研子代理按用户给的近似名解析确认；若实施时仓库不可达或名字有出入，以「8 个模式」的工程实质为准（模式不依赖特定仓库存活）。
