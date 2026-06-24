# 00 · PRD 与方法论总纲 — 问题处理流水线（毛选方法论锚）

> **Plan 目录**: `plans/2026-06-24-problem-handling-pipeline-maoxuan/`
> **创建**: 2026-06-24
> **状态**: DRAFT（待子代理对抗迭代 → EXECUTABLE-AS-IS → 用户 review）
> **一句话**: 把 DeskPet 当前"收到问题就裸 ReAct 反应式作答"的处理方式，升级为一条
> 以《毛泽东选集》方法论为骨、以现代 agent 工程（Claude Code / OpenHands / Hermes）为
> 肉的**显式七步问题处理流水线**，并把"取证门控 / 异体自检 / 收敛止损"三道闸做成主干。

---

## 1. 背景与四个核心问题的回答（需求对齐）

本 plan 直接回答用户提出的 4 个优化方向。详细论证分散在 01/02/03 文档，这里给结论。

### Q1. DeskPet 现在遇到一个问题 A 是怎么处理的？做哪些步骤？

**结论：Companion（默认会话）模式本质是"单步反应式"；Code 模式有弱结构但反思多默认关。**

> **本期范围锚（决策2，round-3）**：本流水线**只作用于主线程 Companion 桌宠对话**，**完全不碰 Code 模式**。
> Code 模式入口已于 2026-06-24 在产品侧暂时关闭（`Toolbar.tsx:26 CODE_MODE_ENTRY_ENABLED=false` + App.tsx 抑制
> `code_mode_suggest`），故本期可安心只做主线。下文凡谈"现状"会提到 code mode 仅为对照背景；Code 模式的流水线
> 优化**不在本 plan 范围**，待主线做实后另开 plan。

当前真实链路（完整带 file:line 见 [`01-current-flow-and-gaps.md`](./01-current-flow-and-gaps.md)）：

```
用户输入 → main.py:_run_chat
  → [预处理] 持久化 / Sentinel / CapabilityGate.classify_request（拒绝类直接挡）
  → [组装] ContextAssembler.assemble：TaskClassifier 三级分类(8 类) → 选 policy
            → 6 个 Component 并发 fanout（persona/memory/tool/skill/time/workspace）
  → [可选规划] maybe_extract_plan（仅 code mode + msg>40 字符，单次 LLM 出 ≤8 步）
  → [主循环] AgentLoop.run() 裸 ReAct：LLM 每轮自己决定调什么工具
            → TerminationGate（轮数/墙钟/cost 硬上限）
            → 第 10/20/30 轮注入 self-check 三级升压
  → [守门] completion_probe(规则,on) / VerifyGate(默认 off) / goal_checker(需 /goal)
            / external_evaluator(默认 off)
  → [收尾] FinalEvent → WS → 前端 / artifact
```

**关键短板（结构性，6 条，详见 01 §4）**：
1. Companion 模式**无显式"分析主要矛盾"步骤**——分类结果只用于选 context policy，不反馈给 LLM，也不分析"这个问题的核心难点是什么"。
2. 问题理解**全靠单次 LLM 前向推理**，无"重述意图确认 / 主动澄清"的结构化前置（`ask_clarification` 存在但靠 persona 软提示、code mode、LLM 随机调用）。
3. **规划能力只在 code mode 且 ≤8 步**，无子问题递归分解，计划注入后无机制强制遵循。
4. **三道真反思守门（VerifyGate / StructuredReflection / external_evaluator）默认全关**，生产实际跑"裸 ReAct + completion_probe"。
5. **分解能力（TaskGraph/spawn）是事后工具不是前置分析**，由 LLM 随机决定何时调。
6. **缺"调查→假设→验证"环**——当前是"理解(弱)→执行(强)→验证(多数关)"，"先想清楚再动手"只靠 persona 软建议。

### Q2. Claude / OpenHands(openclaw) / Hermes 怎么做问题分析和处理？

**结论：成熟 agent 都在"收到问题→给出处理"之间插入显式步骤，且把"收敛/止损"从模型决策里抽出交外层 controller。**详见 [`02-benchmark-claude-openhands-hermes.md`](./02-benchmark-claude-openhands-hermes.md)。

- **Claude Code / Anthropic**：核心循环 = **gather context（取证）→ take action（行动）→ verify work（自检）**；用独立子代理隔离 explore/plan/act；**验证由非执行者打分**（"the agent doing the work isn't the one grading it"）；需要时暂停澄清。
- **OpenHands（CodeAct，即 openclaw 可能所指的开源编码 agent）**：自然语言→分步计划→真实工具执行每步→每步跑测试→失败分析→迭代；**Controller–Agent–Runtime 三层**，把迭代上限/预算等收敛约束放外层 controller 强制，不寄望模型自觉刹车。
- **Hermes（Nous Research）**：把推理拆成**可命名显式标签**（`PLAN`/`EXECUTION`/`REFLECTION`/`SCRATCHPAD`/`UNIT_TEST`…），结构化、可观测、可评测；函数调用准确率显著高于隐式推理。
- **通用范式选型**：ReAct（需真实交互）/ Reflexion（失败长记性）/ Plan-and-Execute（步骤可预拆）/ ToT（早期决策强约束后续）——按问题性质 routing。

### Q3. 怎么优化"收到用户问题→分析→处理"的流程？

**核心方案：引入一条显式七步问题处理流水线 `ProblemHandlingPipeline`**，把现有散落、默认关、仅 code 模式的能力（TaskClassifier / plan / VerifyGate / reflection / TerminationGate / goal_checker）**编排成一条可观测、可门控的 Companion 主线主干**，并补三个新模块（意图分诊 / 取证门控 / 主要矛盾分析）。设计见 [`03-design-7step-pipeline.md`](./03-design-7step-pipeline.md)，逐函数代码改动见 [`04-implementation-plan.md`](./04-implementation-plan.md)。

**范围只作用 Companion 主线（决策2）**：本期**为 Companion 主线新增规划能力**（复用/改造 `maybe_extract_plan` 的能力，但**不修改 code 模式现有 plan 行为**，code 模式那条分支原样不动）；流水线**完全不碰 Code 模式**。

**不是推倒重来**——80% 是"接电 + 编排 + 升级现有组件"，新增模块只有 3 个轻量类。全部 feature-flag 门控、出厂即开（测试环境全量验证），flag 仅留作 kill-switch + 单步调试开关。

### Q4. 毛选处理事情的方法论（本次优化的方法论锚）

见下方 §2，作为整个 plan 的总纲。

---

## 2. 方法论总纲：《毛选》七步问题处理法 → agent 工程算子

> 完整出处与逐条论证见 [`03-design-7step-pipeline.md` §1](./03-design-7step-pipeline.md)。这里给"方法论 → 工程算子"对照总表，作为全 plan 的设计宪法。

| # | 毛选方法论 | 原则核心 | 工程算子（落到本流水线的哪一步） |
|---|---|---|---|
| ① | **没有调查就没有发言权**（《反对本本主义》）；"调查就像十月怀胎，解决问题就像一朝分娩" | 下结论前必须先取证，调查成本可远大于动手成本 | **取证门控**：未取证不得下结论/收尾（Step 2，硬门） |
| ② | **抓主要矛盾**（《矛盾论》《党委会的工作方法》）："用全力找出主要矛盾，捉住了它一切问题迎刃而解" | 多症状先排序，全力打主要矛盾及其主要方面，不平均用力 | **主要矛盾分析器**：结构化输出 contradictions + 排序（Step 3） |
| ③ | **具体问题具体分析**（《矛盾论》）："不同质的矛盾只有用不同方法才能解决" | 不同性质问题走不同 pipeline，反对一套模板套所有 | **routing**：按 problem_type 选差异化处理路径（Step 1/4） |
| ④ | **实践论**：实践→认识→再实践的螺旋；真理由实践检验 | act→observe→reflect→re-act 强制闭环，禁止没执行就下结论 | **执行 + 异体自检**闭环（Step 5/6） |
| ⑤ | **实事求是 / 一切从实际出发** | 决策 grounded in 当前 case 真实状态，禁脚本回放/import 冒充证据 | 全流程证据纪律（贯穿，对齐 `feedback_real_test_discipline`） |
| ⑥ | **弹钢琴**（《党委会的工作方法》）："十个指头都动作……统筹兼顾，有主有次" | 相关子问题并行但分主次，不是十指乱按也不是只按一个 | **统筹并行 + 串行攻坚预算分配**（Step 4） |
| ⑦ | **集中优势兵力各个歼灭** | 不四面出击，集中资源单点突破再转下一个 | **资源聚焦主要矛盾**（Step 4 预算分配 + Step 7 收敛） |
| ⑧ | **胸中有数**（《党委会的工作方法》）："任何质量都表现为一定的数量" | 决策要量化，用数据而非感觉判断收敛 | **量化收敛判据**：失败次数/覆盖/耗时（Step 7） |
| ⑨ | **不耻下问** | 善于倾听一线/求证，不懂就问 | **主动澄清**：意图歧义高时先问（Step 1） |
| ⑩ | **从群众中来，到群众中去** | 从用户真实诉求采集→提炼方案→执行→回用户验证 | **用户需求闭环**：意图分诊(来) + 回用户验证(去)（Step 1/7） |

**三道闸（本流水线相对裸 ReAct 的增量价值核心）**：
- **取证门控**（毛选①⑤）→ 堵"幻觉/凭记忆下结论"。
- **异体自检**（毛选④ + Anthropic 非执行者打分）→ 堵"自己批改自己"。
- **收敛止损**（毛选⑦⑧ + OpenHands 外层 controller）→ 堵"死循环烧预算"。

> **语义 7 步 / 实现上 Step1+3 共用 1 次 LLM（决策4）**：方法论上"意图分诊（Step1）"与"抓主要矛盾（Step3）"仍是
> **两个独立语义步骤**，文档照旧分述。但**实现上把这两次串行 LLM 往返合并成 1 次** structured-output 调用降延迟：
> 闲聊纯规则短路（0 次 LLM）；非闲聊调 1 次，返回 `{意图字段 + (复杂问题才填的)主要矛盾字段}`。性能红线：非闲聊
> 每问题只多 **1 次 gpt-5.5 调用**。三道闸与 7 步方法论精神不变。

---

## 3. 范围与非目标

**In scope**：
- 新增 `ProblemHandlingPipeline` 编排器 + 新模块：`IntentTriage`（决策4：意图分诊 + 主要矛盾分析合并为 1 次调用）/ `EvidenceGate`（取证门控）/ `SelfCheckGate`（异体自检编排）/ `ConvergenceController`（收敛止损）。
- 升级并接电现有组件（**为 Companion 主线新增 plan 能力，code 模式 plan 行为原样不动** / VerifyGate / StructuredReflection / 异体自检 / TerminationGate 整合为收敛 controller）。
- Hermes 式显式标签事件（`<意图>/<调查>/<主要矛盾>/<方案>/<执行>/<自检>/<收敛>`）→ WS 可观测 + 日志可审计。
- 全部 feature-flag 门控，**出厂即开（测试环境全量验证）**，flag 仅作 kill-switch + 单步调试开关。

**Non-goals（本期不做）**：
- **不碰 Code 模式**（决策2）：Code 模式入口产品侧已暂关，其流水线优化待主线做实后另开 plan。
- 不改语音管线 / Live2D 渲染 / 记忆底层 schema。
- 不引入新 LLM provider；**意图+矛盾分析默认复用主 LLM（gpt-5.5），不硬依赖 haiku**（决策3，模型可配，留空=主 LLM）。
- 不做沙箱/权限护栏（对齐 [[feedback_no_sandbox_constraints]]，桌宠只防手滑级破坏）。
- **不做生产级灰度 rollout ceremony**（决策1）：测试环境直接全开，flag 仅留 kill-switch + 单步调试；不做 shadow→light→strict 多档灰度、不做字节级 BC 快照比对。

---

## 4. 验收总纲（硬证据，详见 [`05-test-and-rollout.md`](./05-test-and-rollout.md)）

- **★ 一票否决（决策1 已降级口径）**：把 flag 关掉（`enabled=false` kill-switch）时，全套现有测试（2300+）**不回归**——这是 kill-switch 的回退价值证明。**不再做字节级行为快照 diff**（测试环境无须生产级 ceremony）。
- 七步流水线每步单测覆盖（输入/输出契约 + 门控判据）。
- **真机 windows-mcp E2E**（对齐项目硬纪律 [[feedback_real_test_discipline]]）：至少 3 类问题（debug 取证类 / 多症状主要矛盾类 / 闲聊直答类）走完整流水线，截图 + 抓 backend 日志确认 `<调查>/<主要矛盾>/<自检>/<收敛>` 标签事件按预期触发；闲聊类必须**不被流水线误拖慢**（短路证据）。
- **异体自检诚实降级（决策3）**：单模型中转站下，"异体" = **新开 context 的独立子代理 + 对抗式"尝试反驳"提示**（非执行者本人、无自我辩护包袱），**不保证是不同模型**——中转站只保证有主模型 gpt-5.5（config.toml:19），haiku/sonnet 只是别名、底层端点仍 gpt-5.5（真机实证 `model='sonnet' base='gpt-5.5'`）。评分模型走可配 `self_check_model`（留空=主 LLM）；若中转站日后有更便宜/不同模型，配过去即可。真链路证据 = 评分由独立 fresh-context 子代理产出。

---

## 5. 文档导航

| 文档 | 内容 |
|---|---|
| `00-PRD-and-methodology.md`（本文） | PRD + 四问回答 + 毛选方法论总纲 + 范围 |
| `01-current-flow-and-gaps.md` | Q1 详解：当前问题处理全链路（带 file:line）+ 6 条结构性短板 |
| `02-benchmark-claude-openhands-hermes.md` | Q2 详解：三家做法 + 通用范式选型 + 可借鉴点 |
| `03-design-7step-pipeline.md` | 七步流水线架构设计：每步输入/输出契约 + 门控判据 + 映射到现有/新代码 |
| `04-implementation-plan.md` | **可执行核心**：逐文件逐函数代码改动 + 新文件 + config key + 事件 + 装配点 |
| `05-test-and-rollout.md` | 单测 + 真机 E2E 用例 + flag 策略（kill-switch，无灰度）+ 回退守护 + 回滚 |
