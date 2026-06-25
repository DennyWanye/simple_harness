# BUG-B 修复 plan — 意图路由（chitchat vs 真问题）对齐最佳实践

> **状态**：📋 待执行（spec-first；本文为 spec/plan，未动代码）
> **创建**：2026-06-25
> **严重度**：P0（默认配置下七步流水线形同虚设 —— 真实非闲聊问题被静默丢进闲聊快路径）
> **关联**：spawn `task_742d3399`（本 plan 取代/细化它）；WI-5(b) 续跑结论 commit `e3698466`；
> `plans/manual-results-2026-06-25-problem-pipeline-prod/RESULTS.md` §0 BUG-B；
> 记忆 `project_assembler_embedder_unreliable` / `project_pipeline_preanalysis_blockers`
> **被测代码（接地）**：
> - `backend/deskpet/agent/assembler/classifier.py`（TaskClassifier 三层级联）
> - `backend/deskpet/agent/intent_triage.py`（预分析 LLM + 短路派生）
> - `backend/main.py:1994-2002`（`build_default_assembler(llm_registry=None)`）

---

## 0. 问题陈述（BUG-B 是什么）

收到用户消息后，系统要决定：**走完整七步问题流水线（真问题）还是闲聊快路径短路（裸 ReAct，0 次预分析 LLM）**。
当前这个 routing 决策由**组装期 `TaskClassifier`** 把关，而它的最终兜底是 `default='chat'`（**fail-open**）。生产真测（2026-06-25）发现：无 code 关键词的真实问题（"光合作用为什么需要光"、纯中文 debug 追问）被判 `task_type='chat'` → 派生 `chitchat` → **整条流水线跳过**，"调查先于发言 / 抓主要矛盾 / 异体自检"全部失效。

**默认配置真实覆盖率 ≈ 40%**（RESULTS 诚实声明）；多数 ★PASS 是在三重非默认开关
（`DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT=1` + `DESKPET_DISABLE_CLARIFICATION=1` + `analysis_model=deepseek-v4-pro`）
下取得的。**本 plan 的终极验收 = 关掉所有 bypass 开关，默认配置下真能用。**

---

## 1. 根因结构（代码级，接地）

### 1.1 两个意图判定器并存 + lossy 桥接
| 判定器 | 位置 | 产物 | 驱动什么 |
|---|---|---|---|
| 组装期 `TaskClassifier` | `classifier.py` | 8 类 `task_type`（chat/code/recall/web_search/plan/emotion/command/task） | **上下文组装**（哪些 component / persona / 工具 / skill 进 bundle）|
| 预分析 `IntentTriage` | `intent_triage.py` | 7 类 `problem_type`（chitchat/factual_qa/debug/...） | **流水线短路 / 澄清 / 主要矛盾** |

两者用 `_TASKTYPE_TO_PROBLEM`（`intent_triage.py:43`）做**有损映射**，`chat→chitchat`、`emotion→chitchat`。

### 1.2 三层级联在生产全失效 → 默认 `chat`
1. **rule 层**（`classifier.py:62-92`）：只认 code/报错/python/搜索/计划/情绪关键词。**无关键词的真问题 miss**。
2. **embed 层**（`classifier.py:275-319`）：依赖组装期 embedder，真机多次 `component='memory' status='timeout'`（lock 竞争，见记忆 `project_assembler_embedder_unreliable`）→ 返回 None。
3. **llm 层**（`classifier.py:321-374`）：**`main.py:1998` 硬传 `llm_registry=None` → 该层彻底关闭**；且默认 `llm_model='claude-haiku-4-5'`（`classifier.py:228`）当前 relay 根本没有。
4. → `default 'chat'`（`classifier.py:267-273`）。

### 1.3 fail-open 方向错了（核心反模式）
短路的代价**不对称**：
- 假阴性（真问题误判 chitchat → **跳过流水线**）= 产品形同虚设，**灾难**。
- 假阳性（闲聊误判真问题 → 多跑一遍流水线）= 多花几秒延迟，**可接受**。

→ 兜底**必须倒向能力侧**（跑流水线），而非便宜侧（chat）。当前恰好反了。

### 1.4 WI-5(b) 已修的部分（**不可回退**，必须加回归测试钉死）
- `intent_triage.py:186`：**不再**据 classifier task_type 做早期纯规则短路。
- `intent_triage.py:192`：prompt 去掉 `[系统初判类型]` hint → deepseek 裸判（修 BUG-C 级联带偏）。
- `intent_triage.py:205-209, 218-221`：safe-fail（LLM 失败/超时/畸形 JSON）**绝不派生 short_circuit**，降级裸 ReAct。
- 净效果：**short_circuit 现在要求预分析 LLM 成功 + 明确判 chitchat + 低歧义**才成立。

### 1.5 残留 BUG-B（本 plan 要修的）
1. **短路完全押在一次脆弱的 LLM 调用上**：LLM 一挂（BUG-A relay 抖动）就 safe-fail 到裸 ReAct。"不跳过"是对的，但**真挂时每条挨 30s 超时**；且**没有便宜的确定性快路径**给真·闲聊。桌宠会收到海量"你好/在吗/哈哈/晚安"，每条都打一次易超时的 LLM 往返（或全裸 ReAct）= 另一种 UX 退化。
2. **组装期 classifier 仍是坏的，且仍驱动上下文组装**：真 `code` 问题被判 `chat` → 即便流水线现在会跑，它拿到的 persona/工具/skill bundle 仍是错的（闲聊态人格给 code 干活，见 `persona.py:107` 注释）。**BUG-B 只补了短路症状，routing 大脑还是死的。**
3. **两个 source of truth + 有损映射 = 漂移隐患工厂**。

---

## 2. 横向调研：四个 harness 怎么做 routing

| Harness | routing 机制 | 关键点 |
|---|---|---|
| **Claude Code** | **无事前分类器**。单一 agent loop，模型在生成的一部分里自行决定是否调工具（**in-band**）。"hi" 素回，"fix this bug" 伸手够工具 | 不存在"落了就降级到 dumb path"的旁路；成本控制靠模型本身不过度调工具 + 工具结果才是贵的部分 |
| **Codex CLI** | 同为单循环 agentic，**不用 intent 分类去 gate pipeline**。approval mode 是 policy 不是 intent | 路由 in-band，无脆弱前置门 |
| **Hermes (Nous Research)** | **function-calling fine-tune 本身就是路由器**：`<tools></tools>`（ChatML）里给 schema，模型输出 `<tool_call>` 或素文本由 weights 判 | "让模型决定"直接烧进权重；hermes-agent 还提议把**复杂度→模型切换**做成 **agent 可调用的 tool**（issue #16525），即把"要不要升级处理"当成一次工具调用而非外部分类 |
| **OpenClaw** | 个人 AI 助手（2026-01 一周破 10 万星）。skill + 自主 tool-discovery，**in-band** 判断执行 | 不依赖固定事前分类门；把代码也当文本处理（无语义路由） |

**共识**：现代 agent harness **没有一个**用"独立的、会 fail-open 到 dumb path 的事前分类器"来 gate。路由要么 in-band（模型生成时自决），要么把"升级/降级处理"当成模型可观测的一次决策，且**永远倒向能力侧**。

> 来源：[Claude Code](https://www.eigent.ai/blog/openclaw-vs-claude-code)、[OpenClaw](https://github.com/openclaw/openclaw)、[Hermes-Function-Calling](https://github.com/NousResearch/Hermes-Function-Calling)、[hermes-agent issue #16525](https://github.com/NousResearch/hermes-agent/issues/16525)

---

## 3. 当前最佳实践（提炼 5 原则）

1. **In-band，让在环的能力模型决定** —— 不要再用独立脆弱前置分类器硬路由。DeskPet 已有在环预分析 LLM（IntentTriage），它就该是唯一意图来源。
2. **Fail-CLOSED，倒向能力侧** —— 任何不确定（分类器失败/超时/畸形/低置信）→ 跑完整流水线（或至少全上下文裸 ReAct），**绝不**默认闲聊短路。
3. **Single source of truth** —— 一次意图判定，pipeline 门和上下文组装都消费同一个结果，删掉有损 `_TASKTYPE_TO_PROBLEM` 桥。
4. **便宜确定性层只做"地板"不做"天花板"** —— 词法/规则只当 fallback 的下限保险，绝不当 fail-open 的最终兜底（记忆 `project_assembler_embedder_unreliable` 已立此规矩）。
5. **快路径用高精度 allowlist，不是 block-list** —— 只放行**明显**的寒暄（你好/谢谢/晚安/纯 emoji），假阴性（漏判→落到流水线）天然安全，假阳性靠构造稀有。取代危险的"default chat"。

---

## 4. DeskPet 修复方案

### 4.1 设计决策

- **D1 — 意图单一来源 = IntentTriage 预分析**。把组装期 `TaskClassifier` 从"门"降级为"**纯上下文组装提示**"，其输出**永不**再驱动 short_circuit（WI-5(b) 已实质如此 → 本 plan **形式化 + 加回归测试 + 代码注释/护栏**钉死，防有人重新接回去）。
- **D2 — Fail-CLOSED**：short_circuit **只允许**在 (a) 确定性寒暄 allowlist 命中，或 (b) 预分析 LLM 成功且明确返回 chitchat+低歧义 时成立。失败/超时/畸形/低置信一律不短路（(b) 已具备 → 补 (a) + 显式置信门）。
- **D3 — 确定性寒暄 allowlist（唯一允许的便宜快路径）**：在预分析 LLM 调用**之前**先过一个**高精度** allowlist（招呼/感谢/告别/纯 emoji/纯标点 + 长度 ≤ N + 无问号 + 无祈使动词 + 无 code token）。命中 → 直接 short_circuit（省一次 LLM）；**任何不命中 → 落到预分析 LLM → 流水线**。这给桌宠"你好"快路径而无需 LLM，且因是精度优先 allowlist 而安全。**这取代"default chat"**。
- **D4 — 修复/退役组装期 classifier（让上下文组装重新正确）**：
  - **D4a（先发，止血）**：把 `build_default_assembler` 的 `llm_registry` 真接上（参数已铺好，`main.py:1998` 只是传了 None）绑 **relay 可用的便宜模型**（**不是** relay 没有的 `claude-haiku-4-5`，用配置的 analysis_model / 一个 relay 便宜模型）；并把 `default='chat'` 改成**词法地板**（有 task/code 信号 → 倒向 task/code，仅 allowlist 式寒暄 → chat）。
  - **D4b（目标态，更对齐最佳实践）**：**合并成一次调用**。IntentTriage 已用 LLM 产 `problem_type` → 反向把它喂给组装器当 task_type，pipeline 门和上下文组装消费**同一个**结果，删掉冗余 classifier llm 层 + 有损 `_TASKTYPE_TO_PROBLEM`。组装器只保留 rule+embed 当"triage 之前必须先组装"的罕见兜底提示。

### 4.2 分阶段（按风险递增、价值递减排序）

> **Phase 1 单独就能让默认配置真能用**（解 P0），Phase 2/3 是架构正确性收尾。

**Phase 1（P0 止血，最小爆炸半径）= D2 + D3**
- WI-1：实现确定性寒暄 allowlist（新 helper，精度优先；含 emoji/标点/长度/问号/祈使/code-token 否决项）。
- WI-2：把 short_circuit 派生改为 **allowlist 命中 OR (LLM 成功且 chitchat 且低歧义)**；其余路径一律不短路（形式化 D2，加置信门）。
- WI-3：删/弃用 `DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT` bypass —— Phase 1 之后默认配置就该正确，bypass 不再需要（保留一版兼容期 warning 再删）。
- WI-4：回归测试钉死 WI-5(b) 已修行为（safe-fail 不短路、无 hint、无早期规则短路）+ 新 allowlist 边界用例。

**Phase 2（routing 大脑复活）= D4a**
- WI-5：`main.py:1998` 注入真 `llm_registry`（绑 relay 可用便宜模型）；classifier `llm_model` 默认改 relay 可用模型。
- WI-6：classifier `default` 兜底从 `chat` 改词法地板（fail-closed 倒向 task/code）。
- WI-7：embed 层超时不再静默吞（已 warning）→ 确认 fallback 落到 WI-6 词法地板而非 chat。

**Phase 3（架构收口，可选）= D4b**
- WI-8：调换 assemble() 调用次序，让 IntentTriage 的 problem_type 成为组装器 task_type 的来源；删冗余 classifier llm 层 + `_TASKTYPE_TO_PROBLEM` 有损桥。
- WI-9：单一意图来源后，更新 trace UI / 日志字段（避免双分类字段误导）。

### 4.3 不做什么（避免过度工程）
- 不引入新的独立 ML 分类模型 / 新依赖（记忆 `feedback_no_sandbox_constraints`：桌宠只防手滑级）。
- 不删 rule/embed 层（它们当"地板"仍有价值，省一次 LLM）。
- 不改流水线七步语义本身（本 plan 只修 routing 入口）。

---

## 5. WI 分解与改动面（file:line 接地）

| WI | 文件 | 改动 | 风险 |
|---|---|---|---|
| WI-1 寒暄 allowlist | `intent_triage.py`（新 `_is_obvious_chitchat()`）| 高精度确定性匹配 | 低（纯新增） |
| WI-2 短路派生改 fail-closed | `intent_triage.py:218-221` | allowlist OR (LLM∧chitchat∧低歧义)；其余不短路 | 中（核心逻辑） |
| WI-3 弃用 bypass flag | `intent_triage.py`（读 `DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT` 处）| 兼容期 warning → 删 | 低 |
| WI-4 回归测试 | `backend/tests/test_intent_triage*.py`（新/扩）| 钉死 WI-5(b) + allowlist 边界 | 低 |
| WI-5 接 llm_registry | `main.py:1998` | None → 真 registry，绑 relay 便宜模型 | 中（启动期装配） |
| WI-6 classifier 兜底改词法地板 | `classifier.py:267-273` | `default='chat'` → fail-closed 词法 | 中 |
| WI-7 embed 超时兜底确认 | `classifier.py:275-319` | 落 WI-6 地板非 chat | 低 |
| WI-8 合并单一来源（可选） | `assembler.py` / `intent_triage.py:43` | 删有损桥，调次序 | 高（架构） |

---

## 6. 验收（真测纪律 — HARD CONSTRAINT）

> 触发"真测" → 全局 `~/.claude/knowledge-base/windows-mcp-e2e.md` + 项目 CLAUDE.md 手测纪律强制生效。
> **不许**用 ws 直注 / pytest / import 查 registry / log grep 当 UI 证据。每 case：真坐标点击 + 真中文输入 + 截图 + tauri-dev.log 日志判定；动作前 declare `坐标=(x,y)|动作=|期望=`；失败 retry ≥3 不同 workaround 才标"环境受限"。

### 6.1 单测/集成（先决，非充分）
- `pytest backend/tests/test_intent_triage*.py`（WI-4 新用例全绿）。
- allowlist 边界表：`你好`/`谢谢`/`晚安`/`😄` → 短路；`你好，帮我看下这段为什么报错` / `光合作用为什么需要光` / `在吗？我代码崩了` → **不**短路。

### 6.2 真机 E2E（★ 一票否决，**默认配置，关掉所有 bypass 开关**）
| TC | 用例 | ★ | 期望硬证据（tauri-dev.log）|
|---|---|---|---|
| **BUGB-1** | 默认配置发"光合作用为什么需要光"（无 code 关键词） | ★ | `intent_triage.done problem_type='factual_qa' short_circuit=False`；**无** `pipeline.short_circuit`；流水线真跑 |
| **BUGB-2** | 默认配置发纯中文 debug 追问"刚那段为什么会越界" | ★ | `intent_triage.done problem_type ∈ {debug,factual_qa}` 且 `short_circuit=False` |
| **BUGB-3** | 发真·闲聊"你好呀" | | allowlist 命中 → `pipeline.short_circuit`；**0 次预分析 LLM 调用**（快路径，省 token）|
| **BUGB-4** | 预分析 LLM 故意打挂（relay 抖动/换无效模型）发真问题 | ★ | safe-fail → **不短路** → 裸 ReAct 仍答对（不静默丢进闲聊）|
| **BUGB-5** | 全程**不设** `DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT` / `DESKPET_DISABLE_CLARIFICATION` | ★ | 上述 BUGB-1/2 仍 PASS（证明默认配置真能用，bypass 可退役）|

**收敛标准**：默认配置真实覆盖率从 ≈40% → ≥ 85%；`DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT` 可删。

---

## 7. 风险 / 回退

- **R1 allowlist 误放行真问题**：精度优先 + 否决项（问号/祈使/code-token）兜底；假阴性安全。回退：allowlist 收窄到只剩招呼/感谢。
- **R2 接 llm_registry 引入每回合第二次 LLM 调用（成本）**：Phase 3（D4b）合并消除；Phase 2 期间用 relay 便宜模型 + 32 token 限。
- **R3 relay 上游抖动（BUG-A）干扰真测**：等稳定窗口或用 D4a 的便宜稳模型；safe-fail 已保证不短路，不阻塞本 plan 逻辑验证。
- **R4 改动触碰启动期装配（main.py）**：先 pytest + 启动 smoke，再真机；按端口隔离纪律跑 worktree（`DESKPET_BACKEND_DIR` + `DESKPET_PYTHON`）。

---

## 8. 执行顺序建议

1. 先 **Phase 1（WI-1~4）** —— 单独就解 P0，最小风险，先让默认配置真能用 + 真机 E2E（BUGB-1~5）跑绿。
2. 跑通后更新 `STATUS/status.md`（HARD 纪律）+ 退役 bypass flag。
3. 再 **Phase 2（WI-5~7）** 复活上下文组装正确性。
4. **Phase 3（WI-8~9）** 视收益决定是否做架构收口（单一意图来源）。
5. 全程可先用 codex gpt-5.5 子代理写实现，Claude 做 Lead + 真机验收（全局规范）。
