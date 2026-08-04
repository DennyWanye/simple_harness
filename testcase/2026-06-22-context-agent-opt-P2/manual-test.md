# 上下文 & Agent 优化 P2 手工测试用例（windows-mcp 真模拟人）

> **被测范围**: 方向二「对标系统优化」P2 **四个真缺口新建**（已实读核实落地，非纯点亮）：
> - **WI-OC-2 子代理背压/lane 累计指标可观测（无 flag，always-on）** — `subagent_scheduler.py` 累计 `peak_concurrent` / `total_queued` / `total_rejected` + `lane_wait` 分位，前端 `SubagentProgressPanel.tsx` 展示「峰值 / 累计入队 / 拒绝」。**纯观测增强，最易真机暴露。**
> - **WI-OC-1 子代理 spawn 显式 depth 上界（flag `agent.subagent_explicit_depth` 默认 False）** — `task_kinds.py check_spawn_depth`，env `DESKPET_SUBAGENT_DEPTH` 传播，超 `max_spawn_depth`（默认 1，硬上限 3）→ 拒绝（`forbidden:"spawn_depth"`）。OFF 仍靠 `_strip_forbidden` 保 depth=1（BC）。
> - **WI-OH-4 记忆 self-curation nudge（flag `memory.v2.curation_nudge` 默认 False）** — `memory/curation.py MemoryCurator`，agent_loop FinalEvent 后**每 8 轮** fire-and-forget 触发 → LLM 自决该不该记 → `facts.upsert`。
> - **WI-CC-2 plan mode 物理只读（flag `features.plan_read_only` 默认 False）** — `registry.py execute_tool` 按 `permission_category`（`_WRITE_PERMISSION_CATEGORIES`）拦写类工具；plan-confirm 硬门挂起期 main.py 置该 session 物理只读。
>
> **对应 Plan**: [`plans/2026-06-22-context-and-agent-optimization/02-reference-systems-optimization.md`](../../plans/2026-06-22-context-and-agent-optimization/02-reference-systems-optimization.md)（§3.4 OC-1/OC-2 / §3.1 OH-4 / §3.3 CC-2）。
>
> **最后更新**: 2026-06-23（用例定义，待真机执行回填）

---

## 0. 被测改动摘要（读码核实，2026-06-23）

### WI-OC-2 — 调度器累计指标（**无 flag，always-on**，纯观测加法）

| 文件:符号 | 内容 | 驱动的可见物 |
|---|---|---|
| `backend/deskpet/agent/subagent_scheduler.py:57-60` | `_peak_concurrent` / `_total_queued` / `_total_rejected` / `_lane_wait_ms[]` 累计计数器（`__init__` 初始化） | 调度器全局累计统计 |
| `subagent_scheduler.py:136` | `run()` 入队即 `_total_queued += 1`（只增不减，吞吐口径） | 累计入队 |
| `subagent_scheduler.py:159-160` | 进 running 时 `if running>peak: peak=running`（背压真生效证据，≤ global cap） | 峰值并发 |
| `subagent_scheduler.py:213/224` | 运行中 / 排队中被取消 → `_total_rejected += 1`（真错误不计入） | 拒绝/取消计数 |
| `subagent_scheduler.py:83-89 _metrics()` | 每条进度事件 `_emit` 附 `{peak_concurrent, total_queued, total_rejected}`（纯增 key，旧前端忽略 = BC） | WS 推送累计字段 |
| `subagent_scheduler.py:239-255 snapshot()` | 既有 `running`/`queued` + 累计三字段 + `lane_wait_p50_ms`/`p95_ms` 分位 | observability 快照 |
| `tauri-app/src/code-panel/subagentStore.ts:33-37` | `SchedulerMetrics` 累计字段（`peak_concurrent`/`total_queued`/`total_rejected`），缺字段保持旧值（BC 降级） | 前端 store |
| `tauri-app/src/code-panel/SubagentProgressPanel.tsx:280-313` | **累计观测汇总区**：任一 metric>0 才渲染 → `峰值 N` / `累计入队 N`（testid `subagent-metric-queued`）/ `拒绝 N`（testid `subagent-metric-rejected`） | UI 展示 |

- **既有进度事件不重做**：`subagent_scheduled kind=... run_id=... task_id=...`（`scheduler.py:167`）+ 运行中 N/M（`SubagentProgressPanel.tsx`）已在，本期只**加累计层**。
- **优雅降级**：累计区 `(total_queued>0 || peak_concurrent>0 || total_rejected>0)` 才渲染（`SubagentProgressPanel.tsx:282-284`）→ **无子代理时累计区不出现**（边界）。
- **metrics_sink**：`_record_lane_wait`（`scheduler.py:105-115`）每次跑完 `record("subagent_lane_wait", {kind, duration_ms})`，失败静默吞（不阻调度）。

### WI-OC-1 — spawn 显式 depth 上界（flag `agent.subagent_explicit_depth` 默认 False）

- **flag**：`config.py:443 agent.subagent_explicit_depth: bool = False`，section = `[agent]`。配套 `agent.max_spawn_depth`（默认 1，`task_kinds.py:45 _DEFAULT_MAX_SPAWN_DEPTH`），硬上限 `HARD_MAX_SPAWN_DEPTH=3`（`task_kinds.py:44`，任何配置都 clamp 不超）。
- **机制**：`task_kinds.py:101 check_spawn_depth(raw_agent_cfg)` —— flag OFF → no-op（BC，仍靠 `_strip_forbidden` 保 depth=1）；flag ON 且子深度（`current_depth+1`）> `max_spawn_depth` → 抛 `SpawnDepthExceeded`（`task_kinds.py:48`，文案「子代理 spawn 深度 N 超过上界 M（递归守门：拒绝再 spawn）」）。
- **depth 传播**：env `DESKPET_SUBAGENT_DEPTH`（`task_kinds.py:43 _DEPTH_ENV`）；`current_spawn_depth()` 读 env（顶层=0）；spawn 子代理前 `child_depth_env()` 注 `depth+1`。
- **4 处 spawn 入口已接 check_spawn_depth**（拒绝均返 `forbidden:"spawn_depth"`）：
  - `tools/code_tools/agent_parallel_tool.py:360`、`spawn_subagents_tool.py:135`、`agent_tool.py:112`、`agent/team/spawn_team.py:214`。
- **BC**：flag OFF（出厂）→ `check_spawn_depth` 直接 return、`child_depth_env` 写的 env 不被任何检查读取 → 行为字节不变；仍靠 `_strip_forbidden`（子代理拿不到 spawn 工具）保 depth=1。

> ⚠️ **真机难点（诚实标注）**：现状 `_strip_forbidden` 已剔掉子代理的所有 spawn 类工具（`agent`/`agent_parallel`/`spawn_team`/`spawn_subagents`/`deepresearch`），子代理**根本拿不到** spawn 工具 → 真机难构造「子代理内再 spawn」嵌套来触发 depth 上界。OC-1 是 *第二道* defence-in-depth，正常路径下 `_strip_forbidden` 先生效。真机只能验「flag OFF 默认 depth=1（子代理不嵌套）」+「flag ON 启动不崩、registry 接电」；depth 上界拒绝的精确触发靠 `test_subagent_depth.py` 单测。

### WI-OH-4 — 记忆 self-curation nudge（flag `memory.v2.curation_nudge` 默认 False）

- **flag**：`config.py:221 memory.v2.curation_nudge: bool = False`，section = `[memory.v2]`。频率门控 `config.py:224 curation_nudge_every_n_turns: int = 8`。
- **构造**（仅 flag ON + facts store + LLM 可用，`main.py:2631-2645`）：`MemoryCurator(_facts_store, _curation_llm)` → `service_context.register("memory_curator", ...)` → log `oh4_curation_nudge_wired every_n=N`。
- **触发**（`agent_loop.py:1947`）：FinalEvent 后调 `_maybe_fire_curation_nudge(session_id, working_messages)`（`agent_loop.py:2294`），每 `curation_nudge_every_n_turns` 轮 fire-and-forget 调 `MemoryCurator.nudge(recent)`（`agent_loop.py:2323`）。
- **curator**（`memory/curation.py:237 nudge`）：取最近 N（≤12）user/assistant 轮 → LLM 自决 → 3 级 JSON fallback → `should_remember=True` 的走 `facts.upsert`（`curation.py:315`，category preference/fact/goal/constraint/context）。**永不抛**（失败返 `[]`，不挡主回合）。
- **log 锚点**：成功 `oh4_curation_nudge sid=<sid> turn=<n> decisions=<d> remembered=<r>`（`agent_loop.py:2325`）；失败 `oh4_curation_nudge_failed sid=<sid>: <exc>`（`agent_loop.py:2333`）。
- **BC**：flag OFF（出厂）→ curator 不构造、`_memory_curator=None`、`_maybe_fire_curation_nudge` no-op、不写 facts = 字节级一致。

> ⚠️ **真机难点（诚实标注）**：① nudge 是 **fire-and-forget 异步**，真机看 log 锚点（`oh4_curation_nudge`）为主，不阻塞 UI；② 需连续聊 **≥8 轮** 才到频率门；③ LLM 是否判「值得记」取决于对话内容 + LLM 主观，须聊**含明确隐含偏好**的内容（如反复提到用 neovim / 喜欢深色主题）才大概率 `remembered≥1`。

### WI-CC-2 — plan mode 物理只读（flag `features.plan_read_only` 默认 False）

- **flag**：`config.py:441 features.plan_read_only: bool = False`，section = `[features]`。**前置依赖 `features.plan_confirm_gate`**（`config.py:439`，code 模式出 plan 后 emit `awaiting_confirm` 等用户点[执行]）——plan_read_only 复用其挂起期作进/出只读信号。
- **写类判据**（`registry.py:74 _WRITE_PERMISSION_CATEGORIES`）：`{write_file, desktop_write, shell, skill_install}` —— **按 `permission_category` 判，自动覆盖所有写产物工具**（write_file/edit_file/run_shell + ppt/excel/doc/pdf/memory 写工具），非硬编码工具名集。
- **机制**：`registry.py:302 set_plan_read_only(sid, enabled)` per-session 开关 → `execute_tool`（`registry.py:708-722`）中若 `sid in _plan_read_only_sessions and spec.permission_category in _WRITE_PERMISSION_CATEGORIES` → **handler 不执行**，返 error「规划期只读：工具 ... 在计划确认前不可执行。请先批准执行计划（点[执行]）...」+ log `plan_read_only_deny sid=... tool=... category=...`。
- **进/出只读信号**（`main.py:6663-6702`，仅 `features.plan_read_only` ON）：plan-confirm 硬门挂起前 `set_plan_read_only(sid, True)` + log `plan_read_only_enter sid=...`；用户点[执行]/取消/超时后 `set_plan_read_only(sid, False)` + log `plan_read_only_exit sid=...`。
- **BC**：flag OFF（出厂）→ main.py 永不置位 → `_plan_read_only_sessions` 恒空 → `execute_tool` 该分支 short-circuit、写类工具照常 = 字节级一致。

> ⚠️ **真机难点（诚实标注）**：① 需进 **code 模式** + 开 `plan_confirm_gate` + `plan_read_only` 两个 flag，setup 较重；② 需让桌宠在「出 plan 等确认」窗口期尝试调写类工具——LLM 是否在该窗口真调写类工具受意图路由影响，标 best-effort。

---

## 1. 禁止的绕过方式（HARD CONSTRAINT — 违反即视为未完成）

> 见 `CLAUDE.md`「🔒 手工测试纪律」。本文档**所有标「UI 真测」的 case 必须真模拟人**：
> windows-mcp Screenshot/Snapshot 抓状态 → 真坐标 SetCursorPos+SendInput 点击 / Clipboard 粘贴+Ctrl+V → 截图验证 → grep tauri dev log 判定。

- ❌ **不允许** WebSocket 直连 backend 推 `subagent_progress` 事件 / 注 plan_read_only / 触发 curation nudge 当 UI 证据（协议层 ≠ 用户行为）。
- ❌ **不允许** `pytest test_scheduler_metrics.py`／`test_subagent_depth.py`／`test_memory_curation.py`／`test_plan_mode_readonly.py` / `import` backend 查 `SubagentScheduler.snapshot()` 含 `peak_concurrent` / 查 `config.plan_read_only` 返回值当「点亮了」证据（代码加载 ≠ 用户链路触发）。
- ❌ **不允许** 因 windows-mcp Click schema bug / SendKeys 中文 IME 报错就 fallback 到上述方式 —— 用 workaround（SetCursorPos+SendInput / Clipboard+Ctrl+V）克服，retry ≥3 次不同手法才可标「环境受限」。
- ✅ **必须**：每个动作前 declare `坐标=(x,y) | 动作=click/type | 期望=…`；截图存 `screenshots/`；log 证据贴 grep 锚点。

---

## 2. 测试前置 / 环境配方（照 CLAUDE.md 坑 #7/#8/#9）

| 项 | 要求 |
|---|---|
| **跑当前 checkout 代码** ★ | **不要手动起 backend**（Tauri 自己 spawn）。只给 **Tauri 进程**注入 env：<br>`DESKPET_BACKEND_DIR=G:\projects\deskpet\backend`<br>`DESKPET_PYTHON=G:\projects\deskpet\backend\.venv\Scripts\python.exe`<br>`DESKPET_BACKEND_PORT=8100`<br>`DESKPET_DEV_MODE=1`<br>启动日志须出现 `[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`。**若见 `[backend_launch] Bundled exe=...` 说明跑旧 frozen exe（无本改动 → 白测，先修环境）。** |
| **不双起 backend / vite** ★ | 坑 #7/#9：**只**跑 `npx tauri dev`（带上面 env），它自管唯一 vite + spawn backend。别另手动 `python main.py`（端口 8100 双占 → 桌宠弹「启动失败」）或 `npm run dev:relay`（双 vite 抢 strictPort）。 |
| **登录态** | 走 onboarding 用 `LOCAL-DEV-CREDENTIALS.md`（gitignored）的 dev 账号登录中转站，等 relay 下发 key 写 keychain。**截图前先关 onboarding 窗**，避免截到账号密码（CLAUDE.md 安全约束）。`userdata/llm_runtime.json` 应存在且 `"model":"gpt-5.5"`。 |
| **真实 user_data_dir** ★ | 改 config / overrides 时注意：app 真实 user_data_dir 是 `%APPDATA%\deskpet`（**不是** `backend/userdata`）—— 改错文件无效。 |
| **改 TOML 用 Write 工具，不要 PowerShell `Out-File`** ★ | `Out-File -Encoding utf8` 会给 TOML 加 BOM → `tomllib` 解析失败。改 config 用 Write 工具（无 BOM）或确保 utf8-no-bom。 |
| **flag 配置位置 + section** ★ | 改 `%APPDATA%\deskpet\config.toml` 对应段并**重启**生效：<br>**OH-4** → `[memory.v2]` 段加 `curation_nudge = true`（可选 `curation_nudge_every_n_turns = 2` 降阈值便于真机触发）<br>**CC-2** → `[features]` 段加 `plan_read_only = true` **且** `plan_confirm_gate = true`<br>**OC-1** → `[agent]` 段加 `subagent_explicit_depth = true`<br>**OC-2** → **无 flag**（always-on，出厂即可观测） |
| **抓日志** | tauri dev 重定向 log（backend structlog 走 stderr → `Stdio::inherit()` → 落 tauri dev log）。grep 锚点见各 TC。 |
| **截图/日志存档** | 截图存 `testcase/2026-06-22-context-agent-opt-P2/screenshots/<case-id>.png`；log grep 片段贴进各 case「log 证据」栏。 |

### 启动命令（参考；按本机路径调整）

```powershell
# 0) 先关旧桌宠（坑 #1：TaskStop 留 orphan）
taskkill /F /IM deskpet.exe 2>$null

# 1) 注入 env 启动 Tauri（Tauri 自己 spawn backend，别手动起 backend）
$env:DESKPET_BACKEND_DIR  = "G:\projects\deskpet\backend"
$env:DESKPET_PYTHON       = "G:\projects\deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_BACKEND_PORT = "8100"
$env:DESKPET_DEV_MODE     = "1"
# 在 tauri-app 目录跑：
npx tauri dev
```

### log grep 锚点速查

```powershell
# 跑当前码（非 frozen）
#   期望: [backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend

# OC-2 进度 + 累计（always-on，并发子代理时）
#   期望(既有): subagent_scheduled kind=<k> run_id=<sid>.dr-<i> task_id=<...>
#   累计字段在 subagent_progress WS 事件 payload 上（peak_concurrent/total_queued/total_rejected）
#   lane_wait: subagent_lane_wait（metrics_sink）

# OC-1 depth（flag ON 才生效；真机难精确触发，靠单测）
#   拒绝期望: SpawnDepthExceeded / forbidden":"spawn_depth"

# OH-4 curation（flag ON + ≥N 轮后）
#   接电: oh4_curation_nudge_wired every_n=<N>
#   触发: oh4_curation_nudge sid=<sid> turn=<n> decisions=<d> remembered=<r>
#   失败: oh4_curation_nudge_failed sid=<sid>: <exc>
#   OFF（出厂）则三行均不出现

# CC-2 plan 只读（flag ON + code 模式 plan 挂起期）
#   进/出: plan_read_only_enter sid=<sid> / plan_read_only_exit sid=<sid>
#   拦截: plan_read_only_deny sid=<sid> tool=<name> category=<write_file|shell|...>
#   OFF（出厂）则三行均不出现，写类工具照常执行
```

---

# 第一部分 — WI-OC-2 子代理背压/lane 累计指标（★ 无 flag，最易真机观测）

> ⚙️ **无 flag，always-on**。触发 deepresearch 子代理并发（fan-out）→ `SubagentProgressPanel` 出现 N/M 运行中 → **展开面板累计观测区**显示「峰值 / 累计入队 / 拒绝」。
> **判定核心**：面板累计区显示 `peak_concurrent` / `total_queued`（≥ 子代理数）+ tauri dev log 进度事件携带累计字段；多轮触发后累计数**递增不归零**。
> **边界**：无子代理时累计区不渲染（优雅降级）。

---

## TC-OC2-1 — deepresearch 并发触发 → 面板累计区显示峰值/累计入队/拒绝（★ 必过）

**类型**: UI 真测（真模拟人）+ 后端日志判定

**目的**: 验子代理 fan-out 并发时，`SubagentProgressPanel` 累计观测区真显示 `峰值 N` / `累计入队 N`（≥ 子代理数）/ `拒绝 N`，且 log 进度事件携带累计字段。

**前置**: §2 全满足；启动日志确认 Dev python（非 Bundled exe）；onboarding 已登录；**出厂态即可**（OC-2 无 flag）。需 deepresearch 子代理 fan-out 可用（默认链路）。

| 步骤 | 动作（declare：坐标 / 动作 / 期望） | 期望结果 |
|---|---|---|
| 1 | Screenshot 抓桌宠主界面，记录对话输入框坐标 `(x,y)` | 截图 `screenshots/TC-OC2-1-step1.png` |
| 2 | `坐标=(x,y) \| 动作=Clipboard "深度研究一下宁德时代2024年的经营情况和行业地位" → Ctrl+V → Enter` | 桌宠触发 deepresearch，多子问题派 research 子代理并发 |
| 3 | 子代理开始并发后，定位 code/子代理面板（SubagentProgressPanel）入口坐标，点击展开 | 面板出现「运行中 N/M」运行项；截图 `screenshots/TC-OC2-1-step3.png` |
| 4 | Screenshot 抓面板**累计观测区**（运行项下方汇总条），核对文案 | 出现 `峰值 N`（peak_concurrent，N≥1 且 ≤global cap 4）+ `累计入队 N`（total_queued ≥ 子代理数）+ 可选 `拒绝 N`；截图 `screenshots/TC-OC2-1-step4.png` |
| 5 | grep tauri dev log `subagent_scheduled` | 出现多条 `subagent_scheduled kind=research run_id=<sid>.dr-<i> task_id=...`（子代理调度锚点） |
| 6 | grep tauri dev log 进度事件 / `subagent_lane_wait`（metrics_sink） | 进度事件 payload 含 `peak_concurrent/total_queued/total_rejected`；`subagent_lane_wait` 记录存在 |

**可观测证据**:
- ✅ 累计区成立：面板显示 `峰值`/`累计入队`（≥ 子代理数），log 有 `subagent_scheduled` ≥N 条 + 累计字段。
- ❌ 复发/异常：面板无累计区（metrics 全 0 没渲染 / 字段没接），或 `累计入队` < 实际子代理数（计数漏）。

**PASS 判据**: 步骤 4 累计区显示 `峰值`+`累计入队`（total_queued ≥ 子代理数）**且** 步骤 5/6 log 有调度锚点 + 累计字段。
**FAIL 判据**: 累计区缺失或累计入队明显少于子代理数。

**判定**: _待真机回填_

---

## TC-OC2-2 — 多轮触发后累计数递增不归零（★ 必过 累计语义）

**类型**: UI 真测 + 后端日志

**目的**: 验累计指标是**调度器生命周期累计**（不随单批出队回落）：连续触发 2 次 deepresearch（或 agent_parallel）后，`累计入队`（total_queued）单调递增、不归零。

**前置**: 接 TC-OC2-1（已触发过一次 fan-out，面板有累计值），桌宠不重启。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 记录 TC-OC2-1 结束时面板 `累计入队` 值 = `T1`（如截图读数） | 记录基线 T1 |
| 2 | `坐标=(x,y) \| 动作=Clipboard "再深度研究一下比亚迪2024年的动力电池业务" → Ctrl+V → Enter` | 第二次 fan-out，又派若干 research 子代理 |
| 3 | 等第二批跑起来，Screenshot 抓面板累计区，读 `累计入队` 值 = `T2` | 截图 `screenshots/TC-OC2-2-step3.png` |
| 4 | 比较 `T2 > T1`（严格递增，第二批入队数累加上去，未归零） | T2 = T1 + 第二批子代理数（递增不归零） |
| 5 | grep tauri dev log 第二批 `subagent_scheduled` 与第一批 run_id 不同 | 两批 run_id 区分（`.dr-` 序号不同），累计字段在第二批事件中更大 |

**可观测证据**:
- ✅ 累计语义成立：`T2 > T1`，累计入队跨批单调递增不回落。
- ❌ 复发：第二批后 `累计入队` 归零或 = 第二批数（说明被当瞬时值重置，不是累计）。

**PASS 判据**: 步骤 4 `T2 > T1`（递增不归零）。
**FAIL 判据**: 累计入队归零 / 未累加。

**判定**: _待真机回填_

> ⚠️ **难点诚实标注**：第二批 fan-out 是否真起多个子代理取决于 LLM 是否把第二个研究也拆成多子问题。若第二次只起 1 子代理，累计仍应 +1（仍递增），只是增量小。retry 时可换更明确的「分头研究 A、B、C 三个方向」措辞逼多子代理。

---

## TC-OC2-3 — 无子代理时累计区不渲染（★ 必过 优雅降级边界）

**类型**: UI 真测（真模拟人）

**目的**: 验边界：纯闲聊 / 不触发子代理的会话，`SubagentProgressPanel` 累计观测区**不渲染**（`total_queued/peak/rejected` 全 0 → 条件 `>0` 不满足 → 整块不出现），不显示空的「峰值 0 / 累计入队 0」噪声。

**前置**: §2 全满足；**新会话**（避免累计被前序 fan-out 污染），出厂态。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 起新会话（或重启桌宠重置调度器累计），Screenshot 主界面 | 截图 `screenshots/TC-OC2-3-step1.png` |
| 2 | `坐标=(x,y) \| 动作=Clipboard "你好呀，今天天气不错，给我讲个笑话吧" → Ctrl+V → Enter` | 桌宠正常闲聊回复，不触发任何子代理 |
| 3 | 打开子代理面板（若可见），Screenshot 抓 | 面板无运行项；**累计观测区不渲染**（无「峰值 / 累计入队 / 拒绝」条）；截图 `screenshots/TC-OC2-3-step3.png` |
| 4 | grep tauri dev log，确认本轮无 `subagent_scheduled` | 无子代理调度（累计未变动 / 仍为 0） |

**可观测证据**:
- ✅ 降级成立：无子代理 → 累计区不出现，面板干净。
- ❌ 复发：累计区渲染出「峰值 0 / 累计入队 0」空噪声（降级条件失效）。

**PASS 判据**: 步骤 3 累计区不渲染 **且** 步骤 4 无 `subagent_scheduled`。
**FAIL 判据**: 累计区渲染空 0 值。

**判定**: _待真机回填_

---

# 第二部分 — WI-OC-1 子代理 spawn 显式 depth 上界（需开 flag）

> ⚙️ flag `agent.subagent_explicit_depth = true`（`[agent]` 段）+ 重启。flag OFF（默认）→ 仍靠 `_strip_forbidden` 保 depth=1（BC）。
> **判定核心**：flag ON 时 depth gate 接电不崩 + 子代理不二次 spawn（depth 到上界拒绝）；拒绝锚点 `SpawnDepthExceeded` / `forbidden:"spawn_depth"`。
> **真机难点**：`_strip_forbidden` 已剥掉子代理的 spawn 工具 → 子代理根本拿不到 spawn 工具 → 真机难精确造嵌套触发上界 → **best-effort + 靠单测**。

---

## TC-OC1-1 — flag OFF（出厂）子代理不嵌套，depth=1（★ 必过 BC）

**类型**: UI 真测 + 后端日志（字节级 BC 验收）

**目的**: 验出厂态（`subagent_explicit_depth` 未开）下，子代理 fan-out 仍是扁平 depth=1（`_strip_forbidden` 保证子代理拿不到 spawn 工具），无 `SpawnDepthExceeded`、无 depth env 检查介入。

**前置**: §2 全满足；**确保 config 未开 `subagent_explicit_depth`（出厂 false）**；已重启。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认 config `[agent]` 无 `subagent_explicit_depth=true` → 重启；启动 log 确认 Dev python | flag OFF 态 |
| 2 | `坐标=(x,y) \| 动作=Clipboard "深度研究一下固态电池的技术路线" → Ctrl+V → Enter` | 触发 deepresearch fan-out，派 research 子代理 |
| 3 | grep tauri dev log `subagent_scheduled`，确认子代理 run_id 都是 `.dr-<i>` 单层（无 `.dr-i.dr-j` 二级嵌套） | 子代理扁平 depth=1，无二次 fan-out |
| 4 | grep tauri dev log，确认**无** `SpawnDepthExceeded` / `forbidden:"spawn_depth"` | flag OFF → depth gate no-op，不介入（仍靠 strip） |
| 5 | Screenshot 抓研究完成回复 | 截图 `screenshots/TC-OC1-1-step5.png`；研究正常完成 |

**可观测证据**:
- ✅ BC 成立：子代理扁平单层，无 depth gate 介入（strip 守门），研究正常完成。
- ❌ BC 破坏：出现二级嵌套 run_id，或出厂态竟报 `SpawnDepthExceeded`（flag 默认翻了 ON）。

**PASS 判据**: 步骤 3 子代理单层 **且** 步骤 4 无 depth gate 拒绝日志。
**FAIL 判据**: 出现嵌套或出厂态 depth gate 介入。

**判定**: _待真机回填_

---

## TC-OC1-2 — flag ON 启动接电 + 复杂任务子代理仍不嵌套（best-effort）

**类型**: 配置改 + UI 真测 + 后端日志（best-effort）

**目的**: 验 flag ON 后 depth gate 接电（启动不崩、`check_spawn_depth` 在 4 处 spawn 入口生效）；复杂任务触发子代理时子代理不再二次 spawn（depth 上界=1 拒绝，与 strip 双保险一致）。

**前置**: `%APPDATA%\deskpet\config.toml` `[agent]` 段加 `subagent_explicit_depth = true`（Write 工具，无 BOM）→ 重启。**验完按需还原。**

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 改 config 开 `subagent_explicit_depth=true` → 重启；启动 log 确认正常启动（无 ConfigError）+ Dev python | flag ON 态，启动正常 |
| 2 | `坐标=(x,y) \| 动作=Clipboard "深度研究一下钠离子电池的产业化进展，分头查技术、成本、厂商" → Ctrl+V → Enter` | 触发 fan-out，派多 research 子代理 |
| 3 | grep tauri dev log，确认子代理仍单层（depth=1），无二级嵌套 | 子代理不二次 spawn（strip + depth gate 双保险） |
| 4 | grep tauri dev log，若有子代理尝试再 spawn → 应见 `SpawnDepthExceeded` / `forbidden:"spawn_depth"`（正常路径下 strip 先生效，多半不出现） | 若出现则证明 depth gate 真拦；若不出现说明 strip 已挡在前（仍 PASS BC） |
| 5 | Screenshot 抓研究完成 | 截图 `screenshots/TC-OC1-2-step5.png`；研究正常完成（flag ON 不破功能） |

**可观测证据**:
- ✅ 接电成立：flag ON 启动正常 + 子代理仍单层 + 研究完成（depth gate 不破功能）。
- ❌ 复发：flag ON 后启动崩 / 子代理出现嵌套未被拦 / 研究链路被 depth gate 误杀。

**PASS 判据**: 步骤 1 启动正常 **且** 步骤 3 子代理单层 **且** 步骤 5 研究完成。
**FAIL/best-effort**: depth 上界精确拒绝（步骤 4）真机难触发（strip 先挡）→ 标 best-effort，精确触发靠 `test_subagent_depth.py` 单测。

> ⚠️ **诚实标注（best-effort）**：`_strip_forbidden` 已剥掉子代理 spawn 工具，正常路径子代理无法尝试再 spawn → `check_spawn_depth` 的拒绝分支真机几乎不被走到。本 TC 主要验「flag ON 不破功能（接电 + BC 行为）」；depth 上界拒绝逻辑由 `backend/tests/test_subagent_depth.py` 单测覆盖（depth 达上界返 forbidden）。

**判定**: _待真机回填（depth 拒绝标 best-effort/靠单测）_

---

# 第三部分 — WI-OH-4 记忆 self-curation nudge（需开 flag）

> ⚙️ flag `memory.v2.curation_nudge = true`（`[memory.v2]` 段）+ 重启。可选 `curation_nudge_every_n_turns = 2`（降阈值便于真机触发，默认 8）。
> **判定核心**：连续聊 ≥N 轮（含隐含偏好）→ 第 N 轮后 fire-and-forget curation → log `oh4_curation_nudge` + facts 新增「值得记」行。
> **真机难点**：fire-and-forget 异步看 log 锚点；需 ≥N 轮 + 含明确偏好；LLM 主观判定 → best-effort。flag OFF（默认）→ 不触发（BC）。

---

## TC-OH4-1 — flag ON 连聊 ≥N 轮含偏好 → curation nudge 触发 + facts 新增（★ 必过）

**类型**: 配置改 + UI 真测（真模拟人）+ 后端日志

**目的**: 验 OH-4 全链路：开 flag → 连续聊含隐含偏好的多轮对话 → 第 N 轮 FinalEvent 后 fire-and-forget 触发 curation → log `oh4_curation_nudge ... remembered≥1` + facts 表新增对应行。

**前置**: §2 全满足；`[memory.v2]` 段加 `curation_nudge = true` + `curation_nudge_every_n_turns = 2`（降阈值，Write 工具无 BOM）→ 重启。启动 log 须有 `oh4_curation_nudge_wired every_n=2`。**验完按需还原。**

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 改 config 开 `curation_nudge=true` + `curation_nudge_every_n_turns=2` → 重启；grep 启动 log `oh4_curation_nudge_wired` | 出现 `oh4_curation_nudge_wired every_n=2`；截图 `screenshots/TC-OH4-1-step1.png`（log 截图） |
| 2 | `坐标=(x,y) \| 动作=Clipboard "我平时写代码都用 neovim，而且特别喜欢深色主题" → Ctrl+V → Enter` | 桌宠回复（第 1 轮） |
| 3 | `坐标=(x,y) \| 动作=Clipboard "对了，我的项目主要是 Rust 写的后端服务" → Ctrl+V → Enter` | 桌宠回复（第 2 轮 → 达频率门，FinalEvent 后触发 curation） |
| 4 | grep tauri dev log `oh4_curation_nudge` | 出现 `oh4_curation_nudge sid=<sid> turn=<n> decisions=<d> remembered=<r>`，`remembered≥1`（neovim/深色主题/Rust 至少记一条） |
| 5 | grep tauri dev log，确认无 `oh4_curation_nudge_failed`（curation 未异常） | 无失败行（fire-and-forget 成功落 facts） |
| 6 | （可选）重启后问「你还记得我用什么编辑器吗」验召回 | 桌宠答 neovim（curation 记的 fact 被注入召回） |

**可观测证据**:
- ✅ 链路成立：log 有 `oh4_curation_nudge ... remembered≥1`，无 `_failed`；（可选）重启后召回偏好。
- ❌ 复发：无 `oh4_curation_nudge`（curator 没构造 / 没触发 / 没到频率门），或 `remembered=0`（LLM 没判值得记，retry 换更明确偏好），或 `oh4_curation_nudge_failed`。

**PASS 判据**: 步骤 4 出现 `oh4_curation_nudge` 且 `remembered≥1` **且** 步骤 5 无失败。
**FAIL 判据**: 无 curation 触发，或始终 `remembered=0`（≥3 次不同偏好措辞后）。

**判定**: _待真机回填_

> ⚠️ **诚实标注（best-effort 部分）**：① `remembered` 取决于 LLM 主观判「值得记」，retry ≥3 次不同明确偏好（「记住我用 neovim」「我一直用深色主题」）后再判；② fire-and-forget 异步，curation log 可能略滞后于回复，等几秒再 grep；③ 若到了频率门但 `decisions=0`，记「LLM 未判值得记」边界（非阻断 OH-4 机制本身已触发）。

---

## TC-OH4-2 — flag OFF（出厂）不触发 curation（★ 必过 BC）

**类型**: UI 真测 + 后端日志（字节级 BC 验收）

**目的**: 验出厂态（`curation_nudge` 未开）下，无论聊多少轮都**不触发** curation：无 `oh4_curation_nudge_wired`、无 `oh4_curation_nudge`，不写 curation facts = 字节级一致。

**前置**: §2 全满足；**确保 `[memory.v2]` 未开 `curation_nudge`（出厂 false）**；已重启。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认 config 无 `curation_nudge=true` → 重启；grep 启动 log | **无** `oh4_curation_nudge_wired`（curator 未构造） |
| 2 | 连聊 3+ 轮含偏好（同 TC-OH4-1 内容） | 桌宠正常回复 |
| 3 | grep tauri dev log，确认无 `oh4_curation_nudge` 任意行 | 无 curation 触发（`_maybe_fire_curation_nudge` no-op，curator=None） |
| 4 | Screenshot 抓对话流 | 截图 `screenshots/TC-OH4-2-step4.png` |

**可观测证据**:
- ✅ BC 成立：出厂态无 curation 接电行、无触发行 → flag OFF 字节一致。
- ❌ BC 破坏：出厂态竟出现 `oh4_curation_nudge`（flag 默认翻了 ON）。

**PASS 判据**: 步骤 1 无 `_wired` **且** 步骤 3 无 `oh4_curation_nudge`。
**FAIL 判据**: 出厂态出现 curation 触发。

**判定**: _待真机回填_

---

# 第四部分 — WI-CC-2 plan mode 物理只读（需开 flag + code 模式）

> ⚙️ flag `features.plan_read_only = true` **且** `features.plan_confirm_gate = true`（`[features]` 段）+ 重启 + 进 code 模式。
> **判定核心**：code 模式计划期（出 plan 等[执行]）让桌宠尝试改文件/生成 PPT → 被拒「规划期只读」→ 批准[执行]后可改。
> **真机难点**：code 模式 setup 较重 + LLM 是否在 plan 挂起窗口期真调写类工具 → best-effort。flag OFF（默认）→ 不拦（BC）。

---

## TC-CC2-1 — code 模式计划期写类工具被拒「规划期只读」→ 批准后可改（★ 必过）

**类型**: 配置改 + UI 真测（真模拟人）+ 后端日志

**目的**: 验 CC-2 物理只读：开两 flag + code 模式，桌宠出 plan 等确认期间尝试调写类工具（write_file/ppt_create 等）→ `execute_tool` 按 `permission_category` 拦截返「规划期只读」+ log `plan_read_only_deny`；用户点[执行]后只读解除（`plan_read_only_exit`），写类工具放行。

**前置**: §2 全满足；`[features]` 段加 `plan_read_only = true` + `plan_confirm_gate = true`（Write 工具无 BOM）→ 重启；进 code 模式。**验完按需还原。**

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 改 config 开两 flag → 重启；启动 log 确认 Dev python；切到 code 模式 | flag ON + code 模式就绪；截图 `screenshots/TC-CC2-1-step1.png` |
| 2 | `坐标=(x,y) \| 动作=Clipboard "帮我在工作区新建一个 hello.py 写个 hello world 并运行" → Ctrl+V → Enter` | 桌宠先出 plan（计划确认卡），emit `awaiting_confirm`，等用户点[执行] |
| 3 | grep tauri dev log `plan_read_only_enter sid=<sid>` | 出现进只读日志（plan 挂起期置该 session 物理只读） |
| 4 | 观察 plan 挂起期：若桌宠尝试调 write_file/run_shell（计划期抢跑写） → 应被拦 | grep `plan_read_only_deny sid=... tool=write_file category=write_file`（或 shell）；桌宠收到「规划期只读」error |
| 5 | 定位计划确认卡[执行]按钮坐标，`动作=click` 批准执行 | 用户批准；grep `plan_read_only_exit sid=<sid>`（只读解除） |
| 6 | 等桌宠真执行：调 write_file 建 hello.py + run_shell 运行 | 批准后写类工具放行，文件真建 + 运行；截图 `screenshots/TC-CC2-1-step6.png` |
| 7 | grep tauri dev log，确认[执行]后 write_file/run_shell 成功（无 `plan_read_only_deny`） | 解禁后写类工具正常 |

**可观测证据**:
- ✅ 物理只读成立：plan 挂起期 `plan_read_only_enter` + 计划期写类工具 `plan_read_only_deny`；[执行]后 `plan_read_only_exit` + 写类工具放行真建文件。
- ❌ 复发：plan 期写类工具未被拦（仍执行），或[执行]后仍被拦（解除失败），或无 enter/exit 日志（flag 没读到）。

**PASS 判据**: 步骤 3 `plan_read_only_enter` **且** 步骤 5 `plan_read_only_exit` **且** 步骤 6 批准后文件真建。
**FAIL 判据**: 计划期写类工具未拦截，或批准后写类工具仍被拦。

> ⚠️ **诚实标注（best-effort）**：步骤 4 的 `plan_read_only_deny` 取决于 LLM 是否在 plan 挂起窗口期真尝试调写类工具（很多时候 LLM 出完 plan 就等确认、不抢跑）→ 若步骤 4 不出现 deny，仍可凭步骤 3/5/6（enter→exit→批准后真建）判物理只读机制接电（deny 是「真有工具被拦」的加分证据，非唯一证据）。`plan_read_only` 拦截判据由 `backend/tests/test_plan_mode_readonly.py` 单测覆盖（写类 category 被 deny、只读放行、execute 模式全放行）。

**判定**: _待真机回填_

---

## TC-CC2-2 — flag OFF（出厂）计划期不拦写类工具（★ 必过 BC）

**类型**: UI 真测 + 后端日志（字节级 BC 验收）

**目的**: 验出厂态（`plan_read_only` 未开）下，code 模式计划期**不切只读**：无 `plan_read_only_enter/exit/deny`；写类工具照常（沿现有 plan_confirm_gate 流程，但不物理拦写）= 字节级一致。

**前置**: §2 全满足；**确保 `[features]` 未开 `plan_read_only`（出厂 false）**（`plan_confirm_gate` 可保持现状）；已重启；code 模式。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认 config 无 `plan_read_only=true`（默认 OFF）→ 重启；切 code 模式 | flag OFF 态 |
| 2 | `坐标=(x,y) \| 动作=Clipboard "帮我在工作区新建一个 note.txt 写句话" → Ctrl+V → Enter` | 桌宠按现有流程处理（可能出 plan 或直接做，视 plan_confirm_gate） |
| 3 | grep tauri dev log，确认**无** `plan_read_only_enter` / `plan_read_only_exit` / `plan_read_only_deny` 任意行 | 三类只读日志均不出现（main.py 永不置位 → `_plan_read_only_sessions` 恒空） |
| 4 | 确认写类工具未因「规划期只读」被拦（文件按正常流程建/或经 plan 确认建） | 无「规划期只读」error |
| 5 | Screenshot 抓回复 | 截图 `screenshots/TC-CC2-2-step5.png` |

**可观测证据**:
- ✅ BC 成立：出厂态无 plan_read_only 任意日志、无「规划期只读」拦截 → 字节一致。
- ❌ BC 破坏：出厂态出现 `plan_read_only_enter` / `_deny`（flag 默认翻了 ON）。

**PASS 判据**: 步骤 3 无 plan_read_only 任意日志 **且** 步骤 4 无「规划期只读」拦截。
**FAIL 判据**: 出厂态出现 plan_read_only 日志或拦截。

**判定**: _待真机回填_

---

## 4. 用例数统计 + 必过 / best-effort / env-limited 分级

| 分组 | 用例 | 类型 | 等级 | 前置 |
|---|---|---|---|---|
| **OC-2** | TC-OC2-1 并发触发 → 累计区显示峰值/累计入队/拒绝 | UI 真测 + log | **★ 必过** | 无 flag（always-on） |
| **OC-2** | TC-OC2-2 多轮触发累计递增不归零 | UI 真测 + log | **★ 必过（累计语义）** | 无 flag |
| **OC-2** | TC-OC2-3 无子代理累计区不渲染（优雅降级） | UI 真测 | **★ 必过（边界）** | 无 flag |
| OC-1 | TC-OC1-1 flag OFF 子代理不嵌套 depth=1 | UI 真测 + log | **★ 必过（BC）** | flag OFF（默认） |
| OC-1 | TC-OC1-2 flag ON 接电 + 子代理仍不嵌套 | 配置 + UI 真测 + log | best-effort（depth 拒绝靠单测） | **需开 `[agent] subagent_explicit_depth`** |
| **OH-4** | TC-OH4-1 flag ON 连聊 ≥N 轮 → curation 触发 + facts 新增 | 配置 + UI 真测 + log | **★ 必过** | **需开 `[memory.v2] curation_nudge`** |
| OH-4 | TC-OH4-2 flag OFF 不触发 curation | UI 真测 + log | **★ 必过（BC）** | flag OFF（默认） |
| **CC-2** | TC-CC2-1 code 计划期写类工具被拒 → 批准后可改 | 配置 + UI 真测 + log | best-effort（deny 触发靠 LLM 抢跑；拦截逻辑靠单测） | **需开 `[features] plan_read_only`+`plan_confirm_gate` + code 模式** |
| **CC-2** | TC-CC2-2 flag OFF 计划期不拦写类工具 | UI 真测 + log | **★ 必过（BC）** | flag OFF（默认）+ code 模式 |

- **总计 9 个 TC**（OC-2：3 个；OC-1：2 个；OH-4：2 个；CC-2：2 个）。
- **★ 必过（7 个）**：TC-OC2-1、TC-OC2-2、TC-OC2-3、TC-OC1-1、TC-OH4-1、TC-OH4-2、TC-CC2-2。
- **best-effort（2 个）**：TC-OC1-2（depth 上界拒绝靠 `test_subagent_depth.py` 单测，strip 先挡）、TC-CC2-1（plan 期 deny 取决于 LLM 是否抢跑写类工具，拦截逻辑靠 `test_plan_mode_readonly.py` 单测）。
- **需开 flag / code 模式前置**：
  - **OC-2 = 无 flag**（always-on，最易真机观测 → 放首位）。
  - **OC-1 ON 态需 `[agent] subagent_explicit_depth=true`**（TC-OC1-2）；OFF 态（TC-OC1-1）出厂即可。
  - **OH-4 ON 态需 `[memory.v2] curation_nudge=true`**（+ 建议 `curation_nudge_every_n_turns=2` 降阈值，TC-OH4-1）；OFF 态（TC-OH4-2）出厂即可。
  - **CC-2 需 `[features] plan_read_only=true`+`plan_confirm_gate=true` + code 模式**（TC-CC2-1，setup 最重）；OFF 态（TC-CC2-2）仍需 code 模式但 flag 出厂。
- **最易暴露 bug 的边界**：
  - **TC-OC2-2（累计递增不归零）** —— OC-2 最大风险是累计字段被当瞬时值写（每批重置）；这条直接验「跨批单调递增」，若实现把 `total_queued` 当 snapshot 瞬时值，第二批后会归零 → 端到端暴露为「累计入队不累计」。
  - **TC-OC2-3（无子代理累计区不渲染）** —— 验优雅降级条件（`>0` 才渲染）；若条件写错会冒出空「峰值 0 / 累计入队 0」UI 噪声。
  - **各 OFF 态 BC（TC-OC1-1 / TC-OH4-2 / TC-CC2-2）** —— 验三个新 flag「没被误翻默认开」，破坏即字节基线漂移。
- **真机难触发需 best-effort / 靠单测**：
  - **depth 上界精确拒绝**（OC-1）：`_strip_forbidden` 已剥子代理 spawn 工具 → 子代理无法尝试再 spawn → `check_spawn_depth` 拒绝分支真机几乎走不到 → 靠 `test_subagent_depth.py` 单测；真机只验「flag ON 不破功能 + BC」。
  - **plan 期 write 拦截 deny**（CC-2）：取决于 LLM 是否在 plan 挂起窗口抢跑写类工具 → 靠 `test_plan_mode_readonly.py` 单测；真机凭 enter→exit→批准后真建判机制接电。
  - **curation `remembered≥1`**（OH-4）：LLM 主观判「值得记」 + fire-and-forget 异步 + 需 ≥N 轮 → retry ≥3 次不同明确偏好措辞后才可标环境受限并等用户确认。

---

## 5. 结果汇总（待真机执行回填）

| Case | 范围 | 类型 | 等级 | 判定 |
|---|---|---|---|---|
| TC-OC2-1 | 并发触发 → 累计区显示峰值/累计入队/拒绝 | UI 真测 + log | ★ | _待回填_ |
| TC-OC2-2 | 多轮触发累计递增不归零 | UI 真测 + log | ★ | _待回填_ |
| TC-OC2-3 | 无子代理累计区不渲染 | UI 真测 | ★ | _待回填_ |
| TC-OC1-1 | flag OFF 子代理不嵌套 depth=1（BC） | UI 真测 + log | ★ | _待回填_ |
| TC-OC1-2 | flag ON 接电 + 子代理仍不嵌套 | 配置 + UI 真测 + log | best-effort | _待回填（depth 拒绝靠单测）_ |
| TC-OH4-1 | flag ON 连聊 ≥N 轮 → curation 触发 + facts 新增 | 配置 + UI 真测 + log | ★ | _待回填_ |
| TC-OH4-2 | flag OFF 不触发 curation（BC） | UI 真测 + log | ★ | _待回填_ |
| TC-CC2-1 | code 计划期写类工具被拒 → 批准后可改 | 配置 + UI 真测 + log | best-effort | _待回填（deny 靠单测旁证）_ |
| TC-CC2-2 | flag OFF 计划期不拦写类工具（BC） | UI 真测 + log | ★ | _待回填_ |

> **恢复环境（验完必做）**: 删 `%APPDATA%\deskpet\config.toml` 中临时加的 `[agent] subagent_explicit_depth`（TC-OC1-2）/ `[memory.v2] curation_nudge`+`curation_nudge_every_n_turns`（TC-OH4-1）/ `[features] plan_read_only`+`plan_confirm_gate`（TC-CC2-1）行，还原默认 False。OC-2 无 flag 无需还原。
> **结果存档**: 截图存 `testcase/2026-06-22-context-agent-opt-P2/screenshots/`；执行后日志证据贴回各 case「log 证据」栏与本汇总表。
