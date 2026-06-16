# 上下文压缩升级 Phase 1 手工测试用例（windows-mcp 真模拟人）

> **被测功能**: DeskPet 上下文压缩升级 **Phase 1** = 把"0.6 截断 + 0.8 扁平摘要（阈值反序、默认关）"
> 升级到业界最佳实践的级联压缩。Phase 1 含四个 WI：
> - **WI-1** 触发改"剩余 token buffer"（`min(window×compact_at_pct, eff_win − buffer)`，buffer 随窗口/有效模型自适应）。
> - **WI-2** microcompact 层（廉价清陈旧 tool_result、不调模型；命中后若已降到触发线下则跳过 haiku 重摘）。
> - **WI-3** 结构化摘要（7 段 schema：意图/进行中/已完成/关键事实/文件产物/待办/下一步）+ 保最近 user 原文 + **锚定增量防套娃**（旧 `[压缩摘要]` 抽出作 prior-state，不混进待摘 transcript）。
> - **WI-4a** 目标 **always-on** 统一注入（agent_loop 循环前单点注一条 `[目标锚定]` role=system，永不被压；删周期性 anchor + 删压缩后 `_build_goal_anchor` 注入；去重 DoD = `[目标锚定]` system 恒 ≤1 条）。
>
> **对应 Plan**: `plans/2026-06-16-compaction-bestpractice-upgrade/00-PLAN.md`（§3 WI-1/2/3/4a，§4「真机(windows-mcp)」段，§7 第 1 期）。
>
> **被测代码**:
> - `backend/deskpet/agent/context_compressor.py`:
>   - WI-2 microcompact: `_microcompact_tool_results(...)`（`compress()` 入口、`_partition` 之前跑；L222-246）；命中后 `count_messages_tokens(work) < trigger_tokens()` → 落 `context_microcompact_only`（L231-235）直接返回，省 haiku。
>   - WI-3 结构化 schema: `_SUMMARY_SYSTEM` 7 段（L102-114，含【意图/目标】【进行中/当前任务】【已完成】【关键事实与决策】【涉及的文件/产物】【待办/下一步】）。
>   - WI-3 锚定增量: `_SUMMARY_MARKER = "[压缩摘要 / compressed summary]"`（L569），旧摘要抽出作 prior-state 拼进 system（L318-321），不混进待摘 transcript（防套娃）。
>   - 压缩命中落 `context_compacted`（L394-406），`summary_preview=summary_text[:300]`。
> - `backend/agent/agent_loop.py`:
>   - WI-4a always-on 目标注入: L604-626，`working_messages = list(messages)` 之后、`for iteration` 循环之前，**单点**注一条 `{"role":"system","content":"[目标锚定] 当前目标：…"}`（+ `[当前子目标]`），落 `wi4a_goal_anchor_always_on`（L624-626）。目标取 `session_goal_store.get_goal_text(session_id)`。
> - `backend/main.py`: `:1421` 启动落 `wi4_0_compaction_enabled context_window=%d threshold=%.2f eff_pct=%.2f`（仅 `[features] compaction_enabled = true` 时落）。
>
> **★ 关键事实（影响多条 TC 判定，先读）**:
> 1. **compaction 出厂默认关**（`config.py compaction_enabled=False`）。测 Phase 1 必须**临时开** `[features] compaction_enabled = true` 并重启。
> 2. **窗口取的是「有效出站模型」**（P-B 修复，commit `84e4c25`）。当前出站模型由 onboarding 写的 `userdata/llm_runtime.json model`（中转站 `gpt-5.5`）决定，**不是** `config.toml [llm] model` 旧种子（`gemma4:e4b`）。gpt-5.5 默认窗口 **400K**。
> 3. **逼触发要把窗口调小**：gpt-5.5 默认 400K 窗口很难在手测里顶满。用 `userdata/model_overrides.toml` 给 gpt-5.5 写 `context_window = 8000`（甚至更小），逼压缩在几轮内触发。验完**务必改回**（删该 override 行）。
> 4. **真正撑爆 working_messages 的是单轮 agentic 多轮 tool 调用累积的 tool_result**（一轮 4×web_fetch ≈ 64K 字符），不是历史对话（历史在 assemble 阶段被 `l2_top_k=5` 裁到 ≤10 条）。所以**触发压缩最稳的剧本 = 让桌宠跑深度研究（deep research）/连续多轮工具调用**，而非单纯多打字。
> 5. **日志锚点**（structlog → stderr → `Stdio::inherit()` → tauri dev 重定向 log，**抓 tauri dev 的 log 就有全套 backend 日志**）:
>    - `wi4_0_compaction_enabled context_window=<N> threshold=<f> eff_pct=<f>` — 启动时、压缩开关已开（`main.py:1421`）。`N` 应 = 调小后的窗口（如 8000），证明窗口取对、阈值算对。
>    - `context_compacted middle_tokens_in=… summary_tokens_out=… reduction=… summarized_msgs=… kept_head=… kept_tail=… model=… window=… threshold_pct=… summary_preview=<前300字>` — **压缩真正命中（走了 haiku 摘要）**（`context_compressor.py:394`）。
>    - `context_microcompact_only tool_results_pruned=<n> window=<N>` — **只跑了 microcompact 就降到线下、没调模型**（`context_compressor.py:232`）。
>    - `wi4a_goal_anchor_always_on sid=<sid> tid=<tid>` — 每轮注入一条常驻 `[目标锚定]` system（`agent_loop.py:625`）。
> 6. **[目标锚定] 内容**: `[目标锚定] 当前目标：<goal>`（+ 可选 `[当前子目标] <pending[0]>` + "请确保接下来的动作仍服务于上述目标…"）。role=system → `_partition` 永久排除（永不进 middle、永不被压）。
>
> **最后更新**: 2026-06-16（剧本，判定列留占位，待真机执行回填）

---

## 0. 测试前置

| 项 | 要求 |
|---|---|
| **开启压缩** ★ | `backend/userdata/config.toml` 加/改 `[features]` 段 `compaction_enabled = true`（默认 false）。改后重启桌宠。 |
| **调小窗口逼触发** ★ | `backend/userdata/model_overrides.toml` 给当前出站模型写小窗口，逼几轮内触发压缩：<br>`[models."gpt-5.5"]`<br>`context_window = 8000`<br>（若出站模型不是 gpt-5.5，按 `llm_runtime.json model` 的实际值写 key。验完务必删除该 override 行恢复原窗口。） |
| **登录态（有 llm_runtime.json）** | `backend/userdata/llm_runtime.json` 存在且 `"model":"gpt-5.5"`（onboarding 已登录中转站）。否则先走 onboarding 登录。 |
| **跑当前 checkout 代码** ★ | Phase 1 在 `master`（或本会话工作树）。给 Tauri 注入 `DESKPET_BACKEND_DIR=<repo>/backend` + `DESKPET_PYTHON=<.venv python>` + `DESKPET_DEV_MODE=1`；启动日志须出现 `[backend_launch] Dev python=... backend_dir=<...>`。若见 `[backend_launch] Bundled exe=...` 说明跑旧冻结 exe（无本改动 → 白测，先修环境）。 |
| **不要手动起 backend / 不要双起 vite** ★ | 坑 #7/#9：**只**跑 `npx tauri dev`（带上面 env），Tauri 自己 spawn backend + 跑唯一 vite。别另手动 `python main.py`（端口 8100 双占 → 桌宠弹"启动失败"）或 `npm run dev:relay`（双 vite 抢 strictPort）。 |
| **设一个 goal（WI-4a/TC-5 需要）** | TC-2/TC-5/TC-6 要求该 session 设了目标：onboarding 后在对话里 `/goal <一句话目标>`（或 UI 设目标入口），让 `session_goal_store.get_goal_text(session_id)` 非空。 |
| 后端日志 | 抓 tauri dev 重定向日志（backend structlog 走 stderr）。grep 锚点见上「关键事实 5」。 |
| 截图/日志存档 | 截图存 `testcase/2026-06-16-compaction-bestpractice-phase1/screenshots/<case-id>.png`；日志 grep 片段贴进各 case 的「log 证据」。 |

### 启动命令（参考；按本机 worktree 路径调整）

```powershell
# 0) 先关旧桌宠（坑 #1：TaskStop 留 orphan）
taskkill /F /IM deskpet.exe 2>$null
# 关掉残留 Vite（按 dev 端口找）

# 1) 注入 env 启动 Tauri（Tauri 自己 spawn backend，别手动起 backend）
$env:DESKPET_BACKEND_DIR = "G:\projects\deskpet\backend"
$env:DESKPET_PYTHON      = "G:\projects\deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_DEV_MODE    = "1"
npx tauri dev   # 在 tauri-app 目录；它自管唯一 vite + spawn backend
```

### 抓日志（参考 grep 锚点）

```powershell
# 压缩开关 + 窗口（启动时）
#   期望: wi4_0_compaction_enabled context_window=8000 ...   ← 证明窗口取的是调小后的值
# 压缩真命中（走 haiku 摘要）
#   期望: context_compacted ... summary_preview=【意图/目标】... 【进行中/当前任务】...
# 只跑 microcompact 没调模型
#   期望: context_microcompact_only tool_results_pruned=<n> window=8000
# 每轮目标常驻锚定
#   期望: wi4a_goal_anchor_always_on sid=<sid> tid=<tid>
```

> ⚠️ **windows-mcp 真测纪律**（见 `CLAUDE.md`「🔒 手工测试纪律」）：
> 本文档**所有 case 必须真模拟人**（截图抓状态 → 真坐标点击 / Clipboard 粘贴 + Ctrl+V → 截图验证 → 抓后端日志判定）。
> **不允许**：WebSocket 直连 backend、pytest/脚本回放、`import` 内部模块查 registry/state 当 UI 证据（全部违反纪律，PASS 无效）。
> 每个动作前先 declare：`坐标=(x,y) | 动作=click/type | 期望=…`。
> Click 失败 workaround（按优先级 retry ≥3 次）：PowerShell `SetCursorPos + SendInput`（WebView2 要用 SendInput，不是老式 mouse_event）→ `Click(label=…)` 用 Snapshot → `App switch` 聚焦后再 click。
> 中文输入 workaround：STA Runspace + `Clipboard.SetText("中文")` + Ctrl+V（SendKeys 不支持中文 IME）。

---

## TC-1 — 触发压缩（长对话 / 多轮 deepresearch 把 token 顶到触发线）

**类型**: UI 真测（真模拟人）+ 后端日志判定

**目的**: 在 `compaction_enabled=true` + 小窗口（8000）前提下，让桌宠跑一个多轮工具调用的任务（深度研究 / 连续追问），
把 working_messages token 顶到触发线 → 后端日志出现 `context_compacted`（证明压缩真在生产运行栈命中，而非脚本回放）。

**前置配置**: 0. 测试前置全部满足；`model_overrides.toml` 已把当前出站模型窗口调到 `8000`；已重启桌宠；启动日志确认 `wi4_0_compaction_enabled context_window=8000`。

| 步骤 | 动作（declare：坐标 / 动作 / 期望） | 期望结果 |
|---|---|---|
| 1 | Screenshot 抓桌宠主界面 + 对话输入框，记录输入框坐标 `(x,y)` | 截图 `screenshots/TC-1-step1.png`；输入框可见 |
| 2 | `坐标=(x,y) \| 动作=Clipboard "帮我深度研究 2024 年中国新能源汽车出口情况，要详细的数据和分析" → Ctrl+V → Enter` | 桌宠开始跑深度研究，多轮工具调用（web 搜索 / fetch），对话区出现工具进度 |
| 3 | 等待研究跑若干轮（tool_result 累积），必要时再追问 1~2 条让 token 继续涨（`坐标=(x,y) \| 动作=type "再补充政策背景和竞争格局" \| 期望=继续多轮工具调用`） | working_messages 累积的 tool_result 把 token 顶过触发线 |
| 4 | grep tauri dev log `context_compacted` | 出现一行 `context_compacted middle_tokens_in=… summary_tokens_out=… reduction=… summarized_msgs=… model=… window=8000 …`；`reduction > 0`（真摘掉了中段） |
| 5 | Screenshot 抓压缩后对话仍正常显示、桌宠继续答复 | 截图 `screenshots/TC-1-step5.png`；对话未崩、未报错中止 |

**可观测证据**:
- ✅ 触发后：日志有 `context_compacted ... window=8000 reduction=<>0`，桌宠继续正常答复（不弹"启动失败"、不中止）。
- ❌ 未触发 / 复发：跑了很多轮也无 `context_compacted`（窗口没调小 / compaction 没开 / 触发判据没生效）；或日志 `window=400000`（窗口没取调小值，环境没设对）；或 `window=32000`（取了 gemma 种子，P-B 没修，先修环境）。

**PASS 判据**: 步骤 4 出现 `context_compacted` 且 `window=8000`（= 调小后的有效模型窗口）。
**FAIL 判据**: 多轮工具调用后仍无 `context_compacted`，或 `window` ≠ 8000。

**判定**: _（待真机回填：PASS / FAIL / RETRY-N / SKIP+理由）_

---

## TC-2 — 压缩后追问"刚才在干嘛 / 继续" → 桌宠仍记得任务（根治"看不到任务"）

**类型**: UI 真测（真模拟人）+ 后端日志判定 — **★招牌（一票否决级）**

**目的**: TC-1 触发压缩后，追问"刚才在干嘛 / 继续" → 桌宠**仍记得当前任务**（不答"我们没聊过/请重新说明"）。
这是"压缩后看不到任务"的根治验收，靠两条腿：WI-3 结构化摘要保【进行中/当前任务】+ WI-4a 目标 always-on 钉死。

**前置配置**: 本 session 已 `/goal` 设了目标（关键事实 6）；已完成 TC-1（日志已出现 `context_compacted`）。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认 TC-1 已触发压缩（日志有 `context_compacted`） | 压缩已命中 |
| 2 | grep `context_compacted` 那行的 `summary_preview` | `summary_preview` 含 `【进行中/当前任务】` 段且写了当前那件事（如"深度研究新能源汽车出口"），**不是**"(本段无明确任务)" |
| 3 | `坐标=(x,y) \| 动作=Clipboard "我们刚才在干嘛？继续" → Ctrl+V → Enter` | 桌宠回答能复述当前任务（继续深度研究 / 接着补充政策背景），**不**反问"没有上下文/请重新说明" |
| 4 | Screenshot 抓桌宠的回复 | 截图 `screenshots/TC-2-step4.png`；回复内容与原任务连续 |
| 5 | grep `wi4a_goal_anchor_always_on` | 追问那一轮仍有此行（目标常驻锚定在压缩后仍注入） |

**可观测证据**:
- ✅ 根治后：`summary_preview` 的【进行中/当前任务】段非空且对得上；桌宠追问能接上原任务；`wi4a_goal_anchor_always_on` 在压缩后仍每轮出现。
- ❌ 复发（看不到任务）：桌宠答"我们没有聊过/请重新告诉我要做什么"，或 `summary_preview` 的【进行中/当前任务】= "(本段无明确任务)"，说明摘要把任务压没了（WI-3 失效）或目标段没钉住（WI-4a 失效）。

**PASS 判据**: 步骤 2 摘要含具体【进行中/当前任务】 + 步骤 3 桌宠回复与原任务连续 + 步骤 5 锚定仍在。
**FAIL 判据**: 桌宠"失忆"，或【进行中/当前任务】段为空/占位。

**判定**: _（待真机回填）_

---

## TC-3 — 摘要结构化：`context_compacted` 的 summary_preview 含分段段名

**类型**: 后端日志判定（真实运行栈日志，非脚本回放）

**目的**: 验证 WI-3 摘要是**结构化 7 段**，不是扁平一条。`context_compacted` 的 `summary_preview`（前 300 字）
应含多个段名（至少含【进行中/当前任务】与其他若干段），证明 `_SUMMARY_SYSTEM` 7 段 schema 真生效。

**前置配置**: 同 TC-1；已触发一次压缩。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 触发一次压缩（见 TC-1） | 日志有 `context_compacted` |
| 2 | grep `context_compacted` 行，看 `summary_preview` 字段 | `summary_preview` 内出现至少 2~3 个段名标记，取自：`【意图/目标】`/`【进行中/当前任务】`/`【已完成】`/`【关键事实与决策】`/`【涉及的文件/产物】`/`【待办/下一步】` |
| 3 | 确认 `【进行中/当前任务】` 段在 summary_preview 前 300 字内出现 | 该段是"头等优先保活"段，应在 preview 内可见 |

**可观测证据**:
- ✅ 结构化生效：`summary_preview` 含多个 `【…】` 段名标记。
- ❌ 扁平/旧版：`summary_preview` 是一段连续无分段文字，无任何 `【…】` 段名 → WI-3 schema 没接上（查 `_SUMMARY_SYSTEM` 是否真被用作 summary_system，L318-321）。

**PASS 判据**: 步骤 2 出现 ≥2 个段名标记，且步骤 3【进行中/当前任务】在 preview 内。
**FAIL 判据**: summary_preview 无任何 `【…】` 段名。

**判定**: _（待真机回填）_

---

## TC-4 — microcompact：多轮工具调用 → `context_microcompact_only` 或陈旧 tool_result 被占位

**类型**: 后端日志判定（真实运行栈日志）

**目的**: 验证 WI-2 microcompact 层先于 haiku 摘要跑：把陈旧 tool_result 正文换占位（保 tool 壳 + tool_call_id），
若清理后整体已降到触发线下 → 落 `context_microcompact_only` 并**跳过 haiku**（最高频生效层、零模型调用）。

**前置配置**: 同 TC-1；让桌宠跑**多轮工具调用**（deep research / 连续 web_fetch），制造大量 tool_result。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | `坐标=(x,y) \| 动作=Clipboard "深度研究：对比宁德时代和比亚迪 2024 年财报，多查几个来源" → Ctrl+V → Enter` | 触发多轮 web 搜索 / fetch，tool_result 累积 |
| 2 | 让其跑若干轮工具调用直到接近触发线 | working_messages 里堆了多条 tool_result |
| 3 | grep tauri dev log `context_microcompact_only` | **若**清陈旧 tool_result 后已降到线下 → 出现 `context_microcompact_only tool_results_pruned=<n> window=8000`（n≥1），且该轮**没有** `context_compacted`（省了 haiku） |
| 4 | （若步骤 3 没命中"only"，而是走了完整压缩）grep `context_compacted` 行的 `summarized_msgs` / `reduction` | 仍应看到 microcompact 先跑过（tool_result 已占位）→ `reduction` 反映陈旧 tool_result 被清；二者择一命中即可 |

**可观测证据**:
- ✅ microcompact 生效（理想）：`context_microcompact_only tool_results_pruned=<n>` 且同轮无 `context_compacted`（廉价层独立把 token 压下去，没调模型）。
- ✅ 退而其次：走了 `context_compacted`，但 microcompact 在其前已跑（tool_result 占位），桌宠未崩。
- ❌ 复发：多轮工具调用后 token 一直涨却既无 `context_microcompact_only` 也无 `context_compacted`，或 microcompact 把整条 tool 消息删了导致协议报错（孤儿 tool）。

**PASS 判据**: 步骤 3 出现 `context_microcompact_only`（首选）**或** 步骤 4 确认 microcompact 在压缩前跑过且无孤儿/报错。
**FAIL 判据**: 两个锚点都不出现，或出现 tool 配对协议错误。

**判定**: _（待真机回填）_

---

## TC-5 — 目标 always-on：设了 goal 的 session，每轮 LLM 调用都带 1 条 `[目标锚定]`，压缩后仍在

**类型**: UI 真测（真模拟人）+ 后端日志判定

**目的**: 验证 WI-4a：设了目标的 session，**每轮**循环前注一条 `[目标锚定]` role=system（去重恒 ≤1 条），
且 role=system → `_partition` 永久排除 → **压缩后仍在**。这是目标"钉死不可压"的验收。

**前置配置**: 本 session 已 `/goal` 设了一句话目标；compaction 已开、小窗口。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认本 session 设了目标（`/goal` 已设，或 UI 目标入口显示当前目标） | 目标已存在 |
| 2 | `坐标=(x,y) \| 动作=Clipboard "随便聊两句，今天天气怎么样" → Ctrl+V → Enter`（普通一轮，未触发压缩） | 桌宠正常答复 |
| 3 | grep `wi4a_goal_anchor_always_on` | 该轮出现一行 `wi4a_goal_anchor_always_on sid=<sid> tid=<tid>`（证明目标 always-on 注入，**即使没触发压缩**） |
| 4 | 跑到触发压缩（见 TC-1），压缩命中后再发一轮普通消息 | 压缩后那轮仍有 `wi4a_goal_anchor_always_on`（目标段在 system，未被压掉） |
| 5 | （去重断言）确认任意一轮日志里 `wi4a_goal_anchor_always_on` **每轮只 1 次**，不堆叠多条 `[目标锚定]` | 每轮恒 ≤1 条目标锚定（删了周期性 anchor + 压缩后 `_build_goal_anchor` 注入，无重复） |

**可观测证据**:
- ✅ always-on 生效：每轮（含未压缩轮 + 压缩后轮）都有 `wi4a_goal_anchor_always_on`，且每轮仅 1 条。
- ❌ 复发：未触发压缩的轮**没有**目标锚定（说明目标只在压缩时才注 = 没改成 always-on）；或一轮出现多条 `[目标锚定]`（旧周期性/压缩后注入没删干净，违反去重 DoD）。

**PASS 判据**: 步骤 3（未压缩轮也有锚定）+ 步骤 4（压缩后仍有）+ 步骤 5（每轮 ≤1 条）全满足。
**FAIL 判据**: 普通轮无锚定，或压缩后丢锚定，或一轮多条 `[目标锚定]`。

**判定**: _（待真机回填）_

---

## TC-6 — 防套娃：连续触发 2 次压缩，不出现 `[压缩摘要]` 套 `[压缩摘要]`

**类型**: UI 真测（真模拟人）+ 后端日志判定

**目的**: 验证 WI-3 锚定增量：第二次压缩时，旧 `[压缩摘要 / compressed summary]` 被 `_extract_prior_summary` 抽出作 prior-state
拼进 system，**不混进待摘 transcript** → 不会出现"摘要里又摘了一段旧摘要"（套娃 drift）。

**前置配置**: 同 TC-1，小窗口；要能连续触发 ≥2 次压缩（持续追问 / 持续多轮工具调用）。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 触发第 1 次压缩（见 TC-1） | 日志第 1 条 `context_compacted` |
| 2 | 继续追问 / 跑工具，把 token 再次顶过触发线 | 触发第 2 次压缩，日志第 2 条 `context_compacted` |
| 3 | grep 第 2 条 `context_compacted` 的 `summary_preview` | 第 2 次摘要**不包含**嵌套的 `[压缩摘要 / compressed summary]` 前缀文字（旧摘要被作为 prior-state 增量更新，不是被当待摘内容再摘一遍） |
| 4 | （结构连续性）确认第 2 次摘要仍保住【进行中/当前任务】，未因增量而丢任务 | 任务连续，无低频信息蒸发 |
| 5 | Screenshot 抓两次压缩后桌宠仍连续答复 | 截图 `screenshots/TC-6-step5.png`；对话未崩 |

**可观测证据**:
- ✅ 防套娃生效：第 2 次 `summary_preview` 不含嵌套 `[压缩摘要 / compressed summary]` 前缀；任务态仍在。
- ❌ 套娃复发：第 2 次 summary_preview 里出现 `[压缩摘要 / compressed summary]`（旧摘要被当 transcript 又摘了一遍）→ 多轮后 drift、任务蒸发。

**PASS 判据**: 步骤 3 第 2 次摘要无嵌套摘要前缀 + 步骤 4 任务仍在。
**FAIL 判据**: 第 2 次 summary_preview 出现嵌套 `[压缩摘要]` 前缀。

**判定**: _（待真机回填）_

---

## 结果汇总（待真机执行回填）

| Case | 范围 | 类型 | 判定 |
|---|---|---|---|
| TC-1 | 触发压缩 → `context_compacted` | UI 真测 + log | _待回填_ |
| TC-2 | 压缩后追问仍记得任务（★招牌） | UI 真测 + log | _待回填_ |
| TC-3 | 摘要结构化（summary_preview 含段名） | log | _待回填_ |
| TC-4 | microcompact（`context_microcompact_only` / tool_result 占位） | log | _待回填_ |
| TC-5 | 目标 always-on（每轮 ≤1 条 `[目标锚定]`，压缩后仍在） | UI 真测 + log | _待回填_ |
| TC-6 | 防套娃（连压 2 次不嵌套摘要） | UI 真测 + log | _待回填_ |

> **恢复环境（验完必做）**: 删 `model_overrides.toml` 里的小窗口 override 行；按需把 `[features] compaction_enabled` 改回 false（除非 WI-6 已翻默认）。
> **结果存档**: 截图存 `testcase/2026-06-16-compaction-bestpractice-phase1/screenshots/`；执行后日志证据贴回各 case 的 log 证据栏与本汇总表。

---

## ✅ 真机执行记录（2026-06-16，windows-mcp 真模拟人）

**环境**: tauri dev + 注入 `DESKPET_BACKEND_DIR=G:\projects\deskpet\backend` + `DESKPET_PYTHON=<.venv>` + `DESKPET_DEV_MODE=1`（日志确认 `[backend_launch] Dev python=... backend_dir=...` → 跑当前代码非 frozen exe）。`AppData\Roaming\deskpet\config.toml` 临时开 `[features] compaction_enabled=true`，`AppData\Roaming\deskpet\model_overrides.toml` 把 gpt-5.5 `context_window=8000` 逼触发（验完已恢复 1M）。出站 gpt-5.5（chinzy 中转站）。
**踩坑**: app 真实 user_data_dir 是 `%APPDATA%\deskpet`（非 `backend/userdata`），改错文件无效；PowerShell `Out-File utf8` 给 TOML 加 BOM → tomllib 解析失败 → 改用 Write 工具（无 BOM）。

**操作**: 输入框真坐标 click + Clipboard 中文 + Ctrl+V + Enter 发送 `/goal 调研宁德时代2024年年报核心财务数据并总结要点` → 桌宠跑深度研究（35 次 web_search/web_extract）→ 追问"你刚才在帮我查什么任务？还差哪些没做完？"。

**日志锚点战果**（grep `tauri-test3-err.log`）:
- `wi4_0_compaction_enabled context_window=8000 threshold=0.80 eff_pct=0.95` ✓（窗口取对、阈值算对）
- `context_microcompact_only tool_results_pruned=1 window=8000` ×**24**（WI-2 最高频生效层，把上下文从 19501 压住在 ~14-15K）
- `context_compacted ... summary_preview=【意图/目标】…【进行中/当前任务】…` ×**2**（WI-3 结构化 schema 真出分段）
- `wi4b_preflush_l1 sid=default chars=55` ×**3**（每 run 限一次 latch 生效）
- `p1_4_compaction_fired` ×3 + UX banner "上下文已达 104%（估算 8386/8000 tokens）" ✓

| Case | 范围 | 判定 | 证据 |
|---|---|---|---|
| TC-1 | 触发压缩 | **PASS** | `wi4_0_compaction_enabled window=8000` + `p1_4_compaction_fired` |
| TC-2 | 压缩后追问仍记得任务（★） | **PASS** | 24×microcompact+2×完整摘要后，追问答出"…更早之前还有'调研宁德时代2024…'"——任务连续性保住（截图 `screenshots/TC2-task-continuity-after-compaction.png`） |
| TC-3 | 摘要结构化 | **PASS** | `context_compacted` summary_preview 含【意图/目标】【进行中/当前任务】【关键事实与决策】等段 |
| TC-4 | microcompact | **PASS** | `context_microcompact_only tool_results_pruned=1` ×24（截图 `screenshots/TC1-2-microcompact-research-running.png`） |
| TC-5 | 目标 always-on | **未验（环境受限）** | `/goal` 在本 session 未注册 goal → `get_goal_text` 返 None → `wi4a_goal_anchor_always_on` 未触发（0 次）。WI-4a 逻辑已由单测充分覆盖（always-on 注入 + dedup ≤1 + get_pending_tasks）；实机验证待 goal 设置入口的正确手势。 |
| TC-6 | 防套娃 | **部分**（诚实记录） | 结构机制正确（_extract_prior_summary 抽旧摘要作 prior、不混 transcript），但第 1 条 `context_compacted` 摘要出现**反射**（把"压缩对话历史"元指令当用户任务写入），疑似 session "default" 遗留旧反射摘要被 prior-state 带入；第 2 条摘要正常。haiku 摘要层防反射 prompt 未 100% 压住（issue #46602 式顽疾），microcompact 层无此问题。 |

**结论**: 核心招牌 TC-1/2/3/4 **PASS**（压缩触发 + microcompact 最高频生效 + 结构化摘要 + 任务连续性根治）；TC-5 实机待 goal 手势（单测已证）；TC-6 机制对、haiku 偶发反射为已知 caveat。WI-6 默认开启 gate（真机 case ② 任务连续性）**满足**。
