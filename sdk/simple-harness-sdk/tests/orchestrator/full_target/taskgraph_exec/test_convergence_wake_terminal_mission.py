# SPDX-License-Identifier: Apache-2.0
"""已终止任务的收敛作业不再被周期性唤醒（HTN 精简 片 B 真机发现，2026-10-01）。

真机库里有一个两天前已取消的任务，它的收敛作业一直停在"等待"：每 5 秒被唤醒一次，每次都
读一遍这个任务的全部执行会话（库越大越慢，到这天一次要 5 秒），读完仍然不静止，又排下一次
——累计一万三千多次。主循环每轮开头最多处理 16 条这样的后续任务，于是新任务每轮循环要等
一两分钟，表现为"卡住"。

已终止的任务不会再提交计划，它的收敛作业没有可以等的东西：作业与围栏原样留着（不谎称
已静止），只是不再为它安排唤醒。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for extra in (HERE.parent, HERE.parent / "fixtures" / "htn"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from test_htn_end_to_end import build_world  # noqa: E402

from agent_orchestrator.orchestrator.taskgraph_wakeups import TaskGraphConvergenceWakeups  # noqa: E402
from agent_orchestrator.storage.taskgraph_followups import TaskGraphFollowupStore  # noqa: E402


def _waiting_job(store, mission_id: str, job_id: str = "tg-converge-fixture") -> None:
    """One WAITING job row.  The scheduler under test reads only this table and the Mission's
    status, so the row is written directly: the identity guard and the foreign keys (which tie
    a real job to its planning request and decision) are lifted for this one insert."""
    store.connection.execute("PRAGMA foreign_keys=OFF")
    store.connection.execute("DROP TRIGGER IF EXISTS tg_convergence_identity_guard")
    with store.transaction() as db:
        db.execute(
            "INSERT INTO taskgraph_convergence_jobs VALUES (?,?,?,?,?,?,?,?,'WAITING',2,?,?)",
            (job_id, mission_id, "pd-fixture", "request-fixture", "command-fixture", 1,
             "a" * 64, "b" * 64, 1_000, 1_000))


def _wakes(store, mission_id: str) -> int:
    return sum(1 for event in store.list_events(mission_id)
               if event.type == "TaskGraphConvergenceWakeRequested")


def test_a_waiting_job_of_a_live_mission_is_woken(tmp_path):
    world = build_world(tmp_path, key="wake-live", bound=True)
    store, mission_id = world.service.store, world.mission.id
    _waiting_job(store, mission_id)
    wakeups = TaskGraphConvergenceWakeups(TaskGraphFollowupStore(store), interval_ms=5_000)
    assert wakeups.schedule(mission_id, now_ms=10_000) == 1
    assert _wakes(store, mission_id) == 1


def test_a_waiting_job_of_a_cancelled_mission_is_not_woken_and_keeps_its_fence(tmp_path):
    world = build_world(tmp_path, key="wake-terminal", bound=True)
    store, mission_id = world.service.store, world.mission.id
    _waiting_job(store, mission_id)
    world.service.cancel_mission(mission_id)
    wakeups = TaskGraphConvergenceWakeups(TaskGraphFollowupStore(store), interval_ms=5_000)
    assert wakeups.schedule(mission_id, now_ms=10_000) == 0
    assert wakeups.schedule(mission_id, now_ms=90_000) == 0
    assert _wakes(store, mission_id) == 0
    # the job and its fence stay exactly as they were: nothing claims it settled
    row = store.connection.execute(
        "SELECT state,row_version FROM taskgraph_convergence_jobs WHERE mission_id=?", (mission_id,)).fetchone()
    assert tuple(row) == ("WAITING", 2)
