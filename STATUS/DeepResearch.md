# DeepResearch 模块专项状态

> **最后更新**: 2026-06-20
> **用途**: deep research（深度调研）模块的现状、能力清单、已知问题、调查盲区、演进建议。
> 全局项目状态见 [`status.md`](./status.md)；本文件是 deep research 这一模块的深入档。
> **诚实声明**：本档结论基于**静态读码核实**，除特别标注外**未经运行时实测**（见 §6 盲区）。

---

## 1. 一句话结论

**"旧 research vs 新 deepresearch" 是伪命题。** 桌宠当前唯一真实存在、接线可用的 deep research
是 `research_run`（`backend/deskpet/tools/research_tools.py`，1736 行），它**已吸收各 plan 的成果**
（分层打分 / BGE-M3 / LLM 精排 / query 扩展 / 中文一手源 / reflection / cite-check / 报告落盘）。
传说中的"新 ReAct 子代理"**代码不在仓库**（从未被 git 跟踪、无历史、无 .pyc），唯一区别只是**控制架构**
（自主 ReAct vs 固定流水线），**能力上一项都没多**。

---

## 2. 现状实现：`research_run` 管线 + 能力

**入口**：`skills/builtin/deep-research/SKILL.md`（v0.2.0，完整 6649 字节，路由到 `research_run`）。
**接线**：模块级 `_register_research_tool()`（:1716/:1736）自注册；`main.py`（:638-667 / :1067-1087）
注入 3 钩子（主 LLM / 廉价精排模型 gpt-4.1-mini / BGE-M3 scorer）。**桌宠现在真能调到它。**

| 阶段 / 能力 | 状态 | 证据 |
|---|---|---|
| ① Plan 拆题（3-6 子问题，失败降级用原题） | ✅ | research_tools.py:998-1008 |
| ② Query 扩展（multi-query + HyDE） | ✅ 默认开 | :1010-1015 |
| ③ Search（DDG + site: 定向官方域，区域感知） | ✅（**仅 DDG**，不接付费） | :1017-1057 |
| ④ Fetch（trafilatura → JS渲染 → Jina 三级） | 🟡 JS渲染仅 cdp-edge/Windows 落地 | default_extract:751-831 |
| ⑤ 过滤（字典站/AI生成/乱码/长度门） | ✅ | _passage_from:1088-1103 |
| ④.4 中文一手源直连（巨潮/国标 + EDGAR 兜底） | ✅ | :1133-1175 |
| ④.5 Reflection 反思补搜（仅 deep 档 2 轮） | ✅ 但固定 2 轮无收敛 | :1177-1209 |
| ④.6 BGE-M3 语义精化（取 max，只抬不埋） | 🟡 embedder 在场才生效 | :1211-1237 |
| ④.7 LLM 精排 reranker（候选池≤24，失败如实标） | ✅ 但仅云端 relay 注入 | :1241-1258 |
| ⑤ Synthesize（含口径提示，失败降级段落罗列） | ✅ 同函数内成文 | :1282-1296 |
| ⑥ Cite-check（脚注号对齐，缺失⚠️/未用裁掉） | ✅ 不校验 claim-evidence 对齐 | :1298-1318 |
| ⑦ 落盘 + artifact 卡片 | ✅ `OutPut/Research/` | :1609-1627 |

**打分子系统**（`research_scoring.py`，纯函数）：分层权威分（4 档 + 中文源 + 自媒体降到 2.0）`:203-218`；
新鲜度分 `:228-246`（⚠️见 §4 真 bug）；合成分 `:249-256`；多样性 `:259-285`（≥5 域、单域≤25%）；
主题速度启发式 `:288-297`。

**档位**：light(3问/2URL/8段/1轮) · standard(5/4/12/1) · deep(6/5/16/2)。

---

## 3. Plan 目标 vs 现状

plan 想要的能力**绝大多数已做完**（分层打分/BGE-M3/精排/query扩展/一手源/reflection/cite-check/落盘/多引擎降级）。
**未做的只有**：本地 bge-reranker（可选档，Phase-future）、crawl4ai webview 反向链路（GATE 未做）、
以及**ReAct 子代理架构整套**（受限只读工具子集 / 三重预算闸 / 分离 synthesis）——后者代码已丢失。

### ⚠️ 仓库内部架构方向冲突（需用户裁决）
- **v8-plan（06-13）明确写「单 agent，桌宠单机，不 fan-out subagent」** —— 白纸黑字拒绝子代理化。
- **subagent-status（06-19）转向「独立 ReAct 子代理」**，并宣布 v8/ROADMAP「已过时」。

两者方向对立，不是平滑迁移而是架构掉头。

---

## 4. 已核实的真 bug

**recency 维度事实失效**：`score_recency` 依赖发布日期 `date_str`，但 `default_extract` 返回字典
**根本没有 `date` 键**（research_tools.py:826-831，只有抓取时刻 `fetched_at`）→ 普通网页源永远传空串
→ `score_recency` 永远返回默认 3.0（research_scoring.py:230-231）。**合成分里 0.2 权重恒为常数**，
排序实际只由 authority + relevance + depth 决定。仅 direct_sources 路径硬编码 recency=8.0。**确定可修。**

其它弱点：可观测性不足（异常吞进 errors + log.debug，无阶段耗时/命中率指标）；
loopback/ollama 用户拿不到 LLM 精排（main.py 仅非 loopback 注入）；JS 渲染仅 Windows 真落地；
工具 handler 层 `_handle_research_run` 无测试覆盖。

---

## 5. 文件丢失记录（沙箱回滚事故）

| 文件 | 状态 |
|---|---|
| `research/DEEPRESEARCH-HANDOFF.md`（378 行） | ❌ 不可恢复（声称的 commit `c4f7e21` 是无效对象名，全历史无踪）|
| `backend/deskpet/agent/subagents/`（ReAct 子代理 10 文件） | ❌ 不在仓库（从未被 git 跟踪 / 无 .pyc）|
| `research/PENDING-blind-spots-section.md` | ✅ 用户已手动补回（内容并入本档 §6）|

根因：新文件未即时 `git add+commit` → 被沙箱回滚清除。

---

## 6. 调查边界 / 已知盲区（来自 PENDING，下一个设计者需自行补齐）

> ⚠️ 本次调查**全程静态读代码，一次都没运行过 research**。需要**运行时真相**的结论均为静态推断。

**🔴 高优先（几乎肯定需要，基本空白）**
- **现状真实质量基线**：旧 research_run 实际跑出来报告多好/多差、耗时、失败率 —— 零运行证据。
- **LLM 模型与成本**：一次 deep 档烧多少 token / 钱 / 延迟 —— 未查。
- **单机资源约束**：research 占多少内存/CPU、BGE-M3 + Edge 无头渲染开销、能并发几个 —— 未量化。

**🟡 中优先**：前端/UI 呈现形态（research 结果在桌宠里怎么显示/追问）；`v8-reference/evals` + `scorers.py`
（现成质量评测工具，**plan 过时 ≠ evals 过时**）；搜索引擎在中国网络的真实可用性；记忆系统耦合
（子代理的 `memory_search` 线没查）；真实失败模式分布。

**🟢 低优先**：用户真实使用数据（可能没埋点）；历史决策的"为什么"。

**结论**：本档在**静态代码层面**完整可靠，但**不是全部真相**。最划算的第一步是 **Step 0 质量对比 spike**
（真跑 research_run），一次性填掉"质量基线 / 成本 / 耗时 / 失败模式"几个高优先盲区。

---

## 7. 演进建议

**不删 research_run。** 优先级：① 先做 Step 0 质量 spike 拿真实基线 → ② 修 recency 真 bug 等确定项
→ ③ 用 spike 数据决定"原地升级 vs 重建 ReAct 子代理"。详细方案见
[`plans/deepsearch/00-optimization-plan.md`](../plans/deepsearch/00-optimization-plan.md)。

---

## 关键文件
- 现实现：`backend/deskpet/tools/research_tools.py`（research_run @:967）+ `research_scoring.py`
- 入口：`backend/deskpet/skills/builtin/deep-research/SKILL.md`
- 接线：`backend/main.py` :638-667 / :1067-1087
- 评估基线：[`plans/2026-06-19-deep-research-current-vs-plan-assessment.md`](../plans/2026-06-19-deep-research-current-vs-plan-assessment.md)
- 目标架构 plan：`plans/2026-06-13-deep-research-v8/` · `plans/2026-06-14-deep-search-best-practices/` · `plans/2026-06-14-research-reranker/` · `plans/2026-06-16-crawl4ai-fetch-tier/`
- 盲区原档：`research/PENDING-blind-spots-section.md`
