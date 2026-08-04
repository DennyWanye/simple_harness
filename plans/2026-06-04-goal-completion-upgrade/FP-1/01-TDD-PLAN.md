# FP-1「目标持久化地基」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `SessionGoalStore` 从纯内存态升级为「持久化 + 重启可恢复」的目标唯一权威，接通 lifespan，使 `/goal` 设的目标在 `deskpet.exe` 重启后仍在、`iterations_used` 不归零。

**Architecture:** 沿用 DeskPet 已跑顺 4 次的 sidecar 惯例——`memory_v2_schema._DDL` 追加 `session_goals` 表（runtime `CREATE TABLE IF NOT EXISTS`，不写 migration、不 bump user_version），SessionDB 加 3 个薄方法，`SessionGoalStore` 通过 `bind_persistence(session_db)` 异步落库 + 启动 `load_persisted()` 灌回内存。`get_*` 永读内存（最新权威），落库异步备份、失败 safe-fail 不抛。所有新字段/参数有默认值，`goal_mode` flag-OFF 时 store=None、新表不建、字节不变。

**Tech Stack:** Python 3.14 / aiosqlite + WAL / pytest + pytest-asyncio / dataclasses。后端 venv：`backend/.venv`。

**权威依据：** [00-CONTRACT-FREEZE.md](./00-CONTRACT-FREEZE.md)（🔒 已冻结，schema/DDL/接口以此为准）+ [01-P0-1-execution.md](../01-P0-1-execution.md) WI-1.1 段。

**测试命令前缀（全程统一）：**
```bash
cd /g/projects/deskpet/backend && python -m pytest <test_path> -v
```

---

## 文件结构（先锁分解）

| 文件 | 责任 | 动作 |
|---|---|---|
| `backend/deskpet/memory/memory_v2_schema.py` | `_DDL` 追加 `session_goals` 表 | 改（§T1） |
| `backend/deskpet/memory/session_db.py` | `upsert_session_goal` / `get_active_goals` / `list_active_goals` 三薄方法 | 改（§T2） |
| `backend/deskpet/agent/goal_store.py` | `SessionGoal` 扩字段 + `SessionGoalStore` 持久化方法 | 改（§T3/§T4/§T5） |
| `backend/deskpet/commands/__init__.py` | `_handle_goal` 改 async 落库 | 改（§T6） |
| `backend/main.py` | R-T1 lifespan 接电（bind_persistence + load_persisted，写死顺序） | 改（§T6） |
| `backend/agent/agent_loop.py` | T1：`increment_iteration` 后落库 | 改（§T5） |
| `backend/deskpet/agent/tool_path.py` | WI-1.6 ToolPath 录制 + `get_completed_path`（只录不消费） | 新建（§T7） |
| `backend/tests/test_goal_store_persistence.py` | WI-1.1/T1 单测 | 新建 |
| `backend/tests/test_session_goals_db.py` | SessionDB 三方法 round-trip 单测 | 新建 |
| `backend/tests/test_tool_path_recording.py` | WI-1.6 单测 | 新建 |
| `scripts/e2e_flag_off_baseline.py` | R-T5 flag-OFF 字节基线 | 新建（§T8） |

**Task 依赖序**：T1 → T2 → T3 → T4 → T5 → T6 → T7（独立，可与 T4-T6 并行）→ T8 → T9 手测门。

---

## Task 1: `session_goals` 表 DDL（memory_v2_schema）

**Files:**
- Modify: `backend/deskpet/memory/memory_v2_schema.py:159`（`_DDL` 字符串尾部，`session_plans` 块之后、闭合 `"""` 之前）
- Test: `backend/tests/test_session_goals_db.py`

**冻结依据：** [00-CONTRACT-FREEZE.md](./00-CONTRACT-FREEZE.md) §1.3。

- [ ] **Step 1: 写失败测试（建表幂等 + 列齐全）**

`backend/tests/test_session_goals_db.py`（新建）：
```python
# SPDX-License-Identifier: BUSL-1.1
"""WI-1.1 — session_goals DDL + SessionDB thin-method round-trip."""
from __future__ import annotations

import aiosqlite
import pytest

from deskpet.memory.memory_v2_schema import (
    ensure_memory_v2_tables,
    _reset_cache_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


@pytest.mark.asyncio
async def test_session_goals_table_created_with_frozen_columns(tmp_path):
    db = str(tmp_path / "state.db")
    await ensure_memory_v2_tables(db)
    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute("PRAGMA table_info(session_goals)")
        cols = {row[1] for row in await cur.fetchall()}
    # 冻结 §1.3 的列集，逐字段断言（防漂移）
    assert cols == {
        "goal_id", "session_id", "text", "status", "progress",
        "criteria", "max_iterations", "iterations_used",
        "set_at", "updated_at",
    }


@pytest.mark.asyncio
async def test_ensure_tables_idempotent(tmp_path):
    db = str(tmp_path / "state.db")
    await ensure_memory_v2_tables(db)
    _reset_cache_for_tests()
    # 第二次不应抛（CREATE TABLE IF NOT EXISTS）
    await ensure_memory_v2_tables(db)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_session_goals_db.py::test_session_goals_table_created_with_frozen_columns -v`
Expected: FAIL — `cols` 不含 session_goals（无此表，PRAGMA 返回空集）。

- [ ] **Step 3: 加 DDL**

在 `memory_v2_schema.py` 的 `_DDL` 字符串中，`session_plans` 表块（`:152-158`）之后、闭合 `"""`（`:159`）之前追加：
```sql

-- =====================================================================
-- goal-completion FP-1 — 目标持久化（WI-1.1，冻结 §1.3）
-- =====================================================================
-- 多目标物理支持（goal_id PK，非 session_id PK），API 层 last-write-wins
-- 单活跃目标。criteria 占位列：FP-3(2.3) 用，WI-1.1 不消费。runtime
-- CREATE TABLE IF NOT EXISTS：goal_mode OFF 时永不建表，DB 字节不变。
CREATE TABLE IF NOT EXISTS session_goals (
    goal_id         TEXT    PRIMARY KEY,
    session_id      TEXT    NOT NULL,
    text            TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'active',
    progress        REAL    NOT NULL DEFAULT 0.0,
    criteria        TEXT,
    max_iterations  INTEGER NOT NULL DEFAULT 10,
    iterations_used INTEGER NOT NULL DEFAULT 0,
    set_at          REAL    NOT NULL,
    updated_at      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_goals_sid
    ON session_goals(session_id, status);
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_session_goals_db.py -v`
Expected: 2 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/deskpet/memory/memory_v2_schema.py backend/tests/test_session_goals_db.py
git commit -m "feat(goal-fp1): add session_goals DDL (WI-1.1, freeze §1.3)"
```

---

## Task 2: SessionDB 三薄方法（upsert / get_active / list_active）

**Files:**
- Modify: `backend/deskpet/memory/session_db.py`（在 `clear_session_plan_awaiting` 之后追加，约 `:970+`）
- Test: `backend/tests/test_session_goals_db.py`（追加）

**模板：** 完全同构 `upsert_session_plan`（[session_db.py:868-915](../../../backend/deskpet/memory/session_db.py)）——`ensure_memory_v2_tables` 自带建表 + `_write_lock` + `_with_retry` + `busy_timeout`。

- [ ] **Step 1: 写失败测试（round-trip + active 过滤）**

追加到 `backend/tests/test_session_goals_db.py`：
```python
from deskpet.memory.session_db import SessionDB


@pytest.mark.asyncio
async def test_upsert_and_get_active_goal(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    await db.upsert_session_goal(
        goal_id="g1", session_id="s1", text="整理三个会议纪要",
        status="active", progress=0.0, criteria=None,
        max_iterations=10, iterations_used=2,
        set_at=100.0, updated_at=100.0,
    )
    rows = await db.get_active_goals("s1")
    assert len(rows) == 1
    assert rows[0]["text"] == "整理三个会议纪要"
    assert rows[0]["iterations_used"] == 2
    assert rows[0]["status"] == "active"


@pytest.mark.asyncio
async def test_done_goal_excluded_from_active(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    await db.upsert_session_goal(
        goal_id="g1", session_id="s1", text="t", status="done",
        progress=1.0, criteria=None, max_iterations=10,
        iterations_used=1, set_at=1.0, updated_at=2.0,
    )
    assert await db.get_active_goals("s1") == []


@pytest.mark.asyncio
async def test_list_active_goals_across_sessions(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    for i, sid in enumerate(["s1", "s2"]):
        await db.upsert_session_goal(
            goal_id=f"g{i}", session_id=sid, text=f"t{i}",
            status="active", progress=0.0, criteria=None,
            max_iterations=10, iterations_used=0,
            set_at=float(i), updated_at=float(i),
        )
    rows = await db.list_active_goals()
    assert {r["session_id"] for r in rows} == {"s1", "s2"}


@pytest.mark.asyncio
async def test_upsert_overwrites_same_goal_id(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    for used in (1, 5):
        await db.upsert_session_goal(
            goal_id="g1", session_id="s1", text="t", status="active",
            progress=0.0, criteria=None, max_iterations=10,
            iterations_used=used, set_at=1.0, updated_at=float(used),
        )
    rows = await db.get_active_goals("s1")
    assert len(rows) == 1
    assert rows[0]["iterations_used"] == 5
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_session_goals_db.py::test_upsert_and_get_active_goal -v`
Expected: FAIL — `AttributeError: 'SessionDB' object has no attribute 'upsert_session_goal'`。

- [ ] **Step 3: 实现三方法**

在 `session_db.py` 的 `clear_session_plan_awaiting` 方法之后追加：
```python
    # ───────────────────── goal-completion FP-1 (WI-1.1) ──────────────
    async def upsert_session_goal(
        self,
        *,
        goal_id: str,
        session_id: str,
        text: str,
        status: str,
        progress: float,
        criteria: Optional[str],
        max_iterations: int,
        iterations_used: int,
        set_at: float,
        updated_at: float,
    ) -> None:
        """落一条 goal（同 goal_id 覆盖）。同构 upsert_session_plan：
        自带建表（全新 DB 也能 upsert）+ _write_lock + _with_retry。
        """
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables
        await ensure_memory_v2_tables(self._db_path)

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        """
                        INSERT INTO session_goals(
                            goal_id, session_id, text, status, progress,
                            criteria, max_iterations, iterations_used,
                            set_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(goal_id) DO UPDATE SET
                            text            = excluded.text,
                            status          = excluded.status,
                            progress        = excluded.progress,
                            criteria        = excluded.criteria,
                            max_iterations  = excluded.max_iterations,
                            iterations_used = excluded.iterations_used,
                            updated_at      = excluded.updated_at
                        """,
                        (
                            goal_id, session_id, text, status, progress,
                            criteria, max_iterations, iterations_used,
                            set_at, updated_at,
                        ),
                    )
                    await db.commit()

        await self._with_retry(_do)

    async def get_active_goals(self, session_id: str) -> list[dict[str, Any]]:
        """读某 session 的 active 目标，updated_at 倒序（最新在前）。"""
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables
        await ensure_memory_v2_tables(self._db_path)

        async def _do() -> list[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                cur = await db.execute(
                    """
                    SELECT goal_id, session_id, text, status, progress,
                           criteria, max_iterations, iterations_used,
                           set_at, updated_at
                    FROM session_goals
                    WHERE session_id = ? AND status = 'active'
                    ORDER BY updated_at DESC
                    """,
                    (session_id,),
                )
                rows = await cur.fetchall()
                await cur.close()
                return [self._goal_row_to_dict(r) for r in rows]

        return await self._with_retry(_do)

    async def list_active_goals(self) -> list[dict[str, Any]]:
        """启动恢复用：全库所有 active 目标。"""
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables
        await ensure_memory_v2_tables(self._db_path)

        async def _do() -> list[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                cur = await db.execute(
                    """
                    SELECT goal_id, session_id, text, status, progress,
                           criteria, max_iterations, iterations_used,
                           set_at, updated_at
                    FROM session_goals WHERE status = 'active'
                    ORDER BY updated_at DESC
                    """
                )
                rows = await cur.fetchall()
                await cur.close()
                return [self._goal_row_to_dict(r) for r in rows]

        return await self._with_retry(_do)

    @staticmethod
    def _goal_row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "goal_id": row[0],
            "session_id": row[1],
            "text": row[2],
            "status": row[3],
            "progress": row[4],
            "criteria": row[5],
            "max_iterations": row[6],
            "iterations_used": row[7],
            "set_at": row[8],
            "updated_at": row[9],
        }
```
> 确认文件顶部已 import `Any` / `Optional`（session_plans 方法已用，应已在）。若缺则补 `from typing import Any, Optional`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_session_goals_db.py -v`
Expected: 6 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/deskpet/memory/session_db.py backend/tests/test_session_goals_db.py
git commit -m "feat(goal-fp1): SessionDB session_goals CRUD thin methods (WI-1.1)"
```

---

## Task 3: `SessionGoal` 扩字段 + 不变式

**Files:**
- Modify: `backend/deskpet/agent/goal_store.py:27-57`（dataclass）/ `:69-113`（set/mark_done）
- Test: `backend/tests/test_goal_store_persistence.py`（新建）

**冻结依据：** [00-CONTRACT-FREEZE.md](./00-CONTRACT-FREEZE.md) §1.2 + 不变式 `done == (status == "done")`。

- [ ] **Step 1: 写失败测试（新字段默认值 + 不变式）**

`backend/tests/test_goal_store_persistence.py`（新建）：
```python
# SPDX-License-Identifier: BUSL-1.1
"""WI-1.1 / T1 — SessionGoal 扩字段 + 持久化 + 重启恢复。"""
from __future__ import annotations

import pytest

from deskpet.agent.goal_store import SessionGoal, SessionGoalStore
from deskpet.memory.session_db import SessionDB
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


def test_new_fields_have_bc_defaults():
    g = SessionGoal(session_id="s1", text="t", set_at=1.0)
    assert g.goal_id == ""          # 未落库的内存态
    assert g.status == "active"
    assert g.progress == 0.0
    assert g.criteria is None
    assert g.updated_at == 0.0
    assert g.subgoals == []
    assert g.done is False


def test_set_resets_status_and_done():
    store = SessionGoalStore()
    g = store.set("s1", "目标A")
    assert g.status == "active"
    assert g.done is False


def test_mark_done_sets_both_done_and_status():
    store = SessionGoalStore()
    store.set("s1", "目标A")
    assert store.mark_done("s1") is True
    g = store.get("s1")
    assert g.done is True
    assert g.status == "done"        # 不变式 done == (status=='done')
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_goal_store_persistence.py::test_new_fields_have_bc_defaults -v`
Expected: FAIL — `AttributeError: 'SessionGoal' object has no attribute 'goal_id'`。

- [ ] **Step 3: 扩 dataclass + set/mark_done 维护不变式**

`goal_store.py`：先在顶部 import 区补 `from dataclasses import dataclass, field`（现仅 `dataclass`），并加 `from typing import Optional`（已有则跳过）。

`SessionGoal` dataclass 改为（在现有字段后追加新字段，冻结 §1.2）：
```python
@dataclass
class SessionGoal:
    # —— 现有字段（不动，BC）——
    session_id: str
    text: str
    set_at: float
    max_iterations: int = 10
    iterations_used: int = 0
    done: bool = False
    # —— 新增（全默认值；goal_id="" 即未落库内存态，BC）——
    goal_id: str = ""
    status: str = "active"          # active | done | abandoned（落库权威）
    progress: float = 0.0
    criteria: Optional[str] = None
    updated_at: float = 0.0
    subgoals: list[str] = field(default_factory=list)
```

`set()`（`:69-90`）方法体改为构造时带 status/updated_at + goal_id：
```python
    def set(
        self,
        session_id: str,
        text: str,
        max_iterations: int = 10,
    ) -> SessionGoal:
        import uuid
        now = time.time()
        goal = SessionGoal(
            session_id=session_id,
            text=text,
            set_at=now,
            max_iterations=max_iterations,
            iterations_used=0,
            done=False,
            goal_id=uuid.uuid4().hex,
            status="active",
            updated_at=now,
        )
        self._goals[session_id] = goal
        return goal
```

`mark_done()`（`:105-113`）同时维护 status：
```python
    def mark_done(self, session_id: str) -> bool:
        goal = self._goals.get(session_id)
        if goal is None:
            return False
        goal.done = True
        goal.status = "done"
        goal.updated_at = time.time()
        return True
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_goal_store_persistence.py -v`
Expected: 3 passed。

- [ ] **Step 5: 回归现有 goal_store 调用方**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/ -k "goal" -v`
Expected: 现有 `/goal` / goal_checker 相关测试全绿（新字段全默认值 = BC）。

- [ ] **Step 6: Commit**

```bash
git add backend/deskpet/agent/goal_store.py backend/tests/test_goal_store_persistence.py
git commit -m "feat(goal-fp1): extend SessionGoal fields + done/status invariant (WI-1.1, freeze §1.2)"
```

---

## Task 4: `SessionGoalStore` 持久化（bind / _persist / load_persisted / get_goal_text）

**Files:**
- Modify: `backend/deskpet/agent/goal_store.py`（`SessionGoalStore.__init__` + 新方法）
- Test: `backend/tests/test_goal_store_persistence.py`（追加）

**冻结依据：** §1.4 只读接口 + 一致性窗口（永读内存、`_persist` safe-fail 不抛）。

- [ ] **Step 1: 写失败测试（模拟重启恢复 + safe-fail + 未 bind 纯内存）**

追加到 `test_goal_store_persistence.py`：
```python
@pytest.mark.asyncio
async def test_persist_then_reload_simulates_restart(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    # 进程 1：set + 落库
    store1 = SessionGoalStore()
    store1.bind_persistence(db)
    g = store1.set("s1", "整理三个会议纪要")
    await store1.persist(g)
    # 进程 2：新 store，load_persisted 灌回
    store2 = SessionGoalStore()
    store2.bind_persistence(db)
    await store2.load_persisted()
    restored = store2.get("s1")
    assert restored is not None
    assert restored.text == "整理三个会议纪要"
    assert restored.goal_id == g.goal_id
    assert store2.get_goal_text("s1") == "整理三个会议纪要"


def test_get_goal_text_none_safe():
    store = SessionGoalStore()
    assert store.get_goal_text("nope") is None   # 无目标 → None


@pytest.mark.asyncio
async def test_unbound_store_is_pure_memory(tmp_path):
    store = SessionGoalStore()           # 未 bind_persistence
    g = store.set("s1", "t")
    await store.persist(g)               # 不应抛（safe no-op）
    assert store.get_goal_text("s1") == "t"


@pytest.mark.asyncio
async def test_persist_failure_safe_fail(tmp_path, monkeypatch):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = SessionGoalStore()
    store.bind_persistence(db)
    g = store.set("s1", "t")

    async def _boom(**kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(db, "upsert_session_goal", _boom)
    # safe-fail：不抛，内存仍可读
    await store.persist(g)
    assert store.get_goal_text("s1") == "t"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_goal_store_persistence.py::test_persist_then_reload_simulates_restart -v`
Expected: FAIL — `AttributeError: 'SessionGoalStore' object has no attribute 'bind_persistence'`。

- [ ] **Step 3: 实现持久化方法**

`SessionGoalStore.__init__`（`:66-67`）改为持 session_db 句柄：
```python
    def __init__(self) -> None:
        self._goals: dict[str, SessionGoal] = {}
        self._session_db = None          # set via bind_persistence
        self._logger = logging.getLogger("deskpet.agent.goal_store")
```
顶部 import 区加 `import logging`。

在类内追加方法：
```python
    def bind_persistence(self, session_db: object) -> None:
        """注入 SessionDB 句柄。未调 → 退化纯内存（BC + 测试隔离）。"""
        self._session_db = session_db

    async def persist(self, goal: SessionGoal) -> None:
        """异步落库一条 goal。safe-fail：未 bind 或落库失败都不抛
        （否则 _handle_goal 改 async 后异常冒泡到 slash 处理）。
        """
        if self._session_db is None:
            return
        try:
            await self._session_db.upsert_session_goal(
                goal_id=goal.goal_id,
                session_id=goal.session_id,
                text=goal.text,
                status=goal.status,
                progress=goal.progress,
                criteria=goal.criteria,
                max_iterations=goal.max_iterations,
                iterations_used=goal.iterations_used,
                set_at=goal.set_at,
                updated_at=goal.updated_at or goal.set_at,
            )
        except Exception as exc:  # noqa: BLE001 — safe-fail
            self._logger.warning(
                "goal_store.persist failed sid=%s: %s", goal.session_id, exc,
            )

    async def load_persisted(self) -> int:
        """启动恢复：把所有 active 目标灌回内存 dict。返回恢复条数。
        未 bind → 0（BC）。每 session 取最新 active（list 已按 updated_at 倒序）。
        """
        if self._session_db is None:
            return 0
        try:
            rows = await self._session_db.list_active_goals()
        except Exception as exc:  # noqa: BLE001 — safe-fail
            self._logger.warning("goal_store.load_persisted failed: %s", exc)
            return 0
        n = 0
        for r in rows:
            sid = r["session_id"]
            if sid in self._goals:       # 已有更新的（倒序首条）→ 跳过旧的
                continue
            self._goals[sid] = SessionGoal(
                session_id=sid,
                text=r["text"],
                set_at=r["set_at"],
                max_iterations=r["max_iterations"],
                iterations_used=r["iterations_used"],
                done=(r["status"] == "done"),
                goal_id=r["goal_id"],
                status=r["status"],
                progress=r["progress"],
                criteria=r["criteria"],
                updated_at=r["updated_at"],
            )
            n += 1
        self._logger.info("goal_store.load_persisted restored=%d", n)
        return n

    def get_goal_text(self, session_id: str) -> Optional[str]:
        """冻结 §1.4：sync, None-safe, 永读内存（最新权威）。"""
        g = self._goals.get(session_id)
        return g.text if g is not None else None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_goal_store_persistence.py -v`
Expected: 全 passed（含 Task3 的 3 个 + Task4 的 4 个）。

- [ ] **Step 5: Commit**

```bash
git add backend/deskpet/agent/goal_store.py backend/tests/test_goal_store_persistence.py
git commit -m "feat(goal-fp1): SessionGoalStore persistence + load_persisted + get_goal_text (WI-1.1, freeze §1.4)"
```

---

## Task 5: T1 — `increment_iteration` 落库（重启不归零）

**Files:**
- Modify: `backend/deskpet/agent/goal_store.py`（`increment_iteration` 不动 sync 语义，新增 async 落库辅助）
- Modify: `backend/agent/agent_loop.py:1091`（increment 后 fire 落库）
- Test: `backend/tests/test_goal_store_persistence.py`（追加）

**冻结依据：** §2.1（`iterations_used` 要落库恢复，区别于 in-memory 的 auto_resume_attempts）+ 00-PLAN §14-T1。

- [ ] **Step 1: 写失败测试（increment 后落库，重启恢复非 0）**

追加到 `test_goal_store_persistence.py`：
```python
@pytest.mark.asyncio
async def test_increment_iteration_persists(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store1 = SessionGoalStore()
    store1.bind_persistence(db)
    g = store1.set("s1", "t")
    await store1.persist(g)
    # 模拟 2 轮 rebound
    store1.increment_iteration("s1")
    await store1.persist_iteration("s1")
    store1.increment_iteration("s1")
    await store1.persist_iteration("s1")
    # 重启恢复
    store2 = SessionGoalStore()
    store2.bind_persistence(db)
    await store2.load_persisted()
    assert store2.get("s1").iterations_used == 2     # 非归零
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_goal_store_persistence.py::test_increment_iteration_persists -v`
Expected: FAIL — `AttributeError: ... 'persist_iteration'`。

- [ ] **Step 3: 加 `persist_iteration` 落库辅助**

`goal_store.py` 在 `SessionGoalStore` 内追加（复用 `persist`，更新 updated_at）：
```python
    async def persist_iteration(self, session_id: str) -> None:
        """T1：把当前内存的 iterations_used 落库（重启恢复）。safe-fail。"""
        g = self._goals.get(session_id)
        if g is None:
            return
        import time as _t
        g.updated_at = _t.time()
        await self.persist(g)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_goal_store_persistence.py::test_increment_iteration_persists -v`
Expected: PASS。

- [ ] **Step 5: agent_loop 接电（increment 后 fire-and-await 落库）**

`agent_loop.py:1091`，现：
```python
                        if not _done:
                            self.session_goal_store.increment_iteration(session_id)
```
改为（紧随其后加落库；store 可能无 persist_iteration 时 getattr 防回归旧 store）：
```python
                        if not _done:
                            self.session_goal_store.increment_iteration(session_id)
                            # T1：落库 iterations_used，重启不归零。safe-fail
                            # 内置于 persist_iteration；getattr 兜底旧 store。
                            _pit = getattr(
                                self.session_goal_store,
                                "persist_iteration", None,
                            )
                            if _pit is not None:
                                await _pit(session_id)
```

- [ ] **Step 6: 回归 agent_loop**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/ -k "agent_loop or goal" -v`
Expected: 全绿（getattr 兜底 → 旧 store 无 persist_iteration 时不调，BC）。

- [ ] **Step 7: Commit**

```bash
git add backend/deskpet/agent/goal_store.py backend/agent/agent_loop.py backend/tests/test_goal_store_persistence.py
git commit -m "feat(goal-fp1): T1 persist iterations_used on rebound (restart-safe)"
```

---

## Task 6: `_handle_goal` 改 async + R-T1 lifespan 接电

**Files:**
- Modify: `backend/deskpet/commands/__init__.py:101-133`（`_handle_goal` → async + 落库）+ `:63`（dispatch 调用加 await）
- Modify: `backend/main.py:1067-1081`（构造后 bind_persistence + lifespan load_persisted，写死顺序）
- Test: `backend/tests/test_goal_store_persistence.py`（追加 handler 级）

**冻结依据：** R-T1（lifespan 接电顺序：SessionDB init → schema_v2_migrator → facts_store → goal_store.bind_persistence + await load_persisted → bind_on_goal_set[FP-4 占位] → auto_resume redispatcher → daily_decay）。本 FP 只接 bind + load_persisted，其余顺位是占位锚点。

- [ ] **Step 1: 写失败测试（handler set 落库）**

追加到 `test_goal_store_persistence.py`：
```python
from deskpet.commands import _handle_goal


@pytest.mark.asyncio
async def test_handle_goal_set_persists(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = SessionGoalStore()
    store.bind_persistence(db)
    res = await _handle_goal("整理会议纪要", "s1", store)
    assert res["type"] == "goal_set"
    # 落库验证：新 store 恢复得到
    store2 = SessionGoalStore()
    store2.bind_persistence(db)
    await store2.load_persisted()
    assert store2.get_goal_text("s1") == "整理会议纪要"


@pytest.mark.asyncio
async def test_handle_goal_disabled_when_store_none():
    res = await _handle_goal("x", "s1", None)
    assert res["type"] == "error"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_goal_store_persistence.py::test_handle_goal_set_persists -v`
Expected: FAIL — `_handle_goal` 现 sync，`await` 一个 dict 报 `TypeError`。

- [ ] **Step 3: `_handle_goal` 改 async + set/clear 落库**

`commands/__init__.py`：`def _handle_goal` → `async def _handle_goal`。set 分支落库、clear 分支落 abandoned（保留历史，冻结 §1.2/§1.7）：
```python
async def _handle_goal(
    args: str, session_id: str, store: Any,
) -> dict[str, Any]:
    if store is None:
        return {
            "type": "error",
            "message": "goal feature disabled (set [features] goal_mode = true)",
        }
    args_lower = args.lower().strip()
    if not args or args_lower == "clear" or args_lower == "":
        if args_lower == "clear":
            ok = store.clear(session_id)
            # 落 abandoned（不物理删，保留历史给 P0-3 沉淀）
            _ab = getattr(store, "persist_abandon", None)
            if _ab is not None:
                await _ab(session_id)
            return {"type": "goal_cleared", "session_id": session_id, "ok": ok}
        current = store.get(session_id)
        if current is None:
            return {"type": "goal_status", "active": False}
        return {
            "type": "goal_status",
            "active": True,
            "text": current.text,
            "iterations_used": current.iterations_used,
            "max_iterations": current.max_iterations,
            "done": current.done,
        }
    goal = store.set(session_id, args)
    _persist = getattr(store, "persist", None)
    if _persist is not None:
        await _persist(goal)
    return {
        "type": "goal_set",
        "session_id": session_id,
        "text": goal.text,
        "max_iterations": goal.max_iterations,
    }
```
> ⚠️ `clear()` 现物理 `del` 内存条目（goal_store.py:96-103）。为支持落 abandoned，先在 `clear` 取 goal 快照再 del；新增 `persist_abandon`：

`goal_store.py` 追加：
```python
    async def persist_abandon(self, session_id: str) -> None:
        """/goal clear：落 abandoned（不物理删库行）。内存已 clear，
        从落库行改 status。无 session_db / 无 goal_id → no-op。safe-fail。
        """
        if self._session_db is None:
            return
        gid = self._last_cleared_goal_id.pop(session_id, "")
        if not gid:
            return
        # 读回该行改 status（复用 upsert 覆盖）
        try:
            rows = await self._session_db.get_active_goals(session_id)
            # 注：内存已 clear 但库里仍 active，需按 goal_id 找
            import time as _t
            for r in rows:
                if r["goal_id"] == gid:
                    await self._session_db.upsert_session_goal(
                        goal_id=gid, session_id=session_id, text=r["text"],
                        status="abandoned", progress=r["progress"],
                        criteria=r["criteria"], max_iterations=r["max_iterations"],
                        iterations_used=r["iterations_used"],
                        set_at=r["set_at"], updated_at=_t.time(),
                    )
                    break
        except Exception as exc:  # noqa: BLE001 — safe-fail
            self._logger.warning("persist_abandon failed sid=%s: %s", session_id, exc)
```
并改 `clear` 记下被清的 goal_id（`__init__` 加 `self._last_cleared_goal_id: dict[str, str] = {}`）：
```python
    def clear(self, session_id: str) -> bool:
        g = self._goals.get(session_id)
        if g is not None:
            self._last_cleared_goal_id[session_id] = g.goal_id
            del self._goals[session_id]
            return True
        return False
```

- [ ] **Step 4: dispatch 调用加 await**

`commands/__init__.py:63`：
```python
    if name == "goal":
        return await _handle_goal(args, session_id, session_goal_store)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_goal_store_persistence.py -v`
Expected: 全 passed。

- [ ] **Step 6: R-T1 main.py lifespan 接电（写死顺序）**

`main.py:1073`（goal_mode ON 分支，`_session_goal_store = _GS()` 之后）追加 bind：
```python
            _session_goal_store = _GS()
            _goal_checker = _GC(llm_call=_make_str_llm_call(local_llm or cloud_llm))
            # R-T1：接电持久化。_session_db 在 :817 已构造。
            try:
                _session_goal_store.bind_persistence(_session_db)
                logger.info("goal_store_bound_persistence")
            except Exception as _bp_exc:  # noqa: BLE001
                logger.warning("goal_store_bind_persistence_failed: %s", _bp_exc)
            logger.info("companion_code_v1_goal_mode_ready")
```
然后在 **async lifespan 体内**（与 code_mode_manager 的 `await ... load_persisted` 同段，参考 main.py:1466 `await _cmm_for_restore.load_persisted(_sdb)`）追加 goal_store 恢复——R-T1 写死顺序：在 SessionDB initialize + schema_v2_migrator + facts_store 就绪之后、auto_resume redispatcher 注册之前：
```python
        # R-T1：goal_store 启动恢复（重启仍在的核心路径）。
        _gs_for_restore = service_context.get("session_goal_store")
        if _gs_for_restore is not None:
            try:
                _n = await _gs_for_restore.load_persisted()
                logger.info("goal_store_load_persisted restored=%d", _n)
            except Exception as _lp_exc:  # noqa: BLE001
                logger.warning("goal_store_load_persisted_failed: %s", _lp_exc)
```
> 实现者确认：把这段放在 lifespan 里 `_cmm_for_restore.load_persisted` 附近（同一就绪点）。`service_context.get` 取的是 :1080 register 的实例。

- [ ] **Step 7: 回归 commands + 全量 import 冒烟**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/ -k "command or goal or dispatch" -v`
Run: `cd /g/projects/deskpet/backend && python -c "import main"`（确认 main.py 语法 + import 无误）
Expected: 测试全绿；import 无异常。

- [ ] **Step 8: Commit**

```bash
git add backend/deskpet/commands/__init__.py backend/deskpet/agent/goal_store.py backend/main.py backend/tests/test_goal_store_persistence.py
git commit -m "feat(goal-fp1): async /goal persist + R-T1 lifespan load_persisted wiring"
```

---

## Task 7: WI-1.6 ToolPath 录制 + `get_completed_path`（只录不消费）

**Files:**
- Create: `backend/deskpet/agent/tool_path.py`
- Test: `backend/tests/test_tool_path_recording.py`

**冻结依据：** §1.4 `get_completed_path(session_id, goal_id) -> ToolPath | None`；00-PLAN §12「阶段 A 录制、阶段 C(FP-5) 消费」——本 FP 只交付录制 + getter，单测断言正确即验收（不误判死代码）。

> **设计：** 纯内存录制器（per-run），不落库（消费者在 FP-5，届时再决定是否持久化）。记录每个目标完成时的工具序列。先交付自闭环的 store + 数据结构 + getter，hook 进 agent_loop 留 FP-5 接（本 FP 用单测驱动 record API，避免动主 loop 引入回归）。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_tool_path_recording.py`（新建）：
```python
# SPDX-License-Identifier: BUSL-1.1
"""WI-1.6 — ToolPath 录制 + get_completed_path（只录不消费）。"""
from __future__ import annotations

from deskpet.agent.tool_path import ToolPath, ToolPathRecorder


def test_record_and_complete_path():
    rec = ToolPathRecorder()
    rec.record_tool("s1", name="file_read", ok=True)
    rec.record_tool("s1", name="ppt_create", ok=False, recovered=True)
    rec.record_tool("s1", name="ppt_create", ok=True)
    path = rec.complete("s1", goal_id="g1", goal_text="生成 PPT")
    assert isinstance(path, ToolPath)
    assert [s.name for s in path.steps] == ["file_read", "ppt_create", "ppt_create"]
    assert path.steps[1].recovered is True
    assert path.goal_text == "生成 PPT"


def test_get_completed_path_returns_recorded():
    rec = ToolPathRecorder()
    rec.record_tool("s1", name="x", ok=True)
    rec.complete("s1", goal_id="g1", goal_text="t")
    got = rec.get_completed_path("s1", "g1")
    assert got is not None
    assert got.goal_id == "g1"


def test_get_completed_path_missing_returns_none():
    rec = ToolPathRecorder()
    assert rec.get_completed_path("s1", "nope") is None


def test_complete_clears_active_buffer():
    rec = ToolPathRecorder()
    rec.record_tool("s1", name="x", ok=True)
    rec.complete("s1", goal_id="g1", goal_text="t")
    # 完成后 active buffer 清空，新目标从头录
    rec.record_tool("s1", name="y", ok=True)
    path2 = rec.complete("s1", goal_id="g2", goal_text="t2")
    assert [s.name for s in path2.steps] == ["y"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_tool_path_recording.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'deskpet.agent.tool_path'`。

- [ ] **Step 3: 实现 tool_path.py**

`backend/deskpet/agent/tool_path.py`（新建）：
```python
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""WI-1.6 — 工具路径录制（喂 FP-5 的 4.3 技能自创）。

本 FP 只录不消费：记录每个目标完成时走过的工具序列 + ok/corrected/
recovered 标记 + goal_text。消费者（4.3 触发器）在 FP-5。纯内存 per-run，
不落库（持久化由 FP-5 按需决定）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolStep:
    name: str
    ok: bool = True
    corrected: bool = False   # 被用户/反思纠正过
    recovered: bool = False   # 从错误中恢复（先 fail 后 ok）


@dataclass
class ToolPath:
    session_id: str
    goal_id: str
    goal_text: str
    steps: list[ToolStep] = field(default_factory=list)


class ToolPathRecorder:
    """per-session 活跃工具序缓冲；complete() 时快照为 ToolPath。"""

    def __init__(self) -> None:
        self._active: dict[str, list[ToolStep]] = {}
        # (session_id, goal_id) -> ToolPath
        self._completed: dict[tuple[str, str], ToolPath] = {}

    def record_tool(
        self, session_id: str, *, name: str,
        ok: bool = True, corrected: bool = False, recovered: bool = False,
    ) -> None:
        self._active.setdefault(session_id, []).append(
            ToolStep(name=name, ok=ok, corrected=corrected, recovered=recovered)
        )

    def complete(
        self, session_id: str, *, goal_id: str, goal_text: str,
    ) -> ToolPath:
        steps = self._active.pop(session_id, [])
        path = ToolPath(
            session_id=session_id, goal_id=goal_id,
            goal_text=goal_text, steps=steps,
        )
        self._completed[(session_id, goal_id)] = path
        return path

    def get_completed_path(
        self, session_id: str, goal_id: str,
    ) -> Optional[ToolPath]:
        """冻结 §1.4 契约。无记录 → None。"""
        return self._completed.get((session_id, goal_id))


__all__ = ["ToolStep", "ToolPath", "ToolPathRecorder"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /g/projects/deskpet/backend && python -m pytest tests/test_tool_path_recording.py -v`
Expected: 4 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/deskpet/agent/tool_path.py backend/tests/test_tool_path_recording.py
git commit -m "feat(goal-fp1): WI-1.6 ToolPath recorder + get_completed_path (record-only)"
```

---

## Task 8: R-T5 flag-OFF 字节基线脚本

**Files:**
- Create: `scripts/e2e_flag_off_baseline.py`

**冻结依据：** §3.5 + 00-PLAN §11/R-T5——flag 全 OFF 冷启动 + 对话，断言 `session_goals` 表不建、关键表字节快照 hash 不变（运行时验证字节级契约）。

> 本 FP 首次建立该脚本，断言 **goal_mode OFF 时 `session_goals` 表不存在**。后续 FP 复用 + 扩断言。

- [ ] **Step 1: 写脚本**

`scripts/e2e_flag_off_baseline.py`（新建）：
```python
# SPDX-License-Identifier: BUSL-1.1
"""R-T5 — flag-OFF 字节基线：goal_mode OFF 时 session_goals 不建表。

用法：python scripts/e2e_flag_off_baseline.py
退出码 0 = 基线通过；非 0 = 字节契约被破坏。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import sys
import tempfile


async def _main() -> int:
    from deskpet.memory.session_db import SessionDB

    tmp = tempfile.mkdtemp(prefix="deskpet_flagoff_")
    db_path = os.path.join(tmp, "state.db")

    # flag-OFF 模拟：SessionDB 初始化但绝不调 goal store / ensure goal 路径
    db = SessionDB(db_path=db_path)
    await db.initialize()
    # 模拟一轮对话写入（session_plans 等既有表会建，goal 表不应建）
    await db.upsert_session_plan("s1", "r", [], False)

    with sqlite3.connect(db_path) as conn:
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    if "session_goals" in names:
        print("FAIL: session_goals table created with goal_mode OFF", file=sys.stderr)
        return 1

    # 字节快照（供后续 FP 比对 hash 漂移）
    with open(db_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    print(f"PASS: flag-OFF baseline ok; no session_goals table; sha256={digest[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
```

- [ ] **Step 2: 跑脚本确认基线通过**

Run: `cd /g/projects/deskpet/backend && python ../scripts/e2e_flag_off_baseline.py`
Expected: `PASS: flag-OFF baseline ok; no session_goals table; ...`，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add scripts/e2e_flag_off_baseline.py
git commit -m "test(goal-fp1): R-T5 flag-OFF byte baseline (session_goals not created)"
```

---

## Task 9: 🚦 手测门（windows-mcp 真机 E2E）— 用户执行

> **铁律（项目 HARD CONSTRAINT）**：本门必须真模拟人，禁止 WebSocket/pytest/import 当证据。
> 截图存 `plans/manual-results-2026-06-04-FP-1/screenshots/`。E2E 等级 🔴（设目标→重启→仍在是干净的「真模拟人」证据）。
> **R-T7 隔离**：用独立 `DESKPET_USER_DATA_DIR` + `DESKPET_BACKEND_PORT`（本树 8300）+ `DESKPET_BACKEND_DIR=<worktree>/backend` + `DESKPET_PYTHON=<主.venv python>`（项目坑 #8）。

**启动（只给 Tauri 注入 env，不手动起 backend / vite，项目坑 #7/#9）：**
```powershell
$env:DESKPET_BACKEND_DIR = "G:\projects\deskpet\backend"
$env:DESKPET_BACKEND_PORT = "8300"
$env:DESKPET_USER_DATA_DIR = "G:\projects\deskpet\.tmp\fp1-userdata"
# goal_mode 必须 ON：改 config.toml [features] goal_mode = true（或 env 覆盖）
cd G:\projects\deskpet\tauri-app ; npx tauri dev
```

**TC-1.1-restart（pass^k，k=3 连跑都恢复）：**
- [ ] declare：`坐标=(输入框) | 动作=type "/goal 帮我整理本周三个会议纪要" + Enter | 期望=goal_set 卡`
- [ ] 截图 `tc-1.1-1-goal-set.png` 确认 goal_set
- [ ] 多发几轮对话让 `iterations_used` > 0（触发 goal_checker rebound）
- [ ] declare：`动作=taskkill /F /IM deskpet.exe + 重启 npx tauri dev | 期望=重启后 backend 起来`
- [ ] declare：`动作=type "/goal" + Enter | 期望=goal_status active=true text=原目标 iterations_used 非 0`
- [ ] 截图 `tc-1.1-2-after-restart.png` 确认目标仍在 + iterations 恢复
- [ ] backend log grep（抓 tauri dev 重定向 log，项目坑 #7）：`goal_store_load_persisted restored=` / `companion_code_v1_goal_mode_ready` / `goal_store_bound_persistence`
- [ ] 重复 k=3 次都恢复

**TC-1.1-flagoff（字节基线）：**
- [ ] config goal_mode = false → 冷启动 + 对话 → `python scripts/e2e_flag_off_baseline.py` → PASS（session_goals 表不建）

**判定记录格式（每 case）：**
```
case: TC-1.1-restart (k=1/2/3)
坐标: (x,y)
动作: type "/goal ..." → Enter → taskkill → 重启 → type "/goal"
截图: screenshots/tc-1.1-*.png
log 证据: goal_store_load_persisted restored=1
判定: PASS / FAIL / RETRY-N
```

---

## Task 10: STATUS 更新（过手测门后立即）

- [ ] 更新 `STATUS/status.md` §3：新增/改 FP-1 行为 ✅，记 WI-1.1 + T1 + R-T1 + WI-1.6 完成。
- [ ] 更新 `STATUS/status.md` §4：追加里程碑「FP-1 目标持久化地基过手测门」。
- [ ] 更新 `plans/2026-06-04-goal-completion-upgrade/10-EXECUTION-ROADMAP.md` §3 进度表 FP-1 行全打勾。
- [ ] 改 STATUS 顶部「最后更新」日期。
- [ ] Commit：`docs(status): FP-1 目标持久化地基通过手测门`

---

## 构建日志 / 偏离记录（2026-06-04 实施）

实现完成，后端全绿（23 焦点单测 + 267 广回归 + R-T5 基线 PASS + import main OK）。两处对原计划的偏离，已修正并记录：

- **R-T5 架构修正（commit `fd504cb`）**：原计划把 `session_goals` 放进共享 `_DDL`，但 R-T5 字节基线实跑 FAIL——`ensure_memory_v2_tables` 被 facts/session_plans 常态调用会一并建 session_goals，导致 goal_mode OFF 用户也被建空表，**违反护城河"flag-OFF DB 字节不变"**。修正：拆出独立 `ensure_session_goals_table` + 专用 cache，仅 goal store 落库时触发；3 个 SessionDB goal 方法改调它；加单测断言共享 ensure 不建该表。**冻结 §1.3 的"goal_mode OFF 表不建"由此真正落实。**
- **I-1 done 落库修正（commit `920c478`，末尾 code-review 发现）**：原计划 Task 5 只接了 `increment_iteration` 落库，漏了 `mark_done` 终态落库 → 已完成目标重启后 `load_persisted`（只查 `status='active'`）会复活成 active。修正：加 `persist_done` helper（对称 `persist_iteration`）+ agent_loop:1126 接电 + 回归测试 done→restart→不召回。
- **`_maybe_await` helper（Task 6）**：实现用 `_maybe_await` 包裹 persist 调用（兼容 MagicMock 同步 mock），比原计划的裸 `await` 更稳，保留。

> 实施过程踩坑（已记 memory）：① 一个 implementer 子代理越界把多个 task 写进工作树未提交；② 未跟踪文件被沙箱回滚消失 → 已改为"建文件即提交"。

## Self-Review（spec 覆盖核对）

| 冻结/范围项 | 实现 Task | 状态 |
|---|---|---|
| §1.2 SessionGoal 扩字段 + done/status 不变式 | T3 | ✅ |
| §1.3 session_goals DDL | T1 | ✅ |
| SessionDB 3 薄方法 | T2 | ✅ |
| §1.4 bind/persist/load_persisted/get_goal_text | T4 | ✅ |
| T1 increment_iteration 落库 | T5 | ✅ |
| R-T1 lifespan 接电（写死顺序） | T6 | ✅ |
| _handle_goal async + 落库 | T6 | ✅ |
| WI-1.6 ToolPath + get_completed_path | T7 | ✅ |
| R-T5 flag-OFF 字节基线 | T8 | ✅ |
| R-T7 多 worktree 隔离 | T9（手测门 env） | ✅ |
| 手测门（重启仍在 + iterations 恢复 + flag-OFF） | T9 | ✅ |
| STATUS 更新 | T10 | ✅ |

**待实现者确认的 2 处运行时锚点（非阻塞，实现时核）：**
1. T6 Step6：main.py lifespan 内 `await load_persisted()` 的确切落点 = `_cmm_for_restore.load_persisted` 同段（:1466 附近）。实现时 grep `load_persisted` 定位 async lifespan 体。
2. T5 Step5：`agent_loop.py:1091` 的缩进上下文（在 `if not _done:` 块内）——核对行号可能因前序编辑微移，按 `increment_iteration(session_id)` 锚定。
