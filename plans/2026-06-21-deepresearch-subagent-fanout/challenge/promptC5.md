# 任务：第 5 轮终验（只读）——确认 v0.5 是否 100% 可照做

这份 plan 经 4 轮挑战迭代到 v0.5（共修 11 BLOCKING + 8 MAJOR）。最近两轮都栽在"又一个 tool-filter 路径绕过递归守门"；v0.5 改为**单一共享常量 `task_kinds._FORBIDDEN_IN_KIND`（含 deepresearch），全部 4 条 spawn 路径引用它**。本轮只需确认：(a) 这个根治是否真闭合所有路径、(b) 还有没有任何其它 BLOCKING/MAJOR。必须读真源码。没有就判 EXECUTABLE-AS-IS，**不要为凑数造问题**。

## plan（精读 v0.5 + §13 四轮修订记录）
`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`

## 重点：穷举"LLM 子代理拿到 deepresearch 工具"的所有路径，确认 v0.5 全封
逐一核对，每条说清"是否经过 `_FORBIDDEN_IN_KIND` 过滤"：
1. 单 `agent` 工具显式 tools → `agent_tool.py:109-113`（v0.5 改 `t not in _FORBIDDEN_IN_KIND`）
2. `agent` 工具默认 tools（不传 tools）→ `default_tool_subset`/`_DEFAULT_READONLY_TOOLS`（[agent_tool.py:34]）是否本就不含 deepresearch？
3. `agent_parallel` 显式 tools → `_filter_subagent_tools`/`_FORBIDDEN_NESTED_TOOLS`（v0.5 引用 `_FORBIDDEN_IN_KIND`）
4. `agent_parallel` 经 kind → `resolve_kind` 剥
5. `spawn_subagents` 显式 tools → 复用 `_filter_subagent_tools`（[spawn_subagents_tool.py:136]）
6. `spawn_team` teammate → `FORBIDDEN_TEAMMATE_TOOLS`/`_TeamSubsetRegistry`（v0.5 并入）+ `build_teammate_tools` 是否还有别的工具来源能注入 deepresearch
7. **有没有第 7 条**？（任何我没列到的 spawn/工具注入路径——这是本轮关键，请主动找）

确认 `_FORBIDDEN_IN_KIND` 被这些路径引用时**无循环 import**（task_kinds 依赖了什么；agent_tool/agent_parallel_tool/teammate_tools import task_kinds 是否成环）。

## 整体终扫（只报真 BLOCKING/MAJOR；逐一确认前 4 轮的修复在 v0.5 文本里仍自洽、无回退）
- 预算 D8（硬裁 n / conc=1 边界）、`_fanout_concurrency` 复用 `get_subagent_concurrency`、WI-2 返回字段/全失败兜底、WI-3 `_norm_url` 提取、WI-4 `_finalize_report_md` 签名+脚注 strip/rewrite、WI-5 全局桥/`import os`/logger/parent_sid、WI-8 paths/index helper/probe/home 兜底/旧文案清理——有无任何一处在多轮编辑后出现自相矛盾、悬空引用、或与真源码不符。
- 文件清单 §8、验收 §10 是否与 WI 一致、无遗漏。

## 必读源码
- `agent_tool.py`（:34 默认集 / :109-113 显式过滤 / :180 _SubsetRegistryAdapter）
- `agent_parallel_tool.py`（:57 / :251-260）· `spawn_subagents_tool.py`（:136）
- `teammate_tools.py`（:42 / build_teammate_tools / _TeamSubsetRegistry）· `spawn_team.py`（teammate 工具来源）
- `task_kinds.py`（:30 / :60 / :113-126）
- `research_tools.py`（:34 / :980 / :1396 / :1427 / :1711 / :1770 / :1870 / get_subagent_concurrency 调用）
- `config.py`（:748 get_subagent_concurrency）· `paths.py`（:50 / 顶部 import os）

## 输出
路径 1-7 逐条 VERIFIED/残留；整体残留 `[BLOCKING|MAJOR|MINOR]`+证据(file:line)+修法；末尾一行 `VERDICT: EXECUTABLE-AS-IS` 或 `VERDICT: NOT-EXECUTABLE (N blocking, M major)`。
