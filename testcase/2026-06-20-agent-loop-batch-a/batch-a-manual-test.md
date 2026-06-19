# Agent Loop Batch A — 手工测试文档（WI-1 / WI-2 / WI-3）

> **被测功能**（commit `6eb55f4` + `dff43d0`，plan `plans/2026-06-20-agent-loop-optimization/00-PLAN.md`）：
> - **WI-1 tool_choice 硬约束**：`[agent] force_finish_tool_choice=true`（默认）时，tier3（迭代 ≥ `_SELFCHECK_TIER3_AT=30`）/ verify 耗尽时对下一轮 LLM 调用传 `tool_choice="none"`，协议层禁工具，强制收尾；flag off 回到旧行为（BC）。
> - **WI-2 结构化 trace**：`[agent] iteration_trace_enabled=true`（默认 false）时，每轮往 `<user_data>/traces/<task_id>.jsonl` 逐行记 `iter_start / llm_out / tool_result / gate / end`，其中 `tool_calls`/`tool_result` 的 **args 完整未截断**；tracer=None（默认）零开销不建文件（BC）。
>   ⚠️ 真机测试发现：flag 名用 `iteration_trace_enabled` 而非 `trace_enabled`，避免与 `[context.assembler].trace_enabled`（已有 Context Trace UI / P4-S11）命名碰撞（2026-06-20 改名 + 真机验证）。
> - **WI-3 阶段化提示词收尾自查**：**code 模式** persona 常驻「第 5 步 · 收尾自查」（每个 code-mode 任务收尾前都应自查、对照原始需求打勾），**companion 模式**无此段（BC）。
>
> **执行方式（HARD）**：真人 / windows-mcp 在真实 DeskPet 桌宠 App 上**模拟鼠标点击 + 键盘输入**执行。**禁止**用 WebSocket / pytest / import 内部模块当 UI 测试证据（见根 `CLAUDE.md`「🔒 手工测试纪律」）。
>
> **证据存档**：截图 + 日志放 `plans/manual-results-2026-06-20-batch-a/`，本文件只放用例定义。
> **最后更新**：2026-06-20

---

## 0. 改动事实基线（判定依据，便于复核）

| 事项 | 事实 | 出处 |
|---|---|---|
| WI-1 tier3 常数 | 迭代 ≥ 30 进 tier3 | `backend/agent/agent_loop.py:97` `_SELFCHECK_TIER3_AT=30` |
| WI-1 强制手段 | tier3/verify 耗尽时下一轮传 `tool_choice="none"` | `agent_loop.py:935`(chain) / `:1001`(stream) / `:1078`(nonstream) |
| WI-1 flag 默认 true | `force_finish_tool_choice` 默认 True | `main.py:977` `bool(_agent_cfg.get("force_finish_tool_choice", True))` |
| WI-1 verify 末轮收尾 | verify 耗尽→末轮纯文本→`ErrorEvent(verify_exhausted)` 带总结 | `agent_loop.py:1168`、`:1400-1414` |
| WI-2 flag 默认 false | `trace_enabled` 默认 False | `main.py:978` |
| WI-2 trace 路径 | `<user_data>/traces/<task_id>.jsonl` | `main.py:984` `_paths.user_data_dir()/"traces"`、`trace.py:34` |
| WI-2 事件类型 | iter_start / llm_out / tool_result / gate / end | `agent_loop.py:670,1117,1171,1213,...` `self._tracer.record({"kind":...})` |
| WI-2 args 完整 | json.dumps(ensure_ascii=False)，不截断 | `trace.py` record |
| WI-3 code 第 5 步 | code persona 末尾「【第 5 步 · 收尾自查】」 | `backend/deskpet/agent/assembler/components/persona.py:64-68` |
| WI-3 companion 无 | `_DEFAULT_PERSONA_TEMPLATE` 无第 5 步 | `persona.py:23-30` |
| HARD GATE | 启动日志 `[backend_launch] Dev python=... backend_dir=...` | `tauri-app/src-tauri/src/backend_launch.rs` priority-1 |
| 自动化覆盖 | `backend/tests/test_wi1_tool_choice.py`(9)、`test_wi2_trace.py`(2)、`test_wi3_persona.py`(2) 全绿 | — |

---

## 1. 环境启动（前置 — 每次必做）

> 目标：让 Tauri 跑**本仓库 master backend 代码**（含 Batch A 改动），不是旧 frozen exe。

### 1.1 清理孤儿进程
```powershell
taskkill /F /IM deskpet.exe 2>$null
taskkill /F /IM deskpet-backend.exe 2>$null
```

### 1.2 注入环境并启动（在 `G:\projects\deskpet\tauri-app`）
```powershell
$env:DESKPET_BACKEND_DIR = "G:\projects\deskpet\backend"
$env:DESKPET_PYTHON      = "G:\projects\deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_DEV_MODE    = "1"
npx tauri dev
```

### 1.3 HARD GATE（TC-0）
- ✅ **PASS**：日志出现 `[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`
- ❌ **FAIL**：出现 `[backend_launch] Bundled exe=...` → 环境没设对，**整轮作废**，重设再来。

### 1.4 登录
首启走 onboarding，用 `LOCAL-DEV-CREDENTIALS.md`（gitignored）账号登录 → 等 relay 下发 key → 进主界面，真 LLM 链路可用。截图前先关 onboarding 窗（防截到账号密码）。

---

## TC-1 — BC 主路：普通工具调用任务不破（WI-1 默认路径）
**类型**：UI 真测（windows-mcp）｜**需 windows-mcp：是**

**目的**：验证 `tool_choice` 改动后，默认（auto）路径正常调工具、对话正常完成，未破坏既有能力。

| 步 | 坐标/动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | Screenshot 抓主界面，记录聊天输入框坐标 | — | 截图存档 |
| 2 | Click 输入框 → Clipboard 设中文 → Ctrl+V → Enter | `帮我生成一份"季度工作总结"的 PPT 大纲` | 桌宠开始调工具 |
| 3 | WaitFor 工具完成 | — | 产出大纲/卡片，对话正常收尾 |
| 4 | grep tauri-dev 日志 | — | 有 LLM/工具调用，无 `error_*`/异常禁用栈 |

**PASS**：工具成功调用 + 对话正常完成 + 截图见结果。**FAIL**：报错 / 工具被错误禁用 / 卡死。

---

## TC-2 — ★ WI-3 code 模式收尾自查清单（真机观测，头号用例）
**类型**：UI 真测（windows-mcp）｜**需 windows-mcp：是**

**目的**：验证 code 模式下，桌宠完成一个真实小编码任务时，收尾回复**包含**「收尾自查」对照清单（需求清单 / ✓✗部分 / 验证证据 / 剩余项）。这是 persona 级常驻，普通短任务即可触发，无需 30 轮。

**前置**：切到 code 模式（按产品实际入口：聊天里说"进入 code 模式 / 帮我写代码"或点 code 面板，以真实 UI 为准；截图确认已进 code 模式）。

| 步 | 坐标/动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | 确认/切换到 code 模式，Screenshot | — | 截图见 code 模式 |
| 2 | Click 输入框 → Clipboard → Ctrl+V → Enter | `在 backend 下新建 tmp_hello.txt 写一行 "hi"，确认后删掉它，做完告诉我` | 桌宠规划+执行（write_file/run_shell 等） |
| 3 | WaitFor 收尾 | — | 工具真执行 + 收尾回复产出 |
| 4 | 读收尾回复文本（Snapshot/Screenshot） | — | **回复含收尾自查结构**：列出需求、逐项 ✓/✗/部分、给出验证证据（跑了什么/diff）、剩余项 |

**PASS**：收尾回复明显包含「对照原始需求逐项打勾 + 验证证据」的自查清单（截图为证）。**FAIL**：直接"我做完了"无任何对照自查。
**注**：模型措辞可能不逐字含"收尾自查"四字，但**结构**（需求逐项核对 + 证据）必须出现；若完全没有 → FAIL。

---

## TC-3 — WI-3 companion 模式无收尾自查（BC 负向，真机观测）
**类型**：UI 真测（windows-mcp）｜**需 windows-mcp：是**

**目的**：验证 companion（默认陪伴）模式下同类请求**不**出现 code 模式的收尾自查清单（persona BC）。

| 步 | 坐标/动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | 确保在 companion（默认）模式，Screenshot | — | 非 code 模式 |
| 2 | Click 输入框 → Clipboard → Ctrl+V → Enter | `帮我查一下今天的天气并简单说说` | 正常陪伴式回复 |
| 3 | 读回复 | — | 自然口语回复，**无**"逐项对照需求打勾/验证证据"式工程自查清单 |

**PASS**：companion 回复自然、无 code 模式自查清单。**FAIL**：companion 也冒出收尾自查清单（说明 persona 串了）。

---

## TC-4 — WI-2 trace 开启：真机驱动任务 → 生成 jsonl（核心链路）
**类型**：UI 真测驱动 + 文件验证（windows-mcp 驱动）｜**需 windows-mcp：是**

**前置**：编辑 `<user_data>\config.toml`（通常 `%APPDATA%\deskpet\config.toml`）在 `[agent]` 段加：
```toml
[agent]
iteration_trace_enabled = true
```
按手测纪律 taskkill 后**重启**桌宠（重走 1.1–1.3）。

| 步 | 坐标/动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | 确认 config 已写 trace_enabled=true | — | 文件确认 |
| 2 | Click 输入框 → Clipboard → Ctrl+V → Enter（**真机输入**） | `帮我搜索一下 DeskPet 是什么` | 桌宠调工具回复 |
| 3 | WaitFor 收尾 | — | 正常完成 |
| 4 | 看 `<user_data>\traces\` 目录 | — | 出现 `<uuid>.jsonl` |
| 5 | Python 逐行 `json.loads` 该文件 | — | 每行合法 JSON |
| 6 | grep 各 `"kind"` | — | 至少含 `iter_start` / `llm_out` / `end`；调了工具则含 `tool_result` |

**PASS**：jsonl 存在 + 每行合法 JSON + 含必需事件。**FAIL**：无文件 / 行损坏 / 缺事件。
> 驱动对话必须真机点击输入，不能直接写文件/调脚本造数据。

---

## TC-5 — WI-2 trace 完整性 + 并发不损坏（args 不截断 / 多工具一轮）
**类型**：UI 真测驱动 + JSON 验证｜**需 windows-mcp：是**

**目的**：验证 `tool_calls`/`tool_result` 的 args 完整未截断；一轮多工具并发时 jsonl 不交织损坏（§17.3 record 在 gather 后顺序写 + 锁）。

| 步 | 坐标/动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | （承 TC-4 trace on）Click 输入框 → Clipboard → Ctrl+V → Enter | `帮我生成一个 PPT，主题是"新能源汽车 2025"`（易触发多工具/长 args） | 桌宠执行 |
| 2 | WaitFor 收尾 | — | 完成 |
| 3 | Python 逐行 json.loads 最新 jsonl | — | **每行**都合法 JSON（无半行/交织） |
| 4 | 取 `kind="llm_out"` 行的 `tool_calls[].args` / `kind="tool_result"` 行 | — | args 为**完整对象**（长文本/嵌套不被 `[truncated]`/省略） |

**PASS**：全部行合法 JSON + args 完整。**FAIL**：出现损坏行 / args 被截断。

---

## TC-6 — WI-2 flag off（默认）无 trace（BC）
**类型**：UI 真测驱动 + 文件验证｜**需 windows-mcp：是**

**前置**：删除/注释 config 里 `trace_enabled=true`（恢复默认 false），taskkill 后重启。记录 `traces\` 当前文件清单。

| 步 | 坐标/动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | 重启后 Click 输入框 → Clipboard → Ctrl+V → Enter | `今天几号` | 正常回复 |
| 2 | 再发一轮带工具 | `帮我搜索 deskpet` | 正常 |
| 3 | 对比 `traces\` 目录 | — | **无新增** jsonl 文件 |

**PASS**：flag off 后无新 trace 文件、对话照常。**FAIL**：仍生成 trace（flag 未生效）。

---

## TC-7 — WI-1 flag off 回归（force_finish_tool_choice=false）
**类型**：UI 真测｜**需 windows-mcp：是**

**前置**：config 加 `[agent] force_finish_tool_choice = false`，重启。

| 步 | 坐标/动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | Click 输入框 → Clipboard → Ctrl+V → Enter | `帮我生成一份读书计划的 PPT` | 正常调工具完成 |
| 2 | grep 日志 | — | 永不出现强制 `tool_choice=none`（flag off） |

**PASS**：任务正常完成、无强制收尾行为，与改动前一致。**FAIL**：异常。
> 测后把 config 改回默认（删该行）。

---

## TC-8 — WI-1 tier3/verify 强制收尾深层机制（自动化覆盖 + trace 旁证）
**类型**：自动化为主 + GUI 旁证｜**需 windows-mcp：否（旁证可选）**

**说明**：tier3 需单轮迭代 ≥ 30、verify 耗尽需特定 ledger 失配——GUI 难在几次点击内稳定触发，由自动化覆盖：
- `test_wi1_tool_choice.py::test_tier3_forces_tool_choice_none_in_provider_chain / _in_stream_path / _in_nonstream_path`（三路径强制 none）
- `::test_verify_exhausted_grants_final_text_turn`（verify 耗尽→末轮纯文本→`ErrorEvent(verify_exhausted)` 带总结）
- `::test_force_finish_flag_off_never_forces_none` / `::test_verify_exhausted_flag_off_hard_exits_without_final_turn`（flag off BC）

**GUI 旁证（可选）**：若开 trace 跑一个会多轮调工具的长任务（如"深度调研 X 并出 PPT"），在 jsonl 里观察 `llm_out` 行迭代推进；迭代逼近 30 时 tool_choice 字段切到 none（若自然达到）。

**PASS**：自动化全绿（已验证 9/9）。

---

## 9. 结果汇总表

| Case | 功能范围 | 类型 | 需 windows-mcp | 判定 |
|---|---|---|---|---|
| TC-0 | 环境 HARD GATE（Dev python） | 启动日志 | 是（启动） | _待执行_ |
| TC-1 | BC 主路工具调用不破 | UI 真测 | 是 | _待执行_ |
| TC-2 | ★ WI-3 code 收尾自查清单 | UI 真测 | 是 | _待执行_ |
| TC-3 | WI-3 companion 无自查（BC 负向） | UI 真测 | 是 | _待执行_ |
| TC-4 | WI-2 trace on 生成 jsonl | UI 驱动+文件 | 是 | _待执行_ |
| TC-5 | WI-2 trace 完整性+并发不损坏 | UI 驱动+JSON | 是 | _待执行_ |
| TC-6 | WI-2 flag off 无 trace（BC） | UI 驱动+文件 | 是 | _待执行_ |
| TC-7 | WI-1 flag off 回归（BC） | UI 真测 | 是 | _待执行_ |
| TC-8 | WI-1 tier3/verify 强制收尾深层 | 自动化+旁证 | 否 | 自动化 9/9 ✅ |

---

## 10. 诚实说明：GUI 不可稳定直接触发的部分

- **TC-8（tier3 ≥30 轮 / verify 耗尽）**：单次 GUI 交互难稳定逼出 30 轮迭代或构造 verify ledger 失配，由 `test_wi1_tool_choice.py` 的对应单测覆盖（已 9/9 绿）；GUI 仅作 trace 旁证。其余 7 条（TC-1~TC-7）**全部走真机 windows-mcp 模拟点击+输入**，不降级。
- WI-3 收尾自查是 **persona 常驻**（非 tier3 门控），故 TC-2 可在普通短编码任务上真机观测——这是与早期误解的关键区别。

---

## 参考：核心代码位置速查

| 功能 | 文件:行 |
|---|---|
| WI-1 tier3 常数 | `agent_loop.py:97` |
| WI-1 三路径传 tool_choice | `agent_loop.py:935 / 1001 / 1078` |
| WI-1 verify 末轮收尾 | `agent_loop.py:1168, 1400-1414` |
| WI-1 flag 读取 | `main.py:977` |
| WI-2 flag 读取 + tracer 构造 | `main.py:978-990` |
| WI-2 trace 写入 | `backend/agent/trace.py` |
| WI-3 code persona 第 5 步 | `persona.py:64-68` |
| WI-3 default persona | `persona.py:23-30` |
| 自动化 | `backend/tests/test_wi1_tool_choice.py / test_wi2_trace.py / test_wi3_persona.py` |
