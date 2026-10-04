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


@pytest.mark.replay_audit_exempt("用例故意改坏计划历史")
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


# ======================================================================================
# 2026-10-03 收尾裁决第 3 张：重启恢复、动作对账、启动绑定也在同一个边界里
# ======================================================================================
def test_a_fault_while_recovering_one_mission_does_not_stop_the_others(tmp_path):
    """任务 A 的重启恢复出库错误：``run()`` 不抛，B 照常完成；A 记一条"任务一轮故障"（地点 recover），
    恢复成功之前它这一轮的其余工作都跳过；故障排除后 A 原地继续并完成。"""
    import sqlite3

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store, loop = world.store, world.loop
            a = world.create(_notes("recover-a"))["mission_id"]
            b = world.create(_notes("recover-b"))["mission_id"]
            real = loop.commit.heal_mission
            broken = {"on": True}

            def heal(mission_id: str):  # type: ignore[no-untyped-def]
                if broken["on"] and mission_id == a:
                    raise sqlite3.OperationalError("disk I/O error")
                return real(mission_id)

            loop.commit.heal_mission = heal  # type: ignore[method-assign]
            for _ in range(12):
                await world.drain(timeout=20)
                if _status(store, b) in TERMINAL:
                    break
            assert _status(store, b) == "COMPLETED"
            assert _status(store, a) not in TERMINAL and a in loop._unrecovered
            assert [fault["where"] for fault in _faults(store, a)] == ["recover"]
            assert not store.list_tasks(a) or not [e for e in store.iter_events(a) if e.type == "AttemptCreated"]
            broken["on"] = False
            for _ in range(12):
                await world.drain(timeout=20)
                if _status(store, a) in TERMINAL:
                    break
            assert _status(store, a) == "COMPLETED" and a not in loop._unrecovered

    asyncio.run(case())


def test_a_startup_binding_that_refuses_one_intent_stops_only_its_mission(tmp_path):
    """启动时给在途回合重建工具权限，某一条的身份与冻结记录不符：服务照常起来，这条算数据损坏，
    第一轮只停它所在的任务（带码），不再让整个启动失败。"""
    from agent_orchestrator.contracts.models import ContractError

    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        try:
            async with product_world(tmp_path / "root", provider) as world:
                store, loop = world.store, world.loop
                a = world.create(_notes("startup-a"))["mission_id"]
                runner = asyncio.create_task(loop.run())
                for _ in range(400):
                    if provider.asked.count("worker") >= 1:
                        break
                    await asyncio.sleep(0.05)
                runner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runner

                def refuse(intent):  # type: ignore[no-untyped-def]
                    raise ContractError("SERVICE_TURN_IDENTITY_MISMATCH: turn differs from frozen intent")

                loop._bind_startup_intent = refuse  # type: ignore[method-assign]
                loop._bind_startup_tools()  # what ``__aenter__`` runs: must not raise
                assert loop._startup_faults and loop._startup_faults[0][0] == a
                await loop.recover()
                assert _status(store, a) == "FAILED"
                [fault] = _faults(store, a)
                assert fault["where"].startswith("startup_bind:") and fault["class"] == "CORRUPT"
        finally:
            provider.release.set()

    asyncio.run(case())


def test_an_ended_missions_collection_that_keeps_failing_is_closed_and_settled(tmp_path, monkeypatch):
    """任务已结束、它那次回合的收尾一直出错：到上限后把这条调用关为失败（记明原因），``run()`` 能回到
    空闲；它的额度预留随后能按上限结清（宁可多算、不冻结），并记一条"按上限计入"的用量事件。此前这条
    调用永远开着。

    **改坏检验**：结清时不写那条事件 → 变红。"""
    import sqlite3

    import agent_orchestrator.orchestrator.accounting_recovery as accounting
    import agent_orchestrator.orchestrator.failure_classes as failure_classes

    monkeypatch.setattr(failure_classes, "ROUND_FAULT_MIN_SECONDS", 0.2)
    monkeypatch.setattr(accounting, "ENDED_MISSION_RECHECK_SECONDS", 0.0)

    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            store, loop = world.store, world.loop
            a = world.create(_notes("ended-close"))["mission_id"]
            runner = asyncio.create_task(loop.run())
            try:
                for _ in range(400):
                    if provider.asked.count("worker") >= 1:
                        break
                    await asyncio.sleep(0.05)
                loop.commit.cancel_mission(a)

                async def failing(intent):  # type: ignore[no-untyped-def]
                    raise sqlite3.OperationalError("disk I/O error")

                loop._collect_after_stop = failing  # type: ignore[method-assign]
                provider.release.set()
                await asyncio.wait_for(runner, 60)  # run() comes back to idle on its own
            finally:
                provider.release.set()
                runner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runner
            closed = [fault for fault in _faults(store, a) if fault.get("closed_intent")]
            assert len(closed) == 1 and closed[0]["reason"] == "round_fault_after_mission_end", _faults(store, a)
            assert store.get_intent(closed[0]["closed_intent"]).state == "FAILED"
            accounting._settle_expired_ended_holds(loop)
            # 按上限结清的那条用量事实如实记下（阶段 B 欠的断言，HTN 补齐 F1）
            counteds = [event for event in store.list_events(a) if event.type == "ReservationCountedAtUpperBound"]
            assert len(counteds) == 1, counteds  # 改坏检验只认断言失败
            [counted] = counteds
            assert counted.payload["counted_tokens"] > 0
            assert not store.connection.execute(
                "SELECT 1 FROM budget_reservations r JOIN dispatch_intents i ON i.subject_id=r.subject_id "
                "WHERE i.mission_id=? AND r.state='RESERVED'", (a,)).fetchall()

    asyncio.run(case())


def test_a_mission_still_waiting_for_recovery_is_never_a_stall():
    """核验阻断项：恢复还在重试的任务这一轮其余工作都被跳过，派不出步骤是在等恢复，不是停滞；
    停滞的记录与确认共用 ``_idle_facts``，对它返回"不适用"，不问规划器、不判停。

    **改坏检验**：去掉这条判断 → 走到读计划（替身上没有）→ 变红。"""
    from types import SimpleNamespace

    from agent_orchestrator.orchestrator.event_handler import Orchestrator

    fake = SimpleNamespace(_unrecovered={"m1"})
    assert Orchestrator._idle_facts(fake, SimpleNamespace(id="m1")) is None


@pytest.mark.parametrize("place", ["taskgraph_notifications", "assurance_ingest"])
def test_a_fault_in_a_global_scan_is_one_missions_round_fault(tmp_path, monkeypatch, place):
    """阶段 C 第 0′ 条：主循环每轮的全局扫描（保证通道每轮工作、执行图通知、迟到用量导入……）
    里，一个任务那一份出了库错误，只记成这个任务这一轮的故障（地点写清），``run()`` 不抛，
    别的任务照常完成。迟到用量导入用的是同一个边界（要先造出"用量未知"的预留才走得到，不单
    独造）。

    **改坏检验**：扫描里去掉边界 → 异常冲出 ``run()`` → 变红。"""
    import sqlite3

    broken: dict[str, Any] = {"mission": None, "hits": 0}

    def breaking(original):  # type: ignore[no-untyped-def]
        def share(self, mission_id, *args):  # type: ignore[no-untyped-def]
            if mission_id == broken["mission"]:
                broken["hits"] += 1
                raise sqlite3.OperationalError("disk I/O error")
            return original(self, mission_id, *args)

        return share

    if place == "taskgraph_notifications":
        from agent_orchestrator.orchestrator.taskgraph_notifications import TaskGraphNotifications as Scan

        monkeypatch.setattr(Scan, "_consume_one", breaking(Scan._consume_one))
    else:
        from agent_orchestrator.orchestrator.assurance_tick import AssuranceTick as Scan

        monkeypatch.setattr(Scan, "_ingest_one", breaking(Scan._ingest_one))

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            bad = world.create({"goal": "写一份 A.md", "success_criteria": ["file:A.md"],
                                "idempotency_key": "scan-bad-" + place})["mission_id"]
            good = world.create({"goal": "写一份 B.md", "success_criteria": ["file:B.md"],
                                 "idempotency_key": "scan-good-" + place})["mission_id"]
            broken["mission"] = bad
            for _ in range(20):
                await world.drain(timeout=20)  # run() must not raise
                if str(world.store.get_mission(good).status.value) == "COMPLETED" and broken["hits"]:
                    break
            assert str(world.store.get_mission(good).status.value) == "COMPLETED"
            assert broken["hits"] >= 1
            faults = [e.payload for e in world.store.list_events(bad) if e.type == "MissionRoundFault"]
            assert place in {fault["where"] for fault in faults}, faults

    asyncio.run(case())


def test_a_failing_late_usage_import_is_one_round_fault_and_lands_once_next_round(tmp_path, monkeypatch):
    """崩溃切点 K12（HTN 补齐阶段 G 第 4 批）：任务已取消、那次调用这才返回，导入它的迟到用量时
    写库出错一次。``run()`` 不抛；另一个任务照常完成；出事任务记一轮故障；下一轮导入恰好一次
    （事务回滚，没有半截行、没有重复事件）；全业务重放与两库对照一致。

    **改坏检验**（G-11）：迟到用量的导入挪到故障边界之外 → 异常冲出主循环 → 变红。"""
    import sqlite3

    from agent_orchestrator.governance.budgets import BudgetLedger
    from agent_orchestrator.observability.business_replay import (
        CONSISTENT, verify_execution_ledgers, verify_mission)

    broken: dict[str, Any] = {"subject": None, "hits": 0}
    original = BudgetLedger.import_usage

    def failing_once(self, *, subject_id, mission_id, facts):  # type: ignore[no-untyped-def]
        if subject_id == broken["subject"] and not broken["hits"]:
            broken["hits"] += 1
            raise sqlite3.OperationalError("disk I/O error")
        return original(self, subject_id=subject_id, mission_id=mission_id, facts=facts)

    monkeypatch.setattr(BudgetLedger, "import_usage", failing_once)

    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        root = tmp_path / "root"
        async with product_world(root, provider) as world:
            store = world.store
            a = world.create(_notes("late-import-a"))["mission_id"]
            stop = asyncio.Event()
            crashed: list[BaseException] = []

            async def drive() -> None:
                try:
                    while not stop.is_set():
                        await world.loop.run()
                        await world.deployment.between_cycles(auto=True)
                        await asyncio.sleep(0.05)
                except Exception as error:  # noqa: BLE001 - escaping the loop is the defect
                    crashed.append(error)

            runner = asyncio.create_task(drive())
            try:
                await asyncio.wait_for(provider.entered.wait(), 60)
                world.control.cancel(a)
                for _ in range(300):
                    if _status(store, a) == "CANCELLED":
                        break
                    await asyncio.sleep(0.05)
                [(subject,)] = store.connection.execute(
                    "SELECT subject_id FROM dispatch_intents WHERE mission_id=? AND kind='attempt'", (a,)).fetchall()
                provider.held.discard("worker")
                b = world.create(_notes("late-import-b"))["mission_id"]
                broken["subject"] = subject
                provider.release.set()
                for _ in range(600):
                    if crashed or (_status(store, b) in TERMINAL
                                   and world.loop.commit.ledger.reservation(subject)["state"] == "SETTLED"):
                        break
                    await asyncio.sleep(0.05)
            finally:
                stop.set()
                provider.release.set()
                await asyncio.wait_for(runner, 30)
            assert not crashed, crashed
            assert broken["hits"] == 1 and _status(store, b) == "COMPLETED"
            faults = _faults(store, a)
            assert len(faults) == 1 and "disk I/O error" in faults[0]["summary"], faults
            assert world.loop.commit.ledger.reservation(subject)["state"] == "SETTLED"
            refs = [row[0] for row in store.connection.execute(
                "SELECT usage_ref FROM imported_usage WHERE subject_id=? AND unknown=0", (subject,))]
            writes = [item["key"]["usage_ref"] for event in store.list_events(a, types=("RowsWritten",))
                      for item in event.payload["changed"]
                      if item["table"] == "imported_usage" and item["row"]["subject_id"] == subject]
            assert refs and sorted(writes) == sorted(refs), (writes, refs)
            assert len([e for e in store.list_events(a) if e.type == "BudgetReleased"
                        and e.payload["subject_id"] == subject]) == 1
            assert verify_mission(store, a)["status"] == CONSISTENT
            assert verify_execution_ledgers(store, sorted(root.glob("execution*.db")))["status"] == CONSISTENT

    asyncio.run(case())
