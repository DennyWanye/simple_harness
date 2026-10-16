# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Todos, plans, goals, goal tasks, code sessions and supervisor hints."""

from __future__ import annotations

import json
import time
import time as _time
from typing import Any

import aiosqlite

from deskpet.memory.memory_v2_schema import (
    ensure_goal_tasks_table,
    ensure_memory_v2_tables,
    ensure_session_goals_table,
)

from .core import SessionDBCore
from .support import log


class WorkItemMixin(SessionDBCore):
    """Todos, plans, goals, goal tasks, code sessions and supervisor hints."""

    __slots__ = ()

    # ------------------------------------------------------------------
    # Todo list per session
    # ------------------------------------------------------------------

    async def replace_session_todos(
        self,
        session_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        """Replace an ordinary session's full todo projection.

        The on-disk table retains its historical name for migration
        compatibility; production callers use this mode-neutral API.
        """

        await self.replace_code_todos(session_id, items)

    async def get_session_todos(self, session_id: str) -> list[dict[str, Any]]:
        """Return an ordinary session's todo projection."""

        return await self.get_code_todos(session_id)

    async def replace_code_todos(
        self,
        session_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        """Replace the entire todo list for ``session_id`` atomically.

        ``items`` is a list of dicts shaped like::

            {
                "content": "Implement A",
                "activeForm": "Implementing A",
                "status": "pending" | "in_progress" | "completed",
            }

        Mirrors full-list TodoWrite semantics: the tool is
        idempotent — every call replaces the full list, the LLM doesn't
        track diffs. Sort order is preserved by index in ``items``.
        """
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        "DELETE FROM code_todos WHERE session_id = ? AND workflow_run_id IS NULL",
                        (session_id,),
                    )
                    for idx, item in enumerate(items):
                        status = item.get("status", "pending")
                        if status not in {"pending", "in_progress", "completed"}:
                            status = "pending"
                        await db.execute(
                            """
                            INSERT INTO code_todos
                              (session_id, content, active_form, status, sort_order)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                session_id,
                                str(item.get("content", ""))[:2000],
                                str(item.get("activeForm", ""))[:2000],
                                status,
                                idx,
                            ),
                        )
                    await db.commit()

        await self._with_retry(_do)

    async def get_code_todos(self, session_id: str) -> list[dict[str, Any]]:
        """Return the current todo list for ``session_id`` in render order."""
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                cursor = await db.execute(
                    """
                    SELECT content, active_form, status, sort_order,
                           workflow_run_id, workflow_step_id
                    FROM code_todos
                    WHERE session_id = ?
                    ORDER BY sort_order
                    """,
                    (session_id,),
                )
                rows = await cursor.fetchall()
                await cursor.close()
                return [
                    {
                        "content": row[0],
                        "activeForm": row[1],
                        "status": row[2],
                        "sort_order": row[3],
                        "workflow_run_id": row[4],
                        "workflow_step_id": row[5],
                    }
                    for row in rows
                ]

        return await self._with_retry(_do)

    async def sync_workflow_code_todos(
        self,
        session_id: str,
        workflow_run_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        """Project one workflow run's stable steps without touching legacy rows."""
        if not self._initialized:
            await self.initialize()
        run_id = str(workflow_run_id or "").strip()
        if not run_id:
            raise ValueError("workflow_run_id must be non-empty")

        normalized: list[tuple[str, str, str, str, int]] = []
        seen_steps: set[str] = set()
        for index, item in enumerate(items):
            step_id = str(item.get("workflow_step_id") or "").strip()
            if not step_id:
                raise ValueError(f"items[{index}].workflow_step_id is required")
            if step_id in seen_steps:
                raise ValueError(f"duplicate workflow_step_id: {step_id}")
            seen_steps.add(step_id)
            status = str(item.get("status", "pending"))
            if status not in {"pending", "in_progress", "completed"}:
                status = "pending"
            normalized.append(
                (
                    step_id,
                    str(item.get("content", ""))[:2000],
                    str(item.get("activeForm", item.get("active_form", "")))[:2000],
                    status,
                    int(item.get("sort_order", index)),
                )
            )

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    for step_id, content, active_form, status, sort_order in normalized:
                        await db.execute(
                            """
                            INSERT INTO code_todos(
                                session_id, content, active_form, status, sort_order,
                                workflow_run_id, workflow_step_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(workflow_run_id, workflow_step_id)
                            WHERE workflow_step_id IS NOT NULL DO UPDATE SET
                                session_id=excluded.session_id,
                                content=excluded.content,
                                active_form=excluded.active_form,
                                status=excluded.status,
                                sort_order=excluded.sort_order,
                                updated_at=julianday('now')
                            """,
                            (
                                session_id,
                                content,
                                active_form,
                                status,
                                sort_order,
                                run_id,
                                step_id,
                            ),
                        )
                    if seen_steps:
                        placeholders = ",".join("?" for _ in seen_steps)
                        await db.execute(
                            "DELETE FROM code_todos "
                            "WHERE session_id = ? AND workflow_run_id = ? "
                            f"AND workflow_step_id NOT IN ({placeholders})",
                            (session_id, run_id, *sorted(seen_steps)),
                        )
                    else:
                        await db.execute(
                            "DELETE FROM code_todos WHERE session_id = ? AND workflow_run_id = ?",
                            (session_id, run_id),
                        )
                    await db.commit()

        await self._with_retry(_do)

    async def upsert_code_session(
        self,
        *,
        base_session_id: str,
        code_session_id: str,
        project_root: str,
        project_name: str,
    ) -> None:
        """Reject new writes to the retired legacy project authority."""

        del base_session_id, code_session_id, project_root, project_name
        raise RuntimeError("code_session_authority_retired")

    # ── FEAT-A4 (superpowers): plan-confirm 硬门 awaiting plan sidecar ──
    # F5/HMR rehydration 从 SessionDB 重载会丢前端临时的 awaiting plan
    # 消息（[执行]/[取消] 按钮消失）。这三个方法把 awaiting plan 持久化到
    # session_plans sidecar 表，使面板重载后可恢复。不走 append_message →
    # 不触发 VectorWorker embed / FTS5，plan JSON 不污染语义检索。

    async def upsert_session_plan(
        self,
        session_id: str,
        rationale: str,
        steps: list[dict[str, Any]],
        awaiting: bool,
        *,
        target_directory: str | None = None,
        action_categories: list[str] | None = None,
        auto_confirmed: bool = False,
    ) -> None:
        """记一条 awaiting plan（同 session 覆盖）。

        关键（SW-1）：先确保 DB 初始化 + session_plans 表就绪，使得在
        「从未调过 ensure 的全新 DB」上 upsert 也能自带建表，而不是静默
        被「no such table」吞掉。
        """
        if not self._initialized:
            await self.initialize()
        # 自带建表（幂等）— 全新 DB 上保证 session_plans 存在。

        await ensure_memory_v2_tables(self._db_path)

        steps_json = json.dumps(
            {
                "steps": steps or [],
                "target_directory": target_directory,
                "action_categories": action_categories or [],
                "auto_confirmed": bool(auto_confirmed),
            },
            ensure_ascii=False,
        )
        now = time.time()

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        """
                        INSERT INTO session_plans(
                            session_id, rationale, steps_json, awaiting, ts
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(session_id) DO UPDATE SET
                            rationale  = excluded.rationale,
                            steps_json = excluded.steps_json,
                            awaiting   = excluded.awaiting,
                            ts         = excluded.ts
                        """,
                        (
                            session_id,
                            rationale or "",
                            steps_json,
                            1 if awaiting else 0,
                            now,
                        ),
                    )
                    await db.commit()

        await self._with_retry(_do)

    async def get_session_plan(self, session_id: str) -> dict[str, Any] | None:
        """读 awaiting plan；无行返 None。steps 已 json.loads，awaiting 已 bool。"""
        if not self._initialized:
            await self.initialize()

        await ensure_memory_v2_tables(self._db_path)

        async def _do() -> dict[str, Any] | None:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    """
                    SELECT session_id, rationale, steps_json, awaiting, ts
                    FROM session_plans WHERE session_id = ?
                    """,
                    (session_id,),
                )
                row = await cursor.fetchone()
                await cursor.close()
                if row is None:
                    return None
                try:
                    decoded = json.loads(row[2]) if row[2] else []
                except (ValueError, TypeError):
                    decoded = []
                if isinstance(decoded, dict):
                    steps = decoded.get("steps")
                    if not isinstance(steps, list):
                        steps = []
                    target_directory = decoded.get("target_directory")
                    action_categories = decoded.get("action_categories")
                    if not isinstance(action_categories, list):
                        action_categories = []
                    auto_confirmed = bool(decoded.get("auto_confirmed", False))
                else:
                    # Legacy rows stored a bare steps array.
                    steps = decoded if isinstance(decoded, list) else []
                    target_directory = None
                    action_categories = []
                    auto_confirmed = False
                return {
                    "session_id": row[0],
                    "rationale": row[1] or "",
                    "steps": steps,
                    "target_directory": target_directory,
                    "action_categories": action_categories,
                    "auto_confirmed": auto_confirmed,
                    "awaiting": bool(row[3]),
                    "ts": row[4],
                }

        return await self._with_retry(_do)

    async def clear_session_plan_awaiting(self, session_id: str) -> None:
        """清 awaiting 标记（UPDATE awaiting=0）。幂等，可重复调（无行也安全）。"""
        if not self._initialized:
            await self.initialize()

        await ensure_memory_v2_tables(self._db_path)

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        "UPDATE session_plans SET awaiting = 0 WHERE session_id = ?",
                        (session_id,),
                    )
                    await db.commit()

        await self._with_retry(_do)

    # ───────────────────── goal-completion FP-1 (WI-1.1) ──────────────
    async def upsert_session_goal(
        self,
        *,
        goal_id: str,
        session_id: str,
        text: str,
        status: str,
        progress: float,
        criteria: str | None,
        max_iterations: int,
        iterations_used: int,
        set_at: float,
        updated_at: float,
    ) -> None:
        """落一条 goal（同 goal_id 覆盖）。同构 upsert_session_plan：
        自带建表（全新 DB 也能 upsert）+ _write_lock + _with_retry。
        ⚠️ 用 goal 专用 ensure（非共享 ensure_memory_v2_tables），守 flag-OFF
        字节基线：goal_mode OFF 不落库 → session_goals 表永不建（R-T5）。
        """
        if not self._initialized:
            await self.initialize()

        await ensure_session_goals_table(self._db_path)

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
                            goal_id,
                            session_id,
                            text,
                            status,
                            progress,
                            criteria,
                            max_iterations,
                            iterations_used,
                            set_at,
                            updated_at,
                        ),
                    )
                    await db.commit()

        await self._with_retry(_do)

    async def project_goal_terminal(self, goal_id: str, status: str) -> bool:
        """Strict, idempotent CAS used by durable Goal delivery sinks."""
        if status not in {"done", "abandoned"}:
            raise ValueError("invalid Goal terminal status")
        if not self._initialized:
            await self.initialize()

        await ensure_session_goals_table(self._db_path)

        async def _do() -> bool:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        "UPDATE session_goals SET status=?,updated_at=? "
                        "WHERE goal_id=? AND status='active'",
                        (status, time.time(), goal_id),
                    )
                    row = await (
                        await db.execute(
                            "SELECT status FROM session_goals WHERE goal_id=?",
                            (goal_id,),
                        )
                    ).fetchone()
                    await db.commit()
                    return row is not None and str(row[0]) == status

        return await self._with_retry(_do)

    async def get_active_goals(self, session_id: str) -> list[dict[str, Any]]:
        """读某 session 的 active 目标，updated_at 倒序（最新在前）。"""
        if not self._initialized:
            await self.initialize()

        await ensure_session_goals_table(self._db_path)

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

        await ensure_session_goals_table(self._db_path)

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

    # ───────────────────── goal-completion FP-2 (WI-1.2) ─────────────
    # goal_tasks CRUD + atomic claim.
    # ⚠️ 用 goal_tasks 专用 ensure（非共享 ensure_memory_v2_tables），
    # 守 flag-OFF 字节基线：goal_mode OFF 不落库 → goal_tasks 表永不建（R-T5）。

    async def create_goal_task(
        self,
        *,
        task_id: str,
        goal_id: str,
        session_id: str,
        title: str,
        depends_on: list[str],
        created_at: float,
        updated_at: float,
    ) -> None:
        """Insert a new goal_task row. depends_on stored as JSON."""
        if not self._initialized:
            await self.initialize()

        await ensure_goal_tasks_table(self._db_path)

        depends_json = json.dumps(depends_on, ensure_ascii=False)

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        """
                        INSERT INTO goal_tasks(
                            task_id, goal_id, session_id, title, status,
                            depends_on, claimed_by, result,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'pending', ?, NULL, NULL, ?, ?)
                        """,
                        (
                            task_id,
                            goal_id,
                            session_id,
                            title,
                            depends_json,
                            created_at,
                            updated_at,
                        ),
                    )
                    await db.commit()

        await self._with_retry(_do)

    async def get_goal_task(self, task_id: str) -> dict[str, Any] | None:
        """Read a single goal_task by task_id; None if not found."""
        if not self._initialized:
            await self.initialize()

        await ensure_goal_tasks_table(self._db_path)

        async def _do() -> dict[str, Any] | None:
            async with aiosqlite.connect(self._db_path) as db:
                cur = await db.execute(
                    """
                    SELECT task_id, goal_id, session_id, title, status,
                           depends_on, claimed_by, result, created_at, updated_at
                    FROM goal_tasks WHERE task_id = ?
                    """,
                    (task_id,),
                )
                row = await cur.fetchone()
                await cur.close()
                if row is None:
                    return None
                return self._goal_task_row_to_dict(row)

        return await self._with_retry(_do)

    async def list_goal_tasks(self, goal_id: str) -> list[dict[str, Any]]:
        """List all tasks for a goal, ordered by created_at ASC."""
        if not self._initialized:
            await self.initialize()

        await ensure_goal_tasks_table(self._db_path)

        async def _do() -> list[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                cur = await db.execute(
                    """
                    SELECT task_id, goal_id, session_id, title, status,
                           depends_on, claimed_by, result, created_at, updated_at
                    FROM goal_tasks
                    WHERE goal_id = ?
                    ORDER BY created_at ASC
                    """,
                    (goal_id,),
                )
                rows = await cur.fetchall()
                await cur.close()
                return [self._goal_task_row_to_dict(r) for r in rows]

        return await self._with_retry(_do)

    async def update_goal_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        result: str | None = None,
        claimed_by: str | None = None,
        updated_at: float,
    ) -> None:
        """Partial update: only non-None kwargs are SET."""
        if not self._initialized:
            await self.initialize()

        await ensure_goal_tasks_table(self._db_path)

        # Build SET clause dynamically
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [updated_at]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if result is not None:
            sets.append("result = ?")
            params.append(result)
        if claimed_by is not None:
            sets.append("claimed_by = ?")
            params.append(claimed_by)
        params.append(task_id)

        set_clause = ", ".join(sets)

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        f"UPDATE goal_tasks SET {set_clause} WHERE task_id = ?",
                        tuple(params),
                    )
                    await db.commit()

        await self._with_retry(_do)

        # If marking done, backfill progress on session_goals
        if status == "done":
            await self._backfill_goal_progress(task_id, updated_at)

    async def _backfill_goal_progress(self, task_id: str, now: float) -> None:
        """After a task is marked done, recompute and persist progress
        = done_count / total for the owning goal.
        """
        task_row = await self.get_goal_task(task_id)
        if task_row is None:
            return
        goal_id = task_row["goal_id"]
        session_id = task_row["session_id"]
        tasks = await self.list_goal_tasks(goal_id)
        if not tasks:
            return
        total = len(tasks)
        done = sum(1 for t in tasks if t["status"] == "done")
        progress = done / total

        # Read the current goal row to preserve its other fields
        goals = await self.get_active_goals(session_id)
        goal_row = next((g for g in goals if g["goal_id"] == goal_id), None)
        if goal_row is None:
            # Goal might not exist (no session_goals row) — skip safely
            return

        try:
            await self.upsert_session_goal(
                goal_id=goal_id,
                session_id=session_id,
                text=goal_row["text"],
                status=goal_row["status"],
                progress=progress,
                criteria=goal_row["criteria"],
                max_iterations=goal_row["max_iterations"],
                iterations_used=goal_row["iterations_used"],
                set_at=goal_row["set_at"],
                updated_at=now,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("goal progress backfill failed: %s", exc)

    async def claim_ready_goal_task(
        self,
        goal_id: str,
        agent_id: str,
        now: float,
    ) -> dict[str, Any] | None:
        """ATOMIC claim under _write_lock.

        (1) SELECT all tasks for goal_id
        (2) In Python, find first `status='pending'` whose `depends_on` are
            ALL `status='done'`
        (3) If found, UPDATE that row SET status='claimed', claimed_by=agent_id,
            updated_at=now WHERE task_id=? AND status='pending'
            (guard against race between coroutines)
        (4) Return the claimed row dict or None.

        Same-process asyncio.Lock (per T5 spike conclusion) ensures
        concurrent coroutines serialize.
        """
        if not self._initialized:
            await self.initialize()

        await ensure_goal_tasks_table(self._db_path)

        async def _do() -> dict[str, Any] | None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    # Load all tasks for this goal
                    cur = await db.execute(
                        """
                        SELECT task_id, goal_id, session_id, title, status,
                               depends_on, claimed_by, result, created_at, updated_at
                        FROM goal_tasks WHERE goal_id = ?
                        ORDER BY created_at ASC
                        """,
                        (goal_id,),
                    )
                    rows = await cur.fetchall()
                    await cur.close()

                    if not rows:
                        return None

                    # Build status index for dependency check
                    # Column indices: 0=task_id, 4=status, 5=depends_on
                    status_map: dict[str, str] = {}
                    for r in rows:
                        status_map[r[0]] = r[4]

                    # Find first pending task whose all deps are done
                    candidate = None
                    for r in rows:
                        if r[4] != "pending":
                            continue
                        try:
                            deps: list[str] = json.loads(r[5]) if r[5] else []
                        except (ValueError, TypeError):
                            deps = []
                        if all(status_map.get(d) == "done" for d in deps):
                            candidate = r
                            break

                    if candidate is None:
                        return None

                    # Attempt the claim with status='pending' guard
                    cur2 = await db.execute(
                        """
                        UPDATE goal_tasks
                        SET status='claimed', claimed_by=?, updated_at=?
                        WHERE task_id=? AND status='pending'
                        """,
                        (agent_id, now, candidate[0]),
                    )
                    await db.commit()
                    rows_affected = cur2.rowcount or 0
                    await cur2.close()

                    if rows_affected == 0:
                        # Another coroutine claimed it between our SELECT and UPDATE
                        return None

                    # Return the updated row
                    cur3 = await db.execute(
                        """
                        SELECT task_id, goal_id, session_id, title, status,
                               depends_on, claimed_by, result, created_at, updated_at
                        FROM goal_tasks WHERE task_id = ?
                        """,
                        (candidate[0],),
                    )
                    final_row = await cur3.fetchone()
                    await cur3.close()
                    return self._goal_task_row_to_dict(final_row) if final_row else None

        return await self._with_retry(_do)

    @staticmethod
    def _goal_task_row_to_dict(row: Any) -> dict[str, Any]:
        """Convert a goal_tasks SELECT row to dict.
        Column order must match SELECT statement in all queries above:
          0=task_id, 1=goal_id, 2=session_id, 3=title, 4=status,
          5=depends_on, 6=claimed_by, 7=result, 8=created_at, 9=updated_at
        """
        try:
            depends_on: list[str] = json.loads(row[5]) if row[5] else []
        except (ValueError, TypeError):
            depends_on = []
        return {
            "task_id": row[0],
            "goal_id": row[1],
            "session_id": row[2],
            "title": row[3],
            "status": row[4],
            "depends_on": depends_on,
            "claimed_by": row[6],
            "result": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }

    async def list_code_sessions(self) -> list[dict[str, Any]]:
        """P4-S25 B4: read the persisted project list, newest first.

        Used by CodeModeManager.load_persisted() at startup to repopulate
        the in-memory state map. Order doesn't strictly matter (both
        sidebar and dashboard sort their own way) but newest-first is
        a sensible default if anyone iterates raw.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> list[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    """
                    SELECT base_session_id, code_session_id,
                           project_root, project_name,
                           created_at, last_active_at
                    FROM code_sessions
                    ORDER BY last_active_at DESC
                    """
                )
                rows = await cursor.fetchall()
                await cursor.close()
                return [
                    {
                        "base_session_id": r[0],
                        "code_session_id": r[1],
                        "project_root": r[2],
                        "project_name": r[3],
                        "created_at": r[4],
                        "last_active_at": r[5],
                    }
                    for r in rows
                ]

        return await self._with_retry(_do)

    async def delete_code_session(self, base_session_id: str) -> int:
        """P4-S25 B4: remove a persisted project enrollment.

        Called from the `code_session_delete` IPC handler alongside
        delete_code_todos. We do NOT cascade-delete `messages` rows —
        chat history for that code_session_id stays in the DB so re-
        adding the same project root later resumes the same thread.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> int:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "DELETE FROM code_sessions WHERE base_session_id = ?",
                        (base_session_id,),
                    )
                    deleted = cursor.rowcount or 0
                    await cursor.close()
                    await db.commit()
                    return int(deleted)

        return await self._with_retry(_do)

    # ─────────────────────────────────────────────────────────────────
    # P5-S1: supervisor_hints — audit log for the watchdog's interventions.
    # Every nudge / ask_user / cancel_coerced action emitted by the
    # supervisor LLM gets a row here, plus a follow-up ``user_choice``
    # row when the user clicks a bubble button. Used for: debugging
    # ("why did agent suddenly try a different approach?"), Settings UI
    # count badges, and a future cost-estimate feature.
    # ─────────────────────────────────────────────────────────────────

    async def append_supervisor_hint(
        self,
        *,
        session_id: str,
        alert_id: str,
        hint_text: str,
        action: str,
        severity: str,
        diagnosis: str = "",
        user_button: str | None = None,
        ts: int | None = None,
    ) -> int:
        """Insert one supervisor_hints row. Returns the new row id."""
        if not self._initialized:
            await self.initialize()

        ts_val = int(ts if ts is not None else _time.time())

        async def _do() -> int:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        """
                        INSERT INTO supervisor_hints(
                            session_id, alert_id, hint_text, action, severity,
                            diagnosis, user_button, ts
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            alert_id,
                            hint_text,
                            action,
                            severity,
                            diagnosis,
                            user_button,
                            ts_val,
                        ),
                    )
                    new_id = int(cursor.lastrowid or 0)
                    await cursor.close()
                    await db.commit()
                    return new_id

        return await self._with_retry(_do)

    async def list_supervisor_hints(
        self,
        *,
        session_id: str | None = None,
        since_ts: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read supervisor audit rows newest-first.

        Filter by ``session_id`` to scope to one session; ``since_ts``
        for "today's interventions" style queries; ``limit`` caps result
        set so a long-lived backend doesn't dump 10K rows over IPC.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> list[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                conditions: list[str] = []
                params: list[Any] = []
                if session_id is not None:
                    conditions.append("session_id = ?")
                    params.append(session_id)
                if since_ts is not None:
                    conditions.append("ts >= ?")
                    params.append(int(since_ts))
                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
                params.append(int(limit))
                cursor = await db.execute(
                    f"""
                    SELECT id, session_id, alert_id, hint_text, action,
                           severity, diagnosis, user_button, ts
                    FROM supervisor_hints
                    {where}
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    tuple(params),
                )
                rows = await cursor.fetchall()
                await cursor.close()
                return [
                    {
                        "id": r[0],
                        "session_id": r[1],
                        "alert_id": r[2],
                        "hint_text": r[3],
                        "action": r[4],
                        "severity": r[5],
                        "diagnosis": r[6],
                        "user_button": r[7],
                        "ts": r[8],
                    }
                    for r in rows
                ]

        return await self._with_retry(_do)

    async def count_supervisor_hints(
        self,
        *,
        session_id: str | None = None,
        since_ts: int | None = None,
    ) -> int:
        """Count audit rows matching filter (used by Settings UI badge)."""
        if not self._initialized:
            await self.initialize()

        async def _do() -> int:
            async with aiosqlite.connect(self._db_path) as db:
                conditions: list[str] = []
                params: list[Any] = []
                if session_id is not None:
                    conditions.append("session_id = ?")
                    params.append(session_id)
                if since_ts is not None:
                    conditions.append("ts >= ?")
                    params.append(int(since_ts))
                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
                cursor = await db.execute(
                    f"SELECT COUNT(*) FROM supervisor_hints {where}",
                    tuple(params),
                )
                row = await cursor.fetchone()
                await cursor.close()
                return int(row[0]) if row else 0

        return await self._with_retry(_do)

    async def delete_code_todos(self, session_id: str) -> int:
        """P4-S24 followup: wipe all todos for a code session.

        Used by ``code_session_delete`` IPC when the user removes a
        project from the code panel. Returns the number of rows deleted
        (0 if the session had none).

        We deliberately do NOT touch ``messages`` rows — chat history
        for the project survives so the user can resume the same
        ``code-<sha>`` session later if they re-add the same project
        root. Same philosophy as ``CodeModeManager.exit``.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> int:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "DELETE FROM code_todos WHERE session_id = ?",
                        (session_id,),
                    )
                    deleted = cursor.rowcount or 0
                    await cursor.close()
                    await db.commit()
                    return int(deleted)

        return await self._with_retry(_do)
