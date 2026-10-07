# SPDX-License-Identifier: Apache-2.0
"""重启恢复协议八步（原计划 §25.1 第 11 条、§16.4、§23 P7.1；第 2 批车道 J H01）。

* 迁移 46 的三张表在、重放清单归类齐全；
* 恢复锁：死掉的持有者被接管，活着的被拒；
* 产品同形世界重开：八步按序 DONE、READY、禁副作用解除；
* 一个任务的库与它自己的历史对不上（重建不一致）→ 只隔离这个任务（AER 恢复第 3、8 条"隔离该流、只为
  核对可继续的范围开放执行"），恢复照样 READY，别的任务照常推进；本进程不替被隔离任务写任何东西：
  部署职责不替它签、恢复第 4 步不给它记启动绑定故障、门面对它只接受取消（N3-27）；
* 每次 ``drain()`` 的返回值都断言（N3-30）：健康局面能空闲是 True；执行者回合被扣着、主循环有在途回合
  时是 False（"永不空闲"上次就是没断言漏掉的）；
* 某一步的事实不成立（这里用替身让"未决核对"失败）→ DEGRADED_RECOVERY，写明停在哪一步，主循环不进周期、不问模型；
* 崩溃切点 K19：某一步记完结果后进程没了 → 锁留在库里 → 下一次启动按进程身份接管锁、重走八步。

**改坏检验**：协调者里把"一步失败就降级"改成继续 → 降级那条红；重建不一致时不隔离 → 隔离那条红；``acquire`` 里不核对持有者 → 第 2 条红；
部署职责 / 恢复第 4 步 / 门面三处各删掉隔离判断 → 隔离那条各自的断言红；``_has_inflight`` 不跳过被隔离任务 → 隔离那条的 drain 断言红。
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
        assert schema.SCHEMA_VERSION == 48  # 48 预算补执行者数、搜索次数（推后第 3 批 H08）
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
                assert await world.drain(timeout=20) is True
                if str(world.store.get_mission(mission_id).status.value) in TERMINAL:
                    break
            assert str(world.store.get_mission(mission_id).status.value) == "COMPLETED"
            first = world.loop.recovery_status()
            assert first["state"] == "READY" and first["side_effects_disabled"] is False
        async with product_world(root, LayeredScriptedProvider()) as world:
            assert await world.drain(timeout=20) is True
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
            assert by_step["manifest_check"]["schema_version"] == 48
            assert by_step["reducer_rebuild"]["missions"] == {}  # 已完成的任务不在恢复范围里
            assert by_step["ready"]["pools_woken"]
            # 再跑一次 run()：同一进程只重入三步，不再记新的恢复
            assert await world.drain(timeout=20) is True
            assert world.store.connection.execute("SELECT count(*) FROM recovery_runs").fetchone()[0] == 2

    asyncio.run(case())


@pytest.mark.replay_audit_exempt("用例故意另开连接改坏几行，造'库与自己的历史对不上'")
def test_an_inconsistent_mission_is_isolated_and_the_others_go_on(tmp_path):
    """被隔离的三种任务：执行到一半的（``damaged``）、完成要求还没确认的（``waiting``）、等规划授权的
    （``planning``）。重启后它们全被隔离，健康任务照常做完；本进程不替它们写任何东西（N3-27）。"""

    async def case():
        root = tmp_path / "root"
        first = LayeredScriptedProvider()
        first.held.add("worker")  # 执行者的回合一直不回：任务停在活动态
        async with product_world(root, first) as world:
            damaged = world.create({**NOTE, "idempotency_key": "recover-damaged"})["mission_id"]
            healthy = world.create({**NOTE, "goal": "写另一份笔记", "idempotency_key": "recover-healthy"})["mission_id"]
            for _ in range(20):
                # 执行者的回合一直不回：主循环有在途回合，到时限也不空闲
                assert await world.drain(timeout=5) is False
                if first.asked.count("worker") >= 2:
                    break
            assert first.asked.count("worker") >= 2
            # 手动模式下再建两个：一个确认了完成要求、停在等规划授权；一个连完成要求都没确认
            world.auto = False
            planning = world.create({**NOTE, "goal": "写第三份笔记", "idempotency_key": "recover-planning"})["mission_id"]
            assert world.deployment.duties.auto_confirm_content_completion(auto=True) == 1
            for _ in range(10):
                assert await world.drain(timeout=3) is False  # 前两个任务的执行者回合仍被扣着
                if any(row["mission_id"] == planning for row in world.control.pending_planning_authorizations()):
                    break
            assert any(row["mission_id"] == planning for row in world.control.pending_planning_authorizations())
            waiting = world.create({**NOTE, "goal": "写第四份笔记", "idempotency_key": "recover-waiting"})["mission_id"]
            assert world.store.get_mission(waiting).status.value == "CREATED"
            isolated = {damaged, planning, waiting}
            for mission_id in (damaged, healthy, planning, waiting):
                assert str(world.store.get_mission(mission_id).status.value) not in TERMINAL
            def attempts_of(store, mission_id):
                return store.connection.execute(
                    "SELECT count(*) FROM attempts a JOIN tasks t ON t.task_id=a.task_id WHERE t.mission_id=?",
                    (mission_id,)).fetchone()[0]
            damaged_attempts = attempts_of(world.store, damaged)
            [damaged_task] = [row[0] for row in world.store.connection.execute(
                "SELECT t.task_id FROM attempts a JOIN tasks t ON t.task_id=a.task_id WHERE t.mission_id=? LIMIT 1",
                (damaged,))]
            db = world.store.path

        def footprint(connection, mission_id):
            """被隔离任务在库里的全部可见痕迹：每张带 mission_id 列的表里它有几行，加上派发意图的状态与
            保证通道待办的原样内容。"""
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            counts = {}
            for table in tables:
                columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
                if "mission_id" in columns:
                    counts[table] = connection.execute(
                        f"SELECT count(*) FROM {table} WHERE mission_id=?", (mission_id,)).fetchone()[0]
            intents = sorted(tuple(r) for r in connection.execute(
                "SELECT intent_id, state FROM dispatch_intents WHERE mission_id=?", (mission_id,)))
            work = sorted(tuple(r) for r in connection.execute(
                "SELECT * FROM assurance_pending_work WHERE mission_id=?", (mission_id,)))
            return counts, intents, work
        connection = sqlite3.connect(db)
        try:
            for mission_id in sorted(isolated):
                connection.execute("UPDATE missions SET json=json_set(json,'$.goal','改坏的目标') WHERE mission_id=?",
                                   (mission_id,))
            connection.commit()
        finally:
            connection.close()
        reader = sqlite3.connect(db)
        try:
            before = {mission_id: footprint(reader, mission_id) for mission_id in isolated}
        finally:
            reader.close()
        second = LayeredScriptedProvider()
        async with product_world(root, second) as world:  # 自动模式：部署职责会替没隔离的任务签
            from agent_orchestrator.api.facade import FacadeError
            from agent_orchestrator.orchestrator.event_handler import ServiceTurnIdentityMismatch

            # 部署职责发出的每条命令记下是替哪个任务发的
            duty_calls: list[tuple[str, str]] = []
            control = world.deployment.duties.control
            for name in ("approve_operation_completion_spec", "planning_authorization",
                         "approve_assurance_check_policy"):
                def spy(command, _name=name, _real=getattr(control, name)):
                    duty_calls.append((_name, str(command.get("mission_id"))))
                    return _real(command)
                setattr(control, name, spy)
            # 被隔离任务的一条启动绑定故障：恢复第 4 步不给它记故障、不停它
            world.loop._startup_faults.append(
                (damaged, "startup_bind:fixture", ServiceTurnIdentityMismatch("SERVICE_TURN_IDENTITY_MISMATCH: fixture")))
            for _ in range(20):
                # 健康任务的执行者回合是上一个进程留下的，要等它收回再重派（约半分钟），时限放宽到 60 秒
                assert await world.drain(timeout=60) is True
                if str(world.store.get_mission(healthy).status.value) in TERMINAL:
                    break
            for _ in range(3):
                # 健康任务做完后再跑几轮：被隔离的仍不能被任何人动，也不让主循环永远不空闲
                # （否则所有任务的卡死检测都失效）
                assert await world.drain(timeout=5) is True
            status = world.loop.recovery_status()
            assert status["state"] == str(RecoveryState.READY) and status["side_effects_disabled"] is False
            assert set(status["isolated_missions"]) == isolated
            assert "missions" in status["isolated_missions"][damaged]["tables"]
            steps = {o["step"]: o for o in status["latest"]["obligations"]}
            assert steps["reducer_rebuild"]["status"] == "DONE"
            assert set(steps["reducer_rebuild"]["detail"]["isolated"]) == isolated
            # 恢复第 4 步：被隔离任务的启动绑定故障只记进这一步的结果
            assert steps["inbox_outbox"]["detail"]["startup_faults_isolated"] == [
                {"mission_id": damaged, "where": "startup_bind:fixture", "error": "ServiceTurnIdentityMismatch"}]
            assert steps["inbox_outbox"]["detail"]["startup_faults_settled"] == 0
            assert [e for e in world.store.iter_events(damaged) if e.type == "MissionRoundFault"] == []
            # 健康的任务照常推进到底；被隔离的任务原样：不派发、不判停
            assert str(world.store.get_mission(healthy).status.value) == "COMPLETED"
            assert attempts_of(world.store, damaged) == damaged_attempts
            for mission_id in isolated:
                assert str(world.store.get_mission(mission_id).status.value) not in TERMINAL
            assert world.store.get_mission(waiting).status.value == "CREATED"
            # 部署职责只替没隔离的任务发命令（健康任务的检查策略照常投影），被隔离的一条都没有
            assert {mid for _, mid in duty_calls} & isolated == set()
            assert healthy in {mid for _, mid in duty_calls}
            # 隔离是完整的：在途回合不当"已结束任务"收掉、保证通道不替它入箱或收尾、不导入用量、
            # 部署职责不替它确认 / 授权 / 投影检查策略
            assert {mission_id: footprint(world.store.connection, mission_id) for mission_id in isolated} == before

            # 门面：推进被隔离任务的写入一律报 MISSION_RECOVERY_ISOLATED，什么也不写
            def refused(call):
                """调用的结果：被拒报的码；没被拒是 None。"""
                try:
                    call()
                except FacadeError as error:
                    return error.code
                return None

            control = world.control
            ref = {"id": "r", "revision": 1, "content_hash": "0" * 64}
            amend = {"command_id": "amend-iso", "expected_requirements_ref": ref, "reason": "改",
                     "source": {"kind": "user"}, "changes": [{"op": "add", "statement": "file:notes/b.md"}]}
            spec = {"command_id": "spec-iso", "expected_requirements_ref": ref, "proposal": {},
                    "approval_source": "HUMAN"}
            [pending] = [row for row in control.pending_planning_authorizations() if row["mission_id"] == planning]
            calls = {
                "amend_requirements": lambda mid: control.amend_requirements({**amend, "mission_id": mid}),
                "approve_operation_completion_spec":
                    lambda mid: control.approve_operation_completion_spec({**spec, "mission_id": mid}),
                "submit_operation_intent":
                    lambda mid: control.submit_operation_intent({"schema_version": 2, "mission_id": mid}),
                "register_source": lambda mid: control.register_source(
                    {"mission_id": mid, "path": "notes/x.md", "content": "x", "kind": "text",
                     "idempotency_key": "src-iso"}),
                "approve_assurance_check_policy": lambda mid: control.approve_assurance_check_policy(
                    {"mission_id": mid, "command_id": "cp-iso", "requirements_ref": ref,
                     "candidate_mapping": [{}], "completion_scope": ref}),
                "comment": lambda mid: control.comment(mid, "看一下"),
            }
            for name, call in calls.items():
                assert refused(lambda: call(waiting)) == "MISSION_RECOVERY_ISOLATED", name
                # 没隔离的任务：判断不拦，请求走到原来的核对（这里的请求多半本身不成立、报别的码；评论照常写上）
                assert refused(lambda: call(healthy)) != "MISSION_RECOVERY_ISOLATED", name
            assert refused(lambda: control.planning_authorization(
                {"operation": "issue", "mission_id": planning, "request_id": pending["request_id"],
                 "command_id": "grant-iso"})) == "MISSION_RECOVERY_ISOLATED"
            assert refused(lambda: control.takeover(damaged_task, "retry_with_note", basis="再试")) \
                == "MISSION_RECOVERY_ISOLATED"
            assert {mission_id: footprint(world.store.connection, mission_id) for mission_id in isolated} == before
            # 用户取消被隔离的任务：取消走提交服务照样成功；之后主循环仍能空闲
            for mission_id in sorted(isolated):
                world.control.cancel(mission_id)
                assert str(world.store.get_mission(mission_id).status.value) == "CANCELLED"
            assert await world.drain(timeout=10) is True

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
                # 执行者的回合一直不回：主循环有在途回合，到时限也不空闲
                assert await world.drain(timeout=5) is False
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
            assert await world.drain(timeout=5) is True  # 再跑也不进周期：run() 直接回来
            assert second.asked == []

    asyncio.run(case())


def test_a_crash_after_a_recovery_step_leaves_the_lock_and_the_next_start_takes_over(tmp_path):
    """崩溃切点 K19（``crash_points.json``）。"""

    async def case():
        root = tmp_path / "root"
        async with product_world(root, LayeredScriptedProvider()) as world:
            mission_id = world.create({**NOTE, "idempotency_key": "recover-crash"})["mission_id"]
            for _ in range(20):
                assert await world.drain(timeout=20) is True
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
            assert await world.drain(timeout=20) is True
            status = world.loop.recovery_status()
            assert status["state"] == "READY"
            rows = world.store.connection.execute(
                "SELECT state, detail_json FROM recovery_runs ORDER BY started_at").fetchall()
            assert [r[0] for r in rows] == ["READY", "SUPERSEDED", "READY"]
            assert '"superseded_by"' in rows[1][1]
            assert _obligations(status) == [(no, step, "DONE") for no, step in RECOVERY_STEPS]

    asyncio.run(case())


def test_an_isolated_missions_question_and_approvals_are_refused_and_taken_again_once_it_is_not(tmp_path):
    """门面另两类写入口（N3-27）：回答规划提问、审批决定。任务被隔离时报 ``MISSION_RECOVERY_ISOLATED``、
    提问原样待答；不隔离时同一个回答照常被收下。隔离用的是第 3 步写的同一张名单
    （``Orchestrator.recovery_isolated`` 只读它），这里直接放进去，省一次改坏库再重启。"""
    import json
    from pathlib import Path

    from h1i_seed import CONFIG, CRITERIA, GOAL, run_until

    from agent_orchestrator.api.facade import FacadeError
    from agent_orchestrator.storage.planning_human_store import PlanningHumanStore
    from agent_orchestrator.testing.fixtures import package_of

    fixture = Path(__file__).parent / "fixtures" / "planning_decision_v1" / "valid" / "request-human.json"

    def planner(request):
        body = json.loads(fixture.read_text(encoding="utf-8"))
        body["subject_key"] = package_of(request)["planning_subjects"][0]["subject_key"]
        body["payload"] = {"question": "笔记写给谁看？", "options": [], "blocking": True}
        return "<planning_decision>" + json.dumps(body, ensure_ascii=False) + "</planning_decision>"

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner), **CONFIG) as world:
            loop, control = world.loop, world.control
            mission_id = world.create({"goal": GOAL, "idempotency_key": "iso-question",
                                       "success_criteria": list(CRITERIA)})["mission_id"]
            humans = PlanningHumanStore(loop.store)

            def pending():
                return [row for row in humans.list(mission_id) if row["state"] == "PENDING"]

            await run_until(world, lambda: bool(pending()))
            [question] = pending()
            answer = {"decision_id": question["decision_id"], "answer": "写给自己看",
                      "expected_version": question["version"], "nonce": "n-iso"}
            loop.store.put_approval({"request_id": "approval-iso", "kind": "review", "mission_id": mission_id,
                                     "subject_key": "fixture", "state": "PENDING", "version": 1})

            loop._recovery_isolated[mission_id] = {"tables": ["missions"], "silent_changes": []}
            for call in (lambda: control.answer_planning_question(answer),
                         lambda: control.decide("approval-iso", "approve"),
                         lambda: control.decide("approval-iso", "reject", reason="不要")):
                with pytest.raises(FacadeError) as caught:
                    call()
                assert caught.value.code == "MISSION_RECOVERY_ISOLATED"
            assert [row["decision_id"] for row in pending()] == [question["decision_id"]]
            assert loop.store.get_approval("approval-iso")["state"] == "PENDING"

            del loop._recovery_isolated[mission_id]
            control.answer_planning_question(answer)
            assert humans.get(question["decision_id"])["state"] == "ANSWERED"
            with pytest.raises(FacadeError) as caught:  # 假审批单本身不成立：报的是原来的码，不是隔离
                control.decide("approval-iso", "approve")
            assert caught.value.code != "MISSION_RECOVERY_ISOLATED"

    asyncio.run(case())
