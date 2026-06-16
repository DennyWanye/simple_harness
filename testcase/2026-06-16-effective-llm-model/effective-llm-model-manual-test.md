# 有效出站 LLM 模型解析统一（根治 P-B）手工测试用例

> **被测功能**: 让所有"需要知道当前出站 LLM 模型"的代码读到**运行时有效模型**
> （onboarding 写的 `userdata/llm_runtime.json` 覆盖后的 `gpt-5.5`），而不是
> `config.toml [llm] model` 的**旧种子值**（`gemma4:e4b`）。根治压缩窗口/前端模型环/PPT
> 视觉复审按错模型算（P-B）。
>
> **对应 commit**: `84e4c25` fix(llm): 根治 P-B — 读模型名统一走有效出站模型(运行时覆盖),非 config 旧种子
>
> **被测代码**:
> - `backend/config.py`:
>   - **新增** `effective_llm_model(cfg)` — in-process 访问器，链：`cfg.llm.local.model`（运行时覆盖已应用）
>     → `cfg.raw["llm"]["model"]` → 种子默认 `"gemma4:e4b"`。空安全，绝不抛。
>   - **新增** `effective_llm_model_standalone()` — 工具用，**不依赖 main 单例**，链：直读
>     `<user_data>/llm_runtime.json` 的 `model` → 回落 `load_config(resolve_config_path()).raw["llm"]["model"]`
>     → 种子默认。失败静默返默认。
> - `backend/main.py`:
>   - `:186~` 应用 runtime 覆盖时**同步** `config.raw["llm"]["model"/"base_url"]`（双保险，让所有 in-process raw 读取也读到有效值）。
>   - `:1394` 压缩窗口解析改走 `effective_llm_model(config)`（原读 `config.raw["llm"]["model"]` 旧种子）。
>   - `:4277` `_stub_model`（发给前端 context_usage 环的占位 model 名）改走 `effective_llm_model(config)`。
> - `backend/deskpet/tools/ppt_visual_review.py:155`: 原 `import config as _cfg; _cfg.config.raw...`
>   **恒 AttributeError**（config 模块无 `config` 属性，同 TC-P2-05 坑）→ 被 except 吞 → 一直回落 hardcode `"gpt-5.5"`；
>   改走 `effective_llm_model_standalone()`，换模型时也读对。
>
> **★ 关键事实（影响多条 TC 判定，先读）**:
> 1. **真源 = `config.llm.local.model`**。真出站 provider 是 `main.py:239~` 的 `local_llm`
>    （`model=config.llm.local.model`）；`cloud_llm` 恒 `None`；压缩器 = `local_llm`。覆盖在 `main.py:186~` 已写进 dataclass。
> 2. **根因**：覆盖只写 dataclass，**没同步回 `config.raw["llm"]["model"]`** → raw 永远是种子 `gemma4:e4b`。
>    读 dataclass 的代码对，读 raw dict 的代码（压缩窗口/stub）错。本次修复 = ① 同步 raw（WI-2）② 改读访问器（WI-3）双保险。
> 3. **模型→窗口映射**（`backend/llm/model_info.py` BUILTIN 表，**测前必读**）:
>    - `gpt-5.5` → **默认 400K**（`context_window=400_000`），可选档 `(128K, 400K, 1M)`。
>    - `gemma4:e4b` **不在 BUILTIN 表** → 走 `_default` = **32K**（32000）。
>    - **要让 gpt-5.5 解析出 1M**，必须有用户层 override：`<user_data>/model_overrides.toml` 给 gpt-5.5 写
>      `context_window = 1000000`（见 `model_info.resolve` 三层链：BUILTIN ← model_overrides.toml ← project context.toml）。
>    - ⚠️ **招牌判据的精确表述**：修复生效 = 压缩窗口解析出 **gpt-5.5 对应窗口（默认 400000，有 1M override 时 1000000）**，
>      **关键是不再是 32000**（gemma 的 `_default`）。Plan 里写的 `context_window=1000000` 是"用户已把 gpt-5.5 档调到 1M"的场景；
>      若没设 1M override，正确结果是 **400000**（同样 ✅，因为 ≠ 32000，证明读的是 gpt-5.5 而非 gemma）。
> 4. **日志锚点**（structlog → stderr → tauri dev 重定向 log）:
>    - 压缩窗口（仅 `[features] compaction_enabled = true` 时落）:
>      `wi4_0_compaction_enabled context_window=<int> threshold=<float>`（`main.py:1417`）
>    - 启动默认模型窗口解析（每次启动落一行 + 每次 resolve 落）:
>      `model_context_resolved model=<str> window=<int> source=<builtin|global|project>`（`model_info.py:258`）
> 5. **前端「模型按钮」读的是另一条链（重要，别误判）**:
>    消息大框左下角模型按钮（commit `6e67fcc` 改成"模型-上下文长度 K/M"）读的是
>    `sessionsStore.sessions[SID].preferred_model`（**per-session 用户选的模型覆盖，默认 null**）+ 前端
>    `codeModelsStore` 的 catalog 算 `contextWindowForModel`。**它不是后端 `_stub_model` 那条链**。
>    - `preferred_model = null`（默认未选）→ 按钮显 **"默认模型"**（不显具体模型/窗口）。
>    - 用户在「更换模型」弹框选了 gpt-5.5 → 按钮显 `gpt-5.5-400K`（或 catalog 里该模型档对应的 K/M）。
>    - 所以**前端模型按钮验证的是「per-session 模型选择 + catalog 窗口显示」链**，**不直接证明 `effective_llm_model` 修复**；
>      它作为"用户能看到正确模型+窗口"的间接 UI 观测。`effective_llm_model` 的硬证据在后端日志（事实 4）+ context_usage 环的 model 字段。
>    - **真正受 `_stub_model` 影响的前端 UI** = context_usage **环形仪表**（Claude-Code 风格，由 `context_usage` ws 事件驱动，
>      首轮真 turn 落地前用 stub），它的 model 名/窗口才是 `effective_llm_model(config)` 出来的值。
>
> **最后更新**: 2026-06-16

---

## 0. 测试前置

| 项 | 要求 |
|---|---|
| **登录态（有 llm_runtime.json）** ★ | 核心链 TC 要求 onboarding 已登录 → `backend/userdata/llm_runtime.json` 存在且 `"model":"gpt-5.5"`（中转站）。测前先核对该文件内容（见下「环境核对」）。 |
| **config 种子 = gemma（制造 stale 条件）** ★ | 招牌 TC（TC-01）的前提是 `config.toml [llm] model` **不是** gpt-5.5，理想是出厂种子 `gemma4:e4b`，这样才能证明"raw 是 gemma 但有效模型读成了 gpt-5.5"。若本机 config 被临时手改成 gpt-5.5，需先改回 `gemma4:e4b` 才能验出真修复（否则 raw 恰好也对，测不出 bug，是假性 PASS）。 |
| **跑的是 master/当前 checkout 代码** | 本修复已在 `master`（commit `84e4c25`）。给 Tauri 注入 `DESKPET_BACKEND_DIR=<repo>/backend` + `DESKPET_PYTHON=<.venv python>`；启动日志须出现 `[backend_launch] Dev python=... backend_dir=<...>`；若见 `[backend_launch] Bundled exe=...` 说明跑旧冻结 exe（无本修复 → 测了等于白测，假性结果，先修环境）。 |
| **不要手动起 backend / 双起 vite** ★ | 坑 #7/#9：只跑 `npx tauri dev`（带上面 env），Tauri 自己 spawn backend + 跑唯一 vite。别另手动 `python main.py` / `npm run dev:relay`。 |
| **开启压缩（招牌日志前提）** | `wi4_0_compaction_enabled` 仅在 `[features] compaction_enabled = true` 时落（默认 false）。验压缩窗口的 TC 必须先开此 flag 并重启。 |
| 后端日志 | 能抓 tauri dev 重定向日志（backend structlog 走 stderr → `Stdio::inherit()`）。grep 锚点见上「关键事实 4」。 |
| 截图/日志存档 | `plans/manual-results-2026-06-16-effective-llm-model/screenshots/<case-id>.png`，日志 grep 片段贴报告。 |

### 环境核对（测前一次性，纯读，不改运行栈）

> 这些是**配置/文件核对**（白名单允许的"看一眼"），用于确认前置；**不能**代替 UI 真测的 TC。

```powershell
# 1) llm_runtime.json 是否有 gpt-5.5（核心链前提）
Get-Content backend\userdata\llm_runtime.json   # 期望含 "model": "gpt-5.5"  + base_url 中转站

# 2) config.toml 种子是否仍是 gemma（制造 stale 条件）
#    用 Read 工具看 [llm] model；若被改成 gpt-5.5 需改回 gemma4:e4b 才能验出修复

# 3) model_overrides.toml 是否给 gpt-5.5 设了 1M（决定招牌是 400K 还是 1M）
Get-Content backend\userdata\model_overrides.toml   # 有 [models."gpt-5.5"] context_window=1000000 → 期望 1M；无 → 期望 400K
```

> ⚠️ **windows-mcp 真测纪律**（见 `CLAUDE.md`）：标「UI 真测」的用例必须**真模拟点击 / 粘贴 + 截图 + 抓后端日志**；
> **不能**用 `import config; effective_llm_model(...)` / pytest / WebSocket 注入查内部状态当 UI/E2E 证据。
> 每个 UI 动作前先 declare：`坐标=(x,y) | 动作=click/type | 期望=...`。
> 标「日志/配置核对」的用例为后端日志锚点 + 文件核对类，不强制 windows-mcp 点击，但**仍须是真实运行栈的真启动日志**（不是再跑一遍函数的脚本回放）。

---

## TC-01 — ★招牌：config 种子=gemma + runtime=gpt-5.5 → 压缩窗口按 gpt-5.5 解析（非 32000）

**类型**: 日志/配置核对（真实启动日志，非脚本回放）

**目的**: 在 `config.toml [llm] model = gemma4:e4b`（种子）但 `llm_runtime.json model = gpt-5.5`（运行时覆盖）
且 `[features] compaction_enabled = true` 的前提下，启动桌宠 → 后端启动日志的压缩窗口解析按 **gpt-5.5** 的窗口算
（默认 400000，有 1M override 时 1000000），**绝不是 32000**（gemma 的 `_default`）。这是 P-B 根治的硬验收。

**前置配置**:
- `backend/userdata/config.toml`: `[llm] model = "gemma4:e4b"`（确认是 gemma 种子，不是 gpt-5.5）。
- `backend/userdata/llm_runtime.json`: 含 `"model": "gpt-5.5"`（onboarding 登录已写；若无 → 先登录）。
- `backend/userdata/config.toml`: `[features] compaction_enabled = true`。
- （可选）`backend/userdata/model_overrides.toml` 给 gpt-5.5 设 `context_window = 1000000` → 期望 1M；不设 → 期望 400K。
- 改后**重启桌宠**（taskkill deskpet.exe + 清 Vite，再 `npx tauri dev` 带 worktree env）。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 确认前置（环境核对 1/2/3）：config 种子 gemma、runtime gpt-5.5、compaction_enabled true | 三项满足 |
| 2 | 重启桌宠，等后端起来 | 启动日志出现 `[backend_launch] Dev python=... backend_dir=<...>`（跑当前 checkout，非冻结 exe） |
| 3 | grep 后端日志 `wi4_0_compaction_enabled` | 出现一行 `wi4_0_compaction_enabled context_window=<N> threshold=<...>`；**`N` = 400000（或设了 1M override 则 1000000）**，**绝不是 32000** |
| 4 | grep 后端日志 `model_context_resolved` | 启动行 `model_context_resolved model=gpt-5.5 window=<400000 或 1000000> source=<builtin 或 global>`；**model 字段是 gpt-5.5，不是 gemma4:e4b**；window 同步 ≠ 32000 |

**可观测证据**:
- ✅ 修复后：`wi4_0_compaction_enabled context_window=400000`（或 1000000），`model_context_resolved model=gpt-5.5 window=400000`。
- ❌ 若复发（读了 raw 旧种子 gemma）：`wi4_0_compaction_enabled context_window=32000`（gemma 走 `_default` 32K）→ 压缩阈值按 32K 的 75% ≈ 24K 就触发，gpt-5.5 的几十万窗口被浪费、过早压缩（P-B 本体）。

**PASS 判据**: 步骤 3 `context_window` ∈ {400000, 1000000}（取决于有无 1M override）且 步骤 4 `model=gpt-5.5` → 都 ≠ 32000/gemma。
**FAIL 判据**: 步骤 3 `context_window=32000` 或 步骤 4 `model=gemma4:e4b` → raw 旧种子泄漏到压缩窗口/解析，修复未生效（查 `main.py:186~` 同步 raw + `:1394` 是否真走 `effective_llm_model`）。

---

## TC-02 — ★ 反例对照：把 config 种子改回 gpt-5.5 不影响结论（证明读的是有效模型而非碰巧）

**类型**: 日志/配置核对

**目的**: 排除"碰巧 config 种子也对所以测不出"的混淆。验证无论 config 种子是 gemma 还是 gpt-5.5，
压缩窗口/解析都读出 **gpt-5.5**（因为 runtime 覆盖 + 访问器优先 dataclass）。这条用来**证伪"修复其实没起作用、只是种子恰好对"**。

**前置配置**: 在 TC-01 基础上，**故意把 config 种子改成一个明显错的值** `model = "gemma4:e4b"`（保持 gemma），
确认 runtime 仍是 gpt-5.5。（即 TC-01 的配置；本 TC 强调"种子错也不影响"。）

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 确认 config 种子 = gemma4:e4b、runtime = gpt-5.5、重启 | 起来 |
| 2 | grep `model_context_resolved` 启动行 | `model=gpt-5.5`（**不是** gemma4:e4b）→ 证明访问器优先 dataclass 覆盖值，没被 config 种子带偏 |
| 3 | grep `wi4_0_compaction_enabled` | `context_window` ≠ 32000 |

**PASS 判据**: 种子是 gemma 但解析/压缩窗口出 gpt-5.5 的窗口 → 证明读的是**有效出站模型**，不是 config raw。
**FAIL 判据**: 解析出 gemma/32000 → 访问器没优先 dataclass，或 raw 同步失效。

> 📌 此 TC 与 TC-01 共享一次启动即可同时验（同一份日志看两个锚点）。分列是为强调"可证伪性"：TC-01 证"修对了"，TC-02 证"不是巧合"。

---

## TC-03 — UI：context_usage 环（首轮 stub）显示有效模型，不是 gemma ★

**类型**: windows-mcp 真测（UI 点击 + 截图 + 日志）

**目的**: 后端 `_stub_model` 改走 `effective_llm_model(config)` 后，首轮真 turn 落地前推给前端
context_usage 环的占位 model = gpt-5.5（不是旧 gemma），窗口 ≠ 32K。验证"用户在 UI 上看到的当前模型/窗口是有效模型"。

**前置配置**: config 种子 gemma、runtime gpt-5.5、重启（同 TC-01）。

| 步骤 | 动作（declare 坐标/动作/期望） | 期望结果 |
|---|---|---|
| 1 | Screenshot 桌宠主界面 | 桌宠在线 |
| 2 | declare `坐标=(消息 tip) \| 动作=click \| 期望=打开消息大框`：点桌宠的「消息」tip/入口打开消息大框 | 消息大框打开 |
| 3 | Screenshot 消息大框，观察 context_usage 环形仪表（Claude-Code 风格用量环）所示模型名 | 环上/悬浮提示显示的 model = **gpt-5.5**（或其窗口对应的用量百分比按 400K/1M 算），**不是 gemma4:e4b**、**不是按 32K 算的满格** |
| 4 | grep 后端日志 context_usage / `_stub_model` 相关（首次推送） | 推给前端的 snapshot 里 model = gpt-5.5；窗口 ≠ 32000 |

**可观测证据**:
- ✅ 修复后：环显示 gpt-5.5 + 大窗口（首轮 stub 也对）。
- ❌ 复发：环显示 `gemma4:e4b` + 32K 窗口（首轮就把旧种子推给前端，用量百分比也错）。

**PASS 判据**: context_usage 环的 model = gpt-5.5（非 gemma），窗口非 32K。
**FAIL 判据**: 环显示 gemma / 32K → stub 仍读 raw 旧种子。

> 📌 **若环上不直显 model 名**：以"用量百分比是否按大窗口（400K/1M）算"为肉眼判据 —— 同样的对话 token 数，按 32K 算会显得用量很高（环偏满），按 400K/1M 算用量很低（环几乎空）。结合步骤 4 日志硬据判定。
> retry：环 UI 不易抓时，先发一句短消息触发首轮 → 再看环；仍以日志为准。

---

## TC-04 — UI：消息大框「模型按钮」+「模型与参数」弹框显示模型与上下文窗口 ★

**类型**: windows-mcp 真测（UI 点击 + 截图）

**目的**: 验证消息大框左下角模型按钮（"模型-上下文长度 K/M"）与「模型与参数」弹框的上下文窗口显示正确。
**注意（关键事实 5）**：此按钮读的是 **per-session `preferred_model` + 前端 catalog**，**不是** `_stub_model` 那条链；
本 TC 验证的是"用户选定模型后 UI 正确显示其上下文窗口"，作为 effective-model 体验的间接 UI 观测，**不**作为 `effective_llm_model` 后端修复的硬证据（硬证据在 TC-01/03）。

**前置配置**: 同 TC-01 重启后。

| 步骤 | 动作（declare 坐标/动作/期望） | 期望结果 |
|---|---|---|
| 1 | 点「消息」打开消息大框，Screenshot | 大框打开，左下有模型按钮 |
| 2 | 观察模型按钮文案 | 若 `preferred_model=null`（默认未选）→ 显 **"默认模型"**（**这是预期，不是 bug**，因为按钮读 per-session 选择，默认空）；若已选模型 → 显 `<模型名>-<K/M>`（如 `gpt-5.5-400K` 或 `gpt-5.5-1M`） |
| 3 | declare `坐标=(模型按钮) \| 动作=click \| 期望=打开「模型与参数」弹框`：点模型按钮 | 弹出 ChangeModelModal「模型与参数」 |
| 4 | 在弹框选 gpt-5.5（若有 1M 档则选 1M），Screenshot | 弹框标题「模型与参数（当前 gpt-5.5）」；上下文窗口栏显示 **K/M 单位**（如 `400K` 或 `1M`），**去掉了"X tokens"原始数**（commit `6e67fcc` 的改动）|
| 5 | 关弹框，再看按钮 | 按钮显 `gpt-5.5-400K`（或 `gpt-5.5-1M`），与弹框一致 |

**可观测证据**:
- ✅ 选 gpt-5.5 后按钮显 `gpt-5.5-400K`/`gpt-5.5-1M`，弹框上下文窗口 K/M 显示一致。
- ❌ 坏：按钮显 `gemma4:e4b-32K`（catalog 取错模型）；或弹框上下文窗口显 32K / 显空 / 仍带"X tokens"原始数。

**PASS 判据**: 选定 gpt-5.5 后按钮 + 弹框上下文窗口都按 gpt-5.5 的档（400K/1M）显示，单位为 K/M。
**FAIL 判据**: 显示 gemma/32K/空，或弹框未去掉原始 tokens 数。

> 📌 **判定边界（防误判）**: 若按钮在未选模型时显"默认模型" → **PASS**（这是 per-session 默认空的预期，见关键事实 5）。不要因为"按钮没显 gpt-5.5"就判 FAIL —— 那是用户没在该 session 选模型，与 `effective_llm_model` 修复无关。

---

## TC-05 — 边界①：无 llm_runtime.json（纯本地 ollama）→ 回落 config 种子，不崩

**类型**: 日志/配置核对

**目的**: 无 `llm_runtime.json`（纯本地 ollama 模式，从未登录中转站）时，`effective_llm_model` 回落 config 种子，
`effective_llm_model_standalone` 回落磁盘 config，行为不变（BC），桌宠正常启动不崩。

**前置配置**:
- 临时把 `backend/userdata/llm_runtime.json` 重命名走（如 `llm_runtime.json.bak`）→ 模拟无 runtime。
- config 种子设一个本地模型名（如 `model = "qwen2.5:7b"` 或保持 `gemma4:e4b`）。
- `[features] compaction_enabled = true`，重启。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 确认 `llm_runtime.json` 不存在（已重命名走），config 种子 = `<本地模型名>`，重启 | 桌宠正常起来，**不崩**、不报错弹窗 |
| 2 | grep `model_context_resolved` | `model=<config 种子模型名>`（回落 config，而非 gpt-5.5）；window 按该模型解析（不在 BUILTIN → 32K `_default`，符合预期） |
| 3 | grep `wi4_0_compaction_enabled` | `context_window` = 该种子模型的窗口（本地模型多为 32K `_default`）→ 这是 BC 预期，**非 bug** |
| 4 | 发一句普通消息测对话 | 正常回（本地 ollama 链路或降级），不挂死 |

**可观测证据**:
- ✅ 无 runtime → 读到 config 种子模型，启动正常，访问器没因缺文件抛异常。
- ❌ 坏：缺 `llm_runtime.json` 导致 `effective_llm_model_standalone` 抛 / 桌宠启动崩 / 解析出 gpt-5.5（凭空，文件都没有）。

**PASS 判据**: 无 runtime 文件时回落 config 种子、启动正常不崩。
**FAIL 判据**: 缺文件崩溃；或回落出错误模型。
**测后**: 把 `llm_runtime.json.bak` 改回 `llm_runtime.json`。

---

## TC-06 — 边界②：llm_runtime.json 只有 base_url 无 model → standalone 回落磁盘 config

**类型**: 日志/配置核对（针对 `effective_llm_model_standalone`，主要服务 PPT 路径）

**目的**: `llm_runtime.json` 半配置态（只有 `base_url`，缺 `model` 字段）时，`effective_llm_model_standalone`
**不能**误用空串/缺失，要回落磁盘 config 的 model，再回落种子默认。

**前置配置**:
- 用 Edit 工具把 `backend/userdata/llm_runtime.json` 改成**只含 base_url、删掉 model 字段**（**先备份原文件**）。
- config 种子设一个**可辨识**的值，如 `model = "gpt-5.5"`（让回落目标明确可观测）。
- 重启。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 确认 runtime 只有 base_url 无 model、config 种子 = gpt-5.5、重启 | 起来不崩 |
| 2 | 触发一次会用到 standalone 的路径（最直接：PPT 视觉复审，见 TC-09；或 grep 启动/调用日志中由 standalone 决定的 model） | standalone 回落磁盘 config → 取到 `gpt-5.5`（config 种子），**不是空串、不是种子默认 gemma** |
| 3 | （辅助）观察 in-process 链：`model_context_resolved` 仍由 main 的 dataclass 决定 | 注意 in-process 链（`effective_llm_model`）此时 dataclass 的 model 取决于 `main.py:186~`：runtime 无 model → dataclass 不被覆盖 → 用 config 种子 gpt-5.5 → 同样回落正确 |

**可观测证据**:
- ✅ runtime 缺 model → standalone 回落磁盘 config 的 model（gpt-5.5），不取空串。
- ❌ 坏：standalone 取了 runtime 里不存在的 model → 返空串 `""` → 上层 hardcode/默认；或误判为种子默认 gemma。

**PASS 判据**: 缺 model 时 standalone 回落磁盘 config 的 model（非空、非种子兜底）。
**FAIL 判据**: 返空串或越级回落到 `gemma4:e4b` 默认（说明回落顺序错）。
**测后**: 还原 `llm_runtime.json`（恢复 model 字段）。

---

## TC-07 — 边界③：llm_runtime.json 损坏（非法 JSON）→ standalone 静默回落不崩

**类型**: 日志/配置核对

**目的**: `llm_runtime.json` 内容损坏（非法 JSON）时，`effective_llm_model_standalone` 的宽 except **静默回落**磁盘 config，
不抛异常、不让桌宠/PPT 路径崩。

**前置配置**:
- 用 Write 工具把 `backend/userdata/llm_runtime.json` 写成**非法 JSON**（如 `{ "model": gpt-5.5 ` 缺引号缺括号；**先备份**）。
- config 种子设 `model = "gpt-5.5"`（回落目标）。重启。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 确认 runtime 是损坏 JSON、config 种子 gpt-5.5、重启 | 桌宠正常起来，**不崩**（in-process 链不读 runtime 文件，main.py 的 `_load_llm_runtime_overrides` 自身也应宽容；本 TC 重点是 standalone） |
| 2 | 触发 PPT 视觉复审路径（TC-09）或其它走 standalone 的调用 | standalone 读 runtime 抛 JSONDecodeError → 被宽 except 吞 → 回落磁盘 config → 取 gpt-5.5；**不抛、不崩** |
| 3 | grep 日志有无未捕获异常 traceback | **无** standalone 相关未捕获 traceback |

**可观测证据**:
- ✅ 损坏 JSON → standalone 静默回落 config，PPT 路径照常拿到模型名。
- ❌ 坏：JSONDecodeError 冒泡 → PPT 复审崩 / 日志刷 traceback。

**PASS 判据**: 损坏 JSON 下 standalone 不抛、回落磁盘 config，相关功能正常。
**FAIL 判据**: 抛异常 / 功能崩 / traceback。
**测后**: 还原合法 `llm_runtime.json`。

---

## TC-08 — 边界④：用户改模型档（1M ↔ 400K 默认）重启 → 压缩窗口跟着变 ★

**类型**: 日志/配置核对

**目的**: 验证压缩窗口解析**随用户模型档变化**而正确变化。把 gpt-5.5 在 `model_overrides.toml` 的窗口档
在 1M 和默认（去掉 override = 400K）之间切，重启 → `wi4_0_compaction_enabled context_window` 跟着切。
证明压缩窗口真的走了 `effective_llm_model → model_info.resolve` 全链，而非读死值。

**前置配置**: config 种子 gemma、runtime gpt-5.5、`compaction_enabled = true`。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | `backend/userdata/model_overrides.toml` 给 gpt-5.5 设 `context_window = 1000000`，重启 | 起来 |
| 2 | grep `wi4_0_compaction_enabled` + `model_context_resolved model=gpt-5.5` | `context_window=1000000`、`window=1000000 source=global` |
| 3 | 去掉/改回该 override（删 `model_overrides.toml` 里 gpt-5.5 的 context_window，或整段删），重启 | 起来 |
| 4 | 再 grep | `context_window=400000`、`model_context_resolved model=gpt-5.5 window=400000 source=builtin`（回到 gpt-5.5 BUILTIN 默认） |

**可观测证据**:
- ✅ 1M override → 压缩窗口 1000000；去掉 → 400000。两次都 ≠ 32000，且**随档变化**。
- ❌ 坏：改档后压缩窗口不变（读死值，没走 resolve 全链）；或任一态显 32000（落回 gemma/`_default`）。

**PASS 判据**: 压缩窗口随 gpt-5.5 的 override 档在 1000000 / 400000 间切换，两态都 ≠ 32000。
**FAIL 判据**: 不随档变 / 出现 32000。
**测后**: 把 `model_overrides.toml` 恢复到测前状态。

---

## TC-09 — PPT 路径：视觉复审用有效模型（standalone）★

**类型**: windows-mcp 真测（UI 触发 PPT）+ 日志核对（间接）

**目的**: `ppt_visual_review.review_slides` 取模型名从原"恒 AttributeError → hardcode gpt-5.5"改成 `effective_llm_model_standalone()`。
验证 PPT 视觉复审用的是 standalone 读出的**有效模型**（runtime 的 gpt-5.5），而非硬编码。

> ⚠️ **本 TC 较难纯 UI 直接看到 model 名**（PPT 复审是工具内部对 LLM 的调用，model 名不直接显在桌宠 UI 上）。
> 故判据以**日志/调用证据**为主，UI 部分负责"真触发到 PPT 视觉复审这条路径"。

**前置配置**: runtime gpt-5.5、config 种子 gemma、PPT 生成 + 视觉复审链路可用（onboarding 已登录、LLM 链路 OK）。重启。

| 步骤 | 动作（declare 坐标/动作/期望） | 期望结果 |
|---|---|---|
| 1 | 点「消息」打开消息大框，Click 输入框 → Clipboard `帮我生成一个关于"新能源汽车 2024 趋势"的 PPT，做完帮我视觉复审一下排版` → Ctrl+V → Enter | 消息发出，桌宠调 `ppt_create` 真生成 .pptx，触发视觉复审 |
| 2 | Wait 生成 + 复审完成 | ArtifactCard 渲染 .pptx；复审结果返回 |
| 3 | grep 后端日志 ppt_visual_review / review_slides 调 LLM 的 model 字段（或 relay 出站请求 model） | 复审调用用的 model = **gpt-5.5**（standalone 读 runtime），**不是**因换模型而读不到的旧值 |
| 4 | （可证伪强化）临时把 runtime model 改成另一个 BUILTIN 值（如 `deepseek-v4-pro`）→ 重启 → 再触发 PPT 复审 → grep model | 复审 model 跟着变成 `deepseek-v4-pro`（证明 standalone 真读 runtime，而非 hardcode "gpt-5.5"） |

**可观测证据**:
- ✅ 修复后：standalone 读 runtime → 复审 model 随 runtime 变（步骤 4 改成 deepseek 就用 deepseek）。
- ❌ 复发（仍 hardcode）：无论 runtime 是什么，复审 model 恒 "gpt-5.5" → 步骤 4 改成 deepseek 仍显 gpt-5.5（hardcode 没读 runtime）。

**PASS 判据**: 步骤 3 复审 model = runtime 的 gpt-5.5；**步骤 4** 改 runtime model 后复审 model 跟着变 → 证明走 standalone 非 hardcode。
**FAIL 判据**: 步骤 4 改了 runtime 但复审 model 恒 "gpt-5.5"（hardcode 未替换）；或复审路径崩。
**测后**: 把 runtime model 改回 gpt-5.5。

> 📌 **若 grep 不到复审 model 字段**：退而用步骤 4 的"改 runtime → 复审行为是否跟着变"作可证伪判据；纯 hardcode 不会跟着变。这是把"读没读到有效模型"转成可观测的行为差异。

---

## TC-10 — 回归：关闭 compaction_enabled 时不受影响 + 正常聊天/research 正常

**类型**: windows-mcp 真测（UI）+ 日志核对

**目的**: 验证本修复**没破坏**默认链路。`compaction_enabled = false`（出厂默认）时不落 `wi4_0_compaction_enabled`、
不构造压缩器，普通聊天 + 深度调研正常；同时启动仍正常落 `model_context_resolved model=gpt-5.5`（解析链独立于 compaction flag）。

**前置配置**: `[features] compaction_enabled = false`（默认）、runtime gpt-5.5、config 种子 gemma。重启。

| 步骤 | 动作（declare 坐标/动作/期望） | 期望结果 |
|---|---|---|
| 1 | 重启，grep `wi4_0_compaction_enabled` | **零行**（flag off → 不构造压缩器，符合预期，BC） |
| 2 | grep `model_context_resolved` | 仍出现 `model=gpt-5.5 window=<400000/1000000>`（启动解析独立于 compaction，照常落） |
| 3 | 点「消息」→ 输入框粘贴 `你好，简单介绍下你自己` → Enter | 桌宠正常回复（普通聊天链路 OK，不崩） |
| 4 | 再发 `深度调研一下"2024 年新能源汽车销量趋势"，出一份带来源的报告` → Enter | 正常进入调研、出报告（research 链路 OK） |

**可观测证据**:
- ✅ flag off：无压缩日志、聊天/research 正常、解析仍出 gpt-5.5。
- ❌ 坏：flag off 却仍构造压缩器（误开）；或本修复导致聊天/research 崩。

**PASS 判据**: 无 `wi4_0_compaction_enabled` 行、`model_context_resolved=gpt-5.5`、聊天 + research 都正常出结果。
**FAIL 判据**: flag off 仍落压缩日志；或聊天/research 异常。

---

## 结果汇总表

| 用例 | 被测点 | 类型 | 关键配置 | 日志/UI 锚点 | 判定 |
|---|---|---|---|---|---|
| TC-01 | ★招牌：种子 gemma + runtime gpt-5.5 → 压缩窗口按 gpt-5.5（≠32000） | 日志/配置核对 | 种子 gemma, runtime gpt-5.5, compaction on | `wi4_0_compaction_enabled context_window=400000/1000000` + `model_context_resolved model=gpt-5.5` | ⬜ |
| TC-02 | 反例对照：证明读的是有效模型非碰巧 | 日志/配置核对 | 同 TC-01 | `model_context_resolved model=gpt-5.5`（非 gemma） | ⬜ |
| TC-03 | UI：context_usage 环显有效模型非 gemma | windows-mcp 真测 | 种子 gemma, runtime gpt-5.5 | 环 model=gpt-5.5 / 用量按大窗口算 | ⬜ |
| TC-04 | UI：模型按钮 + 模型与参数弹框显模型-上下文窗口 | windows-mcp 真测 | 选 gpt-5.5（注：读 per-session preferred_model 链） | 按钮 `gpt-5.5-400K/1M`、弹框 K/M 单位 | ⬜ |
| TC-05 | 边界①：无 llm_runtime.json → 回落 config 种子不崩 | 日志/配置核对 | 移走 runtime, 种子本地模型 | `model_context_resolved model=<种子>`、启动不崩 | ⬜ |
| TC-06 | 边界②：runtime 只有 base_url 无 model → standalone 回落磁盘 config | 日志/配置核对 | runtime 删 model 字段, 种子 gpt-5.5 | standalone 取 gpt-5.5（非空串/非默认 gemma） | ⬜ |
| TC-07 | 边界③：runtime 损坏 JSON → standalone 静默回落不崩 | 日志/配置核对 | runtime 非法 JSON, 种子 gpt-5.5 | 无 traceback、回落 config | ⬜ |
| TC-08 | 边界④：改模型档 1M↔400K 重启 → 压缩窗口跟着变 | 日志/配置核对 | model_overrides 切 gpt-5.5 窗口 | `context_window` 1000000↔400000（皆≠32000） | ⬜ |
| TC-09 | PPT 路径：视觉复审用有效模型（standalone 非 hardcode） | windows-mcp 真测 + 日志 | runtime gpt-5.5→改 deepseek 验跟随 | 复审 model 随 runtime 变 | ⬜ |
| TC-10 | 回归：compaction off 不受影响 + 聊天/research 正常 | windows-mcp 真测 + 日志 | compaction off | 无压缩日志 + `model=gpt-5.5` + 聊天/research OK | ⬜ |

> **用例计数**: 共 **10 条**（TC-01 ~ TC-10）。其中 **4 条需 windows-mcp 真测**（TC-03/04/09/10），6 条为真实启动日志 + 配置核对。
>
> **执行结果**: ⬜ 待执行。证据存 `plans/manual-results-2026-06-16-effective-llm-model/`（日志锚点 `wi4_0_compaction_enabled` / `model_context_resolved` + 截图 + 配置快照）。
> ⚠️ 真测前确认启动日志 `[backend_launch] Dev python=... backend_dir=<...>`（跑当前 checkout，非冻结 exe）；且 config 种子确为 gemma（否则测不出 stale，假性 PASS）。

---

## 附：可证伪反例速查（✅修复后 vs ❌若复发）

| 观测点 | ✅ 修复后（正确） | ❌ 若复发（坏） |
|---|---|---|
| `wi4_0_compaction_enabled context_window` | 400000（或 1M override 时 1000000） | **32000**（gemma 走 `_default`，过早压缩浪费窗口 = P-B 本体） |
| `model_context_resolved model` | `gpt-5.5` | `gemma4:e4b` |
| context_usage 环 model / 用量 | gpt-5.5 + 用量按大窗口（环近空） | gemma + 用量按 32K（环偏满，百分比虚高） |
| PPT 视觉复审 model（改 runtime 后） | 跟随 runtime 变（gpt-5.5→deepseek） | 恒 "gpt-5.5"（hardcode 未读 runtime） |
| 无 llm_runtime.json | 回落 config 种子，启动正常 | 崩 / 凭空读出 gpt-5.5 |
| runtime 缺 model / 损坏 JSON | standalone 静默回落磁盘 config | 返空串 / 抛异常 / traceback |
</content>
</invoke>
