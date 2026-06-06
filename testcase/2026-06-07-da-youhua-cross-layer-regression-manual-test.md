# 手工测试用例 — 大优化计划「跨层契约漂移」防复发回归（2026-06-07）

> **文档定位**：这是一份**回归测试**文档，专门覆盖 **goal-completion「大优化计划」会话中真机手测挖出并修复的 16 处跨层 bug + 10 处加固/polish + 9 类边界/环境情况（含 1 个真机新发现的已知未修友好性 bug）**。
> 与同目录 [`goal-completion-manual-test.md`](./goal-completion-manual-test.md) **互补**：
> - 那份是**功能级 FP 验收**（FP-3/4/5 三个功能点的手测门，证明「功能做对了」）。
> - **这份是「防复发回归」**——每条 TC 都对应一个**已修的真 bug**，给出「复发会怎样（坏结果）」vs「修复后应有（好结果）」的**可判定对比**，确保这些系统性死链将来不再静默回归。
>
> **核心主题**：本会话挖出的全部 bug 都是同一种病——**「组件注册 + flag 开 + 480 单测全绿，但 venue / policy / config / 类型契约 / 时序层逐个断」**，即 `feedback_cross_layer_contract` 的最深演绎。**单测全绿掩盖了生产死链**（sync mock embedder + 注入齐全 + flag on 的单测，跑不到生产真机的 async embedder / 缺 policy / 漏 config 段）。FP-5 自动披露 + FP-4 偏好注入原本在生产里**完全死掉**。因此本文 TC **绝大多数必须 windows-mcp 真机或后端 boot log/DB 核对**，禁止用 import / 脚本回放当证据。

> **被测 commit（HEAD）**：`c27b791`（含全 16 修复：`7732c0d` config[skills.codify] / `58fbac8` 5 处接线 / `4f39e3a` relay 鲁棒性 / `1f63c75` ephemeral-card / `c31d261` 方案 B codify helper / `56ba381` 6 层 auto-disclosure / `c64fb81` preference_profile policy / `c739a33` 语音 venue 配置 / `d4ee7d6` 深合并 / `c0bf85d` 语音 build_agent / `ce29833`/`ce9245f` v2_enabled+fire-and-forget polish）on `master`（`G:\projects\deskpet`）。
> **证据来源**：[`plans/manual-results-2026-06-06-FP345/RESULTS.md`](../plans/manual-results-2026-06-06-FP345/RESULTS.md)（本会话完整逐层定位/修复/真机证据）。
> **最后更新**：2026-06-07

### 与 `goal-completion-manual-test.md` 的整合（交叉引用）

本文档与 [`goal-completion-manual-test.md`](./goal-completion-manual-test.md) **互补、不重叠**，两者组织维度不同：

- **本文档**（`2026-06-07-...cross-layer-regression`）= **防本会话挖出的跨层 bug 复发**，**按 bug / commit 组织**（R-1~R-16 一一对应一个已修真 bug + B-1~B-9 边界）。每条 TC 关心「这个 bug 复发会怎样」。
- **`goal-completion-manual-test.md`** = **FP-3/4/5 功能验收**，**按功能点组织**（TC-3.x 完成判定 / TC-4.x 记忆偏好 / TC-5.x 技能）。每条 TC 关心「这个功能做对了没」。

**为什么需要两份**：本会话的 bug 全是「单测全绿但生产死链」——FP 功能在生产里**原本完全死掉**（FP-5 自动披露 / FP-4 偏好注入）。这些跨层 bug 修复后，FP 功能 TC 才**真的能在真机上 PASS**。本文档存在的意义就是**保障那些已 PASS 的功能 TC 不因跨层 bug 复发而再次「生产死掉」**。

**正面行为 ↔ 功能 TC ↔ 本文档保障 bug-TC 映射表**（本会话这些功能 TC 均已真机 PASS，证据见 [`plans/manual-results-2026-06-06-FP345/RESULTS.md`](../plans/manual-results-2026-06-06-FP345/RESULTS.md) + `screenshots/`）：

| 本会话已真机 PASS 的正面行为 | 对应功能 TC（goal-completion） | 本文档保障它的 bug-TC | 真机证据截图 |
|---|---|---|---|
| 强匹配 skill 正文自动载入 / 复用 | TC-5.1、TC-5.8 | **R-7 / R-8 / R-9 / R-10 / R-11**（自动披露 6 层链，R-11 async embedder 是命门） | `tc-5.1-auto-disclosure.png` |
| 技能自创全链（propose→落盘→召回） | TC-5.3 | **R-1 / R-2 / R-5 / R-10**（config 解析 + 接线 + 双触发 + SKILL.md task_types） | `tc-5.3-skill-candidate-proposed.png`、`tc-5.3-skill-card-rendered.png` |
| 跨会话召回（决策/约束） / 偏好冲突 replace | TC-4.1、TC-4.3 | **R-12**（preference_profile 注入）+ FP-4 facts 链（B-10 双写钩） | `tc-4.1-cross-session-recall.png`、`tc-4.3-preference-conflict.png` |
| 真完成放行不误杀 / 未来时态不误判 | TC-3.2、TC-3.4 | **verify gate**（含 **R-15** 语音 venue 也接 verify，文字 venue 接线见 FP-3 链） | `tc-3.2-real-completion-passed.png`、`tc-3.4-future-tense-no-false-flag.png` |

> 读法：跑本文档某条 bug-TC FAIL（复发） → 立即去对照表右侧找受影响的功能 TC，确认是否随之「生产死掉」；反之跑功能 TC 时如发现行为退化，回查本表左列定位是哪个跨层 bug 复发了。

---

## 0. 测试前置准备

### 0.1 启动纪律（CLAUDE.md 踩坑 #7/#8/#9 — 不可违反）

> ⚠️ **不要**手动起 backend（`python main.py`），也**不要**手动起第二个 vite。只给 **Tauri 进程**注入 env，让 Tauri 自己 spawn + 管理 backend + 自带 vite。
> 跑源码代码**必须**设 `DESKPET_BACKEND_DIR`，否则跑的是旧 frozen exe（测了白测）。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| P-0 | 确认在被测 commit：`git rev-parse --short HEAD` | 输出 `c27b791`（或其后续）；`git branch --show-current` = `master` |
| P-1 | 杀残留进程（防 8100 被 orphan 占）：`taskkill /F /IM deskpet.exe`（忽略 not found）+ 杀残留 vite/backend python | 端口 8100 / vite 端口空闲 |
| P-2 | 设全 FP flag（**注入 Tauri 进程 env**，不改 tracked config.toml）：<br>`DESKPET_CONFIG=G:\projects\deskpet\.tmp\fp1-config.toml`（须含 `[skills.codify] enabled=true` + `[skills.auto_disclosure] enabled=true` + `[memory.v2] persona_inject=true` + `goal_facts=true`）<br>`DESKPET_BACKEND_DIR=G:\projects\deskpet\backend`<br>`DESKPET_PYTHON=G:\projects\deskpet\backend\.venv\Scripts\python.exe`<br>`DESKPET_USER_DATA_DIR=<本次测试独立目录>` | — |
| P-3 | 启动 Tauri：`npx tauri dev`（让它自管 backend+vite） | 桌宠窗口出现 |
| P-4 | **确认跑源码非 frozen**：看 tauri dev 终端 log | 出现 `[backend_launch] Dev python=...backend\.venv... backend_dir=G:\projects\deskpet\backend`（**不是** `Bundled exe=...`） |
| P-5 | **确认全 FP flag + 接线点亮**（boot log grep，**本文回归的命门**）：<br>`companion_code_v1_goal_mode_ready`（goal_mode）<br>`verify_gate_init mode=strict`（FP-3）<br>`wi4_0_compaction_enabled ... threshold=0.75`（FP-5 4.0）<br>**`fp5_codify_wiring_ready tool_path=True candidate_store=True llm=True`**（R-1 命门）<br>**`fp5_auto_disclosure_wiring_ready matcher=True`**（R-2 命门）<br>`b10_goal_facts_hook_bound`（FP-4 B-10）<br>`permission_auto_mode_restored enabled=True`（write_file 门，见 0.3） | 全部出现，且**无 ValueError / Traceback**（服务未注册会 boot 即崩） |
| P-6 | **登录态**（agent loop / verify / 技能自创 / 偏好抽取都需真 LLM）：走 onboarding 登录测试账号（凭据从 gitignored `LOCAL-DEV-CREDENTIALS.md` 读）→ 等 relay 下发 key 写 keychain → 关 onboarding 窗 | backend 自动从 keychain 读 key；`GET https://<relay>/v1/models 200` + `model_context_resolved` |
| P-7 | 定位落盘路径：<br>· backend log = tauri dev 终端 stderr（抓时 `tr -d '\000' < <log> | grep`）<br>· state DB = `<user_data>/state.db`（facts / session_goals / pending_skill_candidates / messages）<br>· 自创 SKILL.md = `<user_data>/skills/user/<slug>/SKILL.md`<br>· artifacts = `<user_data>/artifacts/<YYYY-MM-DD>/<tool>/` | 路径可访问 |
| P-8 | **进入 Code 模式面板**：点桌宠 toolbar code-mode 图标 → 新建/打开一个 code project → 用 InputBar 发指令 | 本文绝大多数 TC 走 **code 会话**（FP-5 披露 + FP-4 注入 bug 正是在 code 会话暴露的） |

### 0.2 windows-mcp 圣杯（引 RESULTS.md「圣杯突破」节，每条 ✅ TC 复用）

- **WebView2/Chromium 鼠标 + 键盘都必须 `SendInput`**（INPUT type=0 鼠标 0x0002/0x0004；type=1 键盘 0x11 Ctrl/0x56 V/0x0D Enter）——老 `mouse_event`/`keybd_event` 被 WebView2 忽略。
- content 按钮点击前必须 `SetForegroundWindow`+`BringWindowToTop` 激活 Code 窗。
- 中文输入：`Set-Clipboard`（或 STA `Clipboard::SetText`）+ SendInput Ctrl+V。
- **输入框物理坐标随窗口布局/对话增长变化** → 每次发送前 Snapshot/截图重取坐标。
- 验证：`state.db` 查询 + `tr -d '\000' < <tauri.log> | grep <事件>` + 截图存 `plans/manual-results-<date>/screenshots/`。

### 0.3 write_file 权限门 workaround（环境，非被测特性）

写文件任务会弹「权限请求 写入文件」对话框，overlay 点击易超时。等价用户点「本会话始终允许」：写 `<user_data>/permissions_auto_mode.json = {"enabled":true}` → 重启 → boot `permission_auto_mode_restored enabled=True` → write_file 自动放行（见 B-2）。

### 0.4 三类分级（每条 TC 标注）

- **✅真机 windows-mcp**：截图抓状态 → 真坐标点击/真输入 → 截图 + log/DB 判定。「用户真用得了」的证据。
- **🟡 后端 log + DB 核对**：真机驱动 + boot log / state.db 落盘核对为主（内部接线/时序/抽取，纯点击观测不到）。**禁止** import/脚本回放替代真机运行栈。
- **🔵 纯 flag-OFF 字节核对**：关 flag 后核对「行为与旧版字节级一致」（desc-only / 不接线 / 空 Slice）。可用配置切换 + 后端核对。

### 0.5 判定约定

每条 TC 末尾 `PASS / FAIL / RETRY-N / SKIP（带理由）`。✅类失败必须 retry ≥3 次不同 workaround 才能标 SKIP（需用户确认）。每个 windows-mcp 动作前先 declare：`坐标=(x,y) | 动作=click/type | 期望=...`。
**回归判据**：每条 TC 给出**复发坏结果**与**修复后好结果**的对比；只要观测到坏结果即 FAIL（bug 回归）。

---

# A 组 — 16 处跨层 bug 防复发回归

> 每条 R-x 对应 bug 清单一项。**命门**：很多 bug 的「修复后好结果」是**一行 boot log 或一个 DB 行**——复发时该证据消失（坏结果）。

## R-1 — config 漏解析 `[skills.codify]` → 技能自创整功能生产静默死（🟡 boot log）★

**对应 bug**：#1（commit `7732c0d`）。`config.py:load_config` 原只 pop `auto_disclosure` 子表、**从不解析 `codify`** → `[skills.codify] enabled=true` 被丢弃 → `config.skills.codify.enabled` 恒 False → lifespan codify 块跳过 → `tool_path_recorder`/`skill_candidate_store`/`llm_registry` 全注册 None → codify hook 短路 → 技能自创确认卡**生产永不弹**。

**前置/触发**：P-2 配置含 `[skills.codify] enabled = true` → P-3 启动。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | boot 后 grep tauri log `fp5_codify_wiring_ready` | **出现 `fp5_codify_wiring_ready tool_path=True candidate_store=True llm=True`** |
| 2 | grep boot log Traceback / ValueError | 无（三 service 正确注册） |

**判定**：✅ 好结果 = boot log 出现 `fp5_codify_wiring_ready ...True,True,True`。
❌ **复发坏结果** = boot log **缺** `fp5_codify_wiring_ready`（config 又漏解析 `[skills.codify]` → enabled 恒 False → 接线块整段跳过）→ 后续 R-9 技能自创卡永不弹。
**分级**：🟡（boot log 核对；config 解析正确性由代码侧守护，真机 boot 是生产证据）。

---

## R-2 — 5 处跨层接线断裂（services 注册 / recorder 构造+喂数据 / build_agent 传参）（🟡 boot log + DB）★

**对应 bug**：#2（commit `58fbac8`）。原断裂：(a) codify 依赖的 `tool_path_recorder`/`skill_candidate_store`/`llm_registry` services 未注册 → codify hook `get()` 抛 ValueError；(b) `ToolPathRecorder` 从未构造 + 从未喂数据；(c) `build_agent` 不传 skill_loader+matcher+recorder。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | boot grep `fp5_auto_disclosure_wiring_ready` | **出现 `fp5_auto_disclosure_wiring_ready matcher=True`**（SkillMatcher 构造成功） |
| 2 | boot grep Traceback `Unknown service` / ValueError | 无（若 service 仍未注册，codify hook 的 `get()` 会 boot 即 ValueError） |
| 3 | code 会话发一个 **≥3 不同工具**的任务（如「列目录→读 README→再读一个文件→总结」），等自然完成 | 任务跑通；`record_tool(session_id=_sid)` 与 codify hook `complete(_sid)` 用**同一 session_id**（无 mismatch）；DB `pending_skill_candidates` 在达触发条件后可建行（见 R-9） |

**判定**：✅ 好结果 = boot 干净 + `fp5_auto_disclosure_wiring_ready matcher=True` + recorder 真喂到数据（R-9 能产候选）。
❌ **复发坏结果** = boot ValueError（service 未注册）或 recorder 从不构造 → R-9 候选永不生成。
**分级**：🟡（boot log + recorder→candidate DB 落盘核对）。

---

## R-3 — relay ReadError 鲁棒性：连接级瞬时错误重试，agent 不立即崩（🟡 后端 log）★

**对应 bug**：#3（commit `4f39e3a`）。中转 relay 经代理间歇掉**流式连接** → httpx `ReadError`（name 不含 Timeout）→ 之前落 `LLMProviderError` 不重试 → agent turn 立即崩，跑不到 ≥5 工具。3 处修复：(1) `openai_adapter._map_error` 把 ReadError/ConnectError/RemoteProtocolError → `LLMTimeoutError`（可重试）；(2) `registry.chat_with_fallback` timeout/conn-drop **重试同 provider**（backoff，非直接切下一 provider）；(3) `tool_use_shim.chat_with_fallback_stream` 流式路径**产出任何事件前掉链 → 干净重试整个流**（3 次 backoff），已产出则不重试。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | code 会话发一个 5 步多工具任务（列目录+读 README+再列+再读+总结），耐心等自然完成（relay 慢） | session 状态从可能的「⚠ error」**恢复到 running 并持续推进**；agent **完成全部工具**（如 TODOS 3/3） |
| 2 | grep log `timeout/conn-drop ... retrying same provider` / 流式重试 | 出现重试日志（连接掉链被重试，而非直接报错切 provider 或崩溃） |

**判定**：✅ 好结果 = relay 间歇 ReadError 下 agent **仍能完成全部工具**（流式重试救活掉连接）。
❌ **复发坏结果** = 首次 ReadError 即 `LLMProviderError` 不重试 → agent turn 立即崩（session「⚠ error」，0~2 工具即停）。
**分级**：🟡（relay 故障是外部间歇性，以「agent 完成工具 + 重试 log」为主证据；relay 稳定窗口可能观测不到重试触发，此时核对修复后 agent 至少不因单次 ReadError 崩）。

---

## R-4 — 前端 ephemeral-card：skill_candidate 卡 message reload 时丢失（✅真机 windows-mcp）★

**对应 bug**：#4（commit `1f63c75`）。`skill_candidate`/`plan` 确认卡是 **ephemeral 前端-only 消息**（未持久化到 DB）。打开「完整 chat」触发 `session_messages_load` → `set_messages` 从 DB **整体替换** messages → awaiting 的技能卡被丢弃。修法：`set_messages` 重载时**保留内存里仍 awaiting 的 skill_candidate/plan 卡**（merge 不 replace，见 `sessionsStore.ts:311-324`）。

**前置**：先触发一张 skill_candidate 候选卡（走 R-9 步骤 1-3，DB `pending_skill_candidates` 有 awaiting 行 + 前端绿色「✨ 新技能」卡已渲染）。

| 步骤 | 动作（声明式） | 可观测预期 |
|---|---|---|
| 1 | 截图当前态：绿色「✨ 新技能 · <name>」卡在对话区 | 卡可见（awaiting） |
| 2 | `坐标=(打开完整 chat 入口) | 动作=SendInput click | 期望=触发 session_messages_load` → 等 chat 视图重载 | — |
| 3 | 截图重载后 chat 视图 | **技能卡仍在**（merge 保留 awaiting 卡，未被 DB 快照替换掉） |

**判定**：✅ 好结果 = 打开完整 chat 重载后**技能卡仍存活**（可继续点保存/忽略）。
❌ **复发坏结果** = 打开完整 chat 后技能卡**消失**（set_messages 整体替换丢卡）→ 无法点保存。
**分级**：✅真机 windows-mcp（前端显示层 bug，必须真机打开 chat 视图观测卡存活；不可用 store 单测替代）。
**本会话真机证据指针**：本会话已真机 PASS（技能卡 reload 存活）——证据见 `plans/manual-results-2026-06-06-FP345/screenshots/tc-5.3-skill-card-rendered.png`（候选卡渲染存活态，TC-5.3 全链 PASS 链路同源）+ RESULTS.md 对应节。复跑时按此对照。

---

## R-5 — 方案 B codify：FinalEvent + ErrorEvent 双触发（🟡 后端 log + DB）★

**对应 bug**：#5（commit `c31d261`）。原 codify hook **只挂 FinalEvent 分支** → relay ReadError 中止 turn / 迭代上限 / 长 turn 都不到 FinalEvent → codify 整段不运行 → 不 propose → 卡不弹。修法：codify hook 抽成模块级 `_maybe_codify_skill(...)` helper，在 **FinalEvent + ErrorEvent 两处都调** → turn 经 ReadError / 迭代上限 / 中止结束时（若已跑 ≥5 工具）仍触发技能自创。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | code 会话发达触发阈值（≥5 工具 或 ≥3 不同工具）的多步任务，**让其经 ErrorEvent 路径收尾**（relay 间歇 ReadError 中止，或迭代上限） | turn 即使非 FinalEvent 收尾，codify helper 仍被调 |
| 2 | grep log codify helper 触发 + DB `pending_skill_candidates` | 在 ErrorEvent 路径下若步数达阈值 → 仍产候选行（status='pending'）；步数不足则正确 no-op |

**判定**：✅ 好结果 = ErrorEvent/迭代上限路径下 codify 仍触发（步数够则产候选）。
❌ **复发坏结果** = 只有干净 FinalEvent 才 codify，ReadError 中止的 turn 永不 propose（依赖「快速干净 turn」在 relay 限速下不稳定）。
**分级**：🟡（双分支触发以 log + 候选 DB 落盘核对；4 个 helper 单测守护，真机以非-FinalEvent 路径产候选为证据）。

---

## R-6 — SkillComponent 无观测日志 → 自动披露生产不可见（🟡 后端 log）

**对应 bug**：#6（commit `56ba381` 第 1 层）。SkillComponent 原**无任何观测日志** → 自动披露在生产是否生效完全不可见（调试 6 层死链时第一道墙）。修法：加 `skill_auto_disclosed total/strong/auto_loaded/names/top_sim` 硬证据日志（`skill.py:215`）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | code 会话发任意会让 SkillComponent fan-out 的消息 | grep log **出现 `skill_auto_disclosed total=N strong=N auto_loaded=N names=[...] top_sim=N.NNN`** |

**判定**：✅ 好结果 = `skill_auto_disclosed ...` 日志 fire（披露行为可观测）。
❌ **复发坏结果** = 该日志消失 → 自动披露再次变「黑盒」，无法判定 R-7~R-11 是否生效。
**分级**：🟡（观测性日志，后端 log 核对）。

---

## R-7 — assemble() config 漏 skills 段（文字 venue）→ auto_enabled 恒 False（🟡 后端 log）★

**对应 bug**：#7（commit `56ba381` 第 2 层，venue-miss 第 2 处）。`_run_chat` 调 `assemble()` 传的 config dict 只带 llm/code_mode、**漏 skills** → `SkillComponent._read_auto_disclosure_config` 读不到 → `auto_enabled` 恒 False → desc-only 早返回（永不 auto_load 正文）。修法：main.py 补回 skills 段（`main.py:5145-5165` 附近）+ 根治为 assembler `default_config` 兜底（见 R-13）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | code 会话发强匹配某 skill 的话（如「帮我把这周几个会议纪要整理成 PPT」），等完成 | `skill_auto_disclosed` 行 **`auto_loaded ≥ 1`**（正文真内联） |

**判定**：✅ 好结果 = `auto_loaded ≥ 1`（skills 配置传到，auto_enabled=True）。
❌ **复发坏结果** = `total=0` 或组件早返回 desc-only（auto_loaded 恒 0）→ 强匹配也不载正文。
**分级**：🟡（后端 log 核对；与 R-11 top_sim 合并看）。

---

## R-8 — code policy prefer 漏 skill → SkillComponent 永不 fan-out（🟡 后端 log + 配置核对）★

**对应 bug**：#8（commit `56ba381` 第 3 层，policy-fanout-gating 第 3 处）。`code` policy 的 `prefer` **漏 skill** → `ComponentRegistry.fanout` 在 code 会话（/goal 所在）永不调 SkillComponent。修法：`default.yaml` `code.prefer` 补 `skill`（`default.yaml:73`）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | 静态核对 `default.yaml` code policy | `code:` 的 `prefer` 列表**含 `skill`**（`prefer: [persona, tool, workspace, workspace_memory, skill, preference_profile]`） |
| 2 | code 会话发任意消息 | `skill_auto_disclosed` 日志 **fire**（组件在 code venue 真 fan-out） |

**判定**：✅ 好结果 = code 会话 `skill_auto_disclosed` fire（组件 fan-out）。
❌ **复发坏结果** = code 会话该日志**从不 fire**（skill 不在 code.prefer → SkillComponent 永不被调）。
**分级**：🟡（配置核对 + 后端 log fan-out 证据）。

---

## R-9 — code 会话靠文本分类落 chat 无 skill → task_type_override="code"（🟡 后端 log）★

**对应 bug**：#9（commit `56ba381` 第 4 层）。code 会话原靠**用户文本分类**决定 task_type（「整理纪要生成 PPT」被分到 chat，而 chat policy 无 skill）→ 又落不到 code policy。修法：main.py code 会话传 `task_type_override="code"`（`main.py:5165` 附近），确定性走 code policy。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | code 会话发一句**会被文本分类误判成 chat** 的话（如纯自然语言「帮我把这周几个会议纪要整理成 PPT」），等完成 | grep log task_type 为 **code**（override 生效），`skill_auto_disclosed` fire |

**判定**：✅ 好结果 = code 会话无论文本如何分类，都走 code policy（task_type=code）→ skill 组件 fan-out。
❌ **复发坏结果** = 自然语言任务被分到 chat → skill 不 fan-out → `skill_auto_disclosed` 不 fire。
**分级**：🟡（后端 log task_type 核对）。

---

## R-10 — codify 生成的 SKILL.md 漏 task_types → SkillLoader.select 永远过滤掉自创技能（🟡 SKILL.md 落盘核对）★

**对应 bug**：#10（commit `56ba381` 第 5 层）。codifier 生成的 SKILL.md frontmatter **漏 `task_types`** → `SkillLoader.select(task_type)` 按 task_types 过滤时永远排除它 → codify 造了、disclosure 召不回（FP-5 闭环断裂）。修法：skill_codifier 生成时硬编码 `task_types: [code, task]`（`skill_codifier.py:178`）+ 补已存 SKILL.md。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | 走 R-9 全链产一个自创技能（或检查已存 `<user_data>/skills/user/meeting-minutes-to-ppt/SKILL.md`） | SKILL.md frontmatter **含 `task_types: [code, task]`** + `requires_script: false` |
| 2 | 新 code 会话发触发该技能的话 | `skill_auto_disclosed names=[...含该自创技能...]`（select 不再过滤掉它） |

**判定**：✅ 好结果 = 自创 SKILL.md 含 `task_types` → 后续会话被 select 选中、被 disclosure 召回。
❌ **复发坏结果** = SKILL.md 无 task_types → `select()` 永远过滤掉 → 自创技能召不回（top_sim 即便高也进不了候选）。
**分级**：🟡（SKILL.md frontmatter 落盘核对 + 后续召回 log）。

---

## R-11 — ★SkillMatcher 同步调 async embedder → 缓存恒空 top_sim=0.0 永远零匹配（✅真机 windows-mcp）★★

**对应 bug**：#11（commit `56ba381` 第 6 层，**最隐蔽**）。`SkillMatcher.build()/match()` **同步**调 `embedder.encode(text)`，但生产 BGE-M3 是 **`async encode(list)->ndarray`** → 同步调拿到**未 await 的 coroutine** → `_normalise` 迭代抛异常被 `except: pass` 吞 → **缓存恒空 → top_sim=0.0 永远零匹配**。单测全用 sync mock embedder 掩盖了它。修法：重写 `match_async` 兼容**两种 embedder 契约**（生产 async `embed(list)` / 单测 sync `encode(str)`），惰性建缓存（`skill_matcher.py:115-137`）+ 2 个 async-embedder 回归测试守护契约。

**前置**：boot 须确认真 BGE-M3（`is_mock=False`）。已存自创技能 `meeting-minutes-to-ppt`（R-10）作匹配目标。

| 步骤 | 动作（声明式） | 可观测预期 |
|---|---|---|
| 1 | `坐标=(Code InputBar) | 动作=Clipboard「帮我把这周几个会议纪要整理成 PPT，每个会议要有议题和结论」+ SendInput Ctrl+V + Enter | 期望=触发 skill 强匹配` | 消息发出 |
| 2 | 等完成 → grep log `skill_auto_disclosed` | **`total≥1 strong≥1 auto_loaded≥1 names=['meeting-minutes-to-ppt'...] top_sim>0.55`** |
| 3 | 看 agent 回复 | 明确保留技能字段（「议题」「结论」+ 主动补技能正文独有项） |
| 4 | 截图 `screenshots/r-11-async-matcher.png` | 存盘 |

**判定**：✅ 好结果 = `skill_auto_disclosed strong≥1 auto_loaded≥1 top_sim>0.55`（async 嵌入匹配活了）。
❌ **复发坏结果** = **`top_sim=0.00 strong=0 auto_loaded=0`**（同步调 async embedder → coroutine 被吞 → 缓存恒空 → 永远零匹配）。**这是最隐蔽的回归——单测仍会绿（sync mock），只有真机 BGE-M3 才暴露。**
**分级**：✅真机 windows-mcp（必须真 BGE-M3 + 真发任务，禁用 sync mock 替代——正是 mock 掩盖了原 bug）。
**本会话真机证据指针**：本会话已真机 PASS（`top_sim>0.55` 强匹配自动披露真活）——证据见 `plans/manual-results-2026-06-06-FP345/screenshots/tc-5.1-auto-disclosure.png`（对应 TC-5.1 自动披露链）+ RESULTS.md async-embedder 修复节。复跑时按此对照（坏结果一旦出现 `top_sim=0.00` 即 async embedder 回归）。

---

## R-12 — preference_profile 组件不在任何 policy → FP-4 偏好/画像注入生产全局死（✅真机 windows-mcp）★

**对应 bug**：#12（commit `c64fb81`，第 7 处，同 R-8 同根）。`preference_profile` 组件注册了却**不在 default.yaml 任何 policy 的 prefer 里** → `ComponentRegistry.fanout` 永不调它 → FP-4 偏好/画像注入（WI-3.2）在**任何 task_type 下都死**（persona_inject flag 开了也白搭）。修法：给 chat/recall/task/code/plan/emotion policy 的 prefer 补 `preference_profile`（`default.yaml:36/47/57/73/97/107`）+ `preference_profile_injected facts=N` 观测日志（`preference_profile.py:120`）。

**前置**：先在 code 会话陈述过偏好/决策（让 facts 表有 ≥1 条 profile/preference/constraint）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | 静态核对 `default.yaml` | chat/recall/task/code/plan/emotion 的 `prefer` 均**含 `preference_profile`** |
| 2 | code 会话发任意消息（已有 facts），等完成 → grep log | **`preference_profile_injected facts=N task_type=code`**（N≥1） |

**判定**：✅ 好结果 = `preference_profile_injected facts≥1` fire（组件 fan-out 并把 facts 注入 prompt）。
❌ **复发坏结果** = 该日志**从不出现**（组件不在 fanout）→ FP-4 偏好注入全局死（跨会话召回 / 偏好反映 / 冲突全部失效）。
**分级**：✅真机 windows-mcp（FP-4 注入是用户可感知行为；以真机消息 + 注入 log 为证据）。
**本会话真机证据指针**：本会话已真机 PASS（偏好/画像注入真生效，跨会话召回 + 偏好冲突 replace 均答对）——证据见 `plans/manual-results-2026-06-06-FP345/screenshots/tc-4.1-cross-session-recall.png`（TC-4.1 跨会话召回）+ `tc-4.3-preference-conflict.png`（TC-4.3 偏好冲突）+ RESULTS.md preference_profile 接线节。复跑时按此对照。

---

## R-13 — 语音 venue assemble() 漏 skills 配置 → 根治为 assembler default_config（🟡 后端核对）★

**对应 bug**：#13（commit `c739a33`，第 8 处 venue-miss）。语音 venue 的 `assemble()` 同样漏传 skills 配置（与 R-7 文字 venue 同病）。**根治**：assembler 自带 `default_config`（build 时记住 auto_disclosure 配置，assemble() 时兜底合并；`assembler.py:91-155`），调用方未传 skills 时由默认兜底 → **任何 venue 自动拿到 auto_disclosure** → 杜绝 venue-miss。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | 静态核对 main.py `build_companion_assembler` | 构造 assembler 时传 `auto_disclosure_config={...}`（`main.py:1475-1482`）作 default_config |
| 2 | 静态核对 `assembler.py` assemble() | assemble() 对 `self._default_config` 兜底合并到 config（调用方未传 skills 时用默认） |
| 3 | 语音链路触发一次 assemble（语音对话或单测对应路径） | SkillComponent 拿到 auto_disclosure 配置（非 desc-only 早返回） |

**判定**：✅ 好结果 = 语音 venue 经 default_config 自动拿 skills 配置（无 venue-miss）。
❌ **复发坏结果** = 语音 venue assemble 又漏 skills → 语音里 SkillComponent desc-only 早返回（披露死）。
**分级**：🟡（venue 根治以代码侧 default_config 机制 + 语音链路核对为主；3 个回归测试守护）。

---

## R-14 — default_config 浅合并隐患 → 一层深合并加固（🟡 后端核对，加固）

**对应 bug**：#14（commit `d4ee7d6`，第 10 处加固）。`default_config` 兜底合并若是**浅合并**：未来某 venue 只传 `skills.codify` 会把默认的 `skills.auto_disclosure` 整段抹掉。修法：对二级 dict（如 skills）做**一层深合并**（`assembler.py:145-155`：`_merged[_k] = {**_merged[_k], **_v}`）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | 静态核对 `assembler.py` 合并逻辑 | 二级 dict 走 `{**default, **caller}` 深合并，而非整段覆盖 |
| 2 | 构造一次「调用方只传 skills.codify」场景（单测/语音路径） | 合并后 config.skills **仍含 default 的 auto_disclosure**（未被部分 skills 传参抹掉） |

**判定**：✅ 好结果 = 调用方只传 skills 子键时，default 的其他 skills 子键仍保留（深合并）。
❌ **复发坏结果** = 浅合并 → 调用方传部分 skills 把 auto_disclosure 整段抹掉 → 该 venue 披露死。
**分级**：🟡（加固，代码侧深合并核对 + 深合并测试守护）。

---

## R-15 — 语音 venue 裸 _AgentLoop → codify(FP-5)/verify(FP-3) 不触发 → 改走 build_agent（🟡 后端核对）★

**对应 bug**：#15（commit `c0bf85d`，第 9 处）。语音 venue 原直接用**裸 `_AgentLoop`** → codify(FP-5)/verify(FP-3) 钩子**不接** → 语音对话里技能自创/伪完成拦截全不触发。修法：语音 venue 改走 `build_agent` 工厂（`voice_pipeline.py:547-591`）+ `_maybe_codify_voice`（`voice_pipeline.py:111-137`，在语音 turn 结束的两处调）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | 静态核对 `voice_pipeline.py` | 语音 venue 调 `build_agent(...)` 构造 loop（非裸 `_AgentLoop`）；turn 结束调 `_maybe_codify_voice()`（行 661/664） |
| 2 | 语音链路跑一个达阈值的多工具任务（若可真机驱动语音）/ 单测对应路径 | codify/verify 钩子在语音 venue 也接通（与文字 venue 对齐） |

**判定**：✅ 好结果 = 语音 venue 经 build_agent 接 codify+verify（与文字 venue 一致）。
❌ **复发坏结果** = 语音裸 _AgentLoop → 语音对话技能自创/verify gate 静默不触发。
**分级**：🟡（语音 venue 接线，代码侧 build_agent 核对 + 3 个回归测试；真机语音多工具触发难，以接线核对为主）。

---

## R-16 — 语音 v2_enabled getattr 落空恒 True + codify 阻塞 TTS 风险 → 读 config.raw + fire-and-forget（🟡 后端核对，终轮 polish）

**对应 bug**：#16（commit `ce9245f`/`ce29833`，终轮 polish 2 处瑕疵）。(a) `v2_enabled` 用 `getattr(config, "context", ...)` 落空恒 True——AppConfig 把 `[context]` 放在 `.raw`、**无 `.context` 属性** → v2_enabled 回退闸对语音失效；(b) codify 在语音 turn 内**同步 await 阻塞 TTS**。修法：v2_enabled 读 `config.raw`（`voice_pipeline.py:560-572`）；codify **fire-and-forget**（`_maybe_codify_voice` 内 `asyncio.create_task` + 强引用集防 GC，`voice_pipeline.py:85/111-137`）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | 静态核对 `voice_pipeline.py` v2_enabled 读取 | 从 `config.raw.get("context",{}).get("v2_enabled", True)` 读（非 `getattr(config,"context",...)`） |
| 2 | 静态核对 `_maybe_codify_voice` | codify 走 `asyncio.create_task`（fire-and-forget）+ 任务存入强引用集（防 GC 提前回收，对照 R-2/B-1 的 GC 教训） |
| 3 | 设 `[context] v2_enabled=false` 跑语音 | v2 回退闸对语音**真生效**（不再恒 True） |

**判定**：✅ 好结果 = v2_enabled 读 config.raw 生效 + codify 不阻塞 TTS（fire-and-forget）。
❌ **复发坏结果** = v2_enabled 恒 True（回退闸语音失效）/ codify 同步 await 卡住语音 TTS。
**分级**：🟡（终轮 polish，代码侧核对 + 深合并/v2 测试守护）。

---

# B 组 — 9 类边界 / 环境情况

## B-1 — flag-OFF 字节级 BC：auto_disclosure / codify / persona_inject 关 → desc-only / 不接线 / 空 Slice（🔵 纯 flag-OFF 字节核对）★

**对应边界**：flag-OFF 字节级 BC（守护「关 flag = 行为与旧版一致」，本会话所有修复都带双门控）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | 注入配置关三 flag：`[skills.auto_disclosure] enabled=false` + `[skills.codify] enabled=false` + `[memory.v2] persona_inject=false` → 重启 | backend 起 |
| 2 | boot grep | **无 `fp5_codify_wiring_ready`**（codify 不接线）；`fp5_auto_disclosure_wiring_ready` 不出现或 matcher 不构造 |
| 3 | code 会话发会议纪要任务 | SkillComponent **desc-only**（`auto_loaded=0`，不内联正文）；`preference_profile` 返**空 Slice**（persona_inject off）；无 `preference_profile_injected` |
| 4 | 发多工具任务 | codify 不接线 → 不产候选（`pending_skill_candidates` 不新建） |

**判定**：✅ 好结果 = 三 flag OFF 时全部退化到旧版字节行为（desc-only / 不接线 / 空 Slice，字节不变）。
❌ **复发坏结果** = flag OFF 仍内联正文 / 仍接线 / 仍注入（双门控失守，违反字节契约）。
**分级**：🔵（flag 切换 + 后端核对；与 goal-completion TC-4.6 互补）。

---

## B-2 — write_file 权限门 + auto_mode 放行（🟡 后端 log）

**对应边界**：write_file 权限门 + auto_mode（`permissions_auto_mode.json={"enabled":true}` 放行；boot `permission_auto_mode_restored enabled=True`）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | 写 `<user_data>/permissions_auto_mode.json = {"enabled":true}` → 重启 | boot grep **`permission_auto_mode_restored enabled=True`** |
| 2 | code 会话发「创建 hello.txt 写入 test」 | agent 真调 `write_file` ok（`bytes_written>0` + `artifacts:[{kind:file}]`），**无新 permission denied** 弹窗 |
| 3 | `cat <工作区>/hello.txt` | 内容 = "test"（文件真落盘） |

**判定**：✅ 好结果 = auto_mode 放行 write_file，文件真落盘，0 次新 permission denied。
❌ **坏结果** = 即使 auto_mode 仍弹权限门 / write_file 被拒。
**分级**：🟡（auto_mode 是基础设施非被测特性；以 boot log + write 落盘核对）。

---

## B-3 — relay 间歇 ReadError 下 agent 仍能完成工具（🟡 后端 log）★

**对应边界**：relay 间歇 ReadError 下 agent 仍能完成工具（R-3 鲁棒性的端到端表现）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | code 会话发 5 步多工具任务，relay 间歇抖动时段，耐心等 | session 即使中途「⚠ error」也**恢复推进**；TODOS 全完成（如 3/3）；累计多工具调用 |
| 2 | grep log | 流式重试 log 出现；turn 最终 `stop_reason='end_turn'` 收尾 |

**判定**：✅ 好结果 = 间歇 ReadError 下 agent 完成全部工具。
❌ **坏结果** = 首次 ReadError 即崩，0~2 工具停。
**分级**：🟡（与 R-3 同源，端到端表现核对）。

---

## B-4 — LLM 诚实性：gpt-5.5 拒绝伪造完成声明（verify gate catch 靠单测）（🟡 后端 log + 单测说明）

**对应边界**：LLM 诚实性——现代 LLM 太诚实，「拦假声明」难按需真机触发；verify gate 的 catch 逻辑由 `test_verify_gate`/`test_outcome_verifier` 单测覆盖（合成假声明）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | code 会话设 goal 后，诱导 agent「不调工具直接回复'已完成 X.md'」 | gpt-5.5 **拒绝伪造**，转而真做（如 read_file）；未发假完成声明 |
| 2 | grep log `verify_gate` | verify gate 跑（`verify_gate_init`），但无假声明可拦（真机难触发拦截路径） |
| 3 | 回归守护说明 | verify gate catch 逻辑由 `test_verify_gate.py`/`test_outcome_verifier.py` 合成假声明覆盖；真机价值在「不误杀真完成」（见 goal-completion TC-3.2 PASS） |

**判定**：✅ 好结果 = 诱导下 LLM 诚实、verify gate 不误判；catch 单测绿。
❌ **坏结果** = verify gate 把真完成误杀（误拦），或单测红。
**分级**：🟡（真机 + 单测说明；伪完成拦截受 LLM 诚实性限制，记录为已知）。

---

## B-5 — /goal 粘贴不触发前端 slash 补全（测试法限制，非 bug）（说明节）

**对应边界**：`/goal` 粘贴不触发前端 slash 补全（`msg_type != slash_command` → 当任务；测试法限制非 bug；真人键入触发）。

| 项 | 内容 |
|---|---|
| 现象 | windows-mcp SendInput 粘贴 `/goal X` + Enter 被 agent 当**任务**跑，非设目标 |
| 根因 | 后端 slash 仅在 `msg_type=="slash_command"`（前端检测键入 "/" 弹补全后发的独立 WS verb）时处理；Ctrl+V 粘贴不触发键入检测 → 走普通 chat（`main.py:4264` 附近 + `deskpet/commands` dispatch） |
| 判定 | **非产品 bug**，是 windows-mcp 粘贴测试法限制。真人键入 `/goal` 触发补全 → 正常设目标。后端 slash 解析 + goal_store + facts 钩链路由 goal-completion TC-4.5 验证 |
| 续跑做法 | 依赖 goal 设定的 TC 用「键入 `/goal` 前缀触发补全 + 粘贴中文正文」混合输入法 |

**回归意义**：记录此项防止后续误把「粘贴 /goal 当任务」当成 slash 解析 bug。

---

## B-6 — 多 venue 免疫：未来新增 venue 经 assembler default_config 自动拿 skills 配置（🟡 后端核对）

**对应边界**：多 venue 免疫（assembler default_config 机制让未来新 venue 自动拿 skills 配置，杜绝 R-7/R-13 类 venue-miss 复发）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | 静态核对：assembler `default_config` 在 build 时记住 auto_disclosure（`main.py:1475` / `assembler.py:105`） | 任何 venue 调 assemble() 即使不传 skills，也由 default_config 兜底合并 |
| 2 | 文字 venue（_run_chat）+ 语音 venue 两路 | 两路都拿到 auto_disclosure（R-7 + R-13 均经此根治） |

**判定**：✅ 好结果 = default_config 是 venue-miss 的**根治机制**，新 venue 自动免疫。
❌ **坏结果** = 新 venue 又各自传 config 漏 skills（回到逐 venue 打补丁）。
**分级**：🟡（架构级机制核对；这是「同根第 11 处」不再出现的保证）。

---

## B-7 — daily_decay 是 decay-on-boot（显式 defer 非 bug）（说明节）

**对应边界**：daily_decay 是 decay-on-boot（显式 defer，非 bug）。

| 项 | 内容 |
|---|---|
| 行为 | `FactsStore.daily_decay()` 在 boot 时调度（decay-on-boot），非后台定时器 |
| 判定 | 这是**显式设计 defer**，非「daily_decay 从不调用」的旧 bug（旧 bug 见 goal-completion TC-4.4，已接通 lifespan）。pinned=1 的 fact 跳过衰减（`WHERE pinned=0`） |
| 回归守护 | Pin 不衰减 / unpin 恢复由 goal-completion TC-4.4 + `test_pin_and_pref_decay` 单测覆盖 |

**回归意义**：记录 decay-on-boot 是设计选择，防后续误判为「衰减没跑」。

---

## B-8 — codify dedup：已有 pending 候选时不重复 propose（✅真机 / 🟡 DB）

**对应边界**：codifier dedup（已有 pending 候选时再跑同类任务不重复 propose；合理防重复，但叠加 R-4 ephemeral-card 丢卡曾导致丢卡后无法靠新任务再弹——R-4 已修）。

| 步骤 | 动作 | 可观测预期 |
|---|---|---|
| 1 | 已有一个 pending 候选（DB `pending_skill_candidates` 有 status='pending' 行） | — |
| 2 | 再跑同类多工具任务 | **不重复 propose** 同类候选（dedup 生效，DB 不堆积重复行） |
| 3 | 清 pending 后再跑 | 可重新 propose |

**判定**：✅ 好结果 = pending 存在时不重复弹卡（防打扰）；R-4 修复后丢卡也不影响（卡不再被 reload 丢）。
❌ **坏结果** = 重复弹同类卡乱造 / 或 dedup + R-4 丢卡叠加导致永远拿不回卡（R-4 已修，此项确认不复发）。
**分级**：✅真机观察「不重复弹卡」 + 🟡 DB 核对候选不堆积。

---

## B-9 — 技能候选卡在完整 chat 视图渲染（已知友好性 bug，未修，跟踪 task_01be24af）（🟡 已知未修）★

**对应边界**：本会话真机测 TC-5.4（候选拒绝不落盘）时新发现的**前端友好性 bug**——与 R-4 **同源不同症**。R-4 已修「reload 时保留内存里仍 awaiting 的卡」，但「**完整 chat 视图加载的 DB 快照停在上一 turn、不含最新 turn 的卡**」这条仍在：codify 真机提出**新**候选后（后端 `pending_skill_candidates` 有 pending 行 + `skill_candidate_proposed` WS 已 emit），打开完整 chat 时候选卡**常不显示**（chat 加载的是上一 turn 快照，不含本 turn 刚产生的 ephemeral 卡），导致**无法真机点「忽略」**走 TC-5.4 拒绝路径。

> R-4 vs B-9 区分：R-4 = 卡已渲染后被 reload 整体替换丢失（已修，merge 保留 awaiting）；B-9 = 卡刚由最新 turn 产生、但完整 chat 视图的 DB 快照不刷新到该 turn → 卡从未在 chat 视图出现（**未修**）。

| 步骤 | 动作（声明式） | 可观测预期（修复后） |
|---|---|---|
| 1 | code 会话发一个**多工具任务**（≥5 工具或 ≥3 不同工具）触发 codify 产**新**候选 | 后端 `skill_candidate_proposed cid=N` + DB `pending_skill_candidates` 有 status='pending' 行 |
| 2 | `坐标=(打开完整 chat 入口) | 动作=SendInput click | 期望=chat 视图刷新到含最新 turn 候选卡` → 截图 | **绿色「✨ 新技能」候选卡可靠显示**（chat 视图刷新到最新 turn） |
| 3 | `坐标=(候选卡「忽略」按钮) | 动作=SendInput click | 期望=拒绝路径` → 截图 | pending 行被删（status→rejected/删除）+ **无新 `<user_data>/skills/user/<slug>/SKILL.md`**（= TC-5.4 拒绝路径成立） |

**判定**：✅ 好结果（修复后）= 新候选卡在完整 chat 视图**可靠渲染** → 可点「忽略」→ pending 删 + 无新 SKILL.md（TC-5.4 拒绝路径全证）。
🟡 **当前状态（未修）** = codify 候选生成 ✅（后端 pending + WS emit 已实证）；但**点「忽略」受阻 🟡**（chat 视图不刷新到最新 turn 的卡 → 卡常不显示 → 无法真机点忽略）。accept 路径（TC-5.3）本会话已**全证 PASS**，reject 路径与之对称、逻辑相同，故拒绝路径的**后端语义**已被覆盖，仅**前端可点性**受此 bug 阻塞。
**跟踪**：已 spawn 跟踪任务 **task_01be24af**（未修）。
**分级**：🟡（已知未修友好性 bug，记录现象 + 复现步骤 + 修复后期望；后端拒绝语义可由 DB 核对，前端可点性待修复后真机补 TC-5.4 reject 真机门）。

---

# 测试结果汇总表（执行时填写）

## A 组 — 16 处跨层 bug

| 用例 | 对应 bug / commit | 命门证据 | 分级 | 判定 |
|---|---|---|---|---|
| R-1 config[skills.codify] | #1 / 7732c0d | boot `fp5_codify_wiring_ready True,True,True` | 🟡 | ☐ PASS / ☐ FAIL |
| R-2 5 处接线断裂 | #2 / 58fbac8 | boot `fp5_auto_disclosure_wiring_ready matcher=True` + 无 ValueError | 🟡 | ☐ PASS / ☐ FAIL |
| R-3 relay ReadError 鲁棒 | #3 / 4f39e3a | agent 完成全部工具 + 重试 log | 🟡 | ☐ PASS / ☐ FAIL |
| R-4 ephemeral-card 丢卡 | #4 / 1f63c75 | 开完整 chat 后技能卡存活 | ✅ | ☐ PASS / ☐ FAIL |
| R-5 方案 B codify 双触发 | #5 / c31d261 | ErrorEvent 路径下 codify 仍触发 | 🟡 | ☐ PASS / ☐ FAIL |
| R-6 SkillComponent 观测日志 | #6 / 56ba381 | `skill_auto_disclosed ...` fire | 🟡 | ☐ PASS / ☐ FAIL |
| R-7 assemble() 漏 skills 段 | #7 / 56ba381 | `auto_loaded≥1`（非 desc-only） | 🟡 | ☐ PASS / ☐ FAIL |
| R-8 code policy 漏 skill | #8 / 56ba381 | code.prefer 含 skill + 组件 fan-out | 🟡 | ☐ PASS / ☐ FAIL |
| R-9 task_type_override=code | #9 / 56ba381 | code 会话 task_type=code | 🟡 | ☐ PASS / ☐ FAIL |
| R-10 SKILL.md 漏 task_types | #10 / 56ba381 | SKILL.md 含 `task_types:[code,task]` | 🟡 | ☐ PASS / ☐ FAIL |
| R-11 ★async embedder 零匹配 | #11 / 56ba381 | `top_sim>0.55 strong≥1 auto_loaded≥1` | ✅ | ☐ PASS / ☐ FAIL |
| R-12 preference_profile 缺 policy | #12 / c64fb81 | `preference_profile_injected facts≥1` | ✅ | ☐ PASS / ☐ FAIL |
| R-13 语音 venue 漏 skills | #13 / c739a33 | assembler default_config 兜底 | 🟡 | ☐ PASS / ☐ FAIL |
| R-14 深合并加固 | #14 / d4ee7d6 | 二级 dict 深合并 | 🟡 | ☐ PASS / ☐ FAIL |
| R-15 语音裸 _AgentLoop | #15 / c0bf85d | 语音走 build_agent+codify | 🟡 | ☐ PASS / ☐ FAIL |
| R-16 v2_enabled + fire-and-forget | #16 / ce9245f | v2_enabled 读 config.raw + codify create_task | 🟡 | ☐ PASS / ☐ FAIL |

## B 组 — 9 类边界 / 环境

| 用例 | 边界 | 命门证据 | 分级 | 判定 |
|---|---|---|---|---|
| B-1 flag-OFF 字节 BC | 三 flag OFF | desc-only / 不接线 / 空 Slice | 🔵 | ☐ PASS / ☐ FAIL |
| B-2 write_file 权限门 | auto_mode 放行 | `permission_auto_mode_restored enabled=True` | 🟡 | ☐ PASS / ☐ FAIL |
| B-3 relay 间歇下完成工具 | 鲁棒性端到端 | agent 完成全部工具 | 🟡 | ☐ PASS / ☐ FAIL |
| B-4 LLM 诚实性 | verify catch 单测 | LLM 拒伪造 + catch 单测绿 | 🟡 | ☐ PASS / ☐ FAIL |
| B-5 /goal 粘贴不触发补全 | 测试法限制 | 说明节（非 bug） | — | ☐ 已记录 |
| B-6 多 venue 免疫 | default_config 根治 | 新 venue 自动拿 skills | 🟡 | ☐ PASS / ☐ FAIL |
| B-7 daily_decay decay-on-boot | 显式 defer | 说明节（非 bug） | — | ☐ 已记录 |
| B-8 codify dedup | 防重复弹卡 | pending 存在不重复 propose | ✅/🟡 | ☐ PASS / ☐ FAIL |
| B-9 候选卡 chat 视图渲染 | 已知未修友好性 bug（task_01be24af） | chat 视图刷新到最新 turn 卡 → 可点忽略 | 🟡 | ☐ 已记录（未修） |

---

# 覆盖矩阵（16 bug + 8 边界 → TC 映射，确保无遗漏）

## 16 处 bug

| # | bug 描述 | commit | 对应 TC |
|---|---|---|---|
| 1 | config.py 漏解析 [skills.codify] | 7732c0d | **R-1** |
| 2 | 5 处跨层接线断裂（services/recorder/build_agent） | 58fbac8 | **R-2** |
| 3 | relay ReadError 鲁棒性（adapter+registry+流式重试） | 4f39e3a | **R-3**（+B-3 端到端） |
| 4 | 前端 ephemeral-card reload 丢失 | 1f63c75 | **R-4** |
| 5 | 方案 B codify FinalEvent+ErrorEvent 双触发 | c31d261 | **R-5** |
| 6 | SkillComponent 无观测日志 | 56ba381 | **R-6** |
| 7 | assemble() config 漏 skills 段（文字 venue venue-miss #2） | 56ba381 | **R-7** |
| 8 | code policy prefer 漏 skill（policy-fanout #3） | 56ba381 | **R-8** |
| 9 | code 会话靠文本分类 → task_type_override=code（#4） | 56ba381 | **R-9** |
| 10 | codify SKILL.md 漏 task_types（#5） | 56ba381 | **R-10** |
| 11 | ★SkillMatcher 同步调 async embedder 零匹配（#6） | 56ba381 | **R-11** |
| 12 | preference_profile 不在任何 policy（#7） | c64fb81 | **R-12** |
| 13 | 语音 venue assemble() 漏 skills（#8 venue-miss） | c739a33 | **R-13**（+B-6 免疫） |
| 14 | default_config 浅合并隐患（#10 加固） | d4ee7d6 | **R-14** |
| 15 | 语音 venue 裸 _AgentLoop（#9 codify/verify 不接） | c0bf85d | **R-15** |
| 16 | v2_enabled getattr 落空 + codify 阻塞 TTS（终轮 polish） | ce9245f | **R-16** |

## 9 类边界 / 环境

| 边界 | 对应 TC |
|---|---|
| flag-OFF 字节级 BC（auto_disclosure/codify/persona_inject） | **B-1** |
| write_file 权限门 + auto_mode | **B-2** |
| relay 间歇 ReadError 下完成工具 | **B-3**（+R-3） |
| LLM 诚实性（gpt-5.5 拒伪造，verify catch 靠单测） | **B-4** |
| /goal 粘贴不触发前端 slash 补全（测试法限制非 bug） | **B-5** |
| 多 venue 免疫（assembler default_config） | **B-6**（+R-13） |
| daily_decay 是 decay-on-boot（显式 defer 非 bug） | **B-7** |
| codify dedup（已有 pending 不重复 propose） | **B-8** |
| 技能候选卡在完整 chat 视图渲染（已知未修友好性 bug，task_01be24af） | **B-9** |

**覆盖确认**：16 bug → R-1~R-16（一一对应）；9 边界/环境 → B-1~B-9（一一对应，B-9 为本会话 TC-5.4 真机新发现的已知未修友好性 bug）。**全覆盖，无遗漏**。

---

## 附录：本文回归关键 log / DB 锚点速查

| 类别 | 锚点 | 含义 |
|---|---|---|
| boot 命门 | `fp5_codify_wiring_ready tool_path=True candidate_store=True llm=True` | R-1 config[skills.codify] 解析正确 |
| boot 命门 | `fp5_auto_disclosure_wiring_ready matcher=True` | R-2 SkillMatcher 构造成功 |
| boot | `permission_auto_mode_restored enabled=True` | B-2 write_file auto_mode |
| 披露 | `skill_auto_disclosed total=N strong=N auto_loaded=N names=[...] top_sim=N.NNN` | R-6/7/8/9/10/11 披露全链（top_sim>0.55 是 R-11 命门） |
| 注入 | `preference_profile_injected facts=N task_type=...` | R-12 偏好注入 fan-out |
| relay | `timeout/conn-drop ... retrying same provider` / 流式重试 | R-3/B-3 鲁棒性 |
| 自创 | `skill_candidate_proposed cid=N name=...` + DB `pending_skill_candidates` | R-2/R-5 候选生成 |
| 落盘 | `<user_data>/skills/user/<slug>/SKILL.md`（含 `task_types:[code,task]` + `requires_script:false`） | R-10 自创技能召回前提 |
| 前端 | `sessionsStore.set_messages` 保留 awaiting 卡（`sessionsStore.ts:311-324`） | R-4 ephemeral-card（已修） |
| 前端（已知未修） | 完整 chat 视图 DB 快照不刷新到最新 turn 的 ephemeral 候选卡 → 卡常不显示 | B-9 候选卡 chat 视图渲染（跟踪 task_01be24af） |
| 配置 | `default.yaml` 各 policy `prefer` 含 `skill` / `preference_profile` | R-8/R-12 fanout-gating |
| 代码 | `assembler.py` default_config 一层深合并 | R-13/R-14/B-6 venue 根治 |
| 代码 | `voice_pipeline.py` build_agent + `_maybe_codify_voice` fire-and-forget + config.raw v2_enabled | R-15/R-16 语音 venue |

> **真测纪律提醒**：R-4 / R-11 / R-12 是本文必须 windows-mcp 真机的招牌回归（前端丢卡 / async 零匹配 / 偏好注入死）——正是「单测绿但生产死」的代表。**禁止**用 import / sync mock embedder / 脚本回放当证据（mock 正是掩盖 R-11 原 bug 的元凶）。其余 🟡 项以真机驱动 + boot log / state.db 落盘核对为主，**禁止**纯 pytest 替代真机运行栈。
