# Plan：deepresearch source-pack 优化

## 关联验收标准

- 覆盖 AC-1, AC-2, AC-3, AC-4。

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|------|------|----------|
| `backend/deskpet/tools/research_tools.py` | deepresearch 主编排器 | 增加 source-pack 规则、配置开关、查询生成、coverage 观测字段。 |
| `backend/deskpet/skills/builtin/deep-research/SKILL.md` | 技能路由与使用说明 | 更新为 source-pack + Scrapling-first 的 deepresearch 使用说明。 |
| `backend/tests/test_deskpet_research_tools.py` | deepresearch 行为单测 | 增加 source-pack 默认开启、kill-switch、俄乌局势定向查询、coverage 断言。 |
| `config.toml` | 用户可见配置文档 | 增加 `[research].source_packs` 和每子问题上限说明。 |
| `README.md` / `STATUS/status.md` / `testcase/` | 文档与验收记录 | 按项目纪律同步架构入口、里程碑与回归用例。 |

## 任务清单（按依赖排序）

### Task 1 — source-pack 规则与配置 [覆盖 AC-1, AC-2]
- 改动文件：`backend/deskpet/tools/research_tools.py`
- 现状：`search_specs` 只包含普通子问题、`_site_directive_for()` 单条 site 规则、query expansion。
- 修改方式：
  - 新增 `_source_packs_enabled()`，默认读取 `[research].source_packs`，缺省 True。
  - 新增 `_source_pack_max_queries_per_question()`，缺省 3，限制范围 0-6。
  - 新增 `_source_pack_queries_for(text)`，当关键词命中 geopolitical/current-events/gold 等 pack 时返回 `(pack_name, query_suffix)`。
  - 在构造 `search_specs` 时追加 source-pack query，并去重。
- 验证：单测检查俄乌局势会追加权威 site 查询，kill-switch 关闭后不追加。

### Task 2 — coverage 观测 [覆盖 AC-3]
- 改动文件：`backend/deskpet/tools/research_tools.py`
- 现状：coverage 包含 engine/direct source/reranker/diversity 等，但没有 source-pack 相关字段。
- 修改方式：
  - 在 `route` 中记录 `source_packs_enabled`、`source_packs_hit`、`source_pack_queries`。
  - 通过 `_observability_coverage()` 进入最终 coverage；空结果也要带这些字段。
- 验证：单测断言 `ResearchReport.coverage["route"]` 包含 pack 名称与查询数量。

### Task 3 — skill 文案与配置文档 [覆盖 AC-4]
- 改动文件：`backend/deskpet/skills/builtin/deep-research/SKILL.md`、`config.toml`
- 现状：skill 已禁止外层手动 `web_search` + `web_fetch`，但没有描述 source packs 和 Scrapling-first。
- 修改方式：
  - 更新管线说明，明确“source packs 定向权威来源”和“fetch/extract 优先 Scrapling，失败才退回 httpx/JS/Jina”。
  - 在 config 文档中补充默认开启的 source-pack 开关和上限。
- 验证：文本搜索断言关键字存在。

### Task 4 — 回归与文档闭环 [覆盖全部 AC]
- 改动文件：测试、`STATUS/status.md`、`plans/index.md`、`testcase/`。
- 修改方式：补测试用例文档，跑 focused pytest，更新状态档。
- 验证：pytest 通过；可追溯矩阵无断点。

## 权衡

- 选择 deterministic source packs，而不是再加一次 LLM 判断来源策略：可测试、成本低、不会拖慢每轮 plan。
- 不直接抓固定 URL feed：不同主题变化大，先用 site-directed SERP 召回，再复用现有 Scrapling-first extract、scoring、rerank。
- 默认开启符合当前测试阶段规则；提供 kill-switch 方便定位问题。
