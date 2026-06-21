# 任务：对抗式挑战一份实施计划（只读评审，找出"不能照做"的硬伤）

你是一名严格的架构评审员。下面这份 plan 声称"100% 可被原原本本执行"。你的工作是**证伪它**——找出任何会让实现者照着做却失败、或做出错误结果的地方。**必须读真源码核对**，不要凭空假设。

## 要评审的 plan（精读全文）
`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`

## 你这一份的聚焦范围
fan-out 集成与正确性：WI-1 ~ WI-7（plan 的子代理 fan-out 主体）。WI-8（落盘/路径）由另一名评审员负责，你可略过。

## 必须读码核对的文件（用真实 file:line 验证 plan 里的每一处引用）
- `G:/projects/deskpet/backend/deskpet/tools/research_tools.py` — deepresearch 编排器（plan 引用了大量行号：980/1058/1074/1083/1132/1143/1213/1300/1408/1427/1711/1738/1770/1797/1856），逐一核对是否对得上
- `G:/projects/deskpet/backend/deskpet/agent/subagent_scheduler.py` — `SubagentScheduler.run` 的真实签名与返回值
- `G:/projects/deskpet/backend/deskpet/agent/task_kinds.py` — research KindProfile（递归隐患）
- `G:/projects/deskpet/backend/main.py` — deepresearch 的 LLM 全局桥怎么设、scheduler 在哪构造、`_handle_deepresearch` 怎么被调
- `G:/projects/deskpet/plans/2026-06-21-subagent-concurrency-driver/02-implementation-plan.md` — 本 plan 依赖的子代理 driver（scheduler 由它构造）

## 重点挑战这些点（逐条给结论）
1. **签名变更的 BC**：`deepresearch()` 加 5 个新参数（scheduler/parent_sid/_depth/skip_plan + 既有），现有所有调用方（含单测 `test_deskpet_research_tools.py`、`_handle_deepresearch`）会不会破？默认值是否真的字节级 BC？
2. **递归守门 depth-1**：plan 的三重守门（`_depth==0` 才分叉 + 内层 `scheduler=None` + 防御剥工具）是否真的滴水不漏？有没有路径能让内层再 fan-out？
3. **scheduler.run 用法**：plan 里 `scheduler.run(kind=..., run_id=..., task_id=..., parent_sid=..., coro_factory=_coro)` 与真实签名/返回值是否完全一致？`coro_factory` 是否真延迟构造？取消/异常如何透传到 `gather(return_exceptions=True)`？
4. **注入机制**：plan 说"照抄 LLM 全局桥"设 `_SUBAGENT_SCHEDULER`。请核对 main.py 里 LLM 全局桥的真实写法，确认 scheduler 照抄可行；并确认 main.py 里**真的有** scheduler 实例可注入（依赖 driver 计划是否已落地 + flag）。`_handle_deepresearch` 能否拿到 `parent_sid`（task_id→sid 解析存在吗）？
5. **引用全局重编号**：`_merge_subreport_citations` 依赖 `_norm_url` 与 `Citation` 结构——核对它们真实存在且字段对得上（Citation.n / .url / .as_footnote）。重编号后喂 synthesize、再 cite_check 的链路有没有断点？
6. **统一 synthesize**：`_FANOUT_SYNTH_PROMPT` 是否需要约束 LLM 只用全局编号？子报告正文里残留的本地脚注会不会污染？plan 的"剥本地脚注"方案是否可行（有没有现成 helper 如 `find_footnote_refs`）？
7. **预算/超时**：deepresearch tool 超时 300s（核对 research_tools.py 注册处）。N 个子问题 × 完整管线，即使 scheduler cap=4 并发，最坏 wall-clock 是否会破 300s？plan 的"降档+单跑 wait_for(120s)+并发重叠"是否够？`_DEPTH_PRESETS` 的真实档位值是多少（验证 plan 的降档映射可行）？
8. **失败隔离与全失败兜底**：`_no_results_report` 是否真实存在/可复用？`ResearchReport` 构造字段是否齐全？

## 输出格式（严格）
按严重度分级，每条给：`[BLOCKING|MAJOR|MINOR|GAP]` + 一句话标题 + **证据（真实 file:line + 摘录）** + **具体修法（落到 plan 哪一节怎么改）**。
最后给一行总判定：`VERDICT: EXECUTABLE-AS-IS` 或 `VERDICT: NOT-EXECUTABLE (N blocking, M major)`。
只报真问题，不要客套；没问题的点也明确说"已核对正确"。
