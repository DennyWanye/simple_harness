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
- **Python 入口函数**：`research_run()` → `deepresearch()`，保留 `research_run = deepresearch` 弃用别名（让现有 tests/scripts/import 不破，标注 Phase 1 末或下一轮删）。
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
| R5 | `deskpet/tools/research_tools.py:1574/1600/1724` | `_handle_research_run` 定义+调用+注册引用 | `_handle_deepresearch` | 🟡 建议 |
| R6 | `deskpet/tools/research_tools.py:1521-1528` | schema description「DeepResearch V8」 | 描述保留功能不变，名字口径与新工具名一致即可 | 🟡 |
| R7 | `deskpet/tools/code_tools/registration.py:128` | web_search 描述「请改用 research_run」 | 「请改用 deepresearch」 | ✅ 必改（否则 LLM 被引导喊旧名） |
| R8 | `deskpet/skills/builtin/deep-research/SKILL.md` | `## 2. 调 research_run 工具` + 正文多处 `research_run(...)` | 全改 `deepresearch`；frontmatter `version: 0.2.0`→`0.3.0` | ✅ 必改 |
| R9 | `main.py:639 / :1069` | 注释里 `research_run` | 更新注释（仅文档） | 🟢 cosmetic |
| R10 | `tests/test_deskpet_research_tools.py`（多处） | `from ... import research_run` + 调用 | 靠 R4 别名保持通过；**新增 1 测**断言工具以 `"deepresearch"` 注册 | ✅（加测） |
| R11 | `scripts/e2e_ppt_deepresearch.py:36/144/145` | import + 调用 `research_run` | 靠别名可跑；建议同步改新名 | 🟡 |

### 2.1 必做的"漏网"核查（执行前 grep）
更名前**先跑下列 grep**，把任何按字符串名引用旧工具名的地方一并收口（防 LLM 路由/技能门/工具分区按名 allowlist 漏改）：
```bash
grep -rn '"research_run"\|research_run' backend/deskpet --include=*.py | grep -v research_tools.py
grep -rn 'research_run' backend/deskpet/skills backend/deskpet/agent --include=*.md --include=*.py
# 工具分区 / task_types / 路由配置里是否按名引用
grep -rn 'research_run' backend/deskpet/tools/*selector* backend/deskpet/skills/**/SKILL.md
```
凡命中的"字符串名引用"全部纳入手术面。**不能只改 schema 名而漏改路由/描述里的旧名**（否则模型被旧名引导 → 调不到）。

---

## 3. Phase 0 — 质量基线 spike（🔴 决策前置门，先做）

**目的**：补「零运行证据」盲区，拿到 Phase 3 取舍依据。**不通过不进 Phase 3。**

**评测底座（已确认可复用，见优化方案 Step 0）**：
- 质量评分：`plans/2026-06-13-deep-research-v8/v8-reference/reference/evaluator-prompt.md`（5 维加权 LLM-judge）。
- 主题集：`v8-reference/evals/routing-evals.json` 的 6 个 `should_trigger:true` + 补 1–2 财报类（触发中文一手源）。
- 记录：`v8-reference/scripts/emit_run_summary.py`。

**代码级做法**：
- 新建 `backend/scripts/spike_deepresearch_baseline.py`（独立脚本，**不进产线**）：
  1. 读主题集 → 对每个 topic 跑 `deepresearch(topic, depth in {standard, deep})`，注入真 live LLM（复用 main.py 的注入逻辑或 `scripts/e2e_ppt_deepresearch.py` 的链路搭法）。
  2. 计时 + 抓 token（从 relay 返回 usage 或估算）+ 记 `coverage`/`errors`/源分布。
  3. 把每份报告丢给 evaluator-prompt 评分卡（再调一次 LLM 当 judge）→ 收 5 维分。
  4. 汇总成 `plans/deepresearch-upgrade/01-baseline-spike-report.md`。
- 真机要求：按 `CLAUDE.md` §开发期登录测试账号走真实 relay key；这是真跑，不是 mock。
**验收门**：spike 报告回答「质量到底如何 / 瓶颈在检索还是综合 / deep 档值不值成本 / 哪些 Phase 3 能力真的缺」。

---

## 4. Phase 1 — 更名 `deepresearch` + 结构清理

**目的**：完成 §2 手术面，工具对 LLM 以 `deepresearch` 暴露，旧名平滑弃用。
**改动**：执行 R1–R11 + §2.1 漏网核查命中项。
**实现要点**：
- R4 的别名放法：`deepresearch` 为真身，文件末 `research_run = deepresearch`（标 `# deprecated: use deepresearch`）。
- registration（R2/R3/R5）：保持 `permission_category="read_file"`、超时 300s、其它注册参数不变，仅换名。
- main.py 注入钩子（`set_live_llm_call`/`set_rerank_llm_call`/`set_semantic_scorer`）**不受更名影响**（它们注的是模块级函数，不是工具名）——核查无按工具名注入即可。
**验收门**：①§7 单测全绿（别名生效）②新增测断言 registry 里存在 `deepresearch`、不存在 `research_run`（或仅别名）③真机：桌宠对话喊「深度调研 X」→ 工具调用日志显示 `deepresearch`（不是旧名）。

---

## 5. Phase 2 — 修 recency 真 bug + 可观测性

### 5.1 修 recency（确定项，独立可验）
**根因**：`default_extract`（research_tools.py:826-831）返回字典无 `date` 键 → `_passage_from`（:1106-1108）`payload.get("date")` 恒空 → `score_recency` 恒返回 3.0。
**改法**：
- `default_extract` 已有 `meta = trafilatura.extract_metadata(html)`（:784）。在返回字典加 `"date": (getattr(meta, "date", None) or "")`（trafilatura metadata 自带 `date`，ISO 串）。
- JS 渲染兜底分支若替换了 html（:812），同样从渲染后 html 的 metadata 取 date。
- `_passage_from` 无需改（已读 `payload.get("date")`）。
**验收门**：①新增单测：mock extract 返回带 `date` 的 payload → 断言该 passage 的 `dims["recency"]` ≠ 3.0 且随日期变化；②Phase 0 spike 修前/修后对照：top 源排序确有变化（证明维度激活）。

### 5.2 可观测性事件
**目的**：让 spike 与未来回归能量化各阶段。
**改法**：在 `deepresearch` 各阶段（plan/search/fetch/score/rerank/synth/cite_check）emit 结构化事件（复用项目现有 event/metrics 通道；若无，先在 `ResearchReport.coverage` 扩展字段：`route/mode/n_dropped_by_reason/rerank_used/velocity/rounds/elapsed_ms_per_stage`）。
**验收门**：跑一次能从 coverage/日志看到各阶段耗时 + drop 分类计数 + rerank_used。

---

## 6. Phase 3 — 缺口能力补齐（spike-gated，按 Phase 0 数据取舍）

仅做 Phase 0 证明「确实是瓶颈」的项：
- **本地 bge-reranker 可选档**（research_tools.py:270-282 当前 `local` 退化为 `llm`）：接 `FlagEmbedding` 本地 bge-reranker-v2-m3，`[research].reranker="local"` 真生效；给 CPU 单机加超时/降级。
- **loopback/ollama 用户精排**（main.py:652 仅非 loopback 注入 rerank）：让本地模型也能走精排（或本地 bge），消除「本地用户质量无声下降」。
- **crawl4ai webview 反向链路**（`plans/2026-06-16-crawl4ai-fetch-tier/` 的 WI-0 GATE）：补 JS 渲染在非 Windows 的覆盖。
**验收门**：每项带单测 + spike 对照（开/关该能力的质量分差）。

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
| 别名期残留旧名调用 | 保留 `research_run` 别名一个 Phase，期间日志告警，下轮删 |
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
