# SPDX-License-Identifier: Apache-2.0
"""HTN 补齐阶段 A：§10.1 辅助事件"收敛已推进"（TaskGraphConvergenceAdvanced）。

收敛作业的状态每真正变化一次，同一事务里写一条事件，身份由作业与新行版本决定；状态没变的
重复推进不写。这条事件和"派发已绑定"都不进重查事件集，免得自己触发自己循环。

2026-10-03 A′：收敛作业由产品自己造出（修复时换做法、兄弟步骤的调用还在半路，见
``test_convergence_wake_terminal_mission.waiting_convergence``），不再手插作业行、不再用放行一切的
权限替身推进作业；作业由主循环自己推进到完成。
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from test_convergence_wake_terminal_mission import waiting_convergence  # noqa: E402

from agent_orchestrator.orchestrator import taskgraph_notifications  # noqa: E402
from agent_orchestrator.planning.htn.grounding import derive_id  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _advanced(store, mission_id):
    return [event for event in store.list_events(mission_id) if event.type == "TaskGraphConvergenceAdvanced"]


def test_each_real_state_change_writes_one_advanced_event(tmp_path):
    """作业从"等待"（兄弟步骤的调用还在半路）一路推进到替换提交：每次真正的状态变化恰好一条
    事件，身份 = 作业 + 新行版本，前后状态首尾相接；停在"等待"期间反复检查不写事件。

    **改坏检验**：删掉 ``_transition`` 里的 ``append_event`` → 红；删掉 ``state == job.state``
    的提前返回 → 红（重复的"等待"也写事件）。"""

    async def case() -> None:
        async with waiting_convergence(tmp_path, key="converge-advanced") as waiting:
            world, mission_id, job_id = waiting.world, waiting.mission_id, waiting.job_id
            store = world.store
            parked = [event.payload["to_state"] for event in _advanced(store, mission_id)]
            assert parked == ["WAITING"], parked  # FENCED → WAITING, once
            waiting.provider.late.set()  # the slow call finally returns; its Attempt is already cancelled
            stop = asyncio.Event()

            async def drive() -> None:
                while not stop.is_set():
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=True)
                    await asyncio.sleep(0.05)

            runner = asyncio.create_task(drive())
            try:
                async with asyncio.timeout(60):
                    while str(store.get_mission(mission_id).status.value) not in {"COMPLETED", "FAILED", "CANCELLED"}:
                        if runner.done():
                            runner.result()
                        await asyncio.sleep(0.05)
            finally:
                stop.set()
                runner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runner
            assert str(store.get_mission(mission_id).status.value) == "COMPLETED"
            job = store.connection.execute(
                "SELECT state,row_version FROM taskgraph_convergence_jobs WHERE job_id=?", (job_id,)).fetchone()
            events = [event for event in _advanced(store, mission_id) if event.payload["job_id"] == job_id]
            assert events, "the job's state changes wrote no event"
            for event in events:
                payload = event.payload
                assert event.id == derive_id("tg-convergence-advanced", job_id, str(payload["row_version"]))
                assert event.actor_type == "system"
                assert set(payload) == {"schema_version", "job_id", "decision_id", "from_state", "to_state",
                                        "row_version"}
                assert payload["schema_version"] == 1 and payload["from_state"] != payload["to_state"]
            # one event per real change, chained end to end, ending where the job is now
            assert [e.payload["from_state"] for e in events[1:]] == [e.payload["to_state"] for e in events[:-1]]
            assert len({e.payload["row_version"] for e in events}) == len(events)
            assert events[-1].payload["to_state"] == job["state"]
            assert len({e.trace_id for e in events}) == 1
            assert [(e.payload["from_state"], e.payload["to_state"]) for e in events] == [
                ("FENCED", "WAITING"), ("WAITING", "READY"), ("READY", "APPLIED")]
            # while it waited the job was re-checked (its row version moved) without a state
            # change, and none of those re-checks wrote an event
            assert events[1].payload["row_version"] > events[0].payload["row_version"] + 1

    asyncio.run(case())


def test_auxiliary_events_never_trigger_a_recheck():
    """**Mutation**: add either type to ``_RECHECK_EVENTS`` → red."""
    assert "TaskGraphConvergenceAdvanced" not in taskgraph_notifications._RECHECK_EVENTS
    assert "TaskGraphDispatchBound" not in taskgraph_notifications._RECHECK_EVENTS
