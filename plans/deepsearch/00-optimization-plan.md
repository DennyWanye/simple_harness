# DeepResearch 继续优化方案

> **日期**: 2026-06-20
> **前置**: [STATUS/DeepResearch.md](../../STATUS/DeepResearch.md) · [现状 vs plan 评估](../2026-06-19-deep-research-current-vs-plan-assessment.md) · 盲区档 `research/PENDING-blind-spots-section.md`
> **状态**: 📋 规划（待用户裁决架构方向）

---

## 0. 背景与一句话

桌宠当前可用的 deep research = `research_run`（固定流水线，能力已较完整）。"新 ReAct 子代理"代码已丢失。
本次优化的核心矛盾不是"缺能力"，而是 **(a) 一个确定的真 bug + (b) 一个未决的架构方向 + (c) 整个质量判断零运行证据**。
所以本方案**先验证、后决策、再施工**，而不是上来就重写。

## 1. 指导原则（来自上一轮调查的收口结论）

1. **先 spike 用真实数据验证**——不靠静态推断拍架构。
2. **守住"检索质量"这个刀刃**——deep research 的价值在召回+排序的质量，不在控制流花哨。
3. **一件事做完再开下一件**——不并行铺摊子，每步有验收门。

## 2. 分步路线（每步带验收门）

### Step 0 — 质量基线 spike（🔴 最高优先，决策前置门）
**目的**：把"零运行证据"这个最大盲区填掉，拿到决策依据。
**做什么**：
- 选 5–8 个代表性主题（中/英、快/慢速、财报类、技术类各覆盖），用真 LLM 链路各跑一次 `research_run`
  的 standard + deep 档（按 `CLAUDE.md` §开发期登录测试账号 走真实 relay key）。
- 每次记录：报告质量（人工评 + 可选复用 `v8-reference/evals` 的 scorers）、耗时、token/成本、
  失败阶段分布、最终源的权威/多样性分布、recency 修前对照。
- 顺带量化单机资源（内存/CPU 峰值、JS 渲染开销、并发上限）。
**验收门**：产出一份 `01-baseline-spike-report.md`，回答："现状质量到底如何 / 瓶颈在检索还是综合 / deep 档值不值这个成本"。
**不通过则不进 Step 2。**

### Step 1 — 确定性快赢（与 Step 0 可并行，低风险）
1. **修 recency 真 bug**：让 `default_extract` 抽取发布日期（trafilatura `extract_metadata` 已有 `meta.date`）
   填进返回字典的 `date` 键，激活整个新鲜度维度。**带单测 + Step 0 前后对照验证它真的改变了排序。**
2. **补可观测性**：给每阶段加结构化事件/耗时/命中率（rerank_used / 各阶段 drop 计数 / coverage 扩展），
   为 Step 0 的测量和未来回归提供数据。
**验收门**：recency 修复有单测 + 真机对照证明生效；run-summary 能看到各阶段指标。

### Step 2 — 架构决策（依赖 Step 0 数据 + 用户裁决）
**待决问题（见 §3）**：原地升级 `research_run` vs 重建 ReAct 子代理。
- 若 Step 0 显示瓶颈在**检索/排序质量** → 倾向**原地升级**（加本地 bge-reranker、改进 query 策略、
  补 crawl4ai webview fetch tier），不动控制架构。
- 若 Step 0 显示瓶颈在**流程僵化**（固定 2 轮反思不够、复杂题目需自主多轮探索）→ 才考虑 ReAct 子代理，
  且需先解决架构方向冲突（v8 plan 明确反对子代理）。
**验收门**：用户基于 spike 数据拍板方向，本方案据此展开 Step 3。

### Step 3 — 按决策施工（占位，待 Step 2 后细化）
- **分支 A（原地升级）**：本地 bge-reranker 可选档 / query 策略增强 / crawl4ai webview 反向链路 GATE / 失败模式针对性加固。
- **分支 B（ReAct 子代理）**：重写 `agent/subagents/`（受限只读工具子集 + 三重预算闸 + 分离 synthesis +
  EvidenceLog）+ 接线 main.py + tool 暴露 + 把现有打分/一手源/抓取层嫁接进子代理工具 + 测试 + 真机 E2E。

## 3. 未决问题（需用户裁决）

1. **架构方向**：原地升级 vs ReAct 子代理？（仓库内 v8 plan 与 06-19 状态笔记方向对立）
2. **若走子代理**：是否接受重写并把已调好的能力重新嫁接的回归风险？
3. **Step 0 用哪套质量评测**：纯人工评 vs 复用 `v8-reference/evals` + `scorers.py`？

## 4. 范围 / 非目标

- **不删 `research_run`**（删了桌宠就没 deep research）。
- **不接付费搜索 API**（沿用 v8/ROADMAP 硬约束）。
- 本方案**不预设**走子代理；架构决策交给 Step 0 数据 + 用户。

## 5. 盲区提醒（施工时自行补）

前端/UI 呈现形态、记忆系统耦合（`memory_search` 线）、搜索引擎在中国网络真实可用性——
这些静态调查没碰，施工到相关环节时需补。详见 STATUS/DeepResearch.md §6。
