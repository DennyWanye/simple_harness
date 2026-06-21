# 任务：实现 deepresearch 子代理 fan-out 核心（Phase 2 = WI-1/2/3/4/5a/5b/5d/6）

你是编码 Expert。严格按已锁定 plan(v1.0 LOCKED) 实现，**不可少做、不可自由发挥**。本任务**只改** `backend/deskpet/tools/research_tools.py` 和 `backend/main.py`（递归守门的 task_kinds/agent_tool/agent_parallel/teammate 由另一并行 agent 做，你**别碰**）。

## 先读（必须，逐字按 plan 代码片段实现）
- plan：`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md` —— 重点 §7 的 **WI-1 / WI-2 / WI-3 / WI-4 / WI-5a / WI-5b / WI-5d / WI-6** 全部代码片段 + §3 设计 + §4 决策 D1-D10 + §5 递归守门 + §13 五轮修订（尤其 R2 预算硬裁、R2 `_finalize_report_md` 三元组签名、R3/R4 不影响你这部分）。
- 现状源码：`backend/deskpet/tools/research_tools.py`（`deepresearch`:980 签名 / plan 阶段:1058 / :1074 之后分叉点 / search:1083 / score `_passage_from` / 直连源 `_norm_url` 局部函数(约:1213-1228) / synth:1408 / cite_check+废引用+附录:1427-1452 / `_no_results_template`:1621 / `_extract_summary`:1632 / `_handle_deepresearch`:1711 / 落盘块:1802-1812 / LLM 全局桥:~1797 / 注册 `_register_deepresearch_tool`:1856 / timeout:1870 / `ResearchReport` 字段:572 / `_DEPTH_PRESETS`:1704 / `Citation` / `_host` / `_FOOTNOTE_REF_RE` / `find_footnote_refs`:908 / `cite_check`:912）。
- `backend/config.py`：`get_subagent_concurrency(cfg)`:748（复用，返回 (int, dict)）。
- `backend/deskpet/agent/subagent_scheduler.py`：`SubagentScheduler.run(*, kind, run_id, task_id, parent_sid, coro_factory)`:67 返回 coro_factory() 结果。

## 实现清单（严格按 plan §7）
### WI-5a（research_tools 全局桥 + helper + 常量）
- 顶部加 `import os`（[:34] 缺 os）；**不引 math**（整数 ceil）。
- 在 `_register_deepresearch_tool()`（:1856）**之前**定义模块常量：`_DEEPRESEARCH_TOOL_TIMEOUT = 300.0`、`_FANOUT_OUTER_RESERVE = 60.0`、`_MIN_SUBRUN = 45.0`；注册处(:1870) `timeout_seconds=300.0` → `timeout_seconds=_DEEPRESEARCH_TOOL_TIMEOUT`。
- 全局桥：`_SUBAGENT_SCHEDULER=None` + `set_subagent_scheduler(s)` + `get_subagent_scheduler()`。
- flag/并发 helper（全走 `_research_raw()` 安全兜底；并发复用 `config.get_subagent_concurrency`）：`_fanout_enabled()`(读 `subagent_fanout`,默认 False)、`_fanout_min_subquestions()`(默认2)、`_fanout_max_subquestions()`(默认6)、`_fanout_subrun_mode(outer)`(auto: deep→standard/standard→light/light→light；light/standard/deep 直返；inherit→outer)、`_fanout_concurrency()`(try import config→cfg(config.config 或 load_config(resolve_config_path()))→get_subagent_concurrency→`max(1,min(glob,lanes.get('research',2)))`，except 返 2)。

### WI-1（deepresearch 签名 + 分叉）
- 签名(:980)加：`scheduler=None, parent_sid="default", _depth=0, skip_plan=False`（keyword-only，放现有参数后，默认值=BC）。
- plan 阶段(:1058)：`skip_plan=True` 时跳过 LLM plan，`sub_questions=[topic]`（其余变量如 `_ur`/query expansion 照常初始化，别 NameError）。
- plan 之后(:1074 算出 sub_questions 后)插分叉：
  ```python
  fanout_on = (scheduler is not None and _depth == 0
               and len(sub_questions) >= _fanout_min_subquestions()
               and _fanout_enabled())
  if fanout_on:
      return await _run_subagent_fanout(topic=topic, sub_questions=sub_questions,
          llm_call=llm_call, search=search_fn, extract=extract_fn, scheduler=scheduler,
          parent_sid=parent_sid, mode=mode, user_request=_ur, errors=errors, route=route)
  ```
  否则原扁平管线**一字节不动**。

### WI-2（_run_subagent_fanout，按 plan §7 WI-2 代码片段逐字实现，含 R2 预算硬裁）
- D8 预算：`conc=_fanout_concurrency()`；`max_waves=int((_DEEPRESEARCH_TOOL_TIMEOUT-_FANOUT_OUTER_RESERVE)//_MIN_SUBRUN)`(=5)；`cap=min(_fanout_max_subquestions(), conc*max_waves)`；`eff_subq=sub_questions[:cap]`(超出 errors 记 dropped)；`submode=_fanout_subrun_mode(mode)`；`_,d_urls,d_pass,d_rounds=_DEPTH_PRESETS[submode]`；`waves=max(1,(len(eff_subq)+conc-1)//conc)`；`timeout=min(150.0,(_DEEPRESEARCH_TOOL_TIMEOUT-_FANOUT_OUTER_RESERVE)/waves)`。
- 每子问题：`scheduler.run(kind="research", run_id=f"{parent_sid}.dr-{i}", task_id=f"dr-{i}", parent_sid=parent_sid, coro_factory=_coro)`，`_coro` 内 `asyncio.wait_for(deepresearch(q, llm_call=..., search=..., extract=..., max_sub_questions=1, max_urls_per_query=d_urls, max_total_passages=d_pass, max_rounds=d_rounds, mode=submode, user_request=q, scheduler=None, _depth=1, skip_plan=True), timeout=timeout)`。
- `asyncio.gather(*, return_exceptions=True)`；失败隔离(BaseException→errors+n_failed)；成功收 (q, ResearchReport)+透传 r.errors+fanout_obs(enabled/n_subagents/n_completed/n_failed/waves/per_subrun_timeout_s/per_subquestion)。
- `base_cov={"n_sub_questions":len(sub_questions),"mode":"fanout","subagent_fanout":fanout_obs}`。
- 全失败兜底：用现成 no-passages 构造(:1396 范式)返 ResearchReport(summary="", report_md=_no_results_template(topic,sub_questions), citations=[], coverage={"n_sources":0,"n_domains":0,**base_cov}, errors=errors)。
- 否则：`report_md, merged_citations = await _fanout_synthesize(topic, user_request, sub_reports, llm_call, errors)`；`domains={_host(c.url) for c in merged if _host(c.url)}`；coverage={"n_sources":len(merged),"n_domains":len(domains),**base_cov}；返 ResearchReport(summary=_extract_summary(report_md), ...必填字段齐全)。

### WI-3（_norm_url 提模块级 + _merge_subreport_citations）
- 先把直连源分支内的局部 `_norm_url`（约:1213-1228）**提为模块级** `def _norm_url(u)`，原处改引用模块级（行为不变）。
- `_merge_subreport_citations(sub_reports)->(list[Citation], refmap)`：合并所有子报告 citations，按 `_norm_url` 去重，重排全局 [^n]（从1）；refmap={(report_idx, local_n): global_n}。Citation 字段：.n/.url/.title/.as_footnote()。

### WI-4（脚注 strip/rewrite + _fanout_synthesize + _finalize_report_md 重构）
- `_strip_footnote_definitions(md)`：删末尾「## 引用」整段 + 所有 `[^n]: ...` 定义行，留正文(含正文 [^n])。
- `_rewrite_local_refs(md, local_to_global)`：正文 `[^local]`→`[^global]`（用 `_FOOTNOTE_REF_RE` 替换）。
- `_FANOUT_SYNTH_PROMPT`(module 常量)：输入 N 个「### 子问题\n<已剥附录+已重写全局编号的正文>」+ 合并引用列表；指令明确「引用编号已统一为全局，只能引用这些 [^n]，跨子问题交叉对比消解冲突给统一结论」。
- `_finalize_report_md(report_md, citations, errors)->(report_md, citations, cc)`：把扁平路径 :1427-1452 的 cite_check+废引用清理(`find_footnote_refs`/used_citations)+引用附录追加**逐字搬进来**；扁平路径(:1427-1452)原地改 `report_md, citations, cc = _finalize_report_md(report_md, citations, errors)`，后续 coverage 的 cite_check_ok/cite_missing 用 `cc[...]`（回归 diff=0）。
- `async _fanout_synthesize(topic, user_request, sub_reports, llm_call, errors)->(report_md, list[Citation])`：merge→每份 strip+rewrite→llm_call(_FANOUT_SYNTH_PROMPT)（失败降级到按子问题拼接已重写正文）→`report_md, merged, _cc=_finalize_report_md(...)`→return (report_md, merged)。

### WI-5b（_handle_deepresearch 注入，D9，无 sid 门）
- (:1711) `sid=str(args.get("_session_id") or "default")`；`deepresearch(..., scheduler=get_subagent_scheduler(), parent_sid=sid)`。**不要**写 `_resolve_sid`。

### WI-5d（main.py 接线）
- 在 driver 的 scheduler 注册块之后（`service_context.register("subagent_scheduler",...)` 约 main.py:2007 之后）加：
  ```python
  _sched = service_context.get("subagent_scheduler")
  from deskpet.tools import research_tools as _rt
  if _sched is not None and _rt._fanout_enabled():
      _rt.set_subagent_scheduler(_sched)
      logger.info("deepresearch subagent fanout ENABLED (scheduler wired)")
  ```
  用 `logger`(main.py:86)，不是 `log`。

### WI-6（观测）
- coverage 的 subagent_fanout 块 + mode="fanout"（WI-2 已含）；WS 进度复用 scheduler 自带 emit，无新代码。

## 约束 / 自检
- **BC**：flag OFF / scheduler=None / <2 子问题 / _depth=1 → 走扁平、与现状字节级一致。
- **不破坏现有 research 测试**：`cd G:/projects/deskpet/backend && .venv/Scripts/python.exe -m pytest tests/test_deskpet_research_tools.py tests/test_deepresearch_output_dir.py -q` 必须全绿。
- 别碰 task_kinds/agent_tool/agent_parallel_tool/teammate_tools（另一 agent 做）。
- 中文 UTF-8 编辑勿乱码。python 解释器 `G:/projects/deskpet/backend/.venv/Scripts/python.exe`，跑测试 cwd=`G:/projects/deskpet/backend`。
- 完成报告：改了哪些函数/常量、pytest 结果。
