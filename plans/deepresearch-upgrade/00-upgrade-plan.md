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

**目的**：补「零运行证据」盲区，拿到 Phase 3 取舍依据。**不通过不进 Phase 3。**

**评测底座（已确认可复用，见优化方案 Step 0）**：
- 质量评分：`plans/2026-06-13-deep-research-v8/v8-reference/reference/evaluator-prompt.md`（5 维加权 LLM-judge）。
- 主题集：`v8-reference/evals/routing-evals.json` 的 6 个 `should_trigger:true` + 补 1–2 财报类（触发中文一手源）。
- 记录：`v8-reference/scripts/emit_run_summary.py`。

**代码级做法**：
- 新建 `backend/scripts/spike_deepresearch_baseline.py`（独立脚本，**不进产线**）。
- **live LLM 注入必须照 `backend/scripts/e2e_stage_a_live.py` 的真链路**（⚠️ **不要**抄 `e2e_ppt_deepresearch.py`——那是 `FakeLLM` 罐头响应，抄它会跑成 mock，违背本门"真跑"要求）。具体三步（e2e_stage_a_live.py:36-114 实证）：
  1. `from config import load_config, resolve_cloud_api_key`；`config = load_config()`。
  2. `from providers.openai_compatible import OpenAICompatibleProvider`；
     `cloud = OpenAICompatibleProvider(base_url=config.llm.cloud.base_url, api_key=resolve_cloud_api_key(), model=config.llm.cloud.model)`。
  3. 把 `cloud` 包成 `async def llm_call(prompt)->str`（调 provider 的 chat 接口取文本），传 `deepresearch(topic, llm_call=llm_call, ...)`。rerank/semantic 钩子同 main.py:641-1085 的注入方式（可选，spike 可先只注主 LLM）。
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
- ⚠️ **两处分支都要加**：JS 渲染兜底替换 `html = rendered`（:812）后，title 当前仍用渲染前的——同理 date 也要在渲染后**重新 `extract_metadata(rendered)` 取 date**，否则渲染路径的源 date 仍空。具体：在 :809-812 渲染成功块内补一次 `meta` 重取并更新 date 候选。
- `_passage_from` 无需改（:1106-1108 已读 `payload.get("date")`）。
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
- **A — 扩 `ResearchReport.coverage` 字段**（不动 metrics_sink）：在 coverage 加
  `route/mode/n_dropped_by_reason{ai_generated,low_quality,mojibake,too_short}/rerank_used/velocity/rounds/elapsed_ms_per_stage`。
  纯返回值，单测可断言，spike 直接读。**本 Phase 采用此项。**
- **B（可选，跨会话指标才需要）— 扩白名单后再 emit**：在 `metrics_sink.py` 的 `VALID_EVENTS` 加
  `"deepresearch_run"`，`_ALLOWED_DETAIL_KEYS` 加 `rerank_used`/`n_dropped`/`rounds`/`velocity`/`elapsed_ms`
  （都是枚举/数字/短串，满足隐私墙），再在 deepresearch 末尾 `record("deepresearch_run", {...})`。
  **非 Phase 2 必需，列为后续。**

**验收门**：跑一次能从 `ResearchReport.coverage` 读到各阶段耗时 + drop 分类计数 + rerank_used（走 A）。

---

## 6. Phase 3 — 缺口能力补齐（spike-gated，按 Phase 0 数据取舍）

仅做 Phase 0 证明「确实是瓶颈」的项：
- **本地 bge-reranker 可选档**（research_tools.py:270-282 当前 `_rerank_mode()` 把 `local` 退化为 `llm`；`FlagEmbedding` 已在 `.venv`，有 `FlagReranker`/`FlagAutoReranker`）：让 `local` 真生效（在精排函数 ~:330 加 `mode=="local"` 分支实例化 `FlagReranker("BAAI/bge-reranker-v2-m3")`），复用 `_RERANK_TIMEOUT=25.0` 加超时降级。
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

## 11. 未决问题
1. 架构决策（§1.2）默认「演进+更名」，用户是否接受 / 要不要直接上 ReAct？
2. 模块文件是否一并改名 `research_tools.py`→`deepresearch_tools.py`（牵动 import）？默认不改。
3. Phase 3 三项的优先级由 Phase 0 数据定。

## 执行顺序
Phase 0（spike，门）→ Phase 1（更名）→ Phase 2（recency + 可观测）→〔门：spike 数据〕→ Phase 3（按需）→〔可选〕Phase 4。
