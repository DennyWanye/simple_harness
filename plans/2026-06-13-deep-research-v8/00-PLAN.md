# DeepResearch V8 移植 + 搜索地基升级 — 实施计划

> 2026-06-13 · 用户要"优化桌宠搜索 + deep-research"，参考 `deepresearch-v8.0.skill`，
> **约束：不接任何外部搜索引擎（付费 Tavily/Exa 或免费 SearXNG/Bing 都不要）**，
> 只用 deskpet 现有 DuckDuckGo 搜索 + web_fetch，把精力放在 V8 方法论/打分/校验/观测。

参考资产存于 [`v8-reference/`](./v8-reference/)（原 skill 解包）。

---

## 背景：现状与差距

**现状**：`research_tools.py` 有完整 7 段管线（plan→search→fetch→score→synth→cite_check），
`deep-research` SKILL 三档。但：
- 搜索只 DuckDuckGo HTML 抓取，`kl="us-en"` 写死 → **中文瘸腿**；无 fallback。
- 桌宠聊天**无独立 web_search**（只 Code 模式有）→ "查一下"得跑 180s 重管线。
- 打分用关键词字面命中（项目有 BGE-M3 没用上）；authority 表小。
- 单轮不迭代；report 不落文件；LLM 桥脱离 relay；两套 DDG 解析重复。

**V8 可移植**：分层权威打分（含中文源）、recency/velocity、来源多样性、引用完整性校验、
reflection 迭代、输出分档方法论、run-summary 观测、quality gates。

---

## Part 1 — 搜索地基（只用 DDG，做对）

- **WI-1.1** 新建 `search_provider.py`：统一 DDG 搜索（合并 `research_tools.default_search`
  + `code_tools/web_search_tool.py` 两份重复解析）。**区域感知**：query 含 CJK → `kl="cn-zh"`，
  否则 `us-en`；UA/超时/uddg 解包统一。
- **WI-1.2** 桌宠聊天注册 `web_search` 工具（toolset=web），复用 search_provider。schema 引导
  "快速查找用 web_search，深度调研用 research_run"。
- **WI-1.3** `code_tools/web_search_tool.py` + `research_tools.default_search` 改为薄封装调
  search_provider（保 BC，去重复）。

## Part 2 — research_run 升级到 V8 实质

- **WI-2.1** 移植 `source_evaluator` 分层打分进 `research_scoring.py`：TIER_1/2/3（含中文
  cnki/xinhua/36kr/zhihu/csdn... + gov/edu/.cn 加成）、recency×topic_velocity、来源多样性
  （unique_domains≥N、单域占比≤25%）、子问题覆盖。替代旧 `score_passage` 关键词命中。
- **WI-2.2** BGE-M3 语义相关性：query/子问题 vs passage 余弦，作 relevance 维度（embedder
  不可用降级回关键词）。
- **WI-2.3** reflection 迭代：首轮 synth 后 LLM gap-analysis → 生成补搜 query → 2 轮补证 →
  重合成。`depth=deep` 才开 2 轮；light/standard 单轮。
- **WI-2.4** LLM 桥走 live relay：`_handle_research_run` 注入运行中 local_llm（同 summarizer
  `_make_str_llm_call`），弃 `_resolve_default_llm_call` 自建 provider；synth max_tokens 调大。
- **WI-2.5** 引用完整性校验移植（verify_citations：`[n]` 命中来源池、无悬空、URL 真实、
  单源占比 ≤25%）进管线 + coverage 字段。
- **WI-2.6** 报告落文件：`paths.output_dir("Research")/<slug>-<ts>.md`（含 footnotes+引用
  appendix），artifacts[] emit → 聊天卡片可点开；超长可选 .docx（复用 doc_create）。

## Part 3 — SKILL.md 重写 V8 方法论 + 观测

- **WI-3.1** 重写 `deep-research/SKILL.md`：输出分档路由（brief/full/delta）、notes-first、
  证据 claim-specific、limitations 强制、non-obvious insight、red lines（不编造/不省 errors/
  抓取时间可见）。单 agent（桌宠单机，不 fan-out subagent）。
- **WI-3.2** run-summary 观测：research_run 返回 coverage 扩展（route/mode/sources/domains/
  cite_ok/velocity/rounds），SKILL 指导桌宠回报"覆盖 X 源/Y 域/N 轮"。

## 测试 / 验收

- 单测：search_provider（区域切换/解析/uddg）、research_scoring（分层/recency/多样性/覆盖）、
  reflection（gap→补搜）、cite 校验、报告落盘。BC：现有 research/web 测试不破。
- 真机：桌宠"查一下 X"（web_search 秒回）+ "深度调研 X 出报告"（中文主题真出多源带引用报告
  落 OutPut/Research，WPS/编辑器打开核对引用真实）。

## 约束

- ❌ 不接任何外部搜索引擎（付费/免费第三方），只 DuckDuckGo。
- ✅ flag/降级友好：embedder 不可用→关键词；relay 不可用→部分结果不编造。
- ✅ 中文优先；报告默认 brief，按需 full。
