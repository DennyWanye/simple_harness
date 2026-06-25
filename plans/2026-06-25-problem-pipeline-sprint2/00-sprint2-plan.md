# 七步问题处理流水线 — Sprint 2 计划（修缺口 + 上线就绪 + UX）

> **状态**：📋 待 review（决策点见 §5）
> **前置**：Sprint 1 = 流水线落地（[plans/2026-06-24-problem-handling-pipeline-maoxuan/](../2026-06-24-problem-handling-pipeline-maoxuan/)）+ 本轮生产验收真测（[plans/manual-results-2026-06-25-problem-pipeline-prod/RESULTS.md](../manual-results-2026-06-25-problem-pipeline-prod/RESULTS.md)）
> **被测文档**：[testcase/2026-06-25-problem-pipeline-production/manual-test.md](../../testcase/2026-06-25-problem-pipeline-production/manual-test.md)
> **最后更新**：2026-06-25

---

## 0. 背景：Sprint 1 真测结论（为什么有 Sprint 2）

七步流水线代码已落地、单测全绿、机制本身真机验证正确（11 项 ★ PASS：闲聊幂等/合并预分析/取证门/抓主要矛盾/异体自检/kill-switch/澄清多轮/safe-fail/重启隔离…）。

**但真机 E2E 挖出 4 个真 bug，其中 2 个让"默认配置下用户真能用"不成立**：

| Bug | 一句话 | 严重度 | 现状 |
|---|---|---|---|
| **BUG-B** | classifier fail-open 成 `chat` → 真实非闲聊问题被误当闲聊短路、整条流水线跳过 | **P0 上线拦路** | followup task_742d3399 |
| **BUG-D** | Step7 收敛止损报告在 max_turns 触顶时**不触发**（路径不可达） | **P0/P1** | followup task_bc7826ba |
| **BUG-A** | relay 对 `gpt-5.5 + stream + json_schema` 间歇 502 → 预分析挂到超时 | P1 | 真测 workaround=换 analysis_model |
| **BUG-C** | 预分析被 classifier 的 `chat` hint 带偏继续误判 chitchat（BUG-B 下游放大） | P2 | 真测开关绕过 |

**关键结论（opus 4.8 核对）**：默认配置下因 BUG-B，流水线对"无关键词非闲聊问题"形同虚设。本轮 11 项 PASS 多在"关闭闲聊短路 + 关闭澄清门"非默认配置下取得，证的是**机制对**，不是**默认能用**。默认配置真实覆盖 ≈40%。

**Sprint 2 = 把"机制对"变成"默认能用 + 体验好 + 上线就绪"。**

---

## 1. Sprint 2 目标（验收口径）

1. **默认配置下，真实非闲聊问题不再被误短路**（修 BUG-B）→ 不开任何开关，发"光合作用…"类问题走完整流水线。
2. **触顶能诚实止损**（修 BUG-D）→ max_turns/死循环触顶时产出 Step7"卡在哪+建议"报告，而非截断收尾。
3. **预分析对 relay 不稳健壮**（修 BUG-A）→ relay 502 时快速 safe-fail，不挂满超时；延迟可控。
4. **闲聊短路逻辑重做**（用户拍板项）→ 正确性 + 体验（误判可控 + 非闲聊延迟可接受）。
5. **默认配置全量真测回归**（依赖 1~4）→ TC-1~10 + IDEM-1~6 不开关重跑取默认证据，补 TC-5/TC-8/IDEM-5。

---

## 2. 工作项（WI）

### WI-1 ★（Y 重构）取消闲聊短路 + 每条走 deepseek 预分析 — P0
> **Y 拍板后本 WI 重构**：核心不再是"修 classifier"，而是**取消闲聊纯规则短路、让每条消息都走 deepseek-v4-pro 预分析**。classifier fail-open 修复降级为 WI-1b(P2，只影响组装质量)。

**核心改动（P0）**：
1. **删 intent_triage 早期 chitchat 短路**（intent_triage.py:185-198 的 `if derived_pt=="chitchat": return shortcircuit`）→ 每条消息都进 deepseek 预分析。本轮临时 env 开关 `DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT` 转正（删开关、默认就是不短路）。
2. **中和 prior_task_type hint**（intent_triage.py:210 `[系统初判类型] ... → {derived_pt}`）：classifier 不可靠时别喂 chitchat 倾向带偏 deepseek——chitchat 派生重映射 factual_qa 或干脆不喂 task_type hint，让 deepseek 裸判。
3. **config 默认 analysis_model = deepseek-v4-pro**（config.py:436，B1：通过 `_resolve_ephemeral_provider` 克隆生效，main.py:1710 已是此范式）+ `analysis_timeout_s` 30→≥45（M1，deepseek thinking 慢）。
4. **chitchat 轻收尾**：predeepseek 判 problem_type=chitchat → 走轻路径（不上完整七步取证/自检/收敛），保闲聊回复不啰嗦（虽非 0-LLM）。

**验收**：默认配置（无 env 开关）发任意非闲聊问题（含无关键词"光合作用…"）→ `intent_triage.done` 走完整流水线，**永不**被误短路；闲聊→`problem_type=chitchat` 轻收尾正常回复。删 `DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT` 开关。
**相关（绝对路径）**：`backend\deskpet\agent\intent_triage.py`(:185-210) / `backend\main.py`(:1701-1722 预分析装配/:1710 ephemeral provider) / `backend\config.py`(:431/:436)。

---

### WI-1b（降级 P2）修 classifier fail-open（组装质量）
> Y 取消短路后，classifier 的 task_type **不再决定流水线短不短路**，只服务上下文组装（哪些组件/预算）。fail-open 成 chat **不再让流水线形同虚设**，只让组装策略次优。故降 P2。
**问题（仅组装质量）**：组装期 TaskClassifier 三层级联 rule→embed→llm 全失效 → 默认 `chat`（classifier.py:267）。
- rule 层只认 code/报错/python/搜索/计划/情绪 关键词（classifier.py:62-92）→ 无关键词问题 miss
- embed 层撞 embedder lock 竞争 1500ms 超时（真机 4× `status='timeout'`）→ 返回 None
- **llm 层 main.py:1977 `llm_registry=None` 彻底关掉**（且默认 model=claude-haiku-4-5 relay 没有）

**拍板方案（D1）：接 LLM 层，模型 deepseek-v4-pro** —— ⚠️ 内部挑战 R1 修正 4 处致命坑，照下面做：
1. **【B1 致命】不能靠传 `llm_model` 参数**：`OpenAICompatibleAgentLLM` shim 的 `model` 参数被忽略（tool_use_shim.py:45-48「provider already locked at construction」）→ 传 `llm_model="deepseek-v4-pro"` 会被丢弃、实际跑 shim 绑定的主 LLM gpt-5.5。**正确做法（仿预分析 main.py:1710-1711）**：
   ```
   _clf_provider = _resolve_ephemeral_provider(_pp_base, "deepseek-v4-pro")
   _clf_shim = OpenAICompatibleAgentLLM(provider=_clf_provider)   # 不能复用 codify shim(绑主LLM)
   build_default_assembler(llm_registry=_clf_shim, llm_model="deepseek-v4-pro", ...)  # llm_model 仅日志
   ```
2. **【m3 近致命】classifier `llm_timeout_s=2.0`（classifier.py:229）**：deepseek thinking 30-60s 起步 → 2s 必超时 → return None → **fail-open 到 chat 复活**，接了等于没接。必须提到 **≥20s**（与预分析同量级）。
3. **【M1】config 默认必须改**：config.py:436 `analysis_model=""`（=主 LLM gpt-5.5）→ 改 **`"deepseek-v4-pro"`**，否则默认配置预分析仍跑 gpt-5.5 撞 BUG-A。同步 config.py:431 `analysis_timeout_s` 30→**≥45**（deepseek thinking 慢时逼近 30s）。
4. **删** classifier.py:17-21 + :228 + __init__.py:84 的 `claude-haiku-4-5` 默认/注释（误导）。
- 同时削 BUG-C：classifier 判准 → 喂 intent_triage 的 prior_task_type hint 也准。

**验收**：默认配置（无 env 开关）发"光合作用…"等无关键词非闲聊问题 → `intent_triage.done`（不再短路）；闲聊"你好"仍快回（**靠 WI-3 的 X 细化，见下**）。补 classifier LLM 层接电（真跑 deepseek）+ fail-open 回归单测；删临时开关 `DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT`。
**相关（绝对路径）**：`backend\deskpet\agent\assembler\classifier.py`(:229/:267/:333) / `backend\agent\tool_use_shim.py`(:45) / `backend\main.py`(:1710/:1977) / `backend\config.py`(:431/:436)。

---

### WI-2 ★ 修 Step7 收敛止损不触发（BUG-D）— P0/P1
**问题**：max_turns 触顶时 loop `range(max_iterations)`（agent_loop.py:823）先于 gate `allows_call`（:873，turns_used>=max_turns）耗尽（off-by-one，max_turns==max_iterations + record_turn 底部自增 termination.py:174）→ 走 loop-exhaustion warning（:2546）**绕过 ConvergenceController**（:878-906 的 stop_loss 到不了）；hallucination 又走 ErrorEvent（:2367）。**companion 默认下 Step7 止损路径实际不可达**。

**方案选项**（§5 决策点 D2）：
- **A（推荐）loop-exhaustion 也走 ConvergenceController**：循环正常耗尽时若 pipeline 开，调 `evaluate()` 出止损报告再收尾，而非只 warning。
- B 修 off-by-one：让 gate 在最后一轮能触发（allows_call 用 `>` 或 record_turn 移顶部）——小心别破坏迭代语义。
- C 把 hallucination/permanent_tool_error 等 allows_tool 触顶也接进 ConvergenceController（所有"非正常收尾"都出诚实止损）。
- **组合 A+C**：覆盖最常见的两类触顶。

**⚠️ 内部挑战 R1 修正 2 处（方案 A 接 :2546 的陷阱，plan 原没提）**：
1. **gate reason 必须手动覆盖**：:2546 处 gate **从未 terminate**，`gate.summary()["reason"]` 是合成 `"running"` → ConvergenceController 判 `resource_capped=False`（convergence_controller.py:57）→ **止损照样不触发**。必须仿 :882-884 把 summary 的 reason 覆盖成 `TerminationReason.HARD_MAX_TURNS.value` 再 evaluate。
2. **principal_resolved 不能硬编码 False**：否则"任务其实最后一轮做完了、只是没发 terminal stop_reason"会被误判成止损报告 → **正常完成倒退成"卡在哪/建议"**。需信号源：:2546 处检测最后一轮**是否产出 assistant final content / 有无 unanswered tool_calls**；或对纯轮数耗尽用区分措辞（"轮数用尽，已完成 N 步"vs"卡死"），别统一"卡在哪"。

**验收**：收紧 max_turns 真机触顶 → `convergence.stop_loss reason=error_max_turns` + 桌宠出诚实报告；**且正常完成的任务不被误判成止损**；制造死循环 → hallucination 也出止损报告。补集成测试断言"loop 耗尽时也出 stop_loss"+"正常完成不误触"。
**相关（绝对路径）**：`backend\agent\agent_loop.py`(:647/:823/:873-906/:1442/:2546) / `backend\agent\termination.py`(:138-174) / `backend\deskpet\agent\convergence_controller.py`(:48-76)。

---

### WI-3（Y 下大幅收缩）闲聊短路重做 — 主体并入 WI-1，延迟优化留 Sprint 3
> **Y 拍板后**：短路逻辑重做 = WI-1 的"删短路 + 每条走 deepseek + chitchat 轻收尾"，**已并入 WI-1**。本 WI 只剩"延迟优化"，而 Y **显式接受闲聊也慢（5-18s）**→ 延迟优化（①快模型变体/②非阻塞先轻反应/③分级轻路径）**整体留 Sprint 3**。本 Sprint WI-3 ≈ 空（仅在 WI-1 里做 chitchat 轻收尾）。

<details><summary>（原 WI-3 延迟优化备选，留 Sprint 3）</summary>

**背景**：Y 下每条非闲聊+闲聊都走 deepseek 预分析（5-18s），首响应延迟是已知成本：
- (a) **误判**：依赖 classifier（BUG-B），把真实问题误短路（WI-1 治根因，本 WI 治短路侧）。
- (b) **非闲聊延迟**：每条非闲聊问题先 +1 次预分析（gpt-5.5 thinking ~10-15s，relay 慢时更久）才进主 loop。

**拍板方案（D3）：判断统一交 deepseek-v4-pro，与 WI-1 合并实现**
- 短路/problem_type 判断**直接用 deepseek-v4-pro**（不靠脆弱 rule/embed/haiku 一票）。与 WI-1 同根：classifier LLM 层 + 预分析 analysis_model 都是 deepseek-v4-pro。
- **延迟**：deepseek-v4-pro 实测预分析 stream+json_schema ~5-18s（比 gpt-5.5 thinking 稳）。若要更快可叠 ① 关思考/换更快变体（后续）。
- ⚠️ **§5.2 张力**：方案 X（保留 rule/embed 快路径 + LLM 兜底，闲聊不退化）vs Y（每条都 LLM 判，闲聊也慢）。**codex 挑战须给出 X 的可行设计 + 量化闲聊延迟影响**。
- ② 非阻塞（先轻反应后台预分析）/ ③ 分级（简单问答走轻路径）**留 Sprint 3**（②有竞态风险，本 Sprint 不做）。
**验收**：判断准（不误短路真实问题，deepseek-v4-pro 实测分类正确）；闲聊不退化（方案 X）；非闲聊延迟可接受。
**相关**：intent_triage.py:185-210 / classifier.py / main.py PRE-LOOP 6826-6890 / RESULTS 已知 UX 成本段。
</details>

---

### WI-4 预分析对 relay 不稳健壮化（BUG-A）— P1
**问题**：relay 对 `gpt-5.5 + stream:True + json_schema strict` 间歇 502/503（chat_with_tools 强制 stream openai_compatible.py:262）→ 预分析挂到 `analysis_timeout_s`(默认30s)满才 safe-fail。

**拍板方案（D4）：默认模型换 deepseek-v4-pro + 快降级 A + fallback C**
- **B 已拍板**：预分析 + classifier LLM 层默认模型 = **deepseek-v4-pro**（实测 stream+json_schema 稳，直接规避 gpt-5.5 间歇 502）。落进 config 默认（不只 dev override）。
- **A**：预分析检测到 502/503/连续无字节，别等满 `analysis_timeout_s` 才 safe-fail，**立即降级**（降感知延迟）。
- **C**：stream+json_schema 仍失败时 fallback 到 json_object 或 non-stream 重试一次（probe 实测两者稳）作二次保险。
**验收**：默认 deepseek-v4-pro 下预分析稳定 `intent_triage.done`；偶发 502 时 ≤5s safe-fail 或 fallback 成功，不挂满超时。
**相关**：openai_compatible.py:252-275 / intent_triage.py:208-217 / main.py:1701-1722（analysis_model 绑定）/ config.py ProblemPipelineConfig。

---

### WI-5 默认配置全量真测回归（依赖 WI-1/2/4）— P1
不开任何 env 开关、analysis_model 回默认（或 WI-4 定的新默认），重跑：
- TC-1~10 + IDEM-1~6 全套，取**默认配置真证据**（当前多在非默认配置取得）
- 补本轮未做：**TC-5/IDEM-6**（WI-2 后真出 stop_loss）、**TC-8 code 模式**（真测或显式 env-limited）、**IDEM-5 自愈轮**（主动构造坏 analysis_model + 还原对照）
**验收**：testcase 100% 覆盖（PASS / best-effort / env-limited 标注清楚），默认配置可上线门全过。

---

### WI-6（可选）测试基建 + 清理 — P2
- 补集成测试：classifier fail-open 回归、loop-vs-gate 触顶谁先、短路正确性。
- 清理本轮临时物：`DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT` / `DESKPET_DISABLE_CLARIFICATION` 两个测试开关（WI-1/2/3 落地后删或转正式 config）；probe 脚本保留为 relay 诊断工具。

---

## 3. 排期 / 依赖

```
WI-1 (classifier fail-open) ──┐
                              ├──► WI-5 (默认配置全量回归) ──► 上线门
WI-2 (Step7 止损) ────────────┤
WI-4 (relay 硬化) ────────────┘
WI-3 (短路重做+UX) ── 与 WI-1 部分重叠，可合并 ──┘
WI-6 (测试基建) ── 贯穿
```
- **第一批（P0，并行）**：WI-1 + WI-2（两个上线拦路；可两个子代理并行）
- **第二批**：WI-4（relay 硬化）+ WI-3（短路重做，吃 WI-1 成果）
- **第三批**：WI-5 默认配置全量回归 → 上线门
- WI-6 贯穿

---

## 4. 风险

| 风险 | 缓解 |
|---|---|
| WI-1 词法兜底过度 → 把真闲聊也判成 task 拖慢 | 词法只在 rule+embed 都 miss 时兜底；保留短问题/明确情绪词短路；回归单测两侧都测 |
| WI-2 改 off-by-one 破坏现有迭代语义 | 优先选 A（加 ConvergenceController 调用，不动循环计数）；集成测试守 |
| WI-3 非阻塞预分析引入竞态（先回复后校正打架）| 先做①快模型（低风险），②非阻塞作为独立后续评估 |
| WI-4 改默认 analysis_model 影响其它读该模型的路径 | analysis_model 仅作用预分析；core 主对话不受影响 |
| relay 持续不稳 | WI-4 的 fallback + safe-fail 兜底；真测前先 probe 确认 relay 健康 |

---

## 5. ⭐ Review 决策（已拍板 2026-06-25）

| # | 决策 | **用户拍板** |
|---|---|---|
| **D1** | WI-1 classifier 怎么修 | **接 LLM 层判断，模型用 deepseek-v4-pro**（不再 fail-open；不靠 claude-haiku/None）|
| **D2** | WI-2 止损怎么接 | **A+C**（loop 耗尽走 ConvergenceController + 全触顶接）|
| **D3** | WI-3 短路/分析判断 | **直接用 deepseek-v4-pro 做判断**（统一到 LLM，不靠脆弱 rule/embed/haiku）|
| **D4** | WI-4 relay 硬化 + 默认模型 | **预分析默认模型 = deepseek-v4-pro**（实测 stream+json_schema 稳，一并解 BUG-A）+ 快降级 A + fallback C |
| **D5** | Sprint 范围 | **全做 WI-1~6（含测试基建）** |
| **D6** | 执行方式 | **先 codex gpt-5.5 对抗挑战 plan 到 EXECUTABLE-AS-IS，再实现** |

### 5.1 拍板后的统一主线（D1+D3+D4 合并）
**核心**：把"是不是闲聊 / problem_type 判断"统一交给 **deepseek-v4-pro LLM**，不再信脆弱的组装期 classifier（rule miss + embed 超时 + haiku 没有）做短路决策。
- **WI-1**：classifier LLM 层接电（main.py:1977 `llm_registry` 传真值 + `llm_model="deepseek-v4-pro"`）→ rule/embed miss 时 LLM 兜底判，**不再默认 chat**。
- **WI-3**：预分析 `analysis_model="deepseek-v4-pro"`（已设），由它可靠产出 problem_type（含真闲聊）。
- **WI-4**：deepseek-v4-pro 实测 stream+json_schema 稳 → 同时解 BUG-A（gpt-5.5 间歇 502）。
- 一条线同时解 **BUG-A（relay 不稳）+ BUG-B（fail-open）+ BUG-C（hint 带偏）**。

### 5.2 ✅ X-vs-Y 已拍板：**Y（真·每条都 deepseek-v4-pro 判）**
**用户拍板 Y**：接受闲聊也走 LLM（5-18s）+ 每条消息一次 LLM 成本，换"判断统一、最准、逻辑最简"。**显式放弃 TC-1"闲聊 0 LLM 秒回"红线**（改为"闲聊也走 LLM，但路径轻/不上完整七步"）。

**Y 的关键简化（重要）**：既然每条消息都走 deepseek 预分析，**「闲聊纯规则短路」直接取消**（不再靠组装期 classifier 的 task_type 决定短不短路）：
- **intent_triage 删掉早期 chitchat 短路**（intent_triage.py:185-198）→ 每条消息都跑 deepseek 预分析，由它产出 problem_type（含 chitchat）。chitchat 仍可走"轻收尾"（不上完整七步取证/自检），但 LLM 已调用（非 0-LLM）。
- **BUG-B（classifier fail-open）的流水线影响自动消解**：不再有短路被误触发 → 真实问题不会被误当闲聊跳过。classifier fail-open **降级为"只影响上下文组装质量"的次要问题**（WI-1 改为 P2，见下）。
- **BUG-C（hint 带偏）**：删短路后预分析仍可能被 `[系统初判类型] chat` hint 带偏 → **中和 hint**（classifier 不确定时不喂 chitchat 倾向，或干脆不喂 task_type hint 让 deepseek 裸判）。

**TC-1 验收口径改写**：原"闲聊 0 次 LLM"作废 → 新口径"闲聊走预分析但**不上完整七步**（problem_type=chitchat → 轻收尾，无取证/自检/收敛），延迟可接受"。

### 5.3 内部挑战 R1 结论（codex 本环境挂起，用内部 architect opus 对抗替代）
**VERDICT: NEEDS-FIX → 已修订**：修了 B1(shim 忽略 model→classifier/预分析须用 `_resolve_ephemeral_provider` 克隆 deepseek provider，传 model 参数无效)/B2(止损 :2546 须覆盖 gate reason=HARD_MAX_TURNS + principal_resolved 不能硬编码 False)/M1(config.py:436 默认须改 deepseek-v4-pro + timeout≥45s) 致命修订，并入 WI-1/2/4。
> 注：m3(classifier llm_timeout 2s) 在 Y 下**降级**——Y 取消短路、不再靠 classifier 的 task_type 决定流水线，classifier 仅服务上下文组装；其 LLM 层是否接 deepseek、超时多少，归到 WI-1(P2) 的"组装质量"范畴，不再是流水线关键路径。
**下一步**：跑 R2 内部对抗挑战（验 Y 重构后的 plan）→ 收敛 → 派实现。

---

## 6. 范围外（不在本 Sprint）
- 七步流水线新增能力（只修缺口 + 上线就绪，不加新 step）
- code 模式分支改造（决策2 字节不动）
- relay 中转站本身的稳定性（上游，非项目侧）
</content>
