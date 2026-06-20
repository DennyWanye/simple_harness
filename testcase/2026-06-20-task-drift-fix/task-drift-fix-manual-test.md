# 任务漂移修复（Fix A + Fix B）手工测试文档（windows-mcp 真模拟人）

> **被测改动**：任务漂移修复，commit **acb69a0**（master 已合并，含 Fix A + Fix B + 两条硬 log 锚点）。
> - **Fix A（组装层 · Tier 1 默认开 / Tier 2 默认关）**：
>   - Tier 1 ① **当前请求优先锚定**（`_CURRENT_REQUEST_NUDGE`）：在 `history` 之后、当前 `user` 之前插一条 system nudge「当前请求是本轮唯一任务；先前对话仅为背景，若与当前请求冲突，以当前请求为准」。
>   - Tier 1 ② **L2 历史重定性**（`_L2_CONTEXT_LABEL`）：提升 L2 轮次前在 `l2_history` 头部插一条 system 标签「以下为较早的对话记录，可能涉及其他话题，仅供背景参考」。
>   - Tier 2 **语义截断门控**（`topic_shift_gate`，**默认 false**）：`低相似 ∧ 长消息(>50字) ∧ 无指代起手` 合取才截断旧 L2，短/代词起手豁免；embedder 未 ready/mock/超时 → fail-open 保留全部 L2。
> - **Fix B（分发层 · 通用注入）**：agent loop 在 tool dispatch 处对任何声明了 `user_request` 字段的工具**无条件覆盖**注入本轮用户原话（`loop_user_request`）；deepresearch 的 `_PLAN_PROMPT`/`_SYNTH_PROMPT` 用「ORIGINAL USER REQUEST (authoritative)」双锚。
>
> **对应 Plan**：`plans/2026-06-20-task-drift-fix/00-fix-plan.md`
> （§3 Fix A 机制 / §4 Fix B 机制 / §8 风险分层(Tier1/Tier2) / §9 改点表(A1–A6, B1–B4)）。
>
> **被测代码**（master @ acb69a0）：
> - Fix A：
>   - `backend/deskpet/agent/assembler/components/memory.py`：`_L2_CONTEXT_LABEL`(L34)、`_CURRENT_REQUEST_NUDGE`(L35)、`provide` 里 `relabel_l2`/`anchor_current`/`topic_shift_gate` 门控(L105–129)、`l2_history` 头部插标签(L154–155)、meta 透出 `late_system_nudge`(L191–195)、`_topic_similarity`/`_starts_with_anaphora`(L205–254)。
>   - `backend/deskpet/agent/assembler/bundle.py`：`build_messages` 的 `late_system_nudge` 参数（history 后、user 前插 system，L310–311）+ `ContextBundle.late_system_nudge` 字段(L266)；`MemoryPolicy` 5 字段。
>   - `backend/deskpet/agent/assembler/assembler.py`、`policy.py`（`_to_policy` 解析 5 字段）、`policies/default.yaml`（每个 task_type 段都有 `relabel_l2:true / anchor_current:true / topic_shift_gate:false / topic_shift_threshold:0.35 / l2_keep_on_shift:1`）。
> - Fix B：
>   - `backend/agent/agent_loop.py`：`_tool_declares_user_request`(L47)、`_inject_loop_user_request`(L64) + dispatch 两处调用(L1824, L1911)、`run()` 签名 `loop_user_request`(L582)。
>   - `backend/deskpet/tools/research_tools.py`：`_PLAN_PROMPT`「ORIGINAL USER REQUEST (authoritative)」(L466)、`_SYNTH_PROMPT`(L489)、orchestrator 签名(L992)、`_ur=(user_request or topic).strip()`(L1007)、schema `user_request`(L1605)、handler 取值(L1661/L1685)。
>   - `backend/main.py:6098`：`loop_user_request=(None if _is_sentinel else _text)`（`_text` 是本轮用户原话；sentinel `<<...>>` 不注入）。
> - **★ 两条硬 log 锚点（acb69a0 新增，把原「间接行为证据」升级为硬证据）**：
>   - **`task_drift_context_gate`**（structlog，**每轮组装都打**，落 tauri-dev stderr log）。字段：`session_id` / `relabel_applied`(bool) / `anchor_applied`(bool) / `topic_shift_gate`(bool) / `l2_truncated`(bool) / `gate_sim`(float\|None) / `l2_count_in`(int) / `l2_count_out`(int)。来源：`backend/deskpet/agent/assembler/components/memory.py`（return Slice 前）。
>   - **`task_drift_user_request_injected tool=<name> req_len=<N>`**（stdlib logger，**注入 Fix B `user_request` 时打**）。来源：`backend/agent/agent_loop.py::_inject_loop_user_request`。
>
> **★ 关键事实（影响多条 TC 判定，执行前先读）**：
> 1. **桌宠聊天单一 `session="default"`**（前端不传 session_id）。约 600+（**实测 627** 条）CATL 历史与新请求同一会话 → 这正是漂移温床。
> 2. **漂移是概率性**：模型当场承认 conflict 仍可能漂。所以核心 TC 必须**连续重复 ≥3 次**取稳定结论，单次不漂 ≠ 修复有效。
> 3. **Tier 1 默认开、Tier 2 默认关**：本文档主验 Tier 1（锚定 + 重定性，零删除、零 embedder）+ Fix B。Tier 2（语义截断）默认关，单列可选档 TC-7，需手动改 `topic_shift_gate=true` 才生效。
> 4. **`p5s2_tool_call_args_dump` 是头号「漂没漂」观测锚点**：provider 在 `openai_compatible.py:1295` 打印 LLM 出站的工具调用原始 args（`name=` + `args_preview=` 前 **100 字**）。这是 LLM 自己选的 `topic`，**Fix B 的 `user_request` 注入发生在 agent_loop dispatch（在此 dump 之后）**——所以该 dump 看到的是 LLM 原始选题（漂没漂的判据），不含注入后的 user_request。
> 5. **Fix B 的 `user_request` 注入有硬证据**：grep `task_drift_user_request_injected tool=deepresearch req_len=<N>`，`req_len` ≈ 用户原话字节数 → 直接证明注入了用户原话（不再受 `p5s2` 100 字截断困扰）。落盘报告主题被原话拉回为辅证。
> 6. **Tier 1 锚定 / 重定性现有硬证据**：`task_drift_context_gate` 每轮都打 `anchor_applied` / `relabel_applied`。判定 TC-2/TC-3 直接 grep 这两个布尔字段（不再需要「临时加 prompt dump」或「降级为行为推断」）。`_CURRENT_REQUEST_NUDGE` / `_L2_CONTEXT_LABEL` 的逐字注入位置仍是 system message，若要逐字看可选开 prompt dump（见 §1.6），但**判定不依赖它**。
>
> **执行方式**：真人 / windows-mcp 在真实 DeskPet 桌宠 App 上**模拟鼠标点击 + 键盘输入**执行。**禁止**用 WebSocket 直连 backend、pytest/脚本回放、`import` 内部模块查 registry/policy 当 UI 测试证据（违反项目 CLAUDE.md「手工测试纪律 HARD CONSTRAINT」+ `feedback_real_e2e_not_script_replay`）。**注意**：grep `task_drift_context_gate` / `task_drift_user_request_injected` 是对**真模拟人触发后**真实运行栈打出的 log 做判定（不是脚本回放），合规且为硬证据。
>
> **证据归档**：截图存 `G:\projects\deskpet\testcase\2026-06-20-task-drift-fix\screenshots\`，命名 `<case-id>-<简述>.png`；log/文本 grep 存同父目录 `*.txt`。
>
> **最后更新**：2026-06-20（v3 真机实测后修正 — 见下 ★ 与 [`RESULTS.md`](./RESULTS.md)）
>
> ### ★ v3 真机实测修正（重要，影响 TC-6/TC-7 语义）
> 真机 E2E 暴露并修复了两点（详见 [`RESULTS.md`](./RESULTS.md) §0 三轮迭代）：
> 1. **Tier1 锚定/重定性不足以阻止 topic 漂移**（真机：anchor/relabel 已注入但外层 LLM topic 仍漂回 CATL）→ **Tier2 改为默认开**（`topic_shift_gate: true`），并把过高的 `len>50` 合取阈值改可配置 `topic_shift_min_len`（默认 16，原 50 漏判 32 字的简短新任务）。
> 2. **Tier2 embedding 实时 encode 在真机恒超时**（BGE-M3 subprocess 被 vector worker 回填 + 研究负载抢锁，撞 1500ms 组件 budget）→ Tier2 加**词法内容词重叠兜底**：embedder 超时/不可用 → 退化到零延迟词法信号（跨域漂移 token 重叠≈0 必被抓）。
>
> **新增 log 锚点/字段**（grep 用）：
> - `task_drift_context_gate` 增字段 **`shift_path`**（`off`/`embed`/`lexical`，标明本轮用哪条信号）。
> - `task_drift_sim_ok`（`sim`/`encode_ms`）/ `task_drift_sim_skip`（`reason=no_embedder|not_ready|mock|encode_timeout|encode_error`）——Tier2 相似度路诊断。
>
> **TC-6 语义变更**：原"验 Tier2 默认**关**"已作废 → 现验 Tier2 默认**开**生效（`topic_shift_gate=True` + 跨域 `l2_truncated=True`/同域 `l2_truncated=False`）。
> **TC-7**：embedding 主路真机因锁竞争难稳定触发（恒 `shift_path=lexical`），其正确性由单测覆盖；词法兜底路已真机充分验证。

---

## 1. 测试前置（每次测试前必做）

> 目标：让 Tauri 跑**本仓库 master backend 代码**（含 acb69a0 改动），且 `%APPDATA%/deskpet/data/state.db` 含约 600+（实测 627）条 CATL 历史（漂移金矿）。

### 1.1 清理孤儿进程（坑 #1 / #7：防端口双占）

```powershell
taskkill /F /IM deskpet.exe 2>$null
taskkill /F /IM deskpet-backend.exe 2>$null
Get-Process node -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*deskpet*' } | Stop-Process -Force -ErrorAction SilentlyContinue
```

### 1.2 CATL 历史已实测确认就位（漂移前提，**已成立，可直接用**）

- 桌宠历史存于 app 真实 user_data_dir：`%APPDATA%\deskpet\data\state.db`（**不是** `backend/userdata`，见 deepresearch 测试踩坑）。
- **Lead 已实测确认**：`session='default'` 实有 **627 条**消息（不是旧快照里的 872），且**最近若干条全是 CATL / 宁德时代主题**（年报 / 营业额 / 研发投入 / `site:catl.com` / 做 ppt）。**漂移复现前提成立，无需重建库**，可直接进 §2 测。
- 若执行时想再确认一次（**只读**，不改库，仅环境校验，可跳过）：
  ```powershell
  $DB = "$env:APPDATA\deskpet\data\state.db"
  Test-Path $DB    # 期望 True
  # 仅看条数，不写入。若机器装了 sqlite3：
  # sqlite3 $DB "SELECT count(*) FROM messages WHERE session_id='default';"   # 期望 ≈627
  # sqlite3 $DB "SELECT content FROM messages WHERE session_id='default' ORDER BY created_at DESC LIMIT 5;"  # 期望近 5 条均 CATL/宁德时代
  ```
- ✅ 已确认：`session_id='default'` 约 627 条、**最近若干条都是 CATL / 宁德时代**主题（L2 取最近 5 条 → 全 CATL，构成漂移压力）。
- ❌ 仅当**执行时库被清空**（与已确认事实冲突）才需走 §1.7 重建剧本：用桌宠真模拟人狂聊 CATL 把最近若干轮灌成 CATL，再测。
- 证据（可选）：把只读输出存 `env-01-history-baseline.txt`。

### 1.3 确认 keychain 已有凭据（免登录，真 LLM 链路）

- 之前登录过 → Windows DPAPI keychain 已存 `tsk_xxx`+`key_xxx`，本轮应**直接进主界面、不弹登录窗**。
- 若弹登录窗：从 `G:\projects\deskpet\LOCAL-DEV-CREDENTIALS.md`（gitignored）读账号密码，模拟点击输入 → 登录 → 等 relay 下发 key → 关 onboarding。
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
npx tauri dev 2>&1 | Tee-Object -FilePath "G:\projects\deskpet\testcase\2026-06-20-task-drift-fix\tauri-dev.log"
```

### 1.5 验证跑的是 master 代码（HARD GATE — 不过这条后面全白测）★

```powershell
Select-String -Path "G:\projects\deskpet\testcase\2026-06-20-task-drift-fix\tauri-dev.log" -Pattern "backend_launch"
```

- ✅ 期望：`[backend_launch] Dev python=...\backend\.venv\Scripts\python.exe backend_dir=...\backend`
- ❌ 若出现：`[backend_launch] Bundled exe=...` → 跑的是旧 frozen exe（**不含 acb69a0 改动 → 白测**），停止，回 1.4 修 env 重启。
- 证据：截图主界面（桌宠出现）存 `env-00-boot.png` + grep 输出存 `env-00-backend-launch.txt`。

### 1.6 日志监看入口 + 漂移锚点 grep（后续所有用例共用）

backend structlog 全走 stderr → Tauri `Stdio::inherit()` → 落进 `tauri-dev.log`。

```powershell
$LOG = "G:\projects\deskpet\testcase\2026-06-20-task-drift-fix\tauri-dev.log"

# 【硬锚点 ①】每轮组装门控状态（Tier1 锚定/重定性 + Tier2 截断 + L2 计数）
Select-String -Path $LOG -Pattern "task_drift_context_gate"

# 【硬锚点 ②】Fix B user_request 注入（含 tool 名 + 注入字节数）
Select-String -Path $LOG -Pattern "task_drift_user_request_injected"

# 【漂没漂锚点】LLM 出站工具调用原始 args（含它自己选的 topic）
Select-String -Path $LOG -Pattern "p5s2_tool_call_args_dump"

# deepresearch 调用 / 落盘 / 内部阶段
Select-String -Path $LOG -Pattern "deepresearch|user_request|OutPut/Research|p5s2_tool_call_args_dump"
```

> ✅ **判定优先用两条硬锚点**：`task_drift_context_gate`（Tier1/Tier2 门控真值）+ `task_drift_user_request_injected`（Fix B 注入真值）。这两条每轮真实运行栈打出，是硬证据，**不受** `p5s2` 100 字截断限制。
> ⚠️ `p5s2_tool_call_args_dump` 的 `args_preview` 只截前 **100 字符**，只用作「LLM 选的 topic 漂没漂」的辅助观测；判 Fix B 是否注入原话**不要**用它（用硬锚点 ② 的 `req_len`）。

#### （可选 · 逐字看注入文本）开 prompt dump

两条硬锚点已足够判定全部涉 log 的 TC。仅当想**逐字看** `_CURRENT_REQUEST_NUDGE` / `_L2_CONTEXT_LABEL` 的注入文本与位置时，才需让 backend dump 最终 messages[]（优先用现成调试开关如 `DESKPET_DEBUG_PROMPT_DUMP` 类 env / log_level=debug；无现成开关则跳过，**不强行改产品代码**）。这是锦上添花，**判定不依赖它**。

### 1.7 （备用）重建 CATL 历史剧本（仅当执行时库被清空、与 1.2 已确认事实冲突时才需）

按 §2 真模拟人连发 6~8 条 CATL 深问，把最近若干轮灌成 CATL：
`宁德时代2024年营收多少`→`它的毛利率呢`→`动力电池出货量`→`海外产能布局`→`麒麟电池技术路线`→`和比亚迪刀片电池对比`。每条等桌宠答完。之后最近 5 条 L2 即全 CATL。

---

## 2. 对话操作通用步骤（每个用例复用 · windows-mcp 真模拟）

每个「**发起对话**」均指以下动作序列：

1. **截图**抓当前桌宠状态（基线）。
2. **定位输入框**：Snapshot/Screenshot 找桌宠聊天输入框坐标 `(x_in, y_in)`。
3. **declare**：`坐标=(x_in,y_in) | 动作=click 聚焦输入框 | 期望=光标进入输入框`。
4. **真点击聚焦**：`SetCursorPos(x_in,y_in)` + SendInput LEFTDOWN/UP（WebView2 不吃老式 mouse_event，用 SendInput）。
5. **中文输入用剪贴板**：STA Runspace `[Clipboard]::SetText("<prompt>")` → `Ctrl+V`（SendKeys 不支持中文 IME）。
6. **回车发送**：SendKeys `{ENTER}`（或点发送按钮）。
7. **等待**：deepresearch 重型工具，standard 档 30–120s，最长等 300s（用 `WaitFor` 轮询 ArtifactCard / 报告气泡出现）。
8. **截图**抓最终结果。

> Click 失败 workaround（按优先级 retry ≥3 次）：`SetCursorPos + SendInput` → `Click(label=…)` 用 Snapshot → `App switch` 聚焦后再 click。
> 中文输入失败 workaround：STA Runspace `Clipboard.SetText("中文")` + Ctrl+V；焦点不在目标窗口 → 先 click 输入框聚焦再粘贴；用 backend log 确认消息真到了。

---

## 3. 测试用例

### 维度 1 — 核心漂移修复 happy path（★ 一票否决级）

#### TC-1 — 627 CATL 历史下发无关新请求，topic 不漂回 CATL（连续 ≥3 次）

- **类型**：UI 真测（真模拟人）+ 后端日志判定 — **★招牌**
- **目的**：在最近 5 条 L2 全 CATL 的会话里，发**完全无关**的新调研请求 → LLM 调 `deepresearch` 的 `topic` 应是新请求主题（Rust/Tokio），**不漂回宁德/CATL**。这是修复要根治的核心 bug。
- **前置**：§1 全就绪；§1.2 已确认最近若干条 L2 是 CATL（627 条库）；§1.5 跑的是 master 代码。
- **精确步骤**（同一 prompt **连续发 3 次**，每次 fresh 一轮、等完成）：

| 步骤 | 动作（declare：坐标 / 动作 / 期望） | 期望结果 |
|---|---|---|
| 1 | Screenshot 抓桌宠主界面 + 输入框，记坐标 `(x,y)` | `screenshots/TC-1-step1.png`；输入框可见 |
| 2 | `坐标=(x,y) \| 动作=Clipboard "帮我深度调研 Rust 异步运行时 Tokio 的架构与竞品对比" → Ctrl+V → Enter \| 期望=触发 deepresearch` | 桌宠开始跑深度研究 |
| 3 | 等完成（最长 300s），截图最终报告 | `screenshots/TC-1-run1-report.png` |
| 4 | grep 本轮 `p5s2_tool_call_args_dump` 的 deepresearch 调用 | `name=deepresearch` 行的 `args_preview` 中 `topic` 含 **Rust/Tokio**，**不含** 宁德/CATL/电池 |
| 5 | **重复步骤 2–4 共 3 次**（run1/run2/run3），每次记录 topic | 3 次 topic 均为 Rust 系，0 次漂回 CATL |

- **可观测证据**：
  - ✅ 修复后：3 次 `p5s2_tool_call_args_dump` 的 deepresearch `topic` 均含 Rust/Tokio；最终报告主题是 Rust 异步运行时（截图）。
  - ❌ 漂移复发：任一次 `topic` 出现「宁德时代 / CATL / 动力电池 / 麒麟电池」等 CATL 主题；或报告正文讲的是电池而非 Rust。
- **PASS 判据**：连续 3 次 deepresearch `topic` 全部为 Rust 系、0 次含 CATL 关键词 + 报告主题截图为 Rust。
- **FAIL 判据**：≥1 次 topic 漂回 CATL，或报告主题是 CATL。
- **RETRY 纪律**：单次不漂不足以判 PASS（漂移概率性）；若 3 次里有 1 次漂 → 记为 FAIL 并补跑 2 次确认稳定性（5 次里 ≥1 漂即 FAIL）。
- **证据**：`TC-1-run{1,2,3}-report.png` + `TC-1-log-topic.txt`（3 次 grep 输出，含 timestamp，每行可见 `name=deepresearch` + topic 片段）。

---

#### TC-1b — 第二个无关主题交叉验证（防 prompt 过拟合 Rust）

- **类型**：UI 真测 + log
- **目的**：换一个与 CATL 同样无关、但领域不同的主题，确认不漂不是「恰好 Rust 这条 prompt 不漂」。
- **前置**：同 TC-1（最近 L2 仍是 CATL；若 TC-1 已把 Rust 灌进历史，先按 §1.7 或重启重置回 CATL 占主导，或接受最近 5 条混入 1~2 条 Rust——记录实际状态）。
- **精确步骤**：按 §2 发起 `帮我深度调研 PostgreSQL 与 MySQL 在 OLTP 场景的性能与生态对比`，等完成。grep 该轮 `p5s2_tool_call_args_dump`。
- **预期**：deepresearch `topic` 含 PostgreSQL/MySQL/数据库，不含 CATL/电池。
- **PASS 判据**：topic 为数据库主题、无 CATL 关键词。
- **FAIL 判据**：topic 漂回 CATL。
- **证据**：`TC-1b-report.png` + `TC-1b-log-topic.txt`。

---

### 维度 2 — Tier 1 当前请求锚定真注入

#### TC-2 — `_CURRENT_REQUEST_NUDGE` 锚定每轮生效（硬锚点 `anchor_applied=True`）

- **类型**：后端日志判定（硬锚点 `task_drift_context_gate`，真实运行栈）
- **目的**：验证 Tier 1 ① 当前请求锚定真生效（`anchor_current` 默认 true，default chat policy）。acb69a0 后**每轮组装**都打 `task_drift_context_gate`，其中 `anchor_applied` 标明锚定 system nudge 是否插入。
- **前置**：§1 就绪；最近 L2 是 CATL。无需 prompt dump。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 按 §2 真模拟人发起 `帮我深度调研 Rust 异步运行时 Tokio`（同 TC-1）| 触发一轮组装 → 打出 `task_drift_context_gate` |
| 2 | grep 该轮 `task_drift_context_gate` 的 `anchor_applied` 字段 | 命中行含 `anchor_applied=True` |

- **确切 grep 命令**：
  ```powershell
  Select-String -Path $LOG -Pattern "task_drift_context_gate" | Select-String -Pattern "anchor_applied=True"
  ```
- **可观测证据**：
  - ✅ 生效：本轮 `task_drift_context_gate` 行含 `anchor_applied=True`（默认 chat policy `anchor_current=true` → 每轮都应 True）。
  - ❌ 失效：`anchor_applied=False` 或字段缺失（`anchor_current` 没解析 / meta 没透出 / bundle 没插）。
- **PASS 判据**：本轮 `task_drift_context_gate` 含 `anchor_applied=True`。
- **FAIL 判据**：`anchor_applied=False` 或锚点行缺失。
- **（可选锦上添花）**：若想逐字确认 nudge 文本「当前请求是本轮唯一任务…」位置在 history 后、user 前，开 §1.6 prompt dump；**判定不依赖此项**。
- **证据**：`TC-2-anchor-applied.txt`（grep 命中行，含 timestamp + `anchor_applied=True`）。

---

### 维度 3 — Tier 1 L2 历史重定性真注入

#### TC-3 — `_L2_CONTEXT_LABEL` 重定性每轮生效（硬锚点 `relabel_applied=True`）

- **类型**：后端日志判定（硬锚点 `task_drift_context_gate`，真实运行栈）
- **目的**：验证 Tier 1 ② 重定性标签真插在 `l2_history` 头部（`relabel_l2` 默认 true），让 LLM 把旧 CATL 轮当「背景」而非「活跃对话线」。acb69a0 后每轮组装打 `task_drift_context_gate` 的 `relabel_applied`（**有 L2 历史时**应为 True）。
- **前置**：同 TC-2，且**本轮有 L2 历史**（627 库的 CATL 轮即满足）。
- **精确步骤**：

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 按 §2 真模拟人发起任一新请求（如 TC-1 的 Rust 调研，会带入 CATL L2） | 触发组装 → 打 `task_drift_context_gate`，`l2_count_in>0` |
| 2 | grep 该轮 `task_drift_context_gate` 的 `relabel_applied` 字段 | 命中行含 `relabel_applied=True` |

- **确切 grep 命令**：
  ```powershell
  Select-String -Path $LOG -Pattern "task_drift_context_gate" | Select-String -Pattern "relabel_applied=True"
  ```
- **可观测证据**：
  - ✅ 生效：有 L2 历史时本轮 `task_drift_context_gate` 含 `relabel_applied=True`（且 `l2_count_in>0`）。
  - ❌ 失效：有 L2 时 `relabel_applied=False`（`relabel_l2` 没接上 / l2_history 头部没插标签）。
- **PASS 判据**：有 L2 历史的轮，`relabel_applied=True`。
- **FAIL 判据**：有 L2 历史却 `relabel_applied=False`。
- **（可选锦上添花）**：开 §1.6 prompt dump 逐字确认 history 首条 = `以下为较早的对话记录，可能涉及其他话题，仅供背景参考。`；**判定不依赖此项**。
- **证据**：`TC-3-relabel-applied.txt`（grep 命中行，含 `relabel_applied=True` + `l2_count_in`）。

---

### 维度 4 — 追问连续性不被误伤（★ 头号负向，一票否决级）

#### TC-4 — 先聊 CATL 再发短追问/代词起手 → 仍正确答 CATL，不丢上下文

- **类型**：UI 真测（真模拟人）— **★招牌（防误伤）**
- **目的**：Tier 1 永不删历史 + Tier 2 默认关 → 短追问 / 代词起手**绝不能**被截断上下文。这是修复最危险的反向风险（误删追问需要的历史）。验证桌宠对追问**仍答 CATL 上下文**。
- **前置**：§1 就绪。本 TC **不依赖** 627 库，自建一段 CATL 对话更可控。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 按 §2 发起 `宁德时代2024年的营收和净利润大概是多少` | 桌宠答 CATL 营收数据 |
| 2 | 截图答复 | `screenshots/TC-4-step1.png` |
| 3 | `坐标=(x,y) \| 动作=Clipboard "它的主要竞争对手有哪些？" → Ctrl+V → Enter \| 期望=答 CATL 竞品(比亚迪/LG/松下等)` | 桌宠答**宁德时代的竞品**，不是泛泛或别的公司 |
| 4 | 截图答复 | `screenshots/TC-4-step3.png` |
| 5 | `坐标=(x,y) \| 动作=Clipboard "继续" → Ctrl+V → Enter \| 期望=接着上文展开` | 桌宠**接着 CATL 话题**继续，不反问"继续什么/没有上下文" |
| 6 | 截图答复 | `screenshots/TC-4-step5.png` |
| 7 | （可选）grep `p5s2_tool_call_args_dump` 确认未把追问当全新无关任务漂走 | 若调工具，topic 仍 CATL 相关 |

- **可观测证据**：
  - ✅ 不误伤：步骤 3 答 CATL 竞品（含具体公司名）；步骤 5「继续」接 CATL 话题。
  - ❌ 误伤复发：步骤 3 桌宠答非所问 / 答别的公司 / 反问「你指哪家公司」；步骤 5「继续」答「继续什么？我们没有上下文」——说明上下文被截断（Tier 2 误开或 `_starts_with_anaphora` 豁免失效）。
- **PASS 判据**：步骤 3 + 步骤 5 均正确接 CATL 上下文。
- **FAIL 判据**：任一短追问/代词起手丢上下文或答非所问。
- **证据**：`TC-4-step{1,3,5}.png` + （若调工具）`TC-4-log.txt`。

---

#### TC-4b — Tier 2 默认关时长追问也不应被截（合取判据保护）

- **类型**：UI 真测 + log
- **目的**：Tier 2 默认关，**任何**追问（含较长的延续型追问）都不应丢历史。即便未来误开 Tier 2，合取判据（需「低相似 ∧ 长 ∧ 无指代」三者皆满足才截）也应放过延续型。本 TC 在默认配置下验证延续型长追问不丢上下文。
- **前置**：先建 CATL 对话（TC-4 步骤 1 之后接续）。
- **精确步骤**：按 §2 发 `它在欧洲和北美的产能布局分别是怎么规划的，和上面说的营收增长有什么关系`（长但仍是 CATL 延续追问），看桌宠是否仍答 CATL 上下文。
- **预期**：答 CATL 欧美产能 + 关联前文营收，不漂、不丢上下文。
- **PASS 判据**：答复延续 CATL。
- **FAIL 判据**：丢上下文 / 漂到无关主题。
- **证据**：`TC-4b-reply.png`。

---

### 维度 5 — Fix B deepresearch 原话直传

#### TC-5 — deepresearch 收到注入的 `user_request`（硬锚点 `task_drift_user_request_injected`）

- **类型**：后端日志判定（硬锚点，真实运行栈）
- **目的**：验证 Fix B：agent loop 在 dispatch 处对 deepresearch（声明了 `user_request`）**无条件注入** `loop_user_request=_text`（本轮原话）。即便外层 LLM 选的 `topic` 略漂，研究内部 plan/synth 拿到的是用户原话，主题被拉回。acb69a0 后注入瞬间打 `task_drift_user_request_injected tool=deepresearch req_len=<N>` —— **这是硬证据，彻底替代「p5s2 args_preview 100 字截断看不到 user_request」的旧困境**。
- **前置**：§1 就绪；跑过一次 deepresearch（复用 TC-1 run1 即可）。
- **精确步骤**：

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 复用 TC-1 的 Rust 调研那轮（或新发一次原话「帮我深度调研 Rust 异步运行时 Tokio 的架构与竞品对比」） | 触发 deepresearch，dispatch 处注入 user_request |
| 2 | grep 注入硬锚点 | 出现 `task_drift_user_request_injected tool=deepresearch req_len=<N>`，`req_len` > 0 且 ≈ 用户原话字节数（中文 UTF-8，本句约 60+ 字节级别，非 0） |
| 3 | 点开落盘报告头部 / ArtifactCard，确认研究主题对得上原话 | 报告主题 = Rust Tokio，非 CATL |

- **确切 grep 命令**：
  ```powershell
  Select-String -Path $LOG -Pattern "task_drift_user_request_injected" | Select-String -Pattern "tool=deepresearch"
  ```
- **可观测证据**：
  - ✅ 生效：命中 `task_drift_user_request_injected tool=deepresearch req_len=<N>`，`req_len>0`（注入了非空原话）；报告主题对得上原话。
  - ❌ 失效：无该锚点行（注入没接上 / schema 没声明字段），或 `req_len=0`（注入了空串）。
- **PASS 判据**：命中 `tool=deepresearch req_len>0` + 报告主题与原话一致。
- **FAIL 判据**：无注入锚点行，或 `req_len=0`。
- **证据**：`TC-5-userrequest-injected.txt`（grep 命中行，含 `req_len` + timestamp）+ `TC-5-report-head.png`。

---

#### TC-5b — 即便外层 topic 略漂，user_request 把研究主题拉回（纵深防御验证）

- **类型**：UI 真测 + log（机会性，非必现）
- **目的**：验证 Fix B 的纵深价值：构造一个**容易诱导外层 topic 略漂**的请求（在 CATL 重历史下发一个相邻但不同的新主题），观察即使 `p5s2_tool_call_args_dump` 的 topic 带了点 CATL 味，落盘报告主题仍被 `user_request` 原话拉回。
- **前置**：627 CATL 历史在位。
- **精确步骤**：按 §2 发 `帮我深度调研 钠离子电池 2026 年的产业化进展与主要厂商`（与 CATL「电池」相邻但主题是钠电而非宁德），等完成。对比 `p5s2_tool_call_args_dump` 的 topic vs 落盘报告主题。
- **预期**：即便 topic 偶尔带「宁德」字样，报告主题应是**钠离子电池产业化**（被 user_request 原话锚定），不变成「宁德时代财报」。
- **PASS 判据**：报告主题 = 钠离子电池（用户原话主题）。
- **FAIL 判据**：报告整体变成 CATL 公司调研。
- **固有限制 / SKIP 判据**：本 TC 依赖「外层 topic 恰好略漂」这个**偶发条件**才能展示纵深价值。三种情形：
  - 外层根本不漂（Tier 1 已压住，`p5s2` topic = 钠电）→ 报告本就正确 → **记 PASS** + 标注「纵深条件未触发（主行为 Tier1 已正确），Fix B 纵深未被调用但 user_request 注入已由 TC-5 硬锚点证明」。
  - 外层略漂 + 报告被拉回钠电 → **记 PASS**（纵深真触发并生效，最佳证据）。
  - 外层略漂 + 报告变 CATL → **记 FAIL**。
  - 想强制触发可临时调高漂移压力（更多 CATL 追问后再发），但**不强求**——纵深机制的注入侧已由 TC-5 硬证据覆盖。
- **证据**：`TC-5b-topic-vs-report.txt`（`p5s2` topic vs 报告主题对比 + 是否触发纵深的判断）+ `TC-5b-report.png`。

---

### 维度 6 — Tier 2 默认关回归（无新增延迟 / 无 L2 丢失）

#### TC-6 — `topic_shift_gate=false` 默认下不调 embedder、不截断 L2

- **类型**：后端日志判定 + UI 真测
- **目的**：验证默认配置（`topic_shift_gate:false`）下，组装层**不进入** `_topic_similarity` 分支 → 不调 embedder、不截断 L2、无新增延迟、无 L2 丢失。这是「Tier 1 够用、Tier 2 不上」的回归。
- **前置**：§1 就绪；确认未手动改 `topic_shift_gate`（默认 false）。
- **精确步骤**：

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 按 §2 真模拟人发任一新请求（Rust 调研 / 普通对话） | 触发组装 → 打 `task_drift_context_gate` |
| 2 | grep 该轮 `task_drift_context_gate` 的 Tier2 三字段 | 命中行含 `topic_shift_gate=False` + `l2_truncated=False` + `gate_sim=None`（默认不调 embedder → 相似度未算 → None） |
| 3 | 同行确认 L2 计数不缩（无截断） | `l2_count_out` == `l2_count_in`（一条不少） |
| 4 | 截图桌宠正常答复 | `screenshots/TC-6-reply.png` |

- **确切 grep 命令**：
  ```powershell
  Select-String -Path $LOG -Pattern "task_drift_context_gate" | Select-String -Pattern "topic_shift_gate=False"
  # 同一命中行应同时含 l2_truncated=False 和 gate_sim=None；并核对 l2_count_out == l2_count_in
  ```
- **可观测证据**：
  - ✅ 默认关回归正常：`topic_shift_gate=False` + `l2_truncated=False` + `gate_sim=None`（未调 embedder）+ `l2_count_out==l2_count_in`（L2 完整）。
  - ❌ 异常：默认配置下竟 `topic_shift_gate=True`（`_to_policy` 解析错 / 被误置）、或 `l2_truncated=True` / `l2_count_out<l2_count_in`（默认不该截）、或 `gate_sim` 是浮点数（默认不该调 embedder）。
- **PASS 判据**：默认关下 `topic_shift_gate=False` + `l2_truncated=False` + `gate_sim=None` + `l2_count_out==l2_count_in`。
- **FAIL 判据**：任一字段偏离上述（发生截断 / 调了 embedder / L2 缩短）。
- **证据**：`TC-6-tier2-default-off.txt`（命中行，含三字段 + l2 计数）+ `TC-6-reply.png`。

---

### 维度 7 — Tier 2 开启（可选档，需手动改配置）

#### TC-7 — `topic_shift_gate=true` 下：低相似长新任务截断旧 L2 + 短/代词追问豁免

- **类型**：UI 真测 + log（**可选档**，前置改配置 + 重启）
- **目的**：验证 Tier 2 语义截断门控在开启时**只截真跳变**（低相似 ∧ >50字 ∧ 无指代），且短/代词追问被豁免不截。这是 Tier 1 不够时的灰度档。
- **★前置（须手动开 + 重启，验完务必关回）**：
  - 临时改 `backend/deskpet/agent/assembler/policies/default.yaml` 把相关 task_type 段（至少 `chat` / `task`）的 `topic_shift_gate: false` 改为 `true`（**或**用 override 机制，若 backend 支持）。
  - 重启桌宠，§1.5 重新确认跑 master 代码。
  - **验完务必改回 `false`**（恢复默认），避免污染后续 TC。
  - **embedder 须 ready**（非 mock）：Tier 2 依赖真 BGE-M3；若 embedder 是 mock（md5 无语义）→ `_topic_similarity` 返回 None → fail-open 不门控，截断永不发生 → 本 TC **无法验证**，须标注「embedder 未 ready，环境受限」+ 前置不满足。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 确认 `topic_shift_gate=true` 已生效 + embedder ready（grep boot log 确认非 mock） | 前置满足 |
| 2 | 先建/确认最近 L2 是 CATL（§1.7 或 627 库） | L2 = CATL |
| 3 | 按 §2 发**长无关新任务** `帮我深度调研 Rust 异步运行时 Tokio 的架构、调度模型与主流竞品的工程权衡对比`（>50字、无指代起手） | 该轮 `task_drift_context_gate` 含 `topic_shift_gate=True` + `gate_sim=<具体浮点数>`（低相似）+ `l2_truncated=True` + `l2_count_out<l2_count_in`；`p5s2` topic = Rust 不漂 |
| 4 | 截图报告主题 | `screenshots/TC-7-shift-report.png` |
| 5 | **豁免验证**：新建 CATL 对话后发**短代词追问** `它的竞品呢`（<10字 + 代词起手） | 该轮 `task_drift_context_gate` 含 `l2_truncated=False` + `l2_count_out==l2_count_in`（`_starts_with_anaphora` 豁免 → 不截）→ 桌宠仍答 CATL 竞品 |
| 6 | 截图豁免答复 | `screenshots/TC-7-anaphora-keep.png` |

- **确切 grep 命令**：
  ```powershell
  # 长任务轮：应见截断
  Select-String -Path $LOG -Pattern "task_drift_context_gate" | Select-String -Pattern "topic_shift_gate=True"
  # 核对同行 gate_sim=<float>、l2_truncated=True、l2_count_out < l2_count_in
  # 短代词追问轮：应见豁免（l2_truncated=False、l2_count_out==l2_count_in）
  ```
- **可观测证据**：
  - ✅ Tier 2 正确：长无关新任务 → `gate_sim` 为具体浮点数（低相似）+ `l2_truncated=True` + `l2_count_out<l2_count_in`；短/代词追问 → `l2_truncated=False` + `l2_count_out==l2_count_in`（仍答 CATL）。
  - ❌ Tier 2 错误：长新任务 `l2_truncated=False`（门控没生效）；或短追问 `l2_truncated=True`（豁免失效 → 误伤，比不开还糟）。
- **PASS 判据**：步骤 3 `l2_truncated=True` + `gate_sim` 浮点 + `l2_count_out<l2_count_in` + topic 不漂；步骤 5 `l2_truncated=False` + `l2_count_out==l2_count_in`。
- **FAIL 判据**：长任务不截，或短追问被误截。
- **固有限制 / SKIP 判据（embedder 依赖）**：Tier 2 强依赖 **embedder 非 mock**（真 BGE-M3）。若该机 BGE-M3 未加载 / 是 mock（md5 无语义）→ `_topic_similarity` 返回 None → `gate_sim=None` → fail-open 不门控、截断永不发生 → 本 TC **无法真机验证**。此时标注「**环境受限，Tier 2 真机不可验，靠单测覆盖**（§plan §9.4 四组 fixture）」+ 等用户确认。判别法：grep boot log 确认 embedder 是否 mock，或观察 step 3 是否 `gate_sim=None`。
- **环境受限标注**：若 embedder 非 ready / 无法改配置重启 → 同上标注「Tier 2 可选档，环境受限未验」+ 等用户确认。
- **证据**：`TC-7-shift-report.png` + `TC-7-anaphora-keep.png` + `TC-7-log-gate.txt`（长任务轮 + 短追问轮各一组 `task_drift_context_gate` 命中行）。

---

### 维度 8 — BC / 回归（修复不破坏既有路径）

#### TC-8 — 单一主题连续对话不受影响

- **类型**：UI 真测
- **目的**：全程聊同一主题（高相似），Tier 1 锚定不该副作用、追问连续，无任何「以当前请求为准」导致的上下文割裂。
- **前置**：§1 就绪，从一个干净/单一主题起聊。
- **精确步骤**：按 §2 连发同主题 3 轮：`Python 的 asyncio 是怎么工作的`→`那 event loop 具体调度策略是什么`→`和多线程比性能怎么样`。每轮等答完。
- **预期**：三轮连贯、层层递进答 asyncio，无失忆、无主题割裂。
- **PASS 判据**：三轮答复连续且都在 asyncio 主题内。
- **FAIL 判据**：中途丢上下文 / 锚定导致割裂。
- **证据**：`TC-8-turn{1,2,3}.png`。

---

#### TC-9 — 非 deepresearch 普通对话 / 普通工具仍正常（Fix B 不误注入）

- **类型**：UI 真测 + log
- **目的**：Fix B 的 `user_request` 注入只对**声明了该字段的工具**（`_tool_declares_user_request`）生效。普通对话、普通工具（如 web_search / 不声明 user_request 的工具）不应被注入、不报错。
- **前置**：§1 就绪。
- **精确步骤**：
  1. 按 §2 发普通对话 `你好，今天心情怎么样`（不调工具）→ 桌宠正常闲聊。
  2. 发快查 `Tokio 最新稳定版本号是多少`（应走 web_search，**不**声明 user_request）。
  3. grep 硬锚点确认 **未对 web_search 注入** user_request（注入仅对声明字段的工具）。
- **确切 grep 命令**：
  ```powershell
  # 期望：无 tool=web_search 的注入行（只对声明 user_request 的工具如 deepresearch 才打）
  Select-String -Path $LOG -Pattern "task_drift_user_request_injected" | Select-String -Pattern "tool=web_search"
  ```
- **预期**：普通对话正常；web_search 正常返回；上面 grep **零命中**（`task_drift_user_request_injected` 只对声明字段的工具打，web_search 不声明 → 不打 → 证明未误注入）；无报错。
- **PASS 判据**：闲聊正常 + web_search 正常 + `task_drift_user_request_injected tool=web_search` 零命中 + 无 traceback。
- **FAIL 判据**：出现 `tool=web_search` 注入行（误注入）/ 报错 / 闲聊异常。
- **证据**：`TC-9-chat.png` + `TC-9-log.txt`（含「tool=web_search 注入 grep 零命中」截图/输出）。

---

#### TC-10 — 首条消息（无历史）不报错

- **类型**：UI 真测
- **目的**：全新会话 / L2 为空时，Tier 1（`l2_history` 为空 → 不插标签也不报错）+ Fix A 门控（`l2_rows` 空 → 跳过）+ Fix B 正常工作，不因「无历史」抛异常。
- **前置**：**全新会话**（清空 default 历史，或换一个无历史的 session 状态）。若无法清库，标注前置近似（用历史极少的状态）。
- **精确步骤**：在无历史状态下按 §2 发 `帮我深度调研 Rust 异步运行时 Tokio`。grep 该轮 `task_drift_context_gate`。
- **确切 grep 命令**：
  ```powershell
  Select-String -Path $LOG -Pattern "task_drift_context_gate"
  # 期望该轮 l2_count_in=0、relabel_applied=False（L2 空 → 不插重定性标签，无空标签污染）；anchor_applied 仍可 True
  ```
- **预期**：正常触发 deepresearch、topic = Rust、报告正常出；backend log 无 traceback；该轮 `task_drift_context_gate` 含 `l2_count_in=0` + `relabel_applied=False`（L2 空 → `_L2_CONTEXT_LABEL` 不插入，无空标签污染）。
- **PASS 判据**：无历史下正常出报告 + 无异常 + `relabel_applied=False`（空 L2 不插标签）。
- **FAIL 判据**：报错 / 崩溃 / 空 L2 下 `relabel_applied=True`（插了孤立标签）。
- **证据**：`TC-10-report.png` + `TC-10-log.txt`（含 `l2_count_in=0` + `relabel_applied=False` 命中行）。

---

### 维度 9 — 边界

#### TC-11 — sentinel / auto-resume 不被当用户原话注入（Fix B 边界 · 被动观测）

- **类型**：后端日志判定（被动观测 · 机会性）
- **目的**：`main.py:6098` 用 `loop_user_request=(None if _is_sentinel else _text)`——sentinel 消息（`<<...>>` 起止，如 auto-resume / 系统内部触发）**不应**被当用户原话注入进工具 `user_request`。验证这个守卫。
- **前置**：§1 就绪。sentinel 路径人工**难以**稳定触发。
- **精确步骤（被动观测，不强行构造）**：
  1. **被动观测**：在正常真模拟人发消息（如 TC-1/TC-5 各轮）时，grep `task_drift_user_request_injected`，确认每条注入的 `req_len` 都来自**真实用户输入**（字节数与所发原话相符），**而非 sentinel 文本**。sentinel `<<...>>` 短且内容固定，若被注入会出现异常的 `req_len`（对应 `<<...>>` 字节数）或对 sentinel 轮打了注入行。
  2. **机会性**：若执行期间恰好触发 auto-resume / 系统续跑（sentinel 轮），grep 该轮——期望**无** `task_drift_user_request_injected` 行（`loop_user_request=None` → `_inject_loop_user_request` 早返回不打不写）。
- **确切 grep 命令**：
  ```powershell
  # 列出所有注入行，逐条核对 req_len 对应真实用户原话字节数，无对应 sentinel <<...>> 的异常注入
  Select-String -Path $LOG -Pattern "task_drift_user_request_injected"
  ```
- **预期**：所有 `task_drift_user_request_injected` 的 `req_len` 都对得上真实用户原话；sentinel 轮（若观测到）无注入行。
- **PASS 判据**：注入行全部来自真实输入；无 sentinel 文本被注入。
- **FAIL 判据**：出现 `req_len` 异常匹配 sentinel `<<...>>` 字节数的注入行，或 sentinel 轮打了注入。
- **固有限制 / SKIP 判据**：sentinel 路径难稳定人工触发 → 本 TC 以**被动观测**为主（正常发消息时核对注入来源），auto-resume 场景**机会性**捕获。若整轮执行未自然触发 sentinel → 标注「sentinel 路径未自然触发，靠被动观测注入来源全部正常 + 守卫逻辑位置（main.py:6098 三元 + agent_loop `_inject_loop_user_request` 的 `if not loop_user_request` 早返回）」+ 等用户确认。
- **证据**：`TC-11-log-sentinel.txt`（所有 `task_drift_user_request_injected` 行 + req_len 对应真实输入的核对）。

---

#### TC-12 — 多窗口（主桌宠 + 消息面板）共享 default 会话行为一致

- **类型**：UI 真测（真模拟人）
- **目的**：Fix A 不改 session_id（对 fan-out 零影响）。验证主桌宠小气泡 + 主线程大消息面板共享 `session="default"` 时，漂移修复 + 追问连续在两个 venue 都成立、且互相可见。
- **前置**：§1 就绪；知道如何打开主线程大消息面板（点"消息"按钮，660×900 面板，见 deepresearch/compaction 测试 venue 发现）。
- **精确步骤**：

| 步骤 | 动作（declare） | 期望结果 |
|---|---|---|
| 1 | 在**小气泡**输入框发 `宁德时代2024营收` | 桌宠答 CATL |
| 2 | 打开**主线程大消息面板**，发短追问 `它的竞品呢` | 面板答 CATL 竞品（共享 default 历史，上下文可见） |
| 3 | 在大面板发无关新请求 `帮我深度调研 Rust 异步运行时 Tokio` | topic = Rust 不漂 |
| 4 | grep `p5s2_tool_call_args_dump` 确认两 venue 行为一致 | 追问 venue 答 CATL、新请求 venue topic=Rust |

- **可观测证据**：
  - ✅ 一致：两 venue 共享 default 历史，追问连续 + 新请求不漂在两处都成立。
  - ❌ 不一致：某 venue 丢上下文 / 某 venue 漂移（说明 fan-out 与组装层耦合出问题）。
- **PASS 判据**：两 venue 追问连续 + 新请求不漂。
- **FAIL 判据**：任一 venue 行为偏离。
- **证据**：`TC-12-bubble.png` + `TC-12-panel.png` + `TC-12-log.txt`。

---

## 4. 证据归档说明

- **截图目录**：`G:\projects\deskpet\testcase\2026-06-20-task-drift-fix\screenshots\`
- **log/文本证据目录**：`G:\projects\deskpet\testcase\2026-06-20-task-drift-fix\`（含 `tauri-dev.log` + 各 `*.txt`）。
- **命名规范**：`<case-id>-<简述>.png` / `<case-id>-<简述>.txt`（如 `TC-1-log-topic.txt`）。
- **每个用例报告格式**（汇总写到本目录 `RESULTS.md`）：
  ```
  case:    TC-1
  坐标:    (x_in, y_in) [物理像素]
  动作:    click 输入框 → Clipboard "帮我深度调研 Rust..." → Ctrl+V → Enter（×3）
  截图:    screenshots/TC-1-run1-report.png ...
  log证据: <grep p5s2_tool_call_args_dump 命中行，3 次 topic 均 Rust 无 CATL，含 timestamp>
  判定:    PASS / FAIL / RETRY-N / SKIP（带理由）
  ```
- **失败重试纪律**：任一 case 失败须用 ≥3 种不同 workaround 重试（SetCursorPos+SendInput / Snapshot label click / App switch 聚焦后再 click；中文输入失败换剪贴板 STA Runspace）后才能标「环境受限」，且 SKIP 须等用户确认。
- ⚠️ **截图脱敏**：截任何含 onboarding 窗的画面前先关窗，不要截到账号密码。
- ⚠️ **概率性漂移**：核心 TC-1 必须连续 ≥3 次，单次结论无效。

---

## 5. 用例索引（共 15 例）

| ID | 维度 | 标题 | 一票关键判定 | 需 windows-mcp 真模拟 |
|---|---|---|---|---|
| TC-1 | 1 核心漂移(★) | 627 CATL 下 Rust 新请求不漂(连续3次) | 3 次 deepresearch topic 全 Rust、0 含 CATL | ✅ 是 |
| TC-1b | 1 核心漂移 | 第二主题(PG/MySQL)交叉验证不漂 | topic 为数据库、无 CATL | ✅ 是 |
| TC-2 | 2 锚定注入 | `anchor_applied=True` 每轮生效 | `task_drift_context_gate` 含 `anchor_applied=True` | 触发轮需真模拟(硬锚点判定) |
| TC-3 | 3 重定性注入 | `relabel_applied=True`(有 L2 时) | `task_drift_context_gate` 含 `relabel_applied=True` | 触发轮需真模拟(硬锚点判定) |
| TC-4 | 4 追问不误伤(★) | 短/代词追问仍答 CATL | "它竞品呢"/"继续" 接 CATL 上下文 | ✅ 是 |
| TC-4b | 4 追问不误伤 | 长延续追问也不丢上下文 | 长追问延续 CATL | ✅ 是 |
| TC-5 | 5 FixB 原话直传 | deepresearch 注入 user_request(硬锚点) | `task_drift_user_request_injected tool=deepresearch req_len>0` | ✅ 是(触发轮) |
| TC-5b | 5 FixB 纵深 | 外层略漂时报告主题被原话拉回 | 报告主题=用户原话主题(纵深未触发→PASS) | ✅ 是 |
| TC-6 | 6 Tier2默认关 | 默认不调 embedder、不截 L2 | `topic_shift_gate=False`+`l2_truncated=False`+`gate_sim=None` | ✅ 是(触发轮) |
| TC-7 | 7 Tier2开启(可选) | 长任务截断+短代词豁免 | `gate_sim`浮点+长任务`l2_truncated=True`/短追问`=False` | ✅ 是(需改配置重启) |
| TC-8 | 8 BC 回归 | 单一主题连续对话不受影响 | 三轮连贯不割裂 | ✅ 是 |
| TC-9 | 8 BC 回归 | 普通对话/工具不误注入 user_request | `tool=web_search` 注入零命中 | ✅ 是 |
| TC-10 | 8 BC 回归 | 首条消息(无历史)不报错 | `l2_count_in=0`+`relabel_applied=False`+无异常 | ✅ 是 |
| TC-11 | 9 边界 | sentinel 不被当原话注入(被动观测) | 注入 req_len 全来自真实输入、非 sentinel | 是(随真模拟轮被动观测) |
| TC-12 | 9 边界 | 多窗口共享 default 行为一致 | 两 venue 追问连续+不漂 | ✅ 是 |

**覆盖维度清单**：1 核心漂移修复 happy path（★含连续3次 + 第二主题交叉）/ 2 Tier1 锚定真注入 / 3 Tier1 重定性真注入 / 4 追问连续性不被误伤（★头号负向，短+代词+长延续）/ 5 Fix B 原话直传（含纵深拉回）/ 6 Tier2 默认关回归 / 7 Tier2 开启可选档（截断+豁免）/ 8 BC 回归（单一主题/普通工具/首条无历史）/ 9 边界（sentinel/多窗口）。

---

## 6. 结果汇总（待真机执行回填）

| Case | 范围 | 类型 | 判定 |
|---|---|---|---|
| TC-1 | 核心漂移不漂(★,连续3次) | UI 真测 + log | _待回填_ |
| TC-1b | 第二主题交叉验证 | UI 真测 + log | _待回填_ |
| TC-2 | 锚定注入 | log(硬锚点 anchor_applied) | _待回填_ |
| TC-3 | 重定性注入 | log(硬锚点 relabel_applied) | _待回填_ |
| TC-4 | 短/代词追问不误伤(★) | UI 真测 | _待回填_ |
| TC-4b | 长延续追问不误伤 | UI 真测 | _待回填_ |
| TC-5 | FixB 原话直传 | log | _待回填_ |
| TC-5b | FixB 纵深拉回 | UI 真测 + log | _待回填_ |
| TC-6 | Tier2 默认关回归 | log + UI 真测 | _待回填_ |
| TC-7 | Tier2 开启(可选) | UI 真测 + log | _待回填_ |
| TC-8 | 单一主题连续对话 | UI 真测 | _待回填_ |
| TC-9 | 普通工具不误注入 | UI 真测 + log | _待回填_ |
| TC-10 | 首条无历史不报错 | UI 真测 | _待回填_ |
| TC-11 | sentinel 不注入 | log | _待回填_ |
| TC-12 | 多窗口一致 | UI 真测 | _待回填_ |

> **恢复环境（验完必做）**：若做过 TC-7，把 `default.yaml` 的 `topic_shift_gate` 改回 `false`；确认未遗留任何临时配置 override。
> **结果存档**：截图存 `screenshots/`；执行后日志证据贴回各 case log 证据栏与本汇总表。
