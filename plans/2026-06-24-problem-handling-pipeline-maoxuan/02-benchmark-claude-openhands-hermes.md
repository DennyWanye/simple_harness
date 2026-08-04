# 02 · Q2 详解 — Claude / OpenHands(openclaw) / Hermes 怎么做问题分析与处理

> 联网调研，关键论断带来源。目的：提炼"成熟 agent 在收到问题→给出处理之间普遍插入哪些
> 显式步骤、各自堵什么失败模式"，作为七步流水线（03）的工程对标。

---

## 1. Claude Code / Anthropic — "取证 → 行动 → 自检"三段闭环

- 核心循环：**gather context（取证）→ take action（行动）→ verify work（自检）**。
- **Explore→Plan→Act 分阶段，各阶段用独立子代理隔离上下文**：Explore（只读检索定位、不改动）、Plan（先读代码理解再给策略）、General-purpose（需改动才上）。意图 = 把啰嗦探索关进子代理独立 context window，主对话保持聚焦（上下文工程）。
- **验证由非执行者打分**：推荐 verification 子代理让 fresh model 试图反驳结果——*"the agent doing the work isn't the one grading it"*（evaluator-optimizer 模式）。
- **需要时暂停澄清**：*"pause to ask for clarification when needed"*，不死守脚本；自己判断任务何时真完成（收敛）。
- **长任务跨 context window**：先搭框架 + 让模型写结构化测试 + 提供验证工具，让 agent 无需人类反馈就能自查（问题良定义、输出可客观度量时）。
- 6 模式中最相关：**orchestrator-workers**（中心 LLM 动态拆任务派子代理综合）、**autonomous loop**（每步从环境拿 ground truth）。

来源：[Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) · [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) · [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) · [Claude Code best practices](https://code.claude.com/docs/en/best-practices)

**→ 借鉴**：取证段独立 + 验证由非执行者 + 暂停澄清。直接对应本流水线 Step 2（取证门控）、Step 6（异体自检）、Step 1（澄清）。

---

## 2. OpenHands（CodeAct，原 OpenDevin）— 计划-执行-观测-迭代 + 外层 controller

- CodeAct 循环：**自然语言任务 → 分步计划 → 真实 shell/文件执行每步 → 每次改动跑测试 → 分析失败 → 迭代直到测试过/任务完成**。
- **Controller–Agent–Runtime 三层**：`AgentController` 作 supervisor 强制操作约束（对话迭代上限、budget）、管生命周期；`CodeActAgent` 只决策（LLM 响应→action）；Runtime 隔离沙箱。
- **Event Stream 状态机**：state = 一条按时间排列的 action+observation 事件流，承载全部历史。

来源：[OpenHands 论文 ICLR 2025](https://arxiv.org/html/2407.16741v3) · [OpenHands Agent Framework](https://www.emergentmind.com/topics/openhands-agent-framework)

**→ 借鉴**：**把收敛/止损（迭代上限 + budget）从 agent 决策抽出，交外层 controller 强制**，不寄望模型自觉刹车。对应本流水线 Step 7（收敛 controller）——DeskPet 已有 `TerminationGate` 雏形，本期整合升级为 `ConvergenceController`。

---

## 3. Hermes（Nous Research）— 显式"思维算子"标签化

- Hermes 3 把推理拆成**可命名显式标签**训练：`PLAN`/`EXECUTION`/`REFLECTION`/`REASONING`/`INNER_MONOLOGUE`/`SCRATCHPAD`/`UNIT_TEST`/`SOLUTION`，用 XML 标签结构化输出。
- 函数调用准确率显著（Hermes 2 Pro 90% vs 同规模通用 60–70%）。

来源：[Hermes 3 技术报告](https://arxiv.org/pdf/2408.11857) · [hermes-function-calling-v1](https://huggingface.co/datasets/NousResearch/hermes-function-calling-v1)

**→ 借鉴**：**把七步流水线每步做成 agent 可识别的显式标签/字段**（`<意图>/<调查>/<主要矛盾>/<方案>/<执行>/<自检>/<收敛>`）→ 可观测、可评测、可在日志审计。对应本流水线的事件层设计（03 §4）。

---

## 4. 通用范式选型表（按问题性质 routing）

| 范式 | 核心 | 堵的失败模式 | 适用边界 |
|---|---|---|---|
| 直接 / CoT | 一次推理直出 | 过度规划浪费资源 | 简单确定性任务 |
| **ReAct** | reason 与 act 交替 | 闭门造车脱离现实 | 需真实交互/查库、观测影响下一步 |
| **Reflexion** | 失败后反思整条 trace 存记忆 | 同一错误反复犯 | 可多轮迭代、需自我改进 |
| **Plan-and-Execute** | 先定全局计划再逐步执行 | 走一步看一步缺全局 | 步骤可预先拆清 |
| **Tree-of-Thoughts** | 思维空间搜索分支回溯 | 早期错误锁死后续 | 需前瞻、早期决策强约束后续 |

来源：[Agentic Reasoning Patterns: ReAct, Reflexion & ToT (2026)](https://servicesground.com/blog/agentic-reasoning-patterns/) · [Dynamic Planning: From ReAct to ToT](https://tao-hpu.medium.com/dynamic-planning-in-llm-agents-from-react-to-tree-of-thoughts-a3464a8b114e)

---

## 5. 提炼：成熟 agent 普遍插入的显式步骤 → 失败模式 → 机制

| 显式步骤 | 堵的失败模式 | 对应机制 | DeskPet 现状 | 本期 |
|---|---|---|---|---|
| 意图澄清 | 答非所问/错误前提 | pause-to-clarify | 软提示/code-only/随机 | **Step 1 结构化** |
| 调查取证 | 凭记忆/幻觉下结论 | Explore 子代理 / ReAct 观测 | 无硬门 | **Step 2 取证门控** |
| 主要矛盾/分解 | 眉毛胡子一把抓 | orchestrator 拆任务 / Plan | 无（仅 plan 列步骤） | **Step 3 主要矛盾分析** |
| 方案规划 | 无全局走一步看一步 | Plan-and-Execute / TODO | code-only ≤8 步 | **Step 4 routing+plan 扩展** |
| 执行 | — | CodeAct / 工具调用 | ✅ 已强 | Step 5 复用 |
| 自检/反思 | 自己批改自己/错误反复 | evaluator-optimizer 异体 / Reflexion | 默认全关 | **Step 6 异体自检接电** |
| 收敛/止损 | 死循环/烧预算 | 外层 controller budget | TerminationGate 雏形 | **Step 7 收敛 controller** |

**核心结论**：DeskPet **缺的不是单个能力（多数已有雏形），而是把它们编排成一条显式、可门控、Companion 也适用的主干**，并补齐"取证门控 / 主要矛盾分析 / 异体自检"三个最弱环。这正是七步流水线的设计动机。
