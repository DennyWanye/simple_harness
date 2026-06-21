# 任务：新建 deepresearch fan-out 单测文件 TG-1~5（Phase 2 = WI-7）

为已实现的 fan-out 核心写 `backend/tests/test_deepresearch_subagent_fanout.py`，覆盖 plan §10.1 的 TG-1~5。**只新建这一个测试文件**，不改生产代码。

## 先读
- plan §10.1 测试组：`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`
- 已实现的真实函数签名/行为：`G:/projects/deskpet/backend/deskpet/tools/research_tools.py`
  - `deepresearch(topic,*,llm_call,search,extract,...,scheduler=None,parent_sid="default",_depth=0,skip_plan=False)`
  - `_run_subagent_fanout(...)`、`_fanout_synthesize(...)`、`_merge_subreport_citations(sub_reports)`、`_strip_footnote_definitions(md)`、`_rewrite_local_refs(md,map)`、`_finalize_report_md(report_md,citations,errors)->(md,cits,cc)`、`_norm_url(u)`、`_fanout_enabled/_fanout_min_subquestions/_fanout_max_subquestions/_fanout_subrun_mode/_fanout_concurrency`、`set_subagent_scheduler/get_subagent_scheduler`、常量 `_DEEPRESEARCH_TOOL_TIMEOUT/_FANOUT_OUTER_RESERVE/_MIN_SUBRUN`
  - `ResearchReport`/`Citation` 字段、`_DEPTH_PRESETS`
- 参考现有测试风格：`G:/projects/deskpet/backend/tests/test_deskpet_research_tools.py`（怎么 mock llm_call/search/extract）、`test_deepresearch_output_dir.py`。

## 测试组（TG-1~5）
- **TG-1 BC 分叉**：构造 mock llm_call/search/extract，分别用 `scheduler=None` / `_depth=1` / 只1个子问题 / `_fanout_enabled` monkeypatch False —— 断言**走扁平路径**（不调用 `_run_subagent_fanout`，可 monkeypatch `_run_subagent_fanout` 设哨兵断言它没被调用），产出与扁平一致。
- **TG-2 fanout ON 核心**：
  - mock scheduler：一个对象，`async def run(self,*,kind,run_id,task_id,parent_sid,coro_factory): return await coro_factory()`（直接透传）。
  - monkeypatch 内层 `deepresearch`：用 monkeypatch 让 `_run_subagent_fanout` 里调用的 `deepresearch` 返回桩 `ResearchReport`（每个子问题不同 citations）；**断言内层收到 `scheduler=None` 且 `_depth=1`**（用 wrapper 记录入参）。
  - 断言：N 份子报告合并、`coverage["mode"]=="fanout"`、`coverage["subagent_fanout"]` 含 n_subagents/n_completed/waves、summary 非空、citations 全局唯一。
  - **失败隔离**：让其中 1 个子问题的内层抛异常 → 其余仍合并、n_failed=1、errors 有记录。
  - **全失败兜底**：全部子问题内层抛异常 → 返回 no_results 模板 ResearchReport（summary 字段在、不 TypeError、coverage.subagent_fanout 在）。
  - **cap 截断**：sub_questions 超过 `conc*max_waves` → 被截断 + errors 记 dropped。
  - **预算断言**：对默认 conc(=2) 与极端 conc=1（monkeypatch `_fanout_concurrency`）下，`waves*per_subrun_timeout + _FANOUT_OUTER_RESERVE <= _DEEPRESEARCH_TOOL_TIMEOUT` 且 per_subrun>=_MIN_SUBRUN（可在 `_run_subagent_fanout` 跑通后从 coverage.subagent_fanout 取 waves/per_subrun_timeout_s 校验，或单独算）。
- **TG-3 引用合并**：两份子报告各含 `[^1][^2]`、有重复 URL → `_merge_subreport_citations` 后全局编号唯一、同 URL 跨报告去重为同一 global_n、refmap 正确；`_norm_url` 是模块级可直接调。
- **TG-4 脚注+finalize**：`_strip_footnote_definitions` 去「## 引用」附录+`[^n]:`定义留正文；`_rewrite_local_refs` 撞号→全局正确；`_fanout_synthesize` 在 llm_call 抛异常时降级仍产出含全局引用的报告且 cite_check 通过；`_finalize_report_md` 返回三元组 (md,cits,cc)。
- **TG-5 配置**：monkeypatch/真 config 验 `_fanout_enabled`/`_fanout_max_subquestions`/`_fanout_concurrency` 读取 + 默认兜底；无 `.raw` stub 不抛。

## 约束
- 全部用 mock/monkeypatch，**不触真网络/真 LLM**（异步用 `@pytest.mark.asyncio`，仿现有测试）。
- 跑通：`cd G:/projects/deskpet/backend && .venv/Scripts/python.exe -m pytest tests/test_deepresearch_subagent_fanout.py -q` 全绿。
- 不改生产代码（若发现实现真有 bug 导致测不过，**报告该 bug**，别改实现也别把测试写松将就）。
- 完成报告：用例数 + pytest 结果 + 是否发现实现 bug。
- python：`G:/projects/deskpet/backend/.venv/Scripts/python.exe`，cwd=backend。
