# 任务：第 4 轮终验（只读）——确认 v0.4 是否已 100% 可照做

这份 plan 经 3 轮挑战迭代到 v0.4（共修 10 BLOCKING + 6 MAJOR）。这是最终收敛验证。**只关心一个问题：现在还有没有任何会让实现者照着做却失败/做错的 BLOCKING 或 MAJOR？** 必须读真源码。没有就判 EXECUTABLE-AS-IS；不要为凑数造问题，也不要重复已被前几轮接受为正确的点。

## plan（精读 v0.4 全文 + §13 三轮修订记录，确认每轮 BLOCKING 都已闭合）
`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`

## 重点复核第 3 轮的修复是否真闭合（这是 v0.4 唯一新增 delta）
1. **递归守门三路径全封**：核对
   - `agent_parallel_tool._FORBIDDEN_NESTED_TOOLS`（[:57](/G:/projects/deskpet/backend/deskpet/tools/code_tools/agent_parallel_tool.py:57)）现状 + plan 要加 `deepresearch/spawn_team/spawn_subagents/await_subagents` 是否覆盖 `_filter_subagent_tools`（[:251/:260]）+ `spawn_subagents_tool`（[:136]）两个消费点。
   - `teammate_tools.FORBIDDEN_TEAMMATE_TOOLS`（[:42](/G:/projects/deskpet/backend/deskpet/agent/team/teammate_tools.py:42)）加 deepresearch 是否被 `_TeamSubsetRegistry` 真正消费（确认 teammate 工具过滤走这个集）。
   - `task_kinds._FORBIDDEN_IN_KIND`（[:30](/G:/projects/deskpet/backend/deskpet/agent/task_kinds.py:30)）+ research profile（[:60]）。
   - **还有没有第 4 条 LLM 子代理拿到 deepresearch 的路径**？比如单 `agent` 工具（agent_tool.py 的 `_SubsetRegistryAdapter`，[:180 附近]）显式 tools 走哪个过滤集？它会不会绕过上面三个集？若会，就是残留 BLOCKING。
2. **测试更新**：`test_task_kinds.py:18/:41`、`test_agent_parallel_kinds.py:62` 改断言后逻辑是否自洽；有没有**别的**测试也断言 research 含 deepresearch（再全仓搜一遍 `deepresearch.*tools` / `tools.*deepresearch`）。

## 整体终扫（只报真 BLOCKING/MAJOR）
- 通读 WI-1~8 + §3~§10，是否还有任一处：函数/常量/变量未定义、签名与真源码不符、import 缺失、字节级 BC 主张不成立、预算/递归/落盘任一不变量被破。
- 特别再确认：`agent_tool` 单子代理路径的工具过滤（与 agent_parallel 是否共用 forbidden 集，还是各自一套）。

## 必读源码
- `agent_parallel_tool.py`（:57 / :251-260 / :405）
- `spawn_subagents_tool.py`（:136）
- `agent_tool.py`（_SubsetRegistryAdapter / _DEFAULT_READONLY_TOOLS / 显式 tools 过滤）
- `teammate_tools.py`（:42 / _TeamSubsetRegistry 消费点）
- `task_kinds.py`（:30 / :60 / :113-126 resolve_kind/_strip_forbidden）
- `research_tools.py`（:34 imports / :980 / :1396 / :1427 / :1711 / :1770 / :1870）
- 全仓搜 `deepresearch` 在 tests/ 的断言

## 输出
逐项 VERIFIED / 残留问题 `[BLOCKING|MAJOR|MINOR]`+证据(file:line)+修法；末尾一行 `VERDICT: EXECUTABLE-AS-IS` 或 `VERDICT: NOT-EXECUTABLE (N blocking, M major)`。
