# 上下文 & Agent 优化 P0 手工测试用例（windows-mcp 真模拟人）

> **被测范围**: 方向一「上下文管理优化」P0 两项已实现改动：
> - **WI-1B-1 token 计数口径统一**（不挂 flag，直接统一到 CJK-aware `count_text_tokens`）— 验「中文不再被 `/3.5` 低估」。
> - **WI-1B-2 压缩可观测性增强**（flag `ctx_observability`，出厂 **False**）— 验「压缩命中浮 toast『已压缩，省 N token』」+ flag OFF 字节级 BC。
>
> **对应 Plan**: [`plans/2026-06-22-context-and-agent-optimization/01-context-optimization.md`](../../plans/2026-06-22-context-and-agent-optimization/01-context-optimization.md)（§「token 计数现状审计」+ WI-1B-1 / WI-1B-2 测试点章节）。
>
> **最后更新**: 2026-06-22（用例定义，待真机执行回填）

---

## 0. 被测改动摘要（读码核实，2026-06-22）

### WI-1B-1 — token 口径统一（已落地，不挂 flag）

| 文件:行 | 改前口径 | 改后 | 驱动的用户可见物 |
|---|---|---|---|
| `backend/main.py:3586` `_approx_tokens(text)` | `len/3.5` | 委托 `deskpet.agent.tokens.count_text_tokens`（CJK×4 加权） | context 圈圈 gauge / ContextBreakdownModal 的 persona/memory/history 各段 token |
| `backend/main.py:3646` memory 块 | `mem_total_chars/3.5` 累加 | 对每条 fact 的 cat/subj/val 各调 `_approx_tokens` 再求和（`mem_total_tokens`） | Modal「Memory / facts」段 |
| `backend/main.py:3701` history 块 | `total_chars/3.5` | 对每条 turn content 调 `_approx_tokens`（`hist_tokens`） | Modal「Conversation history」段 |
| `backend/deskpet/agent/assembler/components/skill.py:166,223,263` | `len//_CHARS_PER_TOKEN`(=`//4`) | `count_text_tokens(...)`（`_CHARS_PER_TOKEN=4` 仅留作反向 char 预算 `:220`） | skill slice 截断点 |
| `backend/deskpet/memory/eval/metrics.py:267` | `len(block)//4` | `count_text_tokens(block)`（离线指标，非热路径） | memory eval 离线指标 |

**口径锚点**（`backend/deskpet/agent/tokens.py:51-72`）：`_weighted_chars` = `ascii + ascii//7 + cjk*4`，`count_text_tokens` = `_weighted_chars // 4`。
→ **纯中文文本 ≈ `cjk*4 // 4` ≈ 1 token/字**（接近字符数级别）；旧 `/3.5` 把中文低估到 ~0.29 token/字（约 **3.4 倍低估**）。这是 1B-1 判定的核心锚点。
→ 纯 ASCII：`(ascii + ascii//7)//4 ≈ ascii/3.5`，与旧 `/3.5` **几乎一致**（口径变化应很小）——这是 1B-1 的「ASCII 边界」对照。

> ⚠️ **设计阶段发现的残留瑕疵（建议顺手核对，非 P0 阻断）**：`tauri-app/src/components/ContextBreakdownModal.tsx:179` 的构成栏文案仍写死「前端估算 · ~3.5 chars/token」。后端 `_approx_tokens` 已统一到 CJK-aware 口径后，此文案**已过时**（不再是 3.5 chars/token）。TC-1B1-5 专门核对此处——若文案没改，标为「已知文案漂移」缺陷而非测试阻断。

### WI-1B-2 — 压缩可观测 toast（已落地，flag OFF）

- **flag**: `backend/config.py:442 FeaturesConfig.ctx_observability: bool = False`（出厂 OFF）。
- **事件链**（仅 flag ON）：
  1. `backend/agent/agent_loop.py:984` 压缩成功分支：`if self.ctx_observability:` → `metrics_sink.record("context_compacted", {ratio, model, count})`（`count = tokens_in - tokens_out`）+ `yield ContextCompactedEvent(reduction, tokens_in, tokens_out, model)`（`agent_loop.py:1002`，类定义 `:425`）。
  2. `backend/main.py:7116` 事件转发层：`elif isinstance(ev, _CtxCompactedEv):` → 发 WS 消息 `{type:"context_compacted", payload:{reduction, tokens_in, tokens_out, model, session_id}}`。
  3. 前端 `tauri-app/src/hooks/useContextCompactedToast.ts:24-29`：监听 ControlChannel `context_compacted` → `showToast("已压缩，省 N token")`（`N = max(0, tokens_in - tokens_out)`）。
  4. 渲染槽 `tauri-app/src/App.tsx:2257`：绿色 fixed toast（top-right，`#15803d`），auto-clear 4s。
- **flag OFF = 字节级 BC**：`agent_loop.py:984` 整块 short-circuit → 不 record metrics、不 yield 事件、不发 WS；现有 `context_compressor.py:423 logger.info("context_compacted", ...)`（structlog → stderr）**照常**。前端那条 WS 永不到达 → 无 toast。

---

## 1. 禁止的绕过方式（HARD CONSTRAINT — 违反即视为未完成）

> 见 `CLAUDE.md`「🔒 手工测试纪律」。本文档**所有标「UI 真测」的 case 必须真模拟人**：
> windows-mcp Screenshot/Snapshot 抓状态 → 真坐标 SetCursorPos+SendInput 点击 / Clipboard 粘贴+Ctrl+V → 截图验证 → grep tauri dev log 判定。

- ❌ **不允许** WebSocket 直连 backend 发 `context_breakdown_request` / 注入 `context_compacted` 当 UI 证据（协议层 ≠ 用户行为）。
- ❌ **不允许** pytest / `import` backend 模块查 `count_text_tokens` 返回值 / 查 registry 当「圈圈数字对了」证据（证明代码加载 ≠ 用户看得到）。
- ❌ **不允许** 因 windows-mcp Click schema bug / SendKeys 中文 IME 报错就 fallback 到上述方式 —— 用 workaround（SetCursorPos+SendInput / Clipboard+Ctrl+V）克服，retry ≥3 次不同手法才可标「环境受限」。
- ✅ **必须**：每个动作前 declare `坐标=(x,y) | 动作=click/type | 期望=…`；截图存 `screenshots/`；log 证据贴 grep 锚点。

---

## 2. 测试前置 / 环境配方（照 CLAUDE.md 坑 #7/#8/#9）

| 项 | 要求 |
|---|---|
| **跑当前 checkout 代码** ★ | **不要手动起 backend**（Tauri 自己 spawn）。只给 **Tauri 进程**注入 env：<br>`DESKPET_BACKEND_DIR=G:\projects\deskpet\backend`<br>`DESKPET_PYTHON=G:\projects\deskpet\backend\.venv\Scripts\python.exe`<br>`DESKPET_BACKEND_PORT=8100`<br>`DESKPET_DEV_MODE=1`<br>启动日志须出现 `[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`。**若见 `[backend_launch] Bundled exe=...` 说明跑旧 frozen exe（无本改动 → 白测，先修环境）。** |
| **不双起 backend / vite** ★ | 坑 #7/#9：**只**跑 `npx tauri dev`（带上面 env），它自管唯一 vite + spawn backend。别另手动 `python main.py`（端口 8100 双占 → 桌宠弹「启动失败」）或 `npm run dev:relay`（双 vite 抢 strictPort）。 |
| **登录态** | 走 onboarding 用 `LOCAL-DEV-CREDENTIALS.md`（gitignored）的 dev 账号登录中转站，等 relay 下发 key 写 keychain。**截图前先关 onboarding 窗**，避免截到账号密码（CLAUDE.md 安全约束）。`userdata/llm_runtime.json` 应存在且 `"model":"gpt-5.5"`。 |
| **真实 user_data_dir** ★ | 改 config / overrides 时注意：app 真实 user_data_dir 是 `%APPDATA%\deskpet`（**不是** `backend/userdata`）—— 改错文件无效（compaction phase1 踩坑记录）。 |
| **改 TOML 用 Write 工具，不要 PowerShell `Out-File`** ★ | `Out-File -Encoding utf8` 会给 TOML 加 BOM → `tomllib` 解析失败。改 config 用 Write 工具（无 BOM）或确保 utf8-no-bom。 |
| **抓日志** | tauri dev 重定向 log（backend structlog 走 stderr → `Stdio::inherit()` → 落 tauri dev log）。grep 锚点见各 TC。 |
| **截图/日志存档** | 截图存 `testcase/2026-06-22-context-agent-opt-P0/screenshots/<case-id>.png`；log grep 片段贴进各 case「log 证据」栏。 |

### 启动命令（参考；按本机路径调整）

```powershell
# 0) 先关旧桌宠（坑 #1：TaskStop 留 orphan）
taskkill /F /IM deskpet.exe 2>$null
# 关掉残留 Vite（按 dev 端口找）

# 1) 注入 env 启动 Tauri（Tauri 自己 spawn backend，别手动起 backend）
$env:DESKPET_BACKEND_DIR = "G:\projects\deskpet\backend"
$env:DESKPET_PYTHON      = "G:\projects\deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_BACKEND_PORT = "8100"
$env:DESKPET_DEV_MODE    = "1"
# 在 tauri-app 目录跑：
npx tauri dev
```

### log grep 锚点速查

```powershell
# 跑当前码（非 frozen）
#   期望: [backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend
# 1B-2 压缩命中（structlog 照常，OFF/ON 都有）
#   期望: context_compacted middle_tokens_in=... summary_tokens_out=... reduction=... model=... window=...
# 1B-2 flag ON 才有的 metrics 落盘
#   期望: metrics_sink 收到 event="context_compacted" ratio=... count=...
# 1B-2 flag ON：context_usage 圈圈数据（last_usage_prompt_tokens 对账用）
#   期望: 启动时 wi4_0_compaction_enabled / 每轮 context_usage payload
```

---

# 第一部分 — WI-1B-1 token 口径统一（验「中文不再低估」）

> ⚙️ 1B-1 改动驱动 **context 圈圈 gauge → ContextBreakdownModal** 的各段估值。
> **用户入口**：桌宠 Toolbar 右上的 ContextRing 圈圈（`tauri-app/src/components/Toolbar.tsx:175`）→ 点击 → `onContextRingClick` → `App.tsx:2178 setContextModalOpen(true)` → 弹 `ContextBreakdownModal`（顶部进度条 + 4 段：system/memory/tools/history，每段可展开看 preview）。
> **判定锚点**：中文段的 token 估值 ≈ **字符数级别**（CJK ≈ 1 token/字），**不再是 `len/3.5` 的离谱低估**；与圈圈数据里 `last_usage_prompt_tokens`（relay 权威值）对账，估值不再「远小于真实」。

---

## TC-1B1-1 — 纯中文多轮对话 → 圈圈/Modal 的中文段 token 接近字符数（★ 必过）

**类型**: UI 真测（真模拟人）+ 后端日志对账

**目的**: 累积可观的中文 memory/history 后，打开 ContextBreakdownModal，核对 memory/history 段的 token 数 ≈ 字符数级别（CJK×~1 token/字），证明 1B-1 把 `/3.5` 改成 CJK-aware 口径后**中文不再被低估**。

**前置**: §2 全满足；启动日志确认 Dev python（非 Bundled exe）；onboarding 已登录（圈圈需要至少一次 LLM 调用才有 `last_usage_prompt_tokens`）。

| 步骤 | 动作（declare：坐标 / 动作 / 期望） | 期望结果 |
|---|---|---|
| 1 | Screenshot 抓桌宠主界面，记录对话输入框坐标 `(x,y)` + Toolbar 右上圈圈坐标 `(rx,ry)` | 截图 `screenshots/TC-1B1-1-step1.png`；圈圈可见 |
| 2 | `坐标=(x,y) \| 动作=Clipboard "请帮我详细介绍宁德时代二零二四年的经营情况，包括营业收入、净利润、研发投入、动力电池装机量、储能业务进展，以及在欧洲和北美的市场布局" → Ctrl+V → Enter` | 桌宠正常答复（中文长答复，累积 history） |
| 3 | 再发 2~3 轮中文追问（如「再说说它的供应链和上游锂矿布局」「和比亚迪相比有什么优势」），让中文 history/memory 累积可观 | 多轮中文对话累积；圈圈数字随对话上涨 |
| 4 | `坐标=(rx,ry) \| 动作=click 圈圈 gauge \| 期望=弹 ContextBreakdownModal` | ContextBreakdownModal 打开，显示 system/memory/tools/history 4 段 |
| 5 | Screenshot 抓 Modal，展开「Memory / facts」与「Conversation history」段看各段 token + preview | 截图 `screenshots/TC-1B1-1-step5.png` |
| 6 | 对照 preview 里的中文字数粗算：history 段 token 数应 ≈ 该段中文字符数（CJK ≈ 1 token/字），**不是**字符数的 ~1/3.5 | history/memory 段 token 与中文字符数同量级 |
| 7 | grep tauri dev log，找该 session 最近 `last_usage_prompt_tokens`（或 Modal 底部「LLM 实测」数字），与「估算合计」对照 | 估算合计 **不再远小于** LLM 实测（中文不再低估到 1/3.5）；同量级或略偏 |

**可观测证据**:
- ✅ 修复后：中文 history/memory 段 token ≈ 字符数级别；Modal 底部「估算合计」与「LLM 实测」同量级，不再出现「估算 ≪ 实测」的 3 倍以上离谱低估。
- ❌ 复发：中文段 token ≈ 字符数 / 3.5（明显低估）；或「估算合计」只有「LLM 实测」的 ~1/3 → 说明 `_approx_tokens` 还在跑 `/3.5`（环境跑了旧 frozen exe，或改动没生效）。

**PASS 判据**: 步骤 6 中文段 token 与字符数同量级（CJK≈1 token/字）**且** 步骤 7 估算合计与 LLM 实测不再差 3 倍以上。
**FAIL 判据**: 中文段 token ≈ 字符数/3.5，或估算 ≪ 实测（3 倍以上低估）。

**判定**: _待真机回填_

---

## TC-1B1-2 — 纯 ASCII 对话对照（口径变化应很小，边界）

**类型**: UI 真测 + 对照

**目的**: 1B-1 对纯 ASCII 文本的口径变化应**很小**（新口径 `(ascii+ascii//7)//4 ≈ ascii/3.5`，与旧 `/3.5` 几乎一致）。这条是「不是所有文本都暴涨」的边界对照，防止误判「圈圈对所有内容都翻倍」。

**前置**: 同 TC-1B1-1。建议**新开会话 / 清空上下文**后只跑英文，避免混入 TC-1B1-1 的中文残留。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | `坐标=(x,y) \| 动作=Clipboard "Please give me a detailed overview of CATL financial results in 2024 including revenue net profit RnD spending battery installation and overseas market expansion in Europe and North America" → Ctrl+V → Enter` | 桌宠英文答复 |
| 2 | 再发 1~2 轮英文追问，累积 ASCII history | ASCII history 累积 |
| 3 | `坐标=(rx,ry) \| 动作=click 圈圈 \| 期望=弹 Modal`，展开 history 段 | 截图 `screenshots/TC-1B1-2-step3.png` |
| 4 | 粗算：ASCII history 段 token ≈ 字符数 / 3.5（约 0.29 token/字符），与「中文≈1 token/字」明显不同 | ASCII 段 token 明显小于字符数（~1/3.5），与 TC-1B1-1 的中文段形成对照 |

**可观测证据**:
- ✅ ASCII 段 token ≈ 字符数/3.5（约 0.28~0.30 token/字符），与旧口径几乎一致——证明 1B-1 只「修中文低估」，没把英文也乱改。
- ❌ ASCII 段 token ≈ 字符数（≈1 token/字符）→ 说明把 ASCII 也按 CJK 加权了（口径写错）。

**PASS 判据**: ASCII history 段 token ≈ 字符数/3.5（与中文段 1 token/字明显不同）。
**FAIL 判据**: ASCII 段 token ≈ 字符数（误把 ASCII 当 CJK）。

**判定**: _待真机回填_

---

## TC-1B1-3 — 中英混合对话（加权介于两者之间，边界）

**类型**: UI 真测 + 对照

**目的**: 中英混合文本的 token 估值应介于「纯中文（1 token/字）」与「纯英文（1/3.5）」之间，验加权对混合内容平滑生效，不崩。

**前置**: 同上，建议新会话。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | `坐标=(x,y) \| 动作=Clipboard "帮我对比 CATL 和 BYD 的 battery technology，包括 LFP 和 NCM 路线，以及它们在 energy storage system（ESS）市场的份额" → Ctrl+V → Enter` | 桌宠混合答复 |
| 2 | 再发 1~2 轮混合追问 | 混合 history 累积 |
| 3 | `坐标=(rx,ry) \| 动作=click 圈圈 \| 期望=弹 Modal`，展开 history 段，看 preview 含中英混合 | 截图 `screenshots/TC-1B1-3-step3.png`；token 数在「字符数」与「字符数/3.5」之间 |

**可观测证据**:
- ✅ 混合段 token 数介于纯中文与纯英文之间（中文字符贡献多、英文贡献少）；preview 正常渲染。
- ❌ Modal 渲染崩溃 / token 为 0 / NaN → 加权对混合内容出 bug。

**PASS 判据**: 混合段 token 介于两者之间且 Modal 正常渲染。
**FAIL 判据**: token=0/NaN 或 Modal 崩。

**判定**: _待真机回填_

---

## TC-1B1-4 — 空会话 / 全新会话回归（圈圈不崩、Modal 正常）

**类型**: UI 真测（回归）

**目的**: 验空会话（无 history、memory 可能为空）下圈圈 gauge 不崩、Modal 各段 token=0 正常渲染（不报错/不 NaN），证明 1B-1 的 `if not text: return 0` 守住了 falsy 输入。

**前置**: 刚启动 / 新会话，**未发任何消息**（或发一条后立即看）。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 刚启动桌宠（未对话）Screenshot 看圈圈 | 圈圈呈 dim 态（无 LLM 调用记录，`ContextRing.tsx:97 dim` 逻辑），不崩 |
| 2 | `坐标=(rx,ry) \| 动作=click 圈圈 \| 期望=弹 Modal` | Modal 打开；可能显示「暂无数据」（尚无 context_usage snapshot）或各段 token=0 |
| 3 | Screenshot 抓 Modal 空态 | 截图 `screenshots/TC-1B1-4-step3.png`；无报错、无 NaN、无 `undefined` |
| 4 | 发一条很短消息「你好」后再开 Modal | 各段 token 出现小正整数，无异常 |

**可观测证据**:
- ✅ 空会话圈圈 dim 不崩；Modal 空态优雅；短消息后 token 为合理小整数。
- ❌ 圈圈/Modal 渲染抛错（白屏 / NaN% / token=undefined）。

**PASS 判据**: 空态 + 短消息态都不崩、无 NaN/undefined。
**FAIL 判据**: 任一态崩溃或显示 NaN/undefined。

**判定**: _待真机回填_

---

## TC-1B1-5 — Modal 构成栏文案核对（已知文案漂移，best-effort）

**类型**: UI 真测（文案核对）

**目的**: 设计阶段读码发现 `ContextBreakdownModal.tsx:179` 构成栏仍写死「构成（前端估算 · ~3.5 chars/token）」。后端 `_approx_tokens` 已统一到 CJK-aware 口径，此文案**已过时**。本 TC 核对该文案是否已随 1B-1 同步修正。

**前置**: 任意有数据的会话，开 Modal。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 开 ContextBreakdownModal，看构成区标题文案 | Screenshot `screenshots/TC-1B1-5-step1.png` |
| 2 | 核对文案是否还写「~3.5 chars/token」 | **已于 1B-1 收尾修复**：`ContextBreakdownModal.tsx:179` 文案已改为「构成（后端估算 · CJK-aware tokens）」。预期 PASS（不再写 3.5 chars/token） |

**可观测证据**:
- ✅（理想）文案不再宣称固定 3.5 chars/token。
- ⚠️（当前已知）文案仍写「~3.5 chars/token」→ 标为「已知文案漂移」缺陷，**不阻断** P0；建议顺手修。

**PASS 判据**: 文案与后端实际口径一致（不再误导）。
**FAIL/WARN 判据**: 文案仍写「~3.5 chars/token」（与 CJK-aware 实现不符）→ 记 WARN。

**判定**: _待真机回填（预期 WARN：文案大概率未改）_

---

## TC-1B1-6 — 压缩触发时机变化无害（回归，best-effort）

**类型**: UI 真测 + 后端日志（回归）

**目的**: 1B-1 改了 token 估值（中文升到真实值）→ 可能影响压缩触发判断。验长中文对话下压缩**不过早 / 不过晚**触发，且触发后桌宠不崩（变化无害）。

**前置**: 同 TC-1B1-1；如需逼触发，可临时在 `%APPDATA%\deskpet\model_overrides.toml` 给出站模型写小窗口（`context_window=8000`）+ `[features] compaction_enabled=true`（验完务必删 override 行）。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 跑一个长中文 deepresearch / 连续多轮中文追问，把 token 顶过触发线 | 多轮工具调用 / 长对话累积 |
| 2 | grep tauri dev log `context_compacted` / `p1_4_compaction_fired` | 出现压缩命中行；reduction/middle_tokens_in 合理（非 0 中段也能压） |
| 3 | 观察：压缩是否在合理水位触发（不是刚说两句中文就压，也不是顶爆才压） | 触发水位与窗口阈值相称（中文估值升高后触发点提前但合理） |
| 4 | Screenshot 抓压缩后桌宠继续正常答复 | 截图 `screenshots/TC-1B1-6-step4.png`；对话未崩 |

**可观测证据**:
- ✅ 压缩在相称水位触发，桌宠继续答复，无「估值暴涨→每轮都压」或「永不触发」。
- ❌ 中文估值暴涨导致一发中文就压（过早），或压缩后桌宠失忆/崩溃。

**PASS 判据**: 压缩触发水位相称 + 压缩后桌宠正常。
**FAIL 判据**: 过早/过晚触发明显异常，或压缩后崩。

**判定**: _待真机回填（真机逼触发需小窗口 + 长对话，标 best-effort）_

---

# 第二部分 — WI-1B-2 压缩可观测 toast（flag ctx_observability）

> ⚙️ flag `ctx_observability` 出厂 **False**。OFF 时压缩照常落 structlog 但前端**无 toast、无 WS `context_compacted`**；ON 时压缩命中后前端在圈圈附近浮绿色 toast「已压缩，省 N token」（`N = tokens_in - tokens_out`）。
>
> **关键前置（必读）**：
> 1. **开 flag**：在 **`%APPDATA%\deskpet\config.toml`**（真实 user_data_dir，**不是** `backend/userdata`）的 `[features]` 段加 `ctx_observability = true`，**重启桌宠**让 backend 重读（`main.py:1053` 从 `cfg.features.ctx_observability` 读）。
> 2. **改 TOML 用 Write 工具**（PowerShell `Out-File utf8` 加 BOM 会让 `tomllib` 炸）。
> 3. **逼触发压缩**：gpt-5.5 默认窗口 400K 难顶满。在 `%APPDATA%\deskpet\model_overrides.toml` 给出站模型写 `context_window = 8000` + `[features] compaction_enabled = true`，逼几轮内触发。**验完务必删 override 行 + 按需还原 compaction_enabled**。
> 4. 触发真实压缩需**长 agentic / 多工具任务**（一轮多 web_fetch 累积 tool_result 最稳），不是单纯多打字。

---

## TC-1B2-1 — flag OFF（出厂态）→ 压缩触发但无 toast、无 WS（★ 必过 BC）

**类型**: UI 真测（真模拟人）+ 后端日志判定 — **字节级 BC 验收**

**目的**: 出厂态（`ctx_observability` 不写 / =false）下，跑长任务触发压缩，断言：① 前端**无** toast；② 前端**无** `context_compacted` WS 消息；③ backend log **仍有** `context_compacted`（structlog 照常，证明压缩真发生只是不上报前端）。

**前置**: §2 全满足；**确保 `%APPDATA%\deskpet\config.toml` 未开 `ctx_observability`（出厂 false）**；`model_overrides.toml` 已把出站模型窗口调 8000 + `compaction_enabled=true` 逼触发；已重启。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认 config 无 `ctx_observability=true`（默认 OFF）；启动日志确认 Dev python | flag OFF 态 |
| 2 | `坐标=(x,y) \| 动作=Clipboard "帮我深度研究 2024 年中国动力电池行业格局，要多查几个来源详细分析" → Ctrl+V → Enter` | 桌宠跑多轮工具调用，tool_result 累积 |
| 3 | 跑到触发压缩（必要时追问 1~2 条让 token 继续涨） | working_messages 顶过触发线 |
| 4 | grep tauri dev log `context_compacted` | **有** `context_compacted middle_tokens_in=... reduction=... model=... window=8000`（structlog 照常） |
| 5 | 全程观察前端右上角：**不应**出现绿色「已压缩，省 N token」toast | 截图 `screenshots/TC-1B2-1-step5.png`；无 ctxToast |
| 6 | （可选，不替代 UI 判定）确认前端 console / WS 流里无 `type:"context_compacted"` 消息 | 无该 WS 消息（OFF=不发） |

**可观测证据**:
- ✅ BC 成立：backend log 有 `context_compacted`（structlog），但前端无绿色 toast、无 WS `context_compacted`。
- ❌ BC 破坏：flag OFF 却弹了 toast / 发了 WS（说明 short-circuit 没守住，`agent_loop.py:984` 的 `if self.ctx_observability` 判错）。

**PASS 判据**: 步骤 4 有 structlog `context_compacted` **且** 步骤 5 无前端 toast。
**FAIL 判据**: flag OFF 却出现 toast 或 WS `context_compacted`。

**判定**: _待真机回填_

---

## TC-1B2-2 — flag ON → 压缩命中浮 toast「已压缩，省 N token」+ 对账 N（★ 必过）

**类型**: UI 真测（真模拟人）+ 后端日志对账

**目的**: 开 `ctx_observability=true` 重启后，跑长 agentic 多工具任务触发压缩 → 前端右上角浮绿色 toast「已压缩，省 N token」→ grep tauri dev log `context_compacted` 对账 **N = tokens_in − tokens_out** 与 toast 显示一致。

**前置**: 在 `%APPDATA%\deskpet\config.toml` 的 `[features]` 加 `ctx_observability = true`（Write 工具，无 BOM）；`model_overrides.toml` 窗口 8000 + `compaction_enabled=true`；**重启桌宠**；启动日志确认 Dev python。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认 config 已开 `ctx_observability=true` 且已重启 | flag ON 态 |
| 2 | `坐标=(x,y) \| 动作=Clipboard "深度研究：对比宁德时代和比亚迪 2024 年财报，多查几个来源做详细对比分析" → Ctrl+V → Enter` | 桌宠跑多轮 web 工具调用，tool_result 累积 |
| 3 | 跑到触发压缩（必要时追问让 token 继续涨） | 触发压缩 |
| 4 | 紧盯前端右上角：压缩命中后应浮绿色 toast「已压缩，省 N token」（4s 自动消失） | 截图 `screenshots/TC-1B2-2-step4.png`（抢在 4s 内截）；toast 文案含「已压缩，省」+ 一个正整数 token |
| 5 | grep tauri dev log `context_compacted`（structlog）那行的 `middle_tokens_in` / `summary_tokens_out`（或 metrics `count`） | 后端记录的 `tokens_in - tokens_out`（即 metrics `count`）与 toast 里的 N 一致（或同量级，取 ContextCompactedEvent 的 in/out） |
| 6 | grep metrics（flag ON 才有）`event="context_compacted" ratio=... count=...` | metrics_sink 收到一条 `context_compacted`（`count = tokens_in - tokens_out`） |

**可观测证据**:
- ✅ ON 生效：右上角浮绿色「已压缩，省 N token」toast；N 与后端 `tokens_in - tokens_out` 对得上；metrics 落了一条 `context_compacted`。
- ❌ 复发：flag ON 但压缩命中后**无** toast（事件没 yield / WS 没转 / 前端 hook 没订阅），或 N 与后端数字对不上（取值取错字段）。

**PASS 判据**: 步骤 4 出现 toast **且** 步骤 5 N 与后端 `tokens_in - tokens_out` 一致（或同量级）。
**FAIL 判据**: ON 却无 toast，或 N 与后端数字明显不符。

**判定**: _待真机回填_

> ⚠️ **难点诚实标注（best-effort）**：toast 仅 4s（`App.tsx:739` auto-clear 4000ms），且压缩命中时机不可精确预测。windows-mcp 截图可能错过窗口期。**workaround**：① 把 toast 命中前先连续 Screenshot 轮询；② 若反复错过，用 `context_compacted` WS 消息到达时刻 + 前端 console 日志旁证 toast 触发（**但 UI toast 截图仍是主证据，console 只作辅证**，不替代）；③ 实在抓不到截图，retry ≥3 次不同触发剧本后才可标「toast 截图环境受限（事件已由 WS + metrics + 单测三方旁证）」并等用户确认。

---

## TC-1B2-3 — flag ON：连续多次压缩 → 多次 toast，N 各自对账（边界）

**类型**: UI 真测 + 后端日志

**目的**: 验多次压缩各自独立浮 toast（不是只弹一次后哑火），且每次 N 对应当次压缩的 `tokens_in - tokens_out`。同时验前一条 toast 4s 消失后第二条能正常浮（渲染槽复用不卡死）。

**前置**: 同 TC-1B2-2（flag ON + 小窗口）。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 持续追问 / 持续多轮工具调用，把 token 反复顶过触发线 ≥2 次 | 触发 ≥2 次压缩 |
| 2 | 每次压缩命中观察右上角 toast | 每次都浮一条「已压缩，省 N token」（截图 `screenshots/TC-1B2-3-step2-a.png` / `-b.png`） |
| 3 | grep tauri dev log，确认 ≥2 条 `context_compacted` + 对应 metrics ≥2 条 | 每次 toast 对一次后端压缩事件，N 各自对账 |

**可观测证据**:
- ✅ 多次压缩各浮一条 toast，N 各自对应；toast 槽不卡死。
- ❌ 只弹首次后哑火，或多次压缩 toast 数对不上后端事件数。

**PASS 判据**: toast 次数与后端 `context_compacted` 次数匹配（同量级），N 各自合理。
**FAIL 判据**: toast 哑火 / 次数明显不符。

**判定**: _待真机回填（连续触发难稳定，best-effort）_

---

## TC-1B2-4 — flag ON→OFF 重启回归（关 flag 后回到无 toast，BC 可逆）

**类型**: UI 真测 + 后端日志（回归）

**目的**: 验 flag 是干净可逆的：从 ON 改回 OFF（删 / 改 false）重启后，压缩照常发生但前端回到无 toast 态（= TC-1B2-1 行为），证明 flag 不留副作用。

**前置**: 先做完 TC-1B2-2（ON 态已验）；然后把 `%APPDATA%\deskpet\config.toml` 的 `ctx_observability` 删除或改 false，重启。

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 改 config `ctx_observability=false`（或删行）→ 重启桌宠 | flag 回 OFF |
| 2 | 重跑 TC-1B2-2 的触发剧本 | 触发压缩 |
| 3 | grep `context_compacted` 确认压缩发生 | structlog 有 `context_compacted` |
| 4 | 观察前端：**不再**浮 toast | 截图 `screenshots/TC-1B2-4-step4.png`；无 ctxToast |

**可观测证据**:
- ✅ 关 flag 后压缩照常但无 toast，与 TC-1B2-1 一致 → flag 可逆无残留。
- ❌ 关了 flag 仍弹 toast（flag 读取缓存了 / 重启没重读 config）。

**PASS 判据**: OFF 重启后压缩发生但无 toast。
**FAIL 判据**: OFF 仍弹 toast。

**判定**: _待真机回填_

---

## 3. 用例数统计 + 必过 / best-effort / env-limited 分级

| 分组 | 用例 | 类型 | 等级 |
|---|---|---|---|
| **1B-1** | TC-1B1-1 中文段 token 接近字符数 | UI 真测 + log | **★ 必过** |
| 1B-1 | TC-1B1-2 纯 ASCII 对照（口径变化小） | UI 真测 | 必过 |
| 1B-1 | TC-1B1-3 中英混合（加权介于两者） | UI 真测 | 必过 |
| 1B-1 | TC-1B1-4 空/新会话回归（不崩） | UI 真测 | 必过 |
| 1B-1 | TC-1B1-5 Modal 文案核对（~3.5 chars 漂移） | UI 真测 | best-effort（预期 WARN） |
| 1B-1 | TC-1B1-6 压缩触发时机变化无害 | UI 真测 + log | best-effort（需小窗口逼触发） |
| **1B-2** | TC-1B2-1 flag OFF 无 toast/WS（structlog 仍有） | UI 真测 + log | **★ 必过（BC）** |
| **1B-2** | TC-1B2-2 flag ON 浮 toast + 对账 N | UI 真测 + log | **★ 必过** |
| 1B-2 | TC-1B2-3 连续多次压缩多次 toast | UI 真测 + log | best-effort（连续触发难稳定） |
| 1B-2 | TC-1B2-4 ON→OFF 重启回归（可逆） | UI 真测 + log | 必过 |

- **总计 10 个 TC**（1B-1：6 个；1B-2：4 个）。
- **★ 必过（4 个）**：TC-1B1-1、TC-1B2-1、TC-1B2-2 + （BC 维度）TC-1B2-1。
- **best-effort（3 个）**：TC-1B1-5（文案漂移核对，预期 WARN）、TC-1B1-6（逼触发需小窗口 + 长对话）、TC-1B2-3（连续多次压缩难稳定触发）。
- **env-limited 风险**：TC-1B2-2 的 toast 截图（仅 4s 窗口 + 压缩时机不可控）—— 已在该 TC 标 workaround，retry ≥3 次不同剧本后才可标环境受限并等用户确认。

---

## 4. 结果汇总（待真机执行回填）

| Case | 范围 | 类型 | 等级 | 判定 |
|---|---|---|---|---|
| TC-1B1-1 | 中文段 token ≈ 字符数 | UI 真测 + log | ★ | _待回填_ |
| TC-1B1-2 | 纯 ASCII 对照 | UI 真测 | — | _待回填_ |
| TC-1B1-3 | 中英混合 | UI 真测 | — | _待回填_ |
| TC-1B1-4 | 空/新会话回归 | UI 真测 | — | _待回填_ |
| TC-1B1-5 | Modal 文案核对 | UI 真测 | best-effort | _待回填（预期 WARN）_ |
| TC-1B1-6 | 压缩触发时机无害 | UI 真测 + log | best-effort | _待回填_ |
| TC-1B2-1 | flag OFF 无 toast/WS（★BC） | UI 真测 + log | ★ | _待回填_ |
| TC-1B2-2 | flag ON 浮 toast + 对账 N | UI 真测 + log | ★ | _待回填_ |
| TC-1B2-3 | 连续多次 toast | UI 真测 + log | best-effort | _待回填_ |
| TC-1B2-4 | ON→OFF 重启可逆 | UI 真测 + log | — | _待回填_ |

> **恢复环境（验完必做）**: 删 `%APPDATA%\deskpet\model_overrides.toml` 的小窗口 override 行；按需把 `[features] compaction_enabled` / `ctx_observability` 改回出厂默认（compaction_enabled 现默认 True；ctx_observability 默认 False）。
> **结果存档**: 截图存 `testcase/2026-06-22-context-agent-opt-P0/screenshots/`；执行后日志证据贴回各 case 的 log 证据栏与本汇总表。
