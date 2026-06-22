# 上下文 & Agent 优化 P1 手工测试用例（windows-mcp 真模拟人）

> **被测范围**: 方向二「对标系统优化」P1 三项已实现改动（点亮 / 接线类，非全新建）：
> - **WI-HM-1 自我纠错闭环全档点亮（shadow 默认开）** — `[tools.verifier]` 段出厂默认 `verify_gate_mode="shadow"` + `structured_reflection=True` + `emit_receipts=True`（VG-INVARIANT-1 硬连锁）。shadow = **观测不阻塞**，落 receipt。
> - **WI-OH-2 PROFILE 半衰期：`pref_decay` 默认开 + 对话式 Pin** — `pref_decay=True` 出厂；用户对话式「记住…」→ `memory_write(pinned=true)` → `set_pinned` → pinned 偏好跳衰减、重启后仍注入。
> - **WI-TG-1 goal_task_create 全局工具（需 goal_mode ON）** — `goal_mode` 维持默认 False；ON 时主 agent 全局注册 `goal_task_create/get/list/update` 四件套（`get_active_goal_context` 反查 active goal），DAG + 依赖持久化。
>
> **对应 Plan**: [`plans/2026-06-22-context-and-agent-optimization/02-reference-systems-optimization.md`](../../plans/2026-06-22-context-and-agent-optimization/02-reference-systems-optimization.md)（§3.2 HM-1 / §3.1 OH-2 / §3.5 TG-1）。
>
> **最后更新**: 2026-06-22（用例定义，待真机执行回填）

---

## 0. 被测改动摘要（读码核实，2026-06-22）

### WI-HM-1 — verify_gate shadow 全档默认开（已落地，改默认值，非字节 BC）

| 文件:行（符号定位优先） | 改前 | 改后（出厂默认） | 驱动的可见物 |
|---|---|---|---|
| `backend/config.py:278` `ToolsVerifierConfig.verify_gate_mode` | `"off"` | **`"shadow"`** | verify 守门介入（shadow=观测不阻塞） |
| `backend/config.py:277` `emit_receipts` | `False` | **`True`**（VG-INVARIANT-1 硬连锁：`verify_gate_mode!="off"` 必须 `emit_receipts=True`） | receipt 落盘 |
| `backend/config.py:291` `structured_reflection` | `False` | **`True`** | 校验不过回灌 5 段反思 JSON schema |
| `backend/config.py:280` `ephemeral_subagent_model` | — | `"haiku"`（缺省填充，白名单校验） | 第 3 次失败救援子代理模型 |

- **取值白名单**（`config.py:528 _VALID_VERIFY_GATE_MODES`）：**仅 `off | shadow | strict`**。写 `"on"`/`"ephemeral"` → 启动 `ConfigError: VG-INVARIANT-0`。
- **shadow 行为锚点**（`verify_gate.py:324-331`）：`if self.mode == "shadow":` → log `verify_gate shadow: N unmatched claims (would block in strict)` → `outcome.passed = True`（**shadow 总放行**，不阻塞 end_turn，只观测 + 落 receipt）。
- **闭环组件**（已实现，非本期新建）：`StructuredReflection`（5 段：error_analysis/execution_critique/task_replanning/next_action/confidence）+ `agent_loop.py:1456+` VerifyGate end_turn 守门 + `agent_loop.py:1550 consult_ephemeral_subagent`（真 async LLM 救援）+ `verify_exhausted` 终态。dead config（`ephemeral_subagent_model`）已于 commit `772c4291` 修复。
- **本期判定核心**：shadow ≠ strict —— **不拦截重试，而是「观测 + 落 receipt + warn log」**。表现是「桌宠照常完成，但 log 里有 verify_gate shadow 观测行 + receipt 落盘」，**不是**「拦下来重试」。

### WI-OH-2 — pref_decay 默认开 + 对话式 Pin（已落地）

- **衰减默认开**：`backend/config.py:203 pref_decay: bool = True`（原 WI-3.3 默认 False）。衰减逻辑 `facts.py daily_decay()`（`AND pinned=0` 跳 pinned 行）。各 category 半衰期：profile 永不衰减 / preference≈200d / goal≈200d。
- **对话式 Pin**：`backend/deskpet/tools/memory_tools.py` `memory_write(..., pinned=...)`（schema `:363` 有 `pinned` 字段，handler `:434` 读 `args["pinned"]`）→ pinned=True 时 `:455 await _facts_store.set_pinned(new_id, True)` → 返回 `{"ok":true,"pinned":true,...}`。
  - log 锚点（仅失败时）：`memory_write set_pinned(<id>) failed: ...`。**成功路径无独立 log**，判定看 memory_write 工具调用 + 返回 `pinned:true` + 重启后注入。
- **注入**：`assembler/components/preference_profile.py`（PreferenceProfileComponent，priority=85，📌 标记 Pin 置顶，读 preference/profile/constraint）。
- **forget 入口**：前端 `MemoryPanel.tsx`「事实」tab 已有 🗑 遗忘按钮（硬前置已满足，本期不重测）。

> ⚠️ **真机难点（诚实标注）**：真实「随天数衰减」无法在一次手测里等到（半衰期 ~200 天）→ 衰减本身靠 `test_pin_and_pref_decay.py` 单测覆盖，真机只验「pinned 偏好重启后仍注入」这条可观测链。

### WI-TG-1 — goal_task_create 全局工具（需 goal_mode ON，已落地）

- **flag**：`backend/config.py:424 goal_mode: bool = False`（**维持手动开启，与 HM-1/OH-2 不同，不全局默认开**）。section = `[features]`。
- **全局注册块**（`main.py:1541-1576`，仅 `goal_mode` ON）：`build_global_goal_task_tools(task_graph_store=..., goal_resolver=lambda: _session_goal_store.get_active_goal_context())` → 注册 4 工具到 toolset `goal` → log `goal_task_tools_registered_global count=4` + `companion_code_v1_goal_mode_ready`。
- **工具集**（`task_graph_tools.py`）：`goal_task_create`（:83，必填 `title`，可选 `depends_on[]` / `note`，DAG 防环）、`goal_task_get`（:119）、`goal_task_list`、`goal_task_update`。
- **active goal 反查**（实现坎解法 a）：handler 经 `goal_resolver` 调 `goal_store.py:207 get_active_goal_context()` 拿 active goal_id/session_id；无 active goal → 工具返错引导先 `/goal`。
- **持久化**：`TaskGraphStore`（DAG + `claim_ready` 原子认领）落 `session_db` 的 `goal_tasks` 表 + `session_goals`，重启后任务图 + 依赖仍在。
- **BC**：`goal_mode` OFF（出厂）→ `goal_tasks`/`session_goals` 表永不建（R-T5）；registry **无** `goal_task_create`（字节一致）。

---

## 1. 禁止的绕过方式（HARD CONSTRAINT — 违反即视为未完成）

> 见 `CLAUDE.md`「🔒 手工测试纪律」。本文档**所有标「UI 真测」的 case 必须真模拟人**：
> windows-mcp Screenshot/Snapshot 抓状态 → 真坐标 SetCursorPos+SendInput 点击 / Clipboard 粘贴+Ctrl+V → 截图验证 → grep tauri dev log 判定。

- ❌ **不允许** WebSocket 直连 backend 发 `memory_pin` / 注入 verify 事件 / 调 `goal_task_create` WS 当 UI 证据（协议层 ≠ 用户行为）。
- ❌ **不允许** `pytest test_goal_task_create_tool.py` / `import` backend 查 registry 含 `goal_task_create` / 查 `config.verify_gate_mode` 返回值当「点亮了」证据（代码加载 ≠ 用户链路触发）。
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
| **goal_mode 配置位置** ★ | TG-1 需在 `%APPDATA%\deskpet\config.toml` 加 `[features]` 段 `goal_mode = true` 并**重启**。section 名 = `[features]`，键名 = `goal_mode`（`config.py:424` 所在 `FeaturesConfig`）。 |
| **抓日志** | tauri dev 重定向 log（backend structlog 走 stderr → `Stdio::inherit()` → 落 tauri dev log）。grep 锚点见各 TC。 |
| **截图/日志存档** | 截图存 `testcase/2026-06-22-context-agent-opt-P1/screenshots/<case-id>.png`；log grep 片段贴进各 case「log 证据」栏。 |

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

# HM-1 shadow 观测介入（有 unmatched claim 时）
#   期望: verify_gate shadow: N unmatched claims (would block in strict)
# HM-1 救援 / 终态（shadow 一般不触发，仅 strict；留作旁证）
#   期望: ephemeral_rescued / verify_exhausted

# OH-2 pin 失败才有 log（成功看 memory_write 返回 pinned:true + 重启注入）
#   期望(失败): memory_write set_pinned(<id>) failed: ...
#   期望(注入): preference_profile_injected（重启后下轮 prompt 含 📌 偏好）

# TG-1 goal_mode ON 全局注册（每次带 goal_mode 启动）
#   期望: goal_task_tools_registered_global count=4
#   期望: companion_code_v1_goal_mode_ready
# TG-1 OFF（出厂）则上面两行均不出现，registry 无 goal_task_create
```

---

# 第一部分 — WI-HM-1 verify_gate shadow 全档默认开

> ⚙️ 出厂 `verify_gate_mode="shadow"` + `structured_reflection=True` + `emit_receipts=True`。
> **判定核心锚点**：shadow = **观测不阻塞**。有产物 + 有 claim 的目标 → log 出 `verify_gate shadow: N unmatched claims (would block in strict)` + receipt 落盘；**桌宠照常完成回合**（不拦截重试，那是 strict）。纯闲聊（无 claim）→ shadow 不应误阻塞。

---

## TC-HM1-1 — 有产物目标 → verify_gate shadow 观测介入 + receipt 落盘（★ 必过）

**类型**: UI 真测（真模拟人）+ 后端日志判定

**目的**: 给一个有产物的目标（生成 PPT / 研究写报告），验 shadow 模式守门**观测但不阻塞**：log 出现 `verify_gate shadow` 观测行 + receipt 落盘，且桌宠正常完成（shadow 总放行）。

**前置**: §2 全满足；启动日志确认 Dev python（非 Bundled exe）；onboarding 已登录；**出厂态即可**（shadow 默认开，无需改 config）。

| 步骤 | 动作（declare：坐标 / 动作 / 期望） | 期望结果 |
|---|---|---|
| 1 | Screenshot 抓桌宠主界面，记录对话输入框坐标 `(x,y)` | 截图 `screenshots/TC-HM1-1-step1.png` |
| 2 | `坐标=(x,y) \| 动作=Clipboard "帮我生成一个介绍宁德时代2024年经营情况的PPT" → Ctrl+V → Enter` | 桌宠调 `ppt_create` / `ppt_pro` 真生成产物，ArtifactCard 渲染 |
| 3 | 等回合收尾（end_turn），Screenshot 抓产物卡 | 截图 `screenshots/TC-HM1-1-step3.png`；桌宠**正常完成**（未被拦下反复重试） |
| 4 | grep tauri dev log `verify_gate shadow` | 出现 `verify_gate shadow: N unmatched claims (would block in strict)`（N≥0；有 claim 但 ledger 未匹配时 N>0），证明守门真介入观测 |
| 5 | grep tauri dev log receipt 相关（`emit_receipts=True` 下 verify gate 对账 receipt） | receipt 落盘记录存在（产物 sha / claim 对账），证明 emit_receipts 生效 |
| 6 | 确认整轮表现 = 观测 + 落 receipt，**不是**「拦截 → reflection → 重试」 | 桌宠一次完成，无 verify-nudge 反复回灌（shadow 总放行 `outcome.passed=True`） |

**可观测证据**:
- ✅ 点亮成立：log 有 `verify_gate shadow: N unmatched claims (would block in strict)` + receipt 落盘；桌宠正常完成（shadow 不阻塞）。
- ❌ 复发/异常：log 无任何 `verify_gate shadow`（守门没构造 → mode 没读到 shadow，或跑了旧 frozen），或桌宠被反复拦截重试（说明跑成了 strict 而非 shadow）。

**PASS 判据**: 步骤 4 有 `verify_gate shadow` 观测行 **且** 步骤 5 receipt 落盘 **且** 步骤 6 桌宠正常完成（未拦截重试）。
**FAIL 判据**: 无 shadow 观测行，或 shadow 误阻塞反复重试。

**判定**: _待真机回填_

---

## TC-HM1-2 — 故意首轮虚报完成 → shadow 观测到 unmatched claim（观测语义验证，best-effort）

**类型**: UI 真测 + 后端日志

**目的**: 引导桌宠先「口头宣称完成」但实际未产出对应产物 → shadow 应观测到 unmatched claim（`N>0`）并 log warn，但**仍放行**（shadow 不像 strict 那样回灌 reflection 重试）。验 shadow 的「观测语义」真生效。

**前置**: 同 TC-HM1-1。注意：shadow 模式下虚报**不会**触发 reflection 重试链（那是 strict）；本 TC 只验 shadow 能「看见」claim 与 ledger 不匹配。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | `坐标=(x,y) \| 动作=Clipboard "请只用一句话回复『PPT已经生成完毕』，先别真的调工具" → Ctrl+V → Enter` | 桌宠按引导说「PPT已经生成完毕」类完成声明，但未真调 ppt 工具（无 receipt 对账） |
| 2 | 等收尾，grep `verify_gate shadow` | 出现 `verify_gate shadow: N unmatched claims (would block in strict)`，**N≥1**（声明了完成但 ledger 无对应产物 receipt） |
| 3 | 观察桌宠是否被拦下重试 | **不应**重试（shadow `outcome.passed=True` 总放行）；回合正常结束 |
| 4 | Screenshot 抓最终对话 | 截图 `screenshots/TC-HM1-2-step4.png` |

**可观测证据**:
- ✅ shadow 观测到 `N≥1` unmatched claim 并 warn，但放行（不重试）→ 「观测不阻塞」语义成立。
- ❌ 无 shadow log（claim 没被识别 → claim_patterns 没命中，属已知边界），或被拦下重试（跑成 strict）。

**PASS 判据**: 步骤 2 出现 `N≥1` unmatched claim 观测行 **且** 步骤 3 未重试。
**FAIL/WARN 判据**: claim 未被识别（N=0 或无 log）→ 标 WARN（claim 模式未命中，非阻断）；若拦截重试 → FAIL（跑成 strict）。

> ⚠️ **难点诚实标注（best-effort）**：claim 是否被识别取决于 `claim_patterns.yaml` 与 LLM 实际措辞，引导措辞可能不命中。reflection 重试链由 strict 模式触发，shadow 默认开下不可直接真机观测「重试」——重试链由 `test_build_agent_verify_wiring.py` / `test_goal_loop_integration.py` 单测覆盖。本 TC 只验 shadow 观测语义。

---

## TC-HM1-3 — 纯闲聊（无 claim）→ shadow 不误阻塞（★ 必过 边界）

**类型**: UI 真测（真模拟人）

**目的**: 验全档默认开 shadow 后，**纯闲聊**（无完成声明、无产物 claim）不被误阻塞 end_turn，桌宠正常即时回复。这是 plan §3.2 风险条「全档开含陪伴档需确认无 claim 闲聊不卡」的真机验证。

**前置**: 同 TC-HM1-1，出厂态。建议新会话避免混入产物 claim。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | `坐标=(x,y) \| 动作=Clipboard "今天天气真不错，你喜欢什么颜色呀" → Ctrl+V → Enter` | 桌宠正常闲聊回复，秒级返回，无卡顿/无反复 |
| 2 | 再发 2~3 轮纯闲聊（「讲个笑话」「你今天心情怎么样」） | 每轮都正常即时回复，不被拦截 |
| 3 | grep tauri dev log，确认无 verify-nudge 反复回灌 / 无 end_turn 被阻塞 | 闲聊轮要么无 `verify_gate shadow`（无 claim），要么有但 `passed=True` 放行；无重试循环 |
| 4 | Screenshot 抓闲聊对话流 | 截图 `screenshots/TC-HM1-3-step4.png`；对话流畅 |

**可观测证据**:
- ✅ 闲聊正常放行，无卡顿、无重试循环 → shadow 全档开不伤陪伴体验。
- ❌ 闲聊被卡住 / 反复重试 / end_turn 迟迟不来 → shadow 误阻塞（需退陪伴档为更宽松或排查 claim 误识别）。

**PASS 判据**: 闲聊每轮正常即时回复，无阻塞/重试。
**FAIL 判据**: 任一闲聊轮被阻塞或反复重试。

**判定**: _待真机回填_

---

## TC-HM1-4 — 启动配置 invariant 守护（VG-INVARIANT，回归，best-effort）

**类型**: 配置改 + 启动日志（非 UI；守 VG-INVARIANT-1/0）

**目的**: 验硬连锁：`verify_gate_mode != "off"` 而 `emit_receipts=false` → 启动 `ConfigError: VG-INVARIANT-1`；`verify_gate_mode="on"`（非法值）→ `VG-INVARIANT-0`。证明点亮的三默认值是原子不可拆的。

**前置**: §2；改 `%APPDATA%\deskpet\config.toml` `[tools.verifier]` 段（Write 工具，无 BOM）。**验完务必还原默认（删这些行）。**

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | config.toml 加 `[tools.verifier]` 下 `verify_gate_mode = "shadow"` + `emit_receipts = false` → 重启 | 启动失败，log `ConfigError ... VG-INVARIANT-1: verify_gate_mode='shadow' requires emit_receipts=true` |
| 2 | 改 `verify_gate_mode = "on"`（非法值）→ 重启 | 启动失败，log `VG-INVARIANT-0: verify_gate_mode='on' must be one of ['off', 'shadow', 'strict']` |
| 3 | 删除上述行（还原出厂默认）→ 重启 | 正常启动，shadow 默认开 |

**可观测证据**:
- ✅ 两个非法组合均拒启动并报对应 invariant 错误码；还原后正常 → 硬连锁守住。
- ❌ 非法组合竟能启动（invariant 没守住），或还原后无法启动。

**PASS 判据**: 步骤 1/2 各报对应错误码，步骤 3 正常启动。
**FAIL 判据**: 任一非法组合通过启动，或还原后启动失败。

> ⚠️ **诚实标注（best-effort）**：本 TC 是配置 + 启动日志验证（非 windows-mcp UI 模拟），属 invariant 回归，列为 best-effort；不替代 UI 真测的核心 TC。

**判定**: _待真机回填_

---

# 第二部分 — WI-OH-2 pref_decay 默认开 + 对话式 Pin

> ⚙️ 出厂 `pref_decay=True`；用户对话式「记住…」→ `memory_write(pinned=true)` → pinned 偏好跳衰减、重启后仍注入 system prompt。
> **判定核心**：① memory_write 被调且 `pinned:true`；② 重启后下轮 prompt 含 📌 偏好（`preference_profile_injected`）。
> **真机难点**：真实「随天数衰减」无法一次手测等到（半衰期 ~200 天）→ 衰减靠单测；真机只验「pin 的偏好重启后仍注入」。

---

## TC-OH2-1 — 对话式「记住 X」→ memory_write(pinned=true) → 重启后仍注入（★ 必过）

**类型**: UI 真测（真模拟人）+ 后端日志 + 重启回归

**目的**: 验对话式 Pin 全链路：用户说「记住我喜欢用 neovim 编辑器，别忘了」→ LLM 调 `memory_write(pinned=true)` → 产生 pinned preference fact → **重启桌宠** → 下轮 prompt 注入该 📌 偏好。

**前置**: §2 全满足；出厂态（`pref_decay` 默认 True，无需改 config）。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | Screenshot 抓主界面，记录输入框坐标 `(x,y)` | 截图 `screenshots/TC-OH2-1-step1.png` |
| 2 | `坐标=(x,y) \| 动作=Clipboard "记住我喜欢用 neovim 编辑器，这个偏好别忘了，要一直记着" → Ctrl+V → Enter` | 桌宠确认已记住（措辞如「好的，我记住了你喜欢 neovim」） |
| 3 | grep tauri dev log，找本轮 `memory_write` 工具调用 + 返回 `pinned:true`（成功路径无独立 log，看工具调用 args 含 `pinned:true` + 返回 envelope `"pinned": true`） | memory_write 被调，pinned 参数为 true，返回 `pinned:true`（**无** `set_pinned failed` 行） |
| 4 | **重启桌宠**（taskkill deskpet.exe → 重新 `npx tauri dev` 带同 env），等启动 + onboarding 跳过 | 桌宠重新就绪 |
| 5 | `坐标=(x,y) \| 动作=Clipboard "我之前说的编辑器偏好你还记得吗" → Ctrl+V → Enter` | 桌宠答出 neovim（跨重启召回） |
| 6 | grep tauri dev log `preference_profile_injected` | 出现注入日志，且本轮组装 prompt 含 `📌 [preference]` neovim 偏好（PreferenceProfileComponent 注入） |
| 7 | Screenshot 抓步骤 5 的桌宠回复 | 截图 `screenshots/TC-OH2-1-step7.png`；答复含 neovim |

**可观测证据**:
- ✅ 链路成立：memory_write `pinned:true` → 重启后 `preference_profile_injected` + 桌宠答出 neovim。
- ❌ 复发：memory_write 未带 pinned（`pinned:false`），或重启后桌宠忘了偏好 / 无 `preference_profile_injected`（注入断链）。

**PASS 判据**: 步骤 3 `pinned:true` **且** 步骤 5 重启后桌宠答出 neovim **且** 步骤 6 有 `preference_profile_injected`。
**FAIL 判据**: pinned 未生效，或重启后失忆 / 无注入。

**判定**: _待真机回填_

> ⚠️ **LLM 行为标注**：LLM 是否把「记住 X」映射成 `memory_write(pinned=true)` 取决于工具描述与意图路由。若 LLM 调了 memory_write 但未带 pinned，记为「pin 意图未触发」边界（非阻断本期「pref_decay 默认开」核心），retry ≥3 次不同措辞（「钉住这个偏好」「永久记住」）后再判。

---

## TC-OH2-2 — pref_decay 默认开确认 + pinned 跳衰减（best-effort，靠单测旁证）

**类型**: 配置确认 + 单测旁证（真机仅验「pin 的偏好重启后仍在」）

**目的**: 确认 `pref_decay` 出厂默认开（非 pinned 偏好随时间衰减、pinned 跳衰减）。真机无法等真实天数衰减 → 衰减本身靠 `test_pin_and_pref_decay.py` 单测；真机只验 TC-OH2-1 已覆盖的「pinned 偏好重启后仍注入」。

**前置**: 同上。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认出厂 `config.py:203 pref_decay=True`（读码已核实，非改 config）；启动日志确认 Dev python | pref_decay 默认开态 |
| 2 | 真机层面：TC-OH2-1 已验「pinned 偏好重启后仍注入」= pinned 跳衰减的可观测代理 | 引用 TC-OH2-1 结果 |
| 3 | 衰减逻辑（非 pinned 随天数衰减）：标注靠 `backend/tests/test_pin_and_pref_decay.py` 单测覆盖（真机不可等 ~200 天半衰期） | 单测旁证（运行 `test_pin_and_pref_decay.py` 全绿 = 衰减 + pin 跳衰减逻辑正确） |

**可观测证据**:
- ✅ pref_decay 默认 True（读码确认）；pinned 偏好重启后仍注入（TC-OH2-1）；衰减逻辑单测全绿。
- ❌ pref_decay 默认仍 False，或 pinned 偏好重启后丢失。

**PASS 判据**: pref_decay 默认 True 确认 + TC-OH2-1 PASS。
**FAIL 判据**: 默认未开，或 pinned 偏好重启丢失。

> ⚠️ **诚实标注（best-effort / 单测覆盖）**：真实时间衰减无法真机验证（半衰期 ~200 天），明确标为单测覆盖项。真机可观测的只有「pin 重启后仍注入」（已由 TC-OH2-1 覆盖）。

**判定**: _待真机回填（衰减部分标 best-effort/靠单测）_

---

# 第三部分 — WI-TG-1 goal_task_create 全局工具（需 goal_mode ON）

> ⚙️ `goal_mode` 维持默认 False。需在 `%APPDATA%\deskpet\config.toml` 加 `[features]` 段 `goal_mode = true` 并**重启**。ON 时主 agent 全局注册 `goal_task_create/get/list/update` 四件套（`get_active_goal_context` 反查 active goal），DAG + 依赖持久化。
>
> **关键前置（必读）**：
> 1. **开 flag**：`%APPDATA%\deskpet\config.toml` 的 `[features]` 段加 `goal_mode = true`（Write 工具，无 BOM），**重启桌宠**。启动日志须出现 `goal_task_tools_registered_global count=4` + `companion_code_v1_goal_mode_ready`。
> 2. goal_task_create 需**先有 active goal**（`/goal <text>` 设过）—— `get_active_goal_context()` 反查；无 active goal 时工具返错引导先 `/goal`。
> 3. **验完按需还原** `goal_mode` 默认（删行）。

---

## TC-TG1-1 — goal_mode ON → 设长目标 → goal_task_create 建带依赖任务 → list 查到（★ 必过）

**类型**: UI 真测（真模拟人）+ 后端日志

**目的**: 验 goal_mode ON 后主 agent 能用 `goal_task_create` 把长目标拆成带依赖的任务，`goal_task_list` 查到往返。

**前置**: §2 全满足；`[features] goal_mode = true` 已加并重启；启动日志确认 `goal_task_tools_registered_global count=4` + Dev python（非 frozen）。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | grep 启动 log `goal_task_tools_registered_global count=4` | 出现该行（四件套全局注册成功）；截图 `screenshots/TC-TG1-1-step1.png`（log 截图） |
| 2 | `坐标=(x,y) \| 动作=Clipboard "/goal 完成一份宁德时代2024年的深度分析报告" → Ctrl+V → Enter` | 设 active goal 成功（`session_goals` 落库） |
| 3 | `坐标=(x,y) \| 动作=Clipboard "把这个目标拆成几个带依赖的任务：先收集财报数据，然后分析经营，最后写报告，写报告依赖前两步" → Ctrl+V → Enter` | 桌宠调 `goal_task_create` 建 3 个任务（「写报告」`depends_on` 前两个 task_id） |
| 4 | grep tauri dev log，确认 `goal_task_create` 工具被调 ≥3 次，含 `depends_on` 边 | create 调用 + DAG 边建立（无 `goal_task_create failed` / cycle 错） |
| 5 | `坐标=(x,y) \| 动作=Clipboard "列一下当前目标的任务和它们的依赖" → Ctrl+V → Enter` | 桌宠调 `goal_task_list` 返回 3 个任务 + 依赖关系，文案列出 |
| 6 | Screenshot 抓任务列表回复 | 截图 `screenshots/TC-TG1-1-step6.png`；含 3 任务 + 「写报告依赖前两步」 |

**可观测证据**:
- ✅ 链路成立：log 有注册行 + create ≥3 次（含 depends_on）+ list 返回带依赖的任务图。
- ❌ 复发：无注册行（goal_mode 没读到 / 跑 frozen），或 create 报错 / 无 depends_on，或 list 查不到。

**PASS 判据**: 步骤 1 有注册行 **且** 步骤 4 create 含 depends_on **且** 步骤 5 list 返回带依赖任务图。
**FAIL 判据**: 无注册行，或 create/list 往返失败。

**判定**: _待真机回填_

---

## TC-TG1-2 — 重启后任务图 + 依赖仍在（持久化，★ 必过）

**类型**: UI 真测 + 重启回归 + 后端日志

**目的**: 验 TaskGraphStore 落 `goal_tasks` 表的持久化：建完任务图后**重启桌宠**，任务 + 依赖仍可 `goal_task_list` 查到。

**前置**: 接 TC-TG1-1（已建带依赖的 3 任务）；`goal_mode` 仍 ON。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | **重启桌宠**（taskkill → 重新 `npx tauri dev` 带同 env + goal_mode ON），等就绪 | 桌宠重新就绪；启动 log 再现 `goal_task_tools_registered_global count=4` |
| 2 | `坐标=(x,y) \| 动作=Clipboard "继续上次的目标，列一下任务和依赖" → Ctrl+V → Enter` | 桌宠调 `goal_task_list`，返回重启前建的 3 个任务 + 依赖边 |
| 3 | grep tauri dev log `goal_task_list`，确认返回 3 任务（非空，非重建） | 任务图从 `goal_tasks` 表 load 出来，依赖边完整 |
| 4 | Screenshot 抓重启后的任务列表 | 截图 `screenshots/TC-TG1-2-step4.png`；3 任务 + 依赖仍在 |

**可观测证据**:
- ✅ 持久化成立：重启后 list 仍返回完整任务图 + 依赖。
- ❌ 复发：重启后任务图空 / 依赖丢失（落库没生效，或读了内存态没持久镜像）。

**PASS 判据**: 步骤 2/3 重启后 list 返回完整 3 任务 + 依赖边。
**FAIL 判据**: 重启后任务图空或依赖丢失。

**判定**: _待真机回填_

---

## TC-TG1-3 — goal_mode OFF（出厂）→ registry 无 goal_task_create（★ 必过 BC）

**类型**: UI 真测 + 后端日志（字节级 BC 验收）

**目的**: 验出厂态（`goal_mode` 不写 / =false）下 `goal_task_create` **不注册**：无 `goal_task_tools_registered_global` 注册行；桌宠对「拆任务」请求不会调到 goal_task_create（registry 无此工具）。

**前置**: §2 全满足；**确保 `%APPDATA%\deskpet\config.toml` 未开 `goal_mode`（出厂 false）**；已重启。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认 config 无 `goal_mode=true`（默认 OFF）→ 重启；grep 启动 log | **无** `goal_task_tools_registered_global count=4`、**无** `companion_code_v1_goal_mode_ready`（goal_mode 块整体不进） |
| 2 | `坐标=(x,y) \| 动作=Clipboard "帮我把『写年度报告』这个目标拆成带依赖的任务" → Ctrl+V → Enter` | 桌宠正常回复（可能用 todo / 文字列任务），但**不会**调 `goal_task_create`（工具不在 registry） |
| 3 | grep tauri dev log，确认无 `goal_task_create` 工具调用 | 无 goal_task_create 调用（registry 无此工具名 = 字节一致） |
| 4 | Screenshot 抓回复 | 截图 `screenshots/TC-TG1-3-step4.png` |

**可观测证据**:
- ✅ BC 成立：出厂态无注册行、无 goal_task_create 调用 → goal_mode OFF 字节一致。
- ❌ BC 破坏：出厂态竟注册了 goal_task 工具 / 调到了 goal_task_create（goal_mode 默认翻了 ON）。

**PASS 判据**: 步骤 1 无注册行 **且** 步骤 3 无 goal_task_create 调用。
**FAIL 判据**: 出厂态出现注册行或 goal_task_create 调用。

**判定**: _待真机回填_

---

## TC-TG1-4 — 无 active goal 时 goal_task_create 引导先 /goal（边界，best-effort）

**类型**: UI 真测 + 后端日志（边界）

**目的**: 验 `get_active_goal_context()` 反查为空时（goal_mode ON 但未 `/goal`），goal_task_create 返错引导用户先设目标，而非崩溃 / 建孤儿任务。

**前置**: `goal_mode = true` 并重启；**未设任何 active goal**（新会话，未 `/goal`）。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认启动 log 有注册行；未 `/goal` 过 | goal_mode ON、无 active goal |
| 2 | `坐标=(x,y) \| 动作=Clipboard "给当前目标加一个任务：收集数据" → Ctrl+V → Enter` | 桌宠尝试 goal_task_create → 工具返错引导（如「请先用 /goal 设置一个目标」） |
| 3 | grep tauri dev log，确认 goal_task_create 返回 error（无 active goal）且未建孤儿任务、未崩溃 | 工具优雅返错（非异常栈） |
| 4 | Screenshot 抓引导文案 | 截图 `screenshots/TC-TG1-4-step4.png` |

**可观测证据**:
- ✅ 无 active goal 时 goal_task_create 优雅返错引导先 /goal，不崩、不建孤儿。
- ❌ 崩溃 / 建出无主孤儿任务 / 无任何提示。

**PASS 判据**: 步骤 2 引导先设目标 **且** 步骤 3 未崩溃/未建孤儿。
**FAIL 判据**: 崩溃或建孤儿任务。

> ⚠️ **诚实标注（best-effort）**：是否触发 goal_task_create 取决于 LLM 在无 goal 时是否仍尝试调工具（也可能直接文字回复不调工具）。若 LLM 不调工具，记「LLM 未触发工具」边界，retry ≥3 次不同措辞后判。

**判定**: _待真机回填_

---

## 4. 用例数统计 + 必过 / best-effort / env-limited 分级

| 分组 | 用例 | 类型 | 等级 |
|---|---|---|---|
| **HM-1** | TC-HM1-1 有产物目标 shadow 观测 + receipt | UI 真测 + log | **★ 必过** |
| HM-1 | TC-HM1-2 虚报完成 shadow 观测 unmatched | UI 真测 + log | best-effort（claim 命中靠模式 + LLM 措辞） |
| **HM-1** | TC-HM1-3 纯闲聊 shadow 不误阻塞 | UI 真测 | **★ 必过（边界）** |
| HM-1 | TC-HM1-4 启动 invariant 守护（VG-INVARIANT-0/1） | 配置 + 启动 log | best-effort（非 UI，回归） |
| **OH-2** | TC-OH2-1 对话 Pin → 重启后仍注入 | UI 真测 + log + 重启 | **★ 必过** |
| OH-2 | TC-OH2-2 pref_decay 默认开 + pin 跳衰减 | 配置确认 + 单测旁证 | best-effort（真实衰减靠单测，半衰期 ~200 天） |
| **TG-1** | TC-TG1-1 goal_mode ON → create 带依赖 → list | UI 真测 + log | **★ 必过** |
| **TG-1** | TC-TG1-2 重启后任务图 + 依赖仍在 | UI 真测 + 重启 + log | **★ 必过（持久化）** |
| **TG-1** | TC-TG1-3 goal_mode OFF registry 无 create（★BC） | UI 真测 + log | **★ 必过（BC）** |
| TG-1 | TC-TG1-4 无 active goal 引导先 /goal | UI 真测 + log | best-effort（LLM 是否触发工具） |

- **总计 10 个 TC**（HM-1：4 个；OH-2：2 个；TG-1：4 个）。
- **★ 必过（6 个）**：TC-HM1-1、TC-HM1-3、TC-OH2-1、TC-TG1-1、TC-TG1-2、TC-TG1-3。
- **best-effort（4 个）**：TC-HM1-2（claim 命中靠模式 + LLM 措辞）、TC-HM1-4（非 UI 配置回归）、TC-OH2-2（真实衰减靠单测）、TC-TG1-4（LLM 是否触发工具）。
- **最易暴露 bug 的边界**：
  - **TC-HM1-3（纯闲聊 shadow 不误阻塞）** —— 全档默认开 shadow 后最大风险是「陪伴档闲聊被守门误卡」，这条直接验 plan §3.2 风险条；若 shadow 对无 claim 闲聊误阻塞，端到端会暴露为「桌宠闲聊卡顿/不回」。
  - **TC-TG1-3（goal_mode OFF BC）** —— 验「goal_mode 没被误翻默认开」，与 HM-1/OH-2 默认翻 ON 区分；若 TG-1 也被误默认开会破坏字节基线。
- **真机难触发需 best-effort**：
  - **真实时间衰减**（OH-2）：半衰期 ~200 天，一次手测等不到 → 靠 `test_pin_and_pref_decay.py` 单测；真机只验「pin 重启后仍注入」。
  - **shadow 的 reflection 重试链**：重试由 strict 模式触发，shadow 默认开下真机看不到「拦截重试」（shadow 总放行）→ 重试链靠 `test_build_agent_verify_wiring.py` 等单测覆盖；真机只验 shadow 观测语义。
  - **claim 识别命中**（HM-1）/ **LLM 工具触发**（pin、goal_task_create）：取决于 claim_patterns 与 LLM 实际措辞 → retry ≥3 次不同措辞后才可标环境受限并等用户确认。

---

## 5. 结果汇总（待真机执行回填）

| Case | 范围 | 类型 | 等级 | 判定 |
|---|---|---|---|---|
| TC-HM1-1 | 有产物目标 shadow 观测 + receipt | UI 真测 + log | ★ | _待回填_ |
| TC-HM1-2 | 虚报完成 shadow 观测 unmatched | UI 真测 + log | best-effort | _待回填_ |
| TC-HM1-3 | 纯闲聊 shadow 不误阻塞 | UI 真测 | ★ | _待回填_ |
| TC-HM1-4 | 启动 invariant 守护 | 配置 + 启动 log | best-effort | _待回填_ |
| TC-OH2-1 | 对话 Pin → 重启后仍注入 | UI 真测 + log + 重启 | ★ | _待回填_ |
| TC-OH2-2 | pref_decay 默认开 + pin 跳衰减 | 配置 + 单测旁证 | best-effort | _待回填（衰减靠单测）_ |
| TC-TG1-1 | goal_mode ON create 带依赖 + list | UI 真测 + log | ★ | _待回填_ |
| TC-TG1-2 | 重启后任务图 + 依赖仍在 | UI 真测 + 重启 + log | ★ | _待回填_ |
| TC-TG1-3 | goal_mode OFF registry 无 create（★BC） | UI 真测 + log | ★ | _待回填_ |
| TC-TG1-4 | 无 active goal 引导先 /goal | UI 真测 + log | best-effort | _待回填_ |

> **恢复环境（验完必做）**: 删 `%APPDATA%\deskpet\config.toml` 中临时加的 `[features] goal_mode = true` 行（还原默认 False）+ TC-HM1-4 临时改的 `[tools.verifier]` 行；HM-1/OH-2 出厂默认（shadow / pref_decay True）无需还原。
> **结果存档**: 截图存 `testcase/2026-06-22-context-agent-opt-P1/screenshots/`；执行后日志证据贴回各 case「log 证据」栏与本汇总表。
