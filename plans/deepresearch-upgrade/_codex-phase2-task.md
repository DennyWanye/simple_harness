# CODEX 任务：Phase 2 — 修 recency 真 bug + 可观测性(coverage 字段)

你在 DeskPet 仓库（G:/projects/deskpet）。只做 Phase 2。先读 `plans/deepresearch-upgrade/00-upgrade-plan.md` 的 §5（5.1 + 5.2），严格照它。所有改动在 `backend/deskpet/tools/research_tools.py`。

## 5.1 修 recency 真 bug（确定项）
**根因**：`default_extract`（约 :751-831）返回字典无 `date` 键 → `_passage_from`（:1106-1108）`payload.get("date")` 恒空 → `score_recency` 恒返回 3.0，新鲜度维度失效。
**改法**（trafilatura `extract_metadata().date` 输出已是 `YYYY-MM-DD` 纯字符串，`score_recency` 的 `%Y-%m-%d` 档直接吃；坏值/空值它安全返 3.0）：
1. 主路径：`default_extract` 已有 `meta = trafilatura.extract_metadata(html)`（约 :784）。在返回字典（约 :826-831）加一键 `"date": (getattr(meta, "date", None) or "")`。
2. JS 渲染分支：在渲染成功块（约 :809-812，`html = rendered` 处）内**重新 `extract_metadata(rendered)` 取 date** 更新 date 候选，否则渲染路径源 date 仍空。
3. Jina 兜底路径（约 :815-820）无 date 属预期，不用强加（保持空，score_recency 安全返 3.0）。
4. `_passage_from` 无需改（已读 `payload.get("date")`）。

## 5.2 可观测性（走方案 A：扩 ResearchReport.coverage 字段，不动 metrics_sink）
`coverage` 是 dict（`ResearchReport` 约 :567-573），在 orchestrator 末尾构造（约 :1329-1344）。
**注意**：`rounds`(:1342)/`reranker`即rerank_used(:1343)/`topic_velocity`即velocity(:1337) **已在 coverage**，别重复加/改名。
**真正要新增**这几个 key 到 coverage：
- `route`：本次走的搜索引擎/路径标识（如 "ddg" 或现有 search_provider 暴露的标识；没有就填 "ddg"）。
- `mode`：depth 档（light/standard/deep）——其实就是入参，回填进 coverage。
- `n_dropped_by_reason`：dict `{"ai_generated": N, "low_quality": N, "mojibake": N, "too_short": N}`。**实现**：在 `_passage_from` 各 drop 分支（`errors.append("dropped_low_quality"...)` :1089、`dropped_ai_generated` :1098、`dropped_mojibake` :1102，以及长度门 :1092-1093 的 too_short）**同步累加一个计数器**（用闭包外的 dict 或 nonlocal）。
- `elapsed_ms_per_stage`：dict，各阶段耗时毫秒。**实现**：在 orchestrator 各阶段（plan / search / fetch / score / synth）前后埋 `time.perf_counter()`，算差填入。

## 验收（必须达到）
1. **新增单测**到 `backend/tests/test_deskpet_research_tools.py`：
   - recency：mock extract 返回带 `date`（如近期日期 vs 多年前日期）的 payload → 断言对应 passage 的 `dims["recency"]` **≠ 3.0** 且随日期不同而不同（近期分更高）。
   - coverage：跑一次 deepresearch（用现有 FakeLLM/mock 模式）→ 断言 `report.coverage` 含 `route`/`mode`/`n_dropped_by_reason`/`elapsed_ms_per_stage` 四个新键，且 `n_dropped_by_reason` 在有被剔除源时计数>0。
2. `cd backend && .venv/Scripts/python.exe -m pytest tests/test_deskpet_research_tools.py -q` 全绿（原 76 + 新增，不许有 regression）。
3. 不破坏现有 coverage 字段；不动 metrics_sink.py；不动 `[research]` 配置段。

## 完成后
输出：改了哪些函数/行、新增测试名、pytest passed 数。不要 commit。
