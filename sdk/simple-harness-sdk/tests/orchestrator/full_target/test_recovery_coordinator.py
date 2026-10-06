# SPDX-License-Identifier: Apache-2.0
"""重启恢复协议八步（原计划 §25.1 第 11 条、§16.4、§23 P7.1；第 2 批车道 J H01）。

* 迁移 46 的三张表在、重放清单归类齐全；
* 恢复锁：死掉的持有者被接管，活着的被拒；
* 产品同形世界重开：八步按序 DONE、READY、禁副作用解除；
* 一个任务的库与它自己的历史对不上（重建不一致）→ 只隔离这个任务（AER 恢复第 3、8 条"隔离该流、只为
  核对可继续的范围开放执行"），恢复照样 READY，别的任务照常推进；
* 某一步的事实不成立（这里用替身让"未决核对"失败）→ DEGRADED_RECOVERY，写明停在哪一步，主循环不进周期、不问模型；
* 崩溃切点 K19：某一步记完结果后进程没了 → 锁留在库里 → 下一次启动按进程身份接管锁、重走八步。

**改坏检验**：协调者里把"一步失败就降级"改成继续 → 降级那条红；重建不一致时不隔离 → 隔离那条红；``acquire`` 里不核对持有者 → 第 2 条红。
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from agent_orchestrator.observability.business_replay import check_inventory
from agent_orchestrator.orchestrator.recovery_coordinator import RECOVERY_STEPS, RecoveryState
from agent_orchestrator.storage import schema
from agent_orchestrator.storage.recovery_store import RecoveryLockHeld, RecoveryStore
from agent_orchestrator.storage.store import InjectedCrash, Store
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
NOTE = {"goal": "写一份笔记", "success_criteria": ["file:notes/a.md"]}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _obligations(status: dict) -> list[tuple[int, str, str]]:
    return [(o["step_no"], o["step"], o["status"]) for o in status["latest"]["obligations"]]


def test_migration_46_adds_the_three_runtime_tables_and_the_inventory_knows_them(tmp_path):
    store = Store.open(tmp_path / "o.db")
    try:
        assert schema.SCHEMA_VERSION == 46
        for table in ("recovery_runs", "recovery_obligations", "sandbox_executions"):
            assert store.has_table(table), table
        check_inventory(store)
        columns = [r[1] for r in store.connection.execute("PRAGMA table_info(sandbox_executions)")]
        for column in ("process_group", "session_id", "process_started_at", "cwd", "scratch",
                       "mission_id", "task_id", "attempt_id"):
            assert column in columns, column
    finally:
        store.close()


def test_the_recovery_lock_is_taken_over_from_a_dead_owner_and_refused_for_a_live_one(tmp_path):
    store = Store.open(tmp_path / "o.db")
    try:
        recovery = RecoveryStore(store)
        first, superseded = recovery.acquire(owner="a", pid=4242, process_started_at="T1", alive=lambda p, t: True)
        assert superseded == [] and recovery.get(first).state == "RECOVERY_LOCKED"
        # 另一个进程、持有者还活着：拒绝
        with pytest.raises(RecoveryLockHeld):
            recovery.acquire(owner="b", pid=4343, process_started_at="T2", alive=lambda p, t: True)
        assert recovery.get(first).state == "RECOVERY_LOCKED"
        # 持有者不在了（进程号在但启动时间对不上也算不在）：接管
        second, superseded = recovery.acquire(owner="b", pid=4343, process_started_at="T2",
                                              alive=lambda p, t: not (p == 4242 and t == "T1"))
        assert [s["recovery_id"] for s in superseded] == [first]
        assert recovery.get(first).state == "SUPERSEDED"
        assert recovery.get(first).detail["superseded_by"] == second
        # 同一个进程上一个实例留下的锁：接管，不问 alive
        third, superseded = recovery.acquire(owner="b", pid=4343, process_started_at="T2", alive=lambda p, t: True)
        assert [s["recovery_id"] for s in superseded] == [second]
        assert recovery.get(second).detail["superseded_reason"] == "same process, previous instance"
        recovery.record_step(third, 1, "recovery_locked", "DONE", {"x": 1})
        recovery.finish(third, "READY")
        latest = recovery.latest()
        assert latest["recovery_id"] == third and latest["state"] == "READY"
        assert latest["obligations"] == [{"step_no": 1, "step": "recovery_locked", "status": "DONE",
                                          "detail": {"x": 1}, "created_at": latest["obligations"][0]["created_at"]}]
        with pytest.raises(ValueError):
            recovery.finish(third, "RECOVERY_LOCKED")
    finally:
        store.close()


def test_a_restart_runs_the_eight_steps_in_order_and_ends_ready(tmp_path):
    async def case():
        root = tmp_path / "root"
        async with product_world(root, LayeredScriptedProvider()) as world:
            status = world.loop.recovery_status()
            assert status["state"] == "RECOVERY_LOCKED" and status["side_effects_disabled"] is True
            mission_id = world.create({**NOTE, "idempotency_key": "recover-ready"})["mission_id"]
            for _ in range(20):
                await world.drain(timeout=20)
                if str(world.store.get_mission(mission_id).status.value) in TERMINAL:
                    break
            assert str(world.store.get_mission(mission_id).status.value) == "COMPLETED"
            first = world.loop.recovery_status()
            assert first["state"] == "READY" and first["side_effects_disabled"] is False
        async with product_world(root, LayeredScriptedProvider()) as world:
            await world.drain(timeout=20)
            status = world.loop.recovery_status()
            assert status["state"] == str(RecoveryState.READY)
            assert status["latest"]["state"] == "READY" and status["latest"]["failed_step"] is None
            assert _obligations(status) == [(no, step, "DONE") for no, step in RECOVERY_STEPS]
            assert status["latest"]["recovery_id"] == status["recovery_id"]
            # 第一座世界的恢复记录被同进程的第二座接管（不是两把活锁）
            runs = world.store.connection.execute(
                "SELECT state FROM recovery_runs ORDER BY started_at").fetchall()
            assert [r[0] for r in runs] == ["READY", "READY"]
            by_step = {o["step"]: o["detail"] for o in status["latest"]["obligations"]}
            assert by_step["manifest_check"]["schema_version"] == 46
            assert by_step["reducer_rebuild"]["missions"] == {}  # 已完成的任务不在恢复范围里
            assert by_step["ready"]["pools_woken"]
            # 再跑一次 run()：同一进程只重入三步，不再记新的恢复
            await world.drain(timeout=20)
            assert world.store.connection.execute("SELECT count(*) FROM recovery_runs").fetchone()[0] == 2

    asyncio.run(case())


@pytest.mark.replay_audit_exempt("用例故意另开连接改坏一行，造'库与自己的历史对不上'")
def test_an_inconsistent_mission_is_isolated_and_the_others_go_on(tmp_path):
    async def case():
        root = tmp_path / "root"
        first = LayeredScriptedProvider()
        first.held.add("worker")  # 执行者的回合一直不回：两个任务都停在活动态
        async with product_world(root, first) as world:
            damaged = world.create({**NOTE, "idempotency_key": "recover-damaged"})["mission_id"]
            healthy = world.create({**NOTE, "goal": "写另一份笔记", "idempotency_key": "recover-healthy"})["mission_id"]
            for _ in range(20):
                await world.drain(timeout=5)
                if first.asked.count("worker") >= 2:
                    break
            assert first.asked.count("worker") >= 2
            for mission_id in (damaged, healthy):
                assert str(world.store.get_mission(mission_id).status.value) not in TERMINAL
            def attempts_of(store, mission_id):
                return store.connection.execute(
                    "SELECT count(*) FROM attempts a JOIN tasks t ON t.task_id=a.task_id WHERE t.mission_id=?",
                    (mission_id,)).fetchone()[0]
            damaged_attempts = attempts_of(world.store, damaged)
            db = world.store.path

        def footprint(connection, mission_id):
            """被隔离任务在库里的全部可见痕迹：事件、派发意图的状态、保证通道待办。"""
            events = connection.execute("SELECT count(*) FROM events WHERE mission_id=?",
                                              (mission_id,)).fetchone()[0]
            intents = sorted(tuple(r) for r in connection.execute(
                "SELECT intent_id, state FROM dispatch_intents WHERE mission_id=?", (mission_id,)))
            work = sorted(tuple(r) for r in connection.execute(
                "SELECT * FROM assurance_pending_work WHERE mission_id=?", (mission_id,)))
            return events, intents, work
        connection = sqlite3.connect(db)
        try:
            connection.execute("UPDATE missions SET json=json_set(json,'$.goal','改坏的目标') WHERE mission_id=?",
                               (damaged,))
            connection.commit()
        finally:
            connection.close()
        reader = sqlite3.connect(db)
        try:
            before = footprint(reader, damaged)
        finally:
            reader.close()
        second = LayeredScriptedProvider()
        async with product_world(root, second) as world:
            for _ in range(20):
                await world.drain(timeout=10)
                if str(world.store.get_mission(healthy).status.value) in TERMINAL:
                    break
            for _ in range(3):
                await world.drain(timeout=5)  # 健康任务做完后再跑几轮：被隔离的仍不能被任何人动
            status = world.loop.recovery_status()
            assert status["state"] == str(RecoveryState.READY) and status["side_effects_disabled"] is False
            assert list(status["isolated_missions"]) == [damaged]
            assert "missions" in status["isolated_missions"][damaged]["tables"]
            rebuild = {o["step"]: o for o in status["latest"]["obligations"]}["reducer_rebuild"]
            assert rebuild["status"] == "DONE" and damaged in rebuild["detail"]["isolated"]
            # 健康的任务照常推进到底；被隔离的任务原样：不派发、不判停
            assert str(world.store.get_mission(healthy).status.value) == "COMPLETED"
            assert attempts_of(world.store, damaged) == damaged_attempts
            assert str(world.store.get_mission(damaged).status.value) not in TERMINAL
            # 隔离是完整的：在途回合不当"已结束任务"收掉、保证通道不替它入箱或收尾、不导入用量
            assert footprint(world.store.connection, damaged) == before

    asyncio.run(case())


def test_a_step_whose_facts_do_not_hold_degrades_recovery_and_opens_read_only(tmp_path, monkeypatch):
    from agent_orchestrator.orchestrator import recovery_coordinator

    async def failing(self):
        raise recovery_coordinator.RecoveryStepFailed("pending_reconcile", {"problems": ["fixture"]})

    async def case():
        root = tmp_path / "root"
        first = LayeredScriptedProvider()
        first.held.add("worker")
        async with product_world(root, first) as world:
            mission_id = world.create({**NOTE, "idempotency_key": "recover-degraded"})["mission_id"]
            for _ in range(10):
                await world.drain(timeout=5)
                if "worker" in first.asked:
                    break
            assert "worker" in first.asked
            attempts = world.store.connection.execute("SELECT count(*) FROM attempts").fetchone()[0]
        monkeypatch.setattr(recovery_coordinator.RecoveryCoordinator, "_step_pending", failing)
        second = LayeredScriptedProvider()
        async with product_world(root, second) as world:
            assert await world.drain(timeout=20) is True
            status = world.loop.recovery_status()
            assert status["state"] == str(RecoveryState.DEGRADED_RECOVERY)
            assert status["side_effects_disabled"] is True
            latest = status["latest"]
            assert latest["state"] == "DEGRADED_RECOVERY" and latest["failed_step"] == "pending_reconcile"
            assert _obligations(status)[-1] == (5, "pending_reconcile", "FAILED")
            # 只读与诊断：不问模型、不派发、不唤醒执行池；任务原样
            assert second.asked == []
            assert world.store.connection.execute("SELECT count(*) FROM attempts").fetchone()[0] == attempts
            assert str(world.store.get_mission(mission_id).status.value) not in TERMINAL
            await world.drain(timeout=5)  # 再跑也不进周期
            assert second.asked == []

    asyncio.run(case())


def test_a_crash_after_a_recovery_step_leaves_the_lock_and_the_next_start_takes_over(tmp_path):
    """崩溃切点 K19（``crash_points.json``）。"""

    async def case():
        root = tmp_path / "root"
        async with product_world(root, LayeredScriptedProvider()) as world:
            mission_id = world.create({**NOTE, "idempotency_key": "recover-crash"})["mission_id"]
            for _ in range(20):
                await world.drain(timeout=20)
                if str(world.store.get_mission(mission_id).status.value) in TERMINAL:
                    break
        async with product_world(root, LayeredScriptedProvider()) as world:
            world.store.arm("after_recovery_step:manifest_check")
            with pytest.raises(InjectedCrash):
                await world.loop.run()
            assert "after_recovery_step:manifest_check" in world.store.fired
            rows = world.store.connection.execute(
                "SELECT state FROM recovery_runs ORDER BY started_at").fetchall()
            assert [r[0] for r in rows][-1] == "RECOVERY_LOCKED"
            assert world.loop.recovery_status()["side_effects_disabled"] is True
        async with product_world(root, LayeredScriptedProvider()) as world:
            await world.drain(timeout=20)
            status = world.loop.recovery_status()
            assert status["state"] == "READY"
            rows = world.store.connection.execute(
                "SELECT state, detail_json FROM recovery_runs ORDER BY started_at").fetchall()
            assert [r[0] for r in rows] == ["READY", "SUPERSEDED", "READY"]
            assert '"superseded_by"' in rows[1][1]
            assert _obligations(status) == [(no, step, "DONE") for no, step in RECOVERY_STEPS]

    asyncio.run(case())
