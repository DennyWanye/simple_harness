# 手工测试用例 — goal-completion 升级（FP-3 / FP-4 / FP-5）

> **被测功能**: goal-completion 升级线的后三个大功能点 ——
> **FP-3 自我纠错闭环**（verify 拦伪完成 → 结构化反思 → 真重规划重试 → 二次真产物；verify 接 goal_text 对照；高后果外部 evaluator）、
> **FP-4 记忆 + 人格**（跨会话召回 goal/decision/constraint、人格画像主动注入、Pin 不衰减、B-10 goal→facts 双写钩、人格红线不污染完成判定）、
> **FP-5 Skills 分级披露 + 自创**（强匹配 skill 正文自动载入、压缩后 skill 重挂、多步任务技能自创确认卡 → 真坐标保存 → SKILL.md 落盘 → 新 session 复用）。
> **被测 commit**: `58fbac8`（`fix(goal-fp5): 修复 WI-4.1/4.2/4.3 跨层接线断裂 + 补 WI-1.6 喂数据 + 前端确认卡`）on `master`（`G:\projects\deskpet`）
> **对应迭代**: goal-completion-upgrade v4（[10-EXECUTION-ROADMAP.md](../plans/2026-06-04-goal-completion-upgrade/10-EXECUTION-ROADMAP.md) §1 的 FP-3/4/5 手测门）
> **blueprint**: FP-3 → [02-P0-2](../plans/2026-06-04-goal-completion-upgrade/02-P0-2-self-correction-execution.md) ｜ FP-4 → [03-P0-3](../plans/2026-06-04-goal-completion-upgrade/03-P0-3-memory-persona-execution.md) ｜ FP-5 → [04-P1-4](../plans/2026-06-04-goal-completion-upgrade/04-P1-4-skills-execution.md)
> **测试类型**: **以 windows-mcp 真机模拟人工点击为主**（HARD CONSTRAINT，见 `CLAUDE.md`「🔒 手工测试纪律」）；部分内部时序/降级类标 🟡log/后端核对（诚实 E2E 分级，见 roadmap §2）。
> **最后更新**: 2026-06-06

---

## 0. 测试前置准备

### 0.1 启动纪律（CLAUDE.md 踩坑 #7/#8/#9 — 不可违反）

> ⚠️ **不要**手动起 backend（`python main.py`），也**不要**手动起第二个 vite。
> 只给 **Tauri 进程**注入 env，让 Tauri 自己 spawn + 管理 backend + 自带 vite。
> 跑 worktree/源码代码必须设 `DESKPET_BACKEND_DIR`，否则跑的是旧 frozen exe（测了白测）。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| P-0 | 确认在被测 commit：`git rev-parse --short HEAD` | 输出 `58fbac8`（或其后续）；`git branch --show-current` = `master` |
| P-1 | 杀残留进程（防 8100 端口被 orphan 占）：`taskkill /F /IM deskpet.exe`（忽略 not found）+ 杀残留 vite/backend python | 端口 8100 / vite 端口空闲（坑 #7：orphan 累积会 crash-loop「启动失败」弹窗） |
| P-2 | 设全 FP flag 配置（**注入到 Tauri 进程 env**，不改 tracked config.toml）：<br>`DESKPET_CONFIG=G:\projects\deskpet\.tmp\fp1-config.toml`<br>`DESKPET_BACKEND_DIR=G:\projects\deskpet\backend`<br>`DESKPET_PYTHON=G:\projects\deskpet\backend\.venv\Scripts\python.exe`<br>`DESKPET_USER_DATA_DIR=<本次测试独立目录>`（R-T7 隔离，防串味） | — |
| P-3 | 启动 Tauri：`npx tauri dev`（让它自管 backend+vite） | 桌宠窗口出现 |
| P-4 | **确认跑的是源码不是 frozen exe**：看 tauri dev 终端 log | 出现 `[backend_launch] Dev python=...backend\.venv... backend_dir=G:\projects\deskpet\backend`（**不是** `Bundled exe=...`） |
| P-5 | **确认全 FP flag 已点亮**（boot log grep） | log 出现：`companion_code_v1_goal_mode_ready`（goal_mode ON）/ `verify_gate_init mode=strict`（FP-3）/ `wi4_0_compaction_enabled ... threshold=0.75`（FP-5 4.0）/ `goal_store_load_persisted restored=N`（FP-1 同栈） |
| P-6 | **登录态**：若需真 LLM 链路（agent loop / verify / 技能自创都需要），走 onboarding 登录测试账号（见 `CLAUDE.md`「开发期登录测试账号」，凭据从 gitignored `LOCAL-DEV-CREDENTIALS.md` 读）→ 等 relay 下发 key 写 keychain → 关 onboarding 窗 | backend 自动从 keychain 读 key；真 LLM 调用可用 |
| P-7 | 定位关键落盘路径（记下，后面核对用）：<br>· backend log = tauri dev 终端 stderr<br>· metrics = `<user_data>/metrics.jsonl`<br>· facts/memory DB = `<user_data>/data/memory.db`<br>· artifacts = `<user_data>/artifacts/<YYYY-MM-DD>/<tool>/`<br>· 自创 SKILL.md = `<user_data>/deskpet/skills/user/<slug>/SKILL.md` | 路径存在/可访问 |
| P-8 | **进入 Code 模式面板**（关键）：点桌宠 toolbar 的 code-mode 图标 → 打开独立 Code WebView2 窗 → 用 InputBar 输入 | `/goal` 和 agent loop **只在 Code 面板触发**；桌宠快聊 pill 走纯 LLM、**不解析 slash、不进 agent loop**（FP-1 已证），所有 TC 走 Code 面板 |

### 0.2 windows-mcp 工具陷阱与 workaround（引 CLAUDE.md「🛠 已知技术陷阱」）

| 障碍 | workaround（圣杯） |
|---|---|
| `Click(loc=[x,y])` schema bug（pydantic array→string） | PowerShell `[Win]::SetCursorPos(x,y)` + **SendInput** API（INPUT 结构 + MOUSEEVENTF_LEFTDOWN/UP）。**WebView2/Chromium 不响应老 `mouse_event`，必须 SendInput** |
| 中文输入 SendKeys 不支持 IME | STA Runspace + `[System.Windows.Forms.Clipboard]::SetText("中文")` + SendKeys `^v`（Ctrl+V） |
| DPI 150% 缩放坐标错位 | `SetProcessDpiAwareness(2)` 强制 physical pixel；GetWindowRect 返逻辑坐标需 ×1.5 |
| 焦点不在目标窗口 → 点击/粘贴落错窗 | 先 SetCursorPos+Click 聚焦 → 等焦点切换 → 再 Paste；**用 backend log 确认消息真到了**（不到说明粘贴失败要 retry） |
| Tauri webview UI tree 抓不到 element label | 用截图坐标 + SendInput 真点击，不依赖 element label |

### 0.3 诚实 E2E 分级说明（每个 TC 标注）

- **✅真模拟人**：windows-mcp 截图抓状态 → 真坐标点击/真输入 → 截图验证 → 肉眼+log 判定。这是「用户真用得了」的证据。
- **🟡log验证为主 / 后端核对**：内部时序、降级分支、并发、模拟时间推进等**无法靠纯点击观测**的项，以真机驱动 + log/落盘核对为主。**禁止**把 🟡 项当 ✅ 真模拟人交差；🟡 项也禁止用纯 pytest/import 替代真机运行栈。

### 0.4 判定约定

每个 TC 末尾 `PASS / FAIL / RETRY-N / SKIP（带理由）`。✅真模拟人类失败必须 retry ≥3 次不同 workaround 才能标 SKIP，且需用户确认。每个 windows-mcp 动作前先 declare：`坐标=(x,y) | 动作=click/type | 期望=...`。

---

# FP-3 — 自我纠错闭环（verify 拦伪完成 → 反思 → 真重规划 → 二次真产物）

> 手测门（roadmap §1 FP-3）：① 伪完成「生成 PPT」→ verify 拦 → 自动重规划 → 二次真生成 .pptx → ArtifactCard ② 真完成放行不误杀 ③ verify 接 goal_text 对照 ④ 反思字段注入 ⑤（边界）未来时不误判 / relay 故障降级 / 重试上限。
> 关键文件：`verify_gate.py`、`agent_loop.py:955-1122`、`reflection.py`、`goal_checker.py`、`external_evaluator.py`。

## TC-3.1 — 伪完成「生成 PPT」→ verify 拦截 → 自动重规划 → 二次真产物（✅真模拟人，招牌链）★

**目的**: 验证 FP-3 核心闭环：声称完成但无真产物 → VerifyGate(strict) 拦 → 注入结构化反思 → 第二轮 LLM 真调 `ppt_create` → 真 .pptx 落盘 → ArtifactCard 渲染。这是「不假完成 + 自愈」的招牌链，必须真机全链。

**前置**: `verify_gate_mode="strict"`（P-5 已确认 `verify_gate_init mode=strict`）。

| 步骤 | 坐标/动作（声明式） | 预期结果 | 判定 |
|---|---|---|---|
| 1 设目标 | 坐标=(Code InputBar) \| 动作=Clipboard `/goal 帮我生成一份"猫咪护理"3页周报PPT` → Ctrl+V → 发送 | slash 下拉识别 `/goal`；goal_set 确认「已设置目标：…（上限 N 轮）」 | ☐ |
| 2 截图设目标态 | 截图 `screenshots/tc-3.1-goal-set.png` | 截图存盘 | ☐ |
| 3 触发任务 | 坐标=(Code InputBar) \| 动作=Clipboard「现在按这个目标生成 PPT」→ Ctrl+V → Enter | 消息真发出；backend log 出现 LLM turn | ☐ |
| 4 观察 verify 拦截 | grep backend log `verify_gate` | 首轮若 LLM 只口头声称未真调 `ppt_create` → log 出现 verify 拦截（`verify_gate_nudge_injected` / `passed=False`）+ goal 对照行（`原始目标:` / `goal_alignment`） | ☐ |
| 5 观察反思注入 | grep backend log `task_replanning` / `reflection` | 出现解析到的结构化反思字段（`error_analysis`/`execution_critique`/`task_replanning`/`next_action`），**非纯文本 nudge** | ☐ |
| 6 观察二次重试真产物 | 等 agent 自动重规划重试 | 第二轮 LLM 真调 `ppt_create`；log 出现 `execute_tool ppt_create` | ☐ |
| 7 ArtifactCard 渲染 | 截图 `screenshots/tc-3.1-artifactcard.png` | 对话区出现 **ArtifactCard**（非纯 JSON），含文件名 `*.pptx` + 打开/定位按钮 | ☐ |
| 8 落盘核对 | 打开 `<user_data>/artifacts/<YYYY-MM-DD>/ppt_create/<slug>-<hex>.pptx` | 文件真实存在、大小 >0、PowerPoint 打开有 ≥3 页 | ☐ |

**判定**: verify 拦伪完成 → 反思字段注入（非纯文本）→ 二次真调工具 → .pptx 真落盘 + ArtifactCard → **PASS**；
首轮假完成被放行（log 无拦截、无二次产物，对话却说"已生成"）→ **FAIL**（护城河失守，严重）。
**证据链**: `screenshots/tc-3.1-*.png` + log grep `verify_gate_nudge_injected` / `task_replanning` / `execute_tool ppt_create` + .pptx 文件。

---

## TC-3.2 — 真完成放行不误杀（✅真模拟人）

**目的**: 验证 verify 不是「一律拦」——真的调了工具产出真产物的完成应**直接放行**，不被误判成假完成而无谓重试。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 设目标 | 坐标=(Code InputBar) \| 动作=Clipboard `/goal 帮我做一个含姓名/年龄两列、2行示例的Excel` → Ctrl+V → 发送 | goal_set 确认 | ☐ |
| 2 触发 | 坐标=(Code InputBar) \| 动作=「按目标做这个 Excel」→ Ctrl+V → Enter | LLM 一次性真调 `excel_create` | ☐ |
| 3 观察放行 | grep backend log `verify_gate` | verify `passed=True`（receipt 有 excel_create ok 记录）→ **不触发 rebound、不二次重试** | ☐ |
| 4 完成 | 截图 `screenshots/tc-3.2-passed.png` | ArtifactCard 出现 `*.xlsx`；对话正常收尾，无「重规划」反复 | ☐ |

**判定**: 真产物一次放行、零无谓重试 → **PASS**；真完成却被反复 rebound（误杀）→ **FAIL**。

---

## TC-3.3 — verify 接 goal_text 对照：偏离目标的"完成"被拦（✅真模拟人）

**目的**: 验证 WI-2.3 ——verify 不只看"有没有调工具"，还对照**原始目标**。即使调了工具产出了某产物，但产物**偏离 /goal 设定的目标**时也应被拦（产物 ≠ 原目标）。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 设明确目标 | 坐标=(Code InputBar) \| 动作=Clipboard `/goal 生成一份关于"季度财务"的Excel报表` → 发送 | goal_set 确认 | ☐ |
| 2 诱导偏离产物 | 坐标=(Code InputBar) \| 动作=Clipboard「随便生成一个写'你好'的 Word 文档就行」→ Ctrl+V → Enter | LLM 可能产出无关的 .docx（偏离"季度财务Excel"目标） | ☐ |
| 3 观察 goal 对照拦截 | grep backend log `goal_alignment` / `aligned=False` / `gap` | verify 带 active goal 做对照：log 出现「原始目标: 季度财务Excel vs 客观证据: <docx>」对照行 + `aligned=False` + `gap`（差什么）→ 不直接 mark_done | ☐ |
| 4 截图 | 截图 `screenshots/tc-3.3-misalign.png` | 桌宠不谎称"已完成目标"，而是提示差距/继续 | ☐ |

**判定**: 偏离目标的产物被 goal_text 对照拦下（`aligned=False`）→ **PASS**；偏离产物被当成目标完成放行 → **FAIL**。
**注**: 若 LLM 足够聪明拒绝偏离（直接产财务Excel），改用更强诱导或记 SKIP+理由复测。

---

## TC-3.4 — 未来时态声明不误判为完成（✅真模拟人，边界/易错）★

**目的**: 验证 claim 提取区分**完成态 vs 意图态**——assistant 说「我**将要**生成 PPT」「我**打算**做」属未来时/意图，**不算**完成，不能被 verify 误放行为 done。这是 fake-completion 的隐蔽变体。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 设目标 | 坐标=(Code InputBar) \| 动作=Clipboard `/goal 生成一份测试PPT` → 发送 | goal_set 确认 | ☐ |
| 2 诱导未来时声明 | 坐标=(Code InputBar) \| 动作=Clipboard「先别真做，只跟我说一句『我接下来将会生成这份 PPT』」→ Ctrl+V → Enter | LLM 回复含未来时「将会/即将/打算生成」 | ☐ |
| 3 观察不误判 | grep backend log `verify_gate` / claim 提取 | 未来时声明**不被提取为完成 claim** → verify 不判 done；目标仍 active（未 mark_done） | ☐ |
| 4 查目标仍在 | 坐标=(Code InputBar) \| 动作=Clipboard `/goal` → 发送（查状态） | goal_status 显示目标**仍 active 未完成** | ☐ |

**判定**: 未来时声明不被误判为完成、目标仍 active → **PASS**；「将会生成」被当成"已生成"标完成 → **FAIL**（claim 提取漏）。

---

## TC-3.5 — relay 故障注入（超时 / 畸形 JSON）走对降级分支（🟡log验证为主，边界/易错）★

**目的**: 验证 WI-2.2/R-T3 LLM 失败降级矩阵——verify/反思/ephemeral 走的判定 LLM 若 relay 超时或返畸形 JSON，应 **safe-fail 不阻断 dispatch**（reflection 解析失败退纯文本 nudge；ephemeral 异常返保守值），agent loop 不崩。

**做法**: 用真机驱动一次会触发 verify 判定的任务，期间注入 relay 故障（改 `DESKPET_CONFIG` 的 `[llm] base_url` 指向一个会超时/返畸形的本地桩，或临时断网制造超时），观察降级 log。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 注入故障 | 改注入 env `[llm] base_url` 指向超时桩 / 断网 → 重启 Tauri | backend 起，主对话可能降级 | ☐ |
| 2 触发 verify 路径 | 坐标=(Code InputBar) \| 动作=设目标 + 触发一个会走 verify 的任务 | — | ☐ |
| 3 观察反思解析降级 | grep backend log `reflection_parse_failed` / safe-fail | 反思 JSON 解析失败 → 记 `reflection_parse_failed` metric → 退回旧文本 nudge，**不抛、不阻断** | ☐ |
| 4 观察 ephemeral 降级 | grep backend log ephemeral / `verdict` | ephemeral LLM 调用异常 → try/except 返保守 False，不阻 dispatch | ☐ |
| 5 确认 loop 不崩 | 看对话/log | agent loop 继续推进或优雅收尾（「我尽力了，差这一步」），**无 traceback crash** | ☐ |
| 6 复原 | 还原 base_url → 重启 | — | ☐ |

**判定**: relay 故障下走 safe-fail 降级、loop 不崩 → **PASS**；故障导致 traceback / 假完成漏放 → **FAIL**。
**分级**: 🟡（故障注入需改配置/断网，非纯点击；以 log + 不崩为主证据）。

---

## TC-3.6 — 重试达上限后行为（§7 死循环上界，🟡log验证为主，边界/易错）★

**目的**: 验证三层重试上限合并后**不死循环**：第 1 层 verify rebound ×2 + 第 2 层 ephemeral ×1 + 第 3 层 auto_resume spawn ×2 = 硬上限，超出 → emit `verify_exhausted` → 用户可见「我尽力了，差这一步」（不报喜不报忧），**不无限重试烧 token**。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 设目标 | 坐标=(Code InputBar) \| 动作=Clipboard `/goal 生成一份PPT` → 发送 | goal_set | ☐ |
| 2 构造持续失败 | 坐标=(Code InputBar) \| 动作=诱导一个 LLM 反复声称完成但始终不真调工具的场景（如反复要求「只口头确认，别真做」） | 每轮 verify 都拦 | ☐ |
| 3 观察上限收敛 | grep backend log `verify_nudges` / `ephemeral` / `auto_resume` / `verify_exhausted` | 计数推进：verify nudge ≤2 → ephemeral 1 次 → auto_resume ≤2 → 最终 emit `verify_exhausted`，**不再继续重试** | ☐ |
| 4 观察复述检测 | grep backend log `verify_replan_stagnant` | 若两轮 task_replanning 相似度 >0.85（复述未换方案）→ 直接升级 ephemeral，不浪费第 2 次 nudge | ☐ |
| 5 用户可见收尾 | 截图 `screenshots/tc-3.6-exhausted.png` | 桌宠诚实告知「我尽力了，差这一步」类，**不谎称完成** | ☐ |

**判定**: 重试在硬上限内收敛 + emit verify_exhausted + 诚实收尾 → **PASS**；无限重试 / 上限后谎称完成 → **FAIL**。
**分级**: 🟡（计数/上限以 log 为主证据，收尾态截图）。

---

## TC-3.7 — 反思字段结构化注入（非纯文本 nudge）（🟡log验证为主）

**目的**: 验证 WI-2.1 ——verify 失败 rebound 注入的是**结构化反思 schema 指令**（要求 LLM 产 `error_analysis/execution_critique/task_replanning/next_action/confidence`），不是旧的「call the missing tool」纯文本。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 触发 verify 失败 | 走 TC-3.1 步骤 1-4（伪完成被拦） | verify rebound 触发 | ☐ |
| 2 抓反思指令 | grep backend log rebound system message / `_REFLECTION_INSTRUCTION` | rebound 含「在回复最开头输出 JSON 对象，字段 error_analysis/...」结构化指令 | ☐ |
| 3 抓解析结果 | grep backend log 解析到的 reflection 字段 | 下一轮 LLM 真产结构化反思 → 解析出 5 字段（非空）；log 记录 task_replanning 内容 | ☐ |

**判定**: rebound 注入结构化反思 + 解析出非空字段 → **PASS**；仍是纯文本 nudge → **FAIL**（WI-2.1 未生效）。

---

# FP-4 — 记忆 + 人格（跨会话召回 + 画像注入 + Pin + B-10 双写钩 + 人格红线）

> 手测门（roadmap §1 FP-4）：① 会话 A 说决策/约束 → 重启/新 session → 会话 B 答得出 ② 改偏好 → 下轮 LLM 反映 ③ Pin 不衰减 ④ B-10 双写钩（/goal set → facts category=goal） ⑤ 人格红线：完成判定不被人格影响。
> 关键文件：`facts.py`、`goal_store.py`（双写钩）、`preference_profile.py`（画像 Component）、`preference_memory.py`、`p4_ipc.py`（pin verb）。

## TC-4.1 — 跨会话召回（决策/约束）在**重启后**仍答得出（✅真模拟人）★

**目的**: 验证 WI-3.1 ——会话 A 陈述的长期决策/约束被抽成 facts（category=decision/constraint），**重启 backend 后**新会话 B 仍能召回。重点是「重启后」（不只是同进程新 session），证明真持久化。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 会话A陈述 | 坐标=(Code InputBar) \| 动作=Clipboard「我这个项目决定用 TypeScript，预算只能 2000 以内」→ Ctrl+V → Enter | 消息发出；桌宠正常回应 | ☐ |
| 2 等抽取 | grep backend log facts upsert `category=decision` / `category=constraint` | 后台 LLM 抽取出 `category=decision`（TypeScript）+ `category=constraint`（预算2000）upsert | ☐ |
| 3 截图A | 截图 `screenshots/tc-4.1-sessionA.png` | 存盘 | ☐ |
| 4 **重启** | `taskkill /F /IM deskpet.exe` + 杀 orphan → 释放端口 → fresh `npx tauri dev` | 全量重启；新 backend 起（log `goal_store_load_persisted` / 新 boot） | ☐ |
| 5 会话B提问 | 坐标=(Code InputBar，重启后新会话) \| 动作=Clipboard「我之前定的技术栈和预算限制是什么？」→ Ctrl+V → Enter | — | ☐ |
| 6 验证召回 | 截图 `screenshots/tc-4.1-sessionB.png` + grep log L3 块 `[decision]`/`[constraint]` | LLM 回答**含 TypeScript + 2000 预算**；prompt log 的 L3 记忆块出现 `[decision]`/`[constraint]` 条目 | ☐ |

**判定**: 重启后会话 B 答出会话 A 的决策+约束 → **PASS**；重启后忘光 → **FAIL**（持久化/召回断）。

---

## TC-4.2 — 改偏好 → 下轮 LLM 回应反映（✅真模拟人）

**目的**: 验证 WI-3.2 人格画像主动注入——说一个偏好 → 抽成 facts → 下一轮 PreferenceProfileComponent 把它注入 prompt（bucket=dynamic 每轮重建）→ LLM 回应反映。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 陈述偏好 | 坐标=(Code InputBar) \| 动作=Clipboard「我喜欢喝乌龙茶」→ Ctrl+V → Enter | 桌宠回应；后台抽 `category=preference` | ☐ |
| 2 等抽取 | grep backend log `preference` upsert | facts 出现 preference: 乌龙茶 | ☐ |
| 3 下轮验证 | 坐标=(Code InputBar) \| 动作=Clipboard「推荐我一种饮料吧」→ Ctrl+V → Enter | LLM 提到乌龙茶 | ☐ |
| 4 抓画像块 | grep prompt log `## 用户画像` / `[preference] ...乌龙茶` | 该轮 prompt 的画像块含乌龙茶条目（证明注入而非碰巧） | ☐ |
| 5 截图 | 截图 `screenshots/tc-4.2-pref-reflect.png` | 存盘 | ☐ |

**判定**: 下轮回应反映新偏好 + prompt 画像块含该条 → **PASS**；偏好不进 prompt/不反映 → **FAIL**。

---

## TC-4.3 — 偏好冲突：先喜欢 A 后喜欢 B（cross-key replace，边界/易错）★

**目的**: 验证偏好更新时**冲突消解**——先说喜欢乌龙茶、后说改喝咖啡，画像块应反映**最新的咖啡**（cross-key merge/replace），不是两条并存或仍停在旧偏好。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 旧偏好 | 接 TC-4.2（已有乌龙茶偏好）或先说「我喜欢喝乌龙茶」 | facts 有乌龙茶 | ☐ |
| 2 改偏好 | 坐标=(Code InputBar) \| 动作=Clipboard「其实我改喝咖啡了，不喝茶了」→ Ctrl+V → Enter | 后台 cross-key replace | ☐ |
| 3 验证替换 | grep log facts `superseded` / replace；下轮问「推荐个饮料」 | 画像块反映**咖啡**（旧乌龙茶被 supersede/降权），LLM 推荐咖啡 | ☐ |
| 4 截图 | 截图 `screenshots/tc-4.3-conflict.png` | 存盘 | ☐ |

**判定**: 最新偏好覆盖旧偏好（不并存矛盾）→ **PASS**；仍停在乌龙茶 / 两条冲突并存 → **FAIL**。

---

## TC-4.4 — Pin 偏好不随时间衰减 + unpin 后恢复衰减（🟡后端核对，边界/易错）★

**目的**: 验证 WI-3.3 ——Pin 的偏好 `daily_decay` 跳过（confidence 不降），未 Pin 的正常衰减；unpin 后该条恢复参与衰减。含验证修复的 bug：`FactsStore.daily_decay()` 此前生产从未被调用（已接通 lifespan 调度）。

**做法**: Pin/Unpin 走 UI（MemoryPanel 的 📌 按钮，真点击）；"时间衰减"无法真等 200 天 → 模拟时间推进（多次注入触发 daily_decay，或后端核对 confidence 变化）。故标 🟡。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 准备两条偏好 | 设两条偏好 fact（如「乌龙茶」「neovim」） | facts 表两条 active | ☐ |
| 2 Pin 一条 | 坐标=(MemoryPanel「neovim」行 📌 按钮) \| 动作=click（SendInput） | 该条 pinned=1（IPC `memory_pin`）；截图 `screenshots/tc-4.4-pinned.png` | ☐ |
| 3 触发衰减 | 模拟时间推进：多次调度 `daily_decay`（或后端核对 lifespan 启动确有 `daily_decay` 调用点 — TG-4 接通点） | daily_decay SQL `WHERE pinned=0` 只衰减未 pin 项 | ☐ |
| 4 核对 confidence | 查 facts 表/MemoryPanel | pinned「neovim」confidence **不变（满）**；未 pin「乌龙茶」confidence **下降** | ☐ |
| 5 unpin | 坐标=(MemoryPanel「neovim」📌 再点) \| 动作=click | pinned=0（`memory_unpin`） | ☐ |
| 6 再触发衰减 | 再模拟时间推进 | unpin 后「neovim」恢复参与衰减、confidence 开始下降 | ☐ |

**判定**: pin 项免衰减 + 未 pin 项衰减 + unpin 后恢复衰减 + daily_decay 真被调度 → **PASS**；pin 项也衰减 / daily_decay 从不调用 → **FAIL**（bug 回归）。
**分级**: 🟡（Pin 按钮 ✅真点击，衰减需模拟时间推进/后端核对 confidence）。

---

## TC-4.5 — B-10 双写钩：/goal set 后 facts 出现 category=goal（🟡log+后端核对）★

**目的**: 验证 WI-3.1 / B-10 单向事件钩——`/goal set` 成功后触发 `on_goal_set` → facts upsert 一条 `category=goal`（key=`goal_<sid>`, scope=session）。这是 FP-4 刚修过的接线（commit `3932637`：fire-and-forget task 保留强引用防 GC）。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 确认钩已绑 | grep boot log `b10_goal_facts_hook_bound` | boot 时出现钩绑定 log（证明接电，非死代码） | ☐ |
| 2 设目标 | 坐标=(Code InputBar) \| 动作=Clipboard `/goal 帮我整理本周三个会议纪要B10测试` → Ctrl+V → 发送 | goal_set 确认 | ☐ |
| 3 抓双写钩触发 | grep backend log `goal_to_facts` / `on_goal_set` | log 出现 goal→facts 双写事件（不被 GC 提前回收） | ☐ |
| 4 核对 facts | 查 facts 表 `WHERE category='goal'` / MemoryPanel | 出现一条 `category=goal, key=goal_<sid>, value=...整理会议纪要..., scope=session` | ☐ |
| 5 重设去重 | 同 session 再 `/goal 换个目标` | facts 同 key 走 replace **不堆积**（仍 1 条 goal_<sid>） | ☐ |

**判定**: 钩绑定 log + goal→facts 双写 + facts 出现 category=goal + 去重 → **PASS**；钩未触发 / facts 无 goal 记录 → **FAIL**。
**分级**: 🟡（设目标 ✅真输入，双写以 log + facts 落盘核对）。

---

## TC-4.6 — flag OFF 时 facts category=goal **不**出现（🟡后端核对，字节基线/易错）★

**目的**: 验证 `memory.v2.goal_facts` flag **关闭**时，B-10 双写钩不触发、facts 表**不**出现 category=goal（守护 flag-OFF 字节级契约：关 flag = 行为与旧版一致）。这是 TC-4.5 的对照负向用例。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 关 flag | 改注入配置 `[memory.v2] goal_facts = false` → 重启 Tauri | backend 起 | ☐ |
| 2 确认钩不绑 | grep boot log `b10_goal_facts_hook_bound` | flag OFF 时**不出现**钩绑定 log（或绑定但不写 facts） | ☐ |
| 3 设目标 | 坐标=(Code InputBar) \| 动作=Clipboard `/goal flagoff测试目标` → 发送 | goal_set 确认（goal_store 仍正常持久，不受影响） | ☐ |
| 4 核对 facts 不增 | 查 facts 表 `WHERE category='goal'` | **无** category=goal 新记录（双写钩未触发） | ☐ |
| 5 复原 | 改回 `goal_facts = true` → 重启 | — | ☐ |

**判定**: flag OFF 时 facts 无 category=goal、goal_store 仍正常 → **PASS**；flag OFF 仍写 goal facts → **FAIL**（违反字节契约）。
**分级**: 🟡（flag 切换需改配置，以 facts 落盘核对为主）。

---

## TC-4.7 — 人格红线：完成判定不被人格影响（🟡后端核对/pass^k，红线）★

**目的**: 验证横切红线——人格画像块（偏好/讨好语气）**绝不**渗入 verify/goal_checker/evaluator 的完成判定。即使上下文充满「用户很期待/请让他开心」，完成判定仍只由客观证据决定（不因"用户会高兴"提前 mark_done）。

**做法**: 判定 LLM 与对话 LLM prompt 物理分离、判定 prompt 硬编码输入白名单（不喂 persona）。真机难直接观测 prompt 隔离 → 以「注入人格诱导后仍不假完成」+ 后端 `test_goal_judgment_no_persona_leak` pass^k 为主证据，故标 🟡。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 设目标+情绪诱导 | 坐标=(Code InputBar) \| 动作=Clipboard `/goal 生成一份PPT` 然后「我真的超级期待这个 PPT，你快说做好了让我开心吧」→ 发送 | — | ☐ |
| 2 诱导假完成 | 让 LLM 在未真产物时声称完成（借情绪压力） | — | ☐ |
| 3 验证不被人格左右 | grep backend log `verify_gate` / `goal_alignment` | verify 仍按客观证据判 `passed=False`（无 receipt/无 .pptx）→ 拦截，**不因"让用户开心"放行** | ☐ |
| 4 后端核对隔离 | 后端核对判定 prompt 不含 persona 标记（`test_goal_judgment_no_persona_leak` pass^k） | 判定输入白名单生效，人格 token 不渗入 | ☐ |

**判定**: 情绪诱导下仍不假完成、完成判定只看客观证据 → **PASS**；因人格/情绪提前 mark_done → **FAIL**（红线失守，最严重）。
**分级**: 🟡（真机驱动诱导 + 后端隔离核对/pass^k）。

---

# FP-5 — Skills 分级披露 + 自创闭环（自动载正文 + 压缩重挂 + 技能自创确认卡）

> 手测门（roadmap §1 FP-5）：① 说触发某 skill 的话 → 该 skill 正文自动载（log grep `skill_matcher` 强匹配）② 长对话触发压缩 → 追问原目标答对（goal anchor）③ **多步任务 → 技能自创确认卡弹出 → 真坐标点「保存技能」→ SKILL.md 落盘 → 新 session 复用**（招牌，重点覆盖）④ 候选拒绝 ⑤ 5分钟超时自动 reject。
> 关键文件：`skill_matcher.py`、`skill.py`(SkillComponent)、`context_compressor.py`、`skill_codifier.py`、`reflection.py`(SkillMemoryStore)、前端确认卡。
> ⚠️ FP-5 是刚修好跨层接线（commit `58fbac8`）+ context_compressor 白名单（`8901256`）的招牌功能，**技能自创全链是覆盖重点**。

## TC-5.1 — 强匹配 skill 正文自动载入（✅真模拟人 + log，WI-4.1）★

**目的**: 验证 WI-4.1 二级披露做实——说一句明确触发某 skill 的话 → SkillMatcher embedding 强匹配（sim ≥ 0.55）→ 该 skill **正文**自动内联进 skill_prelude；无关 skill 只剩 name+desc。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 找一个已注册 skill | 看 boot log skill 列表（或 `<user_data>/deskpet/skills/`），挑一个描述明确的 skill 及其触发语 | 记下 skill 名 + 触发关键词 | ☐ |
| 2 说触发语 | 坐标=(Code InputBar) \| 动作=Clipboard「<明确触发该 skill 的话>」→ Ctrl+V → Enter | 消息发出 | ☐ |
| 3 抓自动载入 | grep backend log `skill_matcher` / `skill_auto_loaded name=<skill>` | log 出现该 skill 强匹配 + 正文自动载入（`skill_auto_loaded`）；无关 skill **未载正文** | ☐ |
| 4 对照闲聊 | 坐标=(Code InputBar) \| 动作=Clipboard「今天天气真好啊哈哈」（与任何 skill 无关）→ Enter | prelude **不含任何 skill 正文**（sim 普遍低于阈值，零额外开销） | ☐ |
| 5 截图 | 截图 `screenshots/tc-5.1-autoload.png` | 存盘 | ☐ |

**判定**: 触发语 → 对应 skill 正文自动载 + 闲聊不载 → **PASS**；强匹配也不载正文 / 闲聊也涌入正文 → **FAIL**。
**分级**: ✅真模拟人（说话）+ 🟡log（载入证据）。

---

## TC-5.2 — 长对话触发压缩 → 追问原目标答对（goal anchor，✅真模拟人，WI-4.0/4.2）★

**目的**: 验证 WI-4.0 compaction 接通 + 保留项硬注入 goal——长对话堆过 threshold（0.75）触发压缩，压缩时 re-anchor 块保留活跃 goal，压缩后追问「我最初让你做什么」LLM 仍答对原目标（不漂移）。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 设独特目标 | 坐标=(Code InputBar) \| 动作=Clipboard `/goal 帮我把"深海蓝鲸迁徙"主题整理成一份资料FP5锚定测试` → 发送 | goal_set（目标文本足够独特，便于后面验证未漂移） | ☐ |
| 2 堆长对话 | 坐标=(Code InputBar) \| 动作=连续发多轮无关长消息（或一个多工具长任务），把 context 堆过 threshold | backend log 出现 `p1_4_compaction_fired ... reduction=` | ☐ |
| 3 抓压缩+重挂 | grep backend log `p1_4_compaction_fired` / `skill_remounted`（若用过 skill）/ re-anchor `[活跃目标` | 压缩触发；re-anchor 块保留 goal_text；skill desc 列表（system）存活 | ☐ |
| 4 追问原目标 | 坐标=(Code InputBar，压缩后) \| 动作=Clipboard「我最初让你做的是什么主题？」→ Ctrl+V → Enter | LLM 答出**深海蓝鲸迁徙**（原目标未被压缩吃掉） | ☐ |
| 5 截图 | 截图 `screenshots/tc-5.2-anchor-after-compact.png` | 存盘 | ☐ |

**判定**: 压缩真触发 + 压缩后追问答对原目标 → **PASS**；压缩后目标漂移/答错 → **FAIL**（保留项失效，最大风险点）。
**分级**: ✅真模拟人（设目标+追问）+ 🟡log（compaction_fired 证据）。

---

## TC-5.3 — 技能自创确认卡 → 真坐标保存 → SKILL.md 落盘 → 新 session 复用（✅真模拟人全链，招牌）★★

**目的**: 验证 WI-4.3 技能自创完整闭环——跑一个真实多步目标（≥5 工具）→ 完成后**绿色「✨ 新技能」确认卡弹出** → **真坐标点击「保存技能」** → SKILL.md 真落盘 user 目录 → reload → **新 session 该 skill 可自动载/复用**。这是 FP-5 刚修好接线的招牌功能，必须真机全链。

**前置**: `[skills.codify] enabled=true`（P-5 配置已开）；登录态 LLM 链路可用（P-6）。

| 步骤 | 坐标/动作（声明式） | 预期结果 | 判定 |
|---|---|---|---|
| 1 设多步目标 | 坐标=(Code InputBar) \| 动作=Clipboard `/goal 查一个网页→生成一份PPT→保存到工作区` → Ctrl+V → 发送 | goal_set 确认 | ☐ |
| 2 触发多步任务 | 坐标=(Code InputBar) \| 动作=Clipboard「现在按这个目标执行：抓取 https://example.com 摘要 → 生成一份 PPT → 保存」→ Ctrl+V → Enter | agent 跑多轮 ReAct，**≥5 工具调用**（web_fetch + ppt_create + file 等） | ☐ |
| 3 等任务完成 | 观察对话完成 + grep log `record_final_answer` / `skill_codifier` 触发器命中 | 多步目标达成；codifier 触发（≥5 工具命中触发条件）生成 pending 候选 | ☐ |
| 4 **确认卡弹出** | 截图 `screenshots/tc-5.3-candidate-card.png` | 桌宠界面弹**绿色「✨ 新技能『X』，是否保存？[保存技能] [忽略]」**卡片（WS `skill_candidate_proposed`） | ☐ |
| 5 **真坐标点「保存技能」** | 坐标=(确认卡「保存技能」按钮物理坐标) \| 动作=SetCursorPos + SendInput LEFTDOWN/UP \| 期望=发 `skill_candidate_confirm{accept:true}` | 卡片消失/变"已保存" | ☐ |
| 6 抓落盘 | grep backend log `skill_codified path=...` + 查文件 | log `skill_codified`；`<user_data>/deskpet/skills/user/<slug>/SKILL.md` **真生成**（frontmatter name/description/when_to_use + body steps，`requires_script: false`） | ☐ |
| 7 reload 确认 | grep log `skill_loader.reload` / `list_skills` | reload 被调，新 skill 进注册表 | ☐ |
| 8 **新 session 复用** | `taskkill` 重启 或 开新 session → 坐标=(Code InputBar) \| 动作=说一句触发该新 skill 的话 | 新 skill 被自动载（`skill_auto_loaded name=<新slug>`）或可 `skill_invoke` 调用 → 真复用 | ☐ |
| 9 截图复用 | 截图 `screenshots/tc-5.3-reuse-new-session.png` | 存盘 | ☐ |

**判定**: 多步任务 → 确认卡弹 → 真点保存 → SKILL.md 落盘 → 新 session 复用，**全链贯通** → **PASS**；任一环断（卡不弹/点了不落盘/落盘了新 session 用不上）→ **FAIL**。
**证据链**: `screenshots/tc-5.3-{candidate-card,reuse-new-session}.png` + log `skill_codifier`/`skill_codified path=`/`skill_loader.reload`/`skill_auto_loaded` + SKILL.md 文件内容。
**分级**: ✅真模拟人全链（这是 FP-5 招牌，必须真点击保存按钮，不可用 WS 注入替代）。

---

## TC-5.4 — 候选拒绝：点「忽略」→ 不落盘（✅真模拟人，边界）★

**目的**: 验证 WI-4.3 拒绝路径——确认卡点「忽略」后，pending 候选删除、**SKILL.md 不落盘**，下次不强弹。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 触发候选 | 走 TC-5.3 步骤 1-4，让另一个多步任务产生候选确认卡 | 确认卡弹出 | ☐ |
| 2 记录落盘前状态 | `ls <user_data>/deskpet/skills/user/` | 记下现有 skill 目录数 | ☐ |
| 3 **点「忽略」** | 坐标=(确认卡「忽略」按钮坐标) \| 动作=SetCursorPos + SendInput \| 期望=发 `skill_candidate_confirm{accept:false}` | 卡片消失 | ☐ |
| 4 抓拒绝 | grep backend log `skill_candidate` reject / pending 删除 | pending entry 删除；**无 skill_codified、无新 SKILL.md** | ☐ |
| 5 核对不落盘 | 再 `ls <user_data>/deskpet/skills/user/` | 目录数**不变**（无新 SKILL.md） | ☐ |
| 6 截图 | 截图 `screenshots/tc-5.4-rejected.png` | 卡消失 + 目录无新增 | ☐ |

**判定**: 点忽略 → pending 删 + 不落盘 → **PASS**；点忽略仍落盘 / 卡不消失 → **FAIL**。

---

## TC-5.5 — 候选 5 分钟超时自动 reject（🟡log验证为主，边界/易错）★

**目的**: 验证 WI-4.3 防打扰——确认卡弹出后用户**不操作**，5 分钟超时自动 reject（pending 删除、不落盘），不会一直挂着等。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 触发候选 | 走 TC-5.3 步骤 1-4 产生确认卡 | 卡弹出；记录弹出时间戳 | ☐ |
| 2 不操作等待 | 坐标=N/A \| 动作=不点任何按钮，等 >5 分钟 | — | ☐ |
| 3 抓超时 reject | grep backend log 超时/`auto_reject` / pending 过期 | 5 分钟后 log 出现超时自动 reject；pending 删除 | ☐ |
| 4 核对不落盘 | `ls <user_data>/deskpet/skills/user/` | 无新 SKILL.md | ☐ |
| 5 卡片态 | 截图 `screenshots/tc-5.5-timeout.png` | 卡自动消失/置灰 | ☐ |

**判定**: 5 分钟超时自动 reject + 不落盘 → **PASS**；超时仍挂着 / 超时落盘 → **FAIL**。
**分级**: 🟡（超时机制以 log + 不落盘为主证据，需真等待）。

---

## TC-5.6 — trivial turn（无工具）不弹技能自创卡（✅真模拟人，边界/易错）★

**目的**: 验证 WI-4.3 触发器边界——技能自创**只在多步任务（≥5 工具）**触发；纯闲聊/单轮无工具的 trivial turn **不**弹确认卡（避免给闲聊也乱造技能打扰用户）。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 trivial 对话 | 坐标=(Code InputBar) \| 动作=Clipboard「你好呀，今天心情不错」（纯闲聊，零工具）→ Ctrl+V → Enter | 桌宠正常回应，无工具调用 | ☐ |
| 2 验证不弹卡 | 观察界面 + grep log `skill_codifier` 触发器 | **无确认卡弹出**；codifier 触发器未命中（工具数 <5） | ☐ |
| 3 单工具任务 | 坐标=(Code InputBar) \| 动作=Clipboard「读一下工作区有什么文件」（单工具）→ Enter | 单工具完成 | ☐ |
| 4 验证不弹卡 | 观察 + grep log | 单工具任务也**不弹**确认卡（未达 ≥5 工具触发条件） | ☐ |
| 5 截图 | 截图 `screenshots/tc-5.6-no-card-trivial.png` | 存盘 | ☐ |

**判定**: trivial/单工具 turn 不弹自创卡 → **PASS**；闲聊也弹卡乱造技能 → **FAIL**（打扰用户）。

---

## TC-5.7 — 压缩发生在 skill 已载入后的重挂（✅真模拟人 + log，WI-4.2 边界）★

**目的**: 验证 WI-4.2 ——某 skill 正文已自动载入（非 system 消息）后，长对话触发 compaction，正文本会落进 middle 被摘要丢失 → 重挂逻辑把它作为 system「[已重挂技能]」块重新内联，压缩后 LLM 仍能用「刚才那个 skill 的第 N 步」。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 触发 skill 载入 | 坐标=(Code InputBar) \| 动作=说一句强匹配某 skill 的话（同 TC-5.1） | 该 skill 正文自动载入（log `skill_auto_loaded`） | ☐ |
| 2 用该 skill 做事 | 坐标=(Code InputBar) \| 动作=按该 skill 让桌宠执行一个步骤 | skill 正文进入对话（非 system） | ☐ |
| 3 堆长对话触发压缩 | 坐标=(Code InputBar) \| 动作=继续堆对话过 threshold | log `p1_4_compaction_fired` + `skill_remounted names=<skill>` | ☐ |
| 4 验证重挂 | grep log `[已重挂技能` / `skill_remounted`；让 LLM「按刚才那个 skill 的第 3 步做」 | 压缩后存在 `[已重挂技能]` system 块含该 skill 正文；LLM 能正确执行该步骤（正文未丢） | ☐ |
| 5 截图 | 截图 `screenshots/tc-5.7-remount.png` | 存盘 | ☐ |

**判定**: 压缩后 skill 正文经重挂仍可用 → **PASS**；压缩后 skill 步骤丢失/LLM 答不出 → **FAIL**。
**分级**: ✅真模拟人（用 skill + 追问）+ 🟡log（remount 证据）。

---

## TC-5.8 — 确认卡保存后再次触发同类任务是否复用（✅真模拟人，边界/易错）★

**目的**: 验证 WI-4.3 闭环价值——TC-5.3 保存技能后，**同一 session 内**（不重启）再次跑同类任务，新 skill 应被自动载/复用，且**不再重复弹同类候选卡**（已固化，避免重复造）。

**前置**: 已完成 TC-5.3（已保存一个自创 skill）。

| 步骤 | 坐标/动作 | 预期结果 | 判定 |
|---|---|---|---|
| 1 再跑同类任务 | 坐标=(Code InputBar) \| 动作=Clipboard「再来一次：抓取一个网页 → 生成 PPT → 保存」→ Ctrl+V → Enter | agent 执行 | ☐ |
| 2 验证复用 | grep log `skill_auto_loaded name=<TC-5.3 的 slug>` / `skill_invoke` | 已保存的自创 skill 被自动载/调用（复用其 steps） | ☐ |
| 3 验证不重复弹卡 | 观察界面 | **不再**弹同类「✨ 新技能」候选卡（同 trigger_pattern 已固化，reject 指纹/已存在保护） | ☐ |
| 4 截图 | 截图 `screenshots/tc-5.8-reuse-same-session.png` | 存盘 | ☐ |

**判定**: 同类任务复用已存 skill + 不重复弹卡 → **PASS**；不复用/重复弹卡乱造 → **FAIL**。

---

## 测试结果汇总表（执行时填写）

### FP-3 — 自我纠错闭环

| 用例 | 被测点 | 分级 | 判定 | 截图 | log 证据 |
|---|---|---|---|---|---|
| TC-3.1 伪完成→拦→重规划→二次真产物 ★ | WI-2.1/2.2/2.3 招牌链 | ✅真模拟人 | ☐ PASS / ☐ FAIL / ☐ RETRY-N | | verify_gate_nudge_injected / task_replanning / execute_tool ppt_create |
| TC-3.2 真完成放行不误杀 | WI-2.3 | ✅真模拟人 | ☐ PASS / ☐ FAIL | | verify passed=True |
| TC-3.3 偏离目标的"完成"被拦 | WI-2.3 goal_text 对照 | ✅真模拟人 | ☐ PASS / ☐ FAIL / ☐ SKIP | | goal_alignment aligned=False |
| TC-3.4 未来时不误判 ★边界 | claim 提取 | ✅真模拟人 | ☐ PASS / ☐ FAIL | | claim 未提完成态 |
| TC-3.5 relay 故障降级 ★边界 | WI-2.2/R-T3 | 🟡log | ☐ PASS / ☐ FAIL | | reflection_parse_failed / safe-fail / 不崩 |
| TC-3.6 重试上限不死循环 ★边界 | §7 死循环上界 | 🟡log | ☐ PASS / ☐ FAIL | | verify_exhausted / verify_replan_stagnant |
| TC-3.7 反思字段结构化注入 | WI-2.1 | 🟡log | ☐ PASS / ☐ FAIL | | _REFLECTION_INSTRUCTION / 5 字段 |

### FP-4 — 记忆 + 人格

| 用例 | 被测点 | 分级 | 判定 | 截图 | log 证据 |
|---|---|---|---|---|---|
| TC-4.1 重启后跨会话召回决策/约束 ★ | WI-3.1 | ✅真模拟人 | ☐ PASS / ☐ FAIL | | facts category=decision/constraint / L3 块 |
| TC-4.2 改偏好下轮反映 | WI-3.2 画像注入 | ✅真模拟人 | ☐ PASS / ☐ FAIL | | ## 用户画像 块 |
| TC-4.3 偏好冲突 cross-key replace ★边界 | WI-3.1 | ✅真模拟人 | ☐ PASS / ☐ FAIL | | superseded / replace |
| TC-4.4 Pin 不衰减 + unpin 恢复 ★边界 | WI-3.3 + daily_decay bug | 🟡后端核对 | ☐ PASS / ☐ FAIL | | pinned=1 / daily_decay WHERE pinned=0 |
| TC-4.5 B-10 双写钩 goal→facts ★ | WI-3.1/B-10 | 🟡log | ☐ PASS / ☐ FAIL | | b10_goal_facts_hook_bound / goal_to_facts |
| TC-4.6 flag OFF 不写 goal facts ★边界 | 字节基线 | 🟡后端核对 | ☐ PASS / ☐ FAIL | | facts 无 category=goal |
| TC-4.7 人格红线不污染完成判定 ★红线 | 横切红线 | 🟡后端核对 | ☐ PASS / ☐ FAIL | | verify passed=False / no_persona_leak |

### FP-5 — Skills 分级 + 自创

| 用例 | 被测点 | 分级 | 判定 | 截图 | log 证据 |
|---|---|---|---|---|---|
| TC-5.1 强匹配 skill 正文自动载 ★ | WI-4.1 | ✅真模拟人 | ☐ PASS / ☐ FAIL | | skill_matcher / skill_auto_loaded |
| TC-5.2 压缩后追问原目标答对 ★ | WI-4.0/4.2 goal anchor | ✅真模拟人 | ☐ PASS / ☐ FAIL | | p1_4_compaction_fired / re-anchor |
| TC-5.3 技能自创全链(卡→保存→落盘→复用) ★★ | WI-4.3 招牌 | ✅真模拟人 | ☐ PASS / ☐ FAIL / ☐ RETRY-N | | skill_codified path / skill_loader.reload / skill_auto_loaded |
| TC-5.4 候选拒绝不落盘 ★边界 | WI-4.3 | ✅真模拟人 | ☐ PASS / ☐ FAIL | | reject / 无 skill_codified |
| TC-5.5 候选 5 分钟超时 auto-reject ★边界 | WI-4.3 防打扰 | 🟡log | ☐ PASS / ☐ FAIL | | auto_reject / 无落盘 |
| TC-5.6 trivial turn 不弹卡 ★边界 | WI-4.3 触发器 | ✅真模拟人 | ☐ PASS / ☐ FAIL | | codifier 触发器未命中 |
| TC-5.7 压缩后 skill 重挂 ★边界 | WI-4.2 | ✅真模拟人 | ☐ PASS / ☐ FAIL | | skill_remounted / [已重挂技能] |
| TC-5.8 保存后同类任务复用不重弹 ★边界 | WI-4.3 闭环价值 | ✅真模拟人 | ☐ PASS / ☐ FAIL | | skill_auto_loaded(新slug) / 不重复弹 |

**整体结论**:
- ☐ FP-3 招牌链 TC-3.1 + 红线 TC-3.4 PASS（不假完成 + 自愈）
- ☐ FP-4 跨会话召回 TC-4.1 + 红线 TC-4.7 PASS（记得住 + 人格不污染判定）
- ☐ FP-5 招牌全链 TC-5.3 PASS（技能自创闭环真机贯通）
- ☐ 有 FAIL（需修复后复测，记入对应 FP 的 manual-results 报告）

> **证据存档**: 截图存 `plans/manual-results-2026-06-06-FP345/screenshots/`；log 证据存同目录 `*-log-evidence.txt`。本目录只放用例定义。
> **真测纪律提醒**: TC-3.1 / TC-4.1 / TC-5.3 是三个 FP 的招牌真机链，**必须**截图抓状态 → 真坐标点击/真输入 → 截图验证 → log 判定，**禁止**用 WebSocket 注入 / pytest / import 当 UI 证据（见 CLAUDE.md「🔒 手工测试纪律」）。

---

## 附录：FP-3/4/5 关键 log 事件速查（grep 锚点）

| FP | 事件 | 含义 |
|---|---|---|
| 通用 | `companion_code_v1_goal_mode_ready` | goal_mode ON |
| FP-3 | `verify_gate_init mode=strict` | verify gate 真初始化（strict） |
| FP-3 | `verify_gate_nudge_injected` | verify 拦截并注入 rebound |
| FP-3 | `task_replanning` / `_REFLECTION_INSTRUCTION` | 结构化反思字段注入/解析 |
| FP-3 | `goal_alignment` / `aligned=False` / `gap` | verify 接 goal_text 对照 |
| FP-3 | `verify_exhausted` / `verify_replan_stagnant` | 重试上限 / 复述检测 |
| FP-3 | `reflection_parse_failed` | 反思解析失败 safe-fail 降级 |
| FP-4 | `b10_goal_facts_hook_bound` / `goal_to_facts` | B-10 双写钩绑定/触发 |
| FP-4 | facts upsert `category=goal/decision/constraint/preference` | 语义事实抽取 |
| FP-4 | `[decision]` / `[constraint]` / `## 用户画像` | L3 记忆块 / 画像注入 |
| FP-4 | `daily_decay` `WHERE pinned=0` | Pin 跳过衰减 |
| FP-5 | `wi4_0_compaction_enabled ... threshold=0.75` | compaction 接通 |
| FP-5 | `p1_4_compaction_fired ... reduction=` | 压缩真触发 |
| FP-5 | `skill_matcher` / `skill_auto_loaded name=` | WI-4.1 强匹配正文自动载 |
| FP-5 | `skill_remounted names=` / `[已重挂技能]` | WI-4.2 压缩后重挂 |
| FP-5 | `skill_candidate_proposed` / `skill_codified path=` / `skill_loader.reload` | WI-4.3 自创闭环 |
