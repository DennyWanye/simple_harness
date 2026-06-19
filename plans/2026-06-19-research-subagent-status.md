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

1. **main.py 零引用** —— `grep subagent|register_builtin|SubagentRuntime|run_subagent backend/main.py` → **No matches**。
2. **只有自己引用自己** —— 全 repo grep `subagents|research_agent|run_subagent|synthesize_report|SubagentRuntime|register_builtin` 命中的文件**全部在 `backend/deskpet/agent/subagents/` 目录内部**，没有 tools/、没有 assembler/、没有 main.py。
3. **没有测试** —— `backend/tests/` 下无任何文件引用 subagents / research_agent / run_subagent / synthesize_report。
4. **没有工具 schema 暴露给 LLM** —— presets.py 注释说 `register_builtin_subagents` 应「called from main.py during tool registration」，但 main.py 根本没调；也没有把 research 子代理包成一个 tool 注册进 ToolRegistry / tool_selector 的 web category。

**即：桌宠主 agent 在对话里根本调不到这个 research 子代理。它是写好但未通电的模块。**

---

## ⏳ 最后两个待确认（上一个工具调用被上下文截断，未拿到结果）

接手人请先跑这两条，补全判断：

1. **旧 research_run 是否还活着**（决定「现在桌宠实际用哪套 / 还能不能 research」）：
   - `grep -n "_register_research_tool" backend/deskpet/tools/research_tools.py`（模块级是否仍自注册）
   - `grep -rn research_run backend/main.py`
2. **入口 SKILL.md 是否为空**（之前 Read `backend/deskpet/skills/builtin/deep-research/SKILL.md` 系统提示「this file exists but is empty」，强烈怀疑已被清空）：
   - `wc -c backend/deskpet/skills/builtin/deep-research/SKILL.md`

**推测的当前真实状态（待上面验证）：**
- 旧 research_run 工具可能还注册着（research_tools.py 模块底部 `_register_research_tool()` 模块级执行），但其入口 SKILL.md 疑似被清空 → 旧路径「半残」。
- 新 research 子代理 = 完整代码 + 完整架构，但**完全未接线**（无 main.py wiring / 无 tool 暴露 / 无测试）。
- 结论倾向：**这是一次「架构已重写、接线未完成」的半成品迁移**。用户记忆中「已用子代理实现」对应的是**代码实现完成**，但**尚未真正接通到桌宠可用**。

---

## 完整度评分（初判，待②补全）

| 层面 | 状态 |
|---|---|
| 设计/架构 | ✅ 完整且清晰（ReAct + 受限工具 + 三重预算 + 分离 synthesis + cite-check）|
| 代码实现 | ✅ 基本完整（10 文件，核心路径都在）|
| 接线到运行时 | ❌ 未做（main.py 零引用）|
| 工具暴露给 LLM | ❌ 未做 |
| 测试 | ❌ 无 |
| 真机可用 | ❌ 当前对话调不到 |

**一句话**：架构和代码是成品，接线和验证是 0。属于「写完了、没插电」。

---

## 关键文件绝对路径
- `G:\projects\deskpet\backend\deskpet\agent\subagents\`（全套）
- 旧实现对照：`G:\projects\deskpet\backend\deskpet\tools\research_tools.py`（research_run）
- 入口 skill（疑空）：`G:\projects\deskpet\backend\deskpet\skills\builtin\deep-research\SKILL.md`

## 沟通纪律（用户明确要求）
- **默认中文回复**。
- 动手分析前**先和用户商量**，不要擅自吐长文 / 写大文档。
- 不要再参考 v8 plan / ROADMAP（已过时）。
