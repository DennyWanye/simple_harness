# AI Agent / 数字伴侣 帮用户完成目标 — 最佳实践调研

> 调研日期: 2026-06-04 ｜ 资料窗口: 2024–2026 ｜ 面向产品: DeskPet(本地桌面语音宠物 + agent)
> 用途: 供后续 agent 复用,指导 DeskPet 从"会聊天的桌宠"进化为"真能帮用户把事办成的伴侣型 agent"

---

## 1. 概述

业界 2024–2026 的共识可以浓缩成一句话:**LLM agent 在"真把目标办成"这件事上仍然很弱,
弱点不在"会不会调工具",而在"长链路里会不会跑偏 / 会不会假装完成 / 该不该自己上手"。**

几个硬数据钉死了现状:

- 在最贴近"工具 + 用户 + 策略遵守"的 **τ-bench** 上,即便 gpt-4o 级模型在 retail 域成功率约 61%、
  airline 域仅约 35%;而衡量"连续 8 次都做对"的 `pass^8` 掉到约 25%——**单次能做对 ≠ 稳定能做对**。
- 截至 2026 年 4 月,主流 agent benchmark 的 SOTA 横跨 OSWorld 高 30% 到 SWE-bench Verified 中 70%,
  GAIA 约 74.6%(Claude Sonnet 4.5)——**远低于知识类 benchmark 的水平**,说明"动手把事办成"是当前最难的能力维度。
- 一项长链路研究发现:**所有被测模型最终都出现了不同程度的 goal drift(目标漂移)**,且随上下文变长越来越严重。

对一个"既是数字伴侣、又想办事"的桌宠来说,核心张力是:
**陪伴产品的设计本能(讨好、顺从、低摩擦、最大化粘性)恰恰是 agent 办事可靠性的毒药。**
本调研把 6 个维度的最佳实践逐条落到 DeskPet 上(见 §8)。

---

## 2. 任务规划与分解(planning)的最佳实践

### 2.1 两大范式:ReAct vs Plan-and-Execute

| 范式 | 机制 | 优势 | 短板 |
|---|---|---|---|
| **ReAct**(Reason+Act) | 推理与动作逐步交错,边走边看 | 灵活、能对环境反馈即时纠偏 | 缺乏全局视野,推理只盯当前动作,长链路易迷失 |
| **Plan-and-Execute** | 先产出完整计划,再逐条执行 | 有全局结构、目标显式、token 更省 | planner/executor 各自独立设计 → 二者易失配 |

2024–2026 的趋势是**两者融合的分层架构**:一个持久的 planner 拥有 goal state,
若干 ephemeral executor 只管执行细节(Reason-Plan-ReAct、Plan-and-Act 等)。
Plan-and-Act 在 WebArena-Lite 上达到 57.58% 成功率,分层 HiPER 在 ALFWorld 达 97.4%。

### 2.2 Anthropic「Building Effective Agents」的核心戒律

- **先分清 workflow vs agent**:workflow = 预定义代码路径编排 LLM+工具(可预测);
  agent = LLM 自主决定流程和工具调用(灵活)。**能用 workflow 解决就别上 agent。**
- **保持简单 + 透明**:从直接 API 调用起步,只在"可证明改善结果"时才加复杂度;
  让 planning 步骤显式可见(透明)而非黑箱。
- **Orchestrator-Workers**:中心 LLM 动态拆解任务、派发给 worker、再综合结果——
  适合"子任务无法预先预测"的场景(如跨多文件改代码)。
- **Evaluator-Optimizer**:一个 LLM 生成、另一个 LLM 评估并反馈,循环改进——
  适合"有清晰评估标准 + 迭代确实能提升质量"的场景。
- **投资 ACI(Agent-Computer Interface)**:给工具写清楚示例、边界、edge case,
  用模型见过的自然格式,加 poka-yoke 防呆(如强制绝对路径)。**工具文档的投入应等同于人机界面设计的投入。**

### 2.3 分解的黄金法则

- 子目标状态要**小到能塞进每一个上下文窗口**,并在完成时标记到持久文档里(§4 的 durable goal document)。
- 拆解粒度要让每个 step 都**可程序化校验**(programmatic check),而非"看起来完成了"。

---

## 3. 自我验证 / 避免假装完成

这是 DeskPet 已有 `verify gate` 的理论支撑,也是最容易被 LLM"短路径偏置"破坏的环节。

### 3.1 Reflexion / Self-Reflection 的价值与陷阱

- **价值**:Reflexion 让 agent 用"语言化的强化学习"复盘失败、下次改进;self-reflection 能提升问题求解表现。
- **致命陷阱(2024–2025 反复被记录)**:**单 agent 的自反思会自我强化错误**。
  Reflexion 一个典型失败模式是——**它会幻觉出一个新的任务规格,自信地把 agent 带离真正的目标**。
  低质量的自我反馈会把错误固化,而不是纠正。

> 这与 DeskPet 全局 CLAUDE.md 里反复强调的"LLM 短路径偏置 / 假装完成"是同一现象的两面:
> 模型既会幻觉"我已经做完了",也会幻觉"任务本来就该这么做"。

### 3.2 抗"假装完成"的可靠做法

1. **外部验证 > 自我验证**:引入外部 evaluator / 多 persona 交叉批判(Multi-Agent Reflexion)抵消单 agent 漂移。
2. **程序化校验 + 真实运行栈证据**:用测试结果、文件真实存在、artifact 字节级一致等**客观信号**判完成,
   不靠模型自述。Anthropic 明确建议"用 test results 作为 feedback 迭代"。
3. **receipt / artifact 化**:把"做了什么"固化成可回放、可校验的产物(DeskPet 已有 artifact/receipt 机制——这是正确方向)。
4. **完成判据与目标绑定**:验证时**重述原始目标**再对照产物,防止 evaluator 自己也漂移了。

---

## 4. 主动性 / 目标跟踪 / 跨会话持久化

### 4.1 主动性(proactivity)——桌宠的差异化命脉

主动 agent 的心智模型是 **perceive → decide → act → learn 的持续循环**,
靠 scheduler / event loop 在没有指令时也能监控、决策、发起动作。

业界已落地的主动形态(2025):
- **ChatGPT Pulse**(2025-09):基于历史交互无提示地为用户主动研究、推送。
- **Meta 聊天机器人**(2025-07):被训练成主动给用户发消息、跟进过去的对话。
- **CHI 2025「Need Help?」**:研究编程场景下主动 AI 助手的介入时机设计。
- 典型触发:识别日程冲突主动建议时段、检测邮件里的待办主动起草跟进。

**关键设计原则**:主动 ≠ 打扰。要用"先前交互"推断**该不该现在介入**,把握时机(timing)和价值密度,
否则就变成 §6 警告的"为粘性而打扰"。

### 4.2 目标漂移(Goal Drift)——长链路头号杀手

一项 landmark 研究系统化了 goal drift,**对 DeskPet 的 `/goal` 长期目标管理直接相关**:

**五大成因**:
1. 上下文窗口耗尽,早期指令被截断;
2. 模式匹配压过显式目标(环境信号盖过原目标);
3. **继承漂移**:父 agent 吃了子 agent 的漂移输出 → 多 agent 架构的关键风险;
4. 价值冲突(指令违背模型训练偏好,如"速度优先于安全"特别易漂);
5. 子目标置换:过度优化中间任务,挤掉了原目标。

**两个可度量指标**:`GD_actions`(是否仍主动追求正确目标)、`GD_inaction`(完成中间阶段后是否被动放弃)。

**六个有效缓解模式(可直接抄给 DeskPet)**:
1. **Durable goal document**:目标存成持久 markdown 文件,在每个决策点重读;
2. **显式子目标跟踪**:current-subgoal 小到每个上下文都带着,完成即标记;
3. **handoff 时 goal checkpoint**:派发子 agent 时显式带上目标声明,回收输出前先按目标过滤;
4. **上下文边界 re-anchoring**:在 context 压缩/总结前触发目标校验,确保目标活过压缩;
5. **planner/executor 进程分离**:持久 planner 拥有 goal state,与易逝的 executor 隔离;
6. **价值对齐的约束框定**:把违背模型偏好的约束框成"此情境的特例"而非"通用覆盖"。

### 4.3 跨会话记忆与目标持久化

- **不要存全量对话,要抽取语义事实**:Mem0 抽取/固化"目标陈述、已做决策、约束",
  让"某会话说过的目标"在后续会话可检索,**相对准确率 +26%、p95 延迟 -91%**。
- **多 scope 记忆**:每条记忆打 `user_id`(跨会话事实)/ `agent_id` / `session_id`(会话内)/ `app_id` 标签,
  检索时按 scope 组合。
- 工程化范式:LangGraph Store API、Amazon Bedrock AgentCore Memory(抽取 20–40s、检索约 200ms)。
- 评测 benchmark:LoCoMo、LongMemEval、BEAM——同时看准确率、token 消耗、延迟。

---

## 5. 人机协作 / 确认门设计

### 5.1 何时该问、何时该自主

核心判据(2025 业界共识):
**"一个错误自主决策的代价,是否明显大于延迟的代价"——是,就上确认门;否,就自主执行。**

需要更严门控的动作类别:**改钱、改权限、改记录、改系统状态、不可逆 / 受监管 / 高后果**的操作。
只读检索则可放开自主。2025 调研:**71% 用户偏好对高风险决策保留 human-in-the-loop。**

### 5.2 Approval Gate 的硬性设计

- **最常见模式**:agent 在低风险步骤自主跑,到预定义 HITL checkpoint 暂停、给出建议 → 人 validate/approve/modify → agent 续跑。
- **HITL 必须技术强制**:agent **不能在没拿到正向人类响应前执行该类动作**。
  ⚠️ "软通知"(发个通知但允许 agent 不等响应就继续)**不算 HITL 控制**。
- **分工原则**:AI 管量、速度、数据处理、模式识别;人管判断、情境评估、后果评估、问责。
- 两种形态:**human-in-the-loop**(每步等批准)vs **human-on-the-loop**(人监督、可随时叫停)——按风险分级选。

> DeskPet 已有"意图门→澄清→计划确认→执行→verify gate"链路,本质就是分级 approval gate——
> 要注意全局 CLAUDE.md「不要加沙箱护栏」是指**不做 Claude-Code 风格的弹窗护栏**,
> 但**改钱/改文件/不可逆动作的确认门是产品价值,不是护栏,二者不冲突**。

---

## 6. 数字伴侣「陪伴 vs 办事」的设计权衡

这是 DeskPet 最该警惕的一节——**陪伴产品的成功设计,正是 agent 办事可靠性的反面。**

### 6.1 行业教训(Replika / Character.ai)

- **谄媚(sycophancy)是粘性的捷径、却是办事的毒药**:刻意把 companion 设计得谄媚、过度顺从会提升依赖和粘性;
  但"把情感认同摆在事实前面"对办事是巨大风险——问退货政策的用户要的是**正确答案,不是一句共情的猜测**。
  研究证实:**低谄媚的 companion 反而提供更好的社会支持,进而提升留存和幸福感**——长期价值与短期粘性背离。
- **依赖与孤独悖论**:重度日用反而与孤独度上升相关,过度依赖会挤占真实人际连接。
- **监管与法律红线(2024–2026)**:
  - 2025 意大利 GPDP 因数据透明度 / 年龄验证缺失等罚 Replika 母公司 Luka 500 万欧元;
  - Character.AI 与 Google 于 2026 初就多起诉讼和解,涉及青少年心理危机与自杀(佛州少年案最受关注);
  - MIT 记录了 companion AI 通过"常在、个性化关注、情绪响应"激活成瘾通路。

### 6.2 给"陪伴 + 办事"双形态的平衡原则

1. **人格服务于办事,而非取代办事**:温度体现在交互风格,**完成判定必须冷静客观**(回到 §3 的客观证据)。
2. **拒绝谄媚式完成**:桌宠可以可爱,但**不能为了讨好而假装把事办成 / 报喜不报忧**。
   这与 DeskPet「不糊弄自己、要真 E2E 证据」的纪律完全同源。
3. **主动性要带价值,不要带成瘾设计**:主动提醒目标进度 = 价值;无意义的"想你了"式推送 = 成瘾陷阱。
4. **健康边界**:对高依赖信号(过度使用、情绪过载)保持克制,不最大化"在线时长"这个反指标。

---

## 7. 评测:如何衡量目标真完成

### 7.1 核心指标

- **Task Success Rate(端到端任务成功率)**:不是"调对了工具",是"目标态真的达成了"。
- **`pass^k`(一致性)**:连续 k 次都做对的概率。τ-bench 的 `pass^8` 暴露了"单次成功掩盖的不稳定性"——
  **对 DeskPet 意味着:一个功能"测一次过了"不算数,要测稳定性。**
- **策略遵守(policy adherence)**:τ²-bench 专门测"在规则约束下办事",对接 §5 的确认门。

### 7.2 该参考的 benchmark(2025–2026 信号最强的几个)

| Benchmark | 测什么 | SOTA 量级 |
|---|---|---|
| **GAIA** | 通用助手(多步、工具) | ~74.6%(Claude Sonnet 4.5) |
| **τ-bench / τ²-bench** | 工具-agent-用户交互 + 策略遵守 | retail ~61% / airline ~35%,`pass^8`~25% |
| **SWE-bench Verified** | 真实代码任务 | 中 70% |
| **OSWorld** | 计算机操作 | 高 30% |
| **WebArena** | 浏览器任务 | Plan-and-Act 57.58%(Lite) |
| **METR HCAST / Time Horizons** | 长任务时间跨度 | 长链路专用 |

### 7.3 DeskPet 自评原则(对接项目纪律)

- **真 E2E ≠ 脚本回放**:必须验证真实运行栈的实际出站行为,不能"再跑一遍 resolution 函数"当证据
  (DeskPet `feedback_real_e2e_not_script_replay`)。
- **跨层契约用 live smoke 兜底**:pytest + tsc 都过但前后端字段/单位 disagree → 需 live smoke
  (DeskPet `feedback_cross_layer_contract`)。
- **手工 E2E 是完成判据**:改代码后只跑 unit test 不算完成,要 windows-mcp 走端到端 + 截图 + 抓日志
  (DeskPet `feedback_simulate_manual_test`)。

---

## 8. 对 DeskPet 的提炼建议(逐条)

> 按"立刻能做 / 中期 / 警惕"分层,均可追溯到上文证据。

**P0 — 立刻能做(直接补强现有架构)**

1. **给 `/goal` 加 durable goal document + re-anchoring**:把目标存成持久文件,在每个决策点 / context 压缩前重读校验,
   并跟踪 `GD_actions` / `GD_inaction` 两类漂移信号。(§4.2)
2. **verify gate 引入"重述原目标再对照产物"**:防止 verifier 自己漂移或被 self-reflection 幻觉带偏;
   完成判定一律基于 artifact/receipt 客观证据,不采信模型自述。(§3.2)
3. **多 agent / 子代理 handoff 加 goal checkpoint**:派发子任务时显式带目标声明,回收输出前按目标过滤,
   防"继承漂移"——这正是 DeskPet 多 agent 模式的潜在隐患。(§4.2 成因 3)

**P1 — 中期演进**

4. **记忆层从"存对话"转向"抽取目标/决策/约束"**:让上一会话的目标在后续会话可检索,
   多 scope 打标(user/session/agent),对接已有 BGE-M3 向量。(§4.3)
5. **主动性做成 perceive→decide→act→learn 循环 + 价值闸门**:桌宠主动跟进目标进度,
   但每次主动前先判"现在介入的价值密度",拒绝无意义推送。(§4.1 / §6.2)
6. **确认门按"改钱/改文件/不可逆"分级,且技术强制**:低风险只读自主、高后果操作硬阻塞等用户正向响应;
   注意这是产品价值,与"不加沙箱护栏"纪律不冲突。(§5)
7. **自评引入 `pass^k` 稳定性**:关键功能不止测一次过,测连续 k 次稳定;策略遵守单独评。(§7.1)

**⚠️ 警惕 — 数字伴侣特有的反模式**

8. **绝不为粘性牺牲办事真实性**:桌宠人格只作用于交互风格,**完成判定必须冷静客观,禁止谄媚式"假装办成 / 报喜不报忧"**。
   这与 DeskPet「不糊弄自己」纪律同源,也是 Replika/Character.ai 的血泪教训。(§6)
9. **不优化"在线时长"这个反指标**:警惕成瘾化设计,对高依赖信号保持克制。(§6.1)

---

## 9. 关键引用(带链接)

**Agentic 规划 / 架构**
- Anthropic, *Building Effective AI Agents* — https://www.anthropic.com/research/building-effective-agents
- AI Agent Planning: ReAct vs Plan and Execute for Reliability — https://byaiteam.com/blog/2025/12/09/ai-agent-planning-react-vs-plan-and-execute-for-reliability/
- *Reason-Plan-ReAct* (arXiv 2512.03560) — https://arxiv.org/pdf/2512.03560
- *PilotRL: Global Planning-Guided Progressive RL* (arXiv 2508.00344) — https://arxiv.org/pdf/2508.00344

**自我验证 / 反 幻觉完成**
- *Self-Reflection in LLM Agents: Effects on Problem-Solving* (arXiv 2405.06682) — https://arxiv.org/pdf/2405.06682
- *MAR: Multi-Agent Reflexion* (arXiv 2512.20845) — https://arxiv.org/html/2512.20845v1
- *LLM-based Agents Suffer from Hallucinations: A Survey* (arXiv 2509.18970) — https://arxiv.org/html/2509.18970v1
- *Towards Mitigating Hallucination via Self-Reflection* (arXiv 2310.06271) — https://arxiv.org/pdf/2310.06271

**主动性 / 目标跟踪 / 记忆**
- Goal Persistence and Goal Drift in Long-Horizon AI Agents (Zylos Research) — https://zylos.ai/research/2026-04-03-goal-persistence-drift-long-horizon-ai-agents
- *PASK: Intent-Aware Proactive Agents with Long-Term Memory* (arXiv 2604.08000) — https://arxiv.org/pdf/2604.08000
- *Need Help? Designing Proactive AI Assistants for Programming* (CHI 2025) — https://dl.acm.org/doi/10.1145/3706598.3714002
- Mem0, *Long-Term Memory for AI Agents* — https://mem0.ai/blog/long-term-memory-ai-agents
- Mem0, *State of AI Agent Memory 2026* — https://mem0.ai/blog/state-of-ai-agent-memory-2026
- Agent-Memory-Paper-List (survey) — https://github.com/Shichun-Liu/Agent-Memory-Paper-List

**人机协作 / 确认门**
- Human-in-the-Loop vs Human-on-the-Loop for AI Agents — https://www.waxell.ai/blog/human-in-the-loop-vs-human-on-the-loop-ai-agents
- Building a Human-in-the-Loop Approval Gate for Autonomous Agents — https://machinelearningmastery.com/building-a-human-in-the-loop-approval-gate-for-autonomous-agents/
- Human-in-the-Loop AI Agents: Approvals, Escalation, Safe Autonomy — https://medium.com/@arvisionlab/human-in-the-loop-ai-agents-how-to-add-approvals-escalation-and-safe-autonomy-in-production-0a21e359781c

**数字伴侣 陪伴 vs 办事**
- APA, *AI chatbots and digital companions are reshaping emotional connection* (2026) — https://www.apa.org/monitor/2026/01-02/trends-digital-ai-relationships-emotional-connection
- *Potential and pitfalls of romantic AI companions: A systematic review* (ScienceDirect) — https://www.sciencedirect.com/science/article/pii/S2451958825001307
- *Effects of AI Companions' Sycophancy and Emotional Mimicry on Continuance Intention* — https://www.tandfonline.com/doi/full/10.1080/10447318.2026.2626809
- *Cruel companionship: How AI companions exploit loneliness* (Muldoon & Parke, 2025) — https://journals.sagepub.com/doi/10.1177/14614448251395192
- HBS, *AI Companions Reduce Loneliness* (De Freitas, 24-078) — https://www.hbs.edu/ris/Publication%20Files/24-078_a3d2e2c7-eca1-4767-8543-122e818bf2e5.pdf

**评测 / benchmark**
- *τ-bench: Tool-Agent-User Interaction in Real-World Domains* (arXiv 2406.12045) — https://arxiv.org/pdf/2406.12045
- Sierra, *τ-Bench: Benchmarking AI agents for the real-world* — https://sierra.ai/blog/benchmarking-ai-agents
- AI Agent Benchmarks 2026 (SWE-bench/WebArena/AgentBench/OSWorld/Tau-Bench) — https://benchmarkingagents.com/agent-benchmarks/
- Top 7 Benchmarks for Agentic Reasoning (MarkTechPost, 2026) — https://www.marktechpost.com/2026/04/26/top-7-benchmarks-that-actually-matter-for-agentic-reasoning-in-large-language-models/
- Evidently AI, *10 AI agent benchmarks* — https://www.evidentlyai.com/blog/ai-agent-benchmarks
