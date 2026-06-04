# P0-1 执行 Blueprint — 目标持久化与抗漂移

> 状态：v1 可执行实现蓝图（架构师产出，只读代码后细化）
> 日期：2026-06-04
> 依据：`00-PLAN.md` §2（P0-1）+ `research/deskpet-self-audit/p0-1-goal-persistence.md` +
> `research/best-practices-goal-completion/README.md` §4.2（6 缓解模式）+ `research/cc-haha/README.md` §4.1（Task 族）
> 锁定决策：**WI-1.1 先行**；**WI-1.2 Task 任务图做到跨子 agent 共享状态**。

---

## 0. 现状代码结构速览（已 Read 校准）

| 组件 | 文件:行 | 现状事实 |
|---|---|---|
| `SessionGoal` dataclass | `backend/deskpet/agent/goal_store.py:27-57` | 字段：`session_id / text / set_at / max_iterations(10) / iterations_used / done`。**纯内存** dict（`:67`），重启即丢（`:10-11` 注释 TODO v2） |
| `SessionGoalStore` API | `goal_store.py:60-124` | `set / get / clear / mark_done / increment_iteration`，**全 sync 无 I/O** |
| `/goal` slash | `backend/deskpet/commands/__init__.py:101-133` | `_handle_goal` 调 `store.set/get/clear`；返回 `goal_set/goal_status/goal_cleared` |
| AgentLoop 构造接电 | `backend/agent/agent_loop.py:404-405,434-435` | `session_goal_store / goal_checker` 两个 optional 注入，None→BC 跳过 |
| AgentLoop end_turn goal 块 | `agent_loop.py:1059-1122` | 末轮调 `goal_checker.check(goal.text, working_messages)`；done=False→`increment_iteration` + 注入 `[goal] 未达成` system message + `continue`；done=True→`mark_done` |
| GoalChecker | `backend/deskpet/agent/goal_checker.py:131-204` | LLM-judged，只喂最近 5 条 assistant 消息；safe-fail 返 `(True,"checker_error")` |
| ContextCompressor | `backend/deskpet/agent/context_compressor.py:71-238` | token≥75% 触发；保留 system+first_n(3)+last_n(6)，中段 haiku summarize；**全文无 goal**；`compress(messages)` 单参 |
| history_compactor | `backend/agent/history_compactor.py:62-181` | `len>20` 或 `chars>60k` 触发；`compact_messages(messages, summarize_fn=...)`；**全文无 goal** |
| spawn_team | `backend/deskpet/agent/team/spawn_team.py:66-92,148-252` | `_TEAM_CHARTER_TEMPLATE` 无 goal 段；`spawn_team(...)` 签名无 `parent_goal_text`；`_build_charter` 三参 |
| TeamStore | `backend/deskpet/agent/team/team_store.py:63-88` | 每 team 独立 `.db`，`TeamTask(task_id/description/status/claimed_by/result...)`；**无依赖字段**；team 结束即销毁 |
| auto_resume | `backend/agent/auto_resume.py:219-225` | nudge 分支构 `new_msgs = original_msgs + [supervisor_hint]`；**不读 goal** |
| SessionDB | `backend/deskpet/memory/session_db.py:83-1208` | aiosqlite+WAL+`_with_retry`；已有 `code_todos / session_plans / code_sessions / supervisor_hints` 多组 sidecar 表，全是 `INSERT...ON CONFLICT` upsert 模式 |
| schema 落表惯例 | `backend/deskpet/memory/memory_v2_schema.py:35-208` | **运行时 `CREATE TABLE IF NOT EXISTS`**（不写 migration、不 bump user_version、不动测试 pin v16）；`ensure_memory_v2_tables(db_path)` 幂等带 per-path cache |
| main.py 接电点 | `backend/main.py:817(SessionDB构造) / 1067-1081(goal_store构造+register) / 5300-5314(build_agent 取用)` | `_session_db` 对象在 `1067` 行已存在 → goal_store 可在此 `bind_persistence(_session_db)` |
| 出厂 flag | `backend/config.py:326-347` `FeaturesConfig.goal_mode=False` | goal_mode 默认 OFF；config.features 必须显式开 |

**关键发现**：DeskPet 已经把"sidecar 表 + runtime CREATE TABLE IF NOT EXISTS + bind 模式"跑顺了 4 次（code_todos / session_plans / code_sessions / supervisor_hints）。P0-1 **完全沿用这套惯例**，不引入新迁移机制，不 bump user_version。

---

## WI-1.1 — Durable Goal Store（★ 先行，第一块多米诺）

### 1. 目标文件
- **改**：`backend/deskpet/agent/goal_store.py` — 扩 `SessionGoal` 字段 + 给 `SessionGoalStore` 加 `bind_persistence` / async 持久方法
- **改**：`backend/deskpet/memory/memory_v2_schema.py` — `_DDL` 追加 `session_goals` + `session_subgoals` 两表
- **改**：`backend/main.py:~1075` — 构造后 `_session_goal_store.bind_persistence(_session_db)`；并在 lifespan 启动后 `await _session_goal_store.load_persisted()`
- **改**：`backend/deskpet/commands/__init__.py:_handle_goal` — set/clear 走 async（落库）；新增 `/goal sub <text>` 子目标登记（可选，见 WI-1.2 协同）
- **新建**：`backend/tests/test_goal_store_persistence.py`

### 2. 数据结构 / schema

**`SessionGoal` 扩字段**（向后兼容，新字段全有默认值）：
```python
@dataclass
class SessionGoal:
    session_id: str
    text: str
    set_at: float
    max_iterations: int = 10
    iterations_used: int = 0
    done: bool = False
    # —— 新增 ——
    goal_id: str = ""              # uuid4，主键；空=未落库的内存态（BC）
    status: str = "active"         # active | done | abandoned（生命周期，见 §7）
    progress: float = 0.0          # 0.0~1.0，由子目标完成比驱动（WI-1.2 回填）
    updated_at: float = 0.0
    subgoals: list["SubGoal"] = field(default_factory=list)  # WI-1.2 执行层
```

**SessionDB 新表**（追加进 `memory_v2_schema._DDL`，CREATE TABLE IF NOT EXISTS）：
```sql
CREATE TABLE IF NOT EXISTS session_goals (
    goal_id        TEXT    PRIMARY KEY,         -- uuid4
    session_id     TEXT    NOT NULL,
    text           TEXT    NOT NULL,
    status         TEXT    NOT NULL DEFAULT 'active',  -- active|done|abandoned
    progress       REAL    NOT NULL DEFAULT 0.0,
    max_iterations INTEGER NOT NULL DEFAULT 10,
    iterations_used INTEGER NOT NULL DEFAULT 0,
    set_at         REAL    NOT NULL,
    updated_at     REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_goals_sid
    ON session_goals(session_id, status);
```
> **多目标并存决策**：表用 `goal_id` PK（非 `session_id` PK），物理上支持一 session 多 active goal；但 **WI-1.1 落地阶段 store API 仍是 last-write-wins 单活跃目标**（`get(session_id)` 返回最新 `status='active'` 一行），多目标留 P0-1 之后。这样字节级契约不锁死、又不在本轮引入多目标复杂度。

### 3. 数据流 / 接线点

- **写**：`/goal <text>` → `_handle_goal` → `store.set(sid, text)`（sync 写内存）→ **新增** `await store._persist(goal)`（async 落 `session_goals`）。set 时把同 session 旧 `active` 行置 `abandoned`（last-write-wins 落库一致）。
- **读（恢复）**：lifespan 启动 → `await store.load_persisted()` 把所有 `status='active'` 行灌回内存 dict（重启恢复的核心路径）。
- **流出接口**（给 WI-1.3/1.4/P0-2/P0-3）：新增同步只读 `store.get_goal_text(session_id) -> str | None`（封装 `get().text`，None-safe）——**这是 P0-1 对外的稳定契约**（见结尾）。

### 4. SessionDB 落库方案
- **沿用** `memory_v2_schema` runtime DDL，不新建 migration、不 bump user_version（延续 4 次先例）。
- store 不直接持有 aiosqlite，而是 `bind_persistence(session_db)` 后通过 **SessionDB 新增的薄方法** 读写（与 `upsert_session_plan / get_session_plan` 完全同构）：
  - `SessionDB.upsert_session_goal(goal_id, session_id, text, status, progress, max_iterations, iterations_used, set_at, updated_at)`
  - `SessionDB.get_active_goals(session_id) -> list[dict]`
  - `SessionDB.list_active_goals() -> list[dict]`（启动恢复用）
  - 每个方法首行 `await ensure_memory_v2_tables(self._db_path)`（与 session_plans 三方法一致，全新 DB 自带建表）。
- **未绑定时**（`bind_persistence` 没调 / session_db=None）→ 退化为纯内存（当前行为），保证 BC + 测试隔离。

### 5. re-anchoring（本 WI 不实现，仅暴露接口）
WI-1.1 只负责"目标活下来 + 可读"。re-anchor 注入在 WI-1.3。本 WI 交付 `get_goal_text(sid)` 即满足 1.3 依赖。

### 6. handoff（同上，WI-1.4 用 `get_goal_text`）

### 7. 边界 case / 防回归
- **生命周期**：`status` 三态 `active → done`（`mark_done` 落库）/ `active → abandoned`（`/goal clear` 落库 `abandoned` 而非物理删，保留历史给 P0-3 §3.1 沉淀）。
- **多目标**：表支持、API 单活跃（见 §2 决策）。`get` 取 `updated_at` 最新的 active 行。
- **字节级契约 / flag 默认**：`goal_mode=False` 默认不变；flag OFF → store=None → 整链 BC。**新表只在 goal_mode ON 且首次写时才 CREATE**（runtime DDL 惰性），DB 对 flag-OFF 用户字节不变（与 memory_v2 strangler-fig 一致）。
- **iterations_used 重启归零修复**：恢复时把落库的 `iterations_used` 灌回（修自查条目 5 "resume 后从 0 计数"）。
- **并发**：单 session 串行（AgentLoop per-WS 单 task），沿用 store 注释假设；落库走 SessionDB `_write_lock`。

### 8. 测试计划
- **单测**（`test_goal_store_persistence.py`，用 `tmp_path` + `_reset_cache_for_tests`）：
  - `set→重新构造 store→load_persisted→get` 返回同 text（核心：模拟重启）
  - `mark_done` 落库后恢复 `status='done'` 不在 active 召回
  - `/goal clear` 落 `abandoned` 不物理删
  - 未 bind_persistence → 纯内存、不抛、无表创建（BC）
  - flag-OFF 路径 store=None → `_handle_goal` 返 disabled error（已存在断言，回归）
- **真机 E2E**（windows-mcp，遵循 HARD CONSTRAINT）：
  - TC-1.1-restart：启 Tauri（注入 `DESKPET_BACKEND_DIR`+`features.goal_mode=true`）→ 输入框输 `/goal 帮我整理本周三个会议纪要` → 截图确认 goal_set 卡 → **taskkill deskpet.exe + 重启 Tauri** → `/goal`（查状态）→ 截图确认目标仍在、iterations 非 0 归零 → backend log grep `goal_store.load_persisted` / `companion_code_v1_goal_mode_ready`。
  - **pass^k**：restart 用例连跑 k=3 次都恢复（防偶发空表/race）。

### 9. build order（WI 内）
1. `memory_v2_schema._DDL` 加两表 + 单测建表幂等
2. `SessionDB` 加 3 薄方法（upsert/get_active/list_active）+ 单测 round-trip
3. `SessionGoal` 扩字段（全默认值，先不接子目标）
4. `SessionGoalStore.bind_persistence` + async `_persist` + `load_persisted`
5. `_handle_goal` 改 async 落库 + main.py bind + 启动 load
6. 单测全绿 → windows-mcp restart E2E

---

## WI-1.2 — Task 任务图（跨子 agent 共享状态）★

> 锁定：**做到跨子 agent 共享状态**，作为 goal 的执行层（参考 cc-haha Task 族 + best-practices §4.2 模式 2「显式子目标跟踪」）。

### 1. 目标文件
- **新建**：`backend/deskpet/agent/task_graph.py` — `TaskNode` dataclass + `TaskGraphStore`（持久 + 依赖 + 跨 agent claim）
- **改**：`backend/deskpet/memory/memory_v2_schema.py` — 加 `goal_tasks` 表
- **改**：`backend/deskpet/memory/session_db.py` — `goal_tasks` CRUD + 原子 claim（**复用 TeamStore `claim_task` 的 `UPDATE...RETURNING` 原子模式**，但落在共享 state.db 而非 per-team db）
- **新建 / 改**：工具族 `TaskCreate / TaskUpdate / TaskList / TaskGet`（`backend/deskpet/tools/task_graph_tools.py`），注册进 tool registry；**并加进 `build_teammate_tools` 的子 agent 工具集**（这是"跨子 agent 共享"的接电点）
- **改**：`backend/deskpet/agent/team/spawn_team.py` — 子 agent 工具集合并 task_graph 工具

### 2. 数据结构 / schema

```python
@dataclass
class TaskNode:
    task_id: str            # uuid4
    goal_id: str            # FK → session_goals.goal_id（绑到目标，跨 session 可查）
    session_id: str
    title: str
    status: str             # pending | claimed | in_progress | done | failed | blocked
    depends_on: list[str]   # 前驱 task_id 列表（DAG 依赖）
    claimed_by: str | None  # 哪个 (sub)agent 占用（跨 agent 共享的关键字段）
    result: str | None
    created_at: float
    updated_at: float
```

```sql
CREATE TABLE IF NOT EXISTS goal_tasks (
    task_id     TEXT    PRIMARY KEY,
    goal_id     TEXT    NOT NULL,
    session_id  TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    depends_on  TEXT    NOT NULL DEFAULT '[]',   -- JSON array of task_id
    claimed_by  TEXT,
    result      TEXT,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goal_tasks_goal ON goal_tasks(goal_id, status);
```

**跨 agent 共享读写接口**（`TaskGraphStore`，所有 async、落共享 state.db）：
- `create(goal_id, session_id, title, depends_on=[]) -> TaskNode`
- `claim_ready(goal_id, agent_id) -> TaskNode | None` —— **原子**：只 claim `status='pending'` 且 `depends_on` 全 `done` 的任务（依赖未满足的不可领，DAG 调度核心）。SQL 用 `UPDATE goal_tasks SET status='claimed', claimed_by=? WHERE task_id=(SELECT ... ready ... LIMIT 1) AND status='pending' RETURNING *`（沿用 TeamStore 原子 claim）。"ready" 判定：Python 侧先查出依赖全 done 的候选集再带进 WHERE（SQLite 表达 DAG ready 用子查询较繁，分两步：先 `SELECT` ready 候选，再原子 claim 单个）。
- `update(task_id, status, result=None)` —— 任一 agent 可写；done 时触发 `_recompute_goal_progress(goal_id)` 回填 `session_goals.progress`（§WI-1.1 progress 字段在此被驱动）。
- `list(goal_id) -> list[TaskNode]` —— 父/兄弟 agent 互见进度（自查条目 2 缺口）。
- `get(task_id) -> TaskNode | None`

### 3. 数据流 / 接线点
- **共享面**：TeamStore 是 per-team 临时 db（结束销毁）；`goal_tasks` 落 **共享 state.db** 且按 `goal_id` 关联——这是"跨子 agent 共享状态"与现有 TeamStore 的关键区别。子 agent 通过 charter 拿到 `goal_id`（WI-1.4 注入），用 `TaskList(goal_id=...)` / `TaskUpdate` 读写同一图。
- **进度互见**：任一 agent `TaskUpdate(status='done')` → `goal_tasks` 落库 → 其他 agent 下次 `TaskList` 立即可见（SQLite 单一真相，WAL 读 O(µs)，沿用 TeamStore 哲学：无内存镜像避免缓存一致性 bug）。
- **接 goal**：`TaskCreate` 必带 `goal_id`（从 `store.get(sid).goal_id` 取）。

### 4. SessionDB 落库方案
同 WI-1.1：runtime DDL、共享 state.db、`_with_retry` 包裹、原子 claim 用 `RETURNING`。

### 5. re-anchoring（协同）
`current-subgoal` 即"最近 claimed/in_progress 的 TaskNode.title"。WI-1.3 注入 goal_text 时**带上当前子目标**（best-practices §4.2 模式 2"小到每个上下文都带着"）→ `re-anchor` 文案含 `[当前子目标] {title}`。

### 6. handoff（协同）
spawn_team 子 agent 工具集 **必须含 TaskList/TaskUpdate**，否则子 agent 看不到共享图。`build_teammate_tools` 合并 task_graph 工具（注意：这些不是 `FORBIDDEN_TEAMMATE_TOOLS`，要显式允许）。

### 7. 边界 case / 防回归
- **依赖环**：`create` 时检测 `depends_on` 是否成环（DFS），成环拒绝（防死锁，无任务可 claim）。
- **孤儿任务**：goal 转 `abandoned` → 其 `goal_tasks` 一并标 `failed`（生命周期级联）。
- **flag**：与 goal_mode 同 flag（task graph 是 goal 执行层，goal OFF 时不建表）。
- **TeamStore 不动**：现有 team 流程零改动（BC）；新工具是叠加，team_store 旧路径保留。

### 8. 测试计划
- **单测**：DAG ready 调度（B 依赖 A，A 未 done 时 claim 跳过 B）；两 agent 并发 `claim_ready` 不双占（原子性）；`update done` 回填 progress；环检测拒绝。
- **真机 E2E**：`/goal` 多步目标 → 触发 spawn_team → windows-mcp 截图 TodoListPanel/进度 → 确认子 agent 更新对父可见 → log grep `goal_tasks` claim/update 事件。
- **pass^k**：并发 claim 用例跑 k=5（race 稳定性）。

### 9. build order
1. `goal_tasks` 表 + SessionDB CRUD + 原子 claim 单测
2. `TaskGraphStore` + 依赖/环检测 + progress 回填
3. `TaskCreate/Update/List/Get` 工具 + 注册
4. 接进 `build_teammate_tools` + spawn_team 子 agent 可读写
5. E2E 跨 agent 可见性

---

## WI-1.3 — re-anchoring 抗漂移 ★

> best-practices §4.2 模式 1（durable doc 每决策点重读）+ 模式 4（压缩前 re-anchor）。

### 1. 目标文件
- **改**：`backend/deskpet/agent/context_compressor.py` — `compress` 加 `goal_text` 参数 + 注入
- **改**：`backend/agent/history_compactor.py` — `compact_messages` 加 `goal_text` 参数 + 注入
- **改**：`backend/agent/agent_loop.py` — 压缩调用点传 `goal_text`；并在**决策点**（每 N 轮 / tool_use 前）注入 re-anchor
- **新建**：`backend/deskpet/agent/goal_drift.py` — `GD_actions/GD_inaction` 量化跟踪器（轻量，无 LLM）

### 2. 数据结构 / 接口
```python
# goal_drift.py
@dataclass
class DriftSignals:
    gd_actions: int = 0    # 仍主动追求目标的轮数（assistant 输出/工具调用与 goal 相关）
    gd_inaction: int = 0   # 完成中间阶段后被动放弃的轮数（end_turn 但 goal 未 done 且无新动作）
    last_anchor_iter: int = 0
```
注入文案（system message，压缩后 / 决策点）：
```
[目标锚定] 当前目标：{goal_text}
[当前子目标] {current_subgoal_title}        # WI-1.2 协同，无则省略
请确保接下来的动作仍服务于上述目标，不要被中间步骤带偏。
```

### 3. 数据流 / 接线点
- **压缩前注入**（模式 4）：`agent_loop` 调 `should_compress→compress` 处（compressor 调用点），把 `store.get_goal_text(sid)` 传入。compressor 在 `system_msgs` 之后、`first_chunk` 之前追加一条 `[目标锚定]` system message（不进 summarize，永不被压掉）。
  - 改 `compress(messages, *, goal_text: str | None = None)`：goal_text 非空 → 注入到 `new_messages` 的 system 段尾（在 `_partition` 的 system_msgs 之后）。
  - 同理 `compact_messages(messages, *, summarize_fn, goal_text=None, ...)`：`inject_summary` 后在 system 栈尾追加锚定 message。
- **决策点注入**（模式 1）：`agent_loop` 主循环每 `iteration % anchor_every == 0`（默认 5）且有活跃 goal → append 一条锚定 system message（与 goal_checker nudge 不同：nudge 是"未达成"，anchor 是"别跑偏"，语义正交，自查条目 3 已辨析）。
- **GD 量化**：每轮 end_turn 后更新 `DriftSignals`（gd_inaction++ 当 done=False 且本轮无 tool_call）；emit metric `goal_drift`（沿用 `observability.metrics_sink.record` 既有模式，见 agent_loop:1080-1088）。

### 4. SessionDB 落库
DriftSignals 是 per-run 内存态即可（监控指标走 metrics.jsonl，不必落 SQLite）。**不新建表**。

### 5. re-anchoring 具体策略
- **触发条件**：(a) 压缩/紧缩前（必注）；(b) 每 5 轮决策点（可配 `[goal] anchor_every`）；(c) tool_use 前若距上次 anchor > anchor_every。
- **注入位置**：system 栈尾（活过压缩——compressor `_partition` 把 role=system 全保留）。
- **量化**：`gd_actions`（goal 相关动作轮）/ `gd_inaction`（中途放弃轮）emit 进 metrics.jsonl，监控面板读。

### 6. handoff（WI-1.4 复用同一锚定文案）

### 7. 边界 case / 防回归
- **flag**：`goal_mode` OFF 或无活跃 goal → `goal_text=None` → 注入全跳（compress/compact 行为字节不变，BC）。
- **token 成本**：锚定 message 短（< 100 token），决策点注入有 `anchor_every` 节流，不爆 context。
- **不重复**：同一 iter 不重复注入（`last_anchor_iter` 去重）。
- **契约**：`compress` 旧调用点不传 goal_text → 默认 None → 旧行为（现有 compressor 单测全绿）。

### 8. 测试计划
- **单测**：goal_text 非空 → compress 输出含 `[目标锚定]` 且在 system 段；None → 输出与旧版逐字节一致（BC 断言）；GD_inaction 在 done=False+无 tool 轮 +1。
- **真机 E2E（防漂移核心）**：构造长对话——`/goal 写一个 Python 脚本统计目录下文件数` → 中途插 20+ 轮闲聊把 token 顶过阈值触发压缩 → 压缩后继续 → 截图确认 LLM 仍回到原目标产出脚本（不是聊跑偏）→ log grep `[目标锚定]` 注入 + `goal_drift` metric。
- **pass^k**：漂移用例 k=3（LLM 非确定，验证锚定稳定把它拉回）。

### 9. build order
1. `goal_drift.py` GD 跟踪器 + 单测
2. compressor `goal_text` 参数 + 注入 + BC 单测
3. compactor `goal_text` 参数 + 注入 + BC 单测
4. agent_loop 压缩调用点 + 决策点 anchor 接电
5. 长对话防漂移 E2E

---

## WI-1.4 — handoff goal checkpoint ★

> best-practices §4.2 模式 3（派发带目标声明 + 回收按目标过滤）；cc-haha §4.1（Task 跨 agent）。

### 1. 目标文件
- **改**：`backend/deskpet/agent/team/spawn_team.py` — `spawn_team` 加 `parent_goal_text` / `parent_goal_id` 参数；`_TEAM_CHARTER_TEMPLATE` 加 `## Parent Goal` 段；`_build_charter` 加参数；结果回收加 goal 过滤
- **改**：`backend/main.py` spawn_team 调用点 — 传 `store.get_goal_text(sid)` + `goal_id`

### 2. 数据结构 / Charter 注入格式
`_TEAM_CHARTER_TEMPLATE` 顶部插入：
```
## Parent Goal (do not drift)
本团队服务于用户的总目标：{parent_goal_text}
你领取的每个任务都是该目标的子步骤。完成任务前自检：这个产出是否真的推进了上述目标？
若发现任务与总目标无关或冲突，在 result 里标注 "[off-goal]" 并说明。
```
`spawn_team(..., parent_goal_text: str | None = None, parent_goal_id: str | None = None)`；None → 渲染时省略该段（BC）。

### 3. 数据流 / 接线点
- main.py 在 spawn_team 调用点取 `store.get_goal_text(sid)` 传入（自查建议 3）。
- `parent_goal_id` 传入让子 agent 的 `TaskCreate/List`（WI-1.2）绑同一 goal_id → 任务图与父目标关联。

### 4. SessionDB 落库
无新表（charter 是 prompt 注入；过滤是回收期逻辑）。

### 5. re-anchoring（复用 WI-1.3 锚定文案进 charter）

### 6. handoff goal checkpoint — 输出按目标过滤判定
- **回收过滤**：`spawn_team` 收集 `final_tasks` 后，对每个 `result` 检查：(a) 含 `[off-goal]` 标记 → 标 `goal_aligned=False`；(b)（高后果时）可选调一次轻量 LLM judge `这个 result 是否推进了 {parent_goal_text}`（复用 GoalChecker.llm_call 同源 endpoint）。返回结构加 `aligned_results / flagged_results` 两组。
- **判定方式**：默认走廉价的 `[off-goal]` 标记法（子 agent 自标，零额外 LLM）；LLM judge 仅 P0-2 §2.4「高后果目标」触发（成本护栏对齐）。

### 7. 边界 case / 防回归
- **无 goal**：`parent_goal_text=None` → charter 省略 + 不过滤 → 现有 spawn_team 行为字节不变（现有 team 测试全绿）。
- **过滤不删数据**：flagged 仍返回（只打标），由父 agent 决定是否采纳——不静默丢子 agent 产出。
- **flag**：goal_mode OFF → 调用点传 None → BC。

### 8. 测试计划
- **单测**：`_build_charter(parent_goal_text=...)` 输出含 `## Parent Goal`；None → 输出与旧版一致（BC）；回收识别 `[off-goal]` 标记归入 flagged。
- **真机 E2E**：`/goal` → 触发多 agent → 截图子 agent prompt 含 Parent Goal（或 log grep charter）→ 构造一个 off-goal 子任务 → 确认被标记/拦。
- **pass^k**：handoff 注入 k=3 稳定含目标段。

### 9. build order
1. `_TEAM_CHARTER_TEMPLATE` + `_build_charter` 加 goal 段 + BC 单测
2. `spawn_team` 签名 + 回收过滤
3. main.py 调用点接 `get_goal_text`
4. E2E 子 agent 带目标 + off-goal 拦截

---

## WI-1.5 — resume 接 goal

### 1. 目标文件
- **改**：`backend/agent/auto_resume.py:219-225` — `new_msgs` 注入 goal_text
- **改**：auto_resume 构造 / 调用点 — 注入 `session_goal_store` 句柄（或 `get_goal_text` callable）

### 2. 数据结构 / 接口
auto_resume 持有 `goal_text_getter: Callable[[str], str | None]`（或直接 store）。在 nudge 分支：
```python
new_msgs = list(original_msgs)
goal_text = self._goal_text_getter(sid) if self._goal_text_getter else None
if goal_text:
    new_msgs.append({"role": "system",
        "content": f"[goal] 恢复任务，原目标仍是：{goal_text}\n继续推进。",
        "_is_goal_anchor": True})
new_msgs.append({"role": "system", "content": f"[Supervisor Hint] {hint_text}", "_is_supervisor_hint": True})
```

### 3. 数据流 / 接线点
- WI-1.1 持久化是前提：resume 发生在新进程/新 spawn 时，`store.load_persisted` 已恢复 goal → `get_goal_text(sid)` 可读（修自查条目 5 "resume 不带 goal" + "iterations 归零"）。
- iterations_used 也从落库恢复 → resume 后不从 0 重数。

### 4. SessionDB 落库
复用 WI-1.1，无新增。

### 5/6. re-anchoring / handoff
注入文案与 WI-1.3 锚定一致（统一 `[goal]` 前缀，便于 log grep）。

### 7. 边界 case / 防回归
- **flag / 无 goal**：getter 返 None → 只注 supervisor hint（现行为，BC）。
- **goal 已 done/abandoned**：load_persisted 不召回 → getter None → 不注入（不复活已完成目标）。

### 8. 测试计划
- **单测**：mock getter 返 goal_text → `new_msgs` 含 `[goal]`；返 None → 只含 supervisor hint（BC）。
- **真机 E2E**：长任务 → 触发 max_iterations/circuit 自愈 resume → 截图/日志确认 resume 后 agent 续推原目标（非从零）→ log grep `_is_goal_anchor`。
- **pass^k**：resume k=3 都带 goal。

### 9. build order
1. auto_resume 加 getter + 注入分支 + 单测
2. 调用点接 store
3. E2E resume 续目标

---

## P0-1 整体 build order（连锁多米诺）

```
WI-1.1 (durable goal store) ──┬─→ WI-1.2 (task graph，绑 goal_id)
  [先行，必须最先]            ├─→ WI-1.3 (re-anchor，读 get_goal_text)
                             ├─→ WI-1.4 (handoff，读 get_goal_text)
                             └─→ WI-1.5 (resume，读 get_goal_text + iterations 恢复)
```
- **1.1 必须先单独落地并通过 restart E2E**（其余全依赖 `get_goal_text` + 持久化）。
- 1.2 与 1.3/1.4/1.5 可在 1.1 契约冻结后**并行**（1.3/1.4/1.5 只读 `get_goal_text`；1.2 写新表，互不冲突）。
- 1.3 的"current-subgoal"依赖 1.2 的 TaskNode，但可**降级**：1.2 未完成时 1.3 只注入 goal_text（无子目标段），不阻塞。

## 对外契约（给 P0-2 / P0-3 / P1-4 用的 goal 接口）

P0-1 对外暴露**一个稳定只读接口**，下游不碰 store 内部：

```python
# SessionGoalStore（WI-1.1 交付，sync，None-safe）
store.get_goal_text(session_id: str) -> str | None
    # 返回当前活跃目标文本；无活跃目标 / flag-OFF / store=None → None

store.get(session_id: str) -> SessionGoal | None
    # 完整对象：含 goal_id / status / progress / iterations_used / subgoals

# 通过 service_context 取（已有 register("session_goal_store")）：
store = service_context.get("session_goal_store")  # None when goal_mode OFF
```

**下游用法**：
- **P0-2 §2.3**（verify 接 goal）：`VerifyGate.check(..., goal_text=store.get_goal_text(sid))` —— verify 时"重述原目标 vs 产物"对照。
- **P0-2 §2.4**（外部 evaluator）：高后果目标用 `get().status / goal_id` 关联交叉验证。
- **P0-3 §3.1**（goal/decision 记忆沉淀）：读 `status='done'/'abandoned'` 历史目标灌进 facts goal category（与 durable store 协同：store 管当前活跃、facts 管历史沉淀）。

**字节级契约约定**：
- `goal_mode` 出厂默认 `False`（不变）；新表 runtime 惰性创建，flag-OFF 用户 DB 字节不变。
- 所有新 store 方法、spawn_team/compress/compact/auto_resume 新参数**全有默认值**（None / 旧行为），下游不传 = 旧行为，保证逐 WI 渐进点亮。
