# 手工测试用例 — goal-completion 升级 FP-1（目标持久化地基）

> **被测功能**: goal-completion 升级线第一个大功能点 **FP-1 目标持久化地基**。
> 覆盖 **WI-1.1** Durable Goal Store、**R-T1** lifespan 接电、**T1** `iterations_used` 落库、**WI-1.6** `ToolPathRecorder`、**R-T5** flag-OFF 字节基线、**R-T7** 多 worktree 独立 `USER_DATA_DIR`。
> **核心验收点**: `/goal` 设置的目标在 `deskpet.exe` 重启后仍可查；`iterations_used` 重启不归零；`goal_mode=false` 时不创建 `session_goals` / `goal_tasks`；工具路径录制按真实接线核对。
> **代码依据**: `backend/main.py`、`backend/deskpet/agent/goal_store.py`、`backend/deskpet/memory/session_db.py`、`backend/deskpet/memory/memory_v2_schema.py`、`backend/agent/agent_loop.py`、`backend/deskpet/agent/tool_path.py`、`scripts/e2e_flag_off_baseline.py`。
> **测试类型**: 以 windows-mcp 真机模拟人工为主；内部状态用 🟡log/DB 核对；字节基线用 🔵脚本核对。
> **关联**: FP-2 见 [FP-2-抗漂移-manual-test.md](./FP-2-抗漂移-manual-test.md)；FP-3/4/5 见 [../goal-completion-manual-test.md](../goal-completion-manual-test.md)。
> **最后更新**: 2026-06-09（Round 1 codex 校准 → Round 2 复核：前端渲染字符串经 grep ws.ts:205-211 确认一致、修正 1 个编造测试名(`test_tool_path_recorder_fed_during_run`)、§0.2 补 sid/goal_id 获取步骤 → **Round 3 干跑核验**：全部 log 锚点逐个 grep 证实(goal_store_bound_persistence=main.py:1271, goal_store_load_persisted=main.py:1850, goal_checker_nudge_injected=agent_loop.py:1408, fp5_codify_wiring_ready=main.py:1352)。**3 轮迭代完成**）

---

## 0. 测试前置准备

> 完整启动纪律 / windows-mcp 工具陷阱 / 诚实 E2E 分级，见 [../goal-completion-manual-test.md §0](../goal-completion-manual-test.md)。以下为 FP-1 必需前置。

### 0.1 启动（CLAUDE.md 踩坑 #7/#8/#9 — 不可违反）

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| P-0 | `git rev-parse --short HEAD` + `git branch --show-current` | 位于待测 goal-completion 实现 commit，分支为 `master` 或本轮待测分支 |
| P-1 | `taskkill /F /IM deskpet.exe`（忽略 not found）；确认无残留 backend/vite 占用端口 | 8100 / vite 端口空闲，避免 orphan crash-loop |
| P-2 | 只给 Tauri 进程注入 env，不手动起 backend/vite：<br>`$env:DESKPET_CONFIG="G:\projects\deskpet\.tmp\fp1-config.toml"`<br>`$env:DESKPET_BACKEND_DIR="G:\projects\deskpet\backend"`<br>`$env:DESKPET_PYTHON="G:\projects\deskpet\backend\.venv\Scripts\python.exe"`<br>`$env:DESKPET_USER_DATA_DIR="G:\projects\deskpet\.tmp\fp1-userdata-a"`<br>确保注入 config 的 `[features] goal_mode = true` | env 只影响本次 Tauri 进程；不修改 tracked config |
| P-3 | `cd tauri-app ; npx tauri dev` | 桌宠窗口出现 |
| P-4 | 看 tauri dev 终端 | 出现 `[backend_launch] Dev python=...backend\.venv... backend_dir=G:\projects\deskpet\backend`，不是 `Bundled exe=...` |
| P-5 | boot log grep | 出现 `companion_code_v1_goal_mode_ready`、`goal_store_bound_persistence`、`goal_store_load_persisted restored=N`；首启新目录 N=0 |
| P-6 | 登录测试账号并确认 LLM key 可用 | agent loop / goal_checker 可调用真 LLM |
| P-7 | 打开 Code 模式面板 | `/goal` 在 Code WebView2 输入框内解析；桌宠快聊 pill 不作为本文件证据 |
| P-8 | 记下路径 | DB = `"${env:DESKPET_USER_DATA_DIR}\data\state.db"`；backend log = tauri dev stderr |

### 0.2 windows-mcp 真实操作命令

| 目的 | 命令 / 操作 | 预期结果 |
|---|---|---|
| 枚举 CDP 页面 | `backend\.venv\Scripts\python.exe testcase\_cdp.py pages` | 能看到包含 `code-panel` 的 page |
| 截图 Code 面板 | `backend\.venv\Scripts\python.exe testcase\_cdp.py shot code-panel plans\manual-results-2026-06-09-FP-1\screenshots\<name>.png` | 输出 `saved ... bytes`，文件存在 |
| 发送消息（真实 SendInput） | `backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal 帮我整理本周三个会议纪要"` | 输出 `coords in(x,y) send(x,y)`；Code 面板出现发送内容 |
| 抓对话文本 | `backend\.venv\Scripts\python.exe testcase\_cdp.py text code-panel` | 输出最近聊天文本，可见 goal 响应 |
| **获取 `<当前sid>` / `<goal_id>`**（Round 2 补） | `sqlite3 "${env:DESKPET_USER_DATA_DIR}\data\state.db" "SELECT session_id,goal_id,text FROM session_goals ORDER BY updated_at DESC LIMIT 3;"` | 最新行即当前 session 的 sid/goal_id；**测试用独立 fresh userdata 时只有一个 session，后续 SQL 可直接去掉 `WHERE session_id=...` 改用 `ORDER BY updated_at DESC LIMIT 1`** |

### 0.3 判定约定 + 诚实 E2E 分级

每个 TC 末尾填 `PASS / FAIL / RETRY-N / SKIP（理由） / BLOCKED（证据）`。✅真模拟人类失败必须 retry ≥3 次不同 workaround（SetCursorPos+SendInput / 剪贴板 Ctrl+V / 先聚焦再粘）才能标 SKIP。截图存 `plans/manual-results-<date>-FP-1/screenshots/`。

---

# FP-1 — 目标持久化地基

## TC-1.1 — 设目标 → 重启 deskpet → `/goal` 查仍在（✅真模拟人，招牌链，pass^k k=3）★★

**目的**: 验证 WI-1.1 + R-T1：`/goal` set 会 await `store.persist(goal)` 落 `session_goals`，lifespan 启动调用 `load_persisted()` 恢复 active 目标。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | `backend\.venv\Scripts\python.exe testcase\_cdp.py shot code-panel plans\manual-results-2026-06-09-FP-1\screenshots\tc-1.1-0-init.png` | 截图存在，Code 面板可输入 |
| 2 | `backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal 帮我整理本周三个会议纪要"` | Code 面板返回 `已设置目标: 帮我整理本周三个会议纪要（上限 10 轮）`（前端由 `goal_set` 渲染） |
| 3 | DB 查询：`sqlite3 "${env:DESKPET_USER_DATA_DIR}\data\state.db" "SELECT goal_id,session_id,text,status,max_iterations,iterations_used FROM session_goals WHERE session_id='<当前sid>' AND status='active' ORDER BY updated_at DESC LIMIT 1;"` | 返回 1 行；`text='帮我整理本周三个会议纪要'`、`status='active'`、`max_iterations=10`、`iterations_used=0` |
| 4 | `backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal"` | 返回 `当前目标: 帮我整理本周三个会议纪要（已用 0/10 轮...）` |
| 5 | 重启：`taskkill /F /IM deskpet.exe`，等待退出后用同一 env 再跑 `cd tauri-app ; npx tauri dev` | 桌宠与 backend 重新启动 |
| 6 | boot log grep | 出现 `goal_store_load_persisted restored=1`；若同时看到 `goal_store.load_persisted restored=1` 也属于 store 内部同一路径证据 |
| 7 | 重新打开 Code 面板后发送：`backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal"` | 返回同一目标文本；不是 `当前无活动目标` |
| 8 | 截图：`backend\.venv\Scripts\python.exe testcase\_cdp.py shot code-panel plans\manual-results-2026-06-09-FP-1\screenshots\tc-1.1-2-after-restart.png` | 截图中可见目标仍在 |
| 9 | pass^k：换 3 个不同目标重复步骤 2-7 | 3/3 重启后均恢复；每轮 DB 最新 active 行文本与 `/goal` 一致 |

**预期总判**: 重启后 `/goal` 返回原目标，DB active 行存在，log 有 `goal_store_load_persisted restored=1`。
**判定**: ___

---

## TC-1.2 — 重启后 `iterations_used` 恢复（不归零）（🟡log+DB，T1）★

**目的**: 验证 `AgentLoop` 在 goal_checker 判定未完成时调用 `increment_iteration()` 后 await `persist_iteration()`，重启后从 DB 恢复计数。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 设一个明显多步目标：`backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal 帮我完成一个分三步的调研摘要"` | 返回已设置目标 |
| 2 | 发送执行请求并让它至少出现一次未完成 rebound：`backend\.venv\Scripts\python.exe testcase\_send.py code0 "开始执行；如果没完成请继续"` | backend log 出现 `goal_checker_nudge_injected sid=<sid> iter=1/10` 或更高计数 |
| 3 | DB 查询：`sqlite3 "${env:DESKPET_USER_DATA_DIR}\data\state.db" "SELECT goal_id,iterations_used,status FROM session_goals WHERE session_id='<当前sid>' AND status='active' ORDER BY updated_at DESC LIMIT 1;"` | `iterations_used=M` 且 M > 0 |
| 4 | 重启 deskpet（同 TC-1.1 步骤 5） | backend 重起 |
| 5 | 重启后 DB 查询同一 `goal_id`：`sqlite3 "${env:DESKPET_USER_DATA_DIR}\data\state.db" "SELECT iterations_used,status FROM session_goals WHERE goal_id='<goal_id>';"` | `iterations_used=M`，不是 0 |
| 6 | `/goal` 查询 | 前端文本显示 `已用 M/10 轮` |

**预期总判**: `iterations_used` 重启前后相同且大于 0。
**判定**: ___

---

## TC-1.3 — `/goal clear` → 落 abandoned（不物理删行）（🟡DB，边界）★

**目的**: 验证 clear 走 `persist_abandon()`，把同一 `goal_id` 的 `status` 改为 `abandoned`，不删除历史行。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 先设目标并记录 `goal_id`：`sqlite3 "${env:DESKPET_USER_DATA_DIR}\data\state.db" "SELECT goal_id,text,status FROM session_goals WHERE session_id='<当前sid>' AND status='active' ORDER BY updated_at DESC LIMIT 1;"` | 返回 1 行 active 目标 |
| 2 | `backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal clear"` | 前端返回 `已清除当前目标`（`goal_cleared ok=true`） |
| 3 | `backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal"` | 返回 `当前无活动目标` |
| 4 | DB 查询：`sqlite3 "${env:DESKPET_USER_DATA_DIR}\data\state.db" "SELECT goal_id,text,status,iterations_used FROM session_goals WHERE goal_id='<步骤1的goal_id>';"` | 仍返回同一行；`status='abandoned'`；行未被物理删除 |

**预期总判**: 内存目标清空，DB 历史行保留且 status=abandoned。
**判定**: ___

---

## TC-1.4 — flag-OFF：`goal_mode=false` 时不建 goal 表（🔵脚本/字节核对，R-T5）★

**目的**: 验证 `session_goals` / `goal_tasks` 没有放进共享 DDL，只有 goal-mode 路径实际落库时才建表。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 在 repo root 跑：`cd backend ; $env:PYTHONPATH="."; .\.venv\Scripts\python.exe ..\scripts\e2e_flag_off_baseline.py` | 退出码 0；输出 `PASS: flag-OFF baseline ok; no session_goals table; sha256=<16hex>` |
| 2 | 可选真机：设置 `$env:DESKPET_USER_DATA_DIR="G:\projects\deskpet\.tmp\fp1-flagoff-userdata"` 且 `[features] goal_mode=false` 冷启动，发送普通对话 | app 正常对话，不启用 `/goal` |
| 3 | 表清单查询：`sqlite3 "${env:DESKPET_USER_DATA_DIR}\data\state.db" "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('session_goals','goal_tasks');"` | 无输出；两个表都不存在 |

**预期总判**: 脚本 PASS；flag-OFF 真机 DB 无 `session_goals`/`goal_tasks`。
**判定**: ___

---

## TC-1.5 — 多目标 last-write-wins：连设两个目标，`/goal` 返回最新（✅真模拟人 + DB，边界）★

**目的**: 验证物理上可保留多行 active 目标，但 `get(sid)` / `/goal` 返回内存最新目标。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | `backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal 目标A：写周报"` | 返回已设置目标 A |
| 2 | `backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal 目标B：订机票"` | 返回已设置目标 B |
| 3 | `backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal"` | 返回 `当前目标: 目标B：订机票...` |
| 4 | DB 查询：`sqlite3 "${env:DESKPET_USER_DATA_DIR}\data\state.db" "SELECT text,status FROM session_goals WHERE session_id='<当前sid>' AND status='active' ORDER BY updated_at DESC LIMIT 2;"` | 第一行是目标B；第二行可为目标A（实现没有自动 abandoned 旧 active 行，这是当前真实行为） |
| 5 | 截图 `tc-1.5-latest-active.png` | 截图可见 `/goal` 返回目标B |

**预期总判**: `/goal` 返回最新目标B；DB 按 `updated_at DESC` 最新行为一致。
**判定**: ___

---

## TC-1.6 — WI-1.6 工具路径录制（🟡后端核对，喂 FP-5）★

**目的**: 验证 `ToolPathRecorder.record_tool()` 已由 `AgentLoop` 在每个 tool_result 后喂入；`complete()` 在 FP-5 codify hook `_maybe_codify_skill()` 中调用。注意：`ToolPathRecorder` 本身不落库，也没有成功日志。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 启用 `[skills.codify] enabled=true` 后重启 | boot log 出现 `fp5_codify_wiring_ready tool_path=True candidate_store=True llm=True` |
| 2 | 设目标并跑一个 >=5 工具的多步任务（与 FP-5 TC-5.3 可共用） | agent loop log / 前端 tool_call 流显示多个工具调用 |
| 3 | grep backend log `execute_tool` 或前端 tool_call 事件 | 至少 5 次不同/连续工具调用；这是 `record_tool()` 的输入证据 |
| 4 | 任务结束后观察 FP-5 codify 侧效果 | 若触发条件满足，应出现 `skill_candidate_proposed` 或后续 `skill_codified path=...`；若未满足，只能判定“已喂 recorder 输入”，不能证明候选生成 |
| 5 | 后端单测兜底：`cd backend ; $env:PYTHONPATH="."; .\.venv\Scripts\pytest.exe tests\test_tool_path_recording.py tests\test_deskpet_skill_remount_after_compaction.py::test_tool_path_recorder_fed_during_run -q` | 单测通过，证明 `record_tool` / `complete` / `get_completed_path` API 与 agent_loop 喂数接线有效（Round 2 校准：真实测试名为 `test_tool_path_recorder_fed_during_run`） |

**预期总判**: 真机有工具调用输入；codify 开启时 hook 可消费 ToolPath；无“ToolPath complete”生产日志可 grep，不得伪造。
**判定**: ___

---

## TC-1.7 — `/goal` 接通 goal_checker 末轮循环（✅真模拟人 + log）★

**目的**: 验证 goal_mode 下 `AgentLoop` 末轮接 `GoalChecker`：未完成时注入 `[goal] 未达成` 并继续，完成时 `mark_done` 并持久化 done。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | `backend\.venv\Scripts\python.exe testcase\_send.py code0 "/goal 生成一份包含三点结论的调研摘要"` | 返回已设置目标 |
| 2 | `backend\.venv\Scripts\python.exe testcase\_send.py code0 "开始做，没完成就继续"` | agent loop 开始执行 |
| 3 | grep log `goal_checker_invoked` / `goal_checker_nudge_injected` / `goal_checker.marked_done` / `goal_checker.skipped` | 至少出现一种 goal_checker 相关锚点：未完成为 `goal_checker_nudge_injected sid=... iter=M/10`；完成为 `goal_checker.marked_done sid=...`；LLM 降级为 `goal_checker.skipped sid=...` |
| 4 | DB 查询：`sqlite3 "${env:DESKPET_USER_DATA_DIR}\data\state.db" "SELECT status,iterations_used FROM session_goals WHERE session_id='<当前sid>' ORDER BY updated_at DESC LIMIT 1;"` | 未完成 rebound 时 `iterations_used>0,status='active'`；完成时 `status='done'` |
| 5 | 截图 `tc-1.7-goalcheck.png` | 截图展示执行过程或最终状态 |

**预期总判**: log 与 DB 均证明 goal_checker 接入；降级时必须标注为 `goal_check=skipped`，不能当作完成。
**判定**: ___

---

## TC-1.8 — R-T7 多 worktree / 多 `USER_DATA_DIR` 隔离（🟡DB，新增边界）★

**目的**: 验证不同 `DESKPET_USER_DATA_DIR` 的 `state.db` 互不串目标，避免多 worktree 手测互相污染。

| # | 步骤 | 预期结果 |
|---|---|---|
| 1 | 用 `DESKPET_USER_DATA_DIR=G:\projects\deskpet\.tmp\fp1-userdata-a` 启动，发送 `/goal A目录目标` | A 目录 DB 有 active 目标 A |
| 2 | 退出后改用 `DESKPET_USER_DATA_DIR=G:\projects\deskpet\.tmp\fp1-userdata-b` 启动，发送 `/goal` | B 目录首次查询返回 `当前无活动目标`，boot log `goal_store_load_persisted restored=0` |
| 3 | 在 B 目录发送 `/goal B目录目标` | B 目录 DB 有 active 目标 B |
| 4 | 分别查询：<br>`sqlite3 "G:\projects\deskpet\.tmp\fp1-userdata-a\data\state.db" "SELECT text FROM session_goals WHERE status='active' ORDER BY updated_at DESC LIMIT 1;"`<br>`sqlite3 "G:\projects\deskpet\.tmp\fp1-userdata-b\data\state.db" "SELECT text FROM session_goals WHERE status='active' ORDER BY updated_at DESC LIMIT 1;"` | A 查询只返回 `A目录目标`；B 查询只返回 `B目录目标` |

**预期总判**: 两个 user-data DB 的目标完全隔离。
**判定**: ___

---

## 测试结果汇总表（执行时填写）

| TC | 范围 | E2E 等级 | 判定 | 截图/log/DB 证据 | 备注 |
|---|---|---|---|---|---|
| TC-1.1 | 重启仍在 | ✅真模拟人 pass^k | | | |
| TC-1.2 | iterations 恢复 | 🟡log+DB | | | |
| TC-1.3 | clear→abandoned | 🟡DB | | | |
| TC-1.4 | flag-OFF 不建表 | 🔵脚本 | | | |
| TC-1.5 | 多目标最新 | ✅真模拟人+DB | | | 当前旧 active 行不会自动 abandoned |
| TC-1.6 | ToolPath 录制 | 🟡后端 | | | 无生产成功日志；用工具调用输入 + codify/单测证明 |
| TC-1.7 | goal_checker 接电 | ✅真模拟人+log | | | |
| TC-1.8 | USER_DATA_DIR 隔离 | 🟡DB | | | 新增 R-T7 |

---

## 附录：FP-1 关键 log / DB 速查

| 类别 | 锚点 |
|---|---|
| goal_mode ON | `companion_code_v1_goal_mode_ready` |
| 持久化绑定 | `goal_store_bound_persistence` |
| 启动恢复 | `goal_store_load_persisted restored=N`；store 内部也会打 `goal_store.load_persisted restored=N` |
| 持久化失败 | `goal_store.persist failed sid=...`、`goal_store.load_persisted failed: ...`、`persist_abandon failed sid=...` |
| goal_checker 未完成 | `goal_checker_nudge_injected sid=... iter=M/10` |
| goal_checker 完成 | `goal_checker.marked_done sid=...` |
| goal_checker 降级 | `goal_checker.skipped sid=...`、metric `goal_check_skipped` |
| ToolPath 接线 | `fp5_codify_wiring_ready tool_path=True candidate_store=True llm=True`、tool_call/`execute_tool` 事件；`ToolPathRecorder` 成功录制本身无生产日志 |
| active 目标 SQL | `SELECT goal_id,text,status,progress,criteria,max_iterations,iterations_used,set_at,updated_at FROM session_goals WHERE session_id='<sid>' AND status='active' ORDER BY updated_at DESC LIMIT 1;` |
| clear 历史 SQL | `SELECT goal_id,text,status,iterations_used FROM session_goals WHERE goal_id='<goal_id>';` |
| flag-OFF 表检查 | `SELECT name FROM sqlite_master WHERE type='table' AND name IN ('session_goals','goal_tasks');` |
