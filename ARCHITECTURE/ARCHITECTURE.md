# DeskPet DeepResearch 架构基线

## 范围

本文只记录本次 deepresearch source-pack 优化相关的后端与技能架构，不覆盖 DeskPet 全仓库。

## 关键模块

| 模块 | 职责 | 当前行为 |
|---|---|---|
| `backend/deskpet/skills/builtin/deep-research/SKILL.md` | 面向 LLM 的技能说明和路由约束 | 要求深度调研时一次性调用 `deepresearch`，不要外层手动 `web_search` + `web_fetch` 拼报告。 |
| `backend/deskpet/tools/research_tools.py` | deepresearch 主编排器 | plan 拆子问题、query expansion、source packs 定向权威源搜索、普通搜索、Scrapling-first 抓取抽正文、直连权威源、打分/精排、合成报告、引用自检、coverage 返回。 |
| `backend/deskpet/tools/search_provider.py` | 通用搜索 provider | 提供 google-cdp / bing-cdp / searxng / opt-in fallback 搜索队列，记录命中的 engine 和侧信道错误。 |
| `backend/deskpet/tools/scrapling_tools.py` | Scrapling-backed 抓取工具 | `scrapling_fetch` 和 `gold_price_lookup`，并提供 `_scrapling_get_html()` 给 `web_fetch` / `default_extract` 复用。 |
| `backend/deskpet/tools/file_tools.py` | workspace 文件工具 | `file_read`/`file_write`/`file_grep` 保持 workspace 沙箱；`file_glob` 在默认递归扫描时剪枝重型生成目录，并返回跳过目录元数据。显式 `root` 指到被跳过目录时仍允许访问。 |
| `backend/agent/agent_loop.py` | 外层 ReAct loop | deepresearch 成功返回完整报告后，下一轮强制 `tools=None` + `tool_choice=none`，避免报告已出还继续 web_search。 |

## deepresearch 数据流

1. 技能触发后，LLM 调 `deepresearch(topic, depth, ...)`。
2. `deepresearch()` 解析 depth preset，调用 `research_run()`。
3. `research_run()` 通过 LLM 拆 3-6 个子问题，并可选生成 query expansion。
4. 搜索阶段为每个子问题构造搜索 query，目前包括普通 query、site-directed 官方域 query、source-pack 权威来源 query、query expansion query。
5. 抓取阶段调用 `default_extract()`；真实运行中 `client is None` 时优先 `_scrapling_fetch_html()`，成功后再交给 trafilatura 抽正文，必要时走 JS render / Jina fallback。
6. scoring 阶段过滤低质/AI/乱码源，按 authority、recency、relevance、depth 综合打分，可选 BGE-M3 semantic scorer 和 LLM reranker。
7. direct sources 阶段追加巨潮、EDGAR、国标、百科、Wikipedia/arXiv 等 bypass SERP 的来源。
8. synth 阶段要求 LLM 基于 passages 生成带 `[^n]` 引用的 Markdown 报告。
9. `cite_check` 自检引用编号，并将 coverage/errors/report 返回；handler 落盘到 `DeepResearch/` 并更新 index。

## source-pack 优化现状

- 高时效国际局势问题命中 `geopolitics_ukraine` pack 时，会追加 ISW、UN、Reuters/AP/BBC/Al Jazeera 定向搜索。
- 黄金/金价问题命中 `gold_market` pack 时，会追加 LBMA、World Gold Council、Investing 定向搜索。
- `coverage.route` 暴露 `source_packs_enabled`、`source_packs_hit`、`source_pack_queries`，用于测试与后续 UI/日志观测。
- deep-research skill 文案已说明 source packs + Scrapling-first 的组合策略。

## 约束

- 不引入付费搜索 API。
- source-pack 查询必须有数量上限，不能让一次 deepresearch 搜索量失控。
- 所有新增能力默认开启，但保留 `[research]` kill-switch。

## file_glob 扫描约束

- `file_glob` 仍只接受 workspace-relative `root`，逃逸 workspace 的 root 继续返回 `path outside workspace`。
- 默认递归扫描跳过常见重型目录名（如 `node_modules`、`__pycache__`、`.uv-cache`）和 DeskPet 大资产相对路径（如 `backend/assets`、`backend/models`）。
- 跳过只发生在从祖先目录扫入这些目录时；如果调用方显式把 `root` 设为沙箱内的 `node_modules` / `backend/assets`，仍可读取其中匹配文件，避免兼容性倒退。
- `skipped_dirs` 为去重、排序、workspace-relative、正斜杠目录路径；`skipped_count` 为唯一跳过目录数量。
