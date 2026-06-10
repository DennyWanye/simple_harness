# 手工测试用例 — goal-completion 升级 FP-2（抗漂移闭环）

> **被测功能**: goal-completion 升级线第二个大功能点 **FP-2 抗漂移闭环**。
> 覆盖 **WI-1.2** `goal_tasks` 任务图、**WI-1.3** re-anchoring、**WI-1.4** `spawn_team` handoff goal checkpoint、**WI-1.5** auto-resume 接 goal。
> **真实状态校准**: `goal_tasks` 后端表/API 已实现；teammate 工具在 `spawn_team()` 收到 `task_graph_store + parent_goal_id` 时追加 `goal_task_list`/`goal_task_update`。但当前未在 `main.py` 构造/注册 `TaskGraphStore`，也未发现 Code UI 渲染 `goal_tasks` 的 TodoPanel；这些 UI/产品链路必须标 BLOCKED，不能写成已可真机通过。
> **代码依据**: `backend/agent/agent_loop.py`、`backend/agent/auto_resume.py`、`backend/agent/history_compactor.py`、`backend/deskpet/agent/context_compressor.py`、`backend/deskpet/agent/task_graph.py`、`backend/deskpet/memory/session_db.py`、`backend/deskpet/agent/team/spawn_team.py`、`backend/deskpet/agent/team/teammate_tools.py`、`backend/deskpet/tools/task_graph_tools.py`。
> **关联**: FP-1 见 [FP-1-目标持久化-manual-test.md](./FP-1-目标持久化-manual-test.md)；FP-3/4/5 见 [../goal-completion-manual-test.md](../goal-completion-manual-test.md)。
> **最后更新**: 2026-06-09（Round 1 codex 校准 → Round 2 复核：全部 BLOCKED 判定经 grep 二次确认无误杀、TC-2.7 确认已接线(main.py:2522/2537)、修正 2 个编造测试名(`test_dag_claim_skips_blocked_task`/`test_concurrent_claim_no_double_claim`)、补 BLOCKED 项解锁前置 → **Round 3 干跑核验**：全部 log 锚点逐个 grep 证实(main.py:1304/1352/1271/1850, agent_loop.py:751/826/1408, auto_resume.py:282/315/102)、补「compaction_enabled 默认 False 须显式开启」关键前置。**3 轮迭代完成**）

---

## 0. 测试前置准备

### 0.1 FP-2 专属前置

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| P-1 | 按 FP-1 §0.1 启动（源码 backend、独立 `DESKPET_USER_DATA_DIR`、登录态、Code 面板） | 桌宠 + Code 面板就绪 |
| P-2 | 确认 feature 接线。⚠️**Round 3 关键**：`compaction_enabled` 出厂默认 **False**（`backend/config.py:419`），跑 TC-2.1/2.10 前必须在注入的 config.toml 写 `[features] compaction_enabled = true`，否则压缩永不触发 | boot log 出现 `companion_code_v1_goal_mode_ready`；开启 compaction 后还应出现 `wi4_0_compaction_enabled context_window=32000 threshold=0.75` |
| P-3 | 确认 CDP harness | `backend\.venv\Scripts\python.exe testcase\_cdp.py pages` 能看到 `code-panel` |
| P-4 | 发送消息统一用真实 SendInput | `backend\.venv\Scripts\python.exe testcase\_send.py code0 "<文本>"` 输出坐标并发出消息 |
| P-5 | 记下 DB | `"${env:DESKPET_USER_DATA_DIR}\data\state.db"` |

### 0.2 判定约定 + 诚实 E2E 分级

同 FP-1 §0.2/§0.3。执行时每个 TC 标 `PASS / FAIL / RETRY-N / SKIP（理由） / BLOCKED（证据）`。截图存 `plans/manual-results-<date>-FP-2/screenshots/`。

### 0.3 与 FP-5 TC-5.2 的去重边界

FP-5 TC-5.2 侧重 **compaction 4.0 接电 + skill remount/保留项**。本文件 TC-2.1 只验 **WI-1.3 goal anchor 注入后，压缩/长对话仍不忘原目标**；不重复验证 skill remount，也不把 FP-5 的 `skill_remounted` 当本 TC 必要条件。

---

# FP-2 — 抗漂移闭环

## TC-2.1 — 长对话顶过压缩阈值 → 追问原目标仍答对（✅真模拟人 + log，pass^k k=3）★★

**目的**: 验证 WI-1.3 re-anchoring：`AgentLoop` 在决策点注入 `[目标锚定]`，compaction 路径把 goal_text 传给 compressor，压缩后仍不漂移。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | `backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal 帮我把这份会议纪要整理成结构化要点"` | 前端返回已设置目标 |
| 2 | 连续发送长消息/多工具任务直到超过压缩阈值；期间用 `backend\.venv\Scripts\python.exe testcase\_cdp.py text code-panel` 观察对话 | 对话持续，未崩溃 |
| 3 | grep backend log | compaction 开启时应见 `wi4_0_compaction_enabled context_window=32000 threshold=0.75`；触发时见 `p1_4_compaction_fired sid=<sid> tid=<tid> iter=<n> reduction=<r>`；决策点锚定见 `wi13_goal_anchor_injected sid=<sid> tid=<tid> iter=<n>` |
| 4 | 发送：`backend\.venv\Scripts\python.exe testcase\_send.py code0 "我最初让你做什么来着？"` | LLM 回答仍指向原目标“把会议纪要整理成结构化要点”，未被中间噪声带偏 |
| 5 | 截图：`backend\.venv\Scripts\python.exe testcase\_cdp.py shot code-panel plans\manual-results-2026-06-09-FP-2\screenshots\tc-2.1-after-compact-still-on-goal.png` | 截图可见追问与正确回答 |
| 6 | pass^k k=3：换不同目标和噪声重复 | 3/3 正确回答原目标；至少一次 log 有 `p1_4_compaction_fired`，每轮有 `wi13_goal_anchor_injected` |

**预期总判**: 行为不漂移 + log 有真实锚点。若无法触发 compaction，只能判 `RETRY/SKIP`，不能把普通长对话当压缩证据。
**判定**: ___

---

## TC-2.2 — 多步目标 → `goal_tasks` 创建 + TodoPanel 进度可见（✅/🟡混合，WI-1.2）★

**目的**: 验证任务图。**当前真实状态**: 后端 `goal_tasks` 表/API 已实现；Code UI 当前渲染的是 `code_todos` / `todo_write`，未发现 `goal_tasks` TodoPanel；main.py 也未构造 `TaskGraphStore` 自动拆目标。因此 UI 可见链路为 BLOCKED/待实现。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 真机发送：`backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal 准备一次产品评审：1)收集数据 2)写提纲 3)生成PPT"` | goal_set 成功 |
| 2 | 真机发送：`backend\.venv\Scripts\python.exe testcase\_send.py code0 "开始执行，并把步骤拆成任务图"` | 当前产品若只调用 `todo_write`，前端只会更新 `code_todos`，不是 `goal_tasks` |
| 3 | 查 DB：`sqlite3 "${env:DESKPET_USER_DATA_DIR}\data\state.db" "SELECT name FROM sqlite_master WHERE type='table' AND name='goal_tasks';"` | 当前 main 链路未创建 `TaskGraphStore` 时，可能无 `goal_tasks` 表；这不是测试失败，是产品链路未接 |
| 4 | 若未来产品接通，执行：`sqlite3 "${env:DESKPET_USER_DATA_DIR}\data\state.db" "SELECT task_id,goal_id,session_id,title,status,depends_on,claimed_by,result FROM goal_tasks WHERE goal_id='<goal_id>' ORDER BY created_at ASC;"` | 应返回多行任务，状态可从 pending/in_progress/done 推进 |
| 5 | UI 验证 | **BLOCKED/待实现**：未发现 Code UI 渲染 `goal_tasks` 的 TodoPanel；现有 `SessionSidebar`/`SessionGridView` 展示的是 `code_todos` |

**预期总判**: 本轮只能把“后端 API 可测、产品/UI 链路 BLOCKED”作为诚实结论。
**解锁前置（需接什么线才能真机验，Round 2 复核确认）**: ① `main.py` lifespan 构造 `TaskGraphStore(session_db)` 并 register 进 service_context（grep `TaskGraphStore` 当前 0 命中）；② 目标拆解路径调用 task_graph 建任务（替代/并行 `todo_write`）；③ 前端新增 goal_tasks 渲染（grep tauri-app/src `goal_tasks` 当前 0 文件）。
**判定**: BLOCKED/待实现（证据：`main.py` 无 `TaskGraphStore` 构造/注册；前端仅 `code_todos`）

---

## TC-2.3 — 任务依赖 DAG ready 调度：前置未完不开后续（🟡后端核对，WI-1.2 边界）★

**目的**: 验证 `SessionDB.claim_ready_goal_task()` 只 claim 依赖已 done 的 pending 任务。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 后端测试：`cd backend ; $env:PYTHONPATH="."; .\.venv\Scripts\pytest.exe tests\test_goal_tasks_db.py::test_dag_claim_skips_blocked_task tests\test_task_graph.py -q` | 测试通过（Round 2 校准：真实测试名为 `test_dag_claim_skips_blocked_task`） |
| 2 | 手工 DB 构造或借测试夹具创建 A/B，B 的 `depends_on` 包含 A | `SELECT task_id,title,status,depends_on FROM goal_tasks WHERE goal_id='<goal_id>' ORDER BY created_at;` 可见 B 依赖 A |
| 3 | A 未 done 时 claim | `claim_ready_goal_task()` 只返回 A，不返回 B |
| 4 | A 更新 done 后再次 claim | 返回 B，`claimed_by` 为第二个 agent |

**预期总判**: 依赖未满足的任务不被提前执行。真机产品链路仍依赖 TC-2.2 接通。
**判定**: ___

---

## TC-2.4 — 跨子 agent 共享任务图：子 agent 读写同一 `goal_tasks`（🟡后端，WI-1.2 锁定项）★

**目的**: 验证 teammate 工具在有 `task_graph_store + goal_id` 时包含 `goal_task_list` / `goal_task_update`，可读写同一个 `goal_tasks` 图。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 后端测试：`cd backend ; $env:PYTHONPATH="."; .\.venv\Scripts\pytest.exe tests\test_teammate_tools_taskgraph.py -q` | 测试通过；工具集从 5 个增加到 7 个，包含 `goal_task_list` / `goal_task_update` |
| 2 | DB 查询：`sqlite3 "${env:DESKPET_USER_DATA_DIR}\data\state.db" "SELECT task_id,title,status,claimed_by,result FROM goal_tasks WHERE goal_id='<goal_id>' ORDER BY created_at;"` | 子 agent 更新后同一行状态/结果变化 |
| 3 | 真机 spawn_team 产品链路 | **BLOCKED/待实现**：`spawn_team()` 支持 `parent_goal_id/task_graph_store`，但当前未发现 main/tool 注册层传入 `TaskGraphStore` 与 active `goal_id`（Round 2 复核：grep main.py `spawn_team|task_graph_tools` 0 命中） |

**预期总判**: 后端能力已实现；产品真机链路待接。
**解锁前置**: 在 spawn_team 工具注册处把 service_context 的 `TaskGraphStore` + `session_goal_store.get(sid).goal_id` 传入 `spawn_team(task_graph_store=..., parent_goal_id=...)`（依赖 TC-2.2 解锁前置①）。
**判定**: ___

---

## TC-2.5 — handoff goal checkpoint：`spawn_team` Charter 注入父 goal_text（🟡后端/log，WI-1.4）★

**目的**: 验证 `spawn_team._build_charter()` 在提供 `parent_goal_text` 时生成 `## Parent Goal (do not drift)` 段，防子 agent 漂移。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 后端测试：`cd backend ; $env:PYTHONPATH="."; .\.venv\Scripts\pytest.exe tests\test_spawn_team_goal.py -q` | 测试通过；Charter 含父目标 |
| 2 | 代码/日志锚点 | Charter 文案为 `## Parent Goal (do not drift)`、`本团队服务于用户的总目标：<goal>`、`[off-goal]` 标记要求 |
| 3 | 真机触发 spawn_team | 若产品工具层传入 `parent_goal_text`，teammate prompt 应含父目标；当前生产 log 不打印完整 Charter，不能要求 grep “Team Charter 构建” |

**预期总判**: 后端函数已实现；真机需通过 spawn_team 调用参数或新增审计日志证明。
**判定**: ___

---

## TC-2.6 — 子输出按目标过滤：off-goal 输出被标记/分流（🟡后端，WI-1.4 边界）★

**目的**: 验证当前窄实现：不是 LLM judge 硬拦截，而是 teammate 被要求在 result 里写 `[off-goal]`，`_classify_by_goal()` 将其分入 `flagged`，不丢数据。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 后端测试：`cd backend ; $env:PYTHONPATH="."; .\.venv\Scripts\pytest.exe tests\test_spawn_team_goal.py -q` | 测试覆盖 `_classify_by_goal` 时应通过 |
| 2 | 构造 teammate result 包含 `[off-goal]` | 返回结构中该任务出现在 `goal_classification.flagged` |
| 3 | 检查结果保留 | `results` 仍包含完整任务；没有静默丢弃 |
| 4 | LLM judge 拦截 | **BLOCKED/待实现**：代码未实现 LLM judge；当前仅 marker 分类，无生产 log |

**预期总判**: marker 分流可测；“LLM judge 拦/标记”不属于当前实现。
**判定**: ___

---

## TC-2.7 — 中断 → auto-resume 续原目标（🟡log，WI-1.5 窄版）★

**目的**: 验证 `AutoResumeOrchestrator` 通过 main.py 注入的 `goal_text_getter` 在恢复消息中追加目标锚：`[goal] 恢复任务，原目标仍是：...`，并设置 `_is_goal_anchor=True`。**Round 2 复核确认已接线**：`main.py:2522` 定义 `_goal_text_getter_for_resume`、`:2537` 传入 orchestrator（非 BLOCKED）。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 设目标：`backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal 帮我生成一份调研PPT并保存"` | goal_set 成功 |
| 2 | 触发可 auto-resume 的错误路径（如 `max_iterations` / `verify_exhausted` / `circuit_open`，按当前配置选择） | chat handler 将 reason 路由给 auto_resume；触发集包含 `max_iterations`、`permanent_tool_error`、`circuit_open`、`hallucination`、`verify_exhausted`、`evaluator_revise` |
| 3 | grep log | 出现 `auto_resume_spawned sid=<sid> attempt=<n> hint=...` 或 `auto_resume_engaged sid=<sid> reason=<reason> attempt=<n>`；若达到上限则 `auto_resume_exhausted sid=<sid> attempts=<n> ...` |
| 4 | 验证注入 | 生产 log 不打印完整 system message；用后端单测 `cd backend ; $env:PYTHONPATH="."; .\.venv\Scripts\pytest.exe tests\test_auto_resume_goal.py -q` 证明 `_is_goal_anchor=True` 且内容含 `[goal] 恢复任务，原目标仍是：<goal>` |
| 5 | 真机行为观察 | 恢复后继续推进原目标；不是无目标重跑 |

**预期总判**: 窄版 resume 接 goal 已实现；任务级 checkpoint 不在本轮。
**判定**: ___

---

## TC-2.8 — 并发 claim 不双占（🟡后端 pass^k，非真机）★

**目的**: 验证 `SessionDB._write_lock` + `WHERE status='pending'` 防同一 ready 任务被多个 agent 双占。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 连跑 5 次：`cd backend ; $env:PYTHONPATH="."; for ($i=1; $i -le 5; $i++) { .\.venv\Scripts\pytest.exe tests\test_goal_tasks_db.py::test_concurrent_claim_no_double_claim -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }` | 每次退出码 0（Round 2 校准：真实测试名为 `test_concurrent_claim_no_double_claim`） |
| 2 | 失败时查 DB：`SELECT task_id,status,claimed_by FROM goal_tasks WHERE goal_id='<goal_id>';` | 同一个 task 最多一个 `claimed_by` |

**预期总判**: 并发 claim 单行只一个成功。
**判定**: ___

---

## TC-2.9 — 漂移信号打点：`GD_actions` / `GD_inaction`（🟡log/metrics，边界）★

**目的**: 验证 WI-1.3 计划中的漂移观测信号。**当前真实状态**: repo 内未发现 `GD_actions` / `GD_inaction` 字符串或指标实现。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | `rg -n "GD_actions" backend ; rg -n "GD_inaction" backend` | 当前无结果 |
| 2 | 真机执行长目标并 grep metrics/log | 当前不应期待这两个事件 |

**预期总判**: BLOCKED/待实现。不要把 `wi13_goal_anchor_injected` 或 `p1_4_compaction_fired` 误当 GD 信号。
**判定**: BLOCKED/待实现（证据：代码无 `GD_actions` / `GD_inaction`）

---

## TC-2.10 — compaction 触发前置条件与非触发分支（🟡log，新增边界）★

**目的**: 补齐 FP-2 抗漂移的前置：没有开启 compaction 或未达到阈值时，TC-2.1 不得声称“压缩后不漂移”。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 使用 `[features] compaction_enabled=false` 启动 | boot log 不出现 `wi4_0_compaction_enabled context_window=32000 threshold=0.75` |
| 2 | 跑长对话并 grep | 不应出现 `p1_4_compaction_fired`；若有则配置/日志判定异常 |
| 3 | 使用 `[features] compaction_enabled=true` 但对话短于阈值 | boot log 出现 `wi4_0_compaction_enabled...`，但不出现 `p1_4_compaction_fired` |
| 4 | 超过阈值 | 出现 `p1_4_compaction_fired sid=... reduction=...` 后才允许执行 TC-2.1 的“压缩后追问”判定 |

**预期总判**: 明确区分“未触发压缩”与“压缩后不漂移”。
**判定**: ___

---

## 测试结果汇总表（执行时填写）

| TC | 范围 | E2E 等级 | 判定 | 截图/log/DB 证据 | 备注 |
|---|---|---|---|---|---|
| TC-2.1 | 压缩后不漂移 | ✅真模拟人+log pass^k | | | 与 FP-5 TC-5.2 去重 |
| TC-2.2 | 任务图 + TodoPanel | ✅/🟡混合 | BLOCKED/待实现 | | 后端表有；UI/main 链路未接 |
| TC-2.3 | DAG ready 调度 | 🟡后端 | | | |
| TC-2.4 | 跨 agent 共享 `goal_tasks` | 🟡后端 | | | 产品链路待接 |
| TC-2.5 | handoff 带 goal | 🟡后端/log | | | 生产 log 不打印完整 Charter |
| TC-2.6 | 子输出 off-goal 分类 | 🟡后端 | | | 当前 marker 分类，非 LLM judge |
| TC-2.7 | resume 续原目标 | 🟡log+单测 | | | 窄版已接 |
| TC-2.8 | 并发不双占 | 🟡后端 pass^k | | | 非真机 |
| TC-2.9 | GD 漂移信号 | 🟡log/metrics | BLOCKED/待实现 | | 代码无事件 |
| TC-2.10 | compaction 前置 | 🟡log | | | 新增边界 |

---

## 附录：FP-2 关键 log / DB / 测试速查

| 类别 | 锚点 |
|---|---|
| goal_mode | `companion_code_v1_goal_mode_ready` |
| compaction 初始化 | `wi4_0_compaction_enabled context_window=32000 threshold=0.75` |
| compaction 触发 | `p1_4_compaction_fired sid=... tid=... iter=... reduction=...` |
| 决策点锚定 | `wi13_goal_anchor_injected sid=... tid=... iter=...` |
| 注入文案 | `[目标锚定] 当前目标：<goal>`；auto-resume 文案为 `[goal] 恢复任务，原目标仍是：<goal>` |
| goal_tasks 表 SQL | `SELECT task_id,goal_id,session_id,title,status,depends_on,claimed_by,result,created_at,updated_at FROM goal_tasks WHERE goal_id='<goal_id>' ORDER BY created_at ASC;` |
| ready claim SQL 核对 | `SELECT task_id,title,status,depends_on,claimed_by FROM goal_tasks WHERE goal_id='<goal_id>' ORDER BY created_at ASC;` |
| teammate 任务图工具 | `goal_task_list`、`goal_task_update`（仅当 `build_teammate_tools(task_graph_store=..., goal_id=...)` 两者都有值） |
| spawn_team parent goal 文案 | `## Parent Goal (do not drift)`、`本团队服务于用户的总目标：...`、`[off-goal]` |
| auto_resume | `auto_resume_spawned`、`auto_resume_engaged`、`auto_resume_exhausted`；触发集含 `verify_exhausted` / `evaluator_revise` |
| 后端测试 | `tests\test_goal_tasks_db.py`、`tests\test_task_graph.py`、`tests\test_teammate_tools_taskgraph.py`、`tests\test_spawn_team_goal.py`、`tests\test_auto_resume_goal.py` |
| 明确未实现 | `GD_actions` / `GD_inaction` 指标未在代码中出现；Code UI 未发现 `goal_tasks` TodoPanel；`main.py` 未发现 `TaskGraphStore` 构造/注册 |
