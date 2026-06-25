# 七步问题处理流水线 — 生产上线手工测试（自包含·单文档可执行）

> **被测功能**：「七步问题处理流水线」(ProblemHandlingPipeline，毛选《矛盾论/实践论》方法论，仅 Companion 主线)。
> 把"收到问题 → 处理"落成**可门控、可观测**的显式流水线：
> - **Step1+3 预分析**（意图分诊 + 抓主要矛盾，合并 **1 次 deepseek 调用**，决策4）→ `intent_triage.py`
> - **Step2 取证门控**（没有调查就没有发言权）→ `evidence_gate.py`
> - **Step4 弹钢琴定方案**（attack_order 首步对准 principal）→ `plan.py`
> - **Step5 执行** → ReAct loop
> - **Step6 异体自检**（非执行者 fresh-context 打分）→ `self_check_gate.py`
> - **Step7 收敛止损**（胸中有数 + 不硬撑）→ `convergence_controller.py`
> 编排器 `problem_pipeline.py` 跑 PRE-LOOP（Step1+3+4）；IN-LOOP 三闸（取证/自检/收敛）注入 `agent_loop.py`。
>
> **本文档定位**：**生产上线验收（production ship gate）—— 通过本文档 = 可上生产环境。**
> 单文档自包含：功能正确性（§3 TC）+ 「遍历+写副作用」幂等性（§4 副作用地图 + §5 IDEM）一份跑完即上线判定，
> 不需要跳别的文档。**本文档合并并取代**早期拆成两份的
> [`../2026-06-24-problem-pipeline-maoxuan/manual-test.md`](../2026-06-24-problem-pipeline-maoxuan/manual-test.md)（功能 TC-0~10）与
> [`../2026-06-25-problem-pipeline-production/manual-test.md`](../2026-06-25-problem-pipeline-production/manual-test.md)（幂等专项），
> 作为**唯一可执行上线门**。那两份保留作历史与细节参考。
>
> **本次纳入的鲁棒性改动（WI-4-C，2026-06-25 真测发现并修）**：
> - 预分析走**非流式 + 保留 strict schema**（避开 relay `stream + json_schema` 偶发空 body），失败再退流式（main.py `_make_str_llm_call`）。
> - 剥 deepseek thinking 模型的 `<think>…</think>` CoT 前缀（`_THINK_RX`，intent_triage.py:348/373）。
> - JSON 预清洗私用区/控制字符（`_sanitize_json_text`，:357）+ 防御性 `_safe_int`/`_safe_float`（:299/311，防 `_parse_contradiction` 崩 run_pre_loop）。
> - **safe-fail 绝不短路**：畸形 JSON / LLM 失败 → `_parse` 返回 `None` → `analyze()` 直接返回 `_safe_card`，**不据坏 classifier 的 derived_pt 派生 chitchat 短路**（intent_triage.py:204-209）。
>
> **对应 plan**：[plans/2026-06-24-problem-handling-pipeline-maoxuan/](../../plans/2026-06-24-problem-handling-pipeline-maoxuan/)
> **最后更新**：2026-06-25

---

## 0. 测试前置（HARD — 不满足则结果作废）

### 0.1 环境（必须先满足）

1. **进程清场**：`taskkill /F /IM deskpet.exe` + 杀残留 Vite node（项目坑 #1）。**不要手动起 backend / vite**（坑 #7/#9，Tauri 自管唯一 backend + 唯一 vite）。
2. **跑 worktree 代码（非 frozen）**：只给 **Tauri 进程**注入 env（坑 #8）：
   - `DESKPET_BACKEND_DIR=G:\projects\deskpet\backend`
   - `DESKPET_PYTHON=G:\projects\deskpet\backend\.venv\Scripts\python.exe`
   - `DESKPET_DEV_MODE=1`
   - `DESKPET_USER_DATA_DIR=G:\projects\deskpet\backend\userdata`
   - `DESKPET_CLOUD_API_KEY=<根目录 .env 里的 tsk_ key>`（relay LLM 链路；见 [`LOCAL-DEV-CREDENTIALS.md`](../../LOCAL-DEV-CREDENTIALS.md)）
   - `NO_PROXY=*`（坑：Clash 7897 掐空闲长连，长耗时 LLM 误判挂起）
   启动后 log **必须**出现 `[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`；
   若见 `[backend_launch] Bundled exe=...` → 跑的是旧 frozen，**测了等于白测，立即停下重配**。
3. **流水线装上**：grep `problem_pipeline_init enabled=true intent=... evidence=... self_check=...`（main.py:**1756**）。grep 不到 → 全部用例无意义。
4. **日志落盘**：启动命令把 tauri dev 输出重定向到
   `plans/manual-results-2026-06-25-problem-pipeline-ship/tauri-dev.log`，所有 log 证据 grep 这份（backend structlog 走 stderr → Tauri pipe → 落进这份 log，坑 #7）。

### 0.2 windows-mcp 真测纪律（HARD CONSTRAINT — 不可妥协）

> 触发词已命中（"用 windows-mcp 测试" / "真测"）。本纪律强制生效，完整版见 `~/.claude/knowledge-base/windows-mcp-e2e.md`。

1. **真模拟人**：每个 case 必 Screenshot/Snapshot → **真坐标点击 / 真键盘(剪贴板)输入** → 截图验证 → 日志判 PASS/FAIL。
2. **禁绕过（HARD）**：**不允许**用 `ws://127.0.0.1:8100/*` WebSocket 直注、`pytest`、`import` backend 查 registry、文件/keychain 存在性、log-only grep **替代**真点击作为 UI 证据。**log grep 只是判定辅助，必须配真截图 + 真 UI 操作链路。**
3. **每个动作前 declare**：`坐标=(x,y) | 动作=click/type/restart | 期望=...`。
4. **截图存盘**：`plans/manual-results-2026-06-25-problem-pipeline-ship/screenshots/<case-id>-NN-*.png`。
5. **失败 retry ≥ 3 次不同 workaround** 才能标「环境受限」；**跳过任何 case** 必须显式声明 + 具体理由 + **等用户确认**。
6. **中文输入 workaround**：windows-mcp `Type`(UIA SetValue) 实测多数可靠；不落则 STA Runspace + `Clipboard.SetText("中文")` + 先 Click 输入框聚焦再 Ctrl+V；**发送**用 `Type(..., press_enter=True)` 或真点「发送」按钮（WebView2 不响应老式 mouse_event）。窗口位置每次重启会漂（window-state 插件），**每次重启后重新 Snapshot 取坐标**。

### 0.3 ★ 上线一票否决项（任一 FAIL = 不可上线）

| Case | 验收点 | 类别 |
|---|---|---|
| **TC-1** | 闲聊走 1 次 deepseek `intent_triage.done problem_type=chitchat short_circuit=true` → 轻收尾不进 IN-LOOP；非闲聊对照走完整流水线（**Y-light：不是"0 LLM 纯规则短路"**） | 功能(短路正确性) |
| **TC-2** | debug 取证门控：没取证就下结论被拦 `evidence_gate.blocked` → `evidence_gathered_set`（或模型本就先取证） | 功能(取证) |
| **TC-9** | kill-switch（enabled=false）零回归 BC | 功能(BC 守护) |
| **IDEM-1** | 重复发同一闲聊 N 次，每次各 **1 次** done、**无早期 chitchat_rule 短路 log**、无 IN-LOOP 残留 | 幂等(决策幂等) |
| **IDEM-3** | 澄清持久化单 send 只写 1 条 + 重启后多轮不断裂不重复 | 幂等(持久化) |
| **IDEM-4** | 进程重启后 per-run 标记不残留（干净起跑） | 幂等(状态隔离) |
| **IDEM-5** | 预分析失败可重试自愈，不卡死、不污染后续 | 幂等(失败重试) |

任一 ★ FAIL → 不算完成，回 plan 修。

---

## 1. 真实日志锚点速查（执行时 grep 用，已核实源码行号 2026-06-25）

| 步 | 锚点字符串 | 代码位置 | 含义 |
|---|---|---|---|
| 装配 | `problem_pipeline_init enabled=true intent=... evidence=... self_check=...` | main.py:1756 | 编排器构造成功 |
| 装配 | `pipeline_external_evaluator_model model=... base=...` | main.py:1195 | 异体评估器用独立 provider（Step6 异体证据）|
| Step1+3 | `intent_triage.done problem_type=<...> ambiguity=<f> clarify=<bool> short_circuit=<bool> has_contradiction=<bool>` | intent_triage.py:222 | **每条消息(含闲聊)都调 1 次 deepseek 出意图+矛盾+短路判定**（Y-light：短路决策权交 deepseek）|
| Step1+3 | `intent_triage.llm_failed error=...`（logger.**warning**）| intent_triage.py:200 | 预分析 LLM 失败 → safe-fail 降级裸 ReAct |
| Step1+3 | `intent_triage.parse_failed preview=...`（logger.**warning**）| intent_triage.py:208 | 畸形 JSON → safe-fail（**不**短路）|
| PRE-LOOP | `pipeline.short_circuit problem_type=<...>` | problem_pipeline.py:86 | deepseek 判 chitchat+低歧义 → 编排器整条短路 |
| PRE-LOOP | `pipeline_short_circuit sid=<...>` | main.py:6868 | 闲聊整条短路，不进 IN-LOOP |
| PRE-LOOP | `pipeline.pre_loop_done injections=<n> events=<n>` | problem_pipeline.py:110 | 非闲聊：注入意图(+矛盾) system 后进 IN-LOOP |
| PRE-LOOP | `pipeline_clarification_pause sid=<...>` | main.py:6893 | needs_clarification → 暂停反问 |
| PRE-LOOP | `clarify_persist_assistant_failed sid=<...> err=...` | main.py:6891 | 澄清持久化失败(bug 信号，正常应为 0) |
| ~~已删~~ | ~~`intent_triage.shortcircuit reason=chitchat_rule`~~ | ~~(已删)~~ | **Y-light 已删早期纯规则短路；任何地方出现这条 = 跑了旧 frozen，结果作废** |
| Step2 | `evidence_gathered_set sid=<...> tool=<name>` | agent_loop.py:2423 | **本 run 内**首次命中取证工具，置 `_evidence_gathered=True` |
| Step2 | `evidence_gate.blocked nudges_used=<n>` | evidence_gate.py:88 | 未取证就下结论被拦 |
| Step2 | `evidence_gate_nudge_injected sid=<...> nudge=<n>` | agent_loop.py:1516 | `<调查>` nudge 注入(per-run 计数，从 1 起算) |
| Step2 | `evidence_gate_exhausted nudges_used=<n>` | evidence_gate.py:77 | 超 max_nudges 放行(防死循环) |
| Step6 | `self_check.done mode=<...> passed=<bool> heterogeneous=<bool> unmatched=<n>` | self_check_gate.py:128 | 异体自检结论 |
| Step6 | `self_check_nudge_injected sid=<...> mode=<...> nudge=<n>` | agent_loop.py:1644-1647 | `<自检>` 反思注入(per-run 计数) |
| Step7 | `convergence.stop_loss reason=<...> principal_resolved=<bool> unverified=<n>` | convergence_controller.py:71 | 资源触顶 → 诚实止损 |

> ⚠️ **grep 区分两条同形 log**：`pipeline.short_circuit`（problem_pipeline.py:86，**带点**）与 `pipeline_short_circuit`（main.py:6868，**下划线**）是两条不同 log。用正则 grep `pipeline.short_circuit` 时 `.` 会通配同时匹配两条导致计数翻倍——**用字面匹配**（`grep -F 'pipeline.short_circuit'` / `grep -F 'pipeline_short_circuit'`）分别计数。
> WS 事件(`chat_v2_intent/contradiction/evidence_gate/selfcheck/convergence`)是**瞬态广播**，判定主依据是 backend log + 截图。

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
2. `坐标=(in_x,in_y) | 动作=type "<文本>" press_enter=true | 期望=消息气泡出现，桌宠开始回复`
3. 截图 → `wait_idle`(轮询 log 直到 idle) → grep 锚点。

---

## 3. 功能正确性用例（TC-1 ~ TC-10，逐步 + 预期）

> 每条：目的 → 步骤 → 期望硬证据(截图 + log) → 判定 → 能逼出的 bug。

### TC-1 ★ 闲聊短路 vs 非闲聊完整流水线（Y-light 短路正确性）

**目的**：闲聊由 deepseek 判 chitchat+低歧义 → 短路轻收尾不进 IN-LOOP；非闲聊走完整流水线。验证 Y-light 短路决策权确在 deepseek（不是删掉的纯规则 chitchat_rule）。

**步骤**
1. 发送 `"你好呀今天天气真好"`，截图 `TC-1-01-chitchat.png`，等回完。
2. 点「新话题」`(new_x,new_y)` 重置，截图 `TC-1-02-reset.png`。
3. 发送（非闲聊对照）`"帮我排查我项目里用户登录接口为什么偶尔返回 500，先读源码和日志定位根因再回答"`，截图 `TC-1-03-debug.png`，等回完。

**期望硬证据**
1. 闲聊轮：`intent_triage.done problem_type=chitchat ambiguity=<低> short_circuit=true` **恰 1 次** → `pipeline.short_circuit problem_type=chitchat` → `pipeline_short_circuit sid=...`；**这一轮无任何 IN-LOOP 闸 log**（`evidence_*`/`self_check.*`/`convergence.*` 全 0）。
2. 非闲聊轮：`intent_triage.done problem_type≠chitchat short_circuit=false` → `pipeline.pre_loop_done`（进 IN-LOOP）；走完整流水线。
3. **全程 0 次** `intent_triage.shortcircuit reason=chitchat_rule`（旧代码标志，出现即跑了旧 frozen → 作废）。
4. 两轮桌宠都正常回复（闲聊轻快、debug 进入调查/回答）。

**判定**：PASS = 闲聊 short_circuit=true 不进 IN-LOOP + 非闲聊 short_circuit=false 进完整流水线 + 无 chitchat_rule log。任一项不符 = FAIL。**★ 一票否决。**

**能逼出的 bug**：deepseek 把真 debug 误判 chitchat 短路；闲聊误进 IN-LOOP 触发取证；跑了旧 frozen（冒 chitchat_rule）。

---

### TC-2 ★ debug 取证门控真拦截（没有调查就没有发言权）

**目的**：needs_investigation=true 时，模型想不取证就下结论 → `evidence_gate.blocked` 注入 `<调查>` nudge 逼它先取证。

**步骤**
1. 点「新话题」重置。
2. 发送 `"我的导出功能突然报错了，你直接告诉我是什么原因吧"`（诱导直接下结论），截图 `TC-2-01-send.png`，等回完，截图 `TC-2-02-reply.png`。

**期望硬证据（硬 log 断言）**
1. `intent_triage.done problem_type=debug needs_investigation=true`（has_contradiction 可 true/false）。
2. **二选一即 PASS**：(a) `evidence_gate.blocked nudges_used=1` → `evidence_gate_nudge_injected nudge=1` → 之后 `evidence_gathered_set tool=<...>`（被拦后真去取证）；或 (b) 模型本就先调取证工具 → 直接 `evidence_gathered_set tool=<...>`（gate 未 block）。

**观察项（主观·不作硬判定）**：桌宠回复**基于取证结果**（不是凭空猜原因）—— 人工读，仅记录不卡 PASS/FAIL。

**判定**：PASS = 出现 `evidence_gathered_set`（无论是否先被 block）。唯一硬 FAIL = **没取证就给结论且 gate 没拦**（无 `evidence_gathered_set` 也无 `evidence_gate.blocked`，却给了确定原因）。**★ 一票否决。**

**best-effort 标注**：是否 `evidence_gate.blocked` 依赖 LLM 首轮是否真想直接下结论。若模型先取证（gate 不 block），降级断言 = 出现 `evidence_gathered_set` 即证取证门语义成立，报告注明「gate 未 block」。

---

### TC-3 多症状抓主要矛盾，计划首步对准 principal

**目的**：复合问题（多症状）→ deepseek 填 contradiction 段，点名 principal + attack_order；计划首步对准 principal。

**步骤**
1. 点「新话题」重置。
2. 发送 `"我的网站最近又慢又偶尔白屏，登录还时不时失败，你帮我把这些问题都解决了"`，截图 `TC-3-01.png`，等回完。

**期望硬证据（硬 log 断言）**
1. `intent_triage.done problem_type=<debug|multi_task> has_contradiction=true`。
2. WS 事件 `chat_v2_contradiction` payload 含 `principal`(整数 id) + `attack_order`(数组) + `rationale`（或 backend log `pipeline.pre_loop_done injections>=2`，证注入了 `<主要矛盾>`）。

**观察项（主观·不作硬判定）**：桌宠回复**先攻主要矛盾**（不是平摊或乱序）+ principal 选得是否合理 —— 人工读，仅记录不卡 PASS/FAIL（依赖 LLM 质量）。

**判定**：PASS = has_contradiction=true + 有 principal/attack_order（"抓矛盾 flag 真接通、计划吃到 attack_order"）。principal 选得对不对、回复是否先主后次 = 观察项不评判（best-effort）。

---

### TC-4 异体自检 fresh-context（非执行者打分）

**目的**：Step6 自检在 VerifyGate 反复失败(failure_count≥2)后升级到**异体评估器**（独立 provider，非主 LLM 自评）。

> ⚠️ **真机难触发诚实标注**：`heterogeneous=true` 仅在 `verify_passed=false` 且 `mode=strict`(debug/creation) 且 `failure_count>=2` 且 external_evaluator 非空时才触发（self_check_gate.py:101-107）。需先逼出桌宠"虚报完成"被 VerifyGate 拦 ≥2 次——自然对话很难连错 2 次。

**步骤**
1. 点「新话题」重置。
2. 发送可验证产物任务 `"帮我写一个判断质数的 Python 函数 is_prime，写完跑一下确认正确"`，截图 `TC-4-01.png`，等回完。
3. 观察 log 的 `self_check.done`。

**期望硬证据**
1. `self_check.done mode=strict passed=<bool> heterogeneous=<bool> unmatched=<n>`（debug/creation → strict 档）。
2. 启动时 `pipeline_external_evaluator_model model=... base=...`（异体评估器接通的旁证）。
3. **若** heterogeneous=true：报告记录触发条件（failure_count≥2）。**若** heterogeneous=false：诚实记录"未连错 2 次，未升级异体"——**这不算 FAIL**（异体是兜底，非每次触发）。

**判定**：PASS = `self_check.done` 真出现且档位正确(debug=strict) + 异体评估器已接通(启动 log)。heterogeneous=true 为加分项，触发不了标 best-effort + 实测 failure_count。FAIL = self_check 用执行者自评(无独立 provider)、或 debug 走了 off 档。

---

### TC-5 (功能·best-effort 触发) 收敛止损诚实报告（非假装完成）

**目的**：资源触顶 → ConvergenceController 出 1 份**诚实止损报告**（做了什么/卡在哪/建议），不假装完成、不臆造 stop_reason。

> ⚠️ **构造路径 + 机制（C 实证，B2 修正）**：`max_turns`/`per_tool_max_consecutive` 都是 `GateConfig` 硬编码(termination.py，`max_turns=10000` 实质禁用)，**TOML 不可注入**。
> - **唯一产出 `convergence.stop_loss` 的构造 = 改源码** `main.py:6597` 的 `_max_iter = 50 if _in_code_mode else 16` → `else 4`，重启。
> - **机制（非 allows_call）**：`_max_iter` 是 agent_loop 的 **Python for-loop range 上限（max_iterations）**，**不是** gate 预算；`allows_call` 盯的是 `max_turns=10000` 永不因此触发。实际走 **range 耗尽出口**（agent_loop.py:2575-2606）：循环跑满后**手动**把 `reason=HARD_MAX_TURNS`(=`"error_max_turns"`) 喂 `convergence.evaluate` → stop_loss。最终可观测 log `convergence.stop_loss reason=error_max_turns` 不变。
> - hallucination 构造(反复同参调同一工具)走 `allows_tool` → `ErrorEvent` 路径，**不产出 stop_loss**，不可作本 case 依据。**不存在** `stop_reason=budget`（臆造值）。
> - ⚠️ **触发 best-effort(M2)**：range 耗尽出口仅在**模型连续 4 轮都 tool_use、从不 end_turn** 直到跑满时才到达；任何中途 end_turn/FinalEvent 提前 return 不经此路径。`_max_iter=4` 反而让模型更易在耗尽前收尾。**故 stop_loss 触发与否强依赖 LLM 是否真打满 4 轮工具**，标 best-effort + 记实测在第几轮 end_turn。

**步骤**
1. （构造）改 `main.py:6597` `else 16`→`else 4`，重启，确认 `problem_pipeline_init enabled=true`。报告须标注"改源码非 config"。
2. 发送诱导多轮逼近上限的复合问题 `"帮我把这个含 5 个互相依赖、信息都缺失的子问题彻底解决，每个都要查证后再给结论"`，截图 `TC-5-01-stoploss.png`，等桌宠触顶止损。
3. **测后还原** `main.py:6597` `else 4`→`else 16`，重启确认恢复。

**期望硬证据**
1. **若触发**：`convergence.stop_loss reason=error_max_turns principal_resolved=false unverified=<n>`（reason 真实触顶值，**非 budget、非 hallucination**）。
2. **若触发**：桌宠输出**诚实止损报告**（`<收敛>` 含已做什么/卡在哪/建议），**该 run 内 stop_loss 恰 1 次**（不每轮刷屏）。

**判定**：PASS（条件式）= **若**模型打满 4 轮触发，则 1 份 converged=false 诚实报告 + stop_reason=error_max_turns + 没假装完成 + 不刷屏；**若**模型 ≤4 轮就 end_turn 未触发，记录实际在第几轮收尾 + 标 best-effort/LLM-limited（**不**作硬 FAIL，也**不**作 ship 阻断）。FAIL = 触发了却假装"已完成" / `stop_reason=budget` / 每轮重复刷 stop_loss。**诚实标注(HARD)**：报告写明用了改源码构造 + 实际 stop_reason 值 + 实测轮数，不允许用"跑很久没结束"当证据。

---

### TC-6 澄清出口多轮不断裂

**目的**：高歧义问题 → `clarify=true` → `pipeline_clarification_pause` 暂停反问 → 用户答 → 桌宠承接继续（多轮不断裂）。

**步骤**
1. 点「新话题」重置。
2. 发送歧义问题 `"帮我搞一下那个东西"`，截图 `TC-6-01-vague.png`，等桌宠**反问澄清**，截图 `TC-6-02-clarify.png`。
3. 答澄清 `"我是说帮我把桌面上的几个报表整理成一张汇总表"`，截图 `TC-6-03-answer.png`，等回完。

**期望硬证据**
1. 首轮 `intent_triage.done ... clarify=true` → `pipeline_clarification_pause sid=...`；前端只渲染 **1 条**澄清气泡。
2. `clarify_persist_assistant_failed` = 0。
3. 答后桌宠**承接澄清继续**（不把答案当全新无关问题、不重复反问）。

**判定**：PASS = 触发澄清 + 暂停 + 答后承接。**best-effort**：澄清触发依赖 LLM 判 ambiguity≥0.7；多次重试触不出则标 best-effort + 记实测 ambiguity 值。

---

### TC-7 非闲聊只调 1 次 LLM 预分析（合并证明）

**目的**：决策4 —— 意图 + 主要矛盾**合并 1 次 deepseek 调用**，不是两次串行。

**步骤**
1. 点「新话题」重置。依次发 3 条非闲聊（每条之间 wait_idle）：
   - factual：`"CATL 2024 年营收大概多少"`
   - debug：`"帮我看下这段 Python 为什么 IndexError"`（贴一小段越界代码）
   - research：`"帮我调研一下固态电池的最新进展"`
2. 各截图 `TC-7-01/02/03.png`。

**期望硬证据**
1. 每条各 **恰 1 次** `intent_triage.done`（合并调用，不是 intent + contradiction 两条 done）。
2. 简单 factual `has_contradiction=false`；复杂 debug/research 视情况 `has_contradiction=true`。

**判定**：PASS = 每条恰 1 次 done + 简单/复杂 has_contradiction 合理。每条出现 2 次 done（拆成两次串行调用）= FAIL。

---

### TC-7b (best-effort) WI-4-C 鲁棒性旁证（think-strip / sanitize / 三级 JSON 提取在真 relay 上活着）

**目的**：deepseek-v4-pro 是 thinking 模型，非流式裸补全里常带 `<think>…</think>` CoT 前缀 / 偶发私用区控制字符。验证 `_extract_json`(intent_triage.py:372-387) 的 think-strip(:373) + 控制字符净化(:357) + fenced/bare 三级提取仍能 parse 成功（走 `intent_triage.done` 而非 `parse_failed`）。这是最易随 thinking 模型行为漂移而回归的路径，TC-10/IDEM-5 测的是 LLM **失败**，本 TC 补测**畸形但可救**。

**步骤**
1. 点「新话题」重置。
2. 发一条会触发 deepseek 较多思考 + 填矛盾段的复杂 debug：`"我服务上线后内存持续上涨最后 OOM，GC 日志看不出明显泄漏，帮我系统性分析可能的根因和排查顺序"`，截图 `TC-7b-01.png`，等回完。

**期望硬证据**
1. `intent_triage.done problem_type=<debug|multi_task> has_contradiction=true`（预分析 parse 成功且填了矛盾段）。
2. 该轮窗口内 `intent_triage.parse_failed` = **0**、`intent_triage.llm_failed` = **0**（既没 LLM 失败也没畸形 JSON 兜底 → 证 think-strip/三级提取真把带 CoT 的输出救了回来）。

**判定**：PASS = `intent_triage.done has_contradiction=true` + parse_failed/llm_failed=0。**best-effort 标注**：是否真出现 `<think>` 前缀依赖 deepseek 当次行为，无法强制；若该轮恰好 parse_failed=1 但桌宠仍 safe-fail 正常回复（不卡死），记 best-effort + 注明"本轮预分析未救回，safe-fail 兜住"，不作 ship 阻断（safe-fail 不卡死由 TC-10/IDEM-5 ★ 兜）。

---

### TC-8 code 模式不受流水线影响（决策2 边界）

**目的**：流水线仅 Companion 主线；code 模式入口走原链路，无任何 `intent_triage.*`/`pipeline_*`。

**步骤**
1. 若 code 模式入口可达：打开 code panel，发一条典型编码请求 `"在 utils.py 加一个 slugify 函数"`，截图 `TC-8-01.png`。
2. 若入口不可达：声明 **env-limited** + 等用户确认。

**期望硬证据**：该 code 轮 **无任何** `intent_triage.*` / `pipeline_*` log（流水线没串进 code 路径）。

**判定**：PASS = code 路径零流水线 log。入口不可达 → env-limited 标注等确认。

---

### TC-9 ★ kill-switch（enabled=false）零回归 BC

**目的**：`enabled=false` 时全套流水线代码 short-circuit，对话行为字节级回退到今天的链路。

**步骤**
1. （构造）改 dev config `[features.problem_pipeline].enabled = false`，重启，确认装配 log（应**无** `problem_pipeline_init enabled=true`，而是回退分支）。
2. 发送 `"你好呀"`（闲聊）+ `"帮我排查登录为什么 500"`（debug），各截图 `TC-9-01/02.png`，等回完。
3. **测后还原** `enabled=true`，重启确认 `problem_pipeline_init enabled=true` 恢复。

**期望硬证据**
1. enabled=false 期间：**全程零** pipeline log（`intent_triage.*`/`pipeline.*`/`pipeline_*`/`evidence_*`/`self_check.*`/`convergence.*`）+ **零** `chat_v2_intent/contradiction/...` 事件。
2. 两条对话**仍正常回复**（裸 ReAct，功能不破）。

**判定**：PASS = 零流水线 log + 对话正常 + 还原后恢复。enabled=false 仍冒任何 pipeline log = 接线未挂 flag(BC 破) = FAIL。**★ 一票否决。**

---

### TC-10 safe-fail 降级不卡死（预分析失败兜底）

**目的**：预分析 LLM 失败 → safe-fail 接住 → 降级裸 ReAct 仍正常回复，不卡死。（与 IDEM-5 失败轮合并执行。）

**步骤**
1. （构造）改 dev config `[features.problem_pipeline].analysis_model` 为不存在的 model 名 `gpt-does-not-exist`，重启。
2. 发送非闲聊 `"帮我分析为什么这段排序代码会越界报错"`，截图 `TC-10-01.png`，等回完。
3. **测后还原** analysis_model（删 override 或留空=主 LLM），重启确认。

**期望硬证据**
1. `intent_triage.llm_failed error=...`（safe-fail 命中）。
2. 桌宠**仍正常回复**，**无未捕获 traceback** 致 _run_chat 死亡。
3. safe-fail 返回保守 card（contradiction=None、不澄清）→ **不**冒 `pipeline_clarification_pause` / `chat_v2_contradiction`。

> 注：safe-fail 的 `_safe_card` 对 debug/research/factual_qa 仍置 needs_investigation=true → 失败轮**可能正常出** `evidence_*` log，**不算 FAIL**。

**判定**：PASS = llm_failed + 仍回复不卡死 + 不乱触发澄清/矛盾。预分析失败导致整轮挂掉/无回复/traceback = FAIL。

---

## 4. 副作用 / 幂等地图（代码实证 —— 上线评审硬要求）

> reviewer 对每个「遍历 + 写副作用」逐条问的 5 问，下表逐点给代码层答案。**这是 §5 IDEM-1~6 的设计依据。**

| # | 写副作用点 | 代码位置 | 副作用类型 | ①「已处理」标记 | ②持久化(重启后还认得)? | ③重复调用/重进会重复产生副作用吗? | ④失败那条还能重试吗? | 对应幂等用例 |
|---|---|---|---|---|---|---|---|---|
| **S1** | 置 `_evidence_gathered=True` | agent_loop.py:2423 | 内存布尔(per-run) | `self._evidence_gathered` + **双门控**(:2416-2420)：`not _evidence_gathered` ∧ `is_investigative(tool)` ∧ **`tool ∉ _history_tool_names`** | **否**(by design)：`run()` 起每 run 重置 `_evidence_gathered=False` + 重新快照 `_history_tool_names`(:710-712，含 bundle.history 旧 tool 名) | **否**：置一次后本 run 不再重复；每 run 重新起算。⚠️**注意**：第 2 门控意味着「同会话/重启后**复用同名取证工具**(已在 history 快照里) → `evidence_gathered_set` **按设计不再打印**」——这是去重，非状态泄漏(见 IDEM-2/4 鉴别量修正) | N/A(纯内存，无外部副作用) | IDEM-2 / IDEM-4 |
| **S2** | evidence nudge 注入 + 计数 | agent_loop.py:1516 | 内存计数(per-run，**有界**) | `_evidence_nudges_used < max_nudges`(默认2)，超限 `exhausted` 放行 | 否(每 run 重置) | **否**：有界 max_nudges 防死循环；每 run 从 0 起 | N/A | IDEM-2 |
| **S3** | self_check/verify nudge 计数 | agent_loop.py:1645 | 内存计数(per-run，**有界**) | `verify_nudges_used < max_verify_nudges` | 否(本轮起算) | **否**：有界；每 run 从 0 起 | N/A | IDEM-4 |
| **S4** | **持久化澄清 assistant 行** | main.py 澄清分支 `append_message`(约 :6885，**注：该调用本身不打 log**) | **DB 写(持久化)** | 无 dedup key —— 靠「每轮一条」turn 语义(对齐 FinalEvent 持久化) | **是**：写 session_db，重启重载进 history | **每个 user turn 写 1 条**(语义正确，非重复 bug)；**同一次 send 不双写**(单条 await) | 失败记 `clarify_persist_assistant_failed`，**不卡死**(try/except) | **IDEM-3** |
| **S5** | 持久化 user 消息 | main.py user 持久化 `append_message`(既有链路) | DB 写(持久化) | `_user_msg_id`(既有链路，非流水线新增) | 是 | 每 turn 写 1 条(既有 chat 语义) | — | IDEM-3 旁证 |
| **S6** | `chat_v2_intent/contradiction/...` WS 事件 + peer 广播 | problem_pipeline.py:76/102；peer fan-out main.py `_broadcast_default_chat_peers` | 瞬态广播 | 无(无状态 emit) | 否 | **否**：无状态，重发即重新计算并重发，不累积；多连接 N → 每事件 N 份是设计(fan-out)非重复 bug | N/A | IDEM-1 旁证 |
| **S7** | 收敛止损报告 | agent_loop.py emit + FinalEvent(stop_reason="stop_loss") | 瞬态(随 final answer 落库) | `should_stop_loss` 每 run 判一次；`ConvergenceController` **无任何跨 run 可变状态**(全文无 self 状态字段) | 报告文本随 final answer 持久化(每 run 1 次) | **否**：每触顶 run 出 1 份，不每轮刷屏；结构上无跨 run 残留面 | — | IDEM-6 |

### 4.1 上线评审结论（一句话）

- **唯一新增的持久化写副作用 = S4(澄清持久化)**，且它**复用既有 per-turn 持久化语义**(每轮一条、单 send 不双写、失败有兜底)，**不引入新的重复副作用面**。
- 流水线自身的「已处理」标记 **S1/S2/S3 全是 per-run 内存态、每 run 重置、有界**，**故意不持久化**——表达「**本次** run 是否已取证 / 已 nudge 几次」，跨 run / 跨重启残留反而是 bug。IDEM-4 专门验「重启后不残留」。
- 所有 WS 事件 **S6 无状态**，重发即重算，天然幂等。
- **失败重试**：预分析失败(intent_triage.llm_failed)被 safe-fail 接住 → 该轮降级裸 ReAct → 下一轮全新 run 自愈(per-run，不被上轮失败污染)。IDEM-5 专门验。

---

## 5. 幂等专项用例（IDEM-1 ~ IDEM-6，逐步 + 预期）

> 每条遵循 reviewer 幂等断言范式：**「跑两次/N 次，外部可观测副作用次数符合预期(恒定或每 turn 各 1 次)，无累积、无残留、无双写」**。

### IDEM-1 ★ 重复发同一闲聊 N 次 —— 短路决策幂等

**幂等命题（Y-light）**：deepseek 短路判定对同一输入确定性；连发同一闲聊 3 次，**每次各 1 次** `intent_triage.done problem_type=chitchat short_circuit=true` → 每次都 PRE-LOOP 整条短路，**不会因发过一次而累积 IN-LOOP 副作用，也不会偶发漏短路**。

**步骤**
1. 点「新话题」重置（确保干净窗口）。
2. **第 1 次**：发送 `"你好呀今天天气真好"`，截图 `IDEM-1-01.png`，等回完。
3. **第 2 次**：发**完全相同**内容，截图 `IDEM-1-02.png`，等回完。
4. **第 3 次**：再发相同内容，截图 `IDEM-1-03.png`，等回完。

**期望硬证据（幂等断言）**
1. `intent_triage.done problem_type=chitchat short_circuit=true` 在这 3 轮窗口内出现 **恰 3 次**（每轮各 1 次）。
2. `pipeline.short_circuit problem_type=chitchat` + `pipeline_short_circuit sid=...` 各 **恰 3 次**。
3. **关键幂等断言**：3 轮窗口内 `intent_triage.shortcircuit reason=chitchat_rule` = **0**（旧代码已删）；`intent_triage.llm_failed`/`parse_failed` = **0**。
4. **无残留串扰**：3 轮窗口内 **无** `evidence_gathered_set`/`evidence_gate.blocked`/`self_check.done`/`convergence.stop_loss`。
   > ⚠️ 机制澄清(实证 main.py:6867-6868)：闲聊短路分支**不 return**（只有澄清分支 :6894 才 return）；短路时 `evidence_gate=None` + `_pipe_problem_type=None`(:7204/:7206 附近) → IN-LOOP 三闸**全不接电** → 无任何闸 log。即"仍进 `_agent.run`，但闸被置 None 关掉"，不是"没进 loop"。可观测断言(无闸 log)不变。
5. 3 次回复都正常（闲聊式，不卡顿）；记每轮实测耗时（WI 闲聊 p95 ≤ 15s）。

**判定**：PASS = done(chitchat,sc=true)×3 + chitchat_rule=0 + llm_failed/parse_failed=0 + 无 IN-LOOP 闸残留 + 3 次正常回复。任一轮 short_circuit=false 误进 IN-LOOP / 冒 IN-LOOP 闸 log / 冒 chitchat_rule = FAIL。**★ 一票否决。**

---

### IDEM-2 (best-effort) 取证门控 per-run 重置（重发同一 debug，每轮门控独立武装）

> **best-effort 标注**：硬可观测的是 **S1**(`_evidence_gathered` per-run 重置)。
> ⚠️ **鉴别量修正(B1)**：`evidence_gathered_set` 受**双门控**——`tool ∉ _history_tool_names`(agent_loop.py:2420)。第 2 轮若模型**复用同名取证工具**(该名已在 history 快照)，则 `_evidence_gathered` **保持 False**(整个 if 不成立) → `evidence_gathered_set` 不打、且取证门**仍武装** → end_turn 时反而 `evidence_gate.blocked`。所以"第 2 轮无 `evidence_gathered_set`"**不等于** per-run 重置失效。改用「**门控是否武装**」作鉴别量：每轮只要出现 `evidence_gathered_set`(用了新工具) **或** `evidence_gate.blocked`/`exhausted`(门拦了)，即证该轮门控独立武装(flag=False 起跑)。

**幂等命题**：`_evidence_gathered`(S1) 与 nudge 计数(S2) 是 per-run；同一条需取证 debug 发 2 次，**每轮门控独立从 False 武装**——第 2 轮不因第 1 轮已取证而把门控置死(留 True 跳过)。

**步骤**
1. 点「新话题」重置。
2. **第 1 轮**：发送 `"我的导出功能突然报错了，你直接告诉我是什么原因吧"`，截图 `IDEM-2-01.png`，等回完。
3. **第 2 轮**：发**完全相同**内容，截图 `IDEM-2-02.png`，等回完。

**期望硬证据（门控武装鉴别量）**
1. **每轮门控独立武装**：两轮各自时间窗内，**至少出现** `evidence_gathered_set tool=<...>`（用了非 history 工具）**或** `evidence_gate.blocked`（门拦了未取证的下结论）之一 —— 证该轮 `_evidence_gathered` 从 False 起跑、门控真武装。
2. **nudge 每轮从 0 起**：若第 2 轮 `evidence_gate.blocked`，首次 `evidence_gate_nudge_injected nudge=1`（不接第 1 轮累计秒 exhausted）。
3. 放行后本轮不反复 block（有界收敛）。

**判定**：PASS = 两轮各自门控武装(各出现 `evidence_gathered_set` 或 `evidence_gate.blocked`) + nudge 每轮从 0。唯一 FAIL = **第 2 轮既无 `evidence_gathered_set`、又无 `evidence_gate.blocked`/`exhausted`，却直接给确定结论**（= 门控未武装/flag 残留 True）。两轮都不 block 且都用 history 同名工具 → 降级看截图确认模型确有调查动作，并报告注明「history 去重吞了 evidence_gathered_set」。

---

### IDEM-3 ★ 澄清持久化幂等（单 send 只写 1 条 + 重启后多轮不断裂不重复）

**幂等命题**：S4 每个 user turn 只写 1 条 assistant 澄清行；**重启后** history 重载仍含该澄清，用户答澄清 → 桌宠承接继续，**不重复生成澄清、不双写**。

> ✅ 重启可行性：companion 默认 `session_id="default"`(固定串)，重启前端重连仍落 `default` → 上轮澄清持久化重载进同一 history，可观测。

**步骤**
1. 点「新话题」重置。
2. **触发澄清**：发送 `"帮我搞一下那个东西"`，截图 `IDEM-3-01-vague.png`，等桌宠反问澄清，截图 `IDEM-3-02-clarify.png`。
3. **核对单 send 不双写**：`append_message` 不打 log → 两路判定：(a) **无** `clarify_persist_assistant_failed`；(b) 前端 UI **只渲染 1 条**澄清气泡(截图核对)。
4. **重启桌宠**：taskkill + 重启(env 同 §0.1)，等 `problem_pipeline_init enabled=true` 恢复。
5. **重启后答澄清(同会话 default)**：发送 `"我是说帮我把桌面上的报表整理成一张汇总表"`，截图 `IDEM-3-03-after-restart.png`，等回完。

**期望硬证据**
1. 首轮 `clarify=true` → `pipeline_clarification_pause`；前端只渲染 1 条澄清气泡(单 send 不双写)。
2. `clarify_persist_assistant_failed` 全程 = 0。
3. **重启后**：桌宠承接澄清继续（**不**重新反问同样澄清、**不**把答案当全新无关问题）→ 证 S4 持久化 + 重载 + 多轮不断裂。
4. 重启后**不再写一条重复澄清**。

**判定**：PASS = 单 send 写 1 条 + 持久化无失败 + 重启后承接继续(不断裂、不重复澄清)。重启后丢上下文重新反问 = 断裂(FAIL)；单 send 写 ≥2 条 = 双写(FAIL)。**★ 一票否决。best-effort**：澄清触发依赖 ambiguity≥0.7，触不出标 best-effort + 记实测值；但「单 send 不双写」「重启后不丢」一旦触发必须硬过。

---

### IDEM-4 ★ 进程重启后 per-run 标记不残留（干净起跑·状态隔离）

**幂等命题**：S1/S2/S3 的 per-run 内存标记**不跨进程持久化**；重启后第一条新消息从 `_evidence_gathered=False`/`nudges=0` 干净起跑，不被上次进程残留污染（取证门重新武装）。

> ⚠️ **鉴别量修正(B1，关键)**：直接断言"重启后 `evidence_gathered_set` 必重现，否则 FAIL"是**假 FAIL 陷阱**——重启后 `default` 会话重载 history（含上轮 tool 名）进 `_history_tool_names`(:712)，若模型复用同名取证工具，`evidence_gathered_set` **按设计不打**（`_evidence_gathered` 反而保持 False、门控仍武装）。改用「**门控是否武装**」鉴别：重启后 debug 轮只要出现 `evidence_gathered_set` **或** `evidence_gate.blocked`/`exhausted`，即证 `_evidence_gathered` 在新进程 False 起跑、门控真武装（= per-run 标记没跨进程残留）。**为提高 `evidence_gathered_set` 正向重现概率**，重启后用**不同取证途径**的问题（诱导用 history 里没出现过的工具）。

**步骤**
1. **重启前埋状态**：发 debug `"帮我看下我项目里登录为什么失败，先读代码查清楚"`（诱导 read/grep），截图 `IDEM-4-01-pre.png`，等回完，确认 log 出现 `evidence_gathered_set`(本 run 置位 S1)。
2. **重启桌宠**：taskkill + 重启(env 同 §0.1)，等 `problem_pipeline_init enabled=true`。
3. **重启后发需重新取证的 debug**（发 debug 非闲聊，才真能鉴别状态泄漏；尽量换取证途径）：发送 `"再帮我调研一下这个项目用的加密库最近有没有已知漏洞，先查证"`（诱导 web_search/deepresearch 等不同工具），截图 `IDEM-4-02-post.png`，等回完。

**期望硬证据（门控武装鉴别量）**
1. **关键幂等断言**：重启后这条 debug 轮出现 `evidence_gathered_set tool=<...>`（用了新工具）**或** `evidence_gate.blocked`/`exhausted`（门拦了未取证下结论）之一 → 证 `_evidence_gathered` 在新进程 False 起跑、门控重新武装；**不是**因上次进程残留 True 而**取证门彻底跳过**。
2. nudge 若 block 则首次 `nudge=1`(不接上次进程累计)。
3. 桌宠基于取证正常回复。

**判定**：PASS = 重启后 debug 轮门控武装(出现 `evidence_gathered_set` 或 `evidence_gate.blocked`/`exhausted`) + nudge 从 0 + 正常回复。唯一 FAIL = 重启后这条 debug(needs_investigation=true) **既无 `evidence_gathered_set`、又无 `evidence_gate.blocked`/`exhausted`，却直接给确定结论** → 取证门未武装 = 跨进程状态泄漏。**★ 一票否决。**

**说明**：本 TC 与 IDEM-3 互为对照——**该持久化的(对话 history)持久化了、不该持久化的(per-run 闸标记)没残留**，才是正确的副作用边界。

---

### IDEM-5 ★ 预分析失败可重试自愈（不卡死·不污染后续）

**幂等命题**：预分析 LLM 失败被 safe-fail 接住 → 该轮降级裸 ReAct 仍回复；**修复后下一轮全新 run 自愈**，失败那条不被永久误杀、不污染后续。（失败轮 = TC-10 场景。）

**步骤**
1. （构造）config 设坏 `analysis_model=gpt-does-not-exist`，重启，确认装配 log。
2. **失败轮**：发送 `"帮我分析为什么这段排序代码会越界报错"`，截图 `IDEM-5-01-fail.png`，等回完。
3. **还原 config**：删坏 override(或留空=主 LLM)，**重启**，确认装配 log。
4. **自愈轮**：发同类非闲聊 `"帮我分析为什么这段查找代码结果不对"`，截图 `IDEM-5-02-heal.png`，等回完。

**期望硬证据**
1. 失败轮：`intent_triage.llm_failed error=...` + 桌宠**仍正常回复** + 无未捕获 traceback。
2. 失败轮 safe-fail 返回保守 card → **不**冒 `pipeline_clarification_pause`/`chat_v2_contradiction`（失败不乱触发澄清/矛盾）。
   > 失败轮可能正常出 `evidence_*`（safe-fail 仍置 needs_investigation），**不算 FAIL**。
3. **自愈轮**(还原后)：`intent_triage.done problem_type=<...>` 正常出 + 桌宠正常回复。
4. 失败轮不卡死不留半截状态；自愈轮完全不带失败轮残留(per-run 干净)。

**判定**：PASS = 失败轮 safe-fail 接住仍回复不卡死 + 不乱触发澄清/矛盾 + 还原后自愈轮 done 正常 + 无跨轮污染。预分析失败导致整轮挂掉/无回复/traceback = FAIL；或失败轮冒 clarification_pause/chat_v2_contradiction = FAIL。**★ 一票否决。测后还原**：必还原 analysis_model 重启确认。

---

### IDEM-6 收敛止损报告幂等（每触顶 1 份，不刷屏）

**幂等命题**：S7 止损报告每触顶 run 出 1 份诚实报告(不每轮刷屏、不累积)。

> ⚠️ **C2 实证**：`ConvergenceController` 无任何跨 run 可变状态(仅 `_gate`/`_report_on_stop` 两个 init 字段)，`_verdict` 每 run 局部新构造 → "会话级 stop_reason 残留"架构上不可能 → 该断言不可证伪，本 TC 只保留可证伪的「每触顶 1 份不刷屏」为硬断言。
> 构造路径 = TC-5（改 `main.py:6597` `else 4`，走 max_iterations range 耗尽出口 agent_loop.py:2575-2606，**非 allows_call**）。**触发 best-effort**(同 TC-5 M2)：仅模型连续打满 4 轮 tool_use 才到达；未触发则记实测轮数、不作 FAIL。

**步骤**（合并 TC-5 构造）
1. （构造）改 `main.py:6597` `else 16`→`else 4`，重启。
2. **触顶轮**：发送 `"帮我把这个含 5 个互相依赖、信息都缺失的子问题彻底解决，每个都要查证"`，截图 `IDEM-6-01.png`，等触顶止损。
3. **旁证·简单轮**：**还原 main.py:6597 重启**；发 `"1+1 等于几"`，截图 `IDEM-6-02.png`，等回完。

**期望硬证据**
1. **(硬断言)** 触顶轮：`convergence.stop_loss reason=error_max_turns ...`，桌宠出诚实止损报告，**该 run 内 stop_loss 恰 1 次**（不每轮重复刷 `<收敛>`）。
2. **(旁证)** 简单轮正常答 "2"，无 `convergence.stop_loss`。

**判定**：PASS = 触顶 1 份 converged=false 诚实报告(不刷屏) + 没假装完成。止损报告每轮重复刷(同 run 多次 stop_loss) = 累积(FAIL)；假装"已完成" / `stop_reason=budget` = FAIL。**诚实标注(HARD)**：报告写明构造方式 + 实际 stop_reason。**测后还原** `main.py:6597`。

---

## 6. 执行优化（合并构造，避免重复重启）

| 合并 | 共享构造 | 分别按各自判定核对 |
|---|---|---|
| TC-10 + IDEM-5 失败轮 | 坏 analysis_model 重启 | TC-10(safe-fail 不卡死) + IDEM-5(失败→还原自愈) |
| TC-5 + IDEM-6 触顶轮 | 改 main.py:6597 `else 4` 重启 | TC-5(诚实止损报告) + IDEM-6(每触顶 1 份不刷屏) |
| TC-6 + IDEM-3 | 触发澄清 | TC-6(多轮不断裂) + IDEM-3(单 send 不双写 + 重启后不丢) |
| TC-2 + IDEM-2 | 同一 debug 重发 | TC-2(取证门拦截) + IDEM-2(per-run 重置) |

---

## 7. 结果汇总表（执行后填）

### 7.1 功能正确性

| case | ★ | 坐标 | 动作摘要 | 截图 | log 证据(关键计数) | 判定 |
|---|---|---|---|---|---|---|
| TC-1 | ★ | | | | done(chitchat,sc=true) ___×、chitchat_rule ___(应=0)、非闲聊 sc=false ___ | |
| TC-2 | ★ | | | | evidence_gathered_set ___、blocked ___ | |
| TC-3 | | | | | has_contradiction=true ___、principal/attack_order ___ | |
| TC-4 | | | | | self_check.done mode=strict ___、heterogeneous=___ | |
| TC-5 | ★ | | | | stop_loss reason=___（应=error_max_turns）| |
| TC-6 | | | | | clarify=true→pause ___、承接 ___ | |
| TC-7 | | | | | 各条 done ___×（应各 1）| |
| TC-8 | | | | | code 轮 pipeline log ___(应=0) | |
| TC-9 | ★ | | | | enabled=false 期 pipeline log ___(应=0) | |
| TC-10 | | | | | llm_failed ___、仍回复 ___ | |

### 7.2 幂等专项

| case | ★ | 坐标 | 动作摘要 | 截图 | log 证据(关键计数) | 判定 |
|---|---|---|---|---|---|---|
| IDEM-1 | ★ | | | | done(chitchat,sc=true) ___×、chitchat_rule ___(=0)、IN-LOOP 残留 ___ | |
| IDEM-2 | | | | | 各轮 evidence_gathered_set ___ | |
| IDEM-3 | ★ | | | | UI 澄清气泡 ___条、persist_failed ___、重启后承接 ___ | |
| IDEM-4 | ★ | | | | 重启后 debug 轮 evidence_gathered_set ___ | |
| IDEM-5 | ★ | | | | llm_failed→还原后 done ___ | |
| IDEM-6 | | | | | stop_loss ___×、stop_reason=___ | |

---

## 8. 上线通过线（Production Ship Gate）

- **★ 必过(任一 FAIL = 不可上线)**：功能 **TC-1 / TC-2 / TC-9** + 幂等 **IDEM-1 / IDEM-3 / IDEM-4 / IDEM-5**。
  - TC-10(safe-fail 不卡死) 上线判定并入 IDEM-5 ★（IDEM-5 失败轮 = TC-10 场景）。
  - TC-5/IDEM-6 的 stop_loss 上线判定**仅认构造 (a)**（改源码 main.py:6597 触 error_max_turns）；hallucination 构造不产出 stop_loss，不可作判定依据。
- TC-5/IDEM-6(止损) 与 TC-7b(WI-4-C 旁证) 触发 best-effort：触发则按各自判定核对，未触发记实测值标 best-effort，**不作 ship 阻断**。其余非 ★ 用例 FAIL 按严重度决定是否阻断上线。
- best-effort / env-limited 的 TC 须**显式标注理由 + 实测值**，不允许伪造触发。
- 全绿(或 ★ 全 PASS + 非 ★ 受控标注清楚)后：更新 `STATUS/status.md` §3 模块行 + §4 里程碑；证据存
  `plans/manual-results-2026-06-25-problem-pipeline-ship/screenshots/`，本目录只放用例定义。

---

## 9. 汇总

| 项 | 值 |
|---|---|
| 功能用例 | 11（TC-1~10 + TC-7b），ship 必过 ★3(TC-1/2/9)；TC-5 功能重要但**触发 best-effort**(依赖 LLM 打满 4 轮)不作 ship 阻断 |
| 幂等专项用例 | 6（IDEM-1~6），★4 必过(IDEM-1/3/4/5) |
| 上线 ★ 必过合计 | 7（功能 TC-1/2/9 + 幂等 IDEM-1/3/4/5）；TC-5/IDEM-6 止损为条件式 best-effort、TC-7b WI-4-C 旁证 best-effort，均不作 ship 阻断 |
| 是否需 windows-mcp 真测 | **是**（每例真坐标点击 + 真中文输入 + 截图 + log grep；禁 WS 直注/pytest/import 当 UI 证据）|
| 需改 config 重启的用例 | TC-9(enabled=false)/TC-10+IDEM-5(坏 analysis_model)——测后均须还原 |
| 需改源码重启的用例 | TC-5/IDEM-6：**唯一可达 stop_loss = 改 main.py:6597 `else 4` 触 error_max_turns**——测后还原源码 |
| 需进程重启的幂等用例 | IDEM-3(验持久化)/IDEM-4(验状态隔离)/IDEM-5(还原重启)/IDEM-6(还原重启)/TC-9 |
| best-effort/env-limited | TC-2/3/4/6(依赖 LLM 行为)/TC-8(code 入口可达)/IDEM-2(gate 是否 block)/IDEM-3 澄清触发(歧义≥0.7) |
