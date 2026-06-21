# 任务：第 2 轮对抗验证（只读）——核实 v0.2 修复是否真对 + 猎杀修复引入的新问题

这份 plan 刚吸收了第 1 轮挑战，升到 v0.2。你的工作分两步：
(1) **逐条验证** v0.2 的修复是否真的可照做、与真源码一致；
(2) **猎杀新问题**——修复本身有没有引入新的 BLOCKING/MAJOR（这是重点，迭代最容易在补丁里埋新坑）。
**必须读真源码**，不要凭空假设。聚焦 fan-out 主体（WI-1~5、§5）。

## 要评审的 plan（精读 v0.2 全文，尤其 §13 修订记录里 11 条）
`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`

## 必须读码核对的文件
- `G:/projects/deskpet/backend/deskpet/tools/research_tools.py`（_norm_url 位置/980 签名/1408 synth/1427 cite/1449 引用附录/1621 no_results/1711 handler/1797 全局桥/1870 timeout/_host/_FOOTNOTE_REF_RE/find_footnote_refs/Citation 字段）
- `G:/projects/deskpet/backend/deskpet/agent/subagent_scheduler.py`（run 签名/返回/取消）
- `G:/projects/deskpet/backend/deskpet/agent/task_kinds.py`（research KindProfile 剥工具是否安全，有没有别处依赖 research 含 deepresearch）
- `G:/projects/deskpet/backend/deskpet/tools/registry.py`（`_session_id` 真的注入进 args/params 吗，键名确切是什么）
- `G:/projects/deskpet/backend/main.py`（86 logger / 2007 register subagent_scheduler / 5992 _session_id 注入 / 全局桥注入点）
- `G:/projects/deskpet/backend/config.py`（`[agent].concurrency.lane_caps` 真实结构/键名，确认 _agent_raw/_research_lane_cap 读得对）

## 第 1 步：逐条验证这 11 条修复（每条给 VERIFIED-CORRECT 或 STILL-WRONG+证据）
1. `_norm_url` 提模块级（WI-3）—— 现状它确切在哪几行、提取会不会漏掉闭包变量？
2. 四重递归守门（§5）—— 顶层 sid 门 `any(m in sid for m in (".sub",".par-",".team-",".dr-"))` 是否覆盖所有子代理 sid 命名？核对 agent_tool/agent_parallel/spawn_team/本计划 dr- 的真实 sid 格式。剥 research 工具集有无副作用。
3. D8 动态预算（WI-2/D8）—— `waves*per_subrun+RESERVE ≤ 300` 数学在默认与极端 n 下是否真成立？clamp 下界 45 会不会在大 n 时破坏不等式？
4. WI-2 返回字段（summary + no_results 构造）—— 与 ResearchReport 真实必填字段、:1396 现成构造是否完全对齐？
5. WI-5d main.py 接线（logger / _rt._research_raw / service_context.get）—— 符号都存在吗？注入点时机对吗（scheduler 注册在前）？
6. parent_sid = args.get("_session_id")（D9）—— registry 注入的键名确切是 `_session_id` 吗？handler 拿得到吗？
7. WI-8a 兜底改 home（不落 Roaming）——（paths 这块由另一位重点看，你只需判断 fan-out 不受影响）
8. 子报告脚注 strip/rewrite（WI-4）—— `_strip_footnote_definitions`/`_rewrite_local_refs` 设计能否真去掉 :1449 追加的 `## 引用` + `[^n]:` 定义、且不误伤正文？`_FOOTNOTE_REF_RE` 真实正则是什么、replace 会不会误配？
9-11.（paths/index/打包，另一位看）

## 第 2 步：猎杀新引入的问题（重点！）
- `_agent_raw()`/`_global_concurrency()`/`_research_lane_cap()` 读 `config.raw["agent"]["concurrency"]["lane_caps"]["research"]`——这个嵌套路径与 config.py / 实际 config.toml 真实结构一致吗？driver 计划把 lane_caps 放哪？读错会静默用默认还是抛？
- `_fanout_synthesize` 返回 `(report_md, merged_citations)`，但 WI-2 调用写的是 `report_md, merged_citations = await _fanout_synthesize(...)`，而早期版本注释写过 `(merged_citations, report_md)`——核对 plan 内顺序是否自洽，别埋反序 bug。
- `_finalize_report_md` 抽取：把 :1427-1452 逐字搬走后，扁平路径原地改调它——有没有遗漏变量（如 `cc`/`used_refs`/`citations` 作用域）导致扁平路径 NameError 或行为漂移？
- `import math`、`os`（os.replace）在 research_tools.py 顶部是否已 import？`_INDEX_HEADER` 常量 plan 用到但有没有定义？
- `_update_deepresearch_index` 改成 async 且在 `_handle_deepresearch` 里 await——`_save_report` 仍同步返回 path，两者拆分后落盘与索引的调用顺序/异常隔离是否还正确？
- skip_plan=True 路径：内层 deepresearch 跳过 plan 直接 `sub_questions=[topic]`，但后续 query expansion/search_specs 等是否依赖 plan 阶段产生的其它变量？会不会 NameError？
- 顶层 sid 门：正常顶层聊天的 `_session_id` 长什么样？会不会**误含** `.sub`/`.dr-` 等子串导致顶层被误判成子代理、fan-out 永不触发（功能归零）？

## 输出格式
第 1 步：11 条逐条 `VERIFIED-CORRECT` / `STILL-WRONG`(+证据+修法)。
第 2 步：新问题按 `[BLOCKING|MAJOR|MINOR]` + 证据(file:line) + 修法。
末尾一行：`VERDICT: EXECUTABLE-AS-IS` 或 `VERDICT: NOT-EXECUTABLE (N blocking, M major)`。
