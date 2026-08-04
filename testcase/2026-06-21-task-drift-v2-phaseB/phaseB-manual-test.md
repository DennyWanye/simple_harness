# 任务漂移 v2 · 阶段 B（T1-1 会话切分 + T0-4 voice 全链路 + §8 sentinel + group 多窗口 + 前端）手工测试文档（windows-mcp 真模拟人）

> **被测**：任务漂移修复 **v2 阶段 B** — **T1-1 会话作用域切分（根治层 1）** + **T0-4 voice 全链路 effective sid** + **§8 auto-resume sentinel 禁触发 deepresearch** + **多窗口 group 同步** + **前端"新话题"/"回上个话题"按钮 + `session_switched`/`task_session_started` 响应**。
> **commit**：master @ **ca4022a7**（实现时以 `git log` 复核）。
> **对应 plan**：`plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md`（§0 链路澄清 / §4 T0-4 voice / §5 T1-1 会话切分 + group / §8 落地顺序 + sentinel）。
>
> ---
>
> ## ★★ 阶段 B 治「层 1」根因——执行/判定前必须读懂这条，否则会误判 ★★
>
> 漂移精确发生在**两层**（plan §0）：
> - **层 1（上游·主 agent loop）**：主 loop 带着旧 L2 历史（钠离子/CATL）生成 deepresearch 工具调用，**它选的 `topic` 参数本身就漂了**——因为 `MemoryComponent` 把 L2 旧主题提升成真实 history 插在当前 user 前，污染了主 loop 的"选题判断"。**阶段 B 的 T1-1 就是治这一层**：`/new`/新话题按钮 → 后端起一个**新的 `effective_sid`（`task-<base>-<seq>`）** → **新 scope 的 L2 历史为空** → 主 loop 选 deepresearch `topic` 时**不被旧主题污染** → **从源头不漂**。
> - **层 2（下游·deepresearch 内部）**：T0-1（阶段 A）治，已验。
>
> ### 🚨 阶段 B 的判定口径（与阶段 A 互补、但更深一层）
> 阶段 A 验"即使 topic 漂、报告被原话夺回"（纵深兜底）；**阶段 B 验"切场后 topic 从源头就不漂"**（根治）。
> 因此阶段 B 的**核心硬证据**是：
> 1. **`session_switched` / `task_session_started` 事件**（`main.py:5476-5482` 切场瞬间发，payload 含 `old_sid`/`new_sid=task-<base>-<seq>`/`reason`）。
> 2. **新 effective_sid（`task-*`）贯穿后端落库/组装/工具/回复**（不再读旧 default 的 L2）。
> 3. **deepresearch 搜索 query 的主题 = 用户当前原话主题**（本机**网络受限** → 0 引用不落盘报告，故判定锚点用**搜索 query 主题**，不依赖落盘 `.md`）。
> 4. **多窗口同步**：主桌宠窗 + 消息面板窗在 `/new` 后两窗都切到新 scope（group 广播）。
> 5. **sentinel 拒绝**：auto-resume 续跑轮（`<<auto_resume>>`）**不触发 deepresearch**（拒绝 final 文案 / 该轮无 deepresearch 调度）。
>
> ---
>
> ## ★ 环境现实（阶段 A 实测，阶段 B 沿用）：本机网络受限 → 判定锚点用「搜索 query 主题」而非落盘报告 ★
>
> 本机 **google 被墙、baike 返回少** → deepresearch **0 引用时不落盘报告**（`DeepResearch/*.md` 可能根本不生成）。
> 因此阶段 B **判定 deepresearch 主题漂没漂，一律以「LLM 发出的搜索 query 主题」为锚点**，不依赖落盘报告：
> - **后端日志锚点（首选）**：
>   - `p5s2_tool_call_args_dump`（`providers/openai_compatible.py`，LLM 出站工具调用原始 args 前 100 字，**含它自选的 `topic`**——阶段 B 判 T1-1 是否治好层 1 就**直接看这个 topic 漂没漂**，与阶段 A 相反，阶段 B **把它当判据**）。
>   - `subagent_scheduled kind=... run_id=<sid>.par-<task_id>`（fanout 子代理调度，`run_id` 含 effective_sid）。
>   - `task_drift_user_request_injected tool=deepresearch req_len=<N>`（`agent_loop.py:80`，Fix B 注入原话）。
>   - deepresearch 内部搜索/plan 阶段日志里出现的 query 文本（grep `search?q=` / query expansion / sub_questions 主题）。
> - **辅助（可选）抓网络层 query**：若 deepresearch 走 `httpx GET .../search?q=<主题>` 或 `cdp_edge_render url=...`，grep 该 URL 的 `q=` 参数 = LLM 实际搜的主题。
>
> > ★ 阶段 B 招牌纵深：default 会话有钠离子/电池历史压力下，发 **`/new 帮我深度调研 区块链`** → 后端 `session_switched new_sid=task-default-N` → 新 scope L2 为空 → 主 loop 选 deepresearch `topic=区块链`、搜索 query=区块链，**0 电池/钠离子/宁德**。对照（不加 /new 直发区块链，旧 L2 污染）→ topic/搜索**可能漂**钠离子/CATL（若不漂，说明 T0-1 层 2 兜底也在生效，记录即可）。
>
> ---
>
> ### 被测代码（master @ ca4022a7，实现以 grep 复核行号）
>
> **T1-1（会话作用域切分 — 根治层 1）**：
> - `backend/deskpet/session/task_scope.py`：`TaskSessionManager.resolve(base_sid, text, explicit_new, force_l2)` → `TaskScopeDecision(effective_sid, created, reason, stripped_text, force_l2_page_in)`。
>   - `/new ...` 或 payload `{new_session:true}` → `effective_sid = f"task-{base}-{seq}"`（`seq` 单调递增）、`created=True`、`reason="explicit_new"`、strip 掉 `/new ` 前缀。
>   - `/continue ...` 或 payload `{force_l2:true}` → 留在 `base` sid、`reason="continue"`、`force_l2_page_in="always"`、strip `/continue ` 前缀。
>   - 否则 → `effective_sid=base`、`reason="default"`。
>   - group 管理：`register_peer` / `peer_group` / `remap_peer_group` / `peers_for_group`（transport sid `default` + `message-panel-main` 初始同组 `default`）。
> - `backend/main.py`：
>   - `:3103-3132` `_chat_peer_groups` + `_initial_chat_peer_group` + `_register_chat_peer` + `_remap_chat_peer_group`（transport sid 与 chat sid 分离，防两窗口连同一 `task-*` 被踢）。
>   - `:3135-3151` `_resolve_chat_task_scope(base_sid, text, payload)`：`force_l2 = payload.force_l2 or text.startswith("/continue")`；`decision.created` 时 `_remap_chat_peer_group(base_sid, effective_sid)`。
>   - `:5461-5482` chat/chat_v2 入口：`_msg_sid=payload.session_id or session_id` → `_resolve_chat_task_scope(...)` → `_msg_sid=decision.effective_sid`、`text=decision.stripped_text`；`decision.created` 时对 `("session_switched","task_session_started")` 两事件 `ws.send_json` + `_broadcast_default_chat_peers`。
>   - `:3441-3475` `_broadcast_default_chat_peers`：按 `payload.session_id` / `payload.new_sid` 找 `_chat_peer_groups[peer]==payload_sid` 或 `task_session_manager.peer_group(peer)==payload_sid` 的所有 peer 连接广播（取代旧"硬编码 ==default"）。
>   - effective sid 贯穿：`:5500` code-mode `is_enabled(_msg_sid)`、`:5523` `reset_auto_resume_attempts(_msg_sid)`、append/assemble/工具 context/回复 全用 `_msg_sid`。
>
> **T0-4（voice 全链路 — `backend/pipeline/voice_pipeline.py`）**：
> - `:14` `from deskpet.session.task_scope import task_session_manager`。
> - `:158-186` `_broadcast_chat_v2(... session_id: str | None = None)`：`effective_sid = session_id or self.session_id`，payload 用 `effective_sid`（去掉旧"写死 default"）。
> - `:298-339` voice 轮 resolve：`decision = task_session_manager.resolve(...)` → `effective_sid=decision.effective_sid`；`task_session_manager.remap_peer_group(self.session_id, effective_sid)`；user echo `_broadcast_chat_v2(..., session_id=effective_sid)`。
> - `:384-387` assistant final `_broadcast_chat_v2(..., session_id=effective_sid)`。
> - `:516/531/549/651/655/656/747` 五处统一用 `effective_sid`：append（`:531`）、assembler（`:549`）、stage timer（`:651`）、`loop.run(..., session_id=effective_sid, loop_user_request=text)`（`:655-656`）、assistant 落库（`:747`）。
>
> **§8 sentinel（`backend/agent/agent_loop.py`）**：
> - `:611` `AgentLoop.run(..., is_sentinel_run: bool = False)`。
> - `:1894-1908` `if is_sentinel_run and any(tc.name == "deepresearch" ...)` → yield FinalEvent，content="Auto-resume sentinel cannot start deepresearch. Please explicitly send a research request." 并 `return`（**不执行 deepresearch**）。
> - `main.py:6403-6404` chat 路径 `loop_user_request=(None if _is_sentinel else _text)` + `is_sentinel_run=_is_sentinel`；`:5542` `_is_sentinel = text.startswith("<<") and text.endswith(">>")`。
>
> **前端（`tauri-app/src`）**：
> - `App.tsx:116-117` `activeSid` + `activeSidRef`（初始 `DEFAULT_SESSION_ID`）。
> - `App.tsx:774-782` 收 `session_switched`/`task_session_started` → `switchActiveSid(payload.new_sid)` + `setMessages([])`。
> - `App.tsx:1401-1404` 普通发送 `chat_v2 {text, session_id: activeSidRef.current}`。
> - `App.tsx:1408-1424` `handleNewTopic`（"新话题"按钮 `:1960-1982`）→ 发 `chat_v2 {session_id, new_session:true, text}`（**输入框文本随按钮一起带上**）。
> - `App.tsx:1426-1429` `handleSwitchDefault`（"回到默认话题"，`activeSid !== DEFAULT_SESSION_ID` 时显示 `:1985`）。
> - `MessagePanelRoot.tsx:53/119/305-324` `activeSid` + `session_switched`/`task_session_started` 响应 + 顶部标题显示 `消息 · <sid>` + "回到默认话题"。
> - `code-panel/ws.ts:256-257` `session_switched`/`task_session_started` case；`code-panel/InputBar.tsx:199` 新话题发 `{session_id, new_session:true, text}`。
>
> ### 执行方式 / 真测纪律（HARD CONSTRAINT — 见项目 CLAUDE.md「🔒 手工测试纪律」）
> 真人 / windows-mcp 在真实 DeskPet 桌宠 App 上**模拟鼠标点击 + 键盘输入**（含真点"新话题"按钮）触发。**禁止**：WebSocket 直连 backend 注入当 UI 证据、pytest/脚本回放、`import` 内部模块查 `task_session_manager`/registry 当"功能可用"证据。
> grep `session_switched`/`task_session_started`/`subagent_scheduled`/`p5s2_tool_call_args_dump` = 对**真模拟人触发后**真实运行栈的实际事件/出站行为做判定（合规硬证据，非脚本回放）。
>
> ### 证据归档
> 截图存 `G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseB\screenshots\`，命名 `<case-id>-<简述>.png`；log 核对存同父目录 `*.txt`（如 `TC-B1-switch-evt.txt` / `TC-B1-p5s2.txt`）。
>
> **最后更新**：2026-06-22（v1 初稿，待 Lead 评估迭代）

---

## 1. 测试前置（每次测试前必做）

> 目标：让 Tauri 跑**本仓库 master backend 代码**（含 ca4022a7 T1-1/T0-4/sentinel/group 改动），且 `state.db` 的 `session='default'` 最近若干条是**钠离子电池 / CATL（宁德时代）**主题（构成层 1 漂移压力），并能抓到切场事件 + deepresearch 搜索 query 日志。

### 1.1 清理孤儿进程（坑 #1 / #7：防端口双占）

```powershell
taskkill /F /IM deskpet.exe 2>$null
taskkill /F /IM deskpet-backend.exe 2>$null
Get-Process node -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*deskpet*' } | Stop-Process -Force -ErrorAction SilentlyContinue
```

### 1.2 确认层 1 漂移压力历史就位（钠离子 / CATL 在 default 会话最近若干轮）

- 桌宠历史存 app 真实 user_data_dir：`%APPDATA%\deskpet\data\state.db`（**不是** `backend/userdata`）。
- 阶段 B 招牌 TC（TC-B1）需要 **default 会话**最近 L2 是**钠离子电池/CATL**主题——构成"若不切场就会漂"的压力，从而让 `/new` 切场后"不漂"成为有意义的证据。
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
npx tauri dev 2>&1 | Tee-Object -FilePath "G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseB\tauri-dev.log"
```

### 1.5 验证跑的是 master 代码（HARD GATE — 不过这条后面全白测）★

```powershell
Select-String -Path "G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseB\tauri-dev.log" -Pattern "backend_launch"
```

- ✅ 期望：`[backend_launch] Dev python=...\backend\.venv\Scripts\python.exe backend_dir=...\backend`
- ❌ 若 `[backend_launch] Bundled exe=...` → 跑的是旧 frozen exe（**不含 ca4022a7 改动 → 白测**），停止，回 1.4 修 env 重启。
- 证据：截图主界面存 `env-00-boot.png` + grep 输出存 `env-00-backend-launch.txt`。

### 1.6 日志监看入口（后续所有用例共用）

backend structlog/stdlib log 全走 stderr → Tauri `Stdio::inherit()` → 落进 `tauri-dev.log`。

```powershell
$LOG = "G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseB\tauri-dev.log"

# 【硬锚点 ①·切场】会话切分事件（含 old_sid / new_sid=task-* / reason）
Select-String -Path $LOG -Pattern "session_switched|task_session_started"

# 【硬锚点 ②·层1判据】LLM 出站工具调用原始 args（含它自选 topic — 阶段 B 把它当判据看漂没漂）
Select-String -Path $LOG -Pattern "p5s2_tool_call_args_dump"

# 【硬锚点 ③·注入】Fix B user_request 注入
Select-String -Path $LOG -Pattern "task_drift_user_request_injected"

# 【硬锚点 ④·fanout】子代理调度（run_id 含 effective_sid）
Select-String -Path $LOG -Pattern "subagent_scheduled"

# 【硬锚点 ⑤·sentinel】auto-resume 续跑轮
Select-String -Path $LOG -Pattern "auto_resume|is_sentinel|sentinel"

# deepresearch 调用 / 搜索 query / effective sid 透传
Select-String -Path $LOG -Pattern "deepresearch|search\?q=|cdp_edge_render|effective_sid|task-default"
```

> ✅ **阶段 B 判定优先用**：① `session_switched`/`task_session_started` 事件 + `new_sid=task-default-N`；② 新 scope 下 `p5s2_tool_call_args_dump` 的 `topic` + deepresearch 搜索 query 主题（= 用户当前原话，不漂旧主题）；③ 多窗口同步（两窗都收到切场事件 / 都显示新 sid）；④ sentinel 拒绝文案 / 无 deepresearch 调度。
> ⚠️ **网络受限** → 不依赖落盘 `.md`；判 deepresearch 主题靠**搜索 query / p5s2 topic / sub_questions 主题**。

### 1.7 （备用）真模拟人灌漂移压力历史剧本（default 会话）

按 §2 真模拟人连发 6~8 条同领域深问把 default 最近若干轮灌满：
- **钠离子档**（给 TC-B1/B3）：`钠离子电池2026年产业化进展` → `它的能量密度瓶颈` → `主要厂商有哪些` → `和锂电成本对比` → `钠电正极材料路线` → `钠电储能应用前景`。
- **CATL 档**（给 TC-B4/B5/B6 续场 + 对照）：`宁德时代2024年营收多少` → `它的毛利率呢` → `动力电池出货量` → `海外产能布局` → `麒麟电池技术路线` → `和比亚迪刀片电池对比`。
每条等桌宠答完，之后 default 最近若干条 L2 即被灌成该领域。
> ⚠️ 灌历史**必须用普通发送（不带 /new）**，否则会切场到新 scope，压力就灌不进 default。

---

## 2. 对话操作通用步骤（每个用例复用 · windows-mcp 真模拟）

每个「**发起对话**」均指以下动作序列（涉 UI 输入的 TC 必须真点真输）：

1. **截图**抓当前桌宠状态（基线）。
2. **定位输入框/按钮**：Snapshot/Screenshot 找桌宠聊天输入框坐标 `(x_in, y_in)`，必要时找"新话题"按钮坐标 `(x_btn, y_btn)`。
3. **declare**：`坐标=(x,y) | 动作=click/type/... | 期望=...`。
4. **真点击聚焦**：`SetCursorPos(x,y)` + SendInput LEFTDOWN/UP（WebView2 不吃老式 mouse_event，用 SendInput）。
5. **中文输入用剪贴板**：STA Runspace `[Clipboard]::SetText("<prompt>")` → `Ctrl+V`（SendKeys 不支持中文 IME）。
6. **发送**：普通对话 SendKeys `{ENTER}`；"新话题"场景**改点"新话题"按钮**（不是回车）。
7. **等待**：deepresearch 重型工具，standard 档 30–120s，deep 档可达 300s；网络受限下可能更快返回（少引用早停）。用 `WaitFor` 轮询 ArtifactCard / 工作气泡变化。
8. **抓 log**：grep §1.6 各锚点（切场事件 / p5s2 topic / 搜索 query / 子代理 / sentinel）。

> Click 失败 workaround（按优先级 retry ≥3 次）：`SetCursorPos + SendInput` → `Click(label=…)` 用 Snapshot → `App switch` 聚焦后再 click。
> 中文输入失败 workaround：STA Runspace `Clipboard.SetText("中文")` + Ctrl+V；焦点不在目标窗口 → 先 click 输入框聚焦再粘贴；用 backend log 确认消息真到了（不到说明粘贴失败要 retry）。
> **`/new` 前缀的两种触发方式**（TC 会分别覆盖）：(A) 在输入框里**打字带前缀** `/new 帮我深度调研 区块链` 再回车（走 `text.startswith("/new")` strip 路径）；(B) 输入主题文本后**点"新话题"按钮**（走 payload `{new_session:true}` 路径）。两者后端都切场，但入口不同，必须分别真测。

---

## 3. 测试用例

> **判定通用约定（阶段 B）**：每个切场 TC，跑完后做四件事：
> 1. grep `session_switched`/`task_session_started` → 确认本轮发了切场事件、`new_sid=task-default-N`、`reason=explicit_new`。
> 2. grep 本轮 `p5s2_tool_call_args_dump` 的 deepresearch 行 + deepresearch 搜索 query → 确认主题 = **当前原话主题**（不含旧钠离子/CATL）。
> 3. 多窗口 TC 额外确认两窗都切到新 sid（UI 截图 + 两窗各自的事件 log）。
> 4. grep `task_drift_user_request_injected` 的 `req_len`（辅证注入）。
> **PASS = 切场事件正确 + 新 scope deepresearch 主题 = 原话（不漂）+（多窗口 TC）两窗同步**。

---

### 维度 1 — `/new` 切场隔离（★ 层 1 根治 · 一票否决级 · 打字前缀入口）

#### TC-B1 — 钠离子历史压力下发 `/new 帮我深度调研 区块链` → 切新 task-* scope + deepresearch 搜索=区块链不漂

- **类型**：UI 真测（真模拟人，**必须** windows-mcp 真点真输）+ 切场事件 + 搜索 query 核对 — **★ 阶段 B 头号招牌**
- **目的**：default 会话最近 L2 全是**钠离子电池**（层 1 漂移压力）时，在输入框打 `/new 帮我深度调研 区块链` 回车。T1-1 根治价值：后端 resolve 出**新 `effective_sid=task-default-N`** → **新 scope L2 为空** → 主 loop 选 deepresearch `topic` 时**不被钠离子污染** → `p5s2 topic=区块链`、搜索 query=区块链，**0 电池/钠离子/宁德**。这是比 T0-1（层 2 把报告标题夺回）**更根治**的证明——topic 从源头就不漂。
- **前置**：§1 全就绪；§1.5 跑 master；§1.2/§1.7 确认 default 最近 L2 是**钠离子电池**主题。
- **精确步骤**：

| 步骤 | 动作（declare：坐标 / 动作 / 期望） | 期望结果 |
|---|---|---|
| 1 | Screenshot 抓桌宠主界面 + 输入框，记坐标 `(x,y)` | `screenshots/TC-B1-step1.png`；输入框可见 |
| 2 | `坐标=(x,y) \| 动作=click 聚焦输入框 \| 期望=光标进入` | 输入框聚焦 |
| 3 | `坐标=(x,y) \| 动作=Clipboard "/new 帮我深度调研 区块链的共识机制与主流公链对比" → Ctrl+V → Enter \| 期望=切新 scope + 触发 deepresearch` | 桌宠切场 + 开始跑深度研究 |
| 4 | grep 本轮 `session_switched`/`task_session_started` | 命中两事件，`old_sid=default`、`new_sid=task-default-<N>`、`reason=explicit_new` |
| 5 | UI 截图：主桌宠窗顶部/标题显示新 scope（非 default）、消息区清空（`setMessages([])`） | `screenshots/TC-B1-switched.png`；显示新话题 scope |
| 6 | `WaitFor` 报告/工作完成（最长 300s，网络受限可能早停），截图 | `screenshots/TC-B1-result.png` |
| 7 | grep 本轮 `p5s2_tool_call_args_dump` 的 deepresearch 行 | `topic` = **区块链**（不含钠/钠离子/电池）★ 层 1 不漂 = 判据 |
| 8 | grep 本轮 deepresearch 搜索 query（`search?q=` / sub_questions / query expansion） | 搜索主题全 = 区块链（共识/公链/PoW/PoS…），**0 钠离子/电池/宁德** |
| 9 | grep `task_drift_user_request_injected tool=deepresearch` | 命中 `req_len>0`（注入原话"帮我深度调研 区块链…"，已 strip `/new `） |

- **✅ 修复后（PASS 画面）** vs **❌（FAIL 画面）**：
  - ✅ `session_switched new_sid=task-default-N` + 新 scope `p5s2 topic=区块链` + 搜索 query 全区块链 + UI 切到新话题。**层 1 从源头不漂**。
  - ❌ 无切场事件（仍 `_sid=default`）/ `p5s2 topic=钠离子`（旧 L2 污染穿透到主 loop 选题）/ 搜索 query 含钠离子电池 → T1-1 失效。
- **PASS 判据**：切场事件正确（`new_sid=task-default-N`）+ 新 scope deepresearch `topic`/搜索 query 主题 = 区块链 + `req_len>0` + UI 切到新话题。
- **FAIL 判据**：无切场事件 / 仍写 default sid / topic 或搜索 query 漂钠离子电池 / `/new` 前缀未被 strip（注入文本含 `/new`）。
- **RETRY 纪律**：漂移为概率性 → **连续重复 ≥2 次**（每次先按 §1.7 重新把钠离子压回 default L2）取稳定结论。
- **诚实标注**：本 TC 根治价值在「不切场会漂、切场后不漂」对照下最强 → **必须配 TC-B3 对照**（同样区块链不加 /new 看是否漂）。若对照不漂（T0-1 层 2 兜底使然）→ 本 TC 仍以"切场事件 + 新 sid + topic=区块链"判 PASS，但在报告栏标注「层 1 对照未漂，根治价值由切场事件 + 新 sid 隔离证明」。
- **证据**：`TC-B1-switched.png` + `TC-B1-result.png` + `TC-B1-switch-evt.txt`（切场事件命中行）+ `TC-B1-p5s2.txt`（新 scope topic）+ `TC-B1-search-query.txt`（搜索 query 主题）+ `TC-B1-inject.txt`。

---

### 维度 2 — 前端"新话题"按钮（UI 真点击 + payload new_session 路径）

#### TC-B2 — 输入框打主题文本后**点"新话题"按钮** → 发 `{new_session:true}` 切场 + 输入框文本保留带入

- **类型**：UI 真测（真模拟人，**必须**真点"新话题"按钮）+ 切场事件核对
- **目的**：验证另一条切场入口——**前端"新话题"按钮**（`App.tsx:1408 handleNewTopic` / 按钮 `:1960`）。用户在输入框打"帮我深度调研 区块链"但**不打 /new 前缀**，改**点"新话题"按钮** → 前端发 `chat_v2 {session_id, new_session:true, text:"帮我深度调研 区块链"}`（**输入框文本随按钮带上**，不丢）→ 后端走 `explicit_new=True` 路径切场。这是 UI 路径，与 TC-B1 的打字前缀路径互补。
- **前置**：§1 就绪；default 最近 L2 是钠离子（漂移压力）；确认 UI 上"新话题"按钮可见可点。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | Screenshot 抓主界面，记输入框 `(x,y)` + "新话题"按钮坐标 `(x_btn,y_btn)` | `screenshots/TC-B2-step1.png`；按钮可见 |
| 2 | `坐标=(x,y) \| 动作=click 输入框 → Clipboard "帮我深度调研 区块链的共识机制" → Ctrl+V \| 期望=输入框含该文本（不回车）` | 输入框显示主题文本 |
| 3 | `坐标=(x_btn,y_btn) \| 动作=click "新话题"按钮 \| 期望=发 {new_session:true,text} 切场` | 桌宠切场 + 用该文本跑 deepresearch |
| 4 | grep 本轮 `session_switched`/`task_session_started` | 命中，`new_sid=task-default-<N>`、`reason=explicit_new` |
| 5 | UI 截图：主窗切到新 scope、输入框清空（文本已随按钮发出）、消息区清空 | `screenshots/TC-B2-switched.png` |
| 6 | `WaitFor` 完成，grep `p5s2_tool_call_args_dump` + 搜索 query | `topic`/搜索主题 = 区块链，不漂钠离子 |
| 7 | grep `task_drift_user_request_injected` | `req_len>0`，注入文本 = "帮我深度调研 区块链的共识机制"（无 /new 前缀本就没有，验文本完整带入） |

- **✅ vs ❌**：✅ 点按钮即切场 + 输入框文本完整带入 deepresearch + 主题=区块链；❌ 点按钮不切场 / 输入框文本丢失（发了空 text）/ topic 漂钠离子。
- **PASS 判据**：按钮触发切场事件 + `new_sid=task-*` + 输入框文本完整带入（`req_len>0` 且文本=区块链主题）+ topic/搜索=区块链。
- **FAIL 判据**：点按钮无切场 / text 丢失 / 主题漂。
- **RETRY 纪律**：连续 ≥2 次（重灌钠离子压力）。
- **固有限制标注**：若"新话题"按钮在当前 UI 布局不可见（窗口太小/被遮）→ 先调窗口或滚动找到按钮，retry ≥3 次定位；仍找不到才标"UI 受限"+ 等用户确认（不改产品代码）。
- **证据**：`TC-B2-step1.png` + `TC-B2-switched.png` + `TC-B2-switch-evt.txt` + `TC-B2-p5s2.txt` + `TC-B2-inject.txt`。

---

### 维度 3 — 不切场对照（同请求不加 /new → 旧 L2 仍在，可证伪 T1-1 有效性）

#### TC-B3 — 钠离子历史下**不加 /new** 直发"帮我深度调研 区块链" → 仍 default sid，记录是否漂（对照基线）

- **类型**：UI 真测（真模拟人）+ 对照基线 — **TC-B1 的可证伪对照**
- **目的**：完全相同的区块链请求，但**不加 /new、不点新话题按钮** → 后端 `reason=default`、**仍写 default sid**、**旧钠离子 L2 仍在**。记录此时 deepresearch `topic`/搜索是否漂钠离子。这是 TC-B1 的对照：若此处漂、TC-B1 不漂 → **T1-1 切场隔离价值被坐实**（同一压力，唯一差异是切没切场）。若此处也不漂 → 说明 T0-1 层 2 兜底在生效，记录即可（不算 TC-B1 FAIL，但削弱"对照证明"，需在报告标注）。
- **前置**：§1 就绪；**与 TC-B1 同一钠离子压力状态**（紧接 TC-B1 之后或同样灌满钠离子）；确认仍在 default scope（若上一 TC 切了场，先按"回到默认话题"或重启回 default）。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认当前 scope = default（UI 标题/无 task-* 标记），Screenshot | `screenshots/TC-B3-step1.png`；scope=default |
| 2 | `坐标=(x,y) \| 动作=Clipboard "帮我深度调研 区块链的共识机制与主流公链对比"（不加 /new）→ Ctrl+V → Enter \| 期望=不切场、仍 default` | 桌宠跑研究、**无**切场事件 |
| 3 | grep 本轮 `session_switched`/`task_session_started` | **零命中**（`reason=default`，未切场）|
| 4 | grep 本轮 `p5s2_tool_call_args_dump` + 搜索 query | 记录 `topic`/搜索主题（**可能漂钠离子** = 旧 L2 污染未隔离，属对照预期）|
| 5 | 与 TC-B1 对比：同压力下「切场（B1）vs 不切场（B3）」topic/搜索差异 | B1=区块链不漂；B3=漂或不漂（记录）|

- **✅ vs ❌（本 TC 不判产品 FAIL，是对照记录）**：
  - 对照成立（最有力）：B3 漂钠离子 + B1 不漂 → T1-1 切场隔离的根治价值被坐实。
  - 对照弱化：B3 也不漂 → T0-1 层 2 兜底生效，记录"对照未触发漂移"，T1-1 价值由 B1 的切场事件 + 新 sid 隔离独立证明。
- **判定**：本 TC 标 **REF（对照基线，非 PASS/FAIL）**；核心产出是「不切场仍 default sid + topic/搜索记录」，喂给 TC-B1 的对照分析。
- **RETRY 纪律**：与 TC-B1 配对连续 ≥2 次同压力跑，取稳定对照结论。
- **证据**：`TC-B3-step1.png` + `TC-B3-no-switch.txt`（确认零切场事件）+ `TC-B3-p5s2.txt`（对照 topic/搜索记录）。

---

### 维度 4 — `/continue` 续场（留当前 sid + force_l2 保留上下文）

#### TC-B4 — 先切场建 Rust scope 并问一轮，再发 `/continue 它的竞品呢` → 留当前 sid、reason=continue、L2 保留续上下文

- **类型**：UI 真测（真模拟人）+ 续场事件 + 上下文连续性核对
- **目的**：验证 `/continue` 路径（`task_scope.py:62-70` `reason="continue"` + `force_l2_page_in="always"`；`main.py:3142` `force_l2 = ... or text.startswith("/continue")`）。流程：先 `/new` 建一个 Rust scope 并问"Tokio 架构" → 再发 `/continue 它的竞品呢"（含代词"它"，依赖上文）。验证：①`/continue` **不切新 sid**（留当前 task-* scope，`created=False`、`reason=continue`）；②`force_l2` 触发 → L2 page-in 保留 → 桌宠答出 Rust/Tokio 的竞品（async-std/smol…），**没丢上下文**；③`/continue ` 前缀被 strip（注入文本不含 `/continue`）。
- **前置**：§1 就绪；本 TC **自建** scope（不依赖 default 压力）：先 `/new 帮我深度调研 Rust 异步运行时 Tokio 的架构` 建 scope 并等答完。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | `坐标=(x,y) \| 动作=Clipboard "/new 帮我深度调研 Rust 异步运行时 Tokio 的架构" → Ctrl+V → Enter \| 期望=建新 Rust scope` | `session_switched new_sid=task-default-<N>`；桌宠答 Tokio 架构 |
| 2 | 记下当前 effective sid = `task-default-<N>`，Screenshot | `screenshots/TC-B4-scope.png` |
| 3 | `坐标=(x,y) \| 动作=Clipboard "/continue 它的竞品呢，跟它比起来各有什么优劣" → Ctrl+V → Enter \| 期望=续场、不切 sid、保留上文` | 桌宠在**同一 scope**答竞品（依赖"它"=Tokio）|
| 4 | grep 本轮 `session_switched`/`task_session_started` | **零命中**（`/continue` 不切场，留当前 sid）|
| 5 | grep 本轮 log 确认 sid 仍 = `task-default-<N>`（assemble/append/loop 用同 sid）| 仍当前 task-* sid，未回 default、未新建 |
| 6 | grep `force_l2` / L2 page-in always 相关日志（reason=continue）| 命中 `reason=continue` / force_l2 触发（L2 保留）|
| 7 | 肉眼判桌宠答案：是否正确续上"Tokio 的竞品"（async-std/smol/Glommio…）| 答案对齐 Tokio 竞品，**没把"它"理解错/丢上下文** |

- **✅ vs ❌**：✅ `/continue` 留当前 sid + reason=continue + 答案正确续上 Tokio 竞品；❌ `/continue` 误切新 sid（丢上下文，"它"无指代）/ 回 default sid / 答案与 Tokio 无关（L2 没 page-in）。
- **PASS 判据**：无切场事件 + sid 仍 = 当前 task-* + reason=continue/force_l2 触发 + 答案正确续上文（代词"它"解析为 Tokio）+ `/continue ` 前缀被 strip。
- **FAIL 判据**：误切场 / 回 default / 丢上下文 / 前缀未 strip。
- **固有限制**：续场上下文连续性靠肉眼判答案 → 若网络受限导致答案稀薄，至少验"sid 不变 + reason=continue + 答案提及 Tokio/竞品语义"；纯连续性质量由单测（`/continue` force always page-in + reasoning_content 场景）兜底，主测 sid 行为。
- **证据**：`TC-B4-scope.png` + `TC-B4-continue.png`（续场答案）+ `TC-B4-no-switch.txt`（确认零切场 + sid 不变）+ `TC-B4-continue-reason.txt`（reason=continue/force_l2）。

---

### 维度 5 — 多窗口 group 同步（主桌宠窗 + 消息面板窗）

#### TC-B5 — 主桌宠窗 `/new` 切场 → 消息面板窗（message-panel-main）同步切到新 scope（group 广播）

- **类型**：UI 真测（真模拟人，**两窗都要看**）+ group 广播事件核对
- **目的**：验证 T1-1 的 group 同步（`main.py:3441 _broadcast_default_chat_peers` + `_chat_peer_groups` + `task_session_manager.peer_group/remap_peer_group`）。主桌宠窗（transport sid `default`）和消息面板窗（transport sid `message-panel-main`）初始同组 `default`。在主桌宠窗 `/new` 切场 → 后端 `_remap_chat_peer_group` 把同组两 peer 一起映射到新 `effective_sid` → 切场事件**广播给消息面板窗** → **两窗都切到新 scope**。验证多窗口不脱节（否则"主窗写新 scope、面板还停 default"）。
- **前置**：§1 就绪；**确认消息面板窗已打开**（左侧消息面板 message-panel-main 连上控制通道）；default 最近 L2 是钠离子（可选，用于让切场后 deepresearch 主题对照）。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | Screenshot 同时抓**主桌宠窗 + 消息面板窗**，确认两窗都显示 default scope | `screenshots/TC-B5-both-before.png`；两窗 scope=default |
| 2 | 主窗：`坐标=(x,y) \| 动作=Clipboard "/new 帮我深度调研 区块链" → Ctrl+V → Enter \| 期望=主窗切场 + 广播面板窗` | 主窗切新 scope |
| 3 | grep 本轮 `session_switched`/`task_session_started` | 命中；并核对 `_broadcast_default_chat_peers` 把事件发给了 `message-panel-main` peer（grep group 广播日志，无 `default_chat_peer_broadcast_failed`）|
| 4 | Screenshot 抓**消息面板窗**：顶部标题是否变成 `消息 · task-default-<N>`（`MessagePanelRoot.tsx:324`）| `screenshots/TC-B5-panel-after.png`；面板窗显示新 sid |
| 5 | Screenshot 抓**主桌宠窗**：是否也切到新 scope | `screenshots/TC-B5-main-after.png`；主窗新 scope |
| 6 | 两窗 sid 一致性肉眼核对 | 主窗 activeSid == 面板窗 activeSid == `task-default-<N>` |
| 7 | （可选）面板窗顶部"回到默认话题"按钮（`MessagePanelRoot.tsx:305`）此时应可见（activeSid≠DEFAULT_SID）| 面板窗显示"回到默认话题" |

- **✅ vs ❌**：✅ 主窗 `/new` 后**两窗同步切到 task-default-N**、面板标题变更、group 广播无失败；❌ 仅主窗切、面板还停 default（group 广播没生效 / `_chat_peer_groups` 没 remap）/ 两窗 sid 不一致。
- **PASS 判据**：主窗切场 + 切场事件广播到 message-panel-main + 两窗 UI 都显示同一 `task-default-N` + 面板标题更新。
- **FAIL 判据**：面板窗未同步（仍 default）/ 两窗 sid 分裂 / 广播失败日志。
- **固有限制 / 环境受限标注**：① 若消息面板窗未打开/未连控制通道 → 先打开面板窗（DeskPet 左侧消息面板入口），retry ≥3 次确认 message-panel-main 已连；仍连不上才标"面板窗受限"+ 等用户确认。② 若 DeskPet 当前形态只有单窗（面板内嵌而非独立 transport）→ 记录实际窗口拓扑，按"同一控制通道内两视图同步"验（仍看 activeSid 一致）。
- **证据**：`TC-B5-both-before.png` + `TC-B5-panel-after.png` + `TC-B5-main-after.png` + `TC-B5-group-broadcast.txt`（切场事件 + group 广播命中 message-panel-main，无 failed）。

---

### 维度 6 — sentinel 拒绝（auto-resume 续跑轮不触发 deepresearch）

#### TC-B6 — 诱发 auto-resume 续跑轮（`<<auto_resume>>`）→ sentinel 轮拒绝 deepresearch（不重新引入漂移）

- **类型**：UI 真测（真模拟人诱发）+ sentinel 拒绝 log 核对
- **目的**：验证 §8 sentinel 策略（`agent_loop.py:1894-1908`：`is_sentinel_run and tc.name=="deepresearch"` → yield 拒绝 FinalEvent + return，**不执行 deepresearch**；`main.py:6403-6404` `is_sentinel_run=_is_sentinel`、`loop_user_request=None`）。auto-resume 续跑轮带合成文本 `<<auto_resume>>`（`:5542 _is_sentinel` 判定）。验证：sentinel 轮**即使 LLM 想调 deepresearch 也被拒**，不在续跑里重新引入 deepresearch 漂移（R2-E2）。
- **诱发思路**：auto-resume 由"上一轮任务失败/未完成 → 自动续跑"触发，难直接手点。优先用**真模拟人诱发一个会失败 → 触发 auto-resume 的任务**；若难稳定诱发，按"环境受限"流程标注 + 等用户确认（不伪造、不脚本注入 `<<auto_resume>>` 当 UI 证据）。
- **前置**：§1 就绪；了解本机 auto-resume 触发条件（任务失败 + 在 max_attempts budget 内）。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | Screenshot 抓主界面 | `screenshots/TC-B6-step1.png` |
| 2 | 真模拟人发一个**容易失败 / 会触发 supervisor auto-resume** 的任务（如一个明知会失败的多步工具任务），等其失败 → 系统自动续跑（`<<auto_resume>>` 注入）| log 出现 `auto_resume` 续跑轮（`reset_auto_resume_attempts` / sentinel 轮启动）|
| 3 | grep 本轮 `is_sentinel` / `<<auto_resume>>` 续跑轮日志 | 确认进入 sentinel 轮（`_is_sentinel=True`）|
| 4 | 观察该 sentinel 轮是否尝试调 deepresearch | 若 LLM 想调 → grep 拒绝 FinalEvent 文案 "Auto-resume sentinel cannot start deepresearch. Please explicitly send a research request." |
| 5 | grep 该 sentinel 轮是否有 deepresearch 真执行 / `subagent_scheduled` | **无** deepresearch 执行、**无** fanout 子代理调度（被拒在前）|

- **✅ vs ❌**：✅ sentinel 轮调 deepresearch 被拒（拒绝文案 + 无 deepresearch 执行/无 subagent_scheduled）；❌ sentinel 轮真跑了 deepresearch（重新引入漂移，R2-E2 漏洞复现）。
- **PASS 判据**：sentinel 轮内 deepresearch 被拒（拒绝文案命中 **或** 该轮根本无 deepresearch 调度/执行）；普通轮 deepresearch 正常放行（对照 TC-B1 已验普通轮可跑）。
- **FAIL 判据**：sentinel 轮真执行了 deepresearch / 出现该轮 `subagent_scheduled`。
- **固有限制 / 环境受限标注**：sentinel 轮**是否会尝试 deepresearch** 取决于 LLM 在续跑时的决策——若 LLM 本就不想调 deepresearch，则拒绝分支不被触发（无拒绝文案，但也无 deepresearch 执行 = 仍满足"sentinel 不引入 deepresearch 漂移"目标，记 PASS 并标注"拒绝分支未被诱发，但 sentinel 轮无 deepresearch，目标达成"）。若**无法稳定诱发 auto-resume 续跑轮**（任务不失败/budget 耗尽）→ retry ≥3 次不同失败诱因；仍无法 → 标"auto-resume 难诱发，sentinel 拒绝逻辑由单测覆盖（sentinel 轮拒 deepresearch / 普通轮放行）"+ 等用户确认。**禁止**直接脚本注入 `<<auto_resume>>` 当 UI 证据。
- **证据**：`TC-B6-step1.png` + `TC-B6-sentinel.txt`（auto_resume 续跑轮 + 拒绝文案 / 无 deepresearch 执行的 log）。

---

### 维度 7 — voice 切场 / 续场（语音路径补 Fix B + effective sid 全链路）

#### TC-B7 — 语音说"新话题，帮我深度调研区块链"（或 voice /continue）→ voice 走 effective sid + 不漂

- **类型**：UI 真测（真模拟人语音）+ voice effective sid + 主题核对
- **目的**：验证 T0-4 voice 路径（`voice_pipeline.py:298 task_session_manager.resolve(...)` → `effective_sid`；`:531/549/655/747` 五处统一 effective sid；`:316/384 _broadcast_chat_v2(session_id=effective_sid)`；`:655-656 loop.run(..., loop_user_request=text)` 补 Fix B）。验证语音轮也能 resolve effective sid、走原话注入、主题不漂、广播带 effective sid。
- **前置**：§1 就绪；麦克风可用、语音链路（VAD/ASR）正常；default 最近 L2 是钠离子（漂移压力，可选）。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | Screenshot 抓主界面，确认语音可用（麦克风/VAD 状态）| `screenshots/TC-B7-step1.png` |
| 2 | `动作=语音说"新话题，帮我深度调研区块链的共识机制" \| 期望=voice 轮 resolve effective sid + 切场（若措辞触发 explicit_new）或至少 effective sid 全链路一致` | 桌宠语音应答 + 跑 deepresearch |
| 3 | grep 本轮 voice 路径 effective sid 透传 | append/assemble/loop/assistant 五处用同一 effective sid（`:531/549/655/747`）|
| 4 | grep `task_drift_user_request_injected tool=deepresearch` | `req_len>0`（voice 补了 Fix B，注入语音原话）|
| 5 | grep `p5s2_tool_call_args_dump` + 搜索 query | `topic`/搜索主题 = 区块链，不漂钠离子 |
| 6 | grep voice `_broadcast_chat_v2` 广播 payload session_id | = effective sid（非写死 default）|

- **✅ vs ❌**：✅ voice 轮 effective sid 全链路一致 + `req_len>0` + 主题=区块链 + 广播带 effective sid；❌ voice 轮 loop_user_request 没传（`req_len` 无命中）/ 主题漂钠离子 / 广播写死 default / 五处 sid 不一致。
- **PASS 判据**：voice 五处 sid 一致 + `req_len>0`（Fix B 注入语音原话）+ deepresearch 主题=区块链 + 广播 session_id=effective sid。
- **FAIL 判据**：voice `req_len` 无命中（Fix B 没补）/ 主题漂 / sid 分裂 / 广播写死 default。
- **固有限制 / 环境受限标注**：① 语音"新话题"是否触发 explicit_new 取决于 voice 路径是否解析 `/new` 语义——若 voice 不走 `/new` 前缀切场（仅普通语音轮），则本 TC 退化为"验 voice effective sid 全链路 + Fix B 注入 + 主题不漂"（仍是 T0-4 核心），不强求切新 sid，记录实际行为。② 若麦克风/ASR 不可用 → retry ≥3 次（换设备/重启语音链路）；仍不可用标"语音环境受限"+ 等用户确认（voice sid 一致性由单测兜底）。
- **证据**：`TC-B7-step1.png` + `TC-B7-voice-sid.txt`（五处 effective sid 一致）+ `TC-B7-inject.txt`（req_len）+ `TC-B7-p5s2.txt`（主题）。

---

### 维度 8 — BC 零回归（不发 /new、正常对话）

#### TC-B8 — 不发 /new、正常对话 → effective=default、多窗口共享、渲染发送不变

- **类型**：UI 真测（真模拟人）+ BC 回归
- **目的**：验证未触发切场的普通对话**零回归**——`reason=default`、`effective_sid=default`、多窗口仍共享 default、消息渲染/发送/广播一切照旧。验证 T1-1/T0-4/group 改动对正常路径无副作用。
- **前置**：§1 就绪；scope=default（无切场残留）。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认 scope=default，Screenshot | `screenshots/TC-B8-step1.png` |
| 2 | `坐标=(x,y) \| 动作=Clipboard "你好，今天天气怎么样" → Ctrl+V → Enter \| 期望=普通对话、不切场` | 桌宠正常应答 |
| 3 | grep 本轮 `session_switched`/`task_session_started` | **零命中**（reason=default）|
| 4 | grep 本轮 sid | 全用 `default`（append/assemble/loop/assistant）|
| 5 | 若开着消息面板窗：核对面板窗也收到该对话（default group 广播照旧）| 面板窗同步显示该对话 |
| 6 | grep 本轮 backend log 有无 traceback | 无异常/崩溃 |
| 7 | 普通发一条 deepresearch（不带 /new，干净话题，如"PostgreSQL 与 MySQL 对比"）| 正常跑、主题正常、sid=default、无回归 |

- **✅ vs ❌**：✅ 普通对话 sid=default、不切场、多窗口共享、渲染发送正常、无报错；❌ 普通对话误切场 / sid 异常 / 多窗口不同步 / traceback（改动引入回归）。
- **PASS 判据**：无切场事件 + sid=default + 多窗口共享照旧 + 渲染发送正常 + 无报错。
- **FAIL 判据**：误切场 / sid 异常 / 多窗口脱节 / traceback。
- **证据**：`TC-B8-step1.png` + `TC-B8-bc.txt`（零切场 + sid=default + 无 traceback）。

---

## 4. 结果汇总表

| ID | 维度 | 标题 | 一票关键判定（切场事件 + 搜索 query 主题为准） | 需 windows-mcp 真模拟 | 判定 |
|---|---|---|---|---|---|
| TC-B1 ★ | 1 /new 切场隔离（层1根治） | 钠离子历史下 `/new 区块链`→切 task-* + 搜索=区块链不漂 | `session_switched new_sid=task-default-N` + 新 scope topic/搜索=区块链 + `req_len>0` | ✅ 是（真点真输 /new 前缀） | ⬜ |
| TC-B2 | 2 前端"新话题"按钮 | 打文本后点按钮→`{new_session:true}`切场 + 文本带入 | 按钮触发切场 + 输入框文本完整带入 + topic=区块链 | ✅ 是（真点按钮） | ⬜ |
| TC-B3 | 3 不切场对照 | 不加 /new 直发区块链→仍 default、记录是否漂 | 零切场事件 + sid=default + topic/搜索记录（喂 B1 对照） | ✅ 是 | REF |
| TC-B4 | 4 /continue 续场 | 建 Rust scope 后 `/continue 它的竞品`→留 sid + force_l2 | 零切场 + sid 不变 + reason=continue + 答案续上 Tokio 竞品 | ✅ 是 | ⬜ |
| TC-B5 | 5 多窗口 group 同步 | 主窗 /new→消息面板窗同步切新 scope | 切场广播到 message-panel-main + 两窗 sid 一致 | ✅ 是（两窗都看） | ⬜ |
| TC-B6 | 6 sentinel 拒绝 | auto-resume 续跑轮不触发 deepresearch | sentinel 轮 deepresearch 被拒（拒绝文案 / 无调度） | ✅ 是（诱发 auto-resume） | ⬜ |
| TC-B7 | 7 voice 切场/续场 | 语音"新话题…"→voice effective sid 全链路 + 不漂 | voice 五处 sid 一致 + `req_len>0` + 主题=区块链 | ✅ 是（真语音） | ⬜ |
| TC-B8 | 8 BC 零回归 | 不发 /new 正常对话→default 共享渲染不变 | 零切场 + sid=default + 多窗口共享 + 无报错 | ✅ 是 | ⬜ |

**覆盖维度清单（8 维度 / 8 TC）**：
1. /new 切场隔离（★ 层 1 根治：钠离子→/new 区块链，打字前缀入口）
2. 前端"新话题"按钮（payload new_session 入口 + 输入框文本带入）
3. 不切场对照（TC-B1 的可证伪对照基线）
4. /continue 续场（留当前 sid + force_l2 保留上下文）
5. 多窗口 group 同步（主桌宠窗 + 消息面板窗）
6. sentinel 拒绝（auto-resume 续跑轮不引入 deepresearch 漂移）
7. voice 切场/续场（T0-4 全链路 effective sid + Fix B 注入）
8. BC 零回归（正常对话零副作用）

> **★ 阶段 B 判定铁律重申**：所有切场 TC 的 PASS/FAIL **以「切场事件（`session_switched`/`task_session_started`，`new_sid=task-default-N`）+ 新 scope deepresearch 主题（`p5s2 topic` + 搜索 query，不漂旧主题）」为准**；网络受限下**不依赖落盘 `.md`**，判 deepresearch 主题一律用**搜索 query / p5s2 topic / sub_questions 主题**。与阶段 A 不同：阶段 B **把 `p5s2 topic` 当判据**（验层 1 是否从源头不漂），阶段 A 仅把它当观察。

---

## 5. 证据归档说明

- **截图目录**：`G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseB\screenshots\`
- **log 核对目录**：`G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseB\`（含 `tauri-dev.log` + 各 `*.txt`）。
- **命名规范**：`<case-id>-<简述>.png` / `<case-id>-<简述>.txt`。
- **每个用例报告格式**（汇总写到本目录 `RESULTS.md`）：
  ```
  case:        TC-B1
  坐标:        (x_in, y_in) [物理像素]
  动作:        click 输入框 → Clipboard "/new 帮我深度调研 区块链..." → Ctrl+V → Enter
  截图:        screenshots/TC-B1-switched.png
  切场事件:    session_switched old_sid=default new_sid=task-default-N reason=explicit_new ← 判据①
  层1主题:     p5s2 topic=区块链 / 搜索 query=区块链共识机制（0 钠离子/电池）← 判据②
  inject证据:  task_drift_user_request_injected tool=deepresearch req_len=<N>
  多窗口:      （仅 TC-B5）main+panel 两窗 activeSid=task-default-N 一致
  判定:        PASS / FAIL / RETRY-N / REF / SKIP（带理由）
  ```
- **失败重试纪律**：任一 case 失败须用 ≥3 种不同 workaround 重试（SetCursorPos+SendInput / Snapshot label click / App switch 聚焦后再 click；中文输入失败换剪贴板 STA Runspace）后才能标「环境受限」，SKIP 须等用户确认。
- ⚠️ **截图脱敏**：截任何含 onboarding 窗的画面前先关窗，不要截到账号密码。
- ⚠️ **概率性漂移**：招牌 TC-B1 + 对照 TC-B3 必须连续 ≥2 次（每次重灌钠离子压力），单次结论无效。
- ⚠️ **网络受限**：deepresearch 0 引用不落盘 → 判主题靠**搜索 query / p5s2 topic / sub_questions**，不等落盘 `.md`。
