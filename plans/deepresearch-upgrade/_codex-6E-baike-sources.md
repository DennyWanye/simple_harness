# CODEX 任务：§6.0-A 扩展 — 百度百科/搜狗百科直连源 + 维基可达门控（只动 research_sources.py）

仓库 G:/projects/deskpet。先读 `plans/deepresearch-upgrade/00-upgrade-plan.md` §6.0-A + §6.0.1 契约-2（返回结构）。**唯一可改文件**：`backend/deskpet/tools/research_sources.py`（+ 测试 `backend/tests/test_research_sources.py` + fixtures）。**不要动** research_tools.py / search_provider.py / config.py。

## 背景
现有直连源 cninfo/openstd/wikipedia/arxiv（`DIRECT_FETCHERS` 映射，`direct_source_for` 意图路由，`_direct_source_types` 门控）。维基百科国内可能被墙。需加**国内稳定**的百科直连源 + 给维基/可被墙源加**可达探测**（通才用）。

## 必做
1. **共享可达探测** `async def _reachable(host: str, *, timeout: float = 4.0) -> bool`：
   - 对 `https://{host}/` 发轻量 GET（或 HEAD），≤timeout 秒；成功(任意 2xx/3xx)→True，超时/连接错→False。
   - **进程内缓存结果**（模块级 dict + 短 TTL，如 300s，用可注入 `_now`），避免每次研究重复探测。提供 `_reset_reachable_cache()` 供测试/每轮。
2. **`baidu_baike_search(keyword,*,max_results=3,client=None)`**：百度百科。
   - 词条：`https://baike.baidu.com/item/{quote(keyword)}`；若直接 GET 被反爬/空，可退化用搜索页 `baike.baidu.com/search?word=...` 取首条 item 链接再取词条。
   - trafilatura 抽正文（已 import 模式见现有 fetcher）。返回**严格契约-2**：`{"ok":True,"url":...,"title":...[:200],"text":...[:18000]非空,"fetched_at":float,"source":"baidu_baike"}`。空 text 丢弃。
   - best-effort：任何失败返 `[]` 不抛。
3. **`sogou_baike_search(...)`**：搜狗百科 `https://baike.sogou.com/`，搜索 `baike.sogou.com/Search.e?sp=S{keyword}` 或词条页；同上抽正文 + 契约-2，`source="sogou_baike"`，best-effort。
4. **维基百科加可达门控**：现有 `wikipedia_search` 开头加 `if not await _reachable("zh.wikipedia.org") and not await _reachable("en.wikipedia.org"): return []`（不通快速跳过，不傻等超时）。保留其余逻辑。
5. **意图路由 `direct_source_for` 扩展**：通用/百科类意图（`_WIKIPEDIA_KW`：综述/背景/是什么/概述/介绍/定义/入门 等）现在**同时路由到** `baidu_baike` + `sogou_baike` + `wikipedia`（维基靠可达门控自动跳过）。即 `_WIKIPEDIA_KW` 命中时 hits 追加 `["baidu_baike","sogou_baike","wikipedia"]`。
6. **门控默认值** `_DIRECT_SOURCE_DEFAULT_TYPES` 追加 `baidu_baike`、`sogou_baike`（默认开，国内稳定）。维基保持默认开。`_ALL_DIRECT_SOURCE_TYPES` / `_DIRECT_SOURCE_ORDER` 同步加这两个（顺序建议：cninfo,openstd,baidu_baike,sogou_baike,wikipedia,arxiv,semantic_scholar,wikidata）。
7. **`DIRECT_FETCHERS` 映射**加 `"baidu_baike":baidu_baike_search`、`"sogou_baike":sogou_baike_search`。

## 硬约束
- **零新 pip 依赖**（httpx + trafilatura 已有）。
- 所有 fetcher + 可达探测 best-effort，绝不抛到调用方。
- 不动 search_provider / research_tools（Lead 集成）。

## 验收
1. 测试：
   - baidu_baike/sogou_baike 各落 fixture（词条页 HTML 样本）+ mock HTTP → 断言契约-2 全字段（text 非空 + 截断）；mock 失败 → 返 []。
   - `_reachable`：mock 成功/超时两态 + 缓存命中（注入 `_now` 推进，不真 sleep）。
   - wikipedia 不可达（mock _reachable→False）→ `wikipedia_search` 返 [] 且**不发实际 API 请求**。
   - `direct_source_for("XX综述")` 含 `baidu_baike`/`sogou_baike`/`wikipedia`（默认）。
   - 默认 `_direct_source_types()` 含 baidu_baike/sogou_baike。
2. `cd backend && .venv/Scripts/python.exe -m pytest tests/test_research_sources.py -q` 全绿。

## 完成后
输出：新 fetcher 名 + `_reachable` 签名 + 路由/默认/映射改动 + 新测名 + passed 数。**不要 commit**。
