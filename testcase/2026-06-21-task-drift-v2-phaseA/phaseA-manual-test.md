# 任务漂移 v2 · 阶段 A（T0-1 原话夺权 + T0-2 fanout 隔离）手工测试文档（windows-mcp 真模拟人）

> **被测**：任务漂移修复 **v2 阶段 A** — **T0-1 deepresearch 原话夺权** + **T0-2 fanout 隔离验证**。
> **commit**：master @ **15817813**（实现时以 `git log` 复核）。
> **对应 plan**：`plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md`（§0 链路澄清 / §1 T0-1 全替换表 / §2 T0-2 / §7 真机口径）；根因 `plans/2026-06-21-task-drift-fix-v2/00-research-and-best-fix.md`（attention sink / distractor interference / 相邻领域必失效）。
>
> ---
>
> ## ★★ 阶段 A 只治「层 2」——执行/判定前必须读懂这条，否则会误判 ★★
>
> 漂移精确发生在**两层**（plan §0）：
> - **层 1（上游·主 agent loop）**：主 loop 带着旧 L2 历史（钠离子/CATL）生成 deepresearch 工具调用，**它选的 `topic` 参数本身就漂了**。**本阶段 A 不治层 1**——治层 1 是 **T1-1（会话作用域切分）**，不在本文档范围。
> - **层 2（下游·deepresearch 内部）**：deepresearch 拿到漂了的 `topic`，**但内部应以 `user_request`（用户原话，Fix B 在 dispatch 注入）为唯一权威主题源** → 报告标题 / 文件名 slug / sub_questions / 搜索 / 排序 / 正文全用原话。**T0-1 就是治这一层。**
>
> ### 🚨 这导致一个反直觉但关键的判定口径
> **`p5s2_tool_call_args_dump` 里 LLM 出站的 `topic` 参数，本阶段 A 后【仍可能漂】**（如发"固态电池"它可能仍写 `topic=钠离子电池`）——**那是层 1 的病，T1-1 才治，本阶段不判它**。
>
> **阶段 A 的判定锚点 = 落盘报告的「主题」**，而**不是** p5s2 的 topic 参数：
> 1. **落盘报告文件名 slug**（`DeepResearch/<slug>-<ts>.md` → slug 必须=用户原话主题）。
> 2. **报告 markdown 一级标题**（`# <原话主题>`，`research_tools.py:634/1156/1834` 用 `request_topic`）。
> 3. **报告正文 / sub_questions 主题**（讲的是原话主题）。
> 4. **辅证**：`task_drift_user_request_injected tool=deepresearch req_len=<N>`（Fix B 把原话注入了）；`p5s2_tool_call_args_dump`（**仅用于观察「topic 漂没漂 = 层 1 状态」，不作 PASS 判据**）。
>
> > ★★ **招牌纵深价值**：钠离子历史压力下发"固态电池"deepresearch → 即使 p5s2 的 `topic` 漂成钠离子，**落盘报告 = 固态电池**。这就是 T0-1 把报告主题从层 1 漂移里「夺回来」的纵深证明。
>
> ---
>
> ### 被测代码（master @ 15817813，实现以 grep 复核行号）
> - **T0-1（`backend/deskpet/tools/research_tools.py`）**：
>   - `:1372-1374` `_ur=(user_request or topic).strip()` → `request_topic = _ur`（canonical/authoritative）、`llm_topic = topic`（untrusted candidate）。
>   - `request_topic` 贯穿：sub_questions 兜底 `:1428/1442`、plan prompt `user_request=request_topic` `:1433`、fanout root `topic=request_topic`+`user_request=request_topic` `:1451/1459`、query expansion `:1469`、search owner `:1484`、`_topic_keywords` `:1535`、`infer_topic_velocity` `:1538`、gap followup `:1687`、semantic scorer `:1721`、llm rerank `:1755`、no-results `:1777/1778`、synth `:1793-1795`、passages-only fallback `:1801/1805`、最终 `ResearchReport(topic=request_topic)` `:1834`、fanout synth 标题 `# {request_topic}` `:565/574/634`、fallback 标题 `:1156/1160`。
>   - handler 落盘 `:2164` `save_topic = report.topic or (user_request or topic)` → `_save_report(save_topic)` → `title_slug(save_topic, max_grapheme=40)` `:2200`（`office_paths.py:251`）。
> - **T0-2（fanout 隔离，plan §2）**：`research_tools.py::_run_subagent_fanout`[~1131] 递归 `deepresearch(q, user_request=q, scheduler=None, skip_plan=True)`；`deskpet/agent/subagent_scheduler.py:111` 仅调度并发、**不构造 messages / 不读 parent 历史**（子代理天然不继承主会话历史）。sub_questions 正确性由 T0-1（plan 从 `request_topic` 派生 + fanout root `topic=request_topic`）保证。
> - **硬 log 锚点**：
>   - `task_drift_user_request_injected tool=<name> req_len=<N>`（`agent_loop.py:79`，Fix B 注入瞬间打）。
>   - `subagent_scheduled kind=<k> run_id=<parent_sid>.par-<task_id> task_id=<id>`（`subagent_scheduler.py:111`，每个 fanout 子代理调度时打）。
>   - `p5s2_tool_call_args_dump`（`providers/openai_compatible.py:1295`，LLM 出站工具调用原始 args 前 100 字，**含它自选的 topic**；本阶段**只观察层 1 漂没漂，不作判据**）。
>
> ### 落盘报告目录（判定核心位置）
> - **Dev 跑法（本文档）**：`deepresearch_dir()`（`backend/paths.py:142`）开发态优先 = **仓库根 `G:\projects\deskpet\DeepResearch\`**（git status 已见 `?? DeepResearch/`）；兜底 `~/DeskPet/DeepResearch`。
> - 报告文件名格式 `<slug>-<unix_ts>.md`；header 含 `> 调研覆盖 N 个来源…`；正文首行 `# <主题>`。
> - **执行前先记录基线**：测前列一次 `DeepResearch/` 目录（按 mtime），每个 TC 跑完后**找新出现的 `.md`** 即本轮落盘报告，避免认错旧报告。
>
> ### 执行方式 / 真测纪律（HARD CONSTRAINT — 见项目 CLAUDE.md「🔒 手工测试纪律」）
> 真人 / windows-mcp 在真实 DeskPet 桌宠 App 上**模拟鼠标点击 + 键盘输入**触发。**禁止**：WebSocket 直连 backend、pytest/脚本回放、`import` 内部模块查 registry/loader 当 UI 证据。
> grep `task_drift_user_request_injected` / `subagent_scheduled` / 核对落盘 `.md` slug+标题 = 对**真模拟人触发后**真实运行栈/真实落盘产物做判定（合规硬证据，非脚本回放）。
>
> ### 证据归档
> 截图存 `G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseA\screenshots\`，命名 `<case-id>-<简述>.png`；log/落盘核对存同父目录 `*.txt`（如 `TC-A1-report-slug.txt`）。
>
> **最后更新**：2026-06-21（v1 初稿，待 Lead 评估迭代）

---

## 1. 测试前置（每次测试前必做）

> 目标：让 Tauri 跑**本仓库 master backend 代码**（含 15817813 T0-1/T0-2 改动），且 `state.db` 的 `session='default'` 最近若干条是**钠离子电池 / CATL（宁德时代）**主题（构成漂移压力），并能找到落盘 `DeepResearch/*.md`。

### 1.1 清理孤儿进程（坑 #1 / #7：防端口双占）

```powershell
taskkill /F /IM deskpet.exe 2>$null
taskkill /F /IM deskpet-backend.exe 2>$null
Get-Process node -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*deskpet*' } | Stop-Process -Force -ErrorAction SilentlyContinue
```

### 1.2 确认漂移压力历史就位（钠离子 / CATL 在 default 会话最近若干轮）

- 桌宠历史存 app 真实 user_data_dir：`%APPDATA%\deskpet\data\state.db`（**不是** `backend/userdata`）。
- 阶段 A 招牌 TC（TC-A1）需要**钠离子电池**主题压住最近 L2；跨域 TC（TC-A2）需要 **CATL/宁德时代**主题。两者可用同一库的不同时段历史，或分别用 §1.7 剧本灌。
- **只读校验**（可选，不改库）：
  ```powershell
  $DB = "$env:APPDATA\deskpet\data\state.db"
  Test-Path $DB    # 期望 True
  # 若装了 sqlite3（只读，不写）：
  # sqlite3 $DB "SELECT content FROM messages WHERE session_id='default' ORDER BY created_at DESC LIMIT 6;"
  # 期望近若干条含 钠离子电池 / 宁德时代 / CATL / 动力电池 等主题
  ```
- ❌ 若最近 L2 不含钠离子/CATL → 走 §1.7 真模拟人灌历史剧本后再测。
- 证据（可选）：只读输出存 `env-01-history-baseline.txt`。

### 1.3 确认 keychain 已有凭据（免登录，真 LLM 链路）

- 之前登录过 → Windows DPAPI keychain 已存 `tsk_xxx`+`key_xxx`，本轮应直接进主界面、不弹登录窗。
- 若弹登录窗：从 `G:\projects\deskpet\LOCAL-DEV-CREDENTIALS.md`（gitignored）读账号密码模拟点击登录 → 等 relay 下发 key → 关 onboarding。
  - ⚠️ 截图前先关 onboarding 窗，**不要截到账号密码**。

### 1.4 用 master backend 启动（坑 #7/#8/#9 — 必须遵守）

- **不要**手动 `python main.py`（占 8100 → Tauri `os error 10048`）。
- **不要**手动 `npm run dev:relay`（与 tauri 自带 vite 抢 strictPort）。
- 只给 **Tauri 进程**注入 env，让它自己 spawn backend：

```powershell
$env:DESKPET_BACKEND_DIR = "G:\projects\deskpet\backend"
$env:DESKPET_PYTHON      = "G:\projects\deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_DEV_MODE    = "1"
cd G:\projects\deskpet\tauri-app
npx tauri dev 2>&1 | Tee-Object -FilePath "G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseA\tauri-dev.log"
```

### 1.5 验证跑的是 master 代码（HARD GATE — 不过这条后面全白测）★

```powershell
Select-String -Path "G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseA\tauri-dev.log" -Pattern "backend_launch"
```

- ✅ 期望：`[backend_launch] Dev python=...\backend\.venv\Scripts\python.exe backend_dir=...\backend`
- ❌ 若 `[backend_launch] Bundled exe=...` → 跑的是旧 frozen exe（**不含 15817813 改动 → 白测**），停止，回 1.4 修 env 重启。
- 证据：截图主界面存 `env-00-boot.png` + grep 输出存 `env-00-backend-launch.txt`。

### 1.6 日志监看入口 + 落盘目录基线（后续所有用例共用）

backend structlog/stdlib log 全走 stderr → Tauri `Stdio::inherit()` → 落进 `tauri-dev.log`。

```powershell
$LOG = "G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseA\tauri-dev.log"
$DR  = "G:\projects\deskpet\DeepResearch"   # dev 落盘目录（兜底 $env:USERPROFILE\DeskPet\DeepResearch）

# 【硬锚点 ①】Fix B user_request 注入（含 tool 名 + 注入字节数）
Select-String -Path $LOG -Pattern "task_drift_user_request_injected"

# 【硬锚点 ②·fanout】每个子代理调度（T0-2）
Select-String -Path $LOG -Pattern "subagent_scheduled"

# 【层 1 观测·非判据】LLM 出站工具调用原始 args（含它自选 topic，本阶段只看漂没漂）
Select-String -Path $LOG -Pattern "p5s2_tool_call_args_dump"

# deepresearch 调用 / plan / 阶段
Select-String -Path $LOG -Pattern "deepresearch|user_request|DeepResearch"

# 落盘报告基线（测前先列一次，记下已有 .md，便于事后找「新出现」的报告）
Get-ChildItem $DR -Filter *.md | Sort-Object LastWriteTime | Select-Object Name, LastWriteTime
```

> ✅ **判定优先用**：① 落盘 `.md` 的文件名 slug + 一级标题 + 正文主题（阶段 A 核心判据）；② `task_drift_user_request_injected` 的 `req_len`（注入了原话）。
> ⚠️ `p5s2_tool_call_args_dump` 的 `topic` **本阶段不作 PASS/FAIL 判据**（它是层 1，T1-1 才治）；只用于在报告栏标注「层 1 是否仍漂」。

### 1.7 （备用）真模拟人灌漂移压力历史剧本

按 §2 真模拟人连发 6~8 条同领域深问把最近若干轮灌满：
- **钠离子档**（给 TC-A1）：`钠离子电池2026年产业化进展` → `它的能量密度瓶颈` → `主要厂商有哪些` → `和锂电成本对比` → `钠电正极材料路线` → `钠电储能应用前景`。
- **CATL 档**（给 TC-A2/A4）：`宁德时代2024年营收多少` → `它的毛利率呢` → `动力电池出货量` → `海外产能布局` → `麒麟电池技术路线` → `和比亚迪刀片电池对比`。
每条等桌宠答完，之后最近若干条 L2 即被灌成该领域。

---

## 2. 对话操作通用步骤（每个用例复用 · windows-mcp 真模拟）

每个「**发起对话**」均指以下动作序列（涉 UI 输入的 TC 必须真点真输）：

1. **截图**抓当前桌宠状态（基线）。
2. **定位输入框**：Snapshot/Screenshot 找桌宠聊天输入框坐标 `(x_in, y_in)`。
3. **declare**：`坐标=(x_in,y_in) | 动作=click 聚焦输入框 | 期望=光标进入输入框`。
4. **真点击聚焦**：`SetCursorPos(x_in,y_in)` + SendInput LEFTDOWN/UP（WebView2 不吃老式 mouse_event，用 SendInput）。
5. **中文输入用剪贴板**：STA Runspace `[Clipboard]::SetText("<prompt>")` → `Ctrl+V`（SendKeys 不支持中文 IME）。
6. **回车发送**：SendKeys `{ENTER}`（或点发送按钮）。
7. **等待**：deepresearch 重型工具，standard 档 30–120s，deep 档可达 300s（用 `WaitFor` 轮询 ArtifactCard / 报告卡片出现）。
8. **截图**抓最终结果 + **核对 `DeepResearch/` 新落盘 `.md`**。

> Click 失败 workaround（按优先级 retry ≥3 次）：`SetCursorPos + SendInput` → `Click(label=…)` 用 Snapshot → `App switch` 聚焦后再 click。
> 中文输入失败 workaround：STA Runspace `Clipboard.SetText("中文")` + Ctrl+V；焦点不在目标窗口 → 先 click 输入框聚焦再粘贴；用 backend log 确认消息真到了。

---

## 3. 测试用例

> **判定通用约定（阶段 A）**：每个涉 deepresearch 的 TC，跑完后做三件事：
> 1. `Get-ChildItem $DR -Filter *.md | Sort LastWriteTime` 找**本轮新出现**的 `.md` → 记其**文件名 slug**。
> 2. 打开该 `.md`，看**一级标题 `# ...`** 与**正文/sub_questions 主题**。
> 3. grep `task_drift_user_request_injected tool=deepresearch` 的 `req_len`。
> **PASS = 落盘报告主题（slug+标题+正文）= 用户原话主题**；p5s2 的 topic 漂不漂**不影响本阶段判定**（仅记录）。

---

### 维度 1 — 相邻领域原话夺权（★ 招牌 · 一票否决级）

#### TC-A1 — 钠离子历史压力下发「固态电池」deepresearch → 落盘报告 = 固态电池

- **类型**：UI 真测（真模拟人，**必须** windows-mcp 真点真输）+ 落盘报告核对 — **★ 阶段 A 头号招牌**
- **目的**：最近 L2 全是**钠离子电池**（相邻领域，distractor interference 死穴）时，发与之相邻但不同的**固态电池**调研。T0-1 的纵深价值：**即使外层 LLM 选的 `topic` 漂回钠离子（层 1 未治），deepresearch 内部以 `user_request`（原话=固态电池）为准** → 落盘报告文件名 slug / 一级标题 / 正文主题 = **固态电池**。
- **前置**：§1 全就绪；§1.5 跑 master；§1.2/§1.7 确认最近 L2 是**钠离子电池**主题；记录 `DeepResearch/` 落盘基线。
- **精确步骤**：

| 步骤 | 动作（declare：坐标 / 动作 / 期望） | 期望结果 |
|---|---|---|
| 1 | Screenshot 抓桌宠主界面 + 输入框，记坐标 `(x,y)` | `screenshots/TC-A1-step1.png`；输入框可见 |
| 2 | `坐标=(x,y) \| 动作=click 聚焦输入框 \| 期望=光标进入` | 输入框聚焦 |
| 3 | `坐标=(x,y) \| 动作=Clipboard "帮我深度调研 固态电池 2027 年的技术路线与量产前景" → Ctrl+V → Enter \| 期望=触发 deepresearch` | 桌宠开始跑深度研究 |
| 4 | `WaitFor` 报告卡片出现（最长 300s），截图最终报告 | `screenshots/TC-A1-report.png` |
| 5 | `Get-ChildItem $DR -Filter *.md \| Sort LastWriteTime`，找本轮新 `.md`，记 slug | 新报告文件名 slug **含「固态电池/固态/solid-state」语义**，**不含钠/钠离子** |
| 6 | 打开该 `.md`，看一级标题 `# ...` + 前几段正文 + sub_questions | 标题与正文主题 = **固态电池**（电解质/界面/量产路线…），**不是**钠离子 |
| 7 | grep `task_drift_user_request_injected tool=deepresearch` | 命中 `req_len>0`（注入了原话「固态电池…」） |
| 8 | （仅记录·非判据）grep 本轮 `p5s2_tool_call_args_dump` 的 deepresearch 行 | 记录 LLM 自选 `topic`（**可能仍写钠离子 = 层 1 漂，属预期，不判 FAIL**） |

- **✅ 修复后（PASS 画面）** vs **❌ 漂移（FAIL 画面）**：
  - ✅ 落盘 `.md` slug = `固态电池…`、`# 固态电池…` 标题、正文讲固态电解质/界面阻抗/量产；`req_len>0`。即使 p5s2 topic=钠离子，**报告被原话夺回**。
  - ❌ 落盘 `.md` slug/标题/正文是**钠离子电池**（旧主题污染穿透到报告）→ T0-1 失效。
- **PASS 判据**：落盘报告 slug + 一级标题 + 正文主题三者**全为固态电池** + `req_len>0`。
- **FAIL 判据**：落盘报告 slug 或标题或正文任一为钠离子/钠电主题。
- **RETRY 纪律**：漂移为概率性 → **连续重复 ≥2 次**（每次先按 §1.7 重新把钠离子压回最近 L2）取稳定结论；2 次里有 1 次报告漂钠离子 → 记 FAIL 并补跑确认。
- **诚实标注**：本 TC 的纵深价值在「p5s2 topic 真漂、但报告被拉回」时最强；若 p5s2 topic 本就没漂（钠离子压力不足 / 层 1 没被诱发）→ 报告本就正确，仍记 PASS，但在报告栏标注「层 1 未漂，纵深条件未触发（注入侧由 step 7 `req_len` 证明）」。
- **证据**：`TC-A1-report.png` + `TC-A1-report-slug.txt`（`.md` 文件名 + 一级标题 + 正文前两段截取）+ `TC-A1-inject.txt`（`task_drift_user_request_injected` 命中行）+ `TC-A1-p5s2.txt`（层 1 topic 记录）。

---

### 维度 2 — 跨域原话夺权

#### TC-A2 — CATL 历史压力下发「Rust Tokio」deepresearch → 落盘报告 = Rust

- **类型**：UI 真测（真模拟人）+ 落盘报告核对
- **目的**：跨域（电池 ↔ 编程，token 重叠≈0）下，最近 L2 全 **CATL/宁德时代**时发 **Rust 异步运行时 Tokio** 调研 → 落盘报告主题 = **Rust**。跨域是相对易抓档，与 TC-A1 相邻领域档形成「易/难」对照，证明夺权不止对跨域有效。
- **前置**：§1 就绪；§1.2/§1.7 确认最近 L2 是 **CATL** 主题；记录落盘基线。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | Screenshot 记输入框坐标 `(x,y)` | `screenshots/TC-A2-step1.png` |
| 2 | `坐标=(x,y) \| 动作=Clipboard "帮我深度调研 Rust 异步运行时 Tokio 的架构与竞品对比" → Ctrl+V → Enter \| 期望=触发 deepresearch` | 桌宠跑深度研究 |
| 3 | `WaitFor` 报告卡片（最长 300s），截图 | `screenshots/TC-A2-report.png` |
| 4 | 找本轮新 `.md`，记 slug | slug 含 Rust/Tokio，**不含**宁德/CATL/电池 |
| 5 | 打开 `.md` 看标题 + 正文 + sub_questions | 标题/正文 = Rust 异步运行时（Tokio/async/调度），非电池 |
| 6 | grep `task_drift_user_request_injected tool=deepresearch` | `req_len>0` |
| 7 | （非判据）grep `p5s2_tool_call_args_dump` 记录层 1 topic | 记录（可能漂 CATL，属预期） |

- **✅ vs ❌**：✅ 报告 slug/标题/正文全 Rust；❌ 报告主题为宁德/CATL/电池。
- **PASS 判据**：落盘报告 slug + 标题 + 正文全为 Rust 系 + `req_len>0`。
- **FAIL 判据**：报告任一维度为 CATL/电池主题。
- **RETRY 纪律**：连续 ≥2 次（每次重灌 CATL 压力）。
- **证据**：`TC-A2-report.png` + `TC-A2-report-slug.txt` + `TC-A2-inject.txt` + `TC-A2-p5s2.txt`。

---

### 维度 3 — fanout 漂修（T0-2 子代理隔离 + 子问题对齐原话）

#### TC-A3 — 触发 fanout（多子问题）→ 6 子代理研究的是「你发主题」的子问题

- **类型**：UI 真测（真模拟人）+ 多 log 锚点 + 落盘核对
- **目的**：发一个会**展开多子问题**的请求（足以触发 fanout，`scheduler is not None ∧ depth==0 ∧ len(sub_questions)≥阈值 ∧ fanout 开关 on`）。验证：①`subagent_scheduled` 多条子代理被调度；②每个子代理研究的是**当前原话主题**派生的子问题（不是旧 CATL/钠离子主题）；③汇总报告主题对齐原话。fanout 隔离本身（子代理不继承主历史）由架构保证（`scheduler` 不读 parent 历史），T0-2 验的是「sub_questions 由 `request_topic` 派生 → 子代理不漂」。
- **前置**：§1 就绪；最近 L2 是 CATL（漂移压力）；记录落盘基线。确认 fanout 默认开（若被关，需确认开关，记录环境）。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | Screenshot 记坐标 | `screenshots/TC-A3-step1.png` |
| 2 | `坐标=(x,y) \| 动作=Clipboard "帮我深度调研 Rust 异步运行时 Tokio：架构、调度模型、生态、竞品、性能、生产实践六个方面全面对比" → Ctrl+V → Enter \| 期望=触发 deepresearch fanout` | 桌宠跑深度研究（多子问题 → fanout） |
| 3 | `WaitFor` 报告（fanout 较慢，最长 300s），截图 | `screenshots/TC-A3-report.png` |
| 4 | grep `subagent_scheduled` 本轮命中行 | 命中**多条**（期望 ≈ 子问题数，目标 6 条 `kind=research`），`run_id=<sid>.par-<task_id>` |
| 5 | grep 各子代理对应的 deepresearch plan/搜索阶段 / 落盘子报告主题 | 每个子研究主题是 **Rust 各子方面**（架构/调度/生态/竞品/性能/实践），**无** CATL/电池子问题 |
| 6 | 找本轮新 `.md`（汇总报告），看标题 + 6 段子研究主题 | 标题=Rust；6 份子研究主题全对齐 Rust 子方面，无旧主题 |
| 7 | grep `task_drift_user_request_injected tool=deepresearch` | `req_len>0` |

- **✅ vs ❌**：✅ `subagent_scheduled` 多条 + 子研究主题全 Rust 各子方面 + 汇总报告 6 段全 Rust；❌ 任一子研究主题为 CATL/电池，或汇总报告含旧主题子节。
- **PASS 判据**：`subagent_scheduled` 命中 ≥2 条（理想 6）+ 全部子研究主题对齐原话 Rust 子方面 + 汇总报告主题=Rust。
- **FAIL 判据**：任一子代理研究 CATL/钠离子主题，或汇总报告含旧主题子节。
- **固有限制 / 环境受限标注**：① 若请求未触发 fanout（sub_questions 数未达阈值 / fanout 开关关）→ `subagent_scheduled` 零命中 → 本 TC 退化为单 run，标注「fanout 未触发，需调高子问题数或确认开关」+ 等用户确认（不强行改产品代码）；可改发更明显的「N 个方面」措辞重试 ≥3 次。② 子研究是否落独立 `.md` 取决于实现，若仅汇总落盘 → 以汇总报告 6 段子节主题 + `subagent_scheduled` 条数为准。
- **证据**：`TC-A3-report.png` + `TC-A3-subagent-scheduled.txt`（多条命中行 + run_id）+ `TC-A3-report-slug.txt`（汇总报告标题 + 6 段子主题截取）+ `TC-A3-inject.txt`。

---

### 维度 4 — BC 正常路径（干净会话不被破坏）

#### TC-A4 — 干净会话（无相邻领域污染）发 deepresearch → 报告主题正常（user_request==topic 不破坏）

- **类型**：UI 真测（真模拟人）+ 落盘核对 — BC 回归
- **目的**：当 `user_request` 与 LLM topic 本就一致（无漂移压力的干净会话，或 `user_request=None` 路径退化为 `request_topic=topic`）时，T0-1 **不能引入回归**——报告主题正常、不报错、内容质量不降。验证「夺权」逻辑对正常路径零副作用。
- **前置**：**干净/单一主题会话**——最理想是无 CATL/钠离子污染的新话题状态（若不能清库，用一个与待测主题同域、不会诱发漂移的历史；记录实际状态）。记录落盘基线。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | Screenshot 记坐标 | `screenshots/TC-A4-step1.png` |
| 2 | `坐标=(x,y) \| 动作=Clipboard "帮我深度调研 PostgreSQL 与 MySQL 在 OLTP 场景的性能与生态对比" → Ctrl+V → Enter \| 期望=触发 deepresearch` | 桌宠正常跑深度研究 |
| 3 | `WaitFor` 报告（最长 300s），截图 | `screenshots/TC-A4-report.png` |
| 4 | 找本轮新 `.md`，记 slug + 标题 + 正文 | slug/标题/正文 = 数据库对比主题（PostgreSQL/MySQL/OLTP），内容完整 |
| 5 | grep `task_drift_user_request_injected tool=deepresearch` | `req_len>0`（与原话一致） |
| 6 | grep 本轮 backend log 是否有 traceback | 无异常/崩溃 |

- **✅ vs ❌**：✅ 报告主题正常 = 数据库对比、内容完整、无报错；❌ 报告主题被异常改写 / 内容残缺 / backend traceback（夺权逻辑引入回归）。
- **PASS 判据**：报告主题正常 + 内容完整 + 无报错 + `req_len>0`。
- **FAIL 判据**：报告主题异常 / 内容残缺 / traceback。
- **证据**：`TC-A4-report.png` + `TC-A4-report-slug.txt` + `TC-A4-inject.txt`。

---

### 维度 5 — 深度档（deep 模式多轮 / 二轮补搜仍以原话为准）

#### TC-A5 — deep 模式（多轮 + gap 补搜 + rerank + semantic）报告主题仍 = 原话

- **类型**：UI 真测（真模拟人）+ 落盘核对
- **目的**：deep 档会走二轮 gap followup（`:1687`）、semantic scorer（`:1721`）、llm rerank（`:1755`）等额外阶段——验证这些阶段**也用 `request_topic`（原话）**，不会在补搜/重排环节把主题漂回旧领域。这是 T0-1 全替换表里「易遗漏」的几处（plan §1 #5/#6/#7/#8/#9）的真机验证。
- **前置**：§1 就绪；CATL 漂移压力在位；记录落盘基线。确认能触发 deep 模式（措辞含「深度/全面/详尽」或 UI 深度档；记录实际 mode）。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | Screenshot 记坐标 | `screenshots/TC-A5-step1.png` |
| 2 | `坐标=(x,y) \| 动作=Clipboard "帮我做一份关于 Rust 异步运行时 Tokio 的深度详尽调研，要多轮补充搜索、覆盖架构调度生态竞品" → Ctrl+V → Enter \| 期望=触发 deep deepresearch` | 桌宠跑 deep 研究（多轮） |
| 3 | `WaitFor` 报告（deep 最慢，最长 300s），截图 | `screenshots/TC-A5-report.png` |
| 4 | grep 本轮 log 确认走了多轮 / gap 补搜阶段 | 出现二轮/gap/rerank/semantic 相关阶段日志（确认 deep 路径真触发） |
| 5 | 找本轮新 `.md`，记 slug + 标题 + 正文 | 全 Rust 主题，**含二轮补搜得到的内容仍是 Rust**，无 CATL 渗入 |
| 6 | grep `task_drift_user_request_injected tool=deepresearch` | `req_len>0` |

- **✅ vs ❌**：✅ deep 多轮全程报告主题 = Rust，gap 补搜/rerank 阶段未漂；❌ 报告中后段（二轮补搜内容）出现 CATL/电池渗入（说明 gap/semantic/rerank 某处仍用了 `llm_topic`）。
- **PASS 判据**：deep 报告全文主题 = 原话 Rust，无旧主题渗入 + `req_len>0`。
- **FAIL 判据**：报告任一段（尤其二轮补搜部分）漂回旧主题。
- **固有限制**：若无法稳定触发 deep 多轮（standard 即返回）→ 标注「deep 路径未触发，多轮替换点由单测覆盖（test_task_drift_fixb.py 的 gap/semantic/rerank 入参断言）」+ 重试 ≥3 次不同措辞 + 等用户确认。
- **证据**：`TC-A5-report.png` + `TC-A5-deep-stages.txt`（多轮/gap 阶段日志）+ `TC-A5-report-slug.txt` + `TC-A5-inject.txt`。

---

### 维度 6 — 边界（长口语原话的 slug 处理）

#### TC-A6 — 长口语化原话发 deepresearch → 落盘文件名 slug 不报错、可读、对齐主题

- **类型**：UI 真测（真模拟人）+ 落盘核对（边界）
- **目的**：`title_slug(save_topic, max_grapheme=40)`（`office_paths.py:251`）对**很长 / 口语化 / 含标点和语气词**的用户原话做 FS-safe slug。验证：①不报错、不崩；②落盘文件名可读、≤40 grapheme 截断合理；③slug 仍体现原话主题（不退化成 `report` 兜底，除非真异常）。这是 plan §1「slug 用 title_slug，要短只做确定性 collapse-whitespace+truncate，禁止 LLM 二次清洗标题」的边界验证。
- **前置**：§1 就绪；记录落盘基线。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | Screenshot 记坐标 | `screenshots/TC-A6-step1.png` |
| 2 | `坐标=(x,y) \| 动作=Clipboard "诶你帮我好好查一查那个 Rust 语言里头的 Tokio 异步运行时它到底是怎么个调度法、跟别的运行时比起来强在哪儿弱在哪儿呗，越详细越好！！！" → Ctrl+V → Enter \| 期望=触发 deepresearch` | 桌宠跑研究（超长口语原话） |
| 3 | `WaitFor` 报告（最长 300s），截图 | `screenshots/TC-A6-report.png` |
| 4 | 找本轮新 `.md`，记**完整文件名** | 文件名 `<slug>-<ts>.md`：slug ≤40 grapheme、无非法 FS 字符（`\ / : * ? " < > |` 等被清）、可读、含 Rust/Tokio 语义、**非** `report-<ts>.md` 兜底 |
| 5 | 打开 `.md` 看一级标题 | 标题为用户原话（或其确定性 collapse 版），主题 = Rust Tokio |
| 6 | grep backend log 该轮无 slug/落盘异常 | 无 `research report save skipped` traceback / 无 `title_slug` 异常 |

- **✅ vs ❌**：✅ 文件名可读 ≤40 字、对齐 Rust 主题、无报错；❌ 落盘报错 / 文件名乱码 / 退化成 `report-<ts>.md`（slug 抛异常走兜底）/ 含非法 FS 字符导致写盘失败。
- **PASS 判据**：落盘成功 + 文件名 slug 合法可读 ≤40 grapheme + 体现 Rust 主题 + 无异常。
- **FAIL 判据**：落盘失败 / slug 非法或乱码 / 退化兜底 `report` / traceback。
- **诚实标注**：`title_slug` 实现用 char 长度近似 grapheme（`office_paths.py:258` 注释「简化版」），emoji/组合字符可能截半——若原话含 emoji 导致截半但仍能写盘，记 PASS 并标注「emoji 截半为已知简化，未崩即可」。
- **证据**：`TC-A6-report.png` + `TC-A6-slug.txt`（完整文件名 + 一级标题）。

---

## 4. 结果汇总表

| ID | 维度 | 标题 | 一票关键判定（落盘报告主题为准） | 需 windows-mcp 真模拟 | 判定 |
|---|---|---|---|---|---|
| TC-A1 ★ | 1 相邻领域夺权 | 钠离子历史下发固态电池→报告=固态电池 | 落盘 slug+标题+正文=固态电池（即使 p5s2 topic 漂钠离子）+ `req_len>0` | ✅ 是（真点真输） | ⬜ |
| TC-A2 | 2 跨域夺权 | CATL 历史下发 Rust→报告=Rust | 落盘 slug+标题+正文=Rust + `req_len>0` | ✅ 是 | ⬜ |
| TC-A3 | 3 fanout 漂修 | 多子问题触发 fanout→6 子代理研究你发主题 | `subagent_scheduled` ≥2(理想6) + 子研究主题全对齐原话 + 汇总报告=原话 | ✅ 是 | ⬜ |
| TC-A4 | 4 BC 正常 | 干净会话 deepresearch 不破坏 | 报告主题正常+内容完整+无报错+`req_len>0` | ✅ 是 | ⬜ |
| TC-A5 | 5 深度档 | deep 多轮/补搜报告仍=原话 | deep 全文主题=原话、二轮补搜无旧主题渗入 | ✅ 是 | ⬜ |
| TC-A6 | 6 边界·slug | 长口语原话 slug 不报错可读 | 落盘文件名合法可读≤40 grapheme、对齐主题、非兜底 | ✅ 是 | ⬜ |

**覆盖维度清单（6 维度 / 6 TC）**：
1. 相邻领域原话夺权（★ 招牌 distractor 死穴：钠离子→固态电池）
2. 跨域原话夺权（CATL→Rust，易档对照）
3. fanout 漂修（T0-2：子代理调度 + 子问题对齐原话）
4. BC 正常路径（夺权逻辑零回归）
5. 深度档（gap/rerank/semantic 易遗漏替换点真机验证）
6. 边界（长口语原话 slug 鲁棒性）

> **★ 阶段 A 判定铁律重申**：所有 TC 的 PASS/FAIL **以落盘报告主题（slug+标题+正文）为准**，**不以 `p5s2_tool_call_args_dump` 的 topic 参数为准**（那是层 1，T1-1 才治）。报告栏须分别记录「落盘报告主题（判据）」与「p5s2 层 1 topic（仅记录漂没漂）」两项，便于后续 T1-1 阶段对比层 1 是否被治好。

---

## 5. 证据归档说明

- **截图目录**：`G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseA\screenshots\`
- **log/落盘核对目录**：`G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseA\`（含 `tauri-dev.log` + 各 `*.txt`）。
- **命名规范**：`<case-id>-<简述>.png` / `<case-id>-<简述>.txt`。
- **每个用例报告格式**（汇总写到本目录 `RESULTS.md`）：
  ```
  case:        TC-A1
  坐标:        (x_in, y_in) [物理像素]
  动作:        click 输入框 → Clipboard "帮我深度调研 固态电池..." → Ctrl+V → Enter
  截图:        screenshots/TC-A1-report.png
  落盘报告:    DeepResearch/<slug>-<ts>.md（slug=... / 标题=# ... / 正文主题=...）← 判据
  inject证据:  task_drift_user_request_injected tool=deepresearch req_len=<N>
  层1记录:     p5s2 topic=<...>（仅记录漂没漂，非判据）
  判定:        PASS / FAIL / RETRY-N / SKIP（带理由）
  ```
- **失败重试纪律**：任一 case 失败须用 ≥3 种不同 workaround 重试（SetCursorPos+SendInput / Snapshot label click / App switch 聚焦后再 click；中文输入失败换剪贴板 STA Runspace）后才能标「环境受限」，SKIP 须等用户确认。
- ⚠️ **截图脱敏**：截任何含 onboarding 窗的画面前先关窗，不要截到账号密码。
- ⚠️ **概率性漂移**：招牌 TC-A1/A2 必须连续 ≥2 次（每次重灌漂移压力），单次结论无效。
</content>
</invoke>
