# SPDX-License-Identifier: Apache-2.0
"""已终止任务的收敛作业不再被周期性唤醒（HTN 精简 片 B 真机发现，2026-10-01）。

真机库里有一个两天前已取消的任务，它的收敛作业一直停在"等待"：每 5 秒被唤醒一次，每次都
读一遍这个任务的全部执行会话（库越大越慢，到这天一次要 5 秒），读完仍然不静止，又排下一次
——累计一万三千多次。主循环每轮开头最多处理 16 条这样的后续任务，于是新任务每轮循环要等
一两分钟，表现为"卡住"。

已终止的任务不会再提交计划，它的收敛作业没有可以等的东西：作业与围栏原样留着（不谎称
已静止），只是不再为它安排唤醒。

2026-10-03 A′：等待中的收敛作业由产品自己造出——修复时换做法（规划器为被退回的目标提 v2、
发 REPLACE_METHOD），兄弟步骤的模型调用还在半路，执行图收敛取消它的尝试后作业停在"等待"
（场景与 ``product_world/test_repair_replace_method.py`` 同一份脚本）。不再手插作业行、
不再拆掉身份守卫触发器。唤醒调度器的时钟由测试给（模拟时间流逝）。
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, NamedTuple

import pytest

_PRODUCT_WORLD = Path(__file__).resolve().parents[2] / "product_world"
if str(_PRODUCT_WORLD) not in sys.path:
    sys.path.insert(0, str(_PRODUCT_WORLD))

from test_repair_replace_method import CRITERIA, _Provider  # noqa: E402

from agent_orchestrator.orchestrator.taskgraph_wakeups import TaskGraphConvergenceWakeups  # noqa: E402
from agent_orchestrator.storage.taskgraph_followups import TaskGraphFollowupStore  # noqa: E402
from agent_orchestrator.testing.product_world import ProductWorld, product_world  # noqa: E402

MINUTE_MS = 60_000


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


class Waiting(NamedTuple):
    world: ProductWorld
    mission_id: str
    job_id: str
    provider: Any


def _job(store: Any, mission_id: str) -> Any:
    return store.connection.execute(
        "SELECT job_id,state,row_version FROM taskgraph_convergence_jobs WHERE mission_id=? "
        "ORDER BY created_at DESC LIMIT 1", (mission_id,)).fetchone()


def _cancelled_by_convergence(store: Any, mission_id: str) -> bool:
    return any(event.type == "AttemptCancelled"
               and str(event.payload.get("reason", "")).startswith("taskgraph_convergence:")
               for event in store.list_events(mission_id))


def _open_followups(store: Any, mission_id: str) -> int:
    return int(store.connection.execute(
        "SELECT COUNT(*) FROM taskgraph_followups WHERE mission_id=? AND delivery_state<>'ACKED'",
        (mission_id,)).fetchone()[0])


@asynccontextmanager
async def waiting_convergence(tmp_path: Path, *, key: str) -> AsyncIterator[Waiting]:
    """A replacement's convergence job left WAITING: the sibling step's model call is still in
    flight (its Attempt already cancelled by the convergence).  The main loop is stopped at
    that point with every TaskGraph followup delivered; the slow call stays held until exit."""

    provider = _Provider()
    try:
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "整理事实并写笔记", "idempotency_key": key,
                                       "success_criteria": list(CRITERIA)})["mission_id"]
            store = world.loop.store
            stop = asyncio.Event()

            async def drive() -> None:
                while not stop.is_set():
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=True)
                    await asyncio.sleep(0.05)

            runner = asyncio.create_task(drive())
            try:
                async with asyncio.timeout(60):
                    while not (_cancelled_by_convergence(store, mission_id)
                               and _job(store, mission_id) is not None
                               and _job(store, mission_id)["state"] == "WAITING"
                               and _open_followups(store, mission_id) == 0):
                        if runner.done():
                            runner.result()
                        await asyncio.sleep(0.05)
            finally:
                stop.set()
                runner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runner
            job = _job(store, mission_id)
            assert job["state"] == "WAITING", tuple(job)
            yield Waiting(world, mission_id, str(job["job_id"]), provider)
    finally:
        provider.late.set()


def _wakes(store: Any, mission_id: str) -> int:
    return sum(1 for event in store.list_events(mission_id)
               if event.type == "TaskGraphConvergenceWakeRequested")


def test_a_waiting_job_of_a_live_mission_is_woken(tmp_path):
    async def case() -> None:
        async with waiting_convergence(tmp_path, key="wake-live") as waiting:
            store, mission_id = waiting.world.store, waiting.mission_id
            before = _wakes(store, mission_id)
            wakeups = TaskGraphConvergenceWakeups(TaskGraphFollowupStore(store), interval_ms=5_000)
            later = int(store.now * 1000) + 10 * MINUTE_MS
            assert wakeups.schedule(mission_id, now_ms=later) == 1
            assert _wakes(store, mission_id) == before + 1

    asyncio.run(case())


def test_a_waiting_job_of_a_cancelled_mission_is_not_woken_and_keeps_its_fence(tmp_path):
    async def case() -> None:
        async with waiting_convergence(tmp_path, key="wake-terminal") as waiting:
            store, mission_id = waiting.world.store, waiting.mission_id
            fence = tuple(_job(store, mission_id))
            waiting.world.control.cancel(mission_id)
            assert str(store.get_mission(mission_id).status.value) == "CANCELLED"
            before = _wakes(store, mission_id)
            wakeups = TaskGraphConvergenceWakeups(TaskGraphFollowupStore(store), interval_ms=5_000)
            now = int(store.now * 1000)
            assert wakeups.schedule(mission_id, now_ms=now + 10 * MINUTE_MS) == 0
            assert wakeups.schedule(mission_id, now_ms=now + 90 * MINUTE_MS) == 0
            assert _wakes(store, mission_id) == before
            # the job and its fence stay exactly as they were: nothing claims it settled
            assert tuple(_job(store, mission_id)) == fence

    asyncio.run(case())


def _followup_states(store: Any, mission_id: str) -> list[tuple[str, int]]:
    return [tuple(row) for row in store.connection.execute(
        "SELECT delivery_state, attempts FROM taskgraph_followups WHERE mission_id=? ORDER BY message_id",
        (mission_id,))]


def test_an_isolated_missions_followups_are_left_exactly_as_they_are(tmp_path):
    """夜间 N2（终核 8.5 建议）：重启核对没通过、已隔离的任务，执行图跟进的投递泵不认领它的跟进
    ——不写见证、不写新计划版本、不开审阅工作，跟进原样留在库里（状态与次数都不变）。

    **改坏检验**：投递泵不传 ``excluded_missions`` → 跟进被认领（次数 +1 或已投递）→ 本条失败。"""

    async def case() -> None:
        async with waiting_convergence(tmp_path, key="followup-isolated") as waiting:
            loop, mission_id = waiting.world.loop, waiting.mission_id
            store = loop.store
            wakeups = TaskGraphConvergenceWakeups(TaskGraphFollowupStore(store), interval_ms=5_000)
            assert wakeups.schedule(mission_id, now_ms=int(store.now * 1000) + 10 * MINUTE_MS) == 1
            notifications = loop._taskgraph_notifications
            assert notifications._consume_one(mission_id) >= 1  # 唤醒事件已派生成待投递的跟进
            before = _followup_states(store, mission_id)
            assert any(state == "PENDING" for state, _ in before), before
            loop._recovery_isolated[mission_id] = {"tables": ["fixture"], "silent_changes": []}
            delivered = await notifications.pump.pump_taskgraph_followups(limit=16)
            assert all(item.message_id not in {m for m, in store.connection.execute(
                "SELECT message_id FROM taskgraph_followups WHERE mission_id=?", (mission_id,))}
                       for item in delivered)
            assert _followup_states(store, mission_id) == before

    asyncio.run(case())
