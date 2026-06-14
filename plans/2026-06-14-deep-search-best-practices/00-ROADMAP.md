# 深度搜索能力升级路线图 — 不接付费搜索 API 的最佳实践落地

> 2026-06-14 · 来源：联网调研子代理（11 次 WebSearch + 2 次 WebFetch，每条带源）
> + codex gpt-5.5 两轮报告评审（真实性 5.5→6.5、完整度 7→7.5）驱动。
> 约束：**不接任何付费搜索 API（Tavily/Exa/Serper/Brave 付费/Google CSE 付费档）**。

---

## 1. 背景与现状

DeskPet 的 deep-research（`research_run`，DeepResearch V8）当前搜索层只用
**DuckDuckGo HTML 抓取**。codex 评审真机报告暴露的核心短板：
- 中文权威一手源（政府/标准/企业公告）排不上来，搜到的多是搜狐/百家号转帖；
- 单引擎脆弱、易限流；
- trafilatura 对 JS 渲染站/付费墙无能。

**已具备**（本轮已做）：✅ gap-driven 反思迭代（depth=deep 2 轮）、✅ 分层权威打分
（TIER_1/2/3 含中文源）、✅ 自媒体降权 + AI 生成内容剔除（扫原始 HTML）+ 官方源优先
+ 数据口径提示、✅ 废引用清理、✅ BGE-M3 语义相关性、✅ trafilatura 抽取。

---

## 2. 业界最佳实践技术栈（2025-2026，全免费闭环）

```
query 改写(multi-query + HyDE + site:定向)
  → 搜索: SearXNG 自托管聚合(主) + 一手源直连API + DuckDuckGo(兜底)
  → 抓取三级: trafilatura → r.jina.ai 免费档 → Playwright
  → 去噪: 域名权威白/黑名单规则 + 语义去重 + AI内容剔除
  → 重排: bge-reranker-v2-m3(本地CPU)
  → 综合 + 反思缺口→补搜(默认≤3轮)
```

**5 条核心结论**（每条带源）：
1. SearXNG 自托管聚合 70+ 引擎（Google/Bing/百度/搜狗/360），无 key 无月费，解决单引擎
   脆弱+中文召回 — 但需 Docker。付费档在收缩（Bing API 2025-08 退役、Brave 2026-02 转计费）
   → 靠"某家免费额度"不可持续。
   [searxng-docker-china](https://github.com/He-Xun/searxng-docker-china) ·
   [Brave 转计费](https://www.implicator.ai/brave-drops-free-search-api-tier-puts-all-developers-on-metered-billing/)
2. 抓取三级 fallback：trafilatura(F1≈0.945) → Jina Reader `r.jina.ai` 免费档(server 端跑 JS)
   → Playwright 兜底。[ScrapingHub benchmark](https://github.com/scrapinghub/article-extraction-benchmark) ·
   [Jina Reader](https://deepwiki.com/jina-ai/reader)
3. 本地 `bge-reranker-v2-m3` 重排（多语种 cross-encoder，CPU 可跑 ~130ms/16对），压自媒体顶
   一手源。[BAAI/bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3)
4. 中文一手源走专用直连 API（巨潮 `hisAnnouncement/query`、国家标准全文系统、Wikidata），
   从源头绕开转帖。[use_cninfo](https://github.com/rollysys/use_cninfo)
5. gap-driven 反思迭代 + site: 定向官方域（site:gov.cn / site:cninfo.com.cn / site:arxiv.org）
   对"找一手源"最有效。[local-deep-researcher](https://github.com/langchain-ai/local-deep-researcher)

**最贴近的开源范本**：`langchain-ai/local-deep-researcher`（DuckDuckGo/SearXNG + 本地 LLM
+ 3 轮反思 loop）形态几乎就是我们的桌宠 deep-research，重构主参考。STORM 的"Wikipedia
可靠源规则过滤"= 我们已做的"自媒体降权"范式。

---

## 3. 资源核算（落地约束 — 2026-06-14 实测）

**本机基准**：64GB 内存 / i7-13700KF（开发机富裕）；**Docker 未安装**；已有 bge-m3
实占 ~2.2GB（单格式；当前目录 4.3GB 因 pytorch+onnx 双份冗余）。**桌宠发给终端用户，
机器可能远小于此（8-16GB）**——资源决策按"最低配用户"算。

| 方案 | 外部依赖 | 磁盘 | 激活内存 | 适合进默认安装包? |
|---|---|---|---|---|
| **bge-reranker-v2-m3** | **0**（纯本地，复用 BGE-M3 推理栈）| int8 ~0.6GB / fp16 ~1.1GB | ~0.6-1.1GB | ✅ 是 |
| **SearXNG 自托管** | **需 Docker**（用户劝退）| 镜像 ~几百 MB | 容器 ~200-400MB | ⚠️ 否，仅 power-user/可选 |
| **Jina Reader 二级抓取** | r.jina.ai HTTP（外部免费服务，无 key 限速）| ~0 | ~0 | ✅ 是（纯 HTTP 调用）|
| **巨潮/Wikidata 直连** | HTTP API | ~0 | ~0 | ✅ 是 |
| Playwright 三级兜底 | 浏览器二进制(~数百MB) | 数百 MB | 启动时 ~300MB+ | ⚠️ 偏重，按需 |

**关键结论**：
- `bge-reranker` 是 0.6B 参数，**和已装的 bge-m3 同 backbone（XLM-RoBERTa-large）**，
  同套下载/加载/CPU 推理代码 → 加它=再下一个同款大小模型 + 复用推理。建议 **int8/fp16**
  对齐瘦包。小机器同时载 bge-m3+reranker ≈ 2.5-4.5GB 模型，16GB+ 无压力、8GB 偏紧。
- SearXNG 的真正成本不是 RAM 而是 **Docker 依赖**，不进默认安装包。

---

## 4. 分阶段路线图（按 ROI × 部署友好度排序）

### Phase 1 — 纯本地零依赖，立刻提质（短期，最高 ROI）
- **P1-1 本地 bge-reranker-v2-m3 重排** ★首选：召回后（DDG/语义打分出 top-N）插一层
  cross-encoder 重排，与"域名权威先验分"加权融合，把权威源顶进喂给 synth 的 top-K。
  纯本地、零外部依赖、复用 BGE-M3 基建、int8 ~0.6GB。直解 codex 反复指出的"权威源排不上"。
  - 复用 `model_provisioner` 首启下载（同 bge-m3 走 COS/HF）；`reranker.encode` 接 CPU 推理；
    研究管线 score 后、cap 前插重排步骤；embedder mock / 模型缺失时降级跳过。
- **P1-2 site: 定向官方域模板**：按 query 类型自动加 `site:gov.cn`/`site:cninfo.com.cn`/
  `site:arxiv.org`（政策/企业/学术）。纯 prompt+search 改动，零成本。
- **P1-3 Jina Reader 二级抓取**：trafilatura 失败/正文过短 → 调 `https://r.jina.ai/<url>`
  拿 JS 渲染后 Markdown。纯 HTTP（外部免费服务，可 `[research].jina_reader` 开关 + 超时）。

### Phase 2 — 一手源直连通道（中期）
- **P2-1 中文一手源 API**：巨潮 `hisAnnouncement/query`（上市公司公告）+ Wikidata/Wikipedia API。
  research_run 识别"某公司公告/百科事实"类子问题时走专用通道，绕开搜索引擎转帖。
- **P2-2 query 策略补 multi-query + HyDE**：对短/噪 query 生成多条改写 + 假设答案再检索。

### Phase 3 — 可选/进阶（power-user，不进默认包）
- **P3-1 SearXNG 自托管接入**：`[research].searxng_url` 配置项；填了就走 SearXNG 聚合
  （Google/Bing/百度/搜狗/360）替代 DDG，没填走 DDG。**默认关，给愿意装 Docker 的用户**。
  公共实例不稳故不默认用公共。
- **P3-2 Playwright 三级抓取兜底**：强反爬站；浏览器依赖重，按需 opt-in。

---

## 5. 建议

**先做 Phase 1（P1-1 bge-reranker 优先）**：纯本地、零依赖、复用现有基建、直接解决 codex
反复指出的"权威源排不上/自媒体混入"，且体积可控（int8 ~0.6GB）。Phase 3 的 SearXNG 因
Docker 依赖留作可选项、不进默认安装包。

**与现状的衔接**：P1-1 接在现有"分层打分 + 语义打分"之后做最终重排，不推翻已有逻辑；
权威白/黑名单先验可直接复用 `research_scoring` 的 TIER/SELF_MEDIA。

---

## 附：关键来源
- SearXNG: [中国优化部署](https://github.com/He-Xun/searxng-docker-china) | 免费档收缩: [Brave](https://www.implicator.ai/brave-drops-free-search-api-tier-puts-all-developers-on-metered-billing/) · [Bing 退役](https://cloro.dev/blog/bing-search-api-key/)
- 抓取: [trafilatura eval](https://trafilatura.readthedocs.io/en/latest/evaluation.html) · [ScrapingHub benchmark](https://github.com/scrapinghub/article-extraction-benchmark) · [Jina Reader](https://deepwiki.com/jina-ai/reader)
- 重排: [bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3)（0.6B params, base=bge-m3）
- 查询策略: [Query Decomposition](https://arxiv.org/pdf/2510.18633) · [RAGSmith/HyDE](https://arxiv.org/html/2511.01386v1) · [iterative retrieval](https://arxiv.org/pdf/2509.04820)
- 框架: [GPT-Researcher](https://docs.gptr.dev/docs/gpt-researcher/search-engines) · [STORM](https://github.com/stanford-oval/storm) · [local-deep-researcher](https://github.com/langchain-ai/local-deep-researcher) · [Open Deep Research](https://github.com/langchain-ai/open_deep_research) · [Jina DeepResearch](https://deepwiki.com/jina-ai/node-DeepResearch) · [OpenDeepSearch](https://github.com/sentient-agi/OpenDeepSearch)
- 中文一手源: [巨潮 use_cninfo](https://github.com/rollysys/use_cninfo) · [CnInfoReports](https://github.com/tr1s7an/CnInfoReports) · [深证信 webapi](https://webapi.cninfo.com.cn/)
