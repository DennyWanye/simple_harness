# T5 spike — teammate 进程模型核查（WI-1.2 claim 原语前置）

> 2026-06-04 ｜ 结论：**同进程 asyncio 协程**

## 证据
- [spawn_team.py:216-234](../../../backend/deskpet/agent/team/spawn_team.py)：`teammate_coros=[_run_teammate(...)]` → `await asyncio.gather(*teammate_coros)`，全部 teammate 在**同一 event loop 内并发协程**。
- `_make_default_runner`（:255）懒构造真 `AgentLoop`，在进程内 await，**非子进程/无多进程**。
- `build_teammate_tools(store=, team_id=, teammate_id=)`（:224）现 3 kwarg —— WI-1.2(Task6) 在此加 `task_graph_store/goal_id`。

## 结论（WI-1.2 实现依据）
- `goal_tasks` 原子 claim **用 SessionDB 既有 `_write_lock`(asyncio.Lock) + `_with_retry` 串行**：先 SELECT ready 候选（depends_on 全 done 的 pending），再在锁内单条 UPDATE claim。
- **不用** TeamStore per-team `BEGIN IMMEDIATE`（那是跨进程模型，本场景同进程 asyncio.Lock 已足够且不兼容）。
- 并发 claim 单测用并发协程模拟（pass^k=5），asyncio.Lock 保证不双占。
