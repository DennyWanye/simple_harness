from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from h1i_seed import committed, root_task

from agent_orchestrator.contracts import TaskStatus


def test_planner_task_state_projection_holds_one_write_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A competing process cannot split outcome and Task-state collection.

    The competing UPDATE is attempted after ``occurrence_outcomes`` has read the
    authoritative execution outcome but before the package collector reads the Task
    row again.  The Planner-intent wrapper must already hold BEGIN IMMEDIATE, so the
    second connection is refused and the frozen package carries one coherent state.

    The Task is the first plan revision's leaf, committed and dispatched by the main
    loop; its executor's call is held, so the leaf is running when the planner's next
    request is built.
    """

    async def case() -> None:
        async with committed(tmp_path, key="h1i-wait-snapshot") as (loop, mission, _world, _root, dispatch, _product):
            leaves = [
                task for task in loop.store.list_tasks(mission.id)
                if task.id != root_task(mission.id) and task.status is TaskStatus.ACTIVE
            ]
            assert len(leaves) == 1, leaves
            task = leaves[0]
            failed = replace(task, status=TaskStatus.FAILED, version=task.version + 1)
            original = dispatch.occurrence_outcomes
            lock_refused = False

            def outcomes_with_competing_writer(mission_id: str, network: object):
                nonlocal lock_refused
                outcomes = original(mission_id, network)
                contender = sqlite3.connect(
                    loop.store.path, timeout=0, isolation_level=None
                )
                try:
                    with pytest.raises(sqlite3.OperationalError, match="locked"):
                        contender.execute(
                            "UPDATE tasks SET status = ?, version = ?, json = ?, "
                            "updated_at = updated_at + 1 WHERE task_id = ?",
                            (
                                str(failed.status),
                                failed.version,
                                json.dumps(
                                    failed.to_json(),
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                                failed.id,
                            ),
                        )
                    lock_refused = True
                finally:
                    contender.close()
                return outcomes

            monkeypatch.setattr(dispatch, "occurrence_outcomes", outcomes_with_competing_writer)
            intent = await loop._create_planner_intent(mission.id, ordinal=3)

            assert lock_refused
            stored = loop.store.get_task(task.id)
            assert stored is not None
            assert stored.status is TaskStatus.ACTIVE
            assert stored.version == task.version
            rows = [
                row for row in intent.config["planning_package"]["views"]["goals"]
                if row["task_id"] == task.id
            ]
            assert rows
            assert {
                (row["task_status"], row["task_version"], row["occurrence_outcome"])
                for row in rows
            } == {(str(TaskStatus.ACTIVE), task.version, "RUNNING")}

    asyncio.run(case())
