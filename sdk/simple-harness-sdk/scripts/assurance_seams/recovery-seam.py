"""保证通道恢复接缝（产品同形世界，2026-10-03 迁离 ``_assured_fixture``）。

产品部署、主循环真跑；外界会发生的事只用三种办法造：
* 进程在内容审阅那一层判过、记下之后立刻被杀（产品自带的崩溃点 ``after_layer_pass:attempt``），再用
  同一个库、同一套部署重新起服务；
* 上一次运行时墙钟比现在快（库里持久化的时钟高水位比现在的墙钟晚几秒——磁盘上的状态，分诊裁决①b1），
  重启时就是一次时钟回拨；
* 部署策略里真实的授权时长（几秒）加真实等待，让证书在没有任何业务事件的情况下到期，再重启。

报告里的事实：
1. 冷恢复：崩溃后重启，那份内容审阅沿用原来那次调用（序号只有 1，重启后没有第二次内容审阅的模型
   调用），正式记录照常，任务完成；
2. 重启遇到时钟回拨：启动对账在任何认领之前记下回拨（每个任务一条时间不连续事件、时钟代数加一）；
   回拨期间用户取消任务，收尾 / 通知工作照常入库但不认领、不发通知；墙钟追上以后一条"时钟稳定"，
   工作做完、通知只发一次、代数不再加；
3. 无事件到期：授权时长过了以后重启，启动对账补发到期事件，有效性消费者观察到到期；再重启不重复补发。
"""
from _product_seam import EVIDENCE, ReviewScript, count, quick, source_sha256, write_report  # noqa: F401

import asyncio
import dataclasses
import sqlite3
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.orchestrator.assurance_consumers import NOTIFICATION_EVENT, VALIDITY_CHECKED_EVENT
from agent_orchestrator.storage.store import InjectedCrash
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

GOAL = {"goal": "写一份 NOTES.md，列出三条要点。", "success_criteria": ["file:NOTES.md"]}


def events(store: Any, mission_id: str, kind: str) -> list[Any]:
    return [e for e in store.iter_events(mission_id) if e.type == kind]


def pending(store: Any, mission_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in store.connection.execute(
        "SELECT consumer,work_key,state,tries,owner FROM assurance_pending_work WHERE mission_id=? "
        "ORDER BY consumer,work_key", (mission_id,)).fetchall()]


def environment(store: Any) -> dict[str, Any]:
    return dict(store.connection.execute("SELECT clock_generation,wall_high_ms,clock_state FROM "
                                         "assurance_environment_state WHERE singleton=1").fetchone())


async def drain(tick: Any, limit: int = 20) -> int:
    rounds = 0
    while rounds < limit and await tick.tick():
        rounds += 1
    return rounds


def edit_db(root: Path, sql: str, *params: Any) -> int:
    connection = sqlite3.connect(root / "orchestrator.db")
    with connection:
        changed = connection.execute(sql, params).rowcount
    connection.close()
    return changed


async def cold_resume(root: Path, report: dict[str, Any]) -> None:
    first = ReviewScript()
    async with product_world(root, first) as world:
        mission_id = world.create({**GOAL, "idempotency_key": "cold-1"})["mission_id"]
        # 内容审阅那一层判过、记下以后（第三层：格式、规则、审阅）进程被杀。
        world.loop.arm_fault("after_layer_pass", kind="attempt", skip=2)
        fired = None
        try:
            async with asyncio.timeout(60):
                while str(world.store.get_mission(mission_id).status.value) not in {"COMPLETED", "FAILED"}:
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=world.auto)
        except InjectedCrash:
            fired = world.store.fired[-1]
        assert fired == "after_layer_pass:attempt", fired
        layers = [e.payload.get("layer") for e in events(world.store, mission_id, "VerificationLayerRecorded")]
        assert layers[-1] == "critic_review", layers
        calls_before = dict(first.review_calls)
        invocations = [dict(r) for r in world.store.connection.execute(
            "SELECT review_key,ordinal FROM assurance_review_invocations WHERE mission_id=?", (mission_id,))]
        assert invocations and all(i["ordinal"] == 1 for i in invocations), invocations
        running = [w for w in pending(world.store, mission_id) if w["state"] == "RUNNING"]
    second = ReviewScript()
    async with product_world(root, second) as world:
        startup = dict(world.deployment.assurance.startup)
        mission = await world.run_until_settled(mission_id, timeout=60)
        assert str(mission.status.value) == "COMPLETED", mission.final_report
        after = [dict(r) for r in world.store.connection.execute(
            "SELECT review_key,ordinal FROM assurance_review_invocations WHERE mission_id=?", (mission_id,))]
        crashed_keys = {i["review_key"] for i in invocations}
        reused = [i for i in after if i["review_key"] in crashed_keys]
        assert reused and all(i["ordinal"] == 1 for i in reused), after
        official = world.store.connection.execute(
            "SELECT purpose,count(*) FROM review_records WHERE mission_id=? AND official=1 GROUP BY purpose",
            (mission_id,)).fetchall()
        assert calls_before.get("TASK_CONTENT") == 1 and second.review_calls.get("TASK_CONTENT", 0) == 0, (
            calls_before, second.review_calls)
        report["cold_resume"] = {
            "fault": fired, "review_calls_before_crash": calls_before, "review_calls_after_restart": second.review_calls,
            "invocations_at_crash": invocations, "ordinals_after": sorted({i["ordinal"] for i in reused}),
            "running_claims_at_crash": running, "startup_running_claims": startup.get("running_claims"),
            "official_records": {row[0]: row[1] for row in official}, "mission": "COMPLETED",
            "layers_at_crash": layers}


async def clock_rollback(root: Path, report: dict[str, Any]) -> None:
    async with product_world(root, LayeredScriptedProvider(), auto=False) as world:
        mission_id = world.create({**GOAL, "idempotency_key": "clock-1"})["mission_id"]
        await drain(world.deployment.assurance.tick)
        first = environment(world.store)
        assert first["clock_state"] == "STABLE", first
    ahead_ms = 4_000
    assert edit_db(root, "UPDATE assurance_environment_state SET wall_high_ms=?,row_version=row_version+1 WHERE singleton=1",
                   int(time.time() * 1000) + ahead_ms) == 1
    async with product_world(root, LayeredScriptedProvider(), auto=False) as world:
        store = world.store
        installed = world.deployment.assurance
        startup = dict(installed.startup)
        assert startup["missions"] == 1 and startup["clock_state"] == "ROLLBACK", startup
        assert startup["clock_generation"] == first["clock_generation"] + 1, (startup, first)
        discontinuities = events(store, mission_id, "TimeDiscontinuity")
        assert len(discontinuities) == 1 and discontinuities[0].payload["clock_state"] == "ROLLBACK"
        world.control.cancel(mission_id)
        assert events(store, mission_id, NOTIFICATION_EVENT)
        rolled_rounds = await drain(installed.tick)
        during = pending(store, mission_id)
        assert {(w["consumer"], w["state"]) for w in during} >= {("NOTIFY", "PENDING")}, during
        assert all(w["state"] != "RUNNING" for w in during) and world.notices == [], (during, world.notices)
        assert environment(store)["clock_state"] == "ROLLBACK"
        await asyncio.sleep(max(0.0, environment(store)["wall_high_ms"] / 1000 - time.time()) + 0.3)
        stable_rounds = await drain(installed.tick)
        stable = events(store, mission_id, "AssuranceClockStable")
        env = environment(store)
        assert len(stable) == 1 and env["clock_state"] == "STABLE", (len(stable), env)
        assert env["clock_generation"] == first["clock_generation"] + 1
        after = pending(store, mission_id)
        assert all(w["state"] == "DONE" for w in after), after
        sent = [n for n in world.notices if n.get("mission_id") == mission_id]
        assert len(sent) == 1, world.notices
        report["restart_rollback"] = {"first_environment": first, "startup": startup,
                                      "discontinuity": discontinuities[0].payload, "work_during_rollback": during,
                                      "rounds_rolled_back": rolled_rounds, "rounds_stable": stable_rounds,
                                      "environment_after": env, "sent": len(sent)}


async def eventless_expiry(root: Path, report: dict[str, Any]) -> None:
    policy = dataclasses.replace(DeploymentPolicy(), approval_ttl_seconds=3.0)
    async with product_world(root, LayeredScriptedProvider(), deployment_policy=policy) as world:
        mission_id = world.create({**GOAL, "idempotency_key": "expiry-1"})["mission_id"]
        mission = await world.run_until_settled(mission_id, timeout=60)
        assert str(mission.status.value) == "COMPLETED", mission.final_report
        deadlines = [r[0] for r in world.store.connection.execute(
            "SELECT not_after_ms FROM assurance_use_certificates WHERE mission_id=? AND not_after_ms IS NOT NULL "
            "AND json_extract(certificate_json,'$.decision')='USABLE'", (mission_id,))]
        assert deadlines
        notices = len(world.notices)
    await asyncio.sleep(max(0.0, max(deadlines) / 1000 - time.time()) + 0.5)
    async with product_world(root, LayeredScriptedProvider(), deployment_policy=policy) as world:
        startup = dict(world.deployment.assurance.startup)
        assert startup["expiry_events_emitted"] >= 1, startup
        await drain(world.deployment.assurance.tick)
        expired = [e.payload for e in events(world.store, mission_id, VALIDITY_CHECKED_EVENT)
                   if "EXPIRED" in e.payload.get("reasons", ())]
        assert expired
        assert world.notices == []
    async with product_world(root, LayeredScriptedProvider(), deployment_policy=policy) as world:
        again = dict(world.deployment.assurance.startup)
        assert again["expiry_events_emitted"] == 0, again
    report["eventless_expiry"] = {"usable_with_deadline": len(deadlines), "startup_emitted": startup["expiry_events_emitted"],
                                  "expired_observations": len(expired), "second_restart_emitted": 0,
                                  "notices_before_restart": notices}


async def main() -> None:
    quick()
    report: dict[str, Any] = {
        "status": "PASS",
        "scope": "product deployment, real main loop and restarts on one root: a crash right after the content "
                 "review layer is recorded (product crash point) -> restart reuses the original review invocation "
                 "(no second TASK_CONTENT model call); a restart "
                 "with the persisted clock high-water mark ahead of the wall clock -> rollback recorded before any "
                 "claim, work ingested but not claimed, resume on STABLE, NOTIFY once; certificate expiry with no "
                 "business event rebuilt at restart. Scripted model replies only; not a real model, Host or UI."}
    with TemporaryDirectory(prefix="assurance-recovery-") as temp:
        base = Path(temp).resolve()
        await cold_resume(base / "cold", report)
        await clock_rollback(base / "clock", report)
        await eventless_expiry(base / "expiry", report)
    report["source_sha256"] = source_sha256([
        "orchestrator/assurance_assembly.py", "orchestrator/assurance_review_pins.py", "orchestrator/assurance_tick.py",
        "orchestrator/assurance_clock.py", "storage/assurance_work.py", "assurance/expiry.py"])
    write_report("recovery-seam", report)


if __name__ == "__main__":
    asyncio.run(main())
