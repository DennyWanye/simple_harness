# BUG-B Phase 1 — 闲聊快路径 allowlist + safe-fail 兜底 手工测试（自包含·单文档可执行）

> **被测功能**：BUG-B 修复 plan **Phase 1（P3 闲聊快路径 + WI-2b safe-fail 兜底）**。
> 把"纯寒暄走 1 次预分析 LLM"优化成**高精度词法 allowlist 短路（0 次 LLM）**，且**真问题绝不被误短路**；
> 预分析 LLM 失败时 **safe-fail 倒向能力侧（chitchat→factual_qa，不短路）**，裸 ReAct 仍答对。
>
> **本次 Phase 1 实际改动文件**（被测目标）：
> - `backend/deskpet/agent/lexicon.py` — 新增共享词法模块 `is_obvious_chitchat(msg)`（整句锚定 + 否决优先，规格 plan §2.2）。
> - `backend/deskpet/agent/intent_triage.py` —
>   - `analyze()` 入口（:191-202）：`is_obvious_chitchat` 命中 → 打 **`intent_triage.allowlist_hit`**（:192）→ 直接产出短路 IntentCard（`problem_type=chitchat`, `short_circuit=True`），**不调 LLM**。
>   - `_safe_card`（:253-268）：WI-2b — `derived_pt=='chitchat'` 兜底改 `factual_qa`（真问题 LLM 挂 + prior='chat' 时不跳过取证）。
>
> **对应 plan**：[plans/2026-06-25-bugb-intent-routing-fix/00-PLAN.md](../../plans/2026-06-25-bugb-intent-routing-fix/00-PLAN.md)（§2 Phase 1 全部、§6 最终验收 TC 表 BUGB-1/2/3/4）。
> **默认配置**（真测 run 头部须记录）= plan §0 表：无任何 bypass env、`analysis_model=deepseek-v4-pro`、`analysis_timeout_s=45.0`、非流式预分析。
> **最后更新**：2026-06-26

---

## 0. 测试前置（HARD — 不满足则结果作废）

### 0.1 环境（必须先满足）

1. **进程清场**：`taskkill /F /IM deskpet.exe` + 杀残留 Vite node（项目坑 #1）。**不要手动起 backend / vite**（坑 #7/#9，Tauri 自管唯一 backend + 唯一 vite）。
2. **跑 worktree / master 当前代码（非 frozen）**：只给 **Tauri 进程**注入 env（坑 #8）：
   - `DESKPET_BACKEND_DIR=G:\projects\deskpet\backend`
   - `DESKPET_PYTHON=G:\projects\deskpet\backend\.venv\Scripts\python.exe`
   - `DESKPET_DEV_MODE=1`
   - `DESKPET_USER_DATA_DIR=G:\projects\deskpet\backend\userdata`
   - `DESKPET_CLOUD_API_KEY=<根目录 .env 里的 tsk_ key>`（relay LLM 链路；见 [`LOCAL-DEV-CREDENTIALS.md`](../../LOCAL-DEV-CREDENTIALS.md)，token 常过期需重登）
   - `NO_PROXY=*`（坑：Clash 7897 掐空闲长连，长耗时 LLM 误判挂起）
   启动后 log **必须**出现 `[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`；
   若见 `[backend_launch] Bundled exe=...` → 跑的是旧 frozen，**测了等于白测，立即停下重配**。
3. **流水线装上**：grep `problem_pipeline_init enabled=true intent=... evidence=... self_check=...`（main.py:**1756**）。grep 不到 → 全部用例无意义（allowlist 短路也走 pipeline）。
4. **日志落盘**：启动命令把 tauri dev 输出重定向到
   `plans/manual-results-2026-06-26-bugb-phase1/tauri-dev.log`，所有 log 证据 grep 这份（backend structlog 走 stderr → Tauri pipe → 落进这份 log，坑 #7）。
5. **截图存盘目录**：`plans/manual-results-2026-06-26-bugb-phase1/screenshots/`（不存在先建）。

### 0.2 windows-mcp 真测纪律（HARD CONSTRAINT — 不可妥协）

> 触发词已命中（"用 windows-mcp 测试" / "真测"）。本纪律强制生效，完整版见 `~/.claude/knowledge-base/windows-mcp-e2e.md`。

1. **真模拟人**：每个 case 必 Screenshot/Snapshot → **真坐标点击 / 真键盘(剪贴板)输入** → 截图验证 → 日志判 PASS/FAIL。
2. **禁绕过（HARD）**：**不允许**用 `ws://127.0.0.1:8100/*` WebSocket 直注、`pytest`、`import` backend 调 `is_obvious_chitchat()`、文件/keychain 存在性、log-only grep **替代**真点击作为 UI 证据。**log grep 只是判定辅助，必须配真截图 + 真 UI 操作链路。**（坑：`import lexicon; is_obvious_chitchat("你好")` 在脚本里跑一遍**不算**本用例 PASS，那是协议层/脚本回放，违反 `feedback_real_e2e_not_script_replay`。）
3. **每个动作前 declare**：`坐标=(x,y) | 动作=click/type/restart | 期望=...`。
4. **截图存盘**：`plans/manual-results-2026-06-26-bugb-phase1/screenshots/<case-id>-NN-*.png`。
5. **失败 retry ≥ 3 次不同 workaround** 才能标「环境受限」；**跳过任何 case** 必须显式声明 + 具体理由 + **等用户确认**。
6. **中文输入 workaround**：windows-mcp `Type`(UIA SetValue) 实测多数可靠；不落则 STA Runspace + `Clipboard.SetText("中文")` + 先 Click 输入框聚焦再 Ctrl+V；**发送**用 `Type(..., press_enter=True)` 或真点「发送」按钮（WebView2 不响应老式 mouse_event）。窗口位置每次重启会漂（window-state 插件），**每次重启后重新 Snapshot 取坐标**。
7. **emoji / 纯标点输入**：emoji 走剪贴板（`Clipboard.SetText("😄😄")` + Ctrl+V）最稳；纯标点 `。。。` 同理。输入框可能把 `~`/全角标点改写，发送前截图核对气泡文字与预期一致再判定。

### 0.3 ★ Phase 1 一票否决项（任一 FAIL = Phase 1 不算完成，回 plan 修）

| Case | 验收点 | 类别 |
|---|---|---|
| **TC-A1** | 闲聊"你好呀" → **`intent_triage.allowlist_hit`** + 短路 + **该轮无 `intent_triage.done`、无 `intent_triage.llm_failed`**（证 **0 次 LLM**，BUGB-3 唯一硬证据） | ★ 闲聊 0 LLM 快路径 |
| **TC-B1** | 真问题"光合作用为什么需要光"（BUGB-1） → **无 `allowlist_hit`** + `intent_triage.done problem_type=factual_qa short_circuit=False` → 流水线真跑 | ★ 真问题不误短路 |
| **TC-B2** | 纯中文 debug"刚那段为什么越界"（BUGB-2） → **无 `allowlist_hit`** + `intent_triage.done problem_type∈{debug,factual_qa} short_circuit=False` | ★ 真问题不误短路 |
| **TC-C5** | 伪装寒暄实为求助"你好，帮我看下这段为什么报错" → **无 `allowlist_hit`** + 走 LLM（`intent_triage.done`/`llm_failed` 至少一条），**不**短路 | ★ allowlist 不被前缀寒暄骗 |
| **TC-D1** | 打挂预分析（analysis_model 设 relay 不存在串）发真问题 → `intent_triage.llm_failed` + safe-fail card **problem_type≠chitchat（factual_qa）** + **不短路** + 裸 ReAct 答对 | ★ safe-fail 不误判闲聊 |

任一 ★ FAIL → Phase 1 未完成。

---

## 1. 真实日志锚点速查（执行时 grep 用，已核实源码行号 2026-06-26）

| 路径 | 锚点字符串 | 代码位置 | 含义 |
|---|---|---|---|
| 装配 | `problem_pipeline_init enabled=true intent=... evidence=... self_check=...` | main.py:1756 | 编排器构造成功（不出 → 全用例无意义）|
| **WI-2 快路径** | `intent_triage.allowlist_hit` (structlog kv，带 `preview=<前40字>`) | intent_triage.py:**192** | **allowlist 命中 → 0 次 LLM 短路（BUGB-3 唯一硬证据）**。命中即早 return，该消息**不会**再出 `intent_triage.done`/`llm_failed` |
| 预分析 | `intent_triage.done problem_type=<...> ambiguity=<f> clarify=<bool> short_circuit=<bool> has_contradiction=<bool>` | intent_triage.py:**242** | **走了 1 次 LLM** 并解析成功（allowlist 未命中的真问题路径）|
| 预分析失败 | `intent_triage.llm_failed error=...`（logger.**warning**）| intent_triage.py:**219** | 预分析 LLM 失败/超时 → safe-fail 降级（TC-D1 硬证据）|
| 预分析失败 | `intent_triage.parse_failed preview=...`（logger.**warning**）| intent_triage.py:**227** | 畸形 JSON → safe-fail（**不**短路）|
| PRE-LOOP 短路 | `pipeline.short_circuit problem_type=<...>` | problem_pipeline.py:**86** | card.short_circuit=True → 编排器整条短路（带点 `.`）|
| PRE-LOOP 短路 | `pipeline_short_circuit sid=<...>` | main.py:**6868** | 闲聊整条短路（下划线 `_`）|
| PRE-LOOP 完成 | `pipeline.pre_loop_done injections=<n> events=<n>` | problem_pipeline.py:**110** | 非闲聊：注入意图(+矛盾) system 后进 IN-LOOP（真问题走这条）|
| 澄清 | `pipeline_clarification_pause sid=<...>` | main.py:**6893** | needs_clarification → 暂停反问（safe-fail/allowlist 短路**不应**出现）|
| 取证(旁证) | `evidence_gathered_set sid=<...> tool=<name>` | agent_loop.py:2423 | 真问题进 IN-LOOP 后命中取证工具（真问题真跑的旁证）|

> ⚠️ **grep 区分两条同形 log（坑）**：`pipeline.short_circuit`（problem_pipeline.py:86，**带点**）与 `pipeline_short_circuit`（main.py:6868，**下划线**）是两条不同 log。正则 grep `pipeline.short_circuit` 时 `.` 会通配同时匹配两条导致计数翻倍 —— **判 allowlist 短路时用字面匹配**：`grep -F 'intent_triage.allowlist_hit'`、`grep -F 'pipeline.short_circuit'`、`grep -F 'pipeline_short_circuit'` 分别计数。
> ⚠️ **structlog kv 渲染**：`intent_triage.allowlist_hit` / `intent_triage.done` / `intent_triage.llm_failed` / `pipeline.short_circuit` 是 structlog 事件，渲染到 stderr 后**事件名可能带 `event=` 前缀**（如 `event='intent_triage.allowlist_hit' preview='你好呀'`）。**grep 一律用子串匹配、不要锚行首**，否则会漏判。
> ⚠️ **0 次 LLM 的判定方式（核心）**：allowlist 命中后 `analyze()` 在 :192 **立即 return**，根本不进 :208 之后的 LLM 调用块。故"0 次 LLM"= **该消息时间窗内出现 `intent_triage.allowlist_hit` 且不出现 `intent_triage.done`、`intent_triage.llm_failed`、`intent_triage.parse_failed`**。三者任一出现 = allowlist 没拦住、走了 LLM = 该 case FAIL（除非是 TC-B/C/D 期望走 LLM 的 case）。
> WS 事件（`chat_v2_intent`）是瞬态广播，判定主依据是 backend log + 截图。

---

## 2. 取坐标 & 通用操作模板（执行时每次重启后重填）

> 桌宠主窗每次重启位置会漂。**每个 case / 每次重启前**先 `Screenshot` 或 `Snapshot` 取这 3 个真坐标：

| 控件 | 占位坐标(重启后实测填) | 用途 |
|---|---|---|
| 对话输入框 | `(in_x, in_y)` | Click 聚焦 + Type 输入 |
| 发送 | `(send_x, send_y)` | 发消息（或 `Type(press_enter=True)`）|
| 新话题/重置 | `(new_x, new_y)` | 清当前会话上下文（不重启进程）|

**单条发送通用步骤**（后文各 case 引用为「发送 "<文本>"」）：
1. `坐标=(in_x,in_y) | 动作=click | 期望=输入框聚焦`
2. `坐标=(in_x,in_y) | 动作=type "<文本>" press_enter=true | 期望=消息气泡出现且文字与预期一致，桌宠开始回复`
3. 截图 → 轮询 log 直到该轮 idle → grep 锚点。
4. **每条 case 之间点「新话题」`(new_x,new_y)` 重置**，避免上一轮上下文串扰（尤其 TC-C 伪装寒暄不能被上一轮真问题上下文带偏）。

---

## 3. 测试用例（TC-A allowlist 命中短路 / TC-B 真问题不短路 / TC-C 边界对抗 / TC-D safe-fail）

> 每条：输入文本 → 操作步骤（declare 坐标|动作|期望）→ 硬证据 grep → PASS/FAIL 判据 → 能逼出的 bug。
> **统一 grep 口径**：用 `grep -F` 字面匹配，并**框定该 case 的发送时间窗**（用截图时间 / wait_idle 前后窗口），避免跨 case 串数。

---

### 类别 A — allowlist 命中 → 0 次 LLM 短路（BUGB-3 核心）

#### TC-A1 ★ 闲聊"你好呀" — 0 LLM allowlist 短路（BUGB-3）

**输入**：`你好呀`

**步骤**
1. 点「新话题」重置，截图 `TC-A1-00-reset.png`。
2. 发送 `你好呀`，截图 `TC-A1-01-send.png`，等回完，截图 `TC-A1-02-reply.png`。

**硬证据 grep**（该轮时间窗内）
- `grep -F 'intent_triage.allowlist_hit'` → **恰 1 次**（应含 `preview='你好呀'`）。
- `grep -F 'pipeline.short_circuit'` → 1 次（problem_type=chitchat）。
- `grep -F 'pipeline_short_circuit'` → 1 次。
- `grep -F 'intent_triage.done'` → **0 次**。
- `grep -F 'intent_triage.llm_failed'` → **0 次**；`grep -F 'intent_triage.parse_failed'` → **0 次**。

**PASS/FAIL 判据**：PASS = allowlist_hit=1 + short_circuit 两条各 1 + **done/llm_failed/parse_failed 全 0**（证 0 次 LLM）+ 桌宠正常轻量回复（截图见回复气泡）。任一 done/llm_failed 出现 = allowlist 没拦、走了 LLM = **★ FAIL**。

**能逼出的 bug**：allowlist 漏判"你好呀"（词根没收"你好呀"或被尾缀吃掉）→ 走 LLM 多花一次调用；或短路 log 缺失（接线断）。

---

#### TC-A2 寒暄变体"晚安" / "谢谢" — allowlist 命中

**输入**（两条，分别发，各重置）：`晚安`、`谢谢`

**步骤**：对每条走通用发送步骤，截图 `TC-A2-01-wanan.png` / `TC-A2-02-xiexie.png`，各等回完。

**硬证据 grep**（每条各自时间窗）
- 每条：`intent_triage.allowlist_hit` 恰 1 次 + `intent_triage.done`/`llm_failed`/`parse_failed` 全 0。

**PASS/FAIL 判据**：PASS = 两条各自 allowlist_hit=1 且 done/llm_failed=0。任一条走了 LLM = FAIL（非 ★，但记录）。

**能逼出的 bug**：词根缺收常用寒暄"晚安/谢谢"。

---

#### TC-A3 寒暄+尾缀"你好呀~" / "晚安啊" / "在吗在吗" — 尾缀/催促仍命中

**输入**（三条，分别发，各重置）：`你好呀~`、`晚安啊`、`在吗在吗`

> 规格点（plan §2.2 / lexicon.py:44-50）：尾缀 `[呀啊哟哦呢嘛吧哈~！!。.，,、\s]*` 可在每个招呼词后重复，整句锚定 `(?:(招呼词)(尾缀))+`，故 `在吗在吗` 靠**整句重复锚定**放行（即便含疑问助词"吗"，否决项已改用问号 `[?？]` 不用裸"吗么"，R2 决策）。

**步骤**：对每条走通用发送步骤，截图 `TC-A3-01..03.png`。

**硬证据 grep**（每条各自时间窗）
- 每条：`intent_triage.allowlist_hit` 恰 1 次 + done/llm_failed/parse_failed 全 0。

**PASS/FAIL 判据**：PASS = 三条各 allowlist_hit=1 且 done=0。`在吗在吗` 若走了 LLM（done 出现）= **否决项误用裸"吗"误杀整句寒暄** = FAIL（这正是 R2 决策要防的回归）。

**能逼出的 bug**：尾缀 `~`/`啊` 没被容许（整句锚定 fullmatch 失败 → 漏放行）；`在吗在吗` 被疑问助词否决误杀。

---

#### TC-A4 纯 emoji "😄😄" / 纯标点"。。。" — 无实意短路

**输入**（两条，分别发，各重置）：`😄😄`、`。。。`

> 规格点（lexicon.py:88-106 `_is_only_emoji_or_punct`）：全由 emoji/标点/符号/空白组成（无字母数字汉字）→ True。emoji 范围含 0x1F300-0x1FAFF / 0x2600-0x27BF。

**步骤**：emoji 用剪贴板输入（§0.2 守则7），截图发送前核对气泡确为 `😄😄`；`。。。` 同理（注意输入框别把全角句号改写成别的）。截图 `TC-A4-01-emoji.png` / `TC-A4-02-dots.png`。

**硬证据 grep**：每条 `intent_triage.allowlist_hit` 恰 1 次 + done/llm_failed=0。

**PASS/FAIL 判据**：PASS = 两条 allowlist_hit=1 且 done=0。emoji/纯标点走了 LLM = `_is_only_emoji_or_punct` 漏判 = FAIL。

**能逼出的 bug**：emoji 码点范围漏（某些 emoji 不在判定区间）；全角标点未被 `unicodedata.category` 归为 P 类。

---

### 类别 B — 真问题不被误短路（BUGB-1 / BUGB-2 核心）

#### TC-B1 ★ "光合作用为什么需要光"（BUGB-1） — 含"为什么"否决，走 LLM

**输入**：`光合作用为什么需要光`

**步骤**：点「新话题」重置；发送，截图 `TC-B1-01-send.png` / `TC-B1-02-reply.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `grep -F 'intent_triage.allowlist_hit'` → **0 次**（"为什么"命中否决 `_VETO_IMPERATIVE`，lexicon.py:58-66）。
- `grep -F 'intent_triage.done'` → 1 次，且该行含 `problem_type=factual_qa` 且 `short_circuit=False`。
- `grep -F 'pipeline.pre_loop_done'` → 出现（进 IN-LOOP，流水线真跑）。
- `grep -F 'pipeline_short_circuit'` → **0 次**。

**PASS/FAIL 判据**：PASS = allowlist_hit=0 + `intent_triage.done problem_type=factual_qa short_circuit=False` + 进 pre_loop_done + 桌宠真答（非"你好"式轻收尾）。出现 allowlist_hit / short_circuit=True = 真问题被误短路 = **★ FAIL**。

**能逼出的 bug**：allowlist 把含"为什么"的真问句误放行短路（否决项漏"为什么"）。

---

#### TC-B2 ★ 纯中文 debug"刚那段为什么越界"（BUGB-2） — debug 不短路

**输入**：`刚那段为什么越界`

**步骤**：点「新话题」重置；发送，截图 `TC-B2-01-send.png` / `TC-B2-02-reply.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `grep -F 'intent_triage.allowlist_hit'` → **0 次**（含"为什么"+故障词"越界"双否决，lexicon.py:69-75 `越界`）。
- `grep -F 'intent_triage.done'` → 1 次，该行 `problem_type` ∈ {debug, factual_qa} 且 `short_circuit=False`。
- `grep -F 'pipeline_short_circuit'` → 0 次。

**PASS/FAIL 判据**：PASS = allowlist_hit=0 + `intent_triage.done problem_type∈{debug,factual_qa} short_circuit=False`。被短路（allowlist_hit≥1 或 short_circuit=True）= **★ FAIL**。

> 说明：deepseek 把"刚那段为什么越界"判 debug 还是 factual_qa 依 LLM，两者皆可（plan §6 BUGB-2 口径）。硬约束是**不短路**。

**能逼出的 bug**：纯中文短句 debug 被当寒暄短路（这正是 BUG-B headline 防回归点）。

---

#### TC-B3 故障词单发"崩了" / "报错" / "卡死" — 不短路

**输入**（三条，分别发，各重置）：`崩了`、`报错`、`卡死`

> 规格点：`_VETO_CODE_FAULT`（lexicon.py:69-75）含 `崩`/`报错`/`卡死`。这三条虽短，但故障词否决 → 不放行短路。

**步骤**：各走通用发送步骤，截图 `TC-B3-01..03.png`。

**硬证据 grep**（每条各自时间窗）
- 每条：`intent_triage.allowlist_hit` = **0 次** + `intent_triage.done`/`llm_failed` 至少一条出现（证走了 LLM，未短路）。
- `pipeline_short_circuit` 这三条窗口内 = 0 次。

**PASS/FAIL 判据**：PASS = 三条 allowlist_hit=0 且未短路。任一条短路 = 故障词否决失效 = FAIL（非 ★，但故障词漏 = headline 风险，重点记录）。

**能逼出的 bug**：故障词否决正则漏字（如"崩了"只收"崩溃"没收"崩"）。

---

### 类别 C — 边界对抗（伪装寒暄实为求助，必须不被前缀骗）

> 核心风险：消息**以寒暄开头**但实为求助/故障。整句锚定 `fullmatch` + 否决优先，理应**不放行**。这是 allowlist "高精度、宁可假阴性"原则的关键防线。

#### TC-C5 ★ "你好，帮我看下这段为什么报错" — 寒暄前缀+祈使+故障，不短路

**输入**：`你好，帮我看下这段为什么报错`

**步骤**：点「新话题」重置；发送（剪贴板中文），截图 `TC-C5-01-send.png` / `TC-C5-02-reply.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `grep -F 'intent_triage.allowlist_hit'` → **0 次**（三重否决：祈使"帮"/"看下" + "为什么" + 故障"报错"；且整句非纯招呼词 fullmatch 失败）。
- `grep -F 'intent_triage.done'` 或 `grep -F 'intent_triage.llm_failed'` → 至少一条出现（走了 LLM）。
- `grep -F 'pipeline_short_circuit'` → 0 次。

**PASS/FAIL 判据**：PASS = allowlist_hit=0 + 走 LLM（done/llm_failed 至少一条）+ 不短路。出现 allowlist_hit = **整句锚定被"你好，"前缀骗、误短路真求助** = **★ FAIL**。

**能逼出的 bug**：用子串匹配而非 `fullmatch`（"你好"出现即放行）；否决优先级低于锚定。

---

#### TC-C6 "谢谢，那这个报错怎么办" — 致谢前缀+故障+疑问，不短路

**输入**：`谢谢，那这个报错怎么办`

**步骤**：重置；发送，截图 `TC-C6-01.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `intent_triage.allowlist_hit` = 0（否决：故障"报错" + 祈使/疑问"怎么"）。
- `intent_triage.done`/`llm_failed` 至少一条出现。

**PASS/FAIL 判据**：PASS = allowlist_hit=0 + 走 LLM。出现 allowlist_hit = FAIL。

**能逼出的 bug**："谢谢"前缀误放行；"怎么办"未被疑问/祈使否决覆盖。

---

#### TC-C7 "在吗？我代码崩了" — 问号+故障，不短路（与 TC-A3 "在吗在吗" 对照）

**输入**：`在吗？我代码崩了`

> 对照点：`在吗在吗`（TC-A3）短路 vs `在吗？我代码崩了`（本条）不短路 —— 验证 R2 决策"否决用问号 `[?？]` 而非裸'吗'"的双向正确：纯催促寒暄放行、含真问号+故障的不放行。

**步骤**：重置；发送（含全角问号 `？`，剪贴板输入，截图核对问号确实在气泡里），截图 `TC-C7-01.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `intent_triage.allowlist_hit` = 0（否决：问号 `？` 命中 `_VETO_QUESTION_MARK` lexicon.py:55 + 故障"崩"/"代码"）。
- `intent_triage.done`/`llm_failed` 至少一条出现。

**PASS/FAIL 判据**：PASS = allowlist_hit=0 + 走 LLM。出现 allowlist_hit = 问号否决失效 = FAIL。

**能逼出的 bug**：全角问号 `？` 未被否决正则覆盖（只收半角 `?`）；"代码"/"崩"故障否决漏。

---

#### TC-C8 "hi 帮我 debug" — 英文寒暄+祈使+code，不短路

**输入**：`hi 帮我 debug`

**步骤**：重置；发送，截图 `TC-C8-01.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `intent_triage.allowlist_hit` = 0（否决：祈使"帮我" + code 词"debug"；整句非纯 hi+尾缀 fullmatch 失败）。
- `intent_triage.done`/`llm_failed` 至少一条出现。

**PASS/FAIL 判据**：PASS = allowlist_hit=0 + 走 LLM。出现 allowlist_hit = FAIL。

**能逼出的 bug**："hi" 招呼词放行时未对其后 "帮我 debug" 触发否决（fullmatch 应整体失败）。

---

#### TC-C9 "你好我想问下钠离子电池原理" — 寒暄黏连真问题，不短路

**输入**：`你好我想问下钠离子电池原理`

> 补充边界：寒暄词后**无标点直接黏连**真问题。整句 fullmatch 必失败（"想问下…电池原理"非招呼词/尾缀），且"问"在祈使范围 → 不放行。

**步骤**：重置；发送，截图 `TC-C9-01.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `intent_triage.allowlist_hit` = 0。
- `intent_triage.done` 出现（problem_type 多半 factual_qa）。

**PASS/FAIL 判据**：PASS = allowlist_hit=0 + 走 LLM + 不短路。出现 allowlist_hit = 黏连真问题被误短路 = FAIL（高优先记录，接近 headline 风险）。

**能逼出的 bug**：fullmatch 锚定不严，把"你好"开头的长句放行。

---

#### TC-C10 "请帮我查一下今天天气" — 纯祈使无寒暄，不短路

**输入**：`请帮我查一下今天天气`

**步骤**：重置；发送，截图 `TC-C10-01.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `intent_triage.allowlist_hit` = 0（祈使"请"/"帮我"/"查"三重命中 `_VETO_IMPERATIVE`）。
- `intent_triage.done`/`llm_failed` 至少一条出现。

**PASS/FAIL 判据**：PASS = allowlist_hit=0 + 走 LLM。出现 allowlist_hit = FAIL。

**能逼出的 bug**：祈使否决漏"查"/"请"。

---

#### TC-C11 "你好" 单发 vs "你好啊在干嘛" — 纯寒暄短路 / 带追问不短路（一对对照）

**输入**（两条，分别发，各重置）：`你好`（应短路）、`你好啊在干嘛`（应不短路）

> 对照点：`你好` 纯招呼 fullmatch 通过 → allowlist_hit；`你好啊在干嘛` 含"干嘛"（疑问/求助语气，"什么"近义但需 §2.2 否决覆盖）。注意：若"在干嘛"未被任何否决项命中且"在"是招呼词，可能误放行 —— 本条专测此潜在假阳性边界。

**步骤**：两条各走通用发送步骤，截图 `TC-C11-01-nihao.png` / `TC-C11-02-ganma.png`。

**硬证据 grep**
- `你好`：allowlist_hit=1，done=0。
- `你好啊在干嘛`：**期望** allowlist_hit=0 + done 出现（走 LLM）。

**PASS/FAIL 判据**：
- `你好` 必须短路（allowlist_hit=1）。
- `你好啊在干嘛`：**最优**为不短路（allowlist_hit=0）。**但**若该串因"在"招呼词 + "干嘛"未入否决表而被放行短路（allowlist_hit=1），此为**已知假阳性边界**——按 plan 原则 5"假阴性安全/假阳性危险"，记录为 **观察项 + 上报 plan**（"干嘛/在干嘛"是否应入否决表），**不单独判 Phase 1 ★ FAIL**，但若桌宠把"你好啊在干嘛"当寒暄轻收尾导致答非所问，截图记录交用户判。

**能逼出的 bug**：闲聊式追问"在干嘛/干嘛"被当纯寒暄短路（allowlist 放行面偏宽）——这是 allowlist 设计边界的真实压测点。

---

### 类别 D — safe-fail 兜底（WI-2b：预分析 LLM 失败不误判闲聊）

#### TC-D1 ★ 打挂预分析 → safe-fail factual_qa 不短路（BUGB-4）

**目的**：预分析 LLM 失败时，`_safe_card` 对 `chitchat` derived_pt 兜底改 `factual_qa`（WI-2b），**不短路**，裸 ReAct 仍答对。验证真问题在 LLM 挂掉时**不会**因坏 classifier 的 prior='chat' 派生 chitchat 而被跳过取证/误短路。

**构造（改 config 打挂预分析，参照 ship 文档 TC-10/BUGB-4 env 法）**
1. 改 dev config `[features.problem_pipeline].analysis_model` 为 relay 不存在的串 `gpt-does-not-exist-zzz`，重启，确认 `problem_pipeline_init enabled=true` 恢复。报告记录"改 config 非源码"。

**输入**：`帮我分析为什么这段排序代码会越界报错`（真 debug/factual 问题，非寒暄 → 必走 LLM 路径，但 LLM 模型不存在 → 触发 llm_failed）

**步骤**
2. 点「新话题」重置；发送上述输入，截图 `TC-D1-01-send.png` / `TC-D1-02-reply.png`，等回完。
3. **测后还原** analysis_model（删 override 或还原 `deepseek-v4-pro`），重启确认 `problem_pipeline_init enabled=true`。

**硬证据 grep**（该轮时间窗）
- `grep -F 'intent_triage.allowlist_hit'` → **0 次**（输入含"帮我/为什么/报错/代码/越界"多重否决，根本不进 allowlist）。
- `grep -F 'intent_triage.llm_failed'` → **≥1 次**（safe-fail 命中，error 含模型不存在）。
- `grep -F 'pipeline_short_circuit'` → **0 次**（safe-fail 绝不短路）。
- `grep -F 'pipeline_clarification_pause'` → **0 次**（safe-fail card 不澄清）。
- 旁证：该轮可能出 `evidence_gathered_set`（safe-fail 对 factual_qa/debug 仍置 needs_investigation=True → 进 IN-LOOP 取证），**不算 FAIL**。
- 桌宠**仍正常回复**（截图见实答，无未捕获 traceback / 无"启动失败"对话框）。

> ⚠️ **判 problem_type=factual_qa 的方式**：safe-fail 走 `_safe_card`，**不打 `intent_triage.done`**（done 仅成功解析才打）。故 problem_type=factual_qa 的硬证据是**间接**的：(a) `llm_failed` 出现 + (b) 不短路（无 `pipeline_short_circuit`）+ (c) 进 IN-LOOP（出 `pipeline.pre_loop_done` 或 `evidence_gathered_set`）。若 safe-fail 误派生 chitchat，则会出 `pipeline_short_circuit` → 由 (b) 直接抓到 FAIL。**强证可选**：若需直证 `_safe_card` 返回 factual_qa，在报告附该轮 `pipeline.pre_loop_done injections=...`（注入了 `<意图> 问题类型：factual_qa`）截断作旁证；不可用 import 调 `_safe_card` 当 UI 证据。

**PASS/FAIL 判据**：PASS = allowlist_hit=0 + llm_failed≥1 + **无 pipeline_short_circuit** + 无 clarification_pause + 桌宠正常回复不卡死 + 进 IN-LOOP（pre_loop_done/evidence_gathered_set 至少一条）。出现 `pipeline_short_circuit`（safe-fail 误判 chitchat 短路）= **★ FAIL**（这正是 WI-2b 修的 §0 取证门漏洞）；或整轮挂死/无回复/traceback = FAIL。

**能逼出的 bug**：`_safe_card` 未把 chitchat→factual_qa（derived_pt 来自 prior='chat' 时硬当 chitchat → needs_investigation=False → 误短路/跳过取证）；预分析失败致整轮挂掉。

---

## 4. 防假绿守则（HARD）

1. **log grep 只是辅助，必须配真截图 + 真 UI 点击/输入。** 单跑 `python -c "from deskpet.agent.lexicon import is_obvious_chitchat; print(is_obvious_chitchat('你好呀'))"` **不算**任何 case 的 PASS —— 那是脚本回放内部函数，违反 `feedback_real_e2e_not_script_replay`。每个 case 必须：真坐标点击输入框 → 真键盘/剪贴板输入文本 → 真发送 → 截图气泡 → 等桌宠回完 → 再 grep tauri-dev.log。
2. **WebSocket 直注禁止**：不允许 `ws://127.0.0.1:8100/*` 直发 chat 帧绕过 UI。必须经桌宠输入框真模拟人。
3. **`pipeline.short_circuit`（点）vs `pipeline_short_circuit`（下划线）正则坑**：grep 短路计数一律用 `grep -F`（字面）。用 `grep 'pipeline.short_circuit'`（无 `-F`）时 `.` 通配会把 `pipeline_short_circuit` 也匹进来导致双计 → 误以为短路了 2 次。**判 allowlist 短路三件套**（allowlist_hit / pipeline.short_circuit / pipeline_short_circuit）全部 `grep -F` 分别数。
4. **"0 次 LLM"必须用"无 done/llm_failed/parse_failed"证，不能只看"有 allowlist_hit"**：理论上代码 :192 命中即 return，但真测要双向确认 —— allowlist_hit 出现 **且** 三条 LLM 路径 log 全 0，才算 0 LLM。只看到 allowlist_hit 不去查 done=0，可能漏掉"既打了 allowlist_hit 又因别的 bug 走了 LLM"的回归。
5. **时间窗框定**：多 case 连测时，每个 grep 必须框该 case 的发送-回完时间窗（按截图时间戳 / wait_idle 边界切 log），否则跨 case 串数（如把 TC-A1 的 allowlist_hit 算进 TC-B1 窗口）。建议每 case 发送前在 log 里打一个可识别分隔（或记录行号区间）。
6. **真问题判定看 `short_circuit=False` 而非只看"有回复"**：桌宠对短路闲聊也会回复，"有回复"不区分短路与否。真问题 case（B/C）的硬约束是 **allowlist_hit=0 且 short_circuit 两条 log 均 0**，不是"桌宠答了"。
7. **跑的是当前代码非 frozen**：每次 run 头部确认 `[backend_launch] Dev python=...`，否则 allowlist 改动不在被测进程里（坑 #8），测了白测。
8. **emoji/全角标点输入核对**：TC-A4/C7 含 emoji/全角问号，输入框可能改写或丢字符，发送前必须截图核对气泡文字与预期**逐字一致**再判定，否则测的是别的输入。

---

## 5. 结果汇总表（执行后填）

| case | ★ | 输入 | 坐标 | 截图 | log 证据（关键计数）| 判定 |
|---|---|---|---|---|---|---|
| TC-A1 | ★ | 你好呀 | | | allowlist_hit ___(=1)、done ___(=0)、llm_failed ___(=0) | |
| TC-A2 | | 晚安/谢谢 | | | 各 allowlist_hit ___(=1)、done ___(=0) | |
| TC-A3 | | 你好呀~/晚安啊/在吗在吗 | | | 各 allowlist_hit ___(=1)、done ___(=0) | |
| TC-A4 | | 😄😄/。。。 | | | 各 allowlist_hit ___(=1)、done ___(=0) | |
| TC-B1 | ★ | 光合作用为什么需要光 | | | allowlist_hit ___(=0)、done problem_type=___(factual_qa) sc=___(False) | |
| TC-B2 | ★ | 刚那段为什么越界 | | | allowlist_hit ___(=0)、done pt=___∈{debug,factual_qa} sc=False | |
| TC-B3 | | 崩了/报错/卡死 | | | 各 allowlist_hit ___(=0)、未短路 | |
| TC-C5 | ★ | 你好，帮我看下这段为什么报错 | | | allowlist_hit ___(=0)、走 LLM ___ | |
| TC-C6 | | 谢谢，那这个报错怎么办 | | | allowlist_hit ___(=0) | |
| TC-C7 | | 在吗？我代码崩了 | | | allowlist_hit ___(=0)（问号否决）| |
| TC-C8 | | hi 帮我 debug | | | allowlist_hit ___(=0) | |
| TC-C9 | | 你好我想问下钠离子电池原理 | | | allowlist_hit ___(=0) | |
| TC-C10 | | 请帮我查一下今天天气 | | | allowlist_hit ___(=0) | |
| TC-C11 | | 你好 / 你好啊在干嘛 | | | 你好 hit=1；你好啊在干嘛 hit=___(观察项) | |
| TC-D1 | ★ | 帮我分析为什么这段排序代码会越界报错（坏 analysis_model）| | | allowlist_hit=0、llm_failed ___(≥1)、pipeline_short_circuit ___(=0)、仍回复 ___ | |

---

## 6. Phase 1 通过线

- **★ 必过（任一 FAIL = Phase 1 不算完成，回 plan 修）**：**TC-A1 / TC-B1 / TC-B2 / TC-C5 / TC-D1**。
- 非 ★ allowlist 命中类（TC-A2/A3/A4）FAIL = 词根/尾缀/emoji 漏判，多走一次 LLM（成本回归），按严重度修。
- 非 ★ 真问题/边界类（TC-B3/C6~C10）FAIL = **接近 headline 风险**（真问题被误短路），优先级仅次于 ★，必须修或上报。
- TC-C11 "你好啊在干嘛" 假阳性为**观察项**，记录实测 + 上报 plan 决定是否收窄 allowlist，不单独阻断 Phase 1。
- 需改 config 重启的用例：**TC-D1**（坏 analysis_model，测后还原 `deepseek-v4-pro` 重启确认）。
- best-effort / env-limited 的 case 须**显式标注理由 + 实测值**，不允许伪造触发；失败 case retry ≥3 次不同 workaround 才标"环境受限"。
- 全绿（或 ★ 全 PASS + 非 ★ 受控标注清楚）后：证据存 `plans/manual-results-2026-06-26-bugb-phase1/screenshots/`，本目录只放用例定义；按 plan §5 进入"子代理评估 100% → windows-mcp 真测"循环。

---

## 7. 汇总

| 项 | 值 |
|---|---|
| 用例总数 | **15**（TC-A1~A4 命中短路 4 + TC-B1~B3 真问题不短路 3 + TC-C5~C11 边界对抗 7 + TC-D1 safe-fail 1）|
| ★ 必过 | **5**（TC-A1 闲聊 0 LLM allowlist 短路 / TC-B1 真问题不短路 / TC-B2 中文 debug 不短路 / TC-C5 伪装寒暄不被骗 / TC-D1 safe-fail 不误判闲聊）|
| 边界 case 数 | **≥12**（A3 尾缀×3 + A4 emoji/标点×2 + B3 故障词×3 + C5~C11 伪装/黏连/对照×8，远超要求的 12）|
| 是否需 windows-mcp 真测 | **是**（每例真坐标点击 + 真中文/剪贴板输入 + 截图 + tauri-dev.log grep；禁 WS 直注 / pytest / import `is_obvious_chitchat` 当 UI 证据）|
| 需改 config 重启的用例 | TC-D1（坏 analysis_model，测后还原）|
| 覆盖的 plan 验收点 | BUGB-1（TC-B1）/ BUGB-2（TC-B2）/ BUGB-3（TC-A1）/ BUGB-4（TC-D1）全覆盖 |
