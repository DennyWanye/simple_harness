# research 子代理 完整度分析 — 状态笔记（2026-06-19）

> 用户问题：现在的 deep research 用「research 子代理」实现了，完整度怎么样？
> 本笔记记录**已读代码核实**的发现。旧的 v8 plan / best-practices ROADMAP 用户明确说**已过时，不要再参考**。

---

## 真身位置

**`backend/deskpet/agent/subagents/`**（10 个文件，约 1100 行）。注意不是 `subagents`（用户口述路径）也不是 `team/agents/`（那只有空 __init__）。

| 文件 | 行数 | 作用 |
|---|---|---|
| `research_agent.py` | 140 | **research 子代理定义**：系统提示 `_RESEARCH_SYSTEM`、`ResearchInput`、`build_research_spec`、`register`、`run_research` |
| `research_synthesis.py` | 104 | 证据→带引用报告，含 cite-check |
| `runtime.py` | 187 | `SubagentRuntime.run(spec)`：跑 AgentLoop + 三重预算闸 + 调 synthesis |
| `base.py` | 120 | 纯数据类型：`SubagentSpec` / `EvidenceLog` / `EvidenceItem` |
| `tools.py` | 170 | `build_subset_registry`：只读工具子集 + web_fetch 结果自动进 EvidenceLog |
| `presets.py` | 168 | `register_builtin_subagents(registry)` — main.py 本应在 boot 调用 |
| `registry.py` | 78 | SubagentRegistry |
| `loop.py` | 202 | （未细读）|
| `__init__.py` | 34 | 导出 |

---

## 架构（设计层面，已核实）— 与旧 research_run 的根本区别

research_agent.py 顶部注释自述：「与旧的 `research_run`（一个巨型函数把 plan→search→fetch→score→synth 全写死）不同，本子代理是一个**真正的 agent**」。

| 维度 | 旧 research_run（v8） | 新 research 子代理 |
|---|---|---|
| 控制流 | 固定 7 阶段流水线 | **自主 ReAct**：LLM 自己 reason→act→observe |
| 工具 | 内部写死 | 受限只读子集 `web_search`/`web_fetch`/`memory_search`（allowlist 在 tools.py `_READONLY_ALLOW`，还含 read_file/glob/grep）|
| 预算 | URL/轮数上限 | **三重闸**：max_iterations(spec) + wall_clock(默认180s) + tokens(默认120k)，见 runtime.py:33-34, 75-94 |
| 证据 | passages | `EvidenceLog`，web_fetch 自动累积（tools.py:68 execute_tool 钩子）+ URL 去重（base.py:40）|
| 成稿 | 同函数内 synth | **分离** synthesis（research_synthesis.py，更强模型一次成稿）|
| 自检 | cite_check | 有：synthesis 里 `[^n]` 必须映射 evidence index，缺失则报告追加 ⚠️（research_synthesis.py:72-90）|
| 配置 | 函数参数 | 声明式 `SubagentSpec`（base.py:56）|

档位预算（research_agent.py:72-76）：light=(6轮,4源) / standard=(12轮,8源) / deep=(20轮,14源)。

输出：`{ok, report_md, citations, evidence, coverage, errors}`（runtime.py:116）。

---

## ⚠️ 完整度结论：代码写完了，但是「孤岛」——没接进运行时

**已核实的硬证据：**

1. **main.py 未接 research 子代理运行时** —— `register_builtin_subagents` / `SubagentRuntime` / `run_research` 这三个**具体符号**在 main.py 仍**零引用**。⚠️ 订正（2026-06-19 复核）：原稿写的「`grep subagent ...` → No matches」**不准确**——main.py :891/:899 有 `_ephemeral_subagent` 接线（另一套临时子代理机制，**非** `agent/subagents/` 这套 research 子代理）。所以"未通电"成立的是**这套 research 子代理**，不是"main.py 完全没有任何 subagent"。
2. **只有自己引用自己** —— 全 repo grep `subagents|research_agent|run_subagent|synthesize_report|SubagentRuntime|register_builtin` 命中的文件**全部在 `backend/deskpet/agent/subagents/` 目录内部**，没有 tools/、没有 assembler/、没有 main.py。
3. **没有测试** —— `backend/tests/` 下无任何文件引用 subagents / research_agent / run_subagent / synthesize_report。
4. **没有工具 schema 暴露给 LLM** —— presets.py 注释说 `register_builtin_subagents` 应「called from main.py during tool registration」，但 main.py 根本没调；也没有把 research 子代理包成一个 tool 注册进 ToolRegistry / tool_selector 的 web category。

**即：桌宠主 agent 在对话里根本调不到这个 research 子代理。它是写好但未通电的模块。**

---

## ✅ 两个待确认项 — 已验证（2026-06-19 复核，推翻原稿两处猜测）

原稿因上下文截断留了两个"疑似"判断，本次已用读码核实，**两个猜测都被推翻**：

1. **旧 research_run —— 活着，且已接线**（不是"半残"）：
   - `research_tools.py:1716 _register_research_tool()` → `:1736` 模块级直接执行 → **自注册仍生效**。
   - `research_run` 定义在 `:967`；main.py **确实引用**（`:639` 让其 plan/synthesize/reflection 走聊天 agent 同一 live、`:1069` 把 embedder 结果 blend 进 relevance）。
   - 即：**旧 research_run 是当前桌宠实际可用的 research 路径**。
2. **入口 SKILL.md —— 非空，内容完整**（原稿"疑似被清空"❌ 推翻）：
   - `wc -c` = **6649 字节**。完整 frontmatter（name=deep-research / version 0.2.0 / triggers / task_types）+ 全套提示词。
   - 之前 Read 看到的「this file exists but is empty」是**误读/旧快照**，非真实状态。

**订正后的当前真实状态：**
- 旧 research_run：**活着 + 自注册 + main.py 接线 + SKILL.md 完整** = 桌宠现在能用的 research 路径（**不是半残**）。
- 新 research 子代理（`agent/subagents/`）：完整代码 + 完整架构，但**这套 runtime 未接线**（main.py 无 `register_builtin_subagents`/`SubagentRuntime`/`run_research` 引用、无 tool 暴露、无测试）。
- 结论：这是一次「**新架构已写完但未通电，旧实现仍在岗**」的并存状态。用户记忆中「已用子代理实现」= **新子代理代码完成**，但桌宠**实际跑的仍是旧 research_run**。

---

## 完整度评分（初判，待②补全）

| 层面 | 状态 |
|---|---|
| 设计/架构 | ✅ 完整且清晰（ReAct + 受限工具 + 三重预算 + 分离 synthesis + cite-check）|
| 代码实现 | ✅ 基本完整（10 文件，核心路径都在）|
| 接线到运行时 | ❌ 未做（main.py 零引用）|
| 工具暴露给 LLM | ❌ 未做 |
| 测试 | ❌ 无 |
| 真机可用（新子代理） | ❌ 当前对话调不到 |
| 真机可用（旧 research_run） | ✅ 活着 + 接线 + SKILL.md 完整，桌宠现在用的是这套 |

**一句话**：**新** research 子代理「写完了、没插电」（架构/代码成品，接线/测试为 0）；但**旧 research_run 仍在岗可用**，所以桌宠 research 功能并没断——只是没切到新架构。

---

## 关键文件绝对路径
- `G:\projects\deskpet\backend\deskpet\agent\subagents\`（全套）
- 旧实现对照：`G:\projects\deskpet\backend\deskpet\tools\research_tools.py`（research_run）
- 入口 skill（疑空）：`G:\projects\deskpet\backend\deskpet\skills\builtin\deep-research\SKILL.md`

## 沟通纪律（用户明确要求）
- **默认中文回复**。
- 动手分析前**先和用户商量**，不要擅自吐长文 / 写大文档。
- 不要再参考 v8 plan / ROADMAP（已过时）。
