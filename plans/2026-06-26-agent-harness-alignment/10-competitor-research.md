# 10 — 竞品调研：Claude Code / Codex / Hermes 的独门机制（证据底座）

> 本文档只记录**三家 agent harness 的客观机制**（2026 现状），作为 [00-PLAN.md](./00-PLAN.md) gap 分析的证据底座。
> DeskPet 自身现状映射与缺口判定见 [20-gap-analysis.md](./20-gap-analysis.md)。
> 信源：官方文档 + 2026 技术博客（见文末）。调研日期 2026-06-26。
>
> ⚠️ **第三家 = Hermes Agent（用户 2026-06-27 确认"跟 hermes 类似的"）**。Hermes Agent（NousResearch，开源 MIT，2026-02）独门点：持久记忆 + **自主/自进化技能创建** + **本地推理**(Ollama/vLLM/llama.cpp) + 强结构化函数调用（Hermes 2 Pro 函数调用 90% 准确 vs 同尺寸通用模型 60-70%）。
> 原先调研的 **OpenHands** 机制（可逆 Condenser / 浏览器工具 / 持续评测 harness / microagents）仍**作为"开源 agent 通用最佳实践"旁证保留**（§3 下半），因为它们与具体哪家无关、对桌宠都有价值。若 "hermes-like" 你另有所指请在 review 纠正。

---

## 0. 三家定位一句话

| Harness | 定位 | 形态 | 与 DeskPet 的关系 |
|---|---|---|---|
| **Claude Code** | "agentic planner" — 显式规划 + 结构化工具 + 可扩展三件套(hooks/subagents/skills) | CLI/IDE/桌面/web | DeskPet 的 codingsys harness 本身就是 Claude Code；DeskPet 是被它开发的**产品** |
| **Codex (CLI)** | "shell-first surgeon" — 极简工具面 + apply_patch 外科手术式编辑 + OS 级沙箱 | 终端优先 | DeskPet 子代理写代码默认用 codex |
| **Hermes Agent** | 开源(MIT) Python agent runtime + 持久记忆 + **自进化技能** + **本地推理** + 强函数调用 | 本地优先 | 第三家锚点；契合桌宠"本地部署"身份 |
| *(OpenHands 旁证)* | 开源 agent 平台 + 可逆 Condenser + 浏览器工具 + 持续评测 | — | 通用机制旁证（非锚点，机制对桌宠有用故保留） |

DeskPet 本体是**单机桌面语音宠物**（Live2D + 语音管线 + 工具 + 记忆 + 技能），不是编码 CLI。
因此对齐必须**选择性吸收 agent-harness 工程能力**，而非把桌宠改造成 docker 编码沙箱。

---

## 1. Claude Code 独门机制

### 1.1 Subagents（隔离子代理）
- 主会话 spawn 的隔离 Claude 实例，**各自独立 context window / 工具权限 / 模型**。
- 丰富 frontmatter：`description, prompt, tools, disallowedTools, model, permissionMode, mcpServers, hooks, maxTurns, skills, initialPrompt, memory, effort, background, isolation, color`。
- **Agent Teams**：专精子代理（code review / test / frontend QA / security）由主 agent 协调，主 agent 管规划+集成。

### 1.2 ★ Dynamic Workflows + Performance Outcomes（2026-06 新增）
- **Dynamic Workflows**：lead agent 单会话内**规划并 fan-out 数十~数百个并行子代理**。
- **Performance Outcomes**：独立 **grader** 给每个子代理结果打分，**不达 rubric 就打回重做**，直到合格。
- → 这是「fan-out + 评分-重做闭环」的工业级形态。

### 1.3 ★ Hooks（确定性生命周期强制）
- 事件驱动脚本，在 tool call 前后 / session start / stop / **subagent completion** 触发。
- **执行确定性代码、不会幻觉**；`exit 2` 客户端硬阻断，**优先于模型意愿**。
- "软指令塑意图，硬机制保边界"。

### 1.4 Skills（渐进式披露 + 可执行）
- `SKILL.md`（frontmatter）+ **支撑脚本 + 可选 sub-tools**，按需加载。
- 关键：skill 不只是 prompt，**能带可执行脚本/子工具**。

### 1.5 Plan Mode
- **只读权限模式**；用 Explore 子代理探查，保持主 context 精简。

### 1.6 工具设计
- 专用工具集：View/LS/Glob、Grep、**Edit/Write/Replace**、Bash、WebFetch、Notebook。
- **粒度化风险分级** + 输入净化（拦 backtick / `$()`）。
- 显式权限 UI + "don't ask again"。

---

## 2. Codex 独门机制

### 2.1 ★ apply_patch（训练过的 diff 编辑）
- **不输出整文件**，生成特定格式的 **unified diff**，CLI 拦截内部处理，**红/绿着色 diff** 供审。
- 用户可 approve / reject / **edit** / approve-all。
- 模型被**专门训练**精通该 diff 格式 → 编辑可靠、token 省、改动可审。

### 2.2 ★ Sandbox × Approval 两层正交
- **Sandbox mode**（技术上能做什么）：`read-only` / `workspace-write`（默认，默认无网络+只写工作区）/ `danger-full-access`。
- **Approval policy**（何时必须问）：`suggest`(默认，每个编辑/命令都问) / `auto-edit`(改文件免问、shell 仍问) / `full-auto`(都免问，受沙箱约束)。
- 两层独立组合 → 自治度与控制力解耦。OS 级沙箱（macOS Seatbelt / Linux Docker+iptables）。

### 2.3 AGENTS.md 指令链每 run 重建
- guidance 来自 prompt 或 AGENTS.md；**每次运行 / 每个 TUI session 开始都重建指令链，无缓存**，永不陈旧。

### 2.4 Server-side 加密 compaction
- 超阈值自动 compaction = **服务端操作**，产出 AES 加密 blob（`POST /v1/responses/compact` → `encrypted_content`），保留模型潜在状态/工具调用恢复数据，非人类可读摘要。**专有不可移植**（仅作概念参考）。

### 2.5 系统提示即 mini-API
- 工具契约 + 安全策略直接编码进 system prompt，教模型显式调用格式。

### 2.6 极简工具面
- 单一主工具 = 通用 shell executor；其余靠 shell。"surgical, focused edits"。

---

## 3. Hermes Agent 独门机制（第三家锚点）+ OpenHands 旁证

### 3.0 Hermes Agent（NousResearch，2026-02，MIT）
- ★ **自主/自进化技能创建**（self-improving）：agent 能把重复流程**自己固化成技能**——比 Claude Skills/DeskPet codify（声明式）更进一步（DeskPet codify 红线是"仅声明不执行"）。
- ★ **本地推理优先**：Ollama / vLLM / llama.cpp 本地跑 LLM → 隐私 / 离线 / 无额度兜底。
- ★ **强结构化函数调用**：`<tools>` XML + `<tool_call>` JSON，json-mode / 结构化抽取，单 assistant turn 内可靠出工具调用（Hermes 2 Pro 90% 准确 vs 通用 60-70%）。
- 持久记忆 + 本地推理 runtime。
- → 对 DeskPet：**自进化技能**（强化 GAP-5）+ **本地推理**（GAP-12 新增）是 Hermes 带来的两条新对齐线。

### 3.1～3.8 OpenHands 旁证机制（通用开源-agent 最佳实践，与锚点无关，对桌宠有价值故保留）

### 3.1 ★ Runtime = Docker 沙箱（bash + Jupyter + 浏览器）
- 每个 runtime 含 bash shell + Jupyter IPython server + **Playwright 控制的 Chromium**。
- `SANDBOX_KVM_ENABLED` 可跑 KVM 加速 VM。

### 3.2 ★ BrowserToolSet（agent 驱动的真浏览器）
- 基于 browser-use：**自然语言导航网页 / 点击 / 填表 / 抽取内容**。

### 3.3 ★ Condenser（可逆压缩）
- 压缩只在视图层插 `CondensationAction` marker（记 start/end/summary），**原始 event 永不删** → 可 replay / 审计。

### 3.4 Microagents（关键词触发知识注入）
- 关键词命中 → **确定性注入**知识片段（非向量概率召回）。

### 3.5 StuckDetector（5 种循环模式）

### 3.6 ★ Agent SDK（可组合 + 分布式 + 远程 runtime）
- agent 逻辑跑本地（低延迟/私密），工具执行跑远程沙箱（隔离/可扩展）。
- Sub-Agent Delegation via `TaskToolSet`。

### 3.7 ★ 持续评测流水线（多模型回归）
- **每个 PR + 每天**自动跑：programmatic + LLM-based 测试，**跨多个模型**验证一致性，catch reasoning/tool-use/state 回归。

### 3.8 Agent Canvas
- 浏览器 UI + 后端 server，取代 legacy CLI 成为默认开发面。

---

## 4. 提炼：值得 DeskPet 借鉴的「跨家共识机制」

1. **Diff-first 编辑**（Codex apply_patch / Claude Edit/Replace）——最小可审改动，绕开整文件覆盖截断卡死。
2. **分层审批 / 权限模式正交**（Codex sandbox×approval / Claude permissionMode）。
3. **fan-out + grader 重做闭环**（Claude Performance Outcomes / OpenHands TaskToolSet）。
4. **确定性 hook / exit-2 硬强制**（Claude hooks）——把"靠文字 nudge"升级为"系统级阻断"。
5. **可逆/可审计压缩**（OpenHands Condenser）——marker 不删 event。
6. **agent 驱动的真浏览器**（OpenHands BrowserToolSet）——从"只读抓取"到"交互式办事"。
7. **触发式知识注入**（OpenHands microagents / Cline rules）。
8. **skill 带可执行脚本/子工具**（Claude Skills）。
9. **跨模型自动回归评测**（OpenHands CI）——把昂贵脆弱的手工 E2E 部分自动化。
10. **每 run 重建、永不陈旧的指令链**（Codex AGENTS.md）。

---

## 5. 信源

- Claude Code — [Subagents 文档](https://code.claude.com/docs/en/sub-agents) · [Steering Claude Code: skills/hooks/subagents](https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more) · [Hooks/Subagents/Skills 2026 指南](https://ofox.ai/blog/claude-code-hooks-subagents-skills-complete-guide-2026/) · [Subagents 2026 playbook](https://www.developersdigest.tech/blog/claude-code-agent-teams-subagents-2026)
- Codex — [Sandboxing](https://developers.openai.com/codex/concepts/sandboxing) · [Approvals & security](https://developers.openai.com/codex/agent-approvals-security) · [AGENTS.md](https://developers.openai.com/codex/guides/agents-md) · [Context Compaction 深潜](https://codex.danielvaughan.com/2026/04/14/context-compaction-deep-dive-codex-cli-claude-code-opencode/) · [Codex vs Claude Code(PromptLayer)](https://blog.promptlayer.com/how-openai-codex-works-behind-the-scenes-and-how-it-compares-to-claude-code/)
- **Hermes Agent**（第三家锚点）— [Hermes Agent 综述(CrabTalk)](https://crabtalk.ai/blog/hermes-agent-survey) · [自进化指南(Tosea)](https://tosea.ai/blog/hermes-agent-self-improving-ai-guide) · [Hermes-Function-Calling(GitHub)](https://github.com/NousResearch/Hermes-Function-Calling) · [hermes-function-calling-v1 数据集(HF)](https://huggingface.co/datasets/NousResearch/hermes-function-calling-v1) · [AI Providers(本地推理)](https://hermes-agent.nousresearch.com/docs/integrations/providers)
- OpenHands（旁证）— [Releases](https://github.com/OpenHands/OpenHands/releases) · [Agent SDK 论文](https://arxiv.org/html/2511.03690v1) · [Browser Use](https://docs.openhands.dev/sdk/guides/agent-browser-use) · [Condenser](https://docs.openhands.dev/sdk/guides/context-condenser) · [microagents](https://docs.openhands.dev/openhands/usage/microagents/microagents-overview)
