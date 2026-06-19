# Deep Research 现状 vs Plan 评估（2026-06-19）

> 用户问题：想"去掉旧 research、换成新 deepresearch"，先确认新 deep research 是不是按 plan 做的、完成度多少、有没有未完成。
> 本文是**读码核实**后的评估，作为可靠交接基线。结论：**"旧 vs 新"是伪命题**——见下。

---

## 0. 取证前提：两份"前序文档"的真实状态（重要）

| 文档 | 状态 |
|---|---|
| `research/DEEPRESEARCH-HANDOFF.md`（378 行，声称已提交 commit `c4f7e21`）| ❌ **不存在**。工作树/所有分支/3 worktree/stash/20+ dangling commit/reflog 全扫无；`c4f7e21` 是无效对象名。随沙箱回滚丢失。|
| `research/PENDING-blind-spots-section.md` | ❌ 同上，不存在。|
| `plans/2026-06-19-research-subagent-status.md`（状态笔记）| ⚠️ **存在但不可信**。它描述的 `backend/deskpet/agent/subagents/`（10 文件 1100 行 ReAct 子代理）经核实**在仓库不存在**（从未被 git 跟踪、`git log --all` 无历史、`__pycache__` 无 .pyc）。该笔记还把 `agent/team/`（实际 1366 行真实代码）误判为"只有空 __init__"。已于 commit `21108d4` 订正其 3 处错判（SKILL.md 非空、旧 research_run 活着且接线、main.py 非零引用 subagent）。|

**净结论**：所谓"新 research 子代理"**当前不在仓库**——要么从未提交（untracked 被回滚），要么状态笔记读了幻影路径。

---

## 1. 当前真实实现 = `research_run`（旧的，但其实是现代实现）

文件：`backend/deskpet/tools/research_tools.py`（1736 行）+ `research_scoring.py`（306 行）。
入口：`skills/builtin/deep-research/SKILL.md`（6649 字节，完整，明确路由到 `research_run`）。
接线：模块级 `_register_research_tool()`（:1716/:1736）自注册；main.py 注入 3 钩子（主 LLM / 廉价精排模型 / BGE-M3 scorer）。**桌宠现在真能调到它。**

### 管线（固定 7 段 + 子阶段）
plan(LLM拆题) → query扩展(multi-query+HyDE) → search(DDG, site:定向) → fetch+extract(trafilatura→JS渲染→Jina) → score+filter(authority×recency×relevance×depth) → [direct sources 巨潮/国标] → [reflection 补搜, deep档2轮] → [BGE-M3 语义精化] → [LLM 精排] → synthesize → cite_check → 落盘+artifact卡片

### 已实现的高级能力（带状态）
| 能力 | 状态 |
|---|---|
| query 扩展（multi-query+HyDE）| ✅ 默认开 |
| site: 定向官方域（gov/cninfo/arxiv）| ✅ 默认开 |
| 中文一手源直连（巨潮/国标 + EDGAR 兜底）| ✅ 默认开 |
| trafilatura 抽取 + DDG SERP+正则兜底 | ✅ |
| JS 渲染抓取兜底 | 🟡 cdp-edge(Windows)落地；crawl4ai=dev档；webview=未实现 |
| Jina Reader 二级抓取 | 🟡 默认关，国内需代理 |
| BGE-M3 语义相关性 | 🟡 embedder 在场才生效，否则降级关键词 |
| LLM 精排 reranker（gpt-4.1-mini）| ✅ 默认开；超时/低覆盖 no-op |
| 本地 bge-reranker | ❌ 未实现（config=local 退化为 llm）|
| 分层权威打分 + recency + 多样性 | ✅（多样性≥5域、单域≤25%）|
| AI 生成内容/乱码/低质量站过滤 | ✅ |
| reflection 迭代补搜（deep 2 轮）| ✅ |
| cite_check 引用自检 | ✅ |
| 失败 best-effort 降级（partial coverage）| ✅ |
| 报告落盘 + artifact 卡片 | ✅ |

### 档位
| 档 | 子问题 | 每问URL | 段落上限 | 反思 |
|---|---|---|---|---|
| light | 3 | 2 | 8 | 1 |
| standard | 5 | 4 | 12 | 1 |
| deep | 6 | 5 | 16 | 2 |

---

## 2. Plan 目标 vs 现状：plan 想要的，绝大多数已做完

时间线：v8-plan(06-13) → best-practices ROADMAP(06-14, 自述 v8 那批"已做") → reranker plan(06-14, 已完成) → crawl4ai-fetch-tier(06-16, 规划中) → subagent-status(06-19, 转向子代理)。

| plan 目标特性 | 现状 |
|---|---|
| 分层权威打分(含中文源)+多样性 | ✅ 已做 |
| BGE-M3 语义相关性 | ✅ 已做 |
| LLM 精排 reranker | ✅ 已做（reranker plan 完成）|
| query 扩展 multi-query/HyDE/site: | ✅ 已做 |
| 中文一手源直连 | ✅ 已做 |
| reflection gap-driven 迭代 | ✅ 已做 |
| cite-check / 质量门 | ✅ 已做 |
| 多引擎搜索降级 | ✅ 已做 |
| 报告落盘 + artifact | ✅ 已做 |
| crawl4ai/JS 渲染 fetch tier | 🟡 部分（webview 反向链路 GATE 未做）|
| 本地 bge-reranker（可选档）| ❌ 未做（Phase-future）|
| **ReAct 自主 agent 架构** | ❌ **不存在**（代码已丢）|
| **受限只读工具子集** | ❌ 不存在 |
| **三重预算闸(轮数+时长+token)** | ❌ 部分（只有 URL/轮数上限）|
| **分离 synthesis(更强模型)** | ❌ 不存在（同函数内 synth）|

---

## 3. 关键洞察：「新 vs 旧」唯一实质区别 = **架构**，不是能力

把"新子代理"和"旧 research_run"区分开的**唯一**东西是**控制架构**：
- 旧 = **固定流水线**（步骤写死，条件分支仅由 depth 档/best-effort skip 决定）。
- 新 = **ReAct 自主 agent**（LLM 自己 reason→act→observe 决定下一步）+ 受限只读工具子集 + 三重预算闸 + 分离 synthesis + EvidenceLog。

**能力上新子代理一个都没多**（打分/精排/抓取层这些都在旧实现里）。所以"换成新子代理"≠"获得更多能力"，而是"换一种控制架构"。

### ⚠️ 仓库内部架构方向冲突（需用户裁决）
- **v8-plan（06-13）明确写「单 agent，桌宠单机，不 fan-out subagent」**——白纸黑字拒绝子代理化。
- **subagent-status（06-19）转向「独立 ReAct 子代理」**，并宣布 v8/ROADMAP「已过时」。
两者方向对立。这不是平滑迁移，是一次架构掉头。

---

## 4. 现状里挖到的真 bug

**recency 维度事实失效**：`score_recency` 依赖 `payload.date`，但 `default_extract` 返回字典**无 `date` 键**（research_tools.py:826-831）→ recency 永远取默认 3.0（除非 direct_sources 路径）。确定可修。

其它弱点：
- 可观测性不足（异常吞进 errors 列表 + log.debug，无结构化事件/阶段耗时指标）。
- loopback/ollama 用户（本地模型）不触发 LLM 精排（main.py 仅非 loopback 才注入），质量无声下降。
- JS 渲染仅 Windows/cdp-edge 真落地；非 Windows 等同未实现。
- 工具注册 handler 层（`_handle_research_run`）无测试覆盖。

---

## 5. 成本对比 + 建议

| 方案 | 工作量 | 风险 |
|---|---|---|
| **A. 原地升级 research_run** | 小、增量（修 recency bug / crawl4ai webview / 本地 bge / 可观测性 / loopback 精排）| 低，不动可用功能 |
| **B. 重建 ReAct 子代理替换** | 大（~1100 行重写 + 接线 + tool 暴露 + 测试 + 真机 E2E + 把已调好的打分/一手源/抓取层重新嫁接进子代理工具）| 高，大概率回退一个已调好的可用管线；架构方向仓库内有争议 |

**建议**：不删 research_run。优先在它上面补缺口（先修 recency bug 等确定项）。"换 ReAct 子代理"只在明确认为自主控制流值得、且愿担重写+回归风险时才做。

---

## 关键文件
- 现实现（在岗）：`backend/deskpet/tools/research_tools.py`（research_run @:967，自注册 @:1716/:1736）+ `research_scoring.py`
- 入口 skill（完整）：`backend/deskpet/skills/builtin/deep-research/SKILL.md`
- main.py 钩子注入：`backend/main.py` :638-667 / :1067-1087
- 目标架构 plan：`plans/2026-06-13-deep-research-v8/00-PLAN.md`、`plans/2026-06-14-deep-search-best-practices/00-ROADMAP.md`、`plans/2026-06-14-research-reranker/00-PLAN.md`、`plans/2026-06-16-crawl4ai-fetch-tier/`
- 不可信状态笔记（已订正）：`plans/2026-06-19-research-subagent-status.md`
- 已丢失（不可恢复）：`research/DEEPRESEARCH-HANDOFF.md`、`research/PENDING-blind-spots-section.md`
