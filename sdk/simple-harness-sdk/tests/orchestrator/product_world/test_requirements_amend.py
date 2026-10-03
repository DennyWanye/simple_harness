# SPDX-License-Identifier: Apache-2.0
"""用户中途改要求（HTN 补齐阶段 E）。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.api.facade import FacadeError
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.obligation_store import ObligationStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, planner_reply

SOURCE = {"kind": "MAIN_AGENT", "run_id": "run-1", "call_id": "call-1", "permission_mode": "auto"}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def latest_ref(store: Any, mission_id: str) -> dict[str, Any]:
    latest = HtnStore(store).latest_requirements_revision(mission_id)
    return {"id": str(latest.revision_id), "revision": int(latest.revision), "content_hash": latest.content_hash()}


def amend(world: Any, mission_id: str, changes: list[dict[str, Any]], *, command_id: str = "amend-1",
          expected: dict[str, Any] | None = None) -> dict[str, Any]:
    return world.control.amend_requirements({
        "mission_id": mission_id, "command_id": command_id,
        "expected_requirements_ref": expected or latest_ref(world.store, mission_id),
        "changes": changes, "reason": "用户补充了要求", "source": SOURCE})


def written(store: Any, mission_id: str) -> dict[str, Any]:
    """The seven things an amendment writes, as they stand."""
    htn = HtnStore(store)
    root_task = next(row[0] for row in store.connection.execute(
        "SELECT task_id FROM task_semantics WHERE mission_id=? AND task_id LIKE 'user-root-%'", (mission_id,)))
    root = htn.latest_task_semantics(root_task)
    duty = ObligationStore(store).obligation(mission_id, root.obligation_id)
    return {
        "revisions": [int(item.revision) for item in htn.list_requirements_revisions(mission_id)],
        "root_contract": int(root.contract_revision), "root_refs": tuple(root.requirement_refs),
        "duty_refs": tuple(duty.requirement_refs), "epoch": htn.epoch(mission_id, "mission"),
        "events": len([e for e in store.list_events(mission_id) if e.type == "RequirementsAmended"]),
        "receipts": store.connection.execute(
            "SELECT count(*) FROM commit_receipts WHERE kind='requirements_amended' AND subject_id=?",
            (mission_id,)).fetchone()[0],
    }


async def until_first_plan(world: Any, mission_id: str) -> None:
    for _ in range(12):
        await world.drain(timeout=20)
        plan = HtnStore(world.store).active_plan_revision(mission_id)
        if plan is not None and int(plan.revision) >= 1:
            return
    raise AssertionError("the first plan never committed")


def test_amend_writes_everything_in_one_transaction(tmp_path):
    """改写一条、删一条、加两条：七样在一个事务里写；编号不复用；同一命令重放回同一回执；
    旧版本号、收尾中的任务、写到一半出错——都按名拒绝且一样都没写。

    **改坏检验**：根义务改写挪到事务外 → 注入失败的子情形里义务已被改 → 变红；
    新增编号取"条数 + 1" → 先删后加撞上已用过的编号 → 变红。"""
    async def case():
        provider = LayeredScriptedProvider(planner=planner_reply)
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写三份文件", "idempotency_key": "amend-tx",
                                       "success_criteria": ["file:a.md", "file:b.md", "file:c.md"]})["mission_id"]
            await until_first_plan(world, mission_id)
            before = written(world.store, mission_id)
            assert before["revisions"] == [1] and before["root_contract"] == 1

            stale = dict(latest_ref(world.store, mission_id), content_hash="0" * 64)
            with pytest.raises(FacadeError) as refused:
                amend(world, mission_id, [{"op": "add", "statement": "file:d.md"}], expected=stale)
            assert refused.value.code == "AMEND_REQUIREMENTS_STALE"
            with pytest.raises(FacadeError) as refused:
                amend(world, mission_id, [{"op": "remove", "criterion_id": "c-user-9"}])
            assert refused.value.code == "AMEND_UNKNOWN_CRITERION"
            # 写到一半出错：事件写入失败 → 前面写的全部回滚
            import agent_orchestrator.orchestrator.requirements_amendment as amendment
            import agent_orchestrator.orchestrator.hierarchical_dispatch as hierarchical

            real = hierarchical.append_hierarchical_event

            def broken(store, event_type, *args, **kwargs):
                if event_type == amendment.EVENT:
                    raise amendment.StoreError("injected fault")
                return real(store, event_type, *args, **kwargs)

            hierarchical.append_hierarchical_event = broken
            try:
                with pytest.raises(FacadeError):
                    amend(world, mission_id, [{"op": "add", "statement": "file:d.md"}])
            finally:
                hierarchical.append_hierarchical_event = real
            assert written(world.store, mission_id) == before  # 一样都没写

            changes = [{"op": "rewrite", "criterion_id": "c-user-1", "statement": "file:a.md 且用中文写"},
                       {"op": "remove", "criterion_id": "c-user-3"},
                       {"op": "add", "statement": "file:d.md"}]
            receipt = amend(world, mission_id, changes)
            after = written(world.store, mission_id)
            assert after == {"revisions": [1, 2], "root_contract": 2,
                             "root_refs": ("c-user-1", "c-user-2", "c-user-4"),
                             "duty_refs": ("c-user-1", "c-user-2", "c-user-4"),
                             "epoch": before["epoch"] + 1, "events": 1, "receipts": 1}
            latest = HtnStore(world.store).latest_requirements_revision(mission_id)
            assert latest.amendment_credential_ref == "amend-1"
            assert {str(c.criterion_id): int(c.revision) for c in latest.criteria} == {
                "c-user-1": 2, "c-user-2": 1, "c-user-4": 1}
            assert receipt["changes"] == {"added": ["c-user-4"], "rewritten": ["c-user-1"], "removed": ["c-user-3"]}
            [bumped_by] = [row[0] for row in world.store.connection.execute(
                "SELECT bumped_by FROM validity_epochs WHERE mission_id=? AND scope_id='mission'", (mission_id,))]
            assert bumped_by == f"requirements:{latest.revision_id}"
            # 同一命令重放：同一回执，不再写
            assert amend(world, mission_id, changes, expected=receipt["previous_requirements_ref"]) == receipt
            assert written(world.store, mission_id) == after
            # 先删后加：删掉的 c-user-3 不复用
            again = amend(world, mission_id, [{"op": "remove", "criterion_id": "c-user-4"},
                                              {"op": "add", "statement": "file:e.md"}], command_id="amend-2")
            assert again["changes"]["added"] == ["c-user-5"]

    asyncio.run(case())


# ----------------------------------------------------------------------------- 改要求后重新规划

def parallel_method(context: dict[str, Any]) -> dict[str, Any]:
    """每条要求一个互不依赖的步骤（步骤名 s1、s2…），最后一步收尾。"""
    from agent_orchestrator.testing.scripted_replies import one_step_method

    request = context["request"]
    method = one_step_method(context)
    [step] = method["steps"]
    names = [item["id"] for item in request["criterion_evidence"]]
    method["steps"] = [dict(step, local_id=f"s{n}") for n in range(1, len(names) + 1)]
    method["ordering"] = []
    method["composition"]["criterion_links"] = [
        {"parent_criterion_id": name, "child_step": f"s{n}", "child_criterion_id": name,
         "evidence_requirement": f"s{n} 这一步写出 {name} 要求的文件"} for n, name in enumerate(names, start=1)]
    method["composition"]["finalizer_step"] = f"s{len(names)}"
    return method


def replanning_planner(seen: dict[str, Any]):
    """没有做法时提"每条要求一步"的做法并采用；收到"要求已更新"后为根目标提新做法、再换上去。"""
    from agent_orchestrator.testing.scripted_replies import decision

    def planner(request: Any) -> Any:
        package = package_of(request)
        seen.setdefault("packages", []).append(package)
        contexts = package.get("method_proposal_contexts") or []
        repairs = [entry["request"] for entry in package.get("repair_requests") or ()
                   if entry["request"].get("trigger_source") == "REQUIREMENTS_UPDATE"]
        if not repairs:
            if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
                return decision(contexts[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                    "method": parallel_method(contexts[0]), "rationale": "每条要求一步。"}}, "每条要求一步。")
            return planner_reply(request)
        seen.setdefault("updates", []).extend(repairs)
        goal = next(item for item in package["views"]["goals"]
                    if item["form"] == "compound" and item.get("adopted_method"))
        subject = goal["subject_key"]
        current = goal["adopted_method"]["method_ref"]
        fresh = [item["method_ref"] for item in package["views"]["methods"]
                 if item["method_ref"] != current and (item.get("review") or {}).get("outcome") == "PASSED"
                 and item["method_ref"]["id"] in seen.get("proposed", ())]
        if not fresh:
            [context] = [item for item in contexts if item["subject_key"] == subject]
            method = parallel_method(context)
            seen.setdefault("proposed", []).append(method["method_id"])
            seen["proposed_version"] = method["method_version"]
            return decision(subject, "PROPOSE_METHOD", {"method_proposal": {
                "method": method, "rationale": "按新要求重排。"}}, "要求改了，换做法。")
        instance = next(item for item in package["visible_refs"] if item["kind"] == "method_instance"
                        and item["id"] == goal["adopted_method"]["method_instance_id"])
        return decision(subject, "REPAIR", {"repair_kind": "REPLACE_METHOD", "rejected_method_instance": instance,
                                            "replacement_method_ref": dict(fresh[-1]), "bindings": goal["params"]},
                        "换成按新要求写的做法。")

    return planner


class SlowSecondStep(LayeredScriptedProvider):
    """写 b.md 的那一步一直在跑，直到测试放行。"""

    def __init__(self, **roles: Any) -> None:
        super().__init__(**roles)
        self.go = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        from agent_orchestrator.testing.fixtures import role_of

        if role_of(request) == "worker" and package_of(request).get("task_contract", {}).get("outputs") == ["b.md"]:
            await self.go.wait()
        return await super().invoke(request, cancel=cancel)


def test_amend_holds_dispatch_then_replans_and_delivers(tmp_path):
    """第 1 步验收后用户加一条要求：旧计划不再派新尝试；自动模式代确认第 2 版；规划器收到
    "要求已更新"（带改了哪几条）并按第 2 版重排；任务按第 2 版完成。"""
    seen: dict[str, Any] = {}

    async def case():
        provider = SlowSecondStep(planner=replanning_planner(seen))
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md 和 b.md", "idempotency_key": "amend-replan",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            htn = HtnStore(world.store)
            for _ in range(20):
                await world.drain(timeout=20)
                if htn.list_acceptances(mission_id):
                    break
            assert htn.list_acceptances(mission_id), "a.md 那一步没有通过验收"
            amend(world, mission_id, [{"op": "add", "statement": "file:extra.md"}])
            mark = max(e.seq for e in world.store.list_events(mission_id))
            provider.go.set()
            mission = await world.run_until_settled(mission_id, rounds=40)
            events = list(world.store.list_events(mission_id))
            assert str(mission.status.value) == "COMPLETED", (
                mission.status, mission.final_report,
                [(e.type, json.dumps(e.payload, ensure_ascii=False)[:300]) for e in events
                 if e.type in {"PlanningRejected", "PlanningRepairRequested", "MissionStalled"}][-6:])

            # 自动模式：系统代确认了第 2 版（纯内容要求）
            approvals = [e.payload for e in events if e.seq > mark and e.type == "OperationCompletionSpecApproved"]
            assert [a["approval_source"] for a in approvals] == ["HOST_AUTO_PERMISSION"]
            # 规划包给的是现行要求（第 2 版），旧版通过的那一步如实标出
            asked = next(p for p in seen["packages"] if any(
                entry["request"].get("trigger_source") == "REQUIREMENTS_UPDATE"
                for entry in p.get("repair_requests") or ()))
            assert asked["mission"]["requirements"]["revision"] == 2
            assert [c["id"] for c in asked["mission"]["requirements"]["criteria"]] == [
                "c-user-1", "c-user-2", "c-user-3"]
            assert "success_criteria" not in asked["mission"]
            assert [(row["requirements_revision"], row["counts_under_current"])
                    for row in asked["views"]["accepted_results"]] == [(1, False)]
            # 改要求之后、新计划提交之前：没有新尝试
            committed = next(e.seq for e in events if e.seq > mark and e.type == "PlanningDecisionEvaluated"
                             and e.payload.get("status") == "COMMITTED")
            assert not [e for e in events if mark < e.seq < committed and e.type == "AttemptCreated"]
            # 规划器收到的事实
            [update] = seen["updates"][:1]
            assert update["context"]["changes"] == {"added": ["c-user-3"], "rewritten": [], "removed": []}
            assert update["context"]["previous_revision"] == 1
            # 新计划按第 2 版；根结论按第 2 版
            assert int(htn.active_plan_revision(mission_id).read_set.requirements_revision) == 2
            resolution = htn.adopted_goal_resolution(mission_id, f"user-duty-{mission_id}")
            assert int(resolution.requirements_version) == 2
            [judged] = [e.payload for e in events if e.type == "MissionSuccessJudged"]
            assert [j["criterion"] for j in judged["judgments"]] == ["file:a.md", "file:b.md", "file:extra.md"]
            assert judged["met"] is True

    asyncio.run(case())


def test_planning_waits_for_the_amended_requirements_to_be_confirmed(tmp_path):
    """第 2 版要求还没人确认时，规划器一次都不被问（它出的计划必在冻结完成范围时被拒）；
    用户确认之后才问，任务按第 2 版完成。

    **改坏检验**：开工闸门只在第一次规划前看 → 确认前规划器就被问到 → 变红。"""
    seen: dict[str, Any] = {}

    async def case():
        provider = SlowSecondStep(planner=replanning_planner(seen))
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md 和 b.md", "idempotency_key": "amend-confirm",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            await until_first_plan(world, mission_id)
            # 从这里起没有"自动代确认"：等同于手动模式下等用户点确认
            world.deployment.duties.auto_confirm_content_completion = lambda **_: 0
            amend(world, mission_id, [{"op": "add", "statement": "file:extra.md"}])
            asked_before = len(seen["packages"])
            for _ in range(4):
                await world.drain(timeout=20)
            assert len(seen["packages"]) == asked_before, [  # 规划器没有被问
                ([e["request"].get("trigger_source") for e in p.get("repair_requests") or ()],
                 p["mission"]["requirements"]["revision"]) for p in seen["packages"][asked_before:]] + [
                e.payload.get("approval_source") for e in world.store.list_events(mission_id)
                if e.type == "OperationCompletionSpecApproved"]
            workspace = world.control.snapshot(mission_id)["snapshot"]["operation_workspace"]
            assert workspace["state"] == "CONFIRMATION_REQUIRED" and workspace["requirements_ref"]["revision"] == 2
            ref = workspace["requirements_ref"]
            world.control.approve_operation_completion_spec({
                "mission_id": mission_id, "command_id": "user-confirms-revision-2",
                "expected_requirements_ref": ref,
                "proposal": {"schema_version": 1, "mission_id": mission_id,
                             "requirements_ref": {k: ref[k] for k in ("id", "revision", "content_hash")},
                             "mode": "CONTENT_ONLY",
                             "content_criterion_ids": [c["id"] for c in workspace["criteria"]], "effects": []},
                "approval_source": "HUMAN"})
            provider.go.set()
            mission = await world.run_until_settled(mission_id, rounds=40)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            assert len(seen["packages"]) > asked_before and seen.get("updates")

    asyncio.run(case())
