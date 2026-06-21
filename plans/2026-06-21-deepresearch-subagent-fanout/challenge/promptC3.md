# 任务：第 3 轮收敛终审（只读）——核 v0.3 全部 delta 是否真对 + 整体能否 100% 照做

这份 plan 已迭代到 v0.3（两轮挑战共修 9 BLOCKING + 7 MAJOR）。这是收敛终审。两步：
(1) 逐条核 v0.3 的修复 delta 是否真可照做、与真源码一致；
(2) 整体扫一遍有无任何残留 BLOCKING/MAJOR（含前两轮修复可能引入的连锁问题）。
**必须读真源码**。只报真问题；没问题就明确判 EXECUTABLE-AS-IS，不要为凑数造问题。

## plan（精读 v0.3 全文 + §13 两轮修订记录）
`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`

## 必读源码
- `G:/projects/deskpet/backend/deskpet/tools/research_tools.py`（顶部 imports[:34] / _norm_url 位置 / 980 签名 / 1396 no-passages 构造 / 1427-1452 cite 块 / 1621 no_results / 1655 schema 描述 / 1711 handler / 1751-1775 _save_report / 1797 全局桥 / 1856 注册 / 1870 timeout / _host / _FOOTNOTE_REF_RE / find_footnote_refs / Citation 字段 / ResearchReport 字段[:572]）
- `G:/projects/deskpet/backend/config.py`（get_subagent_concurrency[:748] 签名/返回/默认）
- `G:/projects/deskpet/backend/paths.py`（_install_dir[:50] / 顶部 import os 是否在）
- `G:/projects/deskpet/backend/deskpet/agent/task_kinds.py`（_FORBIDDEN_IN_KIND[:30] / resolve_kind 是否真用它剥 / research profile[:60]）
- `G:/projects/deskpet/backend/main.py`（86 logger / 2007 register subagent_scheduler / 5992 _session_id / 全局桥注入时机）
- `G:/projects/deskpet/backend/deskpet/tools/registry.py`（_session_id 注入键名）

## 第 1 步：逐条核 v0.3 delta（VERIFIED-CORRECT / STILL-WRONG+证据+修法）
1. D8 预算硬裁：`max_waves=(300-60)//45=5`、`cap=min(fanout_max,conc*max_waves)`、`waves=(n+conc-1)//conc`、`timeout=min(150,240/waves)`——在 `conc=1,fanout_max=20`、`conc=2,n=6`、`conc=4,n=8` 三组下，`waves*timeout+60 ≤ 300` 是否恒成立？timeout 是否恒 ≥45？
2. `_fanout_concurrency()` 复用 `config.get_subagent_concurrency(cfg)`：核对该函数真实签名/返回 `(int, dict)`、cfg 获取方式（config.config 或 load_config(resolve_config_path())）是否可行、research 键存在。
3. 递归守门去 sid 门、改 `_FORBIDDEN_IN_KIND` 加 deepresearch：核对 `resolve_kind()` 真的对内置+overrides 都剥 `_FORBIDDEN_IN_KIND`；剥 deepresearch 后有无别的生产代码依赖 research kind 必须含 deepresearch（搜一下）。
4. `import os` 加在 research_tools 顶部、`_INDEX_HEADER`/`_insert_row_after_header` 已在 plan 定义：核对现状 imports 确实缺 os；helper 逻辑（找 `|---|` 分隔行后插）对 `_INDEX_HEADER` 自身格式是否成立（分隔行存在）。
5. `_finalize_report_md(report_md,citations,errors)->(report_md,citations,cc)` 抽取：核对 1427-1452 真实代码块的输入/输出变量，签名能否无损覆盖扁平路径（cc 后续在 1462 coverage 用）。
6. `_save_report` 拆分（同步只写报告、index 更新移到 async handler await）：核对 handler 1751-1765 落盘块结构，能否自然插入 `await _update_deepresearch_index(...)`。
7. probe 唯一名 `os.getpid()`、home 兜底不落 user_data_dir：核对 paths.py 顶部有 os import、Path.home 可用。
8. 旧 OutPut/Research 文案清理点（1655/1751/1771/SKILL.md:86）真实存在可改。

## 第 2 步：整体残留扫描（重点新连锁问题）
- `_run_subagent_fanout` 里 `route` 参数传入但似乎没用到/没并入 coverage？fan-out 路径的 coverage 缺了扁平路径有的哪些键（topic_velocity/unique_domains 等），会不会导致 _save_report header 或前端取键 KeyError？
- skip_plan=True 时内层 deepresearch 跳过 plan，但 `_ur`/query expansion/`_observability_coverage` 等变量是否仍正确初始化（不 NameError）？
- 内层子跑 mode=light/standard 时 `_DEPTH_PRESETS[submode]` 解包 4 元组与内层 deepresearch 参数映射是否一致？
- fan-out 的 coverage 没有 `elapsed_ms_per_stage`/`route`/`topic_velocity`，而 `_save_report`（[:1778-1783]）读 `cov.get('rounds')`/`cov.get('topic_velocity')`——缺键 .get 有默认吗？会不会 header 显示异常但不崩？
- `_handle_deepresearch` 把 index 更新移到 await，但若 `report.report_md and report.citations` 为空（全失败兜底）则不落盘——index 也不更新，符合预期吗？
- 有没有循环 import 风险：main.py `from deskpet.tools import research_tools`、research_tools `import config`、config 是否 import research_tools？

## 输出
第 1 步逐条 VERIFIED-CORRECT/STILL-WRONG；第 2 步残留问题 `[BLOCKING|MAJOR|MINOR]`+证据(file:line)+修法；末尾一行 `VERDICT: EXECUTABLE-AS-IS` 或 `VERDICT: NOT-EXECUTABLE (N blocking, M major)`。
