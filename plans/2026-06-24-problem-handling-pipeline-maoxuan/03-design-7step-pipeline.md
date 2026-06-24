# 03 · 七步问题处理流水线 — 架构设计

> 把毛选方法论（00 §2）与现代 agent 工程（02）落成一条可门控、可观测、Companion 也适用
> 的显式流水线。本文给**架构 / 每步契约 / 数据结构 / 事件 / 装配点 / flag**；逐函数代码改动见
> [`04-implementation-plan.md`](./04-implementation-plan.md)。

---

## 1. 毛选方法论逐条出处（方法论锚的硬底）

| 方法 | 出处 | 关键原文 |
|---|---|---|
| 没有调查就没有发言权 | 《反对本本主义》(1930) | "你对于那个问题不能解决吗？那末，你就去调查那个问题的现状和它的历史吧！……调查就像'十月怀胎'，解决问题就像'一朝分娩'。调查就是解决问题。" |
| 抓主要矛盾 | 《矛盾论》(1937) | "研究任何过程，如果是存在着两个以上矛盾的复杂过程的话，就要用全力找出它的主要矛盾。捉住了这个主要矛盾，一切问题就迎刃而解了。" |
| 具体问题具体分析 | 《矛盾论》 | "不同质的矛盾，只有用不同质的方法才能解决。" |
| 实践论闭环 | 《实践论》(1937) | "实践、认识、再实践、再认识，这种形式，循环往复以至无穷。" |
| 实事求是 | 《改造我们的学习》(1941) | "'实事'就是客观存在着的一切事物，'是'就是客观事物的内部联系，即规律性，'求'就是我们去研究。" |
| 弹钢琴 / 胸中有数 / 不耻下问 | 《党委会的工作方法》(1949) | "要学会'弹钢琴'……十个指头都动作，不能有的动有的不动。" / "任何质量都表现为一定的数量，没有数量也就没有质量。" / "不懂得和不了解的东西要向……下级请教。" |
| 集中优势兵力各个歼灭 | 《中国革命战争的战略问题》 | "集中优势兵力，各个歼灭敌人。" |
| 从群众中来到群众中去 | 《关于领导方法的若干问题》(1943) | "从群众中来，到群众中去。" |

来源：[矛盾论 marxists.org](https://www.marxists.org/chinese/maozedong/marxist.org-chinese-mao-193708.htm) · [党委会的工作方法 人民网](http://theory.people.com.cn/n1/2016/0226/c49157-28151895.html)

---

## 2. 总架构图

```
                      ┌─────────────────────────────────────────────┐
用户问题 A ──────────►│        ProblemHandlingPipeline（新编排器）     │
(main.py:_run_chat)   │   feature flag: features.problem_pipeline      │
                      │   默认 OFF → 完全短路回退现有链路（BC）         │
                      └─────────────────────────────────────────────┘
                                          │
  ┌───────────────────────────────────────┼───────────────────────────────────────┐
  │ PRE-LOOP（组装后、进 AgentLoop 前，main.py 内编排）                              │
  │                                                                                  │
  │  Step1 听诉求·辨意图   IntentTriage(新)  ──► <意图> 事件 + 注入 / (歧义高→澄清)   │
  │  Step3 抓主要矛盾      ContradictionAnalyzer(新) ──► <主要矛盾> 事件 + 注入        │
  │  Step4 定方案·弹钢琴    plan.py 升级(扩 companion) ──► <方案> 事件 + 注入          │
  │                         （Step2 取证门控 + Step5 执行 + Step6 自检在 loop 内）    │
  └──────────────────────────────────────┬───────────────────────────────────────┘
                                          │
  ┌───────────────────────────────────────┼───────────────────────────────────────┐
  │ IN-LOOP（agent_loop.py:AgentLoop.run）                                          │
  │                                                                                  │
  │  Step2 取证门控  EvidenceGate(新)   ── 拦截"未取证就下结论"的 end_turn           │
  │  Step5 执行      现有 ReAct 工具循环（复用）  ──► <执行> 事件（标注每步 observation）│
  │  Step6 异体自检  SelfCheckGate(整合 VerifyGate+StructuredReflection+评估子代理)   │
  │  Step7 收敛止损  ConvergenceController(整合 TerminationGate+self-check 三级)       │
  └──────────────────────────────────────────────────────────────────────────────┘
                                          │
                                    FinalEvent → WS / artifact
                                    <收敛> 事件（量化收敛判据 + 止损报告）
```

**设计原则**：
- **编排器薄、组件复用**：`ProblemHandlingPipeline` 只做"按 problem_type 决定哪几步跑 + 串接 + 发标签事件"，真活由各组件干。
- **PRE-LOOP 分析三步**（意图/主要矛盾/方案）产出**注入 system 消息 + WS 标签事件**，不改 LLM 调用本身。
- **IN-LOOP 三闸**（取证/自检/收敛）改造现有守门链，不新开循环。
- **默认 OFF 即完全回退**：flag off 时 `ProblemHandlingPipeline.enabled=False`，所有新分支早 return，走今天的链路，字节级 BC。

---

## 3. 每步契约（输入 / 输出 / 门控判据 / 映射代码）

### Step 1 · 听诉求·辨意图（IntentTriage，新）

- **方法论**：从群众中来 + 不耻下问 + 具体问题具体分析。
- **输入**：user_message、`ClassifierResult`（复用组装期已算的 task_type，避免重复 LLM）、最近对话摘要。
- **做什么**：一次轻量 structured-output LLM 调用（或对简单/闲聊类纯规则短路）产出 `IntentCard`：
  ```json
  {
    "restated_intent": "用一句话重述用户真正想要什么",
    "problem_type": "chitchat | factual_qa | debug | research | creation | multi_task | ambiguous",
    "ambiguity_score": 0.0,
    "clarifying_questions": ["最多 2 个，仅当 ambiguity_score ≥ 阈值"],
    "needs_investigation": true,
    "needs_decomposition": false
  }
  ```
- **门控判据**：`ambiguity_score ≥ intent.clarify_threshold`（默认 0.7）→ emit `ask_clarification`，**暂停**等用户答（复用现有 ask_clarification 机制），不进 loop。否则注入 `<意图>` system 提示 + 发 `chat_v2_intent` WS 事件。
- **短路**：`problem_type == chitchat` 且 `ambiguity_score` 低 → **整条流水线短路**，直接走裸 ReAct（闲聊不被拖慢，硬性能要求）。
- **映射**：复用 `classifier.py` 结果；新 `intent_triage.py`；装配在 `main.py` 组装后、plan 前。

### Step 2 · 先调查·后发言（EvidenceGate，新，IN-LOOP 硬门）

- **方法论**：没有调查就没有发言权 + 实事求是（核心闸①）。
- **输入**：`IntentCard.needs_investigation`、本轮 working_messages 中已发生的工具调用记录、receipt ledger。
- **做什么**：当 `needs_investigation==True` 且模型在**尚未发生任何"取证类工具调用"**（search/read/grep/inspect/...，可配 `evidence.investigative_tools` 白名单）时就想 `end_turn` 下结论 → **拦截**，注入 `<调查>` nudge："你还没做任何调查就要下结论。先用 {工具} 取证再回答（没有调查就没有发言权）。" + continue。
- **门控判据**：`needs_investigation && tool_calls_so_far ∩ investigative_tools == ∅ && stop_reason != tool_use` → BLOCK（上限 `evidence.max_nudges` 默认 2，超限放行避免死循环并记 `evidence_gate_exhausted`）。
- **映射**：新 `evidence_gate.py`；接在 `agent_loop.py` 守门链最前（completion_probe 之前）。

### Step 3 · 抓主要矛盾（ContradictionAnalyzer，新，PRE-LOOP）

- **方法论**：抓主要矛盾 + 矛盾主要方面 + 胸中有数。
- **触发**：仅当 `problem_type ∈ {debug, research, multi_task, creation}` 或 `IntentCard.needs_decomposition==True`（简单/闲聊/单一事实问答**不触发**，省 token）。
- **做什么**：一次 structured-output LLM 调用产出 `ContradictionMap`：
  ```json
  {
    "contradictions": [{"id":1,"desc":"...","severity":0.0,"aspect":"决定性的那一面"}],
    "principal": 1,
    "principal_aspect": "是数据问题还是逻辑问题——决定性矛盾方面",
    "attack_order": [1,3,2],
    "rationale": "为什么这是主要矛盾"
  }
  ```
- **输出**：注入 `<主要矛盾>` system 提示（"本次主攻：{principal.desc}；先解决它，其余次要矛盾随后弹钢琴统筹"）+ 发 `chat_v2_contradiction` WS 事件。
- **映射**：新 `contradiction_analyzer.py`；装配在 main.py Step1 之后、plan 之前。

### Step 4 · 定方案·弹钢琴（plan.py 升级，PRE-LOOP）

- **方法论**：具体问题具体分析（routing）+ 弹钢琴（统筹并行有主次）+ 集中优势兵力（先打主要矛盾）。
- **改造**：现有 `maybe_extract_plan` 三处升级：
  1. **解除 code-only 限制**：当 `problem_type ∈ {debug,research,multi_task,creation}` 时 companion 模式也生成计划（flag `plan.companion_enabled`）。
  2. **吃主要矛盾**：把 `ContradictionMap.attack_order` 作为计划排序输入，计划首步对准 principal contradiction（集中优势兵力）。
  3. **弹钢琴标注**：每步标 `parallelizable: bool`，可并行的次要子问题标注供后续 spawn 统筹（不强制并行，避免乱按）。
- **输出**：`<方案>` 事件 + 注入（已有 `chat_v2_plan` 复用）。
- **映射**：改 `agent/plan.py:maybe_extract_plan` + 新增 `plan_for_problem()` 入口。

### Step 5 · 执行（现有 ReAct，复用 + 标注）

- **方法论**：实践论（实践第一）。
- **改造**：**不改循环逻辑**，只在工具结果处补发 `<执行>` 标签元数据（标注本步 observation 摘要），供观测与 Step 6/7 量化。
- **映射**：`agent_loop.py` 工具结果 emit 处加 pipeline label（仅 flag on 时）。

### Step 6 · 异体自检·反思（SelfCheckGate，整合，IN-LOOP）

- **方法论**：实践→认识→再实践 + 非执行者打分（Anthropic）。
- **整合现有三件**（不重复造）：
  - `VerifyGate`（regex 抽 claim 对账 receipt）— 作"声明 vs 凭据"对账层。
  - `StructuredReflection`（5 段 JSON 反思）— 作 rebound 时的反思层。
  - **异体评分**：失败 N 次后调**独立子代理/fresh model**（复用 ephemeral_subagent_model + `_resolve_ephemeral_provider`）打分，非主 LLM 自评（"the agent doing the work isn't the one grading it"）。
- **门控判据**：`problem_type` 决定严格度（debug/creation→strict 对账 + 异体评分；factual→轻量；chitchat→跳过）。失败 → 注入 `<自检>` 反思 + continue；连续失败超上限 → 异体救援 → 仍不过 → 止损交 Step 7。
- **映射**：新 `self_check_gate.py` 编排器封装现有三件 + 按 problem_type 选档；替换 agent_loop.py 当前散落的守门 21/23。

### Step 7 · 回用户·判收敛·止损（ConvergenceController，整合，IN-LOOP 闸③）

- **方法论**：到群众中去 + 胸中有数（量化）+ 集中优势兵力/止损。
- **整合**：`TerminationGate`（轮数/墙钟/cost 硬上限）+ self-check 三级升压 + 量化收敛判据。
- **量化收敛判据**（胸中有数）：
  - 主要矛盾是否已解（principal contradiction 对应计划首步 done）。
  - 是否有未对账声明（Step6 ledger 全平）。
  - 资源是否触顶（iteration/cost/wallclock）。
- **止损报告**（不硬撑）：触顶仍未收敛 → 产出 `<收敛>` 事件含"已做什么 / 卡在哪 / 主要矛盾是否解 / 建议下一步"，诚实交付而非假装完成。
- **映射**：新 `convergence_controller.py` 薄封装 + 复用 TerminationGate；接 agent_loop.py 收尾判定。

---

## 4. 显式标签事件 schema（Hermes 式可观测）

新增 WS 事件类型（仅 flag on 时发，前端可选渲染"思考过程"卡片；不渲染也不影响）：

| 事件 | 触发步 | payload 关键字段 |
|---|---|---|
| `chat_v2_intent` | Step1 | `restated_intent, problem_type, ambiguity_score` |
| `chat_v2_contradiction` | Step3 | `principal, attack_order, rationale` |
| `chat_v2_plan`（复用） | Step4 | `steps[], 首步对准 principal` |
| `chat_v2_evidence_gate` | Step2 | `blocked: bool, reason, nudge_count` |
| `chat_v2_selfcheck` | Step6 | `passed: bool, mode, heterogeneous: bool, claims_unverified` |
| `chat_v2_convergence` | Step7 | `converged: bool, principal_resolved, stop_reason, report` |

后端日志同步打结构化 log（structlog）：`pipeline_step step=<n> problem_type=<t> ...`，便于真测审计与 grep。

---

## 5. Feature flag 总表（全默认 OFF，BC）

`backend/config.py` 新增 `[features.problem_pipeline]` 段：

| key | 默认 | 作用 |
|---|---|---|
| `enabled` | `false` | 总开关。off → 整条流水线短路回退现有链路 |
| `intent_triage` | `false` | Step1 |
| `intent.clarify_threshold` | `0.7` | 歧义澄清阈值 |
| `evidence_gate` | `false` | Step2 取证门控 |
| `evidence.max_nudges` | `2` | 取证 nudge 上限 |
| `evidence.investigative_tools` | 白名单（search/read/grep/inspect/web_search/deepresearch…） | 算"取证"的工具 |
| `contradiction_analyzer` | `false` | Step3 |
| `plan.companion_enabled` | `false` | Step4 计划扩到 companion |
| `self_check.mode` | `"off"` | Step6：off/shadow/light/strict（对齐 verify_gate 灰度路径） |
| `self_check.heterogeneous` | `true` | 失败 N 次后启异体评分子代理 |
| `convergence.report_on_stop` | `true` | Step7 止损报告 |
| `observability_events` | `false` | 是否发 `<标签>` WS 事件 |

**灰度路径**（对齐 verify_gate `shadow→strict`）：off（纯回退）→ shadow（跑但不阻塞、只发事件/日志）→ light（轻量门控）→ strict（全门控 + 异体自检）。出厂建议 shadow，真测确认不误伤闲聊/不拖慢后再升 light。

---

## 6. 与现有机制的去重 / 兼容声明

| 现有机制 | 本期处置 | 不重复造的保证 |
|---|---|---|
| TaskClassifier（8 类） | **复用**其结果喂 Step1，不另起分类 LLM | Step1 IntentCard 从 ClassifierResult 派生 problem_type |
| maybe_extract_plan | **升级**（扩 companion + 吃主要矛盾），非替换 | 同一函数加分支，flag off 时行为不变 |
| VerifyGate / StructuredReflection / external_evaluator | **整合**进 SelfCheckGate 编排，非废弃 | 三件原 API 不动，SelfCheckGate 只调度 |
| TerminationGate + self-check 三级 | **整合**进 ConvergenceController | 硬上限逻辑复用，只加量化收敛判据 |
| goal_checker | 与 Step7 并存（/goal 场景）；problem_pipeline 不接管 goal | 两条路径互不干扰，goal_mode 仍独立 |
| ask_clarification | **复用**为 Step1 澄清出口 | 不新造澄清通道 |
| CapabilityGate | 保留在 Step1 之前（拒绝类先挡） | 顺序不变 |

> **关键 BC 不变量**：`features.problem_pipeline.enabled == false` 时，`_run_chat` 与 `AgentLoop.run` 的可观测行为（消息序列、工具调用、事件流）与本 plan 落地前**逐字节相同**。这是 ★ 一票否决验收项（05 文档）。
