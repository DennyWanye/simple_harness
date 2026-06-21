在 DeskPet 项目新建 PPT Pro 计划的大纲卡持久化模块（WI-3 的 store 部分）。**新建一个文件** `G:\projects\deskpet\backend\deskpet\tools\ppt_outline_store.py`，可只读参考其它文件但不要改它们。

先读权威计划：`G:\projects\deskpet\plans\2026-06-21-ppt-deepresearch-pro\00-PLAN.md` 的 WI-3 段（文件 A）+ §6 R-19/R-21。
范本参考（只读）：
- `G:\projects\deskpet\backend\deskpet\skills\skill_codifier.py` 的 `SkillCandidateWaiters`（add/resolve/pop 模式）。
- `G:\projects\deskpet\backend\deskpet\memory\memory_v2_schema.py` 的 `ensure_session_goals_table`（flag-gated 独立建表，不进共享 DDL，保字节 BC）。
- `G:\projects\deskpet\backend\deskpet\memory\session_db.py` 调用前懒 ensure 的写法。
- `G:\projects\deskpet\backend\deskpet\tools\ppt_tools.py` 的 `SlideOutline` dataclass + `parse_outline`（确认 round-trip：dataclasses.asdict → json.dumps 存，取出 parse_outline 还原）。

要实现（`ppt_outline_store.py`）：
1. **`class PPTOutlineWaiters`**：内部 `dict[str, asyncio.Future]`。方法：
   - `add(oid: str, fut)`、
   - `resolve(oid: str, decision: dict) -> bool`：找到 fut，若未 done 则 `set_result(decision)` 返回 True；已 pop/已 done → no-op 返回 False（**幂等、防双面板重复 resolve**）。
   - `pop(oid: str)`：`self._d.pop(oid, None)`（**幂等，防 double-pop KeyError**）。
2. **大纲历史持久化**（SQLite，复用项目 SessionDB 的库路径/连接方式——读 session_db.py 看它怎么拿 db 路径；如果不易复用就接受传入一个 sqlite3 连接工厂参数，保持模块可单测）：
   - `ensure_ppt_outline_table(conn)`：flag-gated 独立建表 `ppt_outline_history(outline_id TEXT PRIMARY KEY, session_id TEXT, topic TEXT, created_at TEXT, slides_json TEXT, sources_count INTEGER, status TEXT)`。**不要塞进任何共享 DDL**（保 BC 字节基线）。status ∈ proposed/accepted/rejected/cancelled/expired/superseded。
   - `save_outline(oid, sid, topic, slides: list, sources_count)`：`dataclasses.asdict` 每个 slide → `json.dumps` 存 slides_json，status=proposed，created_at=ISO。
   - `mark_status(oid, status)`。
   - `list_history(sid, limit=20) -> list[dict]`：倒序返回 `{outline_id, topic, created_at, sources_count, status}`（不含大 slides_json，省带宽），供前端历史区展示。
   - `get_outline(oid) -> dict|None`：返回含 `slides_json` 的整行（供 reuse 还原）。
   - `expire_dangling_proposed()`：把所有 `status='proposed'` 改 `expired`（供 backend 启动时清跨重启残留死卡，R-19）。
3. **不要**在 import 时建表/连库（懒执行）。所有函数对 db 不可用要优雅降级（log + 返回空/None，不崩）。

约束：
- 中文注释；纯库模块，无 FastAPI/IPC。
- 时间戳别用会破坏可测性的全局——用 `datetime.now().isoformat()` 即可（这是普通后端代码，不是 workflow 脚本，可以用）。
- 自己写单测 `tests/test_ppt_outline_store.py`：用临时 sqlite（tmp_path）验证 ensure/save/list/get/mark/expire + round-trip（slides 存取后 parse_outline 还原一致）+ waiters add/resolve/pop 幂等（resolve 已 done no-op、pop 两次不抛）。
- 跑 `cd /g/projects/deskpet/backend && .venv/Scripts/python.exe -m pytest tests/test_ppt_outline_store.py -q` 到绿。

验收：模块可 import、单测全绿、round-trip 无损、waiters/pop 幂等。完成后简述 API 与单测覆盖。