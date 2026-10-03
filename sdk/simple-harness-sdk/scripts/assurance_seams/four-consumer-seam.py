"""四个保证通道消费者接缝（产品同形世界，2026-10-03 迁离 ``_assured_fixture``）。

产品那一份部署在编排服务启动时装上保证通道（审阅 / 有效性 / 收尾 / 通知四个消费者、固定主体的当前读
权限、任务工厂、启动对账），主循环真跑一个用户任务到完成。替身只有脚本化的模型回复；"时间流逝"用
部署策略里真实的授权时长（几秒）加真实等待，不改库的时钟；"游标丢了"是磁盘上那一行没了（字节
损坏，分诊裁决①b1），重启后由启动对账重建。

报告里的事实：
* 安装：四个消费者都在，新库启动对账为空，第二次安装按名拒绝；
* 建任务：任务在保证通道上，四个游标都停在"保证档案已激活"那条事件上，原始要求由登录用户确认；
* 当前读权限：同一主体同一用途得到同一份权限，别的用途不同，别的主体 / 租户 / 不认识的用途被拒；
* 跑完：方法计划审查（在任何完成范围之前）有正式记录、绑定里没有范围；收尾从"差根结论"一路走到
  定稿；完成通知只发一次；任务完成以后空转一轮不自己触发；
* 到期：授权时长过了以后，有效性消费者观察到证书到期；
* 重启：通知游标丢了，启动对账重建它、按稳定键重放，通知不重发。
"""
from _product_seam import EVIDENCE, ReviewScript, count, quick, source_sha256, write_report  # noqa: F401

import asyncio
import dataclasses
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent_orchestrator.assurance.codec import AssuranceError, decode
from agent_orchestrator.assurance.refs import AssuranceRef, Pin
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.orchestrator.assurance_assembly import AssuranceDeploymentPorts, install_assurance
from agent_orchestrator.orchestrator.assurance_consumers import CLOSEOUT_EVENT, NOTIFIED_EVENT, VALIDITY_CHECKED_EVENT
from agent_orchestrator.storage.assurance_store import AssuranceStore
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.product_world import TENANT, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

TTL_SECONDS = 4.0
CONSUMERS = {"REVIEW", "VALIDITY", "CLOSEOUT", "NOTIFY"}


def refused(call: Any, codes: set[str]) -> str:
    try:
        call()
    except AssuranceError as error:
        assert error.code in codes, (error.code, codes)
        return str(error.code)
    raise AssertionError("expected refusal")


def pending(store: Any, mission_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in store.connection.execute(
        "SELECT consumer,work_key,state,tries FROM assurance_pending_work WHERE mission_id=? ORDER BY consumer,work_key",
        (mission_id,)).fetchall()]


async def drain(tick: Any, limit: int = 20) -> int:
    rounds = 0
    while rounds < limit and await tick.tick():
        rounds += 1
    return rounds


def events(store: Any, mission_id: str, kind: str) -> list[Any]:
    return [e for e in store.iter_events(mission_id) if e.type == kind]


async def first_life(root: Path, policy: DeploymentPolicy, report: dict[str, Any]) -> str:
    async with product_world(root, LayeredScriptedProvider(), deployment_policy=policy) as world:
        store = world.store
        installed = world.deployment.assurance
        assert set(installed.consumers) == CONSUMERS
        assert world.loop._assurance_tick is installed.tick
        startup = {k: installed.startup[k] for k in ("missions", "cursors_rebuilt", "expiry_events_emitted",
                                                       "pending_work")}
        assert startup == {"missions": 0, "cursors_rebuilt": [], "expiry_events_emitted": 0, "pending_work": {}}, startup
        second = refused(lambda: install_assurance(world.loop, AssuranceDeploymentPorts(
            tenant_id=TENANT, principal=world.deployment.principal)), {"ASSURANCE_ALREADY_INSTALLED"})

        created = world.create({"goal": "写一份 NOTES.md，列出三条要点。", "success_criteria": ["file:NOTES.md"],
                                "idempotency_key": "four-consumer-1"})
        mission_id = created["mission_id"]
        assert AssuranceStore(store).lane(mission_id) == "ASSURANCE_1_1"
        requirements = HtnStore(store).get_requirements_revision(mission_id, 1)
        assert [c.criterion_id for c in requirements.criteria] == ["c-user-1"]
        assert requirements.authority_subject == world.deployment.principal.principal_id
        activation = events(store, mission_id, "AssuranceProfileActivated")[0]
        cursors = {r[0]: r[1] for r in store.connection.execute(
            "SELECT consumer,last_event_seq FROM assurance_event_cursors WHERE mission_id=?", (mission_id,))}
        assert set(cursors) == CONSUMERS and set(cursors.values()) == {activation.seq}, cursors

        # 当前读权限：部署装上的那一份（门面的实际调用者），只读，什么都不写。
        read = installed.authority.read
        principal = world.deployment.principal
        ref = AssuranceRef("requirements", Pin(str(requirements.revision_id), 1, requirements.content_hash()))
        before = store.connection.total_changes
        permission = read(principal, TENANT, mission_id, ref, "DISCLOSE")
        same = read(principal, TENANT, mission_id, ref, "DISCLOSE")
        other = read(principal, TENANT, mission_id, ref, "CONTEXT")
        assert same.access == permission.access and same.policy == permission.policy
        assert other.access != permission.access and other.policy != permission.policy
        assert permission.not_after_ms - int(store.now * 1000) <= TTL_SECONDS * 1000
        refused(lambda: read(Principal("someone-else"), TENANT, mission_id, ref, "DISCLOSE"), {"ROOT_READ_NOT_AUTHORIZED"})
        refused(lambda: read(principal, "other-tenant", mission_id, ref, "DISCLOSE"), {"ROOT_READ_NOT_AUTHORIZED"})
        refused(lambda: read(principal, TENANT, mission_id, ref, "BOGUS"), {"CURRENT_READ_AUTHORITY_REQUIRED"})
        assert store.connection.total_changes == before

        mission = await world.run_until_settled(mission_id, timeout=60)
        assert str(mission.status.value) == "COMPLETED", mission.final_report
        closeouts = [e.payload for e in events(store, mission_id, CLOSEOUT_EVENT)]
        states = [c["state"] for c in closeouts]
        assert states and states[-1] in {"READY", "FINALIZED"}, states
        row = store.connection.execute("SELECT state FROM assurance_closeouts WHERE mission_id=?",
                                       (mission_id,)).fetchone()
        assert row is not None and row[0] == "FINALIZED", row
        notices = [n for n in world.notices if n.get("mission_id") == mission_id]
        assert len(notices) == 1 and len(events(store, mission_id, NOTIFIED_EVENT)) == 1, notices

        # 方法计划审查发生在任何完成范围之前：正式记录、绑定里没有范围。
        method_plan = []
        for (binding_json,) in store.connection.execute(
                "SELECT binding_json FROM assurance_review_bindings WHERE mission_id=?", (mission_id,)):
            bound = decode(binding_json)
            if bound["subject"]["purpose"] == "METHOD_PLAN":
                method_plan.append(bound)
        assert method_plan and all(b["subject"]["completion_scope_ref"] is None for b in method_plan)
        official = count(store, "SELECT count(*) FROM review_records WHERE mission_id=? AND official=1 "
                                "AND purpose='METHOD_PLAN'", mission_id)
        assert official >= 1

        idle = await drain(installed.tick)
        assert idle == 0 and not installed.tick.has_pending(), pending(store, mission_id)

        # 到期：部署策略给的授权时长过了以后，有效性消费者观察到证书到期。
        deadlines = [r[0] for r in store.connection.execute(
            "SELECT not_after_ms FROM assurance_use_certificates WHERE mission_id=? AND not_after_ms IS NOT NULL "
            "AND json_extract(certificate_json,'$.decision')='USABLE'", (mission_id,))]
        assert deadlines
        wait = max(deadlines) / 1000 - store.now + 0.5
        await asyncio.sleep(max(0.0, wait))
        expiry_rounds = await drain(installed.tick)
        due = events(store, mission_id, "AssuranceUseExpiryDue")
        expired = [e.payload for e in events(store, mission_id, VALIDITY_CHECKED_EVENT)
                   if "EXPIRED" in e.payload.get("reasons", ())]
        assert due and expired, (len(due), len(expired))
        assert {c["certificate_id"] for c in expired} >= {e.payload["certificate_id"] for e in due}
        assert len([n for n in world.notices if n.get("mission_id") == mission_id]) == 1

        report["production_install"] = {
            "consumers": sorted(installed.consumers), "startup": startup, "second_install": second,
            "lane": "ASSURANCE_1_1", "cursor_seq": cursors, "requirements": [c.criterion_id for c in requirements.criteria],
            "authority": {"access_key": permission.access.key, "policy_key": permission.policy.key},
            "idle_rounds": idle}
        report["run"] = {"mission": "COMPLETED", "closeout_states": states, "closeout_row": row[0],
                         "notices": len(notices), "method_plan_official": official,
                         "method_plan_scope": None}
        report["expiry"] = {"usable_certificates_with_deadline": len(deadlines), "due_events": len(due),
                            "expired_observations": len(expired), "rounds": expiry_rounds}
        return mission_id


async def second_life(root: Path, policy: DeploymentPolicy, mission_id: str, report: dict[str, Any]) -> None:
    async with product_world(root, LayeredScriptedProvider(), deployment_policy=policy) as world:
        installed = world.deployment.assurance
        summary = dict(installed.startup)
        assert summary["cursors_rebuilt"] == [mission_id + ":NOTIFY"] and summary["missions"] == 1, summary
        await drain(installed.tick)
        await world.drain(timeout=10)
        assert world.notices == [], world.notices
        assert len(events(world.store, mission_id, NOTIFIED_EVENT)) == 1
        report["restart"] = {"cursor_rebuild": {k: summary[k] for k in ("missions", "cursors_rebuilt")},
                             "notices_after_restart": len(world.notices)}


async def main() -> None:
    quick()
    report: dict[str, Any] = {
        "status": "PASS",
        "scope": "product deployment, real main loop: install_assurance at startup (four consumers, fixed-principal "
                 "current authority, factory, startup reconciliation), a user Mission to COMPLETED (METHOD_PLAN before "
                 "any Scope, closeout to FINALIZED, NOTIFY once), certificate expiry after the deployment's real "
                 "authority lifetime, a lost NOTIFY cursor rebuilt at restart without a second notification. "
                 "Scripted model replies only; not a real model, Host or UI."}
    policy = dataclasses.replace(DeploymentPolicy(), approval_ttl_seconds=TTL_SECONDS)
    with TemporaryDirectory(prefix="assurance-four-consumer-") as temp:
        root = Path(temp).resolve() / "root"
        mission_id = await first_life(root, policy, report)
        import sqlite3

        connection = sqlite3.connect(root / "orchestrator.db")
        with connection:
            lost = connection.execute("DELETE FROM assurance_event_cursors WHERE mission_id=? AND consumer='NOTIFY'",
                                      (mission_id,)).rowcount
        connection.close()
        assert lost == 1
        await second_life(root, policy, mission_id, report)
    report["finished_wall"] = time.time()
    report["source_sha256"] = source_sha256([
        "orchestrator/assurance_assembly.py", "orchestrator/assurance_consumers.py", "orchestrator/assurance_factory.py",
        "orchestrator/assurance_tick.py", "orchestrator/assurance_review_consumer.py", "assurance/expiry.py",
        "deployment/assembly.py"])
    write_report("four-consumer-seam", report)


if __name__ == "__main__":
    asyncio.run(main())
