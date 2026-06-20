# DeepResearch 升级 Plan（工具更名 + 演进）

> **日期**: 2026-06-20　**状态**: 📋 待执行（v1，待子代理挑战迭代）
> **前置**: [STATUS/DeepResearch.md](../../STATUS/DeepResearch.md) · [现状评估](../2026-06-19-deep-research-current-vs-plan-assessment.md) · [优化策略](../deepsearch/00-optimization-plan.md)
> **关系**: 本 plan 是 [`plans/deepsearch/00-optimization-plan.md`] 策略的**代码级实施版**，把工具从 `research_run` 更名为 `deepresearch` 并落地分阶段升级。

---

## 1. 目标与决策

### 1.1 目标
把桌宠当前的深度调研工具从 `research_run` **更名为 `deepresearch`**（LLM 可见的工具名），并在更名的同时做一轮有验收门的演进：先拿真实质量基线，修已知真 bug，按数据补缺口。

### 1.2 架构决策（本 plan 锁定，可被用户推翻）
**采用「在现有 `research_run` 实现上演进 + 更名为 `deepresearch`」，不重建 ReAct 子代理。** 理由：
- `research_run`（1736 行）已吸收各 plan 成果，是经真机踩坑打磨的可用实现；删它桌宠就没 deep research。
- ReAct 子代理代码已丢失（重写 ~1100 行 + 重新嫁接所有已调好能力 = 高回归风险），且与 v8-plan「单 agent，不 fan-out subagent」的显式决策冲突。
- ReAct 架构带来的是「不同控制流」，不是「更多能力」；是否值得须由 Phase 0 真实数据决定。

→ **ReAct 子代理降级为 Phase 4「可选/延后」**，默认不做。

### 1.3 命名口径
- **LLM 可见工具名**：`research_run` → **`deepresearch`**（必改）。
- **Python 入口函数**：`research_run()` → `deepresearch()`，保留 `research_run = deepresearch` 弃用别名（让现有 tests/scripts/import 不破）。**别名生命周期：保留至 Phase 3 结束，下一轮升级再删**（不在 Phase 1 删——Phase 1-3 的测试/脚本都还靠它）。
- **模块文件名**：`research_tools.py` 暂不改名（避免一次性 import 大改），记为可选后续。

---

## 2. 改名手术面（代码级，逐处）

> 全部位于 `backend/`。改完跑 §7 测试矩阵验证。

| # | 文件:行 | 现状 | 改为 | 必须性 |
|---|---|---|---|---|
| R1 | `deskpet/tools/research_tools.py:1520` | `_RESEARCH_SCHEMA["name"] = "research_run"` | `"deepresearch"` | ✅ 必改（LLM 工具名） |
| R2 | `deskpet/tools/research_tools.py:1721` | 注册名 `"research_run"` | `"deepresearch"` | ✅ 必改 |
| R3 | `deskpet/tools/research_tools.py:1716/1736` | `_register_research_tool` + 模块级调用 | `_register_deepresearch_tool` | 🟡 建议（一致性） |
| R4 | `deskpet/tools/research_tools.py:967` | `async def research_run(` | `async def deepresearch(`；文件末加 `research_run = deepresearch  # deprecated alias` | ✅ 必改 + BC 别名 |
| R5 | `deskpet/tools/research_tools.py:1574/1724` | `_handle_research_run` 定义(:1574) + 注册引用(:1724) | `_handle_deepresearch`。注：:1600 的 `await research_run(...)` 属 R4（入口调用点），若把 :1600 同步改 `deepresearch` 则别名仅服务外部 import | 🟡 建议 |
| R6 | `deskpet/tools/research_tools.py:1521-1528` | schema description「DeepResearch V8」 | 描述保留功能不变，名字口径与新工具名一致即可 | 🟡 |
| R7 | `deskpet/tools/code_tools/registration.py:128` | web_search 描述「请改用 research_run」 | 「请改用 deepresearch」 | ✅ 必改（否则 LLM 被引导喊旧名） |
| R8 | `deskpet/skills/builtin/deep-research/SKILL.md`（:42 标题 + :44/:45/:50/:115 正文） | `## 2. 调 research_run 工具` + 正文多处 `research_run(...)` | 全改 `deepresearch`；frontmatter `version: 0.2.0`→`0.3.0` | ✅ 必改 |
| **R12** | **`deskpet/skills/builtin/ppt-generate/SKILL.md:117`** | **下游 PPT 技能正文「先调 deep-research skill / `research_run` 工具」——LLM 可见、按名引导** | **改 `deepresearch`** | **✅ 必改**（Round1-C 挖出；性质同 R7/R8，否则"研究 X 再做 PPT"链路 LLM 被旧名引导） |
| R9 | `main.py:639 / :1069` | 注释里 `research_run` | 更新注释（仅文档） | 🟢 cosmetic |
| R10 | `tests/test_deskpet_research_tools.py`（`from ... import research_run` :43 + 40+ 调用） | 全是 import + 函数调用，**无字符串名/registry 断言**（已核） | 靠 R4 别名保持全绿；**新增 2 断言**（见 Phase 1 验收门） | ✅（加测） |
| R11 | `scripts/e2e_ppt_deepresearch.py:8/36/145` | docstring(:8) + import(:36) + 调用(:145)（:144 是 print 文案，非调用） | 靠别名可跑；建议同步改新名 | 🟡 |

### 2.1 必做的"漏网"核查（执行前 grep）
更名前**先跑下列 grep**（用目录递归，不用脆弱 glob），把任何按字符串名引用旧工具名的地方收口：
```bash
# 源码 + 技能 + agent(剔除 frozen dist*/ 与 venv)
grep -rn 'research_run' backend/deskpet/ --include='*.py' --include='*.md' --include='*.json'
# 工具筛选/危险名单是否按名引用(Round1-C 已核:不在 dangerous_tools_allowlist、category 不变)
grep -rn 'research_run\|dangerous_tools_allowlist' backend/deskpet/tools/registry.py
# 前端是否按工具名特判(Round1-C 已核:零命中,此处留痕)
grep -rn 'research_run\|deepresearch' tauri-app/src
```
**Round 1 已核实的边界（写进 plan 防执行者误判/误改）**：
- ✅ **无**按工具名的路由表 / tool_selector / 工具分区 / allowlist / SKILL.md `task_types`——工具筛选走 `permission_category`（research_run = `read_file`），**与名字解耦**，更名不动 category。
- ✅ 测试无字符串名/registry 断言；前端不按工具名特判；artifact type / 落盘路径不含工具名。
- ⚠️ **`[research]` 配置段名保持不变**！它是**领域配置段**（键 `site_directed/query_expansion/direct_sources/js_render/reranker/search_engines` 等，`_research_raw()` @research_tools.py:135 + `search_provider.py:65` 都读它），**不是工具名映射**。改成 `[deepresearch]` 会让所有 `[research]` 开关失配。**执行者不得连带改 config 段。**

凡 grep 命中的"字符串名引用"全部纳入手术面（R1–R12 已含已知项）。**不能只改 schema 名而漏改描述/技能里的旧名**（否则模型被旧名引导 → 调不到）。

---

## 3. Phase 0 — 质量基线 spike（🔴 决策前置门，先做）

> ✅ **已完成（2026-06-20）** — 报告见 [`01-baseline-spike-report.md`](./01-baseline-spike-report.md)。真 relay + gpt-5.5 + 精排 + evaluator 5 维 LLM-judge。
> **三道门判定**：基线质量 PASS（mcp-sec std=6.5/deep=6.9 ≥6）· cite-check 100% · deep 值得保留（增益+0.4/1.1×）。
> **🔴 决定性发现**：免费 Bing/DDG/百度（HTML SERP 抓取）持续负载下 IP 级封禁、分钟级不恢复（11/13 运行 0 来源）。
> **瓶颈归类**：检索层（① 搜索可靠性=第一瓶颈 ② grounding/evidence=第二瓶颈），**非综合层**。
> → 支持新增 §6.0 搜索可靠性改造（最高优先）+ §6 reranker（次优先）；**不支持 Phase 4 ReAct**（见 §10 按语）。
> 局限：搜索封禁致 clean-N 仅 2，质量基线 suggestive，但"瓶颈=检索"结论强（跨 90s 间隔重现）。

**目的**：补「零运行证据」盲区，拿到 Phase 3 取舍依据。**不通过不进 Phase 3。**

**评测底座（已确认可复用，见优化方案 Step 0）**：
- 质量评分：`plans/2026-06-13-deep-research-v8/v8-reference/reference/evaluator-prompt.md`（5 维加权 LLM-judge）。
- 主题集：`v8-reference/evals/routing-evals.json` 的 6 个 `should_trigger:true` + 补 1–2 财报类（触发中文一手源）。
- 记录：`v8-reference/scripts/emit_run_summary.py`。

**代码级做法**：
- 新建 `backend/scripts/spike_deepresearch_baseline.py`（独立脚本，**不进产线**）。
- **live LLM 注入必须照 `backend/scripts/e2e_stage_a_live.py` 的真链路**（⚠️ **不要**抄 `e2e_ppt_deepresearch.py`——那是 `FakeLLM` 罐头响应，抄它会跑成 mock，违背本门"真跑"要求）。具体三步（e2e_stage_a_live.py:36-114 实证）：
  1. `from config import load_config, resolve_cloud_api_key`；`config = load_config()`；`cloud_key = resolve_cloud_api_key()`。
  2. `from providers.openai_compatible import OpenAICompatibleProvider`；
     ⚠️ **先判空**（Round2-B：无 `[llm.cloud]` 段时 `config.llm.cloud` 为 None）：
     `assert cloud_key and config.llm.cloud is not None`，再
     `cloud = OpenAICompatibleProvider(base_url=config.llm.cloud.base_url, api_key=cloud_key, model=config.llm.cloud.model)`。
  3. 把 `cloud` 包成 `async def llm_call(prompt)->str`——**直接照抄 `research_tools.py:1705-1711` 的 `_call` 闭包**：
     `out = await cloud.chat_with_tools(messages=[{"role":"user","content":prompt}], tools=[], max_tokens=4096)`；`return (out or {}).get("content") or ""`。
     （⚠️ provider **没有**裸 `.chat()` 方法，只有 `chat_stream`/`chat_with_tools`；别望文生义。）传 `deepresearch(topic, llm_call=llm_call, ...)`。rerank/semantic 钩子同 main.py:641-1085（可选，spike 可先只注主 LLM）。
- 对每个 topic 跑 `standard` + `deep` 两档；计时（`time.perf_counter`）+ 抓 token（provider 返回 usage）+ 记 `coverage`/`errors`/源权威&多样性分布。
- 每份报告再调一次 LLM 当 judge，套 `evaluator-prompt.md` 评分卡 → 收 5 维加权分。
- 汇总 `plans/deepresearch-upgrade/01-baseline-spike-report.md`。
- 真机要求：按 `CLAUDE.md` §开发期登录测试账号走真实 relay key。

**验收门（可判定，带阈值）**：
- **基线达标**：标准档 5 维加权分**中位数 ≥ 6.0**（evaluator 的 PASS 线），且 cite-check 通过率 ≥ 90%（脚注不悬空）。
- **deep 档是否值**：判据 = `deep 相对 standard 的质量增益 ÷ 耗时倍数`。若**质量增益 < 10%（绝对分 <0.6）而耗时 > 2×** → 记「deep 默认不值，标 power-user 档」。
- **瓶颈归类**（决定 Phase 3 取舍）：若失败/低分主要源于「检索召回差/源质量低」→ 瓶颈在**检索**（→ Phase 3 reranker/fetch）；若源 OK 但报告综合差 → 瓶颈在**综合**（→ 改 synth prompt/分离 synthesis，非检索能力）。
- 报告须对上述三项各给**明确数值结论**，否则本门 FAIL、不进 Phase 3。

---

## 4. Phase 1 — 更名 `deepresearch` + 结构清理

**目的**：完成 §2 手术面，工具对 LLM 以 `deepresearch` 暴露，旧名平滑弃用。
**改动**：执行 R1–R11 + §2.1 漏网核查命中项。
**实现要点**：
- R4 的别名放法：`deepresearch` 为真身，文件末 `research_run = deepresearch`（标 `# deprecated: use deepresearch`）。
- registration（R2/R3/R5）：保持 `permission_category="read_file"`、超时 300s、其它注册参数不变，仅换名。
- main.py 注入钩子（`set_live_llm_call`/`set_rerank_llm_call`/`set_semantic_scorer`）**不受更名影响**（它们注的是模块级函数，不是工具名）——核查无按工具名注入即可。
**验收门**（断言可写死）：
- ① §7 单测全绿（R4 别名让现有 import 不破）。
- ② 新增 2 条精确断言：(a) `registry.get("deepresearch")` 非空；(b) `registry.get("research_run")` 为空（注册名已换）。另加模块别名断言 `research_tools.research_run is research_tools.deepresearch`（证 BC 别名在）。
- ③ 真机：桌宠对话喊「深度调研 X」→ 工具调用日志显示 `deepresearch`（不是旧名）。

---

## 5. Phase 2 — 修 recency 真 bug + 可观测性

### 5.1 修 recency（确定项，独立可验）
**根因**：`default_extract`（research_tools.py:826-831）返回字典无 `date` 键 → `_passage_from`（:1106-1108）`payload.get("date")` 恒空 → `score_recency` 恒返回 3.0。
**改法**（Round1-B 核实可行：trafilatura `extract_metadata().date` 输出已规范化为 `YYYY-MM-DD` 纯字符串，`score_recency` 的 `%Y-%m-%d` 档直接吃，无格式不兼容风险）：
- `default_extract` 已有 `meta = trafilatura.extract_metadata(html)`（:784）。在返回字典（:826-831）加一键 `"date": (getattr(meta, "date", None) or "")`。
- ⚠️ **渲染分支也要加**：JS 渲染兜底替换 `html = rendered`（:812）后，title 当前仍用渲染前的——同理 date 要在 :809-812 渲染成功块内**重新 `extract_metadata(rendered)` 取 date** 更新候选，否则渲染路径源 date 仍空。
- 注（Round2-B）：第三条 Jina 兜底路径（:815-820）返回无 date，`meta` 保持渲染前值 → 该路径 date 可能为空，**属预期**（score_recency 坏值/空值安全返 3.0，不报错）。
- `_passage_from` 无需改（:1106-1108 已读 `payload.get("date")`）。
- 注：R8 等列的 SKILL.md 行号可能随别处改动**漂移**——执行时**以 §2.1 grep 结果为准**，不死盯表里行号。
**验收门**：①新增单测：mock extract 返回带 `date` 的 payload → 断言该 passage 的 `dims["recency"]` ≠ 3.0 且随日期/velocity 变化；②Phase 0 spike 修前/修后对照：top 源排序确有变化（证明维度激活）。

### 5.2 可观测性
**目的**：让 spike 与未来回归能量化各阶段。

> ⚠️ **Round1-B 纠错（原 v1 事实性错误）**：项目的 metrics 通道是
> `backend/observability/metrics_sink.py`，但它有**封闭双白名单**——`VALID_EVENTS`（:48-82）
> 限定事件名、`_ALLOWED_DETAIL_KEYS`（:90-124）限定 detail key，**不在名单的事件/字段被静默丢弃**
> （:203 附近 `log.debug("dropped unknown event")`）。research 相关事件名和 `rerank_used`/
> `n_dropped_by_reason`/`rounds`/`velocity`/`elapsed_ms_per_stage` 等 key **均不在名单**。
> 所以**不能"直接复用"**——必须二选一：

**Phase 2 默认走 A（自包含、零隐私墙风险）**：
- **A — 扩 `ResearchReport.coverage` 字段**（`coverage` 已是 `dict[str,Any]` @:573，加键**不需改 dataclass**；下游 PPT/前端不读 coverage，无契约破坏）。**本 Phase 采用此项。**
  - ⚠️ Round2 提醒：`rounds`(:1342)/`reranker`即rerank_used(:1343)/`topic_velocity`即velocity(:1337) **已在 coverage**，别重复加/改名。
  - **真正要新加**的只有：`route` / `mode` / `n_dropped_by_reason{ai_generated,low_quality,mojibake,too_short}` / `elapsed_ms_per_stage`。
  - 实现工作量提示：`n_dropped_by_reason` 需在 `_passage_from` 各 `errors.append("dropped_*")` 处（:1089/1098/1102）**同步累加一个计数器**（当前只进 errors 列表）；`elapsed_ms_per_stage` 需在 orchestrator 各阶段**新埋 `time.perf_counter()` 分段点**（当前无分段计时）。
- **B（可选，跨会话指标才需要）— 扩白名单后再 emit**：在 `metrics_sink.py` 的 `VALID_EVENTS` 加
  `"deepresearch_run"`，`_ALLOWED_DETAIL_KEYS` 加 `rerank_used`/`n_dropped`/`rounds`/`velocity`/`elapsed_ms`
  （都是枚举/数字/短串，满足隐私墙），再在 deepresearch 末尾 `record("deepresearch_run", {...})`。
  **非 Phase 2 必需，列为后续。**

**验收门**：跑一次能从 `ResearchReport.coverage` 读到各阶段耗时 + drop 分类计数 + rerank_used（走 A）。

---

## 6. Phase 3 — 缺口能力补齐（spike-gated，按 Phase 0 数据取舍）

### 6.0 🔴 新增（Phase 0 spike 实测后置入，**最高优先**）— 搜索可靠性改造

> 背景：Phase 0 spike（[`01-baseline-spike-report.md`](./01-baseline-spike-report.md)）实测——免费 Bing/DDG/百度**全是 HTML SERP 抓取**（`search_provider.py` `_engine_request`:76 / `_engine_parse`:88），持续负载下被 IP 级封禁、分钟级不恢复（11/13 运行 `no search results`）。瓶颈=检索可靠性。**不再把裸 SERP 抓取当主力。** 沿用硬约束：**不接付费搜索 API**。
> 用户指令（2026-06-20）：弃用 Bing/DDG 当主力，换免费替代。下列四路线 A+B 为主、C/D 辅。

**A — 直连权威源 API 扩展（骨干，bypass SERP，顺带治第二瓶颈 grounding）**
- 接入点：`research_sources.py` 的 `direct_source_for()`(:66) 意图路由 + 新 async fetcher（照 `cninfo_search`:227 / `openstd_search` 模式）；在 `research_tools.py` §4.4 direct-source 块（`deepresearch` :1137 附近）挂上。
- 新增源（全免费官方 API、无 key、不被 IP 封）：
  - `wikipedia_search`：MediaWiki opensearch + REST summary（`zh/en.wikipedia.org/w/api.php`）— 综述/背景意图
  - `arxiv_search`：arXiv API（`export.arxiv.org/api/query`）— 学术/论文/技术选型意图
  - `semantic_scholar_search`：Semantic Scholar Graph API（`api.semanticscholar.org`，无 key 限速档）— 技术/论文
  - `wikidata_search`：Wikidata REST/SPARQL — 结构化事实
- `direct_source_for` 扩展为可返回**多个**源（财报→cninfo、学术→arxiv+s2、通用→wikipedia）；意图关键词表照现有 cninfo/openstd 模式加。命中域名天然 TIER_1（打分已支持）。
- 验收：每个 fetcher 单测（mock HTTP）+ 真机命中（报告引用出现 wikipedia.org/arxiv.org 域名）。

**B — 浏览器渲染搜索（通用网搜兜底，绕 HTTP 层封禁）**
- 复用 `research_cdp_edge.py`（系统 Edge 无头 via CDP，`cdp_edge_available()`:73，渲染 URL→`outerHTML`）。
- 新增 search_provider 引擎 `"bing-cdp"`：在 `_KNOWN_ENGINES`(:49) 注册；`_engine_request`(:76) 对该引擎改走"用 cdp-edge 导航 SERP URL 取 outerHTML"（不是 httpx）；`_engine_parse`(:88) 复用现有 `parse_bing_html`(:183)。真浏览器带 cookie/JS/指纹 → 远难被封。
- 配置：`[research].search_engines = ["wikipedia","bing-cdp","bing",...]`（直连源+浏览器搜索优先，裸 SERP 抓取降为最后兜底）。复用 JS 渲染的 4 次/run 预算 + 超时降级。
- 验收：单测（mock cdp render）+ 真机（**连续 5 次研究不再 0 来源** —— 直接复测 spike 暴露的封禁场景）。

**C — SearXNG 自托管引擎（power-user opt-in，Docker）**
- 新增 search_provider 引擎 `"searxng"`：HTTP GET 本地 SearXNG `[research].searxng_url`（如 `http://127.0.0.1:8888/search?q=...&format=json`），解析 JSON。默认关、用户配 url 才入队列。
- 验收：配 url 后真实例/mock 返回解析正确。

**D — 现有 SERP 抓取硬化（defense-in-depth）**
- `search_provider` 加：真实浏览器 headers + 轮换 UA；引擎被封后**冷却**（失败计数→该引擎冷却 N 分钟跳过，进程内状态）；**结果缓存**（同 query 短期 TTL 复用）；退避重试。
- 验收：单测覆盖冷却/缓存/退避逻辑。

**Phase 3 优先级（spike 后重排，新增此排序，不删原项）**：
`3.0-A 直连源 + 3.0-B 浏览器搜索`（最高，治第一瓶颈搜索可靠性）> `3.0-C/D`（韧性辅助）> 下方原 §6 三项（治第二瓶颈 grounding / 平台覆盖，降为次优先；其中"crawl4ai webview"与 3.0-B 同源可合流实现）。

### 6.0.1 实现契约细则（Round-1 子代理挑战后**追加**，只增不删上文）

> 上文 A/B/C 的"复用现有函数"隐含了返回类型/调用契约的破坏性变更。下列把契约改动 + 现有消费方/单测的连带修改写死，照此执行不撞契约。

**契约-1（A 的 `direct_source_for` 多源化）**：现状 `research_sources.py:66 direct_source_for(text)->Optional[str]`，唯一消费点 `research_tools.py:1192` 是 `if src=="cninfo"...else openstd` **字符串二分支**，且 `tests/test_research_sources.py:14-21` 有 6 条 `=="cninfo"/=="openstd"/is None` 硬断言。
- 改法：`direct_source_for` 改返回 `list[str]`（无命中→`[]`）；direct-source 块（**真实行号 :1185-1227——上文 A 写的 :1137 有误，以此为准**）把二分支改 `for src in srcs:` + dispatch 映射 `_FETCHER_MAP={"cninfo":cninfo_search,"openstd":openstd_search,"wikipedia":wikipedia_search,"arxiv":arxiv_search,"semantic_scholar":semantic_scholar_search}`，cninfo 的"空→edgar 兜底"在该源分支内保留；`test_research_sources.py` 6 条断言改成员检查（`"cninfo" in direct_source_for(...)`）。**§7 测试矩阵追加：既有 test_research_sources.py 同步改并通过。**

**契约-2（A 新 fetcher 返回结构=硬契约）**：每条必须 `{"ok":True,"url":非空,"title":str(≤200),"text":非空摘要正文(≤18000),"fetched_at":float,"source":"wikipedia/arxiv/..."}`。`text` 空会被 `research_tools.py:1208 if not d_url or not d_text: continue` **静默丢弃**——wikipedia summary/arxiv abstract 必须填进 `text`。

**契约-3（B 浏览器搜索撞 `_engine_request` 三元组契约）**：`_engine_request`(:76) 返回 `(method,url,kw)`；`search`(:244) 与 `search_async`(:285) 两个消费者都 `method,url,kw=req`→`cli.request(...)`→`_engine_parse(engine,resp.text,n)`（chat+code web_search 共用，blast radius 大）。
- **不能**让 `bing-cdp` 的 `_engine_request` 返字符串（`ValueError` 解包崩）。正确：在 `search_async` 主循环(:305-318)加前置分支 `if engine.endswith("-cdp"): serp_url=_BING_URL+"?"+urlencode({"q":q,...}); html=await research_cdp_edge.cdp_edge_render(serp_url, timeout=_js_render_timeout()); results=parse_bing_html(html or "", max_results=n)`，**绕开** `_engine_request`/`cli.request`。同步 `search`(:244) 无 async → `bing-cdp` 仅在 `search_async` 生效，deepresearch 走 async 够用。

**契约-4（B 渲染函数真实接口）**：`research_cdp_edge.cdp_edge_render(url:str,*,timeout:float=20.0)->Optional[str]`（**:391，async，失败返 None 需降级到队列下一引擎，绝不抛**），内部已有 `_render_semaphore=2` + 常驻 Edge 复用，**勿自起 Edge**。⚠️ 上文"复用 JS 渲染 4 次/run 预算"无法直接落地：计数器 `_js_render_run_count`(research_tools.py:204) 在 **fetch 阶段**累加，search 阶段够不到 → 搜索阶段 CDP 预算需 **search_provider 模块级独立计数器**（每 research 内 `bing-cdp` 最多 K 次）。

**契约-5（C SearXNG 是 JSON 非 HTML）**：现 `_engine_parse`(:88) 三分支全解析 HTML、`search_async` 喂 `resp.text`。searxng 需 `resp.json()` + 新 `_parse_searxng_json()`（与 B 同属"队列循环按引擎分流解析"，建议 B/C 合并为一次 `search_async` 引擎分派改造）。

**契约-6（D 冷却/缓存=模块级共享 state）**：挂 search_provider 模块级 dict，`search` 与 `search_async` 两入口共用同一读写点（否则同步路径绕过冷却）。

**API 风险（实现时注意，全 best-effort 失败降级返 `[]`）**：
- **Semantic Scholar**：匿名限速约 100 req/5min，持续负载易 429 → 加 `Retry-After` + 静默降级（照 edgar_search 模式）。
- **大陆可达性**：zh.wikipedia.org / export.arxiv.org / wikidata.org 可能被墙 → 双试 `for trust_env in (False, True)`（照 edgar_search 直连+代理双试）。
- **Wikidata** 返结构化 triples，需序列化成自然语言 `text`，复杂度高，放最后做。

> 以下为原 Phase 3 三项（保留不变，按上面排序降为次优先）：

仅做 Phase 0 证明「确实是瓶颈」的项：
- **本地 bge-reranker 可选档**（research_tools.py:270-282 当前 `_rerank_mode()` 把 `local` 退化为 `llm`；`FlagEmbedding` 已在 `.venv`，`from FlagEmbedding import FlagReranker` 可用）：让 `local` 真生效。⚠️ **接入点（Round2-B 纠正）**：**不要**在 `_llm_rerank`（:331，纯 LLM 路径函数）内部加分支；正确做法是**新建 `_bge_local_rerank()` 函数**（:330 附近），并在**调用决策点 :1247** 加 `elif _rerank_mode()=="local": applied = await _bge_local_rerank(...)`，实例化 `FlagReranker("BAAI/bge-reranker-v2-m3")` 做精排，复用 `_RERANK_TIMEOUT=25.0` 超时降级。
  ⚠️ **Round1-B 坑**：首次触发会从 HuggingFace Hub **下载数百 MB 模型**（5-15 分钟），期间必然超 25s → 静默降级回 llm → `local` 看似"正常"实则永不生效。**必须**：① 加启动时/首次预检——检测 `HF_HOME` 本地缓存是否已有该模型，无则提示预下载（不在 research 热路径里同步下载）；② 文档化缓存路径配置。
- **loopback/ollama 用户精排**（main.py:652 仅非 loopback 注入 rerank）：让本地模型也能走精排（或走上面的本地 bge），消除「本地用户质量无声下降」。
- **crawl4ai webview 反向链路**（`plans/2026-06-16-crawl4ai-fetch-tier/` 的 WI-0 GATE）：补 JS 渲染在非 Windows 的覆盖。
**验收门**：每项带单测 + spike 对照（开/关该能力的质量分差）；本地 bge 项额外验「冷启动不阻塞 research 热路径」。

---

## 7. 测试矩阵（每 Phase 必跑）

1. **单元**：`backend/.venv/Scripts/python.exe -m pytest tests/test_deskpet_research_tools.py -v`（更名后全绿 + 新增断言测）。
2. **live smoke**：`scripts/e2e_ppt_deepresearch.py`（真链路出 .pptx，证明更名未断下游 PPT 集成）。
3. **真机 E2E**（按 `CLAUDE.md` 手工测试纪律，windows-mcp 或 computer-use）：桌宠对话「帮我深度调研 X」→ 日志确认调 `deepresearch` → ArtifactCard 渲染报告 → 截图存 `plans/manual-results-<date>/screenshots/`。
> ⚠️ 真机 E2E 不可用 WebSocket/pytest/import 替代（feedback_real_e2e_not_script_replay）。
4. **（§6.0 专属，追加）既有测试同步**：`direct_source_for` 多源化后 `tests/test_research_sources.py`（:14-21 字符串等值断言）必须同步改为成员检查并通过；`search_provider` 加 `bing-cdp`/`searxng`/冷却缓存后既有 search_provider 单测不得回归红。§6.0-B 真机验收 = **连续 5 次研究不再 0 来源**（直接复测 Phase 0 暴露的封禁场景）。

---

## 8. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 更名漏改路由/描述 → LLM 调不到 | §2.1 强制 grep 核查 + 真机 E2E 验证实际调用名 |
| 别名期残留旧名调用 | `research_run` 别名保留至 Phase 3 结束（与 §1.3 一致），下一轮升级再删 |
| recency 修复改变排序 → 回归 | 单测锁定 + spike 修前后对照人工确认是改善非劣化 |
| Phase 3 本地 bge 占用单机资源 | 默认 `reranker=llm` 不变；local 为 opt-in + 超时降级 |

**回滚**：各 Phase 独立提交；更名 Phase 出问题 → revert 该 commit（别名保证旧名仍可用）。

---

## 9. 范围 / 非目标
- **不删 `research_run` 实现**（只更名 + 别名弃用）。
- **不接付费搜索 API**（沿用 v8/ROADMAP 硬约束）。
- **默认不做 ReAct 子代理**（Phase 4 延后，需用户 + Phase 0 数据双确认）。

## 10. Phase 4（延后/可选）— ReAct 子代理
仅当 Phase 0 显示瓶颈在「流程僵化、需自主多轮探索」且用户确认愿担回归风险时启动：重写 `agent/subagents/`（受限只读工具子集 + 三重预算闸 + 分离 synthesis + EvidenceLog）+ 接线 main.py + tool 暴露 + 把现有打分/一手源/抓取层嫁接进子代理工具 + 测试 + 真机 E2E。**本 plan 不展开。**

> 📌 **Phase 0 数据结论（2026-06-20 追加）**：spike **不支持**现在做 Phase 4。瓶颈实测在**检索层**（搜索可靠性 + grounding），而**综合/连贯/校准维度已达标**（有效运行 7-8 分），控制流不是瓶颈。Phase 4 的触发条件（「瓶颈在流程僵化」）**未满足** → 维持延后，除非用户在看过 spike 后仍明确要做。本结论不删除 Phase 4，仅记录"当前数据不启动"。

## 11. 未决问题
1. 架构决策（§1.2）默认「演进+更名」，用户是否接受 / 要不要直接上 ReAct？
2. 模块文件是否一并改名 `research_tools.py`→`deepresearch_tools.py`（牵动 import）？默认不改。
3. Phase 3 三项的优先级由 Phase 0 数据定。→ ✅ **已定（2026-06-20，见 §3 横幅 + §6.0）**：搜索可靠性（§6.0-A/B）> 韧性辅助（§6.0-C/D）> 原 §6 三项 grounding 精排；Phase 4 当前不启动。
4. （新增）§6.0 四路线里，C（SearXNG）需 Docker 不适合普通用户、D（硬化）只是缓兵——A（直连源）+ B（浏览器搜索）作为主推是否认可？第三方源（Marginalia/Brave 免费档）要不要纳入由用户定。

## 执行顺序
Phase 0（spike，门）→ Phase 1（更名）→ Phase 2（recency + 可观测）→〔门：spike 数据〕→ Phase 3（按需）→〔可选〕Phase 4。

> 📌 **进度追加（2026-06-20）**：Phase 0 ✅ / Phase 1 ✅ / Phase 2 ✅ 已完成。spike 数据门已过，**Phase 3 从新增的 §6.0 搜索可靠性改造起步**（3.0-A 直连源 + 3.0-B 浏览器搜索最高优先），原 §6 三项次之；Phase 4 当前不启动。
