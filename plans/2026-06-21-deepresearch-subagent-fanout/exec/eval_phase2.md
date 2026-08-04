# 任务：评估 Phase 2（子代理 fan-out）实现完成度是否 100%（只读）

对照已锁 plan(v1.0) 的 **WI-1/2/3/4/5(a-d)/6/7 + §5 递归守门**，逐项核对真实代码是否**完整正确**实现。读真源码，每项 DONE/PARTIAL/MISSING + 证据(file:line)，末尾 `COMPLETION: NN%` + <100% 列具体缺口。**只认代码事实**。

## 基准
plan：`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`（§7 WI-1~7 + §5 + §13 五轮修订要点）

## 逐项核对（in-scope = 生产代码 + 测试；out-of-scope = 历史 STATUS/testcase/其它 worktree）
1. **WI-1** `deepresearch` 签名加 scheduler/parent_sid/_depth/skip_plan(keyword-only,默认BC) + plan 后 `fanout_on` 分叉(scheduler!=None ∧ _depth==0 ∧ ≥min子问题 ∧ _fanout_enabled) + skip_plan 跳过 plan。`research_tools.py`。
2. **WI-2** `_run_subagent_fanout`：预算硬裁(conc=_fanout_concurrency, max_waves=(300-60)//45=5, cap=min(max_subq,conc*max_waves), 整数 ceil waves, timeout=min(150,(300-60)/waves)) + scheduler.run(kind=research,run_id=<sid>.dr-i) + 内层 deepresearch(scheduler=None,_depth=1,skip_plan=True) + gather(return_exceptions) 失败隔离 + 全失败兜底(no_results,summary 在) + coverage.subagent_fanout/mode=fanout + ResearchReport 字段齐(summary=_extract_summary)。
3. **WI-3** `_norm_url` 提模块级(直连源改引用) + `_merge_subreport_citations`(按 _norm_url 去重,全局重编号,refmap)。
4. **WI-4** `_strip_footnote_definitions`+`_rewrite_local_refs`+`_FANOUT_SYNTH_PROMPT`+`_fanout_synthesize`(失败降级) + `_finalize_report_md(...)->(md,cits,cc)` 抽取且扁平路径(:1427 原址)改用返回值(BC,coverage cite_check_ok/cite_missing 用 cc)。
5. **WI-5a** import os + 常量(_DEEPRESEARCH_TOOL_TIMEOUT 在注册前定义,注册 timeout 引用之/_FANOUT_OUTER_RESERVE/_MIN_SUBRUN) + 全局桥 set/get_subagent_scheduler + flag/并发 helper(复用 config.get_subagent_concurrency)。
6. **WI-5b** `_handle_deepresearch` parent_sid=args.get("_session_id") + scheduler=get_subagent_scheduler()（无 sid 门）。
7. **WI-5c §5 递归守门**：task_kinds._FORBIDDEN_IN_KIND 含 deepresearch + research profile 去 deepresearch；agent_tool/agent_parallel(_FORBIDDEN_NESTED_TOOLS)/teammate(FORBIDDEN_TEAMMATE_TOOLS) 全引用/并入共享集（4 路径全封）。
8. **WI-5d** main.py gated wiring(service_context.get scheduler + _rt._fanout_enabled → set_subagent_scheduler, logger 非 log)。
9. **WI-6** coverage.subagent_fanout + WS 复用。
10. **WI-7 测试** test_deepresearch_subagent_fanout.py TG-1~5 + 守门测试更新(test_task_kinds/test_agent_parallel_kinds/test_agent_parallel/subagent_driver_smoke)。
- **BC**：flag OFF/scheduler=None/_depth=1/<2子问题 走扁平。Lead 已实跑：合并 128 passed + 更广 261 passed(driver+byte_level BC) 0 fail；据源码判覆盖完整性，勿因跑不了测试扣分。

## 输出
逐项 DONE/PARTIAL/MISSING + 证据；末尾 `COMPLETION: NN%` + in-scope 缺口清单。
