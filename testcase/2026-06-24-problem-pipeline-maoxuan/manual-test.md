# 七步问题处理流水线（毛选方法论）— windows-mcp 真机手工测试用例

> **被测功能**：「七步问题处理流水线」(ProblemHandlingPipeline) —— 把毛选方法论落成一条可门控、可观测的显式流水线：
> Step1+3 预分析（意图+主要矛盾，合并 1 次 LLM，决策4）→ Step2 取证门控 → Step4 弹钢琴定方案 → Step5 执行 →
> Step6 异体自检 → Step7 收敛止损。**仅 Companion 主线**（决策2，code 模式分支原样不动）。
> **对应 plan**：[plans/2026-06-24-problem-handling-pipeline-maoxuan/00-*.md](../../plans/2026-06-24-problem-handling-pipeline-maoxuan/)
> （03 架构 / 04 实现 / 05 测试+flag）。
> **实现锚点**：
> - `backend/deskpet/agent/intent_triage.py`（Step1+3 预分析，`analyze`@171）
> - `backend/deskpet/agent/evidence_gate.py`（Step2 取证门控）
> - `backend/deskpet/agent/self_check_gate.py`（Step6 异体自检）
> - `backend/deskpet/agent/convergence_controller.py`（Step7 收敛止损）
> - `backend/deskpet/agent/problem_pipeline.py`（PRE-LOOP 编排器，发 `chat_v2_intent/contradiction`）
> - `backend/agent/agent_loop.py`（IN-LOOP 三闸 + 发 `chat_v2_evidence_gate/selfcheck/convergence`）
> - `backend/main.py`（PRE-LOOP 装配 @6835、`problem_pipeline_init`@1732、异体 provider `pipeline_external_evaluator_model`@1174）
> - `backend/config.py`（`ProblemPipelineConfig`@418，kill-switch `enabled`@428）
> **最后更新**：2026-06-24

---

## 0. 测试前置（HARD — 不满足则结果作废）

### 0.1 进程清场（防孤儿进程占端口，项目坑 #1）

1. 关闭桌宠，然后 `taskkill /F /IM deskpet.exe`（TaskStop 不清 deskpet.exe + Vite）。
2. 杀残留 Vite：`Get-Process node -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*tauri-app*' } | Stop-Process -Force`（或确认 5173 端口空闲）。
3. **不要手动起 backend**（项目坑 #7：手动 `python main.py` 占 8100 → Tauri spawn backend 报 `os error 10048`）。
4. **不要手动起 vite**（项目坑 #9：`tauri dev` 的 `beforeDevCommand` 已自管唯一 vite，双 vite 抢 strictPort → 白屏）。

### 0.2 跑「当前 worktree 代码」而非 frozen exe（HARD GATE，项目坑 #8）

只给 **Tauri 进程**注入 env（不要手动起 backend）：

```
DESKPET_BACKEND_DIR  = G:\projects\deskpet\backend
DESKPET_PYTHON       = G:\projects\deskpet\backend\.venv\Scripts\python.exe
DESKPET_DEV_MODE     = 1
DESKPET_USER_DATA_DIR = G:\projects\deskpet\backend\userdata   （reference_dev_userdata_dir_on_g：dev 数据落 G 不落 C）
```

启动后**必须**在 tauri dev log 里确认出现：
`[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`
若看到 `[backend_launch] Bundled exe=...` → 跑的是旧 frozen，**测了等于白测，立即停下重配 env**。

> backend structlog 全走 stderr → `Stdio::inherit()` → 落进 tauri dev 重定向 log。本文档所有「backend log 证据」都 grep 这份 tauri dev log。
> 建议启动命令把输出重定向到 `plans/manual-results-2026-06-24-problem-pipeline/tauri-dev.log`。

### 0.3 登录 / key / flag

- relay 已登录（dev 自动登录或手动 onboarding；测试凭据见 gitignored `LOCAL-DEV-CREDENTIALS.md`，token 常过期需重登 → reference_dev_test_credentials）。
- LLM key（gpt-5.5）已在 OS keychain（流水线预分析 + 自检都要真调主 LLM）。
- **流水线默认即开**：`ProblemPipelineConfig.enabled` 默认 **True**（config.py:428，决策1 出厂即开）；
  `intent_triage / evidence_gate / plan_companion_enabled / self_check / observability_events` 全默认 on。
- 启动后先在 log 确认装配成功：grep `problem_pipeline_init enabled=true intent=... evidence=... self_check=...`（main.py:1732）。
  **若 grep 不到这行** → 流水线没装上，全部 TC 无意义，先查 config/装配。
- 异体自检 provider：grep `pipeline_external_evaluator_model model=<x> base=<y>`（main.py:1174）应在 build_agent 时出现一次
  （证明异体子代理 provider 实例已克隆出来；TC-4 依据）。`self_check_model` 默认留空=主 LLM，故 `model` 可 == `base`（诚实口径，见 TC-4）。

### 0.4 windows-mcp 真测纪律（HARD CONSTRAINT — 不可妥协）

1. **真模拟人**：每个 case 必须 Screenshot/Snapshot 抓状态 → **真坐标点击 / 真键盘输入** → 截图验证 → 日志判 PASS/FAIL。
2. **禁绕过**：不允许用 `ws://127.0.0.1:8100/*` WebSocket 直注、`pytest`/`last_mile_smoke.py`、`import` backend 查 registry、
   keychain/文件存在/log-only grep 当 UI 测试证据替代真点击（根 CLAUDE.md「🔒 手工测试纪律」）。**log grep 只是判定辅助，
   必须配真截图 + 真 UI 操作链路**。
3. **每个动作前 declare**：`坐标=(x,y) | 动作=click/type/drag | 期望=...`。
4. **截图存盘**：`plans/manual-results-2026-06-24-problem-pipeline/screenshots/<case-id>-NN-*.png`。
5. **失败 retry ≥ 3 次不同 workaround** 才能标「环境受限」。
6. **跳过任何 case** 必须显式声明 + 给具体环境受限理由 + 等用户确认。
7. **中文输入 workaround**：STA Runspace + `Clipboard.SetText("中文")` + Ctrl+V；焦点不在目标窗口先 Click 输入框聚焦再粘贴；
   用 backend log 确认消息真收到（grep 收到的 user message / `intent_triage.*`）。

### 0.5 ★ 必过项（一票否决）

| Case | 验收点 | 为什么是一票否决 |
|---|---|---|
| **TC-1** | 闲聊纯规则短路 0 次 LLM，不拖慢 | 性能红线（04 风险#2）；短路失效=拖慢闲聊回归 |
| **TC-2** | debug 取证门控真拦截 | Step2 核心闸①「没有调查就没有发言权」 |
| **TC-3** | 多症状抓主要矛盾，计划首步对准 principal | Step3 核心「抓主要矛盾」 |
| **TC-4** | 异体自检走独立 fresh-context 子代理 | Step6 核心「非执行者打分」 |
| **TC-5** | 收敛止损出诚实报告（非假装完成） | Step7 核心「胸中有数 + 止损」 |
| **TC-9** | kill-switch（enabled=false）零回归 | ★ L0 一票否决，BC 守护（05 §1） |

任一 ★ FAIL → 不算完成，回 plan 修。

---

## 1. 真实日志锚点速查（执行时 grep tauri dev log 用）

> 全部已核实存在于源码（行号附后）。structlog 关键字风格：`key=value`（structlog renderer）。grep 用锚点字符串即可。

| 步 | 事件 | 锚点字符串 | 代码位置 | 含义 |
|---|---|---|---|---|
| 装配 | 流水线装上 | `problem_pipeline_init enabled=true intent=... evidence=... self_check=...` | main.py:1732 | enabled=true 时编排器构造成功 |
| 装配 | 装配失败 | `problem_pipeline_init_failed:` | main.py:1735 | 构造异常 → 自动 disabled（应排查） |
| 装配 | 异体 provider 克隆 | `pipeline_external_evaluator_model model=<x> base=<y>` | main.py:1174 | 异体自检独立 provider 实例（TC-4 依据） |
| Step1+3 | 闲聊纯规则短路 | `intent_triage.shortcircuit reason=chitchat_rule task_type=<...>` | intent_triage.py:186 | 闲聊不调 LLM（0 次，TC-1） |
| Step1+3 | 预分析完成 | `intent_triage.done problem_type=<...> ambiguity=<f> clarify=<bool> short_circuit=<bool> has_contradiction=<bool>` | intent_triage.py:221 | 非闲聊调 1 次 LLM 出意图+矛盾 |
| Step1+3 | 预分析 LLM 失败 | `intent_triage.llm_failed error=...` | intent_triage.py:208 | safe-fail 降级裸 ReAct（TC-边界 safe-fail） |
| PRE-LOOP | 整条短路 | `pipeline_short_circuit sid=<...>` | main.py:6844 | 闲聊短路，不进 IN-LOOP（TC-1） |
| PRE-LOOP | 澄清出口暂停 | `pipeline_clarification_pause sid=<...>` | main.py:6869 | needs_clarification → emit + return（TC-6 澄清） |
| Step2 | 取证拦截 | `evidence_gate.blocked nudges_used=<n>` | evidence_gate.py:88 | 未取证就下结论被拦（TC-2） |
| Step2 | nudge 超限放行 | `evidence_gate_exhausted nudges_used=<n>` | evidence_gate.py:77 | 超 max_nudges 放行避免死循环 |
| Step2 | 取证发生 | `evidence_gathered_set sid=<...> tool=<name>` | agent_loop.py:2393 | 本 run 内调了取证工具（放行依据） |
| Step2 | nudge 注入 | `evidence_gate_nudge_injected sid=<...> nudge=<n>` | agent_loop.py:1516 | `<调查>` nudge 注入了 |
| Step6 | 自检完成 | `self_check.done mode=<...> passed=<bool> heterogeneous=<bool> unmatched=<n>` | self_check_gate.py:128 | 异体自检结论（TC-4） |
| Step6 | 自检 nudge 注入 | `self_check_nudge_injected sid=<...> mode=<...> nudge=<n>` | agent_loop.py:1645 | `<自检>` 反思注入 |
| Step6 | 自检超限放行 | `self_check_exhausted sid=<...> nudge=<n>/<m> → force_finish` | agent_loop.py:1663 | 连续失败超上限 → 强制收尾 |
| Step6 | 异体评分异常 | `self_check.evaluator_failed error=...` | self_check_gate.py:120 | 异体子代理调用失败（保守降级） |
| Step7 | 止损触发 | `convergence.stop_loss reason=<...> principal_resolved=<bool> unverified=<n>` | convergence_controller.py:71 | 资源触顶未收敛 → 诚实止损（TC-5） |

### 1.1 WS 标签事件（observability_events=true 时发，control/chat 通道）

| 事件 | 触发步 | payload 关键字段 | 代码位置 |
|---|---|---|---|
| `chat_v2_intent` | Step1 | `restated_intent, problem_type, ambiguity_score` | problem_pipeline.py:76 |
| `chat_v2_contradiction` | Step3 | `principal, attack_order, rationale` | problem_pipeline.py:102 |
| `chat_v2_plan`（复用） | Step4 | `steps[], 首步对准 principal` | — |
| `chat_v2_evidence_gate` | Step2 | `blocked, reason, nudge_count` | agent_loop.py:1502 |
| `chat_v2_selfcheck` | Step6 | `passed, mode, heterogeneous, claims_unverified` | agent_loop.py:1629 |
| `chat_v2_convergence` | Step7 | `converged, principal_resolved, stop_reason, report` | agent_loop.py:891/2171 |

> WS 事件在前端是否渲染卡片不影响判定；**判定主依据是 backend log + 截图**。若前端有「思考过程」卡片渲染则一并截图佐证。

### 1.2 TerminationGate 真实 reason 取值（TC-5 依据，已核实 termination.py）

> `stop_reason` **不存在 `budget` 这个值**（05 §6 注，原臆造值必挂）。正常收尾只有 `success`/`user_interrupted`/`running`；
> 其余全是触顶/错误态（`resource_capped=True`）：
> `error_max_turns`（默认 max_turns=10000，自然跑逼不出）/ `error_tool_budget` / `error_wall_clock_exceeded`（默认禁用）/
> `error_max_budget_usd`（默认禁用）/ `hallucination`（`per_tool_max_consecutive=8` 触发，真死循环主防线）/
> `permanent_tool_error` / `all_providers_failed` / `context_budget_block` / `circuit_breaker_open`。
> ConvergenceController 用补集判据：`reason not in ("running","success","user_interrupted")` 即视为触顶（convergence_controller.py:57-58）。

---

## 2. 测试用例

> 每条格式：case-id / 场景 / 前置 / declare 动作序列 / 期望硬证据 / backend log 锚点 / 判定标准。
> 坐标占位 `(x,y)` 执行时按真机 Snapshot 实测填，报告里写真实物理像素。

---

### TC-0（前置度量）闲聊 classifier 落 chat/emotion 覆盖率（TC-1 的前置硬证据，非 PASS/FAIL only）

> 闲聊纯规则短路依赖组装期 `_bundle.task_type` 落 `chat`/`emotion`（→`_TASKTYPE_TO_PROBLEM` 派生 chitchat → intent_triage.py:182-185）。
> **若 classifier 把闲聊误判成 `code`/`task`，短路失效 → 闲聊被拖进完整流水线**（04 风险#2）。故 TC-1 前先跑覆盖率度量。

**前置**：§0 全部满足；`problem_pipeline_init enabled=true` 已确认。

**步骤**：逐条发送 ≥10 条代表性闲聊/情绪语料（每条一次对话，发送间隔等桌宠回完）。建议语料：

| # | 语料 | 期望 task_type | 期望短路 |
|---|---|---|---|
| 1 | 你好呀 | chat | shortcircuit |
| 2 | 今天天气真好 | chat | shortcircuit |
| 3 | 我有点累 | emotion | shortcircuit |
| 4 | 陪我聊聊天吧 | chat | shortcircuit |
| 5 | 哈哈哈你真有意思 | chat | shortcircuit |
| 6 | 我心情不太好 | emotion | shortcircuit |
| 7 | 晚安啦 | chat | shortcircuit |
| 8 | 谢谢你 | chat | shortcircuit |
| 9 | 好无聊啊 | emotion | shortcircuit |
| 10 | 你今天开心吗 | chat | shortcircuit |

每条 declare：`坐标=(桌宠输入框 x,y) | 动作=click 聚焦 → Clipboard SetText "<语料>" → Ctrl+V → Enter | 期望=消息发出 + 桌宠闲聊式回复`。
每条发送后截图 `TC-0-NN-<语料前4字>.png`。

**期望硬证据**：每条在 backend log 出现 `intent_triage.shortcircuit reason=chitchat_rule task_type=chat`（或 `task_type=emotion`）。
统计命中率 = 出现 shortcircuit 的条数 / 10。

**判定标准**：
- 命中率 **≥ 80%（≥8/10）** = PASS（短路覆盖达标）。把实测命中率数值 + 每条 task_type 写进报告（**硬证据，不只 PASS/FAIL**）。
- 命中率 < 80% → 标记 bug：短路覆盖不足，闲聊会被拖进完整流水线；需在 Step1 补闲聊兜底规则，并把 miss 的语料 + 误判 task_type 列出。

**能逼出的 bug**：classifier 漂移把闲聊误判 → 短路失效；这是 TC-1 性能红线的根因之一，必须先量化。

---

### TC-1 ★ 闲聊不拖慢（纯规则短路，0 次 LLM）

**场景**：闲聊走纯规则短路，整条流水线不介入，延迟与 flag off 基线相当。

**前置**：§0 满足；TC-0 覆盖率达标。准备一条对照基线（见步骤 4）。

**步骤**
1. `坐标=(桌宠输入框 x,y) | 动作=click | 期望=输入框聚焦`
2. `动作=Clipboard SetText "你好呀今天天气真好" → Ctrl+V → Enter | 期望=消息发出`，截图 `TC-1-01-sent.png`，**记发送时刻**。
3. 等桌宠回完（闲聊式回复），截图 `TC-1-02-reply.png`，**记回复完成时刻**（算延迟 Δt_chitchat）。
4. **非闲聊对照（验合并预分析只调 1 次，非两次串行）**：
   `动作=Clipboard SetText "帮我分析一下为什么我这段 Python 排序代码越界报错" → Ctrl+V → Enter`，截图 `TC-1-03-nonchitchat.png`。

**期望硬证据**
1. 闲聊路径 log：`intent_triage.shortcircuit reason=chitchat_rule task_type=chat` + `pipeline_short_circuit sid=<...>`。
2. **闲聊 0 次预分析 LLM 调用**：该轮 **无** `intent_triage.done`（done 只在非闲聊调 LLM 后打）、**无** `intent_triage.llm_failed`、
   **无** `chat_v2_intent`/`chat_v2_contradiction` 事件（纯规则短路，不发意图/矛盾事件）。
3. 非闲聊对照轮：`intent_triage.done problem_type=debug ...` **恰出现 1 次**（合并预分析一次往返出意图+矛盾，决策4），
   **不是两次串行**（不应看到两条独立的意图分析、矛盾分析往返 log）。
4. 延迟对比：闲聊 Δt_chitchat 与「flag off 基线」相当（见下「基线获取」）。

**基线获取（时间戳对比，best-effort）**：理想基线 = 同一条闲聊在 `enabled=false` 下的延迟。可在本 TC 后做一次 TC-9（kill-switch）
时顺带发同一条闲聊记 Δt_baseline，回填本表。若不便切 flag，至少用「短路 log 到回复事件的时间间隔」证明短路链路无额外 LLM 往返。

**backend log 锚点**：`intent_triage.shortcircuit`（闲聊）/ `pipeline_short_circuit`（闲聊）/ `intent_triage.done problem_type=debug`（非闲聊对照，恰 1 次）。

**判定标准**：PASS = 闲聊命中 shortcircuit + 该轮 0 次预分析 LLM（无 done/无 intent 事件）+ 非闲聊对照 done 恰 1 次 + 延迟不被拖慢。
任一缺失 = FAIL。**这是性能红线一票否决。**

**能逼出的 bug**：① 短路失效（闲聊也打了 `intent_triage.done` → 被拖进流水线）；② 合并预分析没真合并（非闲聊看到两次串行往返）；
③ flag 接通但闲聊路径误发了 `chat_v2_intent` 事件。

---

### TC-2 ★ debug 取证门控（没有调查就没有发言权）

**场景**：debug 类问题，模型想不取证直接下结论 → EvidenceGate 拦截 → 模型转去 read/grep 取证 → 放行。

**前置**：§0 满足。问题要「信息不足、诱导直接猜结论」——故意不给代码细节。

**步骤**
1. `坐标=(桌宠输入框 x,y) | 动作=click 聚焦`
2. `动作=Clipboard SetText "我的导出功能突然报错了，你直接告诉我是什么原因吧" → Ctrl+V → Enter | 期望=消息发出`，截图 `TC-2-01-sent.png`
3. 观察桌宠是否先去「调查」（调 read/grep/search 类工具）再回答，等回完截图 `TC-2-02-reply.png`

**期望硬证据**
1. log：`intent_triage.done problem_type=debug ... needs_investigation`（needs_investigation=true 隐含在 card；problem_type=debug）。
2. log：`evidence_gate.blocked nudges_used=<n>`（模型首轮想直接下结论被拦）+ `evidence_gate_nudge_injected sid=<...> nudge=<n>`
   （`<调查>` nudge 注入）+ WS `chat_v2_evidence_gate` payload `blocked=true`。
3. 之后 log：`evidence_gathered_set sid=<...> tool=<read|grep|search|...>`（模型转去取证）→ 后续 `evidence_gate` 不再 block（放行）。
4. 桌宠最终回复**基于取证结果**，而非凭空猜（肉眼+截图）。

**backend log 锚点**：`evidence_gate.blocked` → `evidence_gate_nudge_injected` → `evidence_gathered_set tool=...`（顺序）。
**反向 bug 信号**：若全程**无** `evidence_gate.blocked`、模型直接给结论 → 门控没真生效（FAIL）。

**判定标准**：PASS = needs_investigation 的 debug 问题被 block 至少 1 次 + nudge 注入 + 模型转去取证 + 放行后基于证据回答。
若门控从未触发或模型未取证直接结论 = FAIL。**一票否决。**

**best-effort 标注**：是否触发 block 依赖 LLM 首轮**真的想直接下结论**（若模型本就先取证，则 gate 不 block 也算正确行为）。
故判定放宽为：**要么 gate 拦了再放行，要么模型本就先取证（log 有 `evidence_gathered_set` 在 end_turn 前）**——两者都证明「调查先于发言」语义成立；
**唯一 FAIL = 模型未取证就给了结论且 gate 没拦**。若多次重试都无法诱导出「不取证就下结论」，标 best-effort 并记录实测模型行为。

---

### TC-3 ★ 多症状抓主要矛盾（集中优势兵力）

**场景**：抛一个含 2-3 个症状的复合问题，流水线识别主要矛盾，计划首步对准 principal。

**前置**：§0 满足。

**步骤**
1. `坐标=(桌宠输入框 x,y) | 动作=click 聚焦`
2. `动作=Clipboard SetText "我的网站最近又慢又偶尔 500 报错，用户还说登录验证码经常收不到，你帮我看看怎么搞" → Ctrl+V → Enter | 期望=消息发出`，截图 `TC-3-01-sent.png`
3. 等桌宠分析/拟方案，若前端渲染「主攻」卡片则截图 `TC-3-02-contradiction-card.png`；回完截图 `TC-3-03-reply.png`

**期望硬证据**
1. log：`intent_triage.done problem_type=<debug|multi_task> ... has_contradiction=true`（复杂问题填了 contradiction 段）。
2. WS `chat_v2_contradiction` payload 含 `principal=<id>`、`attack_order=[...]`、`rationale`（主要矛盾被识别）。
3. WS `chat_v2_plan`（若 plan_companion 生成计划）：**首步对准 principal contradiction**（计划第 1 步描述与 principal 一致）。
4. 桌宠回复体现「先打主要矛盾、次要矛盾随后弹钢琴统筹」的结构（肉眼+截图）。

**backend log 锚点**：`intent_triage.done has_contradiction=true`；WS `chat_v2_contradiction`（含 principal/attack_order）。
**反向 bug 信号**：`has_contradiction=false`（复杂问题没填矛盾段）或无 `chat_v2_contradiction` 事件 → 抓主要矛盾失效。

**判定标准**：PASS = 复杂问题 has_contradiction=true + 发 contradiction 事件含 principal/attack_order + 计划首步对准 principal。
若复杂问题未产出 contradiction 段 = FAIL。**一票否决。**

**best-effort 标注**：principal 选得「对不对」是 LLM 主观判断，本 TC **不评判 principal 选择质量**，只验**机制接通**：
复杂问题确实产出了 contradiction 段且计划首步与所选 principal 一致。principal 选择合理性记录供人工 review，不作 FAIL 依据。

---

### TC-4 ★ 异体自检（非执行者打分，fresh-context 子代理）

**场景**：让桌宠完成一个可验证产物类任务，诱导其「假装完成」→ 异体子代理打回 → 二次修正。

**前置**：§0 满足；启动时已 grep 到 `pipeline_external_evaluator_model`（异体 provider 已克隆）。

**步骤**
1. `坐标=(桌宠输入框 x,y) | 动作=click 聚焦`
2. `动作=Clipboard SetText "帮我写一个 Python 函数 is_prime(n) 判断素数，并自己验证它对 1、2、9、17、25 都正确，给我最终确认无误的版本" → Ctrl+V → Enter | 期望=消息发出`，截图 `TC-4-01-sent.png`
3. 等桌宠产出 + 自检；若有「打回-修正」过程观察气泡，回完截图 `TC-4-02-reply.png`

**期望硬证据（⚠️ 诚实口径 — 05 MINOR②）**
1. log：`self_check.done mode=<strict|...> passed=<bool> heterogeneous=true unmatched=<n>`。`heterogeneous=true` 证明走了异体子代理。
2. 启动 log（或本轮）有 `pipeline_external_evaluator_model model=<x> base=<y>`：证明评分走经 `_resolve_ephemeral_provider` 克隆出的
   **独立 provider 实例**、**新开 context**、**非执行者本人**。
3. **不能断言 `model` 不同**：`self_check_model` 默认留空=主 LLM gpt-5.5（中转站单模型不保证不同 model），故 `model` **可以 == base**。
   验的是 **fresh-context 独立子代理**而非 diff-model。
4. 若诱导出「假装完成被打回」：log 序列 `self_check.done passed=false` → `self_check_nudge_injected`（`<自检>` 反思）→ 二次修正 → `self_check.done passed=true`。

**backend log 锚点**：`pipeline_external_evaluator_model`（独立 provider）+ `self_check.done heterogeneous=true`。
**可选增强**：仅当显式把 config `[features.problem_pipeline].self_check_model` 配成与主 LLM 不同的 model 时，才另断 `model=` ≠ `base=`（非必需）。

**判定标准**：PASS = 自检走 heterogeneous=true 的独立 fresh-context 子代理（log 双证据：external_evaluator_model + self_check.done heterogeneous=true）。
若 `heterogeneous=false`（用执行者自评）或无 external_evaluator_model log = FAIL。**一票否决。**

**best-effort 标注**：「假装完成被打回再修正」依赖 LLM 真先给错版本——可能模型一次就对（is_prime 太简单，对 1 应返回 False 是常见坑可诱导）。
若诱导不出打回，**最低验收 = 异体子代理确实被调用（heterogeneous=true + external_evaluator_model log）**，打回-修正循环记 best-effort。
若要更稳触发打回，可换更易出错的任务（如「写正则匹配中文姓名并保证不误匹配英文」），并在报告记用了哪个任务。

---

### TC-5 ★ 收敛止损（胸中有数 + 不硬撑）

**场景**：构造一个解不动 / 逼近迭代上限的问题 → 桌宠输出**诚实止损报告**（卡在哪+建议）而非假装完成。

**前置**：§0 满足。**⚠️ 必读 05 §L3 注 + §6**：默认 `GateConfig` 硬上限**极大**（termination.py:76 `max_turns=10000`、:79 `wall_clock_seconds=None` 禁用、
:80 `max_budget_usd=None` 禁用），**自然跑逼不出触顶**，且**不存在 `stop_reason=budget` 这个值**。必须**构造触顶**。二选一并在报告写明用哪种：

**构造方式 (a)：临时收紧 gate（推荐，可控）**
- 改 dev config 注入小 `max_turns`（如 `4`）或小 `per_tool_max_consecutive`（如 `3`，制造同工具同参重复触 `hallucination`）后重启桌宠。
- 期望真实 `stop_reason` ∈ {`error_max_turns`（收紧 max_turns）, `hallucination`（收紧 per_tool_max_consecutive）}。

**构造方式 (b)：制造同工具同参重复触 hallucination（不改 config）**
- 抛一个让模型反复用相同参数调同一工具的死循环型任务（如「一直读同一个不存在的文件直到读到为止」），逼 `per_tool_max_consecutive=8` 触 `hallucination`。
- 这条更接近真实死循环，但触发不稳定，可能需多轮。

**步骤**（以 (a) max_turns=4 为例）
1. （构造前置）改 config 注入小 max_turns 重启 → 启动 log 确认。
2. `坐标=(桌宠输入框 x,y) | 动作=click 聚焦`
3. `动作=Clipboard SetText "帮我把这个含 5 个互相依赖、信息都缺失的子问题的复杂工程问题彻底解决，每个都要查证" → Ctrl+V → Enter | 期望=消息发出`，截图 `TC-5-01-sent.png`
4. 等桌宠逼近上限并止损，截图 `TC-5-02-stoploss-report.png`

**期望硬证据**
1. log：`convergence.stop_loss reason=<error_max_turns|hallucination|...> principal_resolved=false unverified=<n>`
   （reason 取真实触顶值，**不是 `budget`**）。
2. WS `chat_v2_convergence` payload：`converged=false`、`principal_resolved=false`、`stop_reason=<真实值>`、`report` 非空。
3. 桌宠输出**诚实止损报告**（「已做什么 / 卡在哪 / 主要矛盾是否解 / 建议下一步」），**而非假装"已完成"**（肉眼+截图核对措辞）。
4. 报告里**写明用了构造方式 (a) 还是 (b) + 实际 `stop_reason` 值**（05 §L3 注硬要求）。

**backend log 锚点**：`convergence.stop_loss reason=...`（reason ∈ §1.2 触顶集合，非 `budget`/非 `running`/非 `success`）。

**判定标准**：PASS = 触顶后产出 converged=false 的诚实止损报告 + stop_reason 是真实触顶值 + 桌宠没假装完成。
若桌宠假装"已完成"/无止损报告 = FAIL；若 log 出 `stop_reason=budget`（臆造值）或 `converged=true` 假收敛 = FAIL。**一票否决。**

**诚实标注（HARD）**：本 TC **必须**在报告写明触发用的是临时收紧 gate 还是制造 hallucination，以及**实际 `stop_reason` 值**——
不允许用默认配置「跑了很久没结束」当止损证据（默认 max_turns=10000 自然跑逼不出，那是假止损）。

**测后还原**：构造方式 (a) 改的 config 测后**务必还原** max_turns / per_tool_max_consecutive 重启确认恢复。

---

### TC-6 澄清出口（needs_clarification，多轮不断裂）

**场景**：歧义极高的问题 → 桌宠反问澄清 → 用户答 → 下一轮 history 含澄清问题（多轮澄清不断裂）。

**前置**：§0 满足。问题要**足够歧义**（ambiguity_score ≥ 0.7 默认阈值才走澄清出口）。

**步骤**
1. `坐标=(桌宠输入框 x,y) | 动作=click 聚焦`
2. `动作=Clipboard SetText "帮我搞一下那个东西" → Ctrl+V → Enter | 期望=消息发出`，截图 `TC-6-01-vague.png`
3. 等桌宠**反问澄清**（列澄清问题），截图 `TC-6-02-clarify-question.png`
4. `动作=Clipboard SetText "我是说帮我把桌面上的报表整理成一张汇总表" → Ctrl+V → Enter | 期望=用户答澄清`，截图 `TC-6-03-answer.png`
5. 等桌宠**基于澄清继续推进**（不重新反问、不丢上下文），截图 `TC-6-04-continue.png`

**期望硬证据**
1. 首轮 log：`intent_triage.done problem_type=ambiguous ... clarify=true` → `pipeline_clarification_pause sid=<...>`（走澄清出口暂停）。
2. 桌宠首轮回复 = 澄清问题（来自 `IntentCard.clarifying_questions`），**不进 agent loop 干活**。
3. 第二轮（用户答后）：桌宠回复**承接澄清**继续推进——**不再次问同样的澄清**、**不当成全新无关问题**。
   （多轮不断裂的硬证据：澄清问题这条 assistant 消息被持久化进 history，下轮重建 history 含它 → main.py:6862 `_sdb.append_message role=assistant`。）

**backend log 锚点**：`pipeline_clarification_pause sid=<...>`（首轮暂停）；首轮**无** IN-LOOP 干活 log（无 evidence_gate/self_check）。
若持久化失败会出 `clarify_persist_assistant_failed` → 那是 bug 信号。

**判定标准**：PASS = 歧义问题触发澄清反问 + 暂停（pipeline_clarification_pause）+ 用户答后承接继续（多轮不断裂）。
若桌宠对歧义问题不澄清直接瞎干 = 澄清出口失效；若第二轮丢了上下文重新反问 = 持久化/history 断裂 bug。

**best-effort 标注（HARD）**：澄清出口**依赖 LLM 主观判 ambiguity_score ≥ 0.7**——「帮我搞一下那个东西」是否被判够歧义由模型决定。
若多次重试（换更歧义的输入：「弄一下」「处理那个」）都触发不出 `clarify=true`，标 **best-effort / env-limited**，记录实测 ambiguity_score
（log `intent_triage.done ambiguity=<f>`），并说明阈值 0.7 下模型未判够歧义。**不允许**调低阈值伪造触发后当真证据（要测就如实测默认阈值）。

---

### TC-7 非闲聊只调 1 次 LLM（决策4 合并证明）

**场景**：证明 Step1+3 合并成 1 次 LLM 往返（非两次串行），守性能红线。

> 注：TC-1 步骤4 已带一条非闲聊对照；TC-7 是其**独立加强版**，专门多取几条非闲聊确认稳定只调 1 次。

**前置**：§0 满足。

**步骤**：逐条发送 3 条不同非闲聊问题（factual_qa / debug / research 各一），每条发送后等回完截图：
1. `动作=Clipboard SetText "光合作用的暗反应在叶绿体哪个部位进行" → Ctrl+V → Enter`（factual_qa），截图 `TC-7-01-factual.png`
2. `动作=Clipboard SetText "我的 React 组件每次输入都重新渲染整个列表，帮我看怎么优化" → Ctrl+V → Enter`（debug），截图 `TC-7-02-debug.png`
3. `动作=Clipboard SetText "帮我调研 2025 年固态电池量产进展" → Ctrl+V → Enter`（research），截图 `TC-7-03-research.png`

**期望硬证据**
1. 每条**该轮 `intent_triage.done` 恰出现 1 次**（合并预分析一次往返），**不是两条独立的意图分析 + 矛盾分析往返**。
2. factual_qa 轮：`intent_triage.done problem_type=factual_qa ... has_contradiction=false`（简单问题不填矛盾段，省 token）。
3. debug/research 轮：`intent_triage.done ... has_contradiction=true`（复杂问题同一次调用填了矛盾段）。

**backend log 锚点**：每轮 `intent_triage.done` 计数 == 1（按 sid + 时间窗内统计）。

**判定标准**：PASS = 每条非闲聊预分析恰 1 次 LLM（done 1 次）+ 简单问题 has_contradiction=false / 复杂问题 has_contradiction=true。
若某轮看到 2 次 done 或 2 次串行分析往返 = 合并没真合并（FAIL，性能红线破）；若简单问题也填矛盾段 = 省 token 逻辑失效（次要 bug）。

---

### TC-8 code 模式不受影响（决策2）

**场景**：验证流水线 PRE-LOOP 编排**只作用 Companion 主线**，code 模式分支原样不动、不回归。

**前置**：§0 满足。

**步骤**
1. 进入 **code 模式**（若桌宠有 code-panel/code 入口，按现有 UI 切到 code 模式），截图 `TC-8-01-code-mode.png`。
2. `动作=Clipboard SetText "<一个 code 模式典型请求，如：帮我在 utils.py 加一个 slugify 函数>" → Ctrl+V → Enter`，截图 `TC-8-02-code-req.png`
3. 等 code 模式正常产出，截图 `TC-8-03-code-result.png`

**期望硬证据**
1. code 模式路径**不触发** PRE-LOOP 编排：**无** `intent_triage.shortcircuit`/`intent_triage.done`/`pipeline_short_circuit`/
   `pipeline_clarification_pause`、**无** `chat_v2_intent`/`chat_v2_contradiction` 事件（编排只在 companion 主线跑）。
2. code 模式行为与改造前一致（plan.py 的 `in_code_mode` 分支原样不动，决策2）。

**backend log 锚点**：code 模式轮**无任何** `intent_triage.*` / `pipeline_*` / `chat_v2_intent` 锚点。

**判定标准**：PASS = code 模式不被流水线介入 + 行为不回归。若 code 模式出现 `intent_triage.*` 介入 = 决策2 边界破（FAIL）。

**条件跳过标注**：若当前桌宠 **code 入口已关闭/不可达**，则本 TC 的等价验收 = 「确认 PRE-LOOP 编排装配处只在 `not in_code_mode`
路径触发」——但**不允许**用 import/读代码当 UI 证据。正确做法：声明 code 入口不可达 → 标 **env-limited / 条件跳过** + 等用户确认，
并说明 companion 主线已由 TC-1~TC-7 覆盖。

---

### TC-9 ★ kill-switch 回退（enabled=false 零回归，BC 守护）

**场景**：把流水线 flag 关掉（`[features.problem_pipeline].enabled=false`）→ 无任何 `chat_v2_*` pipeline 事件 → 等价今天的链路（BC）。

**前置**：§0 满足。**改 dev config 设 `[features.problem_pipeline] enabled = false` 后重启桌宠。**

**步骤**
1. （前置）config 设 `enabled=false` 重启 → 启动 log 确认**无** `problem_pipeline_init enabled=true`（应看不到 init，或看到 disabled）。
2. `坐标=(桌宠输入框 x,y) | 动作=click 聚焦`
3. `动作=Clipboard SetText "你好呀今天天气真好" → Ctrl+V → Enter`（闲聊），截图 `TC-9-01-chitchat-flagoff.png`，**记延迟 Δt_baseline**（回填 TC-1）。
4. `动作=Clipboard SetText "我的导出功能报错了帮我看看" → Ctrl+V → Enter`（debug），截图 `TC-9-02-debug-flagoff.png`
5. 等两轮都回完。

**期望硬证据**
1. 启动 log **无** `problem_pipeline_init enabled=true`（kill-switch 关闭，编排器不构造）。
2. 两轮对话全程**无任何** `intent_triage.*` / `evidence_gate.*` / `self_check.*` / `convergence.*` / `pipeline_*` log。
3. 两轮对话全程**无任何** `chat_v2_intent/contradiction/evidence_gate/selfcheck/convergence` WS 事件。
4. 对话仍正常工作（等价今天的裸链路）：闲聊有回复、debug 问题正常走原 VerifyGate/external_evaluator 老守门链。
5. **（可选强证据）** `cd backend && python -m pytest -q` 与落地前同样 PASS 数（基线 2300+）——但这是协议层证据，
   **仅作辅助**，不替代上面的真 UI + log 证据（按 §0.4.2 禁绕过）。

**backend log 锚点**：grep 全部 `intent_triage` / `evidence_gate` / `self_check` / `convergence` / `pipeline_` / `chat_v2_intent`
锚点应**全部为空**（flag off 零事件）。

**判定标准**：PASS = enabled=false 时零 pipeline log + 零 chat_v2_* 事件 + 对话仍正常（BC 不破）。
若 flag off 仍冒出任何 pipeline 事件/log = kill-switch 没真接通（FAIL，BC 破）。**这是 L0 一票否决（05 §1）。**

**测后还原（HARD）**：测完**务必**把 config `enabled` 还原回 `true`（或删掉该 override）重启，确认 `problem_pipeline_init enabled=true` 恢复
——否则后续 TC 全在 flag off 下白测。

**能逼出的 bug**：① flag off 没全接通（某步漏判 enabled，仍发事件）；② flag off 把对话搞挂（BC 破，等价链路不可用）。

---

### TC-10 safe-fail（analysis LLM 异常/超时 → 降级裸 ReAct 不卡死）

**场景**：预分析 LLM 调用失败/超时/畸形 JSON → safe-fail 返回保守 IntentCard → 降级裸 ReAct，不卡死流水线。

**前置**：§0 满足。**构造预分析 LLM 失败**（二选一，记录用哪种）：
- (a) 把 config `[features.problem_pipeline].analysis_model` 配成一个**不存在的 model 名**（如 `gpt-does-not-exist`）后重启
  → 预分析调用 4xx/报错 → safe-fail。
- (b) 临时把预分析 timeout 调极小（若 config 暴露 `analysis_timeout_s`）逼超时。
> 注：(a) 更可控。若 analysis_model 留空=主 LLM，配错只影响预分析调用本身（intent_triage 内 `_llm_call`），不影响主对话 LLM。

**步骤**
1. （前置）config 设坏 analysis_model 重启。
2. `动作=Clipboard SetText "帮我分析为什么这段代码报错" → Ctrl+V → Enter`（非闲聊，会触发预分析）截图 `TC-10-01-sent.png`
3. 等桌宠**仍正常回复**（降级裸 ReAct），截图 `TC-10-02-reply.png`

**期望硬证据**
1. log：`intent_triage.llm_failed error=...`（预分析失败被 safe-fail 捕获）。
2. **桌宠不卡死**：对话仍走完、有回复（降级裸 ReAct）；**无 traceback 致 _run_chat 死亡**。
3. safe-fail 返回保守 card（contradiction=null、不澄清、不阻塞）→ 后续不应有澄清暂停/矛盾事件。

**backend log 锚点**：`intent_triage.llm_failed`（safe-fail 命中）；之后对话正常收尾（有 FinalEvent，无未捕获异常 traceback）。

**判定标准**：PASS = 预分析失败被 safe-fail 接住 + 桌宠降级裸 ReAct 仍正常回复 + 不卡死。
若预分析失败导致整轮对话挂掉/无回复/traceback = safe-fail 没兜住（FAIL）。

**测后还原**：还原 analysis_model（或删 override）重启确认恢复。

**能逼出的 bug**：safe-fail 没真兜住 → 预分析失败把整条对话拖死（最危险的可用性回归）。

---

## 3. 结果汇总表（执行后填）

| case | 维度 | ★ | 坐标 | 动作摘要 | 截图 | log 证据 | 判定 |
|---|---|---|---|---|---|---|---|
| TC-0 | 闲聊覆盖率度量 | | | | | 命中率 ___/10 | |
| TC-1 | 闲聊短路 0 LLM 不拖慢 | ★ | | | | | |
| TC-2 | debug 取证门控 | ★ | | | | | |
| TC-3 | 多症状抓主要矛盾 | ★ | | | | | |
| TC-4 | 异体自检 fresh-context | ★ | | | | | |
| TC-5 | 收敛止损诚实报告 | ★ | | | | stop_reason=___ | |
| TC-6 | 澄清出口多轮不断裂 | | | | | | |
| TC-7 | 非闲聊只调 1 次 LLM | | | | | | |
| TC-8 | code 模式不受影响 | | | | | | |
| TC-9 | kill-switch 零回归 | ★ | | | | | |
| TC-10 | safe-fail 不卡死 | | | | | | |

---

## 4. 通过线

- **★ 必过（一票否决）**：TC-1 / TC-2 / TC-3 / TC-4 / TC-5 / TC-9。任一 FAIL → 不算完成，回 plan 修。
- 非 ★ 用例 FAIL 记录为 bug，按严重度决定是否阻断。
- 全 PASS（或 ★ 全 PASS + 非 ★ 受控/best-effort 标注清楚）后更新 `STATUS/status.md` §3 新增「问题处理流水线」模块行 + §4 里程碑（项目硬纪律）。
- 截图/日志证据存 `plans/manual-results-2026-06-24-problem-pipeline/screenshots/`，本目录只放用例定义。

---

## 5. 汇总

| 项 | 值 |
|---|---|
| 用例总数 | 11（TC-0 前置度量 + TC-1~TC-10） |
| ★ 必过数 | 6（TC-1/2/3/4/5/9） |
| 是否需 windows-mcp 真测 | **是**（每个 TC 真坐标点击 + 真中文输入 + 截图 + log grep；禁 WS 直注/pytest/import 当 UI 证据） |
| 需改 config 重启的 TC | TC-5（收紧 gate）/ TC-9（enabled=false）/ TC-10（坏 analysis_model）——测后均须还原 |
| best-effort / env-limited 的 TC | TC-2（依赖 LLM 是否想直接下结论）/ TC-3（principal 选择质量不评判）/ TC-4（打回-修正依赖 LLM 先出错）/ TC-6（依赖 LLM 判歧义≥0.7）/ TC-8（依赖 code 入口是否可达） |
