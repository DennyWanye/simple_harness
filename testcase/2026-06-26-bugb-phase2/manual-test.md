# BUG-B Phase 2 — 复活组装期 classifier 手工测试（自包含·单文档可执行）

> **被测功能**：BUG-B 修复 plan **Phase 2（P2 组装质量 — 复活组装期 classifier llm 层 + 词法地板 fail-closed）**。
> 把"组装期 classifier 恒 `llm_registry=None` → 跳 llm tier → embed 撞锁 fallback 无脑 `default='chat'`"修成：
> **（WI-5）注入现成 shim 复活 classifier llm tier**（实际跑 relay 主模型，timeout 调到 8s 适配 thinking）+
> **（WI-6/WI-7）所有上游 tier 都不命中时落"词法地板"**（确定寒暄→`chat`；有 code 信号→`code`；否则倒向能力侧→`task`），
> **真 code/debug 问题不再被组装成 `chat` bundle**（拿对 persona/工具/skill），闲聊仍 `chat`。
>
> **本次 Phase 2 实际改动文件**（被测目标）：
> - `backend/main.py`（:2001-2014）— WI-5：注入 `OpenAICompatibleAgentLLM(provider=local_llm or cloud_llm)` 给 `build_default_assembler(llm_registry=..., llm_timeout_s=8.0)`；注入成功打 **`assembler_classifier_llm_injected provider=...`**（:2007，logger.info）；注入失败打 **`assembler_classifier_llm_inject_failed: ... — fallback lexical floor`**（:2009，logger.warning，降级词法地板不崩 boot）。
> - `backend/deskpet/agent/assembler/__init__.py`（:84-135）— `build_default_assembler` 新增 `llm_timeout_s` 形参（默认 6.0，main.py 传 8.0），透传给 `TaskClassifier(llm_timeout_s=...)`，放宽 classifier llm tier 默认 2.0s 超时（shim 忽略 model 实跑 relay 主模型 thinking 4-6s，2s 必超时=接了等于没接）。
> - `backend/deskpet/agent/assembler/classifier.py`（:243-287）— WI-6/WI-7：`classify()` 末尾原 `default='chat'` 改 **`_lexical_floor(user_message)`**：`is_obvious_chitchat`→`chat`（path=default, rationale="lexical floor: obvious chitchat"）；`has_code_signal`→`code`（rationale="lexical floor: code signal → capability side"）；否则→`task`（rationale="lexical floor: fail-closed to capability"）。复用 Phase 1 同款共享词法模块 `deskpet/agent/lexicon.py`（避免双词表，plan 原则 3）。
>
> **对应 plan**：[plans/2026-06-25-bugb-intent-routing-fix/00-PLAN.md](../../plans/2026-06-25-bugb-intent-routing-fix/00-PLAN.md)（§3 Phase 2 全部 WI-5/6/7、§6 最终验收 TC 表 **BUGB-6**）。
> **默认配置**（真测 run 头部须记录）= plan §0 表：无任何 bypass env、`analysis_model=deepseek-v4-pro`、`analysis_timeout_s=45.0`、非流式预分析、组装期 classifier llm tier **已注入**（`assembler_classifier_llm_injected` 应在 boot log 出现）。
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
   - `DESKPET_CLOUD_API_KEY=<根目录 .env 里的 tsk_ key>`（relay LLM 链路；见 [`LOCAL-DEV-CREDENTIALS.md`](../../LOCAL-DEV-CREDENTIALS.md)，token 常过期需重登。**Phase 2 强依赖真 relay**：classifier llm tier 要真打 relay 主模型，token 过期 → llm tier 全失败 → 全落词法地板，B 类用例测不出 path=llm，务必先确认链路通）
   - `NO_PROXY=*`（坑：Clash 7897 掐空闲长连，长耗时 LLM 误判挂起）
   启动后 log **必须**出现 `[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`；
   若见 `[backend_launch] Bundled exe=...` → 跑的是旧 frozen，**测了等于白测，立即停下重配**。
3. **classifier llm tier 已复活（Phase 2 招牌前置，grep 不到 = 全用例无意义）**：
   - grep `assembler_classifier_llm_injected provider=...`（main.py:**2007**）→ **必须出现**（证 WI-5 注入成功，llm tier 真接电）。
   - grep `assembler_classifier_llm_inject_failed`（main.py:**2009**）→ **必须不出现**（出现 = 注入失败已降级词法地板，B 类 path=llm 测不到，记 ★E 同时失败）。
4. **流水线装上**：grep `problem_pipeline_init enabled=true intent=... evidence=... self_check=...`（main.py:**1756**）。grep 不到 → 组装/流水线链路异常。
5. **日志落盘**：启动命令把 tauri dev 输出重定向到
   `plans/manual-results-2026-06-26-bugb-phase2/tauri-dev.log`，所有 log 证据 grep 这份（backend structlog 走 stderr → Tauri pipe → 落进这份 log，坑 #7）。
6. **截图存盘目录**：`plans/manual-results-2026-06-26-bugb-phase2/screenshots/`（不存在先建）。

### 0.2 windows-mcp 真测纪律（HARD CONSTRAINT — 不可妥协）

> 触发词已命中（"用 windows-mcp 测试" / "真测"）。本纪律强制生效，完整版见 `~/.claude/knowledge-base/windows-mcp-e2e.md`。

1. **真模拟人**：每个 case 必 Screenshot/Snapshot → **真坐标点击 / 真键盘(剪贴板)输入** → 截图验证 → 日志判 PASS/FAIL。
2. **禁绕过（HARD）**：**不允许**用 `ws://127.0.0.1:8100/*` WebSocket 直注、`pytest`、`import` backend 调 `TaskClassifier.classify()` / `is_obvious_chitchat()` / `has_code_signal()`、文件/keychain 存在性、log-only grep **替代**真点击作为 UI 证据。**log grep 只是判定辅助，必须配真截图 + 真 UI 操作链路。**（坑：`python -c "from deskpet.agent.assembler.classifier import TaskClassifier; ..."` 在脚本里跑一遍 classify **不算**本用例 PASS，那是协议层/脚本回放，违反 `feedback_real_e2e_not_script_replay`。组装期 classifier 必须由桌宠真发消息触发 `assemble()` → 真打 `assembler_task_classified` log。）
3. **每个动作前 declare**：`坐标=(x,y) | 动作=click/type/restart | 期望=...`。
4. **截图存盘**：`plans/manual-results-2026-06-26-bugb-phase2/screenshots/<case-id>-NN-*.png`。
5. **失败 retry ≥ 3 次不同 workaround** 才能标「环境受限」；**跳过任何 case** 必须显式声明 + 具体理由 + **等用户确认**。
6. **中文输入 workaround**：windows-mcp `Type`(UIA SetValue) 实测多数可靠；不落则 STA Runspace + `Clipboard.SetText("中文")` + 先 Click 输入框聚焦再 Ctrl+V；**发送**用 `Type(..., press_enter=True)` 或真点「发送」按钮（WebView2 不响应老式 mouse_event）。窗口位置每次重启会漂（window-state 插件），**每次重启后重新 Snapshot 取坐标**。

### 0.3 ★ Phase 2 一票否决项（任一 FAIL = Phase 2 不算完成，回 plan 修）

| Case | 验收点 | 类别 |
|---|---|---|
| **TC-A1** | 真 code"帮我看这段 python 为什么 IndexError"（BUGB-6） → 组装 **`assembler_task_classified task_type=code`**（非 chat），证 classifier 复活（原恒 chat）| ★ 真 code → 组装 task_type 非 chat（BUGB-6 招牌）|
| **TC-A2** | 真 debug"修一下这个 bug 它老是崩" → `assembler_task_classified task_type∈{code}`（rule/floor 任一路径），**非 chat** | ★ 真 debug 不被组装成 chat |
| **TC-E1** | boot 自检：`assembler_classifier_llm_injected` 出现 + **无 `assembler_classifier_llm_inject_failed`** + 无 boot 崩溃 + 桌宠正常起 | ★ classifier 复活接电成功（B 类前提）|

任一 ★ FAIL → Phase 2 未完成。

---

## 1. 真实日志锚点速查（执行时 grep 用，已核实源码行号 2026-06-26）

| 路径 | 锚点字符串 | 代码位置 | 含义 |
|---|---|---|---|
| **WI-5 注入成功** | `assembler_classifier_llm_injected provider=<类名>` | main.py:**2007**（logger.info，`%s` 渲染）| **classifier llm tier 已接电**（boot 期出 1 次；不出 = 全用例无意义，TC-E1 ★FAIL）|
| **WI-5 注入失败** | `assembler_classifier_llm_inject_failed: <err> — fallback lexical floor` | main.py:**2009**（logger.warning）| 注入异常已降级词法地板（出现 = TC-E1 ★FAIL，且 B 类 path=llm 测不到）|
| **组装 task_type 硬证据** | `assembler_task_classified task_type=<...> must=[...] prefer=[...]` | assembler.py:**251**（structlog kv）| **每轮组装 bundle 都打 1 次**；`task_type=` 字段 = BUGB-6 唯一硬证据（非 chat = classifier 复活/词法地板倒向能力侧生效）|
| classifier embed 失败 | `classifier.query_embed_failed error=...`（warning）| classifier.py:**300** | embed tier query 撞锁/超时 → 跳过 embed（fallback 链路触发点之一）|
| classifier embed 失败 | `classifier.exemplar_embed_failed error=...`（warning）| classifier.py:**169** | exemplar 向量化失败 → embed tier 整体跳过 |
| classifier llm 超时 | `classifier.llm_timeout timeout_s=8.0`（warning）| classifier.py:**355** | llm tier 超 8s（thinking 太久）→ 落词法地板（不回 chat）|
| classifier llm 失败 | `classifier.llm_failed error=...`（warning）| classifier.py:**360** | llm tier 调用异常（relay 502/token 失效等）→ 落词法地板 |
| classifier llm 畸形 | `classifier.llm_unknown_task_type content=...`（warning）| classifier.py:**384** | llm 返回非 8 类 task_type 词 → 落词法地板（不回 chat）|
| 流水线装配 | `problem_pipeline_init enabled=true intent=... evidence=... self_check=...` | main.py:**1756** | 编排器构造成功 |
| 装配启动确认 | `[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend` | rust 侧 | 跑当前代码非 frozen（坑 #8）|

> ⚠️ **核心判定锚点 = `assembler_task_classified` 的 `task_type=` 字段**。这是组装 bundle 的硬证据，**与 Phase 1 的 `intent_triage.*` 是两个不同判定器**：
> - `intent_triage.*`（intent_triage.py）决定**短路/流水线**（Phase 1 测过：allowlist_hit / done / short_circuit）。
> - `assembler_task_classified`（assembler.py:251）决定**组装 bundle**（persona/工具/skill），其 `task_type` 是 **Phase 2/BUGB-6 唯一硬证据**。
> 二者**同一轮消息都会各打各的 log**，互不替代。BUGB-6 看的是后者 `task_type≠chat`。
> ⚠️ **classifier 自身不为"选中的 path"（rule/embed/llm/default）单打一条 log**——`path` 只在 `ClassifierResult` 对象里。**判 path 来源靠组合证据**：
>   - **path=rule**：输入命中 `_RULE_PATTERNS`（含 `python`/`报错`/`debug`/`修.{0,12}bug`/`代码` 等），**该轮无任何 `classifier.*` warning** 且 `task_type=code`（rule 在最前，命中即返回，不进 embed/llm）。
>   - **path=llm**：该轮 embed 未命中（出 `classifier.query_embed_failed` 或 embed 静默跳过）+ **无 `classifier.llm_timeout`/`classifier.llm_failed`/`classifier.llm_unknown_task_type`**（即 llm tier 真出了合法结果）+ `task_type` 为非 default 判定。
>   - **path=default（词法地板）**：该轮出现 `classifier.llm_timeout` 或 `classifier.llm_failed` 或 `classifier.llm_unknown_task_type`（llm tier 没出结果）→ 落 `_lexical_floor`；此时 `task_type` 必为 `chat`(寒暄)/`code`(有 code 信号)/`task`(其余)三者之一，**绝不是别的**。
> ⚠️ **structlog kv 渲染**：`assembler_task_classified` / `classifier.*` 是 structlog 事件，渲染到 stderr 后**事件名可能带 `event=` 前缀**（如 `event='assembler_task_classified' task_type='code' ...`）。**grep 一律用子串匹配、不要锚行首**。`assembler_classifier_llm_injected` 是 logger.info `%s` 风格（非 structlog kv），grep 子串 `assembler_classifier_llm_injected`。

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
4. **每条 case 之间点「新话题」`(new_x,new_y)` 重置**，避免上一轮上下文串扰（尤其 D 类闲聊不能被上一轮 code 上下文带偏，组装 classifier 吃当前 user_message）。

---

## 3. 测试用例（A 真 code→非 chat / B llm tier 真跑 / D 闲聊仍 chat / E 不回归）

> 每条：输入文本 → 操作步骤（declare 坐标|动作|期望）→ 硬证据 grep → PASS/FAIL 判据 → 能逼出的 bug。
> **统一 grep 口径**：用 `grep -F` 字面匹配，并**框定该 case 的发送时间窗**（用截图时间 / wait_idle 前后窗口），避免跨 case 串数。
> **核心硬锚点**：每条 case 都 grep `assembler_task_classified` 取该轮 `task_type=` 值，这是 Phase 2 的唯一硬证据。

---

### 类别 A — 真 code/debug 问题 → 组装 task_type 非 chat（BUGB-6 核心）

#### TC-A1 ★ "帮我看这段 python 为什么 IndexError"（BUGB-6） — 组装 task_type=code

**输入**：`帮我看这段 python 为什么 IndexError`

**步骤**
1. 点「新话题」重置，截图 `TC-A1-00-reset.png`。
2. 发送 `帮我看这段 python 为什么 IndexError`，截图 `TC-A1-01-send.png`，等回完，截图 `TC-A1-02-reply.png`。

**硬证据 grep**（该轮时间窗内）
- `grep -F 'assembler_task_classified'` → **恰 1 次**，且该行含 **`task_type=code`**（rule tier 命中 `python` 关键字 → path=rule → `code`；即便 rule 没命中也会因 `has_code_signal('python'/'IndexError')` 落词法地板 `code`，双保险）。
- `grep -F 'task_type=chat'` 在该轮窗口 → **0 次**（不得被组装成 chat bundle）。
- 旁证（非必须）：该轮 `classifier.llm_*` warning 多半不出现（rule 命中即返回，不进 llm）。

**PASS/FAIL 判据**：PASS = 该轮 `assembler_task_classified task_type=code`（非 chat）+ 桌宠真给 code/debug 向回复（解释 IndexError 原因/给排查方向，非"你好"式轻收尾）。出现 `task_type=chat` = **classifier 没复活/词法地板没倒向能力侧 = ★ FAIL**（这正是 BUG-B P2 缺陷：真 code 拿 chat persona/工具/skill bundle）。

**能逼出的 bug**：classifier 仍 `default='chat'`（WI-6 没接）；rule tier code 正则漏 `python`；`has_code_signal` 漏 `IndexError`/`python`。

---

#### TC-A2 ★ "修一下这个 bug 它老是崩" — 真 debug 组装 task_type=code（非 chat）

**输入**：`修一下这个 bug 它老是崩`

**步骤**：点「新话题」重置；发送，截图 `TC-A2-01-send.png` / `TC-A2-02-reply.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `grep -F 'assembler_task_classified'` → 1 次，含 **`task_type=code`**（rule 命中 `修.{0,12}bug` → `code`；或 `has_code_signal('bug'/'崩')` 词法地板 → `code`）。
- `grep -F 'task_type=chat'` 该轮 → **0 次**。

**PASS/FAIL 判据**：PASS = `task_type=code`（非 chat）。出现 `task_type=chat` = **★ FAIL**（真 debug 被组装成 chat）。

**能逼出的 bug**：rule 正则 `修.{0,12}bug` 中文间隔超界没命中且 `has_code_signal` 漏"崩"/"bug" → 落 `task`（虽非 chat 仍算倒向能力侧通过，但若落 chat 则 FAIL）。

> 说明：`修一下这个 bug 它老是崩` 落 `code`（命中 bug/崩）为最优；若 rule 未命中、embed 未命中、llm tier 判 `task`，亦**非 chat**，按 BUGB-6 口径（"非 chat 即组装质量已修"）仍 PASS，但记录实测 task_type。**硬约束是 task_type≠chat**。

---

#### TC-A3 中性真问题"钠离子电池工作原理" — 非寒暄非 code，倒向能力侧（非 chat）

**输入**：`钠离子电池工作原理`

> 设计意图：这条**既不命中 rule code 正则、又非寒暄、又无 code 信号**，是 plan §3 WI-6 "倒向能力侧"的关键压测：词法地板对它应判 `task`（fail-closed），而非 `chat`。若 embed/llm tier 命中，则走 path=embed/llm 出别的合理类型（也可能 web_search），**只要不是 chat 即可**。

**步骤**：点「新话题」重置；发送，截图 `TC-A3-01-send.png` / `TC-A3-02-reply.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `grep -F 'assembler_task_classified'` → 1 次，记录该行 `task_type=` 实际值。
- 判 path（组合证据）：
  - 若该轮无 `classifier.llm_*` warning 且 `task_type` 非 code → 多半 path=llm 真出结果或 path=default 词法地板 `task`。
  - 若该轮出 `classifier.llm_timeout`/`classifier.llm_failed` → path=default 词法地板，**此时 task_type 必为 `task`**（非寒暄非 code → fail-closed task）。

**PASS/FAIL 判据**：PASS = `task_type≠chat`（期望 `task` 或 llm 判的合理类型如 `web_search`/`task`）。出现 `task_type=chat` = **词法地板对中性真问题 fail-open 成 chat = FAIL**（非 ★，但这是 WI-6 "不做天花板只做地板/不无脑 chat"的核心，重点记录）。

**能逼出的 bug**：`_lexical_floor` 的 else 分支没倒向 `task` 而回了 `chat`；llm tier 把中性问题判 chat（属 llm 判断，记录但 task_type≠chat 仍 PASS）。

---

### 类别 B — classifier llm tier 真跑（WI-5 复活验证）

> 目的：证 llm tier 真接电（path=llm 真出结果），且超时/失败时落词法地板**不回 chat**（WI-7）。
> ⚠️ B 类强依赖 relay 链路通（§0.1.2 token 未过期 + §0.1.3 `assembler_classifier_llm_injected` 已出）。链路不通 → 全落词法地板，B 类降级为"观察 llm tier fallback 是否落对地板"。

#### TC-B1 中性问句触发 llm tier — 出 path=llm 非 default

**输入**：`光合作用的暗反应在哪里进行`

> 设计意图：这条非寒暄（无招呼词）、非 code（无 code 信号）、rule 不命中（不含 python/报错/搜索/计划/情绪词），embed 多半也 <0.75 → **真把球传到 llm tier**。验证 WI-5 注入后 llm tier 真跑出一个非 default 判定（factual→多半 `chat` 或 `task`，依 relay 主模型）。

**步骤**：点「新话题」重置；发送，截图 `TC-B1-01-send.png` / `TC-B1-02-reply.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `grep -F 'assembler_task_classified'` → 1 次，记录 `task_type=`。
- `grep -F 'classifier.llm_timeout'` / `grep -F 'classifier.llm_failed'` / `grep -F 'classifier.llm_unknown_task_type'` → **理想为 0 次**（llm tier 真出合法结果 = path=llm）。
- 若上述三条任一出现 → llm tier 没出结果，落了词法地板（该输入非寒暄非 code → 应得 `task`）。

**PASS/FAIL 判据**：
- **最优（证 WI-5 真复活）**：三条 `classifier.llm_*` warning 全 0 + `assembler_task_classified` 出一个合法 task_type（path=llm 真跑）。记 PASS（llm tier 复活）。
- **次优（llm 不稳但 fail-closed 对）**：出 `classifier.llm_timeout`/`llm_failed` + `task_type=task`（非寒暄非 code 落地板 task，不回 chat）。记 PASS-with-note（llm tier 接了但 relay 不稳，地板兜底正确）。
- **FAIL**：`task_type=chat` 且该输入非寒暄（光合暗反应非闲聊）→ 词法地板/llm 误判 chat = FAIL（接近 BUGB-6 风险）。

**能逼出的 bug**：llm tier 注入了但 timeout 太短（2s 没改成 8s）→ 恒 `llm_timeout` → 永远落地板（WI-5 R2 命门没修）；地板 else 回 chat 而非 task。

---

#### TC-B2 llm tier 真跑不踩 BUG-A（stream+json_schema）观察 — 桌宠不卡死

**输入**：`帮我规划一下下周的复习日程`

> 设计意图：rule tier 命中 `计划|规划|安排.{0,3}(行程|任务|日程)` → path=rule → `plan`（不进 llm）。本条主要验证 **rule tier 命中类不被 WI-5 注入破坏**（注入 llm tier 不应让 rule 命中的轮次反而变慢/出错），且组装 task_type 正确为 `plan`（非 chat）。同时作为 BUG-A 旁证：classifier llm tier 用 `chat_with_fallback(max_tokens=32)` 非 json_schema（plan §3.1 R2 命门），rule 命中轮根本不进 llm，更不踩 BUG-A。

**步骤**：点「新话题」重置；发送，截图 `TC-B2-01.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `grep -F 'assembler_task_classified'` → 1 次，含 `task_type=plan`（rule 命中规划/日程）。
- `grep -F 'task_type=chat'` 该轮 → 0 次。
- 桌宠正常回复（出日程/计划向内容），无卡死/无 traceback。

**PASS/FAIL 判据**：PASS = `task_type=plan`（非 chat）+ 桌宠正常回复不卡死。出现 `task_type=chat` = rule plan 正则没接/被 WI-5 改动破坏 = FAIL；桌宠卡死/traceback = WI-5 注入破坏组装链路 = FAIL（重点记录，接近 R-Phase2 timeout/BUG-A 风险）。

**能逼出的 bug**：WI-5 注入 shim 后 rule 命中轮意外进 llm 卡住；classifier llm tier 踩 relay stream+json_schema BUG-A 致整组装挂起。

---

### 类别 D — 闲聊仍 chat（词法地板 chitchat 分支 + 与 Phase 1 协同不打架）

> 关键说明：闲聊"你好"会被 **Phase 1 allowlist 短路**（`intent_triage.allowlist_hit`，决定流水线短路），但**组装仍会跑**（classifier 仍分类组装 bundle）。故 D 类用 `assembler_task_classified task_type=chat` 看组装侧，同时旁证 Phase 1 `allowlist_hit` 仍在（两层协同）。

#### TC-D1 闲聊"你好" — 组装 task_type=chat + Phase 1 allowlist 短路仍生效

**输入**：`你好`

**步骤**：点「新话题」重置；发送，截图 `TC-D1-01-send.png` / `TC-D1-02-reply.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `grep -F 'assembler_task_classified'` → 1 次，含 **`task_type=chat`**（`is_obvious_chitchat('你好')=True` → 词法地板 `chat`，或 embed/llm 判 chat；闲聊本就该 chat）。
- 旁证（Phase 1 协同）：`grep -F 'intent_triage.allowlist_hit'` → 1 次（Phase 1 短路仍生效，与组装 classifier 不打架）。
- 桌宠轻量寒暄回复（截图见回复气泡）。

**PASS/FAIL 判据**：PASS = `assembler_task_classified task_type=chat` + `intent_triage.allowlist_hit` 出现（两层协同：流水线短路 + 组装 chat bundle）。`task_type≠chat`（如被误判 code/task）= 闲聊拿了错误能力 bundle = FAIL（词法地板 chitchat 分支没接 / 否决项误伤"你好"）；`allowlist_hit` 消失 = Phase 1 被 Phase 2 改动回归破坏 = FAIL。

**能逼出的 bug**：`_lexical_floor` 的 `is_obvious_chitchat` 分支没生效（闲聊落 task）；Phase 2 改动意外破坏 Phase 1 短路链路（两层耦合回归）。

---

#### TC-D2 闲聊变体"晚安啦" — 组装 task_type=chat

**输入**：`晚安啦`

**步骤**：点「新话题」重置；发送，截图 `TC-D2-01.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `grep -F 'assembler_task_classified'` → 1 次，含 `task_type=chat`。
- 旁证：`grep -F 'intent_triage.allowlist_hit'` → 1 次（"晚安啦" 整句锚定命中 Phase 1 allowlist）。

**PASS/FAIL 判据**：PASS = `task_type=chat` + 桌宠轻量回复。`task_type≠chat` = FAIL（非 ★，记录）。

**能逼出的 bug**：寒暄尾缀"啦"未被 `is_obvious_chitchat` 整句锚定容许 → 词法地板落 task；闲聊拿错 bundle。

---

### 类别 E — 不回归（boot 接电 + 普通对话无崩溃）

#### TC-E1 ★ boot 自检：classifier llm 注入成功 + 无 inject_failed + 无 boot 崩溃

**目的**：验证 WI-5 注入在真 boot 链路成功接电（B 类前提），且注入失败兜底（main.py:2008-2010 try/except）不会崩 boot。

**步骤**
1. 按 §0.1 完整起 Tauri（注入 env）；桌宠主界面正常出现，截图 `TC-E1-01-boot.png`。
2. grep boot 段 log（启动后 30s 内窗口）。

**硬证据 grep**（boot 时间窗）
- `grep -F 'assembler_classifier_llm_injected'` → **≥1 次**（含 `provider=<类名>`，如 `provider=LocalLLM` 或具体 provider 类名）。
- `grep -F 'assembler_classifier_llm_inject_failed'` → **0 次**。
- `grep -F 'problem_pipeline_init enabled=true'` → 出现（编排器构造成功）。
- `grep -F '[backend_launch] Dev python='` → 出现（跑当前代码非 frozen）。
- 桌宠主界面正常显示，无"启动失败"对话框，无未捕获 traceback。

**PASS/FAIL 判据**：PASS = `assembler_classifier_llm_injected` 出现 + `assembler_classifier_llm_inject_failed` 不出现 + pipeline_init 出现 + 桌宠正常起。`inject_failed` 出现 = WI-5 注入异常已降级（llm tier 没接电，B 类 path=llm 测不到）= **★ FAIL**；boot 崩溃/启动失败对话框 = **★ FAIL**。

**能逼出的 bug**：shim import 路径错（`agent.tool_use_shim` vs `deskpet.agent.tool_use_shim`）；`local_llm`/`cloud_llm` 均 None 致注入跳过（provider 缺）；WI-5 注入抛异常未被 try/except 兜住致崩 boot。

---

#### TC-E2 普通连续对话 3 轮无回归 — 桌宠正常应答，每轮组装 task_type 合理

**输入**（三条，连发不重置，模拟正常使用）：`今天天气不错`、`帮我写一首关于秋天的小诗`、`谢谢你`

**步骤**：依次发送（不点新话题，保留上下文），每条等回完，截图 `TC-E2-01..03.png`。

**硬证据 grep**（每条各自时间窗）
- 每条均出 `assembler_task_classified`（组装链路每轮真跑）：
  - `今天天气不错` → `task_type` 多半 `chat`（闲聊式陈述）或 llm 判定，记录实测。
  - `帮我写一首关于秋天的小诗` → `task_type` 多半 `task`（rule 命中 `写` 在祈使但 rule code 正则需 `写.{0,4}代码` 不命中；落 llm/地板 → `task`），**非 chat**。
  - `谢谢你` → `task_type=chat`（寒暄）+ `intent_triage.allowlist_hit`。
- 三轮桌宠均正常应答，无 `assembler_classifier_llm_inject_failed`（运行期不应再冒注入失败）、无 traceback、无卡死。

**PASS/FAIL 判据**：PASS = 三轮均正常应答 + 每轮 `assembler_task_classified` 出现且 task_type 合理（写诗这条非 chat）+ 全程无 inject_failed/traceback。任一轮卡死/崩溃/写诗被判 chat 导致答非所问 = FAIL（记录截图交用户判）。

**能逼出的 bug**：WI-5 注入致运行期组装偶发抛错；多轮上下文下 classifier 吃 history 误判（应只吃当前 user_message）；"写诗"落 chat 拿不到 task bundle。

---

## 4. 防假绿守则（HARD）

1. **log grep 只是辅助，必须配真截图 + 真 UI 点击/输入。** 单跑 `python -c "from deskpet.agent.assembler.classifier import TaskClassifier; print(await c.classify('帮我看 python IndexError'))"` **不算**任何 case 的 PASS —— 那是脚本回放内部函数，违反 `feedback_real_e2e_not_script_replay`。每个 case 必须：真坐标点击输入框 → 真键盘/剪贴板输入文本 → 真发送 → 截图气泡 → 等桌宠回完 → 再 grep tauri-dev.log 的 `assembler_task_classified`。
2. **WebSocket 直注禁止**：不允许 `ws://127.0.0.1:8100/*` 直发 chat 帧绕过 UI。必须经桌宠输入框真模拟人。
3. **`assembler_task_classified` 是唯一组装硬证据，不能用 `intent_triage.*` 替代**：Phase 2 测的是**组装 bundle 的 task_type**（assembler.py:251），不是 intent_triage 的 problem_type。两个判定器同轮各打各的 log。判 BUGB-6 只看 `assembler_task_classified task_type=`，**不要**拿 `intent_triage.done problem_type=` 充数（那是 Phase 1 的判定器）。
4. **判 path 来源用组合证据，不能臆断**：classifier 不为 path 单打 log。要说"走了 llm tier"，必须有"该轮无 `classifier.llm_*` warning + 非 rule 命中输入"的组合；要说"落了词法地板"，必须有 `classifier.llm_timeout`/`llm_failed`/`llm_unknown_task_type` 其一出现。**不能因为 `task_type=task` 就断定走了地板**（也可能是 llm 真判 task）。
5. **时间窗框定**：多 case 连测时，每个 grep 必须框该 case 的发送-回完时间窗（按截图时间戳 / wait_idle 边界切 log），否则跨 case 串数（如把 TC-A1 的 `assembler_task_classified` 算进 TC-D1 窗口）。建议每 case 发送前在 log 里记录行号区间。
6. **真 code 判定看 `task_type≠chat` 而非只看"有回复"**：桌宠对 chat bundle 也会回复，"有回复"不区分组装质量。A 类 case 的硬约束是 **`assembler_task_classified task_type=code`（或至少非 chat）**，不是"桌宠答了 code 内容"（chat persona 也可能蒙对答案）。
7. **跑的是当前代码非 frozen**：每次 run 头部确认 `[backend_launch] Dev python=...` + `assembler_classifier_llm_injected`，否则 WI-5/6 改动不在被测进程里（坑 #8 + 注入未接），测了白测。
8. **B 类 path=llm 的诚实标注**：relay token 过期/不稳时 llm tier 全失败 → 全落词法地板，path=llm **测不到**。此时 B 类按"fail-closed 是否落对地板"判（非寒暄非 code → task），并**显式标注"relay 链路不稳，llm tier 真跑未观测到，降级测地板兜底"**，不伪造 path=llm 的 PASS。

---

## 5. 结果汇总表（执行后填）

| case | ★ | 输入 | 坐标 | 截图 | log 证据（关键：assembler_task_classified task_type=）| 判定 |
|---|---|---|---|---|---|---|
| TC-E1 | ★ | (boot 自检) | | | classifier_llm_injected ___(出现)、inject_failed ___(=0)、pipeline_init ___(出现) | |
| TC-A1 | ★ | 帮我看这段 python 为什么 IndexError | | | assembler_task_classified task_type=___(code)、task_type=chat ___(=0) | |
| TC-A2 | ★ | 修一下这个 bug 它老是崩 | | | task_type=___(code，至少非 chat) | |
| TC-A3 | | 钠离子电池工作原理 | | | task_type=___(非 chat，期望 task)、classifier.llm_* ___ | |
| TC-B1 | | 光合作用的暗反应在哪里进行 | | | task_type=___、classifier.llm_timeout/failed ___(理想=0 证 path=llm) | |
| TC-B2 | | 帮我规划一下下周的复习日程 | | | task_type=___(plan)、不卡死 ___ | |
| TC-D1 | | 你好 | | | task_type=___(chat)、intent_triage.allowlist_hit ___(出现，Phase1 协同) | |
| TC-D2 | | 晚安啦 | | | task_type=___(chat)、allowlist_hit ___(出现) | |
| TC-E2 | | 今天天气不错/写秋天小诗/谢谢你 | | | 各 task_type ___（写诗非 chat）、inject_failed ___(=0)、无 traceback | |

---

## 6. Phase 2 通过线

- **★ 必过（任一 FAIL = Phase 2 不算完成，回 plan 修）**：**TC-E1（classifier 复活接电）/ TC-A1（真 code→task_type=code，BUGB-6 招牌）/ TC-A2（真 debug→非 chat）**。
- 非 ★ 倒向能力侧类（TC-A3/B1）FAIL = 词法地板 else 没倒向 task 或 llm 误判 chat → 中性真问题拿 chat bundle，**接近 BUGB-6 风险**，优先级仅次于 ★，必须修或上报。
- 非 ★ rule/闲聊协同类（TC-B2/D1/D2）FAIL = rule plan 正则断 / Phase 1 短路被 Phase 2 改动回归破坏，按严重度修。
- TC-E2 任一轮崩溃/卡死/写诗被判 chat = 记录截图交用户判（WI-5 注入运行期稳定性 + 组装质量）。
- **B 类 path=llm 依赖 relay 链路**：token 过期/relay 不稳致 llm tier 全失败时，B 类降级为"地板兜底是否落对"，**显式标注 env-limited + 实测值**，不伪造 path=llm PASS；retry ≥3 次不同 workaround（重登 token / 换稳定窗口 / 确认 NO_PROXY）才标"环境受限"。
- 需改 config 重启的用例：**无**（Phase 2 全部默认配置真测；TC-E1 只需正常 boot 确认注入）。
- 全绿（或 ★ 全 PASS + 非 ★ 受控标注清楚）后：证据存 `plans/manual-results-2026-06-26-bugb-phase2/screenshots/`，本目录只放用例定义；按 plan §5 进入"子代理评估 100% → windows-mcp 真测"循环。

---

## 7. 汇总

| 项 | 值 |
|---|---|
| 用例总数 | **9**（TC-E1 boot 接电 + TC-A1~A3 真 code/中性倒向能力侧 3 + TC-B1~B2 llm tier 真跑 2 + TC-D1~D2 闲聊仍 chat 2 + TC-E2 不回归 1）|
| ★ 必过 | **3**（TC-E1 classifier 复活接电 / TC-A1 真 code→task_type=code BUGB-6 招牌 / TC-A2 真 debug→非 chat）|
| 覆盖的 bug/边界类别 | **A 真 code/debug→组装 task_type≠chat**（BUGB-6 招牌，classifier 复活/词法地板倒向能力侧）·**A3 中性真问题 fail-closed→task**（WI-6 不无脑 chat）·**B llm tier 真跑**（WI-5 复活 path=llm + timeout 8s 适配 thinking + 不踩 BUG-A）·**B fail-closed 兜底**（llm 超时/失败落词法地板非 chat，WI-7）·**D 闲聊仍 chat + 两层判定器协同**（组装 chat bundle + Phase 1 allowlist 短路不打架）·**E 不回归**（boot 注入接电成功无崩溃 + 运行期无 inject_failed/traceback）|
| 是否需 windows-mcp 真测 | **是**（每例真坐标点击 + 真中文/剪贴板输入 + 截图 + tauri-dev.log grep `assembler_task_classified` task_type；禁 WS 直注 / pytest / import classify 当 UI 证据）|
| 需改 config 重启的用例 | **无**（全默认配置真测）|
| 覆盖的 plan 验收点 | **BUGB-6**（TC-A1 招牌 + TC-A2/A3 扩展）+ WI-5（TC-E1/B1/B2）+ WI-6 词法地板（TC-A1/A2/A3/D1/D2）+ WI-7 fail-closed 兜底（TC-A3/B1）|
| 两个判定器区分 | `intent_triage.*`(Phase 1，决定短路/流水线) vs `assembler_task_classified`(Phase 2，决定组装 bundle)；BUGB-6 只看后者 task_type≠chat |
</content>
</invoke>
