# P0-1 目标持久化与抗漂移 — 现状自查

> 生成时间：2026-06-04
> 检查范围：`backend/` (`G:\projects\deskpet\backend\`)，以代码为准

---

## 结论速览

| 机制 | 状态 | 一句话 | 关键证据 file:line |
|---|---|---|---|
| 1. `/goal` 长期目标管理 | 🟡 部分 | `/goal` 已接通 AgentLoop，但目标**纯内存态**，进程重启即消失，无子目标/进度字段 | `backend/deskpet/agent/goal_store.py:6-18`（显式注释"TODO: SessionDB persistence留 v2"） |
| 2. 任务图 / TODO 结构 | 🟡 部分 | 两套 todo：代码模式有 SessionDB 持久化的扁平 todo_write；通用 todo_tools 也持久化到 JSON；但两者均**无依赖关系/子任务/跨 agent 共享状态** | `backend/deskpet/tools/code_tools/todo_write_tool.py:7-13`；`backend/deskpet/tools/todo_tools.py:9-22` |
| 3. re-anchoring / 抗漂移 | ❌ 缺口 | context 压缩/历史紧缩时**均不回读目标**；goal_text 仅在 end_turn 时 checker 判定，中间轮次无 re-anchor 注入 | `backend/deskpet/agent/context_compressor.py`（全文无 goal 字样）；`backend/agent/history_compactor.py`（全文无 goal 字样） |
| 4. 多 agent handoff goal checkpoint | ❌ 缺口 | spawn_team 给子 agent 的 Team Charter 仅含**任务描述列表**，无父级 `/goal` 声明；子 agent 输出回收后无目标过滤 | `backend/deskpet/agent/team/spawn_team.py:66-92`（charter 模板无 goal 字段） |
| 5. 中断/恢复（任务级 checkpoint） | 🟡 部分 | AutoResume + supervisor 诊断已能从失败原因重新 spawn；但 `/goal` 目标本身**不随 resume 传递**，spawn 时只带 supervisor hint，不带 goal_text | `backend/agent/auto_resume.py:220-225`（new_msgs 仅 append supervisor hint，无 goal_text 注入）；`backend/deskpet/agent/goal_store.py:9-18`（内存态，restart-clean） |

---

## 逐条详述

### 条目 1：`/goal` 长期目标管理

**现状代码**

`SessionGoalStore`（`backend/deskpet/agent/goal_store.py:60-127`）是纯 Python `dict`，存在进程内存里，键为 `session_id`，值为 `SessionGoal` dataclass。`SessionGoal` 字段：`session_id`, `text`, `set_at`, `max_iterations`, `iterations_used`, `done`。

数据流：
- 用户输 `/goal <text>` → WS slash_command → `dispatch_slash_command` (`backend/deskpet/commands/__init__.py:63`) → `store.set(session_id, args)`
- AgentLoop end_turn 时（`backend/agent/agent_loop.py:1059-1122`）读 `store.get(session_id)` → 调 `GoalChecker.check(goal_text, working_messages)` → `done=False` → 注入 system nudge `[goal] 未达成 (N/M): <hint>\n继续工作...` → `continue` 循环
- `done=True` → `store.mark_done(session_id)`

**缺什么**

1. **无持久化**：代码顶注释 `# Persistence is deliberately NOT implemented in v1 — goals are session-lifetime only (TODO: SessionDB persistence留 v2)` (`goal_store.py:10-11`)
2. **无子目标 / 里程碑结构**：`SessionGoal` 只有一个 `text` 字符串，无 `subgoals`, `progress`, `criteria` 等字段
3. **无目标文档**（durable goal document）：没有可跨对话读写的"目标 + 子目标 + 进度"文件，goal 只存在当前 WS 连接的内存

---

### 条目 2：任务图 / TODO 结构

**现状代码**

存在**两套**不同语义的 todo：

**A. 代码模式 `todo_write` 工具**（`backend/deskpet/tools/code_tools/todo_write_tool.py`）
- 每次调用**整体覆盖**当前 code_session 的任务列表（幂等语义，类 Claude Code）
- 持久化到 SessionDB `code_todos` 表（migration v11），并广播到 control WS 供 TodoListPanel 实时显示
- 字段：`content`, `activeForm`, `status`（pending / in_progress / completed）
- 只作用于当前 code session，无跨 session 引用

**B. 通用 `todo_tools`**（`backend/deskpet/tools/todo_tools.py`）
- 写入 `todo.json`（platformdirs user_data_dir）
- 字段：`id`, `title`, `due_date`, `priority`, `status`（open / done）
- 原子替换写（tmp + os.replace），有进程级 threading.Lock

**缺什么**

两套都是**扁平列表**，均无：
- 任务依赖关系（DAG / 先决条件）
- 跨子 agent 共享（team 模型用的是 `TeamStore` 的独立 tasks 表，不读 todo.json / code_todos）
- 子任务 / 分层结构
- 与 `/goal` 的关联（GoalChecker 只看 working_messages，不读 todo 状态）

`TeamStore`（`backend/deskpet/agent/team/team_store.py`）是第三套任务结构，有 SQLite 持久化和原子 claim，但它是团队级的，每次 `spawn_team` 全新建库，完成后即销毁（caller 清理），不跨 session 保留。

---

### 条目 3：re-anchoring / 抗漂移

**现状代码**

有两处 context 缩减逻辑：

**A. `ContextCompressor`**（`backend/deskpet/agent/context_compressor.py`）
- 触发条件：prompt_tokens ≥ context_window × threshold_percent（默认 75%）
- 策略：保留 system 消息 + first_n（3）+ last_n（6），中间部分请求 haiku summarize
- 摘要注入格式：`[压缩摘要 / compressed summary]\n<text>` 放到 first_chunk 之后
- **无 goal 读取**：压缩前后均不读 `session_goal_store`，goal_text 不额外注入

**B. `history_compactor`**（`backend/agent/history_compactor.py`）
- 触发条件：`len(messages) > 20` 或 total_chars > 60k
- 策略：保留 system 栈 + last 6 条，中间部分摘要替换
- 摘要标记：`[Summary of N earlier turns — compacted to save context budget]`
- **无 goal 读取**：`_format_for_summarize` 和 `inject_summary` 均与 goal 无关

**GoalChecker 的局限**

GoalChecker 仅在**每个 turn 结束**（AgentLoop `end_turn` 路径）被调用一次，用最近 5 轮 assistant 消息判定。它是**被动判定**而非**主动锚定**——没有机制在 context 被压缩后把 goal_text 重新注入到 system 层。

**缺什么**

- 压缩前"重读 goal 校验"钩子：完全缺失
- goal drift 信号：无。GoalChecker 返回 `done=False` 会 nudge，但这是"未完成"不是"偏离"——两者语义不同
- 抗漂移注入：goal_text 从未在 system prompt 里出现（组装器 `backend/deskpet/agent/assembler/assembler.py` 不读 goal_store），只在 end_turn nudge 时以 system message 形式临时注入

---

### 条目 4：多 agent handoff goal checkpoint

**现状代码**

`spawn_team`（`backend/deskpet/agent/team/spawn_team.py:148-252`）构建 Team Charter (`_TEAM_CHARTER_TEMPLATE` 第 66-92 行)，内容：
- `team_id`, `teammate_id`
- Workflow 说明（claim → work → update → repeat）
- 初始任务池摘要（`initial_pool_summary` = 任务描述列表）

父级 session 的 `/goal` 目标**完全不包含**在 charter 中。`spawn_team` 签名接收 `parent_session_id` 用于子 session id 前缀，不接收 `parent_goal_text` 或 `session_goal_store`。

子 agent（`_TeamSubsetRegistry`）工具集中没有 `goal_*` 相关工具，子 agent 看不到父目标。

**回收机制**

`spawn_team` 收集 `final_tasks`（所有任务的 `to_dict()`）作为结果返回，没有对父目标做过滤或比对（即无"按目标验收"逻辑）。

**缺什么**

- 父 goal 传递：子 agent charter 无父 `/goal` 声明
- 防继承漂移：无机制校验子 agent 输出是否与父目标对齐
- Goal-filtered 结果回收：spawn_team 只返回任务完成状态，不做目标语义比对

---

### 条目 5：中断/恢复（任务级 checkpoint）

**现状代码**

有 AutoResume + Supervisor 体系（`backend/agent/auto_resume.py`, `backend/agent/supervisor.py`）：
- 触发：AgentLoop 返回 `max_iterations / permanent_tool_error / circuit_open / hallucination`
- 动作：supervisor 诊断 → 如 `action=nudge` → 构建 `new_msgs = original_msgs + [{system, "[Supervisor Hint] {hint_text}"}]` → `_dispatcher(sid, new_msgs)` 重新 spawn

`auto_resume.py:221-224`：
```python
new_msgs = list(original_msgs) + [{
    "role": "system",
    "content": f"[Supervisor Hint] {hint_text}",
    "_is_supervisor_hint": True,
}]
```

注意 `original_msgs` 是原始对话消息列表，**不含** goal_text 注入（goal_text 只存在 SessionGoalStore 内存，AutoResume 不读）。

SessionActivityStore 用于记录 `auto_resume_attempts` 次数（内存态，restart-clean），Team `task_done_at` 时间戳存 SQLite（但只存任务本身的完成时间，不是"最后 verified state" checkpoint）。

**缺什么**

- 恢复时 goal_text 不随 resume 传递：新 spawn 的 agent 不知道当前 `/goal`
- 无任务级 checkpoint：代码模式没有"从最后 verified state 续跑"的 checkpoint 文件；resume 靠的是 supervisor hint + 原始消息，不是中间态快照
- `/goal` 的 `iterations_used` 在进程重启后归零，resume 后从 0 重新计数

---

## 给 plan 的建议

### 真缺口（需新建）

1. **goal 持久化**（条目 1 核心缺口）
   - 最小路径：在 `SessionGoalStore` 加 `bind_persistence(sdb)` 接口（对齐 `CodeProjectRegistry.bind_persistence` 已有模式），用 SessionDB 新增 `session_goals` 表存储 `(session_id, text, set_at, done, iterations_used)`
   - 这样 resume 时可 `load_persisted()` 还原 goal

2. **context 压缩时的 goal re-anchor**（条目 3 核心缺口）
   - 最小实现：在 `ContextCompressor.compress()` 和 `history_compactor.compact_messages()` 中加 optional `goal_text` 参数；若非空，在 system 层追加 `[当前目标] {goal_text}` 消息
   - 或在 assembler 的 system prompt 组装期直接注入（更早、更稳）

3. **spawn_team 传递父 goal**（条目 4 核心缺口）
   - `spawn_team` 增加 `parent_goal_text: str | None = None` 参数
   - 若非空，`_TEAM_CHARTER_TEMPLATE` 添加 `## Parent Goal\n{parent_goal_text}` 段
   - 结果回收时加 goal-alignment 校验（可选，可后做）

4. **resume 时恢复 goal**（条目 5 衍生缺口）
   - AutoResume `handle_failure` 读 `session_goal_store.get(sid)`；若有 goal → 在 `new_msgs` 里额外注入 `[goal] <text>` system 消息

### 已有只需增强

- **GoalChecker 判定机制**（条目 1）：机制健全，只缺持久化和 sub-goal 结构
- **todo 持久化**（条目 2）：code_todos SessionDB 持久化已有，只需与 goal 关联
- **AutoResume 基础架构**（条目 5）：supervisor + resume spawn 路径已完整，只需在 payload 里多带 goal_text

---

