# 20 — DeskPet vs Claude Code / Codex / Hermes：缺口分析（代码核实版）

> 把 [10-竞品调研](./10-competitor-research.md) 的「跨家共识机制」逐条对照 DeskPet **实际代码**，
> 判定哪些是真缺口、哪些其实已实现（别重做）。每条带 file:line 证据。
> 核实方式：2 个子代理（架构测绘 + STATUS 对抗审计）+ Lead 亲读关键文件。调研日期 2026-06-26。
>
> ⚠️ **重要前提**：DeskPet ≠ 编码 CLI，而是**单机桌面语音宠物**（Live2D + 语音 + 工具 + 记忆 + 技能）。
> 对齐目标是**选择性吸收 agent-harness 工程能力以提升"桌宠为用户办事"的可靠性与能力面**，
> 不是把桌宠改造成 docker 编码沙箱。沙箱类机制（OS 沙箱/远程 runtime/KVM）**明确不采纳**
> （对齐 [[feedback_no_sandbox_constraints]]：桌宠只防手滑级破坏）。

---

## 0. 先说"别重做"——DeskPet 已吸收的最佳实践

`STATUS/AgentImprovements.md`（2026-06-21）当时列的 7 个优化点，**现已全部落地**（见 STATUS §3 "Agent Loop 优化 7 WI"），代码核实如下：

| 竞品机制 | DeskPet 现状 = 已实现 | 证据 file:line |
|---|---|---|
| tool_choice 协议级硬约束 | ✅ tier3 自检 + force_finish 传 `tool_choice="none"` | `backend/agent/agent_loop.py:1167/1234/1300/1376` |
| 每轮结构化 trace | ✅ `IterationTracer.record()→jsonl` | `backend/agent/trace.py:15-49` |
| Focus Chain todo 回灌 | ✅ 每 8 迭代注入 todo 进度 | `agent_loop.py:_TODO_SYNC_EVERY=8` (137) |
| 触发式知识注入 | ✅ skills `knowledge_enabled` + auto_disclosure cos-sim | `config.py:378-415`（flag 默认 off） |
| SEARCH/REPLACE 降级编辑 | ✅ exact→whitespace→anchor→did_you_mean | `deskpet/tools/os_tools/edit_file.py:46-120` |
| ask_clarification 澄清工具 | ✅ 阻塞式 control WS 提问 120s | `deskpet/tools/code_tools/clarify_tool.py` |
| in-loop 自检三级 | ✅ tier1@10/tier2@20/tier3@30 | `agent_loop.py:134-169` |
| **MCP 客户端** | ✅ 完整（stdio/SSE + 重连退避 + `mcp_{server}_{tool}` 命名空间 + 故障隔离） | `deskpet/mcp/manager.py` |
| ref-store 截断恢复 | ✅ `fetch_tool_result` + 磁盘 spill | `deskpet/tools/code_tools/fetch_tool_result_tool.py` |
| StuckDetector 循环检测 | ✅ 部分（args-aware 幻觉 + signature 重复 + difflib stagnation） | `agent/termination.py` |
| 子代理 fan-out + 背压调度 | ✅ scheduler（semaphore lane cap + peak/queued/rejected 指标） | `deskpet/agent/subagent_scheduler.py:28+` |

→ **结论：DeskPet 的 agent 工程底子非常扎实**，竞品的"控制层硬度 / trace / 截断 / 熔断 / MCP / fan-out"等大多已有。
真正的缺口更上层、更精细，集中在 11 条（下文）。

---

## 1. 缺口总表（11 条，按 Phase 排序）

| ID | 竞品优势 | DeskPet 现状（代码核实） | 缺口性质 | 来源 |
|---|---|---|---|---|
| **GAP-1** | 多-hunk 原子 diff 编辑（Codex `apply_patch`：一次补丁含多处改/新建/删，训练过的 diff 格式，红绿审阅） | `edit_file` **单 hunk**：一次只能一个 old_string→new_string；多处改要多次调用；无"新建+改+删"原子补丁；无 unified-diff | 真·中 | Codex |
| **GAP-2** | fan-out + grader 重做闭环（Claude Performance Outcomes：独立 grader 给每个子代理打分，不达 rubric 打回重做） | `external_evaluator` 只评**主 agent 最终目标**、仅高后果触发、`flag off`、**无 revise 循环**（返 revise 后靠 nudge，无 per-subagent 评分/重做） | 真·高 | Claude/OpenHands |
| **GAP-3** | 可逆/可审计压缩（OpenHands Condenser：插 marker 不删 event，可 replay） | `history_compactor` **有损**：中段→单条摘要，**原文丢弃**（`inject_summary` 替换）；trace.jsonl 仅审计不可回灌 | 真·中 | OpenHands |
| **GAP-4** | 用户可扩展确定性 hooks（Claude：pre/post-tool/stop/subagent 完成，`exit 2` 硬阻断，优先于模型意愿） | 仅**硬编码** in-loop gates + dev-harness codingsys hooks；产品 agent **无**用户可配运行时 hook 机制 | 真·中 | Claude |
| **GAP-5** | skill 带可执行脚本/子工具（Claude Skills：SKILL.md + 脚本 + sub-tools） | `skill_invoke` 只注入 Markdown body（prompt 注入）；`requires_script` **恒 false**，不产 function call（STATUS §3 明列此缺口） | 真·中 | Claude |
| **GAP-6** | 自治档/效率（Codex：approval 轴 suggest/auto-edit/full-auto） | 逐工具权限弹窗、**无自治档**，桌宠每写文件/跑命令都打断。**v1.1 反转为"高效自治默认"**（用户要少审批、高效办事） | 真·高（效率） | Codex |
| **GAP-7** | 每-run 重建、永不陈旧的规则文件（Codex AGENTS.md / OpenHands microagents 关键词触发） | 注入 persona/memory/skill；后端 grep **无** AGENTS.md/规则文件加载机制；触发知识注入 flag off | 真·中低 | Codex/OpenHands |
| **GAP-8** | agent 驱动的通用浏览器办事（OpenHands BrowserToolSet：导航/点击/填表/抽取） | `browser_use_tool` 已存在但 **`toolset=e2e` / `[code_e2e].browser_use_enabled` 默认 false / 仅 code-mode / 定位"E2E 测试"** | 半·高价值（基建在，定位错） | OpenHands |
| ~~**GAP-9**~~ | ~~代码语义检索 / RepoMap / AST（Aider/Claude）~~ | **移出本轮**（用户：code 入口隐藏不处理，先做强主线程）→ 00-PLAN 附录 C | 移出 | Aider |
| **GAP-10** | 跨模型自动 agent 行为回归评测（开源 agent：每 PR + 每日，programmatic + LLM-based，多模型） | 重度依赖**昂贵脆弱的手工 windows-mcp E2E** + pytest；无场景回放/轨迹断言/多模型 matrix | 真·高（直击项目 #1 痛点） | OpenHands/Hermes |
| **GAP-11** | （DeskPet 自身暗能力激活） | `memory.v2`/`[features]`/`[skills]` 大批已实现能力原 flag 默认 off → **2026-06-27 测试阶段已全量点亮（config.py working tree，见 00-PLAN WI-0.0）**；剩真测核对 | 激活类·高 ROI（基本已落地） | — |
| **GAP-12** | 本地推理选项（**Hermes**：Ollama/vLLM/llama.cpp 本地 LLM；隐私/离线/兜底） | LLM 全走中转站 relay 云端；无本地 provider；断网/超额即瘫 | 真·中（兜底+隐私，契合"本地部署"身份） | Hermes |

---

## 2. 明确不采纳（避免误对齐）

| 竞品机制 | 不采纳理由 |
|---|---|
| OS 级沙箱（Seatbelt / Docker+iptables / KVM） | 桌宠是单机产品，用户自己的机器；[[feedback_no_sandbox_constraints]] 明确只防手滑级破坏，不加 Claude-Code 风格沙箱护栏 |
| 远程 runtime / 分布式 agent SDK | 单机部署，无远程执行需求 |
| Codex server-side 加密 compaction | 专有不可移植；GAP-3 用 OpenHands 可逆 Condenser 思路替代 |
| Agent Canvas（浏览器 IDE 化主界面） | 桌宠形态是 Live2D 悬浮宠物，非 IDE |
| "一次一工具串行"（Cline） | DeskPet 并发分发是**特性**（`asyncio.gather` + partition_dispatch），别退化 |

---

## 3. 优先级排序逻辑

排序 = 价值（对"桌宠主线程为用户办事"的提升）× 可行性（基建复用度）× 风险。**v1.1（用户反馈）**：第三家锚 **Hermes Agent**（非 OpenHands，原 OpenHands 机制保留为旁证）；**GAP-9（code）移出本轮**；**GAP-6 反转为效率优先**；**新增 GAP-12 本地推理**。全部聚焦 **Companion 主线程"最强能力 + 最高效率"**。

- **批 0（天级，先做）**：**GAP-6 高效自治默认**（效率立竿见影，用户第一诉求）+ GAP-11 激活暗能力 + GAP-7 规则文件/知识注入。
- **批 1（周级）**：GAP-2 子代理 grader 重做闭环 + GAP-1 apply_patch。
- **批 2（周+，最强主线程）**：GAP-8 浏览器办事（去 e2e-only）+ GAP-5（+Hermes 自进化）可执行/自创技能 + GAP-3 可逆 Condenser。
- **批 3（周+）**：GAP-4 用户 hooks + GAP-10 自动评测 harness（尽早并行，省后续真测成本）+ GAP-12 本地推理选项（兜底，可放最后）。
- **移出本轮**：GAP-9 RepoMap/AST（code 入口隐藏，待重开再做）。

代码级实现见 [00-PLAN.md](./00-PLAN.md)（WI 章节 ID 沿用原编号，批次见 00-PLAN §0）。
