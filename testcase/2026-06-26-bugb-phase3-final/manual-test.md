# BUG-B Phase 3（单一意图来源收口）+ 最终验收（plan §6 全功能）手工测试（自包含·单文档可执行）

> **被测功能**：BUG-B 修复 plan **Phase 3（WI-9b 去 hint 预分析 prompt + WI-8a 单一来源护栏注释）** 的**无回归验证**，**合并** plan **§6 最终验收全功能真机人工测试**（BUGB-1/2/3/4/6 全部 ★）。
> 通过本文档 = **BUG-B plan 整体收官、可上线**。
>
> **Phase 3 定位（关键）**：Phase 3 是**内部重构**，**无新用户可见行为**：
> - **WI-9b**（已修，`intent_triage.py` `_PRE_ANALYSIS_SYSTEM`）：预分析 prompt **去掉"+ 系统已判定的初步任务类型"** 文案，与 Y-light 去 hint 行为对齐（prompt 不再把坏 classifier 的 prior task_type 当 hint 喂给 deepseek，deepseek **裸判**）。
> - **WI-8a**（已修，`intent_triage.py` `_TASKTYPE_TO_PROBLEM` 桥）：加单一来源**护栏注释**，钉死"本桥仅 safe-fail fallback、IntentTriage LLM 裸判才是唯一权威意图来源、禁止重新接回正常路径当 hint（防 BUG-C 复活）"。**纯注释，零运行时行为改变**。
> - 全量收口（删 classifier llm 层 + 调换 assemble/triage 次序）已**触发降级标 deferred**（plan §4 决策，>3 文件 + 与 Phase 2 复活的 classifier 冲突）。
>
> 故本文档 = **Phase 3 无回归验证（WI-9b 去 hint 后 deepseek 裸判不退化）** + **plan §6 最终全功能验收** 合一。
>
> **本次 Phase 3 实际改动文件**（被测目标，均 `backend/deskpet/agent/intent_triage.py`）：
> - `_PRE_ANALYSIS_SYSTEM` prompt 文本：删"+ 系统已判定的初步任务类型"（WI-9b 去 hint 文案漂移修正）。
> - `_TASKTYPE_TO_PROBLEM` 桥附近：新增单一来源护栏注释（WI-8a，纯注释）。
>
> **对应 plan**：[plans/2026-06-25-bugb-intent-routing-fix/00-PLAN.md](../../plans/2026-06-25-bugb-intent-routing-fix/00-PLAN.md)（§4 Phase 3 WI-9b/WI-8a + **§6 最终验收 TC 表 BUGB-1/2/3/4/6 全部**）。
> **默认配置**（真测 run 头部须记录）= plan §0 表：**无任何 bypass env**、`analysis_model=deepseek-v4-pro`、`analysis_timeout_s=45.0`、**非流式预分析**、组装期 classifier llm tier **已注入**（`assembler_classifier_llm_injected` 应在 boot log 出现，Phase 2 落地）。
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
   - `DESKPET_CLOUD_API_KEY=<根目录 .env 里的 tsk_ key>`（relay LLM 链路；见 [`LOCAL-DEV-CREDENTIALS.md`](../../LOCAL-DEV-CREDENTIALS.md)，token 常过期需重登。**本文档强依赖真 relay**：BUGB-1/2/6 要真打 deepseek 预分析 + classifier llm tier，token 过期 → 预分析全 safe-fail、classifier 全落地板，★ 用例测不出真判定，务必先确认链路通）
   - `NO_PROXY=*`（坑：Clash 7897 掐空闲长连，长耗时 LLM 误判挂起）
   启动后 log **必须**出现 `[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`；
   若见 `[backend_launch] Bundled exe=...` → 跑的是旧 frozen，**测了等于白测，立即停下重配**。
3. **预分析默认模型确认（HARD，Phase 3 + §6 默认配置硬前提）**：
   - 确认 dev config `[features.problem_pipeline].analysis_model="deepseek-v4-pro"`（plan §0：config.py:436 默认值；若 dev config.toml 有 override 须为 deepseek-v4-pro，**绝不能**残留上轮 BUGB-4 的坏串 `gpt-does-not-exist-zzz`）。
   - **无任何 bypass env**：`DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT` / `DESKPET_DISABLE_CLARIFICATION` **不设**（plan §0：已退役，0 读取点；确认 env 里没残留）。
4. **流水线 + classifier 双装上（grep 不到 = 全用例无意义）**：
   - grep `problem_pipeline_init enabled=true intent=... evidence=... self_check=...`（main.py:**1756**）→ 编排器构造成功。
   - grep `assembler_classifier_llm_injected provider=...`（main.py:**2007**）→ **必须出现**（Phase 2 classifier llm tier 已接电，BUGB-6 前提）。
   - grep `assembler_classifier_llm_inject_failed`（main.py:**2009**）→ **必须不出现**（出现 = classifier 注入失败已降级地板，BUGB-6 path=llm 测不到）。
5. **日志落盘**：启动命令把 tauri dev 输出重定向到
   `plans/manual-results-2026-06-26-bugb-final/tauri-dev.log`，所有 log 证据 grep 这份（backend structlog 走 stderr → Tauri pipe → 落进这份 log，坑 #7）。
6. **截图存盘目录**：`plans/manual-results-2026-06-26-bugb-final/screenshots/`（不存在先建）。

### 0.2 windows-mcp 真测纪律（HARD CONSTRAINT — 不可妥协）

> 触发词已命中（"用 windows-mcp 测试" / "真测" / "真 E2E"）。本纪律强制生效，完整版见 `~/.claude/knowledge-base/windows-mcp-e2e.md`。

1. **真模拟人**：每个 case 必 Screenshot/Snapshot → **真坐标点击 / 真键盘(剪贴板)输入** → 截图验证 → 日志判 PASS/FAIL。
2. **禁绕过（HARD）**：**不允许**用 `ws://127.0.0.1:8100/*` WebSocket 直注、`pytest`、`import` backend 调 `IntentTriage.analyze()` / `TaskClassifier.classify()` / `is_obvious_chitchat()`、文件/keychain 存在性、`cmdkey /list`、log-only grep **替代**真点击作为 UI 证据。**log grep 只是判定辅助，必须配真截图 + 真 UI 操作链路。**（坑：`python -c "from deskpet.agent.intent_triage import IntentTriage; ..."` 在脚本里跑一遍 **不算**任何 case PASS，那是协议层/脚本回放，违反 `feedback_real_e2e_not_script_replay`。两个判定器都必须由桌宠真发消息触发，真打 `intent_triage.*` + `assembler_task_classified` log。）
3. **每个动作前 declare**：`坐标=(x,y) | 动作=click/type/restart | 期望=...`。
4. **截图存盘**：`plans/manual-results-2026-06-26-bugb-final/screenshots/<case-id>-NN-*.png`。
5. **失败 retry ≥ 3 次不同 workaround** 才能标「环境受限」；**跳过任何 case** 必须显式声明 + 具体理由 + **等用户确认**。
6. **中文输入 workaround**：windows-mcp `Type`(UIA SetValue) 实测多数可靠；不落则 STA Runspace + `Clipboard.SetText("中文")` + 先 Click 输入框聚焦再 Ctrl+V；**发送**用 `Type(..., press_enter=True)` 或真点「发送」按钮（WebView2 不响应老式 mouse_event）。窗口位置每次重启会漂（window-state 插件），**每次重启后重新 Snapshot 取坐标**。
7. **BUGB-4 改 config 用例隔离**：BUGB-4 要改坏 `analysis_model` 重启，**必须排在所有默认配置用例之后**，且**测后还原 deepseek-v4-pro 重启**确认 `problem_pipeline_init enabled=true`，否则污染后续。

### 0.3 ★ 一票否决项（plan §6 五条全 ★，任一 FAIL = BUG-B plan 不算收官，回 plan 修）

| Case | 验收点 | 类别 | plan §6 |
|---|---|---|---|
| **TC-1** | 真问题"光合作用为什么需要光" → **`intent_triage.done problem_type=factual_qa short_circuit=False`**（非 safe-fail）+ 无短路 + 流水线真跑（`pipeline.pre_loop_done`）| ★ 真问题不误短路 + 去 hint 裸判正确 | BUGB-1 |
| **TC-2** | 纯中文 debug"我这段Python代码列表越界为什么报IndexError" → **`intent_triage.done problem_type∈{debug,factual_qa} short_circuit=False`** | ★ 中文 debug 不误短路 + 去 hint 裸判正确 | BUGB-2 |
| **TC-3** | 闲聊"你好呀" → **`intent_triage.allowlist_hit`** + 短路 + **该轮无 `intent_triage.done`/`llm_failed`**（证 0 次 LLM）| ★ 闲聊 0 LLM 快路径 | BUGB-3 |
| **TC-4** | 打挂预分析（坏 analysis_model）+ 真问题 → **`intent_triage.llm_failed`** + **无 `pipeline_short_circuit`** + 桌宠裸 ReAct 仍答 | ★ safe-fail 不误短路（去 hint 后桥仅 fallback）| BUGB-4 |
| **TC-6** | 真 code"帮我看这段 python 为何 IndexError" → **`assembler_task_classified task_type=code`**（非 chat，classifier 复活）+ 流水线真跑 | ★ 真 code → 组装 task_type 非 chat | BUGB-6 |

任一 ★ FAIL → BUG-B plan 未收官。

> **Phase 3 无回归判定融入 ★**：WI-9b 去掉 prompt 里的 hint 后，deepseek **裸判** problem_type 是否仍正确，由 **TC-1（factual_qa）+ TC-2（debug/factual_qa）** 直接钉死——若去 hint 致 deepseek 退化（如把"光合作用为什么需要光"判错类型、或误短路），TC-1/TC-2 立刻 FAIL。WI-8a 是纯注释，无运行时行为，由"无回归"整体覆盖（不新增专测，但 §3.6 列无回归专项确认清单）。

---

## 1. 真实日志锚点速查（执行时 grep 用，前两阶段已核实复用，2026-06-26）

> **本文档涉及两个不同判定器，必须分清**（防假绿守则 §4.3）：
> - **`intent_triage.*`**（intent_triage.py）决定**短路/流水线**（BUGB-1/2/3/4 看这个）。**Phase 3 WI-9b 改的就是这里的 prompt**。
> - **`assembler_task_classified`**（assembler.py:251）决定**组装 bundle**（persona/工具/skill，BUGB-6 看这个）。
> - 二者**同一轮消息各打各的 log，互不替代**。

| 判定器 | 锚点字符串 | 代码位置 | 含义 |
|---|---|---|---|
| 装配 | `problem_pipeline_init enabled=true intent=... evidence=... self_check=...` | main.py:1756 | 编排器构造成功（不出 → 全用例无意义）|
| 装配 | `assembler_classifier_llm_injected provider=...` | main.py:2007 | classifier llm tier 已接电（BUGB-6 前提，不出 → BUGB-6 测不到 path=llm）|
| 装配 | `[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend` | rust 侧 | 跑当前代码非 frozen（坑 #8）|
| **intent · 快路径** | `intent_triage.allowlist_hit`（structlog kv，带 `preview=<前40字>`）| intent_triage.py:**192** | **allowlist 命中 → 0 次 LLM 短路（BUGB-3 唯一硬证据）**；命中即早 return，该消息**不再**出 `done`/`llm_failed` |
| **intent · 预分析成功** | `intent_triage.done problem_type=<...> ambiguity=<f> clarify=<bool> short_circuit=<bool> has_contradiction=<bool>` | intent_triage.py:**242** | **走了 1 次 LLM 并解析成功**（allowlist 未命中的真问题路径；BUGB-1/2 硬证据，去 hint 后裸判结果）|
| **intent · 预分析失败** | `intent_triage.llm_failed error=...`（logger.**warning**）| intent_triage.py:**219** | 预分析 LLM 失败/超时 → safe-fail 降级（BUGB-4 硬证据）|
| **intent · 解析失败** | `intent_triage.parse_failed preview=...`（logger.**warning**）| intent_triage.py:**227** | 畸形 JSON → safe-fail（**不**短路）|
| **intent · 短路(点)** | `pipeline.short_circuit problem_type=<...>` | problem_pipeline.py:**86** | card.short_circuit=True → 编排器整条短路（**带点 `.`**）|
| **intent · 短路(下划线)** | `pipeline_short_circuit sid=<...>` | main.py:**6868** | 闲聊整条短路（**下划线 `_`**）|
| **intent · 流水线真跑** | `pipeline.pre_loop_done injections=<n> events=<n>` | problem_pipeline.py:**110** | 非闲聊注入意图 system 后进 IN-LOOP（真问题走这条，BUGB-1/2 流水线真跑旁证）|
| intent · 澄清 | `pipeline_clarification_pause sid=<...>` | main.py:**6893** | needs_clarification → 暂停反问（safe-fail/allowlist 短路**不应**出现）|
| intent · 取证 | `evidence_gathered_set sid=<...> tool=<name>` | agent_loop.py:2423 | 真问题进 IN-LOOP 后命中取证工具（真问题真跑旁证）|
| **assembler · 组装硬证据** | `assembler_task_classified task_type=<...> must=[...] prefer=[...]` | assembler.py:**251** | **每轮组装 bundle 打 1 次**；`task_type=` = **BUGB-6 唯一硬证据**（非 chat = classifier 复活生效）|
| assembler · llm 超时 | `classifier.llm_timeout timeout_s=8.0`（warning）| classifier.py:355 | llm tier 超 8s → 落词法地板（不回 chat）|
| assembler · llm 失败 | `classifier.llm_failed error=...`（warning）| classifier.py:360 | llm tier 异常 → 落词法地板 |
| assembler · llm 畸形 | `classifier.llm_unknown_task_type content=...`（warning）| classifier.py:384 | llm 返回非 8 类词 → 落词法地板（不回 chat）|

> ⚠️ **grep 区分两条同形短路 log（坑）**：`pipeline.short_circuit`（problem_pipeline.py:86，**带点**）与 `pipeline_short_circuit`（main.py:6868，**下划线**）是两条不同 log。正则 grep `pipeline.short_circuit` 时 `.` 会通配同时匹配两条导致计数翻倍 —— **一律 `grep -F` 字面分别计数**：`grep -F 'intent_triage.allowlist_hit'`、`grep -F 'pipeline.short_circuit'`、`grep -F 'pipeline_short_circuit'`、`grep -F 'assembler_task_classified'`。
> ⚠️ **structlog kv 渲染**：上述 structlog 事件渲染到 stderr 后**事件名可能带 `event=` 前缀**（如 `event='intent_triage.done' problem_type='factual_qa' ...`）。**grep 一律用子串匹配、不要锚行首**，否则会漏判。`assembler_classifier_llm_injected` 是 logger.info `%s` 风格（非 structlog kv），同样 grep 子串。
> ⚠️ **0 次 LLM 的判定方式（BUGB-3 核心）**：allowlist 命中后 `analyze()` 在 :192 **立即 return**，不进 LLM 调用块。故"0 次 LLM"= **该消息时间窗内出现 `intent_triage.allowlist_hit` 且不出现 `intent_triage.done`/`intent_triage.llm_failed`/`intent_triage.parse_failed`**。三者任一出现 = 走了 LLM = BUGB-3 FAIL。
> ⚠️ **判 BUGB-6 只看 `assembler_task_classified task_type=`，不能拿 `intent_triage.done problem_type=` 充数**（那是另一个判定器，决定的是短路/流水线不是组装 bundle）。
> WS 事件（`chat_v2_intent`）是瞬态广播，判定主依据是 backend log + 截图。

---

## 2. 取坐标 & 通用操作模板（执行时每次重启后重填）

> 桌宠主窗每次重启位置会漂。**每个 case / 每次重启前**先 `Screenshot` 或 `Snapshot` 取这 3 个真坐标。
> **两种 composer 形态都给坐标**（消息窗 detached 浮窗 vs 内联在桌宠主体下方）——实测以当前布局为准，取不到内联就切 detached 消息窗：

| 控件 | detached 消息浮窗坐标(实测填) | 内联 composer 坐标(实测填) | 用途 |
|---|---|---|---|
| 对话输入框 | `(in_x, in_y)` | `(in_x2, in_y2)` | Click 聚焦 + Type 输入 |
| 发送 | `(send_x, send_y)` | `(send_x2, send_y2)` | 发消息（或 `Type(press_enter=True)`）|
| 新话题/重置 | `(new_x, new_y)` | `(new_x2, new_y2)` | 清当前会话上下文（不重启进程）|

**单条发送通用步骤**（后文各 case 引用为「发送 "<文本>"」）：
1. `坐标=(in_x,in_y) | 动作=click | 期望=输入框聚焦`
2. `坐标=(in_x,in_y) | 动作=type "<文本>" press_enter=true | 期望=消息气泡出现且文字与预期一致，桌宠开始回复`
3. 截图 → 轮询 log 直到该轮 idle → grep 锚点。
4. **每条 case 之间点「新话题」`(new_x,new_y)` 重置**，避免上一轮上下文串扰（尤其 TC-2 中文 debug 不能被上一轮带偏，组装 classifier 吃当前 user_message）。

---

## 3. 测试用例（plan §6：BUGB-1/2/3/4/6 + Phase 3 无回归专项）

> 每条：输入文本 → 操作步骤（declare 坐标|动作|期望）→ 硬证据 grep → PASS/FAIL 判据 → 能逼出的 bug。
> **统一 grep 口径**：用 `grep -F` 字面匹配，并**框定该 case 的发送时间窗**（用截图时间 / wait_idle 前后窗口），避免跨 case 串数。

---

### TC-1 ★ BUGB-1 "光合作用为什么需要光" — factual_qa 不短路 + 去 hint 裸判正确

**输入**：`光合作用为什么需要光`

**步骤**
1. 点「新话题」重置，截图 `TC-1-00-reset.png`。
2. 发送 `光合作用为什么需要光`，截图 `TC-1-01-send.png`，等回完，截图 `TC-1-02-reply.png`。

**硬证据 grep**（该轮时间窗内）
- `grep -F 'intent_triage.allowlist_hit'` → **0 次**（"为什么"命中否决，不进 allowlist）。
- `grep -F 'intent_triage.done'` → **1 次**，且该行含 **`problem_type=factual_qa`** 且 **`short_circuit=False`**。
- `grep -F 'pipeline.pre_loop_done'` → 出现（进 IN-LOOP，流水线真跑）。
- `grep -F 'pipeline_short_circuit'` → **0 次**；`grep -F 'pipeline.short_circuit'` → **0 次**。
- 旁证：`grep -F 'assembler_task_classified'` → 1 次（组装链路每轮真跑；task_type 不作硬约束，记录实测）。

**PASS/FAIL 判据**：PASS = allowlist_hit=0 + `intent_triage.done problem_type=factual_qa short_circuit=False`（**非 safe-fail**，done 是成功解析才打）+ 进 pre_loop_done + 短路两条 log 均 0 + 桌宠真答（讲光合作用原理，非"你好"式轻收尾）。出现 allowlist_hit / short_circuit=True = 真问题被误短路 = **★ FAIL**；problem_type 不是 factual_qa（如被去 hint 后裸判成 chitchat/code）= 去 hint 致 deepseek 退化 = **★ FAIL**。

**能逼出的 bug**：WI-9b 去掉 prompt hint 后 deepseek 裸判退化（把 factual 判错类型/误短路）；allowlist 把含"为什么"的真问句误放行。

---

### TC-2 ★ BUGB-2 "我这段Python代码列表越界为什么报IndexError" — 中文 debug 不短路 + 去 hint 裸判正确

**输入**：`我这段Python代码列表越界为什么报IndexError`

**步骤**：点「新话题」重置，截图 `TC-2-00-reset.png`；发送（剪贴板中文+英混合），截图 `TC-2-01-send.png` / `TC-2-02-reply.png`，等回完。

**硬证据 grep**（该轮时间窗）
- `grep -F 'intent_triage.allowlist_hit'` → **0 次**（含"为什么"+故障词"报错/越界/代码/IndexError"多重否决，不进 allowlist）。
- `grep -F 'intent_triage.done'` → **1 次**，该行 `problem_type` ∈ {debug, factual_qa} 且 **`short_circuit=False`**。
- `grep -F 'pipeline_short_circuit'` → **0 次**；`grep -F 'pipeline.short_circuit'` → **0 次**。
- 旁证：`grep -F 'pipeline.pre_loop_done'` → 出现（流水线真跑）。

**PASS/FAIL 判据**：PASS = allowlist_hit=0 + `intent_triage.done problem_type∈{debug,factual_qa} short_circuit=False`。被短路（allowlist_hit≥1 或 short_circuit=True）= **★ FAIL**（这正是 BUG-B headline 防回归点）；problem_type 落 chitchat 或其它 = 去 hint 裸判退化 = **★ FAIL**。

> 说明：deepseek 把本句判 debug 还是 factual_qa 依 LLM，两者皆可（plan §6 BUGB-2 口径）。硬约束是**不短路** + **不是 chitchat**。

**能逼出的 bug**：纯中文短句 debug 被当寒暄短路（BUG-B headline 回归点）；去 hint 后 deepseek 对中文 debug 裸判退化成 chitchat。

---

### TC-3 ★ BUGB-3 闲聊"你好呀" — 0 LLM allowlist 短路

**输入**：`你好呀`

**步骤**
1. 点「新话题」重置，截图 `TC-3-00-reset.png`。
2. 发送 `你好呀`，截图 `TC-3-01-send.png`，等回完，截图 `TC-3-02-reply.png`。

**硬证据 grep**（该轮时间窗内）
- `grep -F 'intent_triage.allowlist_hit'` → **恰 1 次**（应含 `preview='你好呀'`）。
- `grep -F 'pipeline.short_circuit'` → 1 次（problem_type=chitchat）。
- `grep -F 'pipeline_short_circuit'` → 1 次。
- `grep -F 'intent_triage.done'` → **0 次**。
- `grep -F 'intent_triage.llm_failed'` → **0 次**；`grep -F 'intent_triage.parse_failed'` → **0 次**。

**PASS/FAIL 判据**：PASS = allowlist_hit=1 + short_circuit 两条各 1 + **done/llm_failed/parse_failed 全 0**（证 **0 次 LLM**）+ 桌宠正常轻量回复。任一 done/llm_failed/parse_failed 出现 = allowlist 没拦、走了 LLM = **★ FAIL**。

**能逼出的 bug**：allowlist 漏判"你好呀"（词根没收"你好呀"或被尾缀吃掉）→ 走 LLM 多花一次调用；短路 log 缺失（接线断）。

---

### TC-4 ★ BUGB-4 打挂预分析 → llm_failed 不短路 + 裸 ReAct 仍答

**目的**：预分析 LLM 失败时，`_safe_card` 倒向能力侧（chitchat→factual_qa，WI-2b）**不短路**，裸 ReAct 仍答对。**Phase 3 关联**：WI-8a 护栏注释钉死 `_TASKTYPE_TO_PROBLEM` 桥**仅 safe-fail fallback**——本 case 正是走该桥的唯一合法路径，验证桥的 fallback 语义未被 Phase 3 改坏（去 hint 后桥不被重新接回正常路径当 hint）。

> ⚠️ **本 case 改 config，必须排在所有默认配置用例（TC-1/2/3/6 + 无回归专项）之后**，测后还原。

**构造（改 config 打挂预分析）**
1. 改 dev config `[features.problem_pipeline].analysis_model` 为 relay 不存在的串 `gpt-does-not-exist-zzz`，重启，确认 `problem_pipeline_init enabled=true` 恢复。报告记录"改 config 非源码"。`坐标=N/A | 动作=editconfig+restart | 期望=pipeline_init 出现`。

**输入**：`帮我分析为什么这段排序代码会越界报错`（真 debug/factual 问题，非寒暄 → 必走 LLM 路径，但模型不存在 → 触发 llm_failed）

**步骤**
2. 点「新话题」重置；发送上述输入，截图 `TC-4-01-send.png` / `TC-4-02-reply.png`，等回完。
3. **测后还原** analysis_model（删 override 或还原 `deepseek-v4-pro`），重启确认 `problem_pipeline_init enabled=true`。`坐标=N/A | 动作=editconfig+restart | 期望=pipeline_init 出现`，截图 `TC-4-03-restored.png`。

**硬证据 grep**（该轮时间窗）
- `grep -F 'intent_triage.allowlist_hit'` → **0 次**（输入含"帮我/为什么/报错/代码/越界"多重否决）。
- `grep -F 'intent_triage.llm_failed'` → **≥1 次**（safe-fail 命中，error 含模型不存在）。
- `grep -F 'pipeline_short_circuit'` → **0 次**；`grep -F 'pipeline.short_circuit'` → **0 次**（safe-fail 绝不短路）。
- `grep -F 'pipeline_clarification_pause'` → **0 次**（safe-fail card 不澄清）。
- 旁证：该轮可能出 `pipeline.pre_loop_done` 或 `evidence_gathered_set`（safe-fail 对 factual_qa/debug 仍置 needs_investigation=True → 进 IN-LOOP），**不算 FAIL**。
- 桌宠**仍正常回复**（截图见实答，无未捕获 traceback / 无"启动失败"对话框）。

> ⚠️ **判 problem_type=factual_qa 的方式**：safe-fail 走 `_safe_card`，**不打 `intent_triage.done`**。故 factual_qa 的硬证据是**间接**的：(a) `llm_failed` 出现 + (b) 不短路（无 `pipeline_short_circuit`/`pipeline.short_circuit`）+ (c) 进 IN-LOOP（出 `pipeline.pre_loop_done` 或 `evidence_gathered_set`）。若 safe-fail 误派生 chitchat，会出 `pipeline_short_circuit` → 由 (b) 直接抓到 FAIL。

**PASS/FAIL 判据**：PASS = allowlist_hit=0 + llm_failed≥1 + **无 pipeline_short_circuit（两条）** + 无 clarification_pause + 桌宠正常回复不卡死 + 进 IN-LOOP（pre_loop_done/evidence_gathered_set 至少一条）。出现 `pipeline_short_circuit`（safe-fail 误判 chitchat 短路）= **★ FAIL**；整轮挂死/无回复/traceback = **★ FAIL**。

**能逼出的 bug**：`_safe_card` 未把 chitchat→factual_qa（WI-8a 桥被 Phase 3 改坏、或 derived_pt 硬当 chitchat → needs_investigation=False → 误短路/跳过取证）；预分析失败致整轮挂掉。

---

### TC-6 ★ BUGB-6 真 code"帮我看这段 python 为何 IndexError" — 组装 task_type=code

**输入**：`帮我看这段 python 为何 IndexError`

**步骤**
1. 点「新话题」重置，截图 `TC-6-00-reset.png`。
2. 发送 `帮我看这段 python 为何 IndexError`，截图 `TC-6-01-send.png`，等回完，截图 `TC-6-02-reply.png`。

**硬证据 grep**（该轮时间窗内）
- `grep -F 'assembler_task_classified'` → **恰 1 次**，且该行含 **`task_type=code`**（rule tier 命中 `python` → path=rule → `code`；即便 rule 没命中也因 `has_code_signal('python'/'IndexError')` 落词法地板 `code`，双保险）。
- `grep -F 'task_type=chat'` 在该轮窗口 → **0 次**（不得被组装成 chat bundle）。
- 旁证（流水线真跑）：`grep -F 'intent_triage.done'`（problem_type 多半 debug/factual_qa，short_circuit=False）或 `grep -F 'pipeline.pre_loop_done'` → 出现。
- `grep -F 'intent_triage.allowlist_hit'` → **0 次**（含"帮我/为何/python/IndexError"否决，不进 allowlist）。

**PASS/FAIL 判据**：PASS = 该轮 `assembler_task_classified task_type=code`（非 chat）+ 流水线真跑（done short_circuit=False 或 pre_loop_done）+ 桌宠真给 code/debug 向回复。出现 `task_type=chat` = **classifier 没复活/词法地板没倒向能力侧 = ★ FAIL**（BUG-B P2 缺陷：真 code 拿 chat persona/工具/skill bundle）。

**能逼出的 bug**：classifier 仍 `default='chat'`（Phase 2 WI-6 没接/被 Phase 3 回归）；rule tier code 正则漏 `python`；`has_code_signal` 漏 `IndexError`/`python`。

---

### 3.6 Phase 3 无回归专项确认（WI-9b 去 hint + WI-8a 护栏注释，无新行为）

> Phase 3 是内部重构、无新用户可见行为。本节**不新增独立真测 case**（避免重复 ★ 用例），而是把"去 hint 后不退化、分流与前两阶段一致"作为**对照确认清单**，**复用上面 TC-1/2/3/6 + TC-4 的实测结果**逐条核对。任一项不符 = Phase 3 引入了回归。

| 无回归确认项 | 依据 case | 期望（与前两阶段一致）| 不符即回归 |
|---|---|---|---|
| **WI-9b 去 hint 后 factual 裸判不退化** | TC-1 | `intent_triage.done problem_type=factual_qa`（与 Phase 1 TC-B1 一致）| problem_type≠factual_qa 或误短路 |
| **WI-9b 去 hint 后中文 debug 裸判不退化** | TC-2 | `intent_triage.done problem_type∈{debug,factual_qa} short_circuit=False`（与 Phase 1 TC-B2 一致）| 落 chitchat 或短路 |
| **闲聊分流与 Phase 1 一致** | TC-3 | `allowlist_hit`=1 + done=0（0 LLM，与 Phase 1 TC-A1 一致）| 走了 LLM |
| **safe-fail 桥仅 fallback（WI-8a 护栏语义）** | TC-4 | `llm_failed` + 无 `pipeline_short_circuit`（chitchat→factual_qa，与 Phase 1 TC-D1 一致）| 误短路 |
| **code 分流与 Phase 2 一致** | TC-6 | `assembler_task_classified task_type=code`（与 Phase 2 TC-A1 一致）| task_type=chat |
| **WI-8a 纯注释零行为漂移** | TC-1/2/4 全体 | 桥仍只在 safe-fail 路径生效（done 成功解析的 TC-1/2 走的是裸判 problem_type 非桥；仅 TC-4 safe-fail 走桥）| done 路径的 problem_type 来自桥而非裸判（表现为 problem_type 与输入语义系统性错配）|
| **静态确认（非 UI，旁证）** | 源码 grep | `_PRE_ANALYSIS_SYSTEM` prompt **正文**（:159 起的指令串）**不含**"系统已判定的初步任务类型"指令；`_TASKTYPE_TO_PROBLEM`（:55）附近有单一来源护栏注释（:46-54）| prompt 正文仍含 hint 指令 / 护栏注释缺失 |

> ⚠️ 最后一行"静态确认"是**旁证**（非 UI 证据，不替代真测）。注意 grep 区分**注释 vs prompt 正文**：
> - `grep -n "系统已判定" backend/deskpet/agent/intent_triage.py` 在当前代码**只命中第 158 行的注释**（注释解释"已去掉它"），**prompt 正文（:159 起的双引号指令串）里不含该指令** —— 这才是 WI-9b 已落地。**不要**因 grep 有 1 命中（注释行）就误判未去 hint；读那行确认是注释即可。
> - `grep -n "唯一权威\|单一来源\|防 BUG-C\|safe-fail" backend/deskpet/agent/intent_triage.py` 应命中 :46-54 护栏注释块（WI-8a 已落地）。
>
> **真测 PASS 仍以 TC-1~6 的 UI 链路证据为准**，静态 grep 只佐证改动在位。

---

## 4. 防假绿守则（HARD）

1. **log grep 只是辅助，必须配真截图 + 真 UI 点击/输入。** 单跑 `python -c "from deskpet.agent.intent_triage import IntentTriage; ..."` 或 `import is_obvious_chitchat` 跑一遍 **不算**任何 case 的 PASS —— 那是脚本回放内部函数，违反 `feedback_real_e2e_not_script_replay`。每个 case 必须：真坐标点击输入框 → 真键盘/剪贴板输入 → 真发送 → 截图气泡 → 等桌宠回完 → 再 grep tauri-dev.log。
2. **WebSocket 直注禁止**：不允许 `ws://127.0.0.1:8100/*` 直发 chat 帧绕过 UI。必须经桌宠输入框真模拟人。
3. **两个判定器分清，不能互充**（Phase 3 核心防混淆）：BUGB-1/2/3/4 看 **`intent_triage.*`**（短路/流水线判定器，Phase 3 改的就是它的 prompt）；BUGB-6 看 **`assembler_task_classified task_type=`**（组装 bundle 判定器）。**不要**拿 `intent_triage.done problem_type=` 当 BUGB-6 证据，也不要拿 `assembler_task_classified` 当 BUGB-1 证据。同一轮两个 log 都会出，各判各的。
4. **`pipeline.short_circuit`（点）vs `pipeline_short_circuit`（下划线）正则坑**：grep 短路计数一律用 `grep -F`（字面）。用 `grep 'pipeline.short_circuit'`（无 `-F`）时 `.` 通配会把 `pipeline_short_circuit` 也匹进来导致双计。**判短路三件套**（allowlist_hit / pipeline.short_circuit / pipeline_short_circuit）+ 组装锚点 `assembler_task_classified` 全部 `grep -F` 分别数。
5. **"0 次 LLM"（BUGB-3）必须用"无 done/llm_failed/parse_failed"证，不能只看"有 allowlist_hit"**：双向确认 —— allowlist_hit 出现 **且** 三条 LLM 路径 log 全 0，才算 0 LLM。只看到 allowlist_hit 不去查 done=0，可能漏掉"既打了 allowlist_hit 又因别的 bug 走了 LLM"的回归。
6. **真问题判定看 `short_circuit=False` 而非只看"有回复"**：桌宠对短路闲聊也会回复，"有回复"不区分短路与否。BUGB-1/2 的硬约束是 **allowlist_hit=0 且 short_circuit 两条 log 均 0 且 done problem_type 正确**，不是"桌宠答了"。
7. **BUGB-4 改 config 须还原**：BUGB-4 改坏 analysis_model 后**必须**测后还原 `deepseek-v4-pro` 重启，并确认 `problem_pipeline_init enabled=true`，否则污染后续/下次 run（坑：残留坏串会让所有真问题 safe-fail）。run 结束前 grep 确认 config 已还原。
8. **跑的是当前代码非 frozen + classifier 已注入**：每次 run 头部确认 `[backend_launch] Dev python=...` + `assembler_classifier_llm_injected`（无 `inject_failed`），否则 Phase 3 改动 / Phase 2 classifier 不在被测进程里（坑 #8），测了白测。
9. **去 hint 退化要靠裸判 problem_type 正确证，不能只看"不短路"**：WI-9b 的回归风险是 deepseek 去掉 hint 后**判错类型**（不一定误短路）。TC-1 必须钉 `problem_type=factual_qa`、TC-2 钉 `∈{debug,factual_qa}` 且**非 chitchat**——只验"不短路"会漏掉"裸判退化成 web_search/chat 但仍不短路"的隐性回归。
10. **relay 不稳的诚实标注**：token 过期/relay 502 致预分析全 safe-fail（BUGB-1/2 的 done 测不到，全走 llm_failed）或 classifier llm tier 全落地板（BUGB-6 path=llm 测不到）时，**显式标注 env-limited + 实测值**，retry ≥3 次不同 workaround（重登 token / 换稳定窗口 / 确认 NO_PROXY）才标"环境受限"，不伪造 PASS。

---

## 5. 收敛标准（plan §6）

通过本文档 = **BUG-B plan 整体收官、可上线**，须同时满足：

1. **★ 全 PASS**：TC-1（BUGB-1 factual_qa 不短路）/ TC-2（BUGB-2 中文 debug 不短路）/ TC-3（BUGB-3 闲聊 0 LLM）/ TC-4（BUGB-4 safe-fail 不短路）/ TC-6（BUGB-6 真 code→task_type=code）五条全过。
2. **组装 task_type 对真 code/debug 不再恒 chat**：TC-6 `assembler_task_classified task_type=code`（非 chat），证 Phase 2 classifier 复活在最终链路仍生效。
3. **闲聊 0 次 LLM**：TC-3 `allowlist_hit`=1 且 done/llm_failed/parse_failed=0。
4. **Phase 3 无回归**：§3.6 七条确认项全部"与前两阶段一致"——WI-9b 去 hint 后 deepseek 裸判 problem_type 不退化（TC-1/2）、闲聊/code 分流与 Phase 1/2 一致（TC-3/6）、safe-fail 桥仅 fallback（TC-4）、静态 grep 确认 prompt 去 hint + 护栏注释在位。
5. **BUGB-4 测后 config 已还原**：run 结束 grep 确认 `analysis_model=deepseek-v4-pro` 生效、`problem_pipeline_init enabled=true`。

任一 ★ FAIL 或无回归项不符 → BUG-B plan **未收官**，回 plan 修后复测。relay 不稳导致的 env-limited 须显式标注 + retry≥3，不计入"伪 PASS"。

---

## 6. 结果汇总表（执行后填）

| case | ★ | plan §6 | 输入 | 坐标 | 截图 | log 证据（关键计数）| 判定 |
|---|---|---|---|---|---|---|---|
| TC-1 | ★ | BUGB-1 | 光合作用为什么需要光 | | | allowlist_hit ___(=0)、done problem_type=___(factual_qa) sc=___(False)、pre_loop_done ___(出现)、short_circuit×2 ___(=0) | |
| TC-2 | ★ | BUGB-2 | 我这段Python代码列表越界为什么报IndexError | | | allowlist_hit ___(=0)、done pt=___∈{debug,factual_qa} sc=False、short_circuit×2 ___(=0) | |
| TC-3 | ★ | BUGB-3 | 你好呀 | | | allowlist_hit ___(=1)、done ___(=0)、llm_failed ___(=0)、parse_failed ___(=0)、short_circuit×2 各 ___(=1) | |
| TC-4 | ★ | BUGB-4 | 帮我分析为什么这段排序代码会越界报错（坏 analysis_model）| | | allowlist_hit ___(=0)、llm_failed ___(≥1)、pipeline_short_circuit ___(=0)、clarification_pause ___(=0)、仍回复 ___、config 已还原 ___ | |
| TC-6 | ★ | BUGB-6 | 帮我看这段 python 为何 IndexError | | | assembler_task_classified task_type=___(code)、task_type=chat ___(=0)、流水线真跑 ___ | |

**Phase 3 无回归确认（§3.6）**：

| 确认项 | 依据 case | 实测 | 符合？ |
|---|---|---|---|
| WI-9b factual 裸判不退化 | TC-1 | problem_type=___ | |
| WI-9b 中文 debug 裸判不退化 | TC-2 | problem_type=___ | |
| 闲聊分流与 Phase1 一致 | TC-3 | allowlist_hit=___ done=___ | |
| safe-fail 桥仅 fallback | TC-4 | llm_failed=___ short_circuit=___ | |
| code 分流与 Phase2 一致 | TC-6 | task_type=___ | |
| 静态：prompt 正文去 hint | grep 旁证 | "系统已判定" 仅命中注释行(:158)___、prompt 正文(:159+)不含指令 ___(确认) | |
| 静态：护栏注释在位(:46-54) | grep 旁证 | 护栏注释命中 ___(≥1) | |

---

## 7. 汇总

| 项 | 值 |
|---|---|
| 用例总数 | **5 真测 TC**（TC-1/2/3/4/6，对齐 plan §6 全 5 条）+ **1 无回归确认清单**（§3.6 七项，复用上述 case 实测，不新增真测轮次）|
| ★ 必过 | **5**（TC-1 BUGB-1 / TC-2 BUGB-2 / TC-3 BUGB-3 / TC-4 BUGB-4 / TC-6 BUGB-6，plan §6 五条全 ★）|
| 文档定位 | **Phase 3 无回归 + plan §6 最终全功能验收合一**；Phase 3 是内部重构（WI-9b 去 hint prompt + WI-8a 护栏注释）无新用户可见行为，无回归专项靠 §3.6 对照前两阶段确认 |
| 覆盖的 plan §6 验收点 | **BUGB-1**（TC-1）/ **BUGB-2**（TC-2）/ **BUGB-3**（TC-3）/ **BUGB-4**（TC-4）/ **BUGB-6**（TC-6）**全覆盖**；BUGB-5 plan §6 表未列（无此编号）|
| 两个判定器区分 | `intent_triage.*`（intent_triage.py，决定短路/流水线，BUGB-1/2/3/4 + Phase 3 改的 prompt）vs `assembler_task_classified`（assembler.py:251，决定组装 bundle，BUGB-6）；同轮各打各 log 不互替 |
| 是否需 windows-mcp 真测 | **是**（每例真坐标点击 + 真中文/剪贴板输入 + 截图 + tauri-dev.log grep；禁 WS 直注 / pytest / import 当 UI 证据）|
| 需改 config 重启的用例 | **TC-4**（坏 analysis_model `gpt-does-not-exist-zzz`，**测后还原 deepseek-v4-pro 重启**确认 pipeline_init）|
| relay 依赖诚实标注 | BUGB-1/2 的 done、BUGB-6 的 path=llm 依赖 relay 链路；token 过期/relay 不稳须标 env-limited + retry≥3 + 实测值，不伪造 |
