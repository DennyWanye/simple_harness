# codex 作业 B1：实现 WI-4（Focus Chain — todo 进度周期回灌）

DeskPet 项目（仓库根 `G:\projects\deskpet`，已在此 cwd）。**严格按权威 plan 实现。**

## 第 0 步：读 plan
打开 `plans/2026-06-20-agent-loop-optimization/00-PLAN.md`，读 **§5（WI-4）、§13.4（E1/E2）、§15.2 A-6、§16.1、§17.2**。以修订节为准（§16.1 给出正确的显式循环版骨架，§17.2 要求加 tier3 守卫）。

## 第 1 步：实现（只改 `backend/agent/agent_loop.py` + `backend/main.py`）
1. `agent_loop.py`：
   - 模块常量加 `_TODO_SYNC_EVERY = 8` 和模板 `_TODO_SYNC_MSG = "[当前任务进度]\n{body}\n请优先完成未完成项，勿遗漏。"`（措辞可微调）。
   - `AgentLoop.__init__` 加参数 `code_todo_getter: Optional[Callable[[str], Awaitable[list[dict]]]] = None`（带默认 None，BC），存 `self.code_todo_getter`。
   - 在 `run()` 循环内、**selfcheck 注入块之后**（约 `agent_loop.py:877` 后），加 todo sync 注入，**必须带 tier3 守卫**（§17.2，与 §13.1 B4 一致），用 §16.1 的**显式循环版**（不要用 §15.2 A-6 那个 list comprehension，它有 f-string 大括号语法错）：
     ```python
     if (self.code_todo_getter is not None
             and iteration % _TODO_SYNC_EVERY == 0
             and iteration < _SELFCHECK_TIER3_AT):
         try:
             todos = await self.code_todo_getter(session_id)
         except Exception:
             todos = []
         if todos:
             lines = []
             for t in todos:
                 content = (t.get("content") or "")[:80]
                 status = (t.get("status") or "").lower()
                 mark = {"completed": "✓", "in_progress": "🔄"}.get(status, "⏳")
                 lines.append(f"  {mark} {content}")
             working_messages.append({"role": "system",
                                      "content": _TODO_SYNC_MSG.format(body="\n".join(lines))})
             logger.info("wi4_todo_sync sid=%s iter=%d n=%d", session_id, iteration, len(todos))
     ```
2. `backend/main.py` `build_agent`（约 821-1005，构造 `_AgentLoop(...)` 在约 992）：注入 `code_todo_getter`。它应与 completion_probe **同源**（都来自 SessionDB 的 `get_code_todos`）。grep `completion_probe` 在 build_agent 里怎么构造的，照同样方式拿到 `_sdb`/`get_code_todos` 闭包传 `code_todo_getter=`。注意 `get_code_todos(session_id)` 是 **async**，返回 `list[dict]`，字段 content/activeForm/status/sort_order（grep `backend/deskpet/memory/session_db.py` 确认）。

## 第 2 步：测试
新建 `backend/tests/test_wi4_focus_chain.py`（参考 §5.4 + §17.2）：
- `test_todo_sync_injected_every_n`：getter 返 3 条 todo，跑到第 8 轮 → working_messages 出现含 ✓/🔄/⏳ 的 system 消息。
- `test_no_getter_no_injection`：getter=None → 无注入（BC）。
- `test_empty_todos_no_injection`：getter 返 [] → 不注入。
- `test_todo_sync_suppressed_at_tier3`：迭代 ≥ `_SELFCHECK_TIER3_AT`(30) 时不注 todo sync（§17.2 守卫）。
mock provider 参考 `backend/tests/test_wi1_tool_choice.py` 的 `_FallbackLLM`/`_ChainProvider`（让循环能跑到第 8、第 30 轮：mock 一直返 stop_reason="tool_use" 直到某轮 end_turn）。

跑：
```
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_wi4_focus_chain.py -v
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_deskpet_agent_loop.py backend/tests/test_build_agent_verify_wiring.py backend/tests/test_p6_agent_loop_gate.py -q
```
改到新测试全绿 + BC 全绿。

## 约束
- **不要 `git commit` / `git add`**。只改 agent_loop.py + main.py + 新建 test_wi4_focus_chain.py。
- 完成输出：改动摘要 + 测试结果。
