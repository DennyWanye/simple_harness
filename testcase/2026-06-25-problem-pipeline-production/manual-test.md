# 七步问题处理流水线 — 生产上线验收 + 幂等/副作用专项手工测试

> **被测功能**：「七步问题处理流水线」(ProblemHandlingPipeline，毛选方法论，仅 Companion 主线)。
> Step1+3 预分析(意图+主要矛盾，合并 1 次 LLM，决策4) → Step2 取证门控 → Step4 弹钢琴定方案 →
> Step5 执行 → Step6 异体自检 → Step7 收敛止损。
>
> **本文档定位**：**生产上线验收 (production-acceptance)** —— **通过本文档 = 可上生产环境**。
> 在功能正确性之上，专项加测**「遍历 + 写副作用」幂等性**：流水线被重复调用 / 重进会话 / 进程重启后，
> 会不会重复产生副作用？有没有「已处理」标记？标记持久化了吗？失败的那条还能重试吗？有没有幂等断言？
>
> **与功能 doc 的关系**：核心功能逐步验收见姊妹文档
> [`../2026-06-24-problem-pipeline-maoxuan/manual-test.md`](../2026-06-24-problem-pipeline-maoxuan/manual-test.md)
> (TC-0~TC-10，11 例)。**本文档不重复那 11 例的细节**，而是：
> ① §2 给出**副作用/幂等地图**(代码实证)；② §3 给出 **IDEM-1~6 幂等专项用例**(全步骤，本文档独有的上线门)；
> ③ §4 把功能 doc 的 ★ 必过项收口成**上线 checklist**(一句话步骤 + 判定，详情指向功能 doc)。
> **两份文档一起跑全绿 = 上线门通过。**
>
> **对应 plan**：[plans/2026-06-24-problem-handling-pipeline-maoxuan/](../../plans/2026-06-24-problem-handling-pipeline-maoxuan/)
> **最后更新**：2026-06-25

---

## 0. 测试前置（HARD — 不满足则结果作废）

> 完整前置(进程清场 / 跑 worktree 非 frozen / 登录 key / windows-mcp 真测纪律)见功能 doc §0，此处只列**上线验收专属强约束**。

### 0.1 环境（同功能 doc §0.1~0.3，必须先满足）

1. **进程清场**：`taskkill /F /IM deskpet.exe` + 杀残留 Vite（项目坑 #1）。**不要手动起 backend / vite**（坑 #7/#9，Tauri 自管）。
2. **跑 worktree 代码**：只给 Tauri 进程注入 env：
   `DESKPET_BACKEND_DIR=G:\projects\deskpet\backend` / `DESKPET_PYTHON=G:\projects\deskpet\backend\.venv\Scripts\python.exe` /
   `DESKPET_DEV_MODE=1` / `DESKPET_USER_DATA_DIR=G:\projects\deskpet\backend\userdata`。
   启动后 log **必须**出现 `[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`；
   若见 `Bundled exe=...` → 跑的是旧 frozen，**测了等于白测，立即停下重配**。
3. **流水线装上**：grep `problem_pipeline_init enabled=true intent=... evidence=... self_check=...`(main.py:**1735**)。grep 不到 → 全部用例无意义。
4. **日志落盘**：启动命令把输出重定向到 `plans/manual-results-2026-06-25-problem-pipeline-prod/tauri-dev.log`，所有 log 证据 grep 这份。

### 0.2 windows-mcp 真测纪律（HARD CONSTRAINT — 不可妥协）

1. **真模拟人**：每个 case 必 Screenshot/Snapshot → **真坐标点击 / 真键盘(剪贴板)输入** → 截图验证 → 日志判 PASS/FAIL。
2. **禁绕过**：不允许用 `ws://127.0.0.1:8100/*` WebSocket 直注、`pytest`、`import` backend 查 registry、文件/keychain 存在性、
   log-only grep **替代**真点击作为 UI 证据。**log grep 只是判定辅助，必须配真截图 + 真 UI 操作链路。**
3. **每个动作前 declare**：`坐标=(x,y) | 动作=click/type/restart | 期望=...`。
4. **截图存盘**：`plans/manual-results-2026-06-25-problem-pipeline-prod/screenshots/<case-id>-NN-*.png`。
5. **失败 retry ≥ 3 次不同 workaround** 才能标「环境受限」；**跳过任何 case** 必须显式声明 + 具体理由 + **等用户确认**。
6. **中文输入 workaround**：windows-mcp `Type`(UIA SetValue) 实测可靠；或 STA Runspace + `Clipboard.SetText("中文")` + 焦点在输入框后 Ctrl+V；
   **发送**用 SendInput 真点「发送」按钮(WebView2 不响应老式 mouse_event)。

### 0.3 ★ 上线一票否决项（任一 FAIL = 不可上线）

| Case | 验收点 | 类别 |
|---|---|---|
| **§4 TC-1** | 闲聊纯规则短路 0 次 LLM，不拖慢 | 功能(性能红线) |
| **§4 TC-9** | kill-switch(enabled=false) 零回归 BC | 功能(BC 守护) |
| **IDEM-1** | 重复发同一闲聊 N 次，预分析 LLM 副作用恒 = 0 | **幂等** |
| **IDEM-3** | 澄清持久化单次只写 1 条 + 重启后多轮不断裂不重复 | **幂等(持久化)** |
| **IDEM-4** | 进程重启后 per-run 标记不残留(干净起跑) | **幂等(状态隔离)** |
| **IDEM-5** | 预分析失败可重试自愈，不卡死、不污染后续 | **幂等(失败重试)** |

任一 ★ FAIL → 不算完成，回 plan 修。

---

## 1. 真实日志锚点速查（执行时 grep 用，已核实源码行号）

| 步 | 锚点字符串 | 代码位置 | 含义 |
|---|---|---|---|
| 装配 | `problem_pipeline_init enabled=true ...` | main.py:1735 | 编排器构造成功 |
| Step1+3 | `intent_triage.shortcircuit reason=chitchat_rule task_type=<...>` | intent_triage.py:189 | 闲聊纯规则短路(0 次 LLM) |
| Step1+3 | `intent_triage.done problem_type=<...> ambiguity=<f> ... has_contradiction=<bool>` | intent_triage.py:224 | 非闲聊调 1 次 LLM 出意图+矛盾 |
| Step1+3 | `intent_triage.llm_failed error=...`（logger.**warning**） | intent_triage.py:211 | 预分析失败 → safe-fail 降级裸 ReAct |
| PRE-LOOP | `pipeline_short_circuit sid=<...>` | main.py:6847 | 闲聊整条短路，不进 IN-LOOP |
| PRE-LOOP | `pipeline_clarification_pause sid=<...>` | main.py:6872 | needs_clarification → 暂停反问 |
| PRE-LOOP | `clarify_persist_assistant_failed sid=<...>` | main.py:6870 | 澄清持久化失败(bug 信号，正常应为 0) |
| Step2 | `evidence_gathered_set sid=<...> tool=<name>` | agent_loop.py:2393 | **本 run 内**首次命中取证工具，置 `_evidence_gathered=True` |
| Step2 | `evidence_gate.blocked nudges_used=<n>` | evidence_gate.py:88 | 未取证就下结论被拦 |
| Step2 | `evidence_gate_nudge_injected sid=<...> nudge=<n>` | agent_loop.py:1516 | `<调查>` nudge 注入（per-run 计数，nudge 从 1 起算）|
| Step2 | `evidence_gate_exhausted nudges_used=<n>` | evidence_gate.py:77 | 超 max_nudges 放行(防死循环) |
| Step6 | `self_check.done mode=<...> passed=<bool> heterogeneous=<bool> unmatched=<n>` | self_check_gate.py:128 | 异体自检结论 |
| Step6 | `self_check_nudge_injected sid=<...> mode=<...> nudge=<n>` | agent_loop.py:1645 | `<自检>` 反思注入（per-run 计数）|
| Step7 | `convergence.stop_loss reason=<...> principal_resolved=<bool> unverified=<n>` | convergence_controller.py:71 | 资源触顶 → 诚实止损 |

> WS 事件(`chat_v2_intent/contradiction/evidence_gate/selfcheck/convergence`)是**瞬态广播**，判定主依据是 backend log + 截图。

---

## 2. 副作用 / 幂等地图（代码实证 —— 上线评审硬要求）

> reviewer 对每个「遍历 + 写副作用」逐条问的 5 问，下表逐点给出代码层答案。**这是 IDEM-1~6 的设计依据。**

| # | 写副作用点 | 代码位置 | 副作用类型 | ①「已处理」标记 | ②持久化(重启后还认得)? | ③重复调用/重进会重复产生副作用吗? | ④失败那条还能重试吗? | 对应幂等用例 |
|---|---|---|---|---|---|---|---|---|
| **S1** | 置 `_evidence_gathered=True` | agent_loop.py:2392 | 内存布尔(per-run) | `self._evidence_gathered` + 守卫 `not self._evidence_gathered`(:2388) | **否**(by design)：`run()` 起 :710-711 重置 | **否**：置一次后守卫挡住，本 run 不重置；每 run 重新起算 | N/A(纯内存，无外部副作用) | IDEM-2 / IDEM-4 |
| **S2** | evidence nudge 注入 + 计数 | agent_loop.py:1507 | 内存计数(per-run，**有界**) | `_evidence_nudges_used < max_nudges`，超限 :76 `exhausted` 放行 | 否(:710-711 重置) | **否**：有界 max_nudges(默认2)防死循环；每 run 从 0 起 | N/A | IDEM-2 |
| **S3** | self_check/verify nudge 计数 | agent_loop.py:1635 | 内存计数(per-run，**有界**) | `verify_nudges_used < max_verify_nudges` | 否(:753-755 本轮起算) | **否**：有界；每 run 从 0 起 | N/A | IDEM-4 |
| **S4** | **持久化澄清 assistant 行** | main.py:6865 `append_message`（**注：该调用本身不打 log**） | **DB 写(持久化)** | 无 dedup key —— **靠「每轮一条」turn 语义**(对齐 FinalEvent 持久化) | **是**：写 session_db，重启重载进 history | **每个 user turn 写 1 条**(语义正确，非重复 bug)；**同一次 send 不双写**(单条 await) | 失败记 `clarify_persist_assistant_failed`，**不卡死**(已 try/except) | **IDEM-3** |
| **S5** | 持久化 user 消息 | main.py:6184 `append_message` | DB 写(持久化) | `_user_msg_id`(既有链路，非流水线新增) | 是 | 每 turn 写 1 条(既有 chat 语义) | — | IDEM-3 旁证 |
| **S6** | `chat_v2_intent/contradiction/convergence` WS 事件 + **peer 广播** | problem_pipeline.py:76/102，agent_loop.py:891/2171；peer fan-out main.py:6845 `_broadcast_default_chat_peers` | 瞬态广播 | 无(无状态 emit) | 否 | **否**：无状态，重发即重新计算并重发，不累积；**多连接 N → 每事件 N 份是设计(fan-out)非重复 bug** | N/A | IDEM-1 旁证 |
| **S7** | 收敛止损报告 | agent_loop.py:891/2171 emit + :897 FinalEvent(stop_reason="stop_loss") | 瞬态(随 final answer 落库) | `should_stop_loss` 每 run 判一次；ConvergenceController **无任何跨 run 可变状态**(controller 全文无 self 状态) | 报告文本随 final answer 持久化(每 run 1 次) | **否**：每触顶 run 出 1 份，不每轮刷屏；**结构上无跨 run 残留面** | — | IDEM-6 |

### 2.1 上线评审结论（一句话）

- **唯一新增的持久化写副作用 = S4(澄清持久化)**，且它**复用既有 per-turn 持久化语义**(每轮一条、单 send 不双写、失败有兜底)，**不引入新的重复副作用面**。
- 流水线自身的「已处理」标记 **S1/S2/S3 全是 per-run 内存态、每 run 重置、有界**，**故意不持久化**——它们表达「**本次** run 是否已取证 / 已 nudge 几次」，跨 run / 跨重启残留反而是 bug。IDEM-4 专门验「重启后不残留」。
- 所有 WS 事件 **S6 无状态**，重发即重算，天然幂等。
- **失败重试**：预分析失败(S 链路 intent_triage.llm_failed)被 safe-fail 接住 → 该轮降级裸 ReAct → **下一轮全新 run 自愈**(per-run，不被上轮失败污染)。IDEM-5 专门验。

---

## 3. IDEM 幂等专项用例（本文档独有上线门 —— 全步骤 + 预期）

> 每条遵循 reviewer 幂等断言范式：**「跑两次/N 次，外部可观测副作用次数符合预期(恒定或每 turn 各 1 次)，无累积、无残留、无双写」**。
> 坐标占位 `(x,y)` 按真机 Snapshot 实测填，报告写真实物理像素。

---

### IDEM-1 ★ 重复发同一闲聊 N 次 —— 预分析 LLM 副作用恒 = 0（短路无累积）

**幂等命题**：闲聊纯规则短路是无状态的；连发同一条闲聊 3 次，**每次都 0 次预分析 LLM**，不会因「发过一次」而产生任何累积副作用，也不会偶发漏短路。
> 鉴别力说明(m-1)：短路是**纯规则无状态**(intent_triage.py:188-198，`derived_pt=="chitchat"` 直接 return，不碰 LLM、无任何 `self.` 可变态)，**结构上不存在「第 N 次才漏」的状态面**——抽样 3 次是佐证而非「证明恒等」；鉴别力来自「任一次冒 `intent_triage.done` 即抓到回归」。

**前置**：§0 满足；`problem_pipeline_init enabled=true` 已确认。

**步骤**
1. `坐标=(输入框 x,y) | 动作=click 聚焦`
2. **第 1 次**：`动作=Type/粘贴 "你好呀今天天气真好" → SendInput 点「发送」 | 期望=消息发出 + 桌宠闲聊回复`，截图 `IDEM-1-01-send1.png`，等回完。
3. **第 2 次**：发**完全相同**的 "你好呀今天天气真好"，截图 `IDEM-1-02-send2.png`，等回完。
4. **第 3 次**：再发相同内容，截图 `IDEM-1-03-send3.png`，等回完。

**期望硬证据（幂等断言）**
1. log 中 `intent_triage.shortcircuit reason=chitchat_rule` 出现 **恰 3 次**（每轮各 1 次，每轮都短路）。
2. log 中 `pipeline_short_circuit sid=<...>` 出现 **恰 3 次**。
3. **关键幂等断言**：这 3 轮窗口内 `intent_triage.done` 计数 = **0**、`intent_triage.llm_failed` = **0**、
   `chat_v2_intent` / `chat_v2_contradiction` = **0**（**预分析 LLM 副作用恒 = 0，跑 N 次不变**）。
4. **无残留串扰**：这 3 轮窗口内 **无** `evidence_gathered_set` / `evidence_gate.blocked` / `self_check.done` / `convergence.stop_loss`
   （闲聊在 PRE-LOOP 即 `pipeline_short_circuit` 后 `return`，**根本不进 IN-LOOP**，故所有 IN-LOOP 闸 log 必为 0；重复发也不会「攒出」一次）。
5. 3 次桌宠回复都正常（闲聊式，不卡顿、不报错）。

**判定**：PASS = shortcircuit×3 + done/llm_failed/intent 事件全 0 + 无 IN-LOOP 闸残留 + 3 次都正常回复。
任一轮漏短路(冒 `intent_triage.done`)或出现累积 IN-LOOP 副作用 = FAIL。**★ 一票否决。**

**能逼出的 bug**：短路逻辑带隐藏状态(如「第 N 次才短路」)、闲聊误触发 IN-LOOP 闸、重复消息被误判成「上下文复杂」而退出短路。

---

### IDEM-2（best-effort）取证门控 per-run 重置（重发同一 debug，S1 必验 / S2 大概率观测不到）

> **整体 best-effort 标注(M-2)**：本 TC 真正硬可观测的是 **S1(`_evidence_gathered` per-run 重置)**；**S2(nudge 计数重置)** 只在 gate 真 block 时才有正向 log，
> 而取证工具命中即置位 + 模型通常先取证 → **gate 两轮都不 block 是大概率事件**，故 S2 重置在真机**多半观测不到**，落到下方降级断言。本 TC 不作 ★ 一票否决。

**幂等命题**：`_evidence_gathered`(S1) 与 evidence nudge 计数(S2) 是 per-run 的；同一条需取证的 debug 问题发 2 次，
**每轮独立从零起算**——第 2 轮不会因第 1 轮已取证而跳过取证(S1)，若两轮都被 block 则 nudge 计数不跨轮累积(S2)。

**前置**：§0 满足。问题要 needs_investigation(信息不足、诱导直接下结论)。

**步骤**
1. `动作=click 聚焦`
2. **第 1 轮**：`动作=粘贴 "我的导出功能突然报错了，你直接告诉我是什么原因吧" → 发送`，截图 `IDEM-2-01-r1.png`，等回完。
3. **第 2 轮**：发**完全相同**的同一句，截图 `IDEM-2-02-r2.png`，等回完。

**期望硬证据（幂等断言）**
1. **每轮独立取证**：`evidence_gathered_set sid=<...> tool=<...>` 在**两轮各自的时间窗内都出现**（第 2 轮没有因为第 1 轮已置位而跳过——
   证明 `_evidence_gathered` 已随 `run()` 重置，:710-711）。
2. **nudge 计数每轮从 0 起**：若两轮都触发 `evidence_gate.blocked`，则第 2 轮 grep `evidence_gate_nudge_injected sid=<...> nudge=<n>`(agent_loop.py:1516) **首次 `nudge=1`**（证明计数从 0 重新起算，不接着第 1 轮累计冲破 max_nudges → 第 2 轮不应一上来就 `evidence_gate_exhausted`）。
   > ⚠️ 注：`evidence_gate_nudge_injected` 仅在模型**真想不取证下结论被拦**时才出。若 gate 两轮都不 block（模型本就先取证），则 S2 nudge 重置**无正向 log 可证**——此时降级到断言 1（每轮各自 `evidence_gathered_set`，证 S1 per-run 重置），并在报告注明「gate 未 block，S2 nudge 重置无法独立观测」。
3. **放行后本轮不反复 block**：每轮一旦 `evidence_gathered_set`，该轮后续 `evidence_gate` 不再 block（有界、收敛）。
4. 两轮桌宠都基于取证结果回答(非凭空猜)。

**判定**：PASS = 两轮各自独立取证 + nudge 每轮从 0 起算(第 2 轮不被上轮污染成秒 exhausted) + 每轮放行后不反复 block。
若第 2 轮直接跳过取证 / 一上来就 exhausted = per-run 重置失效(状态泄漏，FAIL)。

**best-effort 标注**：是否 `evidence_gate.blocked` 依赖 LLM 首轮**是否真想直接下结论**(同功能 doc TC-2)。若模型两轮都先取证(gate 不 block)，
则降级断言为：**两轮都各自出现 `evidence_gathered_set`(per-run 各取证一次)即证明 per-run 语义成立**；唯一 FAIL = 第 2 轮未取证就给结论且 gate 没拦。

---

### IDEM-3 ★ 澄清持久化幂等（单 send 只写 1 条 + 重启后多轮不断裂不重复）

**幂等命题**：S4(澄清持久化 main.py:6865) 每个 user turn 只写 1 条 assistant 澄清行；**重启后** history 重载仍含该澄清，
用户答澄清 → 桌宠承接继续(多轮不断裂)，**不会重复生成澄清、不会双写**。

**前置**：§0 满足。问题要足够歧义(ambiguity_score ≥ 0.7 默认阈值)。
> ✅ 重启可行性已核实(m-3)：companion 默认 `session_id="default"`(main.py:4630 固定串)，重启前端重连仍落 `default` → 上轮澄清持久化(写 session_db)重载进同一 history，**可观测**。

**步骤**
1. `动作=click 聚焦`
2. **触发澄清**：`动作=粘贴 "帮我搞一下那个东西" → 发送`，截图 `IDEM-3-01-vague.png`，等桌宠**反问澄清**，截图 `IDEM-3-02-clarify.png`。
3. **核对单 send 不双写**：⚠️ `append_message` 调用本身**不打 log**，无法直接 grep 计数。改用两路判定：(a) **无** `clarify_persist_assistant_failed`(持久化没出错)；(b) **前端 UI 只渲染 1 条澄清气泡**(截图 `IDEM-3-02-clarify.png` 核对，不是两条重复澄清)。
4. **重启桌宠**：`动作=restart`（taskkill deskpet.exe + 重启 Tauri，env 同 §0.1）。等 `problem_pipeline_init enabled=true` 恢复。
5. **重启后答澄清(同会话)**：`动作=粘贴 "我是说帮我把桌面上的报表整理成一张汇总表" → 发送`，截图 `IDEM-3-03-answer-after-restart.png`，等回完，截图 `IDEM-3-04-continue.png`。

**期望硬证据（幂等断言）**
1. 首轮：`intent_triage.done ... clarify=true` → `pipeline_clarification_pause sid=<...>`；**前端只渲染 1 条澄清气泡**(单 send 不双写，UI 截图核对；append_message 不打 log 故不可 grep 计数)。
2. **`clarify_persist_assistant_failed` 全程 = 0**(持久化成功)。
3. **重启后**：第二轮桌宠**承接澄清**继续推进——**不再重新反问同样的澄清**、**不把答案当全新无关问题**
   （证明澄清 assistant 行 S4 已持久化、重启重载进 history、多轮不断裂）。
4. 第二轮**不会再写一条重复澄清**(不是又触发一次 clarification_pause)；若第二轮仍判歧义那是模型判断问题，但**不应是「重启丢了上轮澄清 → 重新问一遍」**。

**判定**：PASS = 首轮澄清单 send 写 1 条(不双写) + 持久化无失败 + **重启后**承接继续(多轮不断裂、不重复澄清)。
若重启后丢上下文重新反问 = 持久化/重载断裂(FAIL)；若单 send 写 ≥2 条 assistant 澄清 = 双写(FAIL)。**★ 一票否决。**

**best-effort 标注**：澄清触发依赖 LLM 判 ambiguity≥0.7(同功能 doc TC-6)。若多次重试("弄一下"/"处理那个")都触不出 `clarify=true`，标 best-effort 记实测 ambiguity 值；
**但「单 send 不双写」与「重启后 history 不丢」这两条幂等断言一旦澄清触发就必须硬过。**

---

### IDEM-4 ★ 进程重启后 per-run 标记不残留（干净起跑，状态隔离）

**幂等命题**：S1/S2/S3 的 per-run 内存标记**不跨进程持久化**；重启后第一条新消息从 `_evidence_gathered=False` / `nudges=0` 干净起跑，
**不被上次进程 run 的残留污染**。

**前置**：§0 满足。

**步骤**
1. **重启前埋状态**：发一条会触发取证的 debug 问题 `"帮我看下我项目里登录为什么失败，先查清楚"`，截图 `IDEM-4-01-pre.png`，
   等回完，确认 log 出现 `evidence_gathered_set`(本 run 已置位 S1)。
2. **重启桌宠**：`动作=restart`（taskkill + 重启，env 同 §0.1），等 `problem_pipeline_init enabled=true` 恢复。
3. **重启后发一条「需重新取证的 debug 问题」**(m-2 修正：发 debug 而非闲聊，才真能鉴别状态泄漏——闲聊根本不读那些标记)：
   `动作=粘贴 "再帮我看下这个项目导出 Excel 为什么乱码，先查清楚" → 发送`，截图 `IDEM-4-02-post-debug.png`，等回完。

**期望硬证据（幂等断言）**
1. **关键幂等断言**：重启后这条 debug 轮**重新出现 `evidence_gathered_set sid=<...> tool=<...>`**——证明 `_evidence_gathered` 在新进程里是 `False` 起跑、**重新触发了取证**；
   **不是**因为上次进程残留了 `True` 而**跳过取证**(若跨进程残留，这条 debug 会直接不取证就下结论、无 `evidence_gathered_set`)。
2. **nudge 计数从 0 起**：若该轮 `evidence_gate.blocked`，`evidence_gate_nudge_injected` 首次 `nudge=1`(不接着上次进程累计)。
3. 桌宠基于取证结果正常回复。

**判定**：PASS = 重启后 debug 轮**重新取证**(出现 `evidence_gathered_set`，非跳过) + nudge 从 0 起 + 正常回复 —— 证明 per-run 内存态随进程销毁、新进程干净起跑。
若重启后这条 debug **跳过取证直接下结论**(无 `evidence_gathered_set` 却给了结论) = 跨进程状态泄漏(FAIL)。**★ 一票否决。**

**说明**：本 TC 验的是「per-run 标记**故意不持久化**」这一设计的正确性——它和 IDEM-3(澄清**应当**持久化)互为对照：
**该持久化的(对话 history)持久化了、不该持久化的(per-run 闸标记)没残留**，这才是正确的副作用边界。

---

### IDEM-5 ★ 预分析失败可重试自愈（不卡死、不污染后续）

**幂等命题**：预分析 LLM 失败被 safe-fail 接住 → 该轮降级裸 ReAct 仍正常回复；**修复后下一轮全新 run 自愈**，
失败的那条**不被永久误杀**、**不污染后续轮**。

**前置**：§0 满足。**构造预分析失败**：改 dev config `[features.problem_pipeline].analysis_model` 为不存在的 model 名(如 `gpt-does-not-exist`)，重启。

**步骤**
1. （构造）config 设坏 analysis_model 重启，确认装配 log。
2. **失败轮**：`动作=粘贴 "帮我分析为什么这段排序代码会越界报错" → 发送`(非闲聊会触发预分析)，截图 `IDEM-5-01-fail.png`，等回完。
3. **还原 config**：删掉坏 analysis_model override(或还原留空=主 LLM)，**重启**，确认装配 log。
4. **自愈轮**：发**同类**非闲聊问题 `"帮我分析为什么这段查找代码结果不对"`，截图 `IDEM-5-02-heal.png`，等回完。

**期望硬证据（幂等断言）**
1. 失败轮：log `intent_triage.llm_failed error=...`(safe-fail 命中) + 桌宠**仍正常回复** + **无未捕获 traceback 致 _run_chat 死亡**。
   > ⚠️ 注：safe-fail 的 `_safe_card`(intent_triage.py:241) 对 debug/research/factual_qa 仍置 `needs_investigation=True` → **失败轮可能正常进 Step2 出 `evidence_gate.*`/`evidence_gathered_set` log，这不算 FAIL**（safe-fail 降级保留取证语义是合理的）。
2. 失败轮 safe-fail 返回保守 card(`contradiction=None`、不设澄清)→ **不应**出现 `pipeline_clarification_pause` / `chat_v2_contradiction` 事件(失败不乱触发**澄清/矛盾**副作用)。
   > 推理链(M-3，可自证)：`_safe_card`(intent_triage.py:236-244) 置 `ambiguity_score=0.0` 且无 `clarifying_questions` → 派生 `needs_clarification` 恒 False(:216-219) → 不会 `pipeline_clarification_pause`；`contradiction=None` → 无 `chat_v2_contradiction`。
3. **自愈轮**(还原后)：log `intent_triage.done problem_type=<...>` **正常出**(预分析恢复)；桌宠正常回复。
4. **幂等断言**：失败轮**不卡死、不留半截状态**；自愈轮**完全不带失败轮的残留**(per-run，干净)，证明失败可重试、不被误杀。

**判定**：PASS = 失败轮被 safe-fail 接住仍回复不卡死 + 不乱触发**澄清/矛盾** + 还原后自愈轮 `intent_triage.done` 正常 + 无跨轮污染。
（失败轮出现 `evidence_gate.*` 属正常，见上注，不作 FAIL 依据。）
唯一 FAIL：预分析失败导致整轮挂掉/无回复/traceback = safe-fail 没兜住；或失败轮冒 `pipeline_clarification_pause`/`chat_v2_contradiction`；或失败轮污染后续轮。**★ 一票否决。**

**测后还原**：务必还原 analysis_model(或删 override)重启确认恢复。

---

### IDEM-6 收敛止损报告幂等（每触顶 1 份，不刷屏）

**幂等命题**：S7 止损报告每触顶 run 出 1 份诚实报告(不每轮刷屏、不累积)。

> ⚠️ **C2 实证修正**：`ConvergenceController`(convergence_controller.py)**无任何跨 run 可变状态**，`_verdict` 是每个 run 内新构造的局部量(agent_loop.py:885)。
> 故"会话级 stop_reason 残留"在**架构上不可能发生**——这条原断言无法构造 FAIL(不可证伪)。本 TC 因此**只保留可证伪的「每触顶 1 份不刷屏」**为硬断言；
> "还原后简单轮正常"降为**旁证观测**(结构上恒 PASS，仅确认无意外残留，不作独立 FAIL 依据)。

**前置（C1+C-1 实证修正 — 路径分叉！只有构造 (a) 出 stop_loss）**：
⚠️ **两条触顶路径产物不同，已核实 agent_loop.py**：
- **`allows_call()`(agent_loop.py:873，查 max_turns/wall-clock/cost) 触顶 → 经 ConvergenceController → 真出 `convergence.stop_loss` + 诚实止损报告**(:878-906)。
- **`allows_tool()`(agent_loop.py:2338，查 per_tool_max_consecutive=hallucination) 触顶 → 直接 `ErrorEvent`+return(:2367-2377)，绕过 ConvergenceController，不产出 `convergence.stop_loss`、不产出止损报告**。
⚠️ 且 `max_turns`/`per_tool_max_consecutive` **都是 `GateConfig` 硬编码字段**(termination.py:76/86)，**TOML 不可注入**；默认 `max_turns=10000` 自然跑逼不出。

**故本 TC 的 `convergence.stop_loss` 断言唯一可达构造 = (a)**：临时改源码 `main.py:6576` 的 `_max_iter = 50 if _in_code_mode else 16` → `else 4` 重启
(报告须标注"改源码非 config")，触 `error_max_turns` 经 allows_call → 止损报告。**不存在 `stop_reason=budget`**(臆造值)。

> 构造 (b)(让模型反复同参调同一工具触 hallucination) **不能用于本 TC**：它走 ErrorEvent 路径，无 `convergence.stop_loss`；且 `read_file` 命中即置 `_evidence_gathered=True`(:2392)、`per_tool_max_consecutive` 是 args-aware(:177)，模型读到 ENOENT 多半换参/放弃而非同参死磕 8 次——触发本身也脆弱。若要观测 hallucination，另判 `Termination gate blocked tool`(:2375) 的 ErrorEvent，**不在本 TC 范围**。

**步骤**（构造 (a)）
1. （构造）改 `main.py:6576` `else 16`→`else 4`，重启，确认装配 log。
2. **触顶轮**：`动作=粘贴 "帮我把这个含 5 个互相依赖、信息都缺失的子问题彻底解决，每个都要查证" → 发送`(诱导多轮逼近 4 轮上限)，截图 `IDEM-6-01-stoploss.png`，等桌宠触顶止损。
3. **旁证·简单轮**：**还原 main.py:6576 重启**；同会话发 `"1+1 等于几"`，截图 `IDEM-6-02-after.png`，等回完。

**期望硬证据**
1. **(硬断言)** 触顶轮：log `convergence.stop_loss reason=error_max_turns principal_resolved=false unverified=<n>`(reason 真实触顶值，**非 budget、非 hallucination**——构造 (a) 走 allows_call 必是 error_max_turns)；
   桌宠输出**诚实止损报告**(已做什么/卡在哪/建议)，**报告只出 1 份**(grep `convergence.stop_loss` 该 run 内恰 1 次，不每轮重复刷 `<收敛>`)。
2. **(旁证)** 简单轮正常答 "2"，无 `convergence.stop_loss`(结构上本就不会残留，确认无意外即可)。

**判定**：PASS = 触顶产出 1 份 converged=false 诚实报告(stop_reason 真实、不刷屏) + 桌宠没假装完成。
若止损报告每轮重复刷(`convergence.stop_loss` 同 run 多次)=副作用累积(FAIL)；若假装"已完成"或 `stop_reason=budget`=FAIL。

**诚实标注(HARD)**：必须在报告写明用构造 (a) 还是 (b) + 实际 `stop_reason` 值；不允许用默认配置「跑很久没结束」当止损证据。
**测后还原**：若用 (a) 务必还原 `main.py:6576` 重启确认恢复。

---

## 4. 核心功能上线门 checklist（收口功能 doc 的 ★ 必过，详情见姊妹文档）

> 以下为**上线必过功能项**，逐步细节 + 反向 bug 信号见
> [`../2026-06-24-problem-pipeline-maoxuan/manual-test.md`](../2026-06-24-problem-pipeline-maoxuan/manual-test.md) 对应 TC。
> 本表给一句话步骤 + 上线判定；执行时**必须真模拟点击 + 截图 + log grep**，不得只勾选。

| Case | 一句话步骤 | 上线判定(PASS 条件) | ★ |
|---|---|---|---|
| **TC-1** | 发 "你好呀今天天气真好" + 一条非闲聊对照 | 闲聊 `shortcircuit` + 该轮 0 次 `intent_triage.done`；非闲聊对照 `done` 恰 1 次(合并预分析) | ★ |
| **TC-2** | 发 "导出功能报错了直接告诉我原因吧"(诱导不取证) | `evidence_gate.blocked`→`evidence_gathered_set` 或模型本就先取证；唯一 FAIL=没取证就给结论且没拦 | ★ |
| **TC-3** | 发含 2-3 症状的复合问题 | `intent_triage.done has_contradiction=true` + `chat_v2_contradiction` 含 principal/attack_order | ★ |
| **TC-4** | 发可验证产物任务(is_prime 等) | `self_check.done heterogeneous=true` + 启动 `pipeline_external_evaluator_model`(异体独立 provider) | ★ |
| **TC-5** | 构造触顶(见 IDEM-6 前置：**唯一可达 = 改源码 main.py:6576 `else 4`** 触 error_max_turns；hallucination 走 ErrorEvent 不出 stop_loss) | `convergence.stop_loss reason=error_max_turns` + 诚实止损报告(非假装完成、非 budget) | ★ |
| **TC-6** | 发歧义问题 → 答澄清 | `clarify=true`→`pipeline_clarification_pause`→答后承接继续(多轮不断裂) | |
| **TC-7** | 发 3 条非闲聊(factual/debug/research) | 每条 `intent_triage.done` 恰 1 次；简单 `has_contradiction=false`/复杂 `=true` | |
| **TC-8** | code 模式典型请求(若入口可达) | code 路径**无任何** `intent_triage.*`/`pipeline_*`(决策2 边界)；入口关则声明 env-limited 等确认 | |
| **TC-9** | 设 `enabled=false` 重启发闲聊+debug | 全程零 pipeline log + 零 `chat_v2_*` 事件 + 对话仍正常(BC)；测后还原 enabled=true | ★ |
| **TC-10** | 构造坏 analysis_model 发非闲聊 | `intent_triage.llm_failed` + 降级裸 ReAct 仍回复不卡死(与 IDEM-5 失败轮可合并执行) | |

> **执行优化**：IDEM-5 失败轮 ≈ TC-10、IDEM-6 触顶轮 ≈ TC-5、IDEM-3 ≈ TC-6 的重启加强版——可合并跑同一构造，分别按各自判定核对，避免重复构造 config。

---

## 5. 结果汇总表（执行后填）

### 5.1 幂等专项（本文档上线门）

| case | 幂等命题 | ★ | 坐标 | 动作摘要 | 截图 | log 证据(关键计数) | 判定 |
|---|---|---|---|---|---|---|---|
| IDEM-1 | 重复闲聊预分析副作用恒 0 | ★ | | | | shortcircuit ___×、done ___× | |
| IDEM-2 | 取证 per-run 重置不累积 nudge | | | | | 各轮 evidence_gathered_set ___ | |
| IDEM-3 | 澄清单 send 不双写+重启不断裂 | ★ | | | | UI 澄清气泡 ___条、persist_failed ___ | |
| IDEM-4 | 重启后 per-run 标记不残留 | ★ | | | | 重启后闲聊轮 evidence ___ | |
| IDEM-5 | 预分析失败可重试自愈 | ★ | | | | llm_failed→还原后 done ___ | |
| IDEM-6 | 止损报告每触顶 1 份不残留 | | | | | stop_loss ___×、stop_reason=___ | |

### 5.2 核心功能上线门（详情见姊妹 doc）

| case | ★ | 截图 | log 证据 | 判定 |
|---|---|---|---|---|
| TC-1 | ★ | | | |
| TC-2 | ★ | | | |
| TC-3 | ★ | | | |
| TC-4 | ★ | | | |
| TC-5 | ★ | | stop_reason=___ | |
| TC-6 | | | | |
| TC-7 | | | | |
| TC-8 | | | | |
| TC-9 | ★ | | | |
| TC-10 | | | | |

---

## 6. 上线通过线（Production Ship Gate）

- **★ 必过(任一 FAIL = 不可上线)**：功能 TC-1 / TC-9 + 幂等 IDEM-1 / IDEM-3 / IDEM-4 / IDEM-5。
  - **TC-10(safe-fail 不卡死) 的上线判定已并入 IDEM-5 ★**(IDEM-5 失败轮 = TC-10 场景，合并执行；"预分析失败不把整轮拖死"由 IDEM-5 ★ 兜)。
- 其余功能 ★(TC-2/3/4/5) 按功能 doc 判定(含 best-effort 标注)全过。**注：TC-5/IDEM-6 的 stop_loss 上线判定仅认构造 (a)(改源码 main.py:6576 触 error_max_turns)；hallucination 构造不产出 stop_loss，不可作判定依据。**
- 非 ★ 用例 FAIL → 记 bug 按严重度决定是否阻断上线。
- best-effort / env-limited 的 TC 须**显式标注理由 + 实测值**，不允许伪造触发。
- 全绿(或 ★ 全 PASS + 非 ★ 受控标注清楚)后：更新 `STATUS/status.md` §3 模块行 + §4 里程碑；证据存
  `plans/manual-results-2026-06-25-problem-pipeline-prod/screenshots/`，本目录只放用例定义。

---

## 7. 汇总

| 项 | 值 |
|---|---|
| 幂等专项用例 | 6（IDEM-1~6），★4 必过(IDEM-1/3/4/5) |
| 核心功能上线门 | 10（TC-1~10，引用姊妹 doc），★6 必过(TC-1/2/3/4/5/9) |
| 上线 ★ 必过合计 | 6（功能 TC-1/9 + 幂等 IDEM-1/3/4/5） |
| 是否需 windows-mcp 真测 | **是**（每例真坐标点击 + 真中文输入 + 截图 + log grep；禁 WS 直注/pytest/import 当 UI 证据） |
| 需改 config 重启的用例 | IDEM-5(坏 analysis_model)/TC-9(enabled=false)/TC-10(坏 analysis_model)——测后均须还原 |
| 需构造触顶的用例 | IDEM-6/TC-5：**唯一可达 stop_loss = 改源码 main.py:6576 `else 4` 触 error_max_turns**(max_turns 非 config key；hallucination 走 ErrorEvent 不出 stop_loss)——测后还原源码 |
| 需进程重启的幂等用例 | IDEM-3(重启验持久化)/IDEM-4(重启验状态隔离)/IDEM-5(还原重启)/IDEM-6(还原重启) |
| best-effort/env-limited | IDEM-2(依赖 LLM 是否想直接下结论)/IDEM-3 澄清触发(依赖歧义≥0.7)/TC-2/3/4/6/8(见功能 doc) |
</content>
</invoke>
