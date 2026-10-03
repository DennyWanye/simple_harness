# SPDX-License-Identifier: Apache-2.0
"""一个任务的库读写出错，不许拖垮主循环和别的任务（2026-10-03 阶段 B 裁决第 9 类）。

主循环把"一个任务这一轮的工作"包进同一个边界：接住任何异常，这个任务这一轮跳过、记一条
"任务一轮故障"，别的任务照常；下一轮原地再来，不重新调模型。查一张表分两类：

* 数据损坏（完整性拒绝）：当轮停这个任务（规划失败，带完整性错误码）；
* 其余（写失败、触发器拒绝……）：原地重试；同一处连续 6 轮且至少 2 分钟才以"库读写故障"停。

写失败用测试自己的触发器 ``RAISE(ABORT)`` 注入真实事务（条件限定任务号）；损坏用改库字节造
（分诊裁决①b1）。

**改坏检验**：边界里"记故障并跳过"改回原样抛出 → 第一条变红；分类表把历史完整性码改成重试
→ 第三条变红（不再当轮停）。
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest

from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _status(store, mission_id: str) -> str:
    return str(store.get_mission(mission_id).status.value)


def _faults(store, mission_id: str) -> list:
    return [event.payload for event in store.iter_events(mission_id) if event.type == "MissionRoundFault"]


def _block_tasks_of(store, mission_id: str) -> None:
    store.connection.execute(
        "CREATE TRIGGER test_round_fault BEFORE INSERT ON tasks WHEN NEW.mission_id="
        f"'{mission_id}' BEGIN SELECT RAISE(ABORT,'TEST_STORE_FAULT'); END")


def _notes(key: str) -> dict:
    return {"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"], "idempotency_key": key}


def test_a_store_fault_in_one_mission_does_not_stop_the_others(tmp_path):
    async def case():
        provider = LayeredScriptedProvider()
        async with product_world(tmp_path / "root", provider) as world:
            store = world.store
            a = world.create(_notes("fault-a"))["mission_id"]
            b = world.create(_notes("fault-b"))["mission_id"]
            _block_tasks_of(store, a)  # A's plan can never be written while this stands
            for _ in range(30):
                await world.drain(timeout=10)  # never raises out of run()
                if _status(store, b) in TERMINAL and _faults(store, a):
                    break
            assert _status(store, b) == "COMPLETED"
            assert _status(store, a) not in TERMINAL
            [fault] = _faults(store, a)  # recorded once per streak, not once per round
            assert fault["class"] == "RETRY" and "TEST_STORE_FAULT" in fault["summary"]
            planner_calls = provider.asked.count("planner")
            for _ in range(3):  # retried in place: the durable reply is re-admitted, no new call
                await world.drain(timeout=10)
            assert provider.asked.count("planner") == planner_calls

            store.connection.execute("DROP TRIGGER test_round_fault")
            mission = await world.run_until_settled(a, rounds=20)
            assert str(mission.status.value) == "COMPLETED"

    asyncio.run(case())


def test_a_persistent_store_fault_stops_its_mission_by_name(tmp_path, monkeypatch):
    import agent_orchestrator.orchestrator.failure_classes as failure_classes

    monkeypatch.setattr(failure_classes, "ROUND_FAULT_MIN_SECONDS", 0.0)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store = world.store
            a = world.create(_notes("stuck-a"))["mission_id"]
            b = world.create(_notes("stuck-b"))["mission_id"]
            _block_tasks_of(store, a)
            for _ in range(40):
                await world.drain(timeout=10)
                if _status(store, a) in TERMINAL and _status(store, b) in TERMINAL:
                    break
            mission = store.get_mission(a)
            assert str(mission.status.value) == "FAILED" and mission.stop_reason == "store_fault"
            detail = mission.final_report["detail"]
            assert detail["rounds"] >= 6 and "TEST_STORE_FAULT" in detail["summary"]
            assert _status(store, b) == "COMPLETED"

    asyncio.run(case())


def test_a_damaged_plan_history_stops_only_its_own_mission(tmp_path):
    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            store = world.store
            a = world.create(_notes("damaged-a"))["mission_id"]
            b = world.create(_notes("damaged-b"))["mission_id"]
            stop = asyncio.Event()

            async def drive() -> None:
                while not stop.is_set():
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=True)
                    await asyncio.sleep(0.05)

            runner = asyncio.create_task(drive())
            try:
                for _ in range(400):  # both plans committed and both workers are on their way
                    if all(store.connection.execute(
                            "SELECT 1 FROM taskgraph_revision_records WHERE mission_id=?", (m,)).fetchone()
                           for m in (a, b)) and provider.asked.count("worker") >= 2:
                        break
                    await asyncio.sleep(0.05)
                with store.transaction() as connection:
                    connection.execute("DROP TRIGGER taskgraph_revision_records_no_update")
                    connection.execute("UPDATE taskgraph_revision_records SET manifest_hash=? "
                                       "WHERE mission_id=? AND revision=1", ("f" * 64, a))
                provider.release.set()
                for _ in range(600):
                    if _status(store, a) in TERMINAL and _status(store, b) in TERMINAL:
                        break
                    await asyncio.sleep(0.05)
            finally:
                stop.set()
                provider.release.set()
                await asyncio.wait_for(runner, 30)
            mission = store.get_mission(a)
            assert str(mission.status.value) == "FAILED" and mission.stop_reason == "planning_failed"
            assert mission.final_report["detail"]["code"] == "TASKGRAPH_HISTORY_INTEGRITY"
            assert _status(store, b) == "COMPLETED"

    asyncio.run(case())


def test_an_ended_missions_failing_collection_is_recorded_once_not_every_round(tmp_path):
    """核验阻断项（2026-10-03）：任务已结束、它那次回合的收尾收集一直出错时，故障只记一次。
    此前任务结束就清计数，每轮写一条新的"任务一轮故障"，主循环按最短间隔空转。"""

    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            store = world.store
            a = world.create(_notes("ended-a"))["mission_id"]
            stop = asyncio.Event()

            async def drive() -> None:
                while not stop.is_set():
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=True)
                    await asyncio.sleep(0.05)

            runner = asyncio.create_task(drive())
            try:
                for _ in range(400):
                    if provider.asked.count("worker") >= 1:
                        break
                    await asyncio.sleep(0.05)
                world.loop.commit.cancel_mission(a)
                store.connection.execute(
                    "CREATE TRIGGER test_after_stop BEFORE INSERT ON events WHEN NEW.mission_id="
                    f"'{a}' AND NEW.type='IntentSettled' BEGIN SELECT RAISE(ABORT,'TEST_AFTER_STOP'); END")
                provider.release.set()
                for _ in range(100):
                    if _faults(store, a):
                        break
                    await asyncio.sleep(0.05)
                await asyncio.sleep(2)
            finally:
                stop.set()
                provider.release.set()
                # The stopped turn's intent stays open while its collection keeps failing,
                # so ``run()`` keeps waiting on it (backing off, writing nothing).
                runner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runner
            faults = _faults(store, a)
            assert len(faults) == 1 and "TEST_AFTER_STOP" in faults[0]["summary"], faults

    asyncio.run(case())
