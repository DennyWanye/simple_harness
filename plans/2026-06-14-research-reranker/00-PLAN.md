# deep-research 精排（rerank）— LLM 重排（默认）+ 本地 bge-reranker（可选）

> 2026-06-14 · 承接 [深度搜索最佳实践路线图](../2026-06-14-deep-search-best-practices/00-ROADMAP.md)
> 的 **P1-1（检索后重排）**。用户决策：**默认走中转站廉价模型（gpt-4.1-mini）做 LLM 重排，
> 免下载本地模型**；本地 `bge-reranker-v2-m3` 留作可选档。

---

## 1. 背景与动机

codex 两轮评审反复指出：research 报告"权威源排不上、自媒体混入"。根因——召回后仅靠
**关键词覆盖 + bge-m3 语义相似度**排序，会被"措辞像但其实是转帖"骗高分。最佳实践是召回后
插一层 **cross-encoder 精排**（真"对照阅读"问题+文档后打分）。

**两条实现路径**（资源对比见路线图 §3）：

| | LLM 重排（gpt-4.1-mini，relay）| 本地 bge-reranker（下载 0.6GB）|
|---|---|---|
| 模型下载 | **0** | +0.6GB（int8）|
| 本地内存 | 0 | 调研时 +0.6-1GB |
| 成本 | 每次调研多 1 次廉价 API（18 段×~200 字，mini 便宜）| 0 |
| 依赖 | relay（合成本就依赖）| 纯本地、可离线 |
| 安装包 | **不增** | 首启多下 0.6GB |

→ **桌宠发行场景选 LLM 重排**：安装包不增、不占本地内存、复用 relay。本地档留给离线/
零 API 成本的 power-user。

---

## 2. 设计

### 配置开关
`config.toml`：
```toml
[research]
reranker = "llm"            # llm(默认) | local(本地 bge-reranker,Phase-future) | off
reranker_model = "gpt-4.1-mini"   # llm 重排用的廉价模型(中转站目录里的)
```
- `llm`（默认）：用 `reranker_model` 经 relay 精排。
- `local`：本地 `bge-reranker-v2-m3`（**尚未实现**，当前安全退化为 `llm`）。
- `off`：关闭精排，回到打分排序。

### 数据流（research_run 第 4.7 步，召回+打分+语义refine 之后、cap 之前）
```
候选段落(round1+2,已打分排序)
  → 取 top 2×上限(控 token)做候选池
  → _llm_rerank: 发 [id | 来源层级 | 标题 | 正文前200字] 给 reranker_model
  → 模型返回 [{id,score 0-10}]
  → 把 score 当 relevance 维度,重算 composite(authority×recency×relevance×depth)
  → 重排 → cap 到 max_total_passages → 喂合成
```
关键点：
- **id 用列表位置 1..N**（此处 citation.n 还是 0，重编号在 rerank 之后）。
- **精排分进 composite 的 relevance 维度**，仍与"域名权威先验分"(TIER/SELF_MEDIA)加权，
  权威+真相关才顶上来（不是纯 LLM 说了算）。
- **只在 rerank 桥已注入时触发**（main.py 默认注入 gpt-4.1-mini）；未注入则跳过，
  不回退主 llm（省一次 gpt-5.5 调用）。
- **全程 best-effort**：rerank LLM 失败/解析空 → 保留原打分，研究照常出报告。

### 接线
- `research_tools.set_rerank_llm_call(fn)` + `_RERANK_LLM_CALL` 全局（同 set_live_llm_call/
  set_semantic_scorer 模式）。
- `main.py`：用 `config.llm.local.base_url + keychain key + model=reranker_model` 建
  `OpenAICompatibleProvider` → `_make_str_llm_call(..., max_tokens=1024)` → 注入。
- `coverage.reranker` ∈ {"llm","off"} 写进报告观测。

---

## 3. 实现状态（本次已完成）

- ✅ `research_tools.py`：`_RERANK_LLM_CALL`/`set_rerank_llm_call`/`_rerank_mode`/
  `_RERANK_PROMPT`/`_parse_rerank_scores`/`_llm_rerank` + research_run 第 4.7 步接入 +
  coverage.reranker。
- ✅ `main.py`：gpt-4.1-mini 重排桥注入（`[research].reranker_model` 可覆盖）。
- ✅ 测试：`_parse_rerank_scores` 解析、rerank 重排序（低权威被判高分顶上来）、
  未注入跳过（coverage=off + 主 llm 不被多消耗）。research/scoring 回归 78 passed。

## 4. Phase-future（本地 bge-reranker 可选档）

- `reranker = "local"` 时走本地 `bge-reranker-v2-m3`（int8 ~0.6GB）：复用 `model_provisioner`
  首启下载（同 bge-m3 走 COS）+ BGE-M3 同款 CPU 推理栈；`_local_rerank(passages)` 算
  cross-encoder 分。给离线/零 API 成本用户。
- 触发条件：用户显式配 `local` + 模型已下载；缺模型则退化 `llm` + 日志提示。

## 5. 验收

- 单测：见 §3。
- 真机：桌宠"深度调研 X 出报告" → 日志确认 `model=gpt-4.1-mini` 重排调用 +
  `coverage.reranker=llm`；codex 复评报告质量较未重排版是否再升。
