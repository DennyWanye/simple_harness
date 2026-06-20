# CODEX 任务 2：§6.0-A 直连权威源扩展（只动 research_sources.py）

仓库 G:/projects/deskpet。**先读** `plans/deepresearch-upgrade/00-upgrade-plan.md` 的 §6.0-A + §6.0.1 契约-1/契约-2 + §6.0.3(灰度:direct_source_types 默认值 / 依赖裁决) + §6.0.4(待定量)。严格照它。

**唯一可改文件**：`backend/deskpet/tools/research_sources.py`（+ 测试加进 `backend/tests/test_research_sources.py`、fixture 加 `backend/tests/fixtures/{wikipedia,arxiv,s2,wikidata}_sample.*`）。**不要动** research_tools.py（§4.4 挂载由 Lead 集成）/ search_provider.py / config.py。

## 必做（逐项）
1. **新增 4 个 async fetcher**（照现有 `cninfo_search`:227 / `openstd_search` 模式，**返回结构严格对齐契约-2**）：
   - `wikipedia_search(keyword,*,max_results=3,client=None)`：MediaWiki opensearch (`{zh|en}.wikipedia.org/w/api.php?action=opensearch`) 取标题 → REST summary (`/api/rest_v1/page/summary/{title}`) 取正文。**大陆可达性**：照 edgar 模式 `for trust_env in (False,True)` 双试，**每试 ≤6s 超时**。
   - `arxiv_search(...)`：arXiv API (`export.arxiv.org/api/query?search_query=...`)。返回 **Atom XML** → **用 stdlib `xml.etree.ElementTree` 解析**（照 `web_tools.py:32` 模式，**禁止 import feedparser / 新依赖**）。
   - `semantic_scholar_search(...)`：S2 Graph API (`api.semanticscholar.org/graph/v1/paper/search`)，无 key 匿名档。**429 风险高** → 命中 429/超时 **静默降级返 []**（处理 Retry-After，不抛）。
   - `wikidata_search(...)`：Wikidata REST/SPARQL，结构化 triples → 序列化成自然语言 text。复杂度高，best-effort，失败返 []。
   - 每个返回 `list[dict]`，**每条 dict 严格 = 契约-2**：`{"ok":True, "url":str, "title":str[:200], "text":str[:18000]非空, "fetched_at":float, "source":"wikipedia"/"arxiv"/"semantic_scholar"/"wikidata"}`。text 为空的条目**丢弃**（不返回）。
2. **`direct_source_for` 改多源返回**（:66，当前返回 `Optional[str]`）：
   - 改为返回 `list[str]`（命中多个就都返回；无命中返 `[]`）。意图关键词表照现有 cninfo/openstd 模式加：财报/上市公司→cninfo；国标/标准/GB→openstd；综述/背景/「是什么」类→wikipedia；论文/学术/研究/技术选型→arxiv + semantic_scholar；结构化事实/实体→wikidata。
   - ⚠️ 受 `direct_source_types` 准入硬条件门控（见第3点）：只返回**已启用**的源。
   - **保留向后兼容**：如有内部旧调用按 `Optional[str]` 用，需同步（research_tools 的调用由 Lead 改，你只改 research_sources 内部 + 暴露清晰新签名 + docstring 标注"返回 list，Lead 集成端按 list 消费")。
3. **flag**：新增读取 `[research].direct_source_types`（准入硬条件），**默认 `["cninfo","openstd","wikipedia","arxiv"]`**（**semantic_scholar / wikidata 默认 off**，因 429/被墙/SPARQL 序列化风险）。`direct_source_for` 只返回在此 list 内的源。提供 `_direct_source_types()` 读取器（照现有 `_research_raw().get(...)` 模式）。
4. **FETCHER 映射**：建 `DIRECT_FETCHERS: dict[str, Callable] = {"cninfo":cninfo_search,"openstd":openstd_search,"wikipedia":wikipedia_search,"arxiv":arxiv_search,"semantic_scholar":semantic_scholar_search,"wikidata":wikidata_search}` 供 Lead 集成端 dispatch（替代现有 if/elif）。

## 硬约束
- **零新 pip 依赖**：arxiv Atom 用 stdlib `xml.etree.ElementTree`；其余 httpx+.json()。
- 所有 fetcher best-effort：任何网络/解析失败 → 返 `[]`，**绝不抛到调用方**。
- 不接付费 API。

## 验收（必须）
1. 新增 `backend/tests/test_research_sources.py` 用例（或扩现有）：
   - 每个新 fetcher：落一份真实 API 响应 fixture（`tests/fixtures/{wikipedia,arxiv,s2,wikidata}_sample.*`，arxiv=Atom XML、wikidata=SPARQL JSON、wikipedia/s2=JSON）；mock HTTP 返该 fixture → 断言输出满足契约-2 **全字段**（重点 `text` 非空 + title≤200 + text≤18000 截断）。
   - 降级：mock 429/超时/被墙 → 断言返 `[]` 不抛。
   - `direct_source_for`：综述类 query → 含 wikipedia；论文类 → 含 arxiv；财报 → 含 cninfo；**断言默认不含 semantic_scholar/wikidata**（默认 off）；配 `direct_source_types` 开 s2 → 含 s2。
   - `direct_source_for` 返回类型是 `list[str]`。
2. 跑 `cd backend && .venv/Scripts/python.exe -m pytest tests/test_research_sources.py -q` 全绿。

## 完成后
输出：新增 fetcher 名 + `direct_source_for` 新签名 + `DIRECT_FETCHERS` 映射 + `_direct_source_types` 默认值 + 新增测试名 + pytest passed 数。**不要 commit**。
