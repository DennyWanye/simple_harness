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
from agent_orchestrator.testing.product_world import TENANT, product_world
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
            # 删掉最后一条、下一次再加：编号按"历来用过的最大号"往上走，不回头用 c-user-5
            amend(world, mission_id, [{"op": "remove", "criterion_id": "c-user-5"}], command_id="amend-3")
            later = amend(world, mission_id, [{"op": "add", "statement": "file:f.md"}], command_id="amend-4")
            assert later["changes"]["added"] == ["c-user-6"]

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
    """写 b.md 的那一步一直在跑，直到测试放行；记下每个执行者包。"""

    def __init__(self, **roles: Any) -> None:
        super().__init__(**roles)
        self.go = asyncio.Event()
        self.worker_packages: list[dict[str, Any]] = []

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        from agent_orchestrator.testing.fixtures import role_of

        if role_of(request) == "worker":
            self.worker_packages.append(package_of(request))
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
            # 窗口里：这版计划不再开新工（计划按第 1 版、现行第 2 版）
            dispatch = world.loop._dispatch_for(mission_id)
            assert dispatch.requirements_changed(mission_id)
            assert dispatch.requirements_revisions(mission_id) == {
                "planned_requirements_revision": 1, "current_requirements_revision": 2}
            # 派发处如实报出不开工的原因（产品只读接口"为什么还不开工"）
            reads = world.loop.taskgraph_read_api(tenant_id=TENANT, principal=world.control._principal)
            plan = htn.active_plan_revision(mission_id)
            reasons = {code for member in htn.list_plan_memberships(mission_id, plan.revision)
                       for code in reads.why_not_ready(mission_id, str(member.occurrence_id))["reason_codes"]}
            assert "requirements_changed" in reasons, reasons
            # 任务详情同一读法：按第 1 版通过的那一步，在第 2 版下不再算数（HTN 一致性补改 H-7）
            [stale] = world.control.snapshot(mission_id)["snapshot"]["steps_no_longer_counting"]
            assert stale["requirements_revision"] == 1 and stale["label"]
            assert stale["acceptance_id"] == str(htn.list_acceptances(mission_id)[0].acceptance_id)
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
            # 新计划里的执行者拿到的是第 2 版要求原文（阶段 E 欠的断言，HTN 补齐 F1）
            extra = [p for p in provider.worker_packages if "extra.md" in json.dumps(
                p.get("task_contract", {}).get("outputs") or [])]
            assert extra and extra[-1]["mission_success_criteria"] == ["file:a.md", "file:b.md", "file:extra.md"]

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


def test_a_result_that_lands_while_requirements_are_unconfirmed_is_not_reviewed_under_the_old_ones(tmp_path):
    """改了要求、第 2 版还没确认：在跑的那一步照常跑完交结果。它的完成范围是旧版的——不为它切审查包
    （审出来的验收也不会被接受），主循环不报故障；确认后规划器按新版重排，任务完成。"""
    seen: dict[str, Any] = {}

    async def case():
        provider = SlowSecondStep(planner=replanning_planner(seen))
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md 和 b.md", "idempotency_key": "amend-window",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            htn = HtnStore(world.store)
            for _ in range(20):
                await world.drain(timeout=20)
                if htn.list_acceptances(mission_id):
                    break
            confirm = world.deployment.duties.auto_confirm_content_completion
            world.deployment.duties.auto_confirm_content_completion = lambda **_: 0
            amend(world, mission_id, [{"op": "add", "statement": "file:extra.md"}])
            mark = max(e.seq for e in world.store.list_events(mission_id))
            provider.go.set()  # b.md 那一步在"要求已改、未确认"的窗口里跑完
            for _ in range(6):
                await world.drain(timeout=20)
            window = [e for e in world.store.list_events(mission_id) if e.seq > mark]
            rejected = [e.payload for e in window if e.type == "ResultRejected"]
            assert [r["reason"] for r in rejected] == ["superseded"]  # 归档为被取代，不算执行者做错
            assert not [e.payload for e in window if e.type in {"MissionRoundFault", "MissionFailed"}]
            assert not [e for e in window if e.type == "PlanningRepairRequested"
                        and e.payload["request"]["trigger_source"] == "WORKER_REJECT"]
            assert len(htn.list_acceptances(mission_id)) == 1  # 窗口里没有按旧版新增验收
            assert not [e for e in window if e.type == "AssuranceReviewImported"]  # 也没有白花一次审阅
            assert str(world.store.get_mission(mission_id).status.value) == "ACTIVE"  # 在等人确认，不是停滞
            world.deployment.duties.auto_confirm_content_completion = confirm
            mission = await world.run_until_settled(mission_id, rounds=40)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)

    asyncio.run(case())


def test_an_amendment_landing_while_a_check_is_imported_sets_that_verification_aside(tmp_path, monkeypatch):
    """改要求恰好落在一步的检查跑完、正要入账的那一刻（随机序列 F1-6 发现主循环在这里崩）：这次验证
    放下不提交，主循环不报故障；新计划把旧尝试归档，任务按第 2 版完成。

    **改坏检验**：验证处不认"要求已改" → 检查入账读不到旧版完成范围的错误冲出主循环 → 变红。"""
    from agent_orchestrator.orchestrator import assurance_local_checks

    seen: dict[str, Any] = {}
    race: dict[str, Any] = {"world": None, "mission_id": None, "fired": False}
    original = assurance_local_checks.LocalCheckImporter.import_executor

    def amended_mid_check(self, recorded, document):  # type: ignore[no-untyped-def]
        if not race["fired"] and race["mission_id"] is not None:
            race["fired"] = True
            amend(race["world"], race["mission_id"], [{"op": "add", "statement": "file:extra.md"}])
        return original(self, recorded, document)

    monkeypatch.setattr(assurance_local_checks.LocalCheckImporter, "import_executor", amended_mid_check)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=replanning_planner(seen))) as world:
            race["world"] = world
            mission_id = world.create({"goal": "写 a.md 和 b.md", "idempotency_key": "amend-mid-check",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            race["mission_id"] = mission_id
            try:
                mission = await world.run_until_settled(mission_id, rounds=40)
            except Exception as error:  # noqa: BLE001 - 冲出主循环就是这条要抓的缺陷
                raise AssertionError(f"main loop crashed: {type(error).__name__}: {error}") from error
            events = list(world.store.list_events(mission_id))
            assert race["fired"]
            assert not [e for e in events if e.type in {"MissionRoundFault", "MissionFailed"}]
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            [judged] = [e.payload for e in events if e.type == "MissionSuccessJudged"]
            assert [j["criterion"] for j in judged["judgments"]] == ["file:a.md", "file:b.md", "file:extra.md"]

    asyncio.run(case())


def test_an_amendment_landing_between_the_verdict_and_its_commit_sets_the_result_aside(tmp_path, monkeypatch):
    """验完、提交结论前用户改了要求：提交读完成范围时已过期，结果按"被取代"归档，主循环不崩，
    任务按第 2 版完成（阶段 G 随机序列发现；与验证开头同一条规则）。

    **改坏检验**（G-23）：提交结论处不认"完成范围已过期" → 错误冲出主循环 → 变红。"""
    from agent_orchestrator.orchestrator.commit_service import CommitService

    seen: dict[str, Any] = {}
    race: dict[str, Any] = {"world": None, "mission_id": None, "fired": False}
    original = CommitService.accept_result

    def amended_before_commit(self, result_id, **kwargs):  # type: ignore[no-untyped-def]
        if not race["fired"] and race["mission_id"] is not None:
            race["fired"] = True
            amend(race["world"], race["mission_id"], [{"op": "add", "statement": "file:extra.md"}])
        return original(self, result_id, **kwargs)

    monkeypatch.setattr(CommitService, "accept_result", amended_before_commit)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=replanning_planner(seen))) as world:
            race["world"] = world
            mission_id = world.create({"goal": "写 a.md 和 b.md", "idempotency_key": "amend-before-commit",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            race["mission_id"] = mission_id
            try:
                mission = await world.run_until_settled(mission_id, rounds=40)
            except Exception as error:  # noqa: BLE001 - 冲出主循环就是这条要抓的缺陷
                raise AssertionError(f"main loop crashed: {type(error).__name__}: {error}") from error
            events = list(world.store.list_events(mission_id))
            assert race["fired"] and str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            assert [e.payload["detail"]["error"] for e in events if e.type == "ResultRejected"
                    and e.payload.get("reason") == "superseded"][:1] == ["completion_scope_stale"]
            # 归档后再结清：尝试已关、它名下预留给审阅的尾部额度随之释放，必须留事件（阶段 G 收尾
            # 随机序列发现的静默改动）
            from agent_orchestrator.observability.business_replay import CONSISTENT, verify_mission
            report = verify_mission(world.store, mission_id)
            assert report["status"] == CONSISTENT, report["silent_changes"]

    asyncio.run(case())


def test_a_step_kept_by_the_replan_is_redone_when_the_amendment_lands_mid_check(tmp_path, monkeypatch):
    """改要求落在第一步的检查正要入账时，规划器只换掉另一步、把这一步原样留在新计划里（阻断核验 B1）：
    这一步的结果按"被取代"归档、尝试回到重试等待，新计划提交后由规划器定原样重做、通过；主循环不崩，任务按新版完成。

    **改坏检验**：验证处只"跳过等新计划"、不归档 → 新计划不关这一步的旧尝试 → 重新验证时按旧版读完成
    范围出错冲出主循环 → 变红。"""
    from agent_orchestrator.orchestrator import assurance_local_checks
    from agent_orchestrator.testing.scripted_replies import decision

    seen: dict[str, Any] = {}
    race: dict[str, Any] = {"world": None, "mission_id": None, "task_id": None}
    original = assurance_local_checks.LocalCheckImporter.import_executor

    def amended_mid_check(self, recorded, document):  # type: ignore[no-untyped-def]
        if race["task_id"] is None and race["mission_id"] is not None:
            world = race["world"]
            [running] = world.store.list_results_by_verification("RUNNING")
            race["task_id"] = str(running.envelope.task_id)
            amend(world, race["mission_id"], [{"op": "rewrite", "criterion_id": "c-user-2", "statement": "file:b2.md"}])
            world.provider.go.set()
        return original(self, recorded, document)

    monkeypatch.setattr(assurance_local_checks.LocalCheckImporter, "import_executor", amended_mid_check)
    base = replanning_planner(seen)

    def planner(request: Any) -> Any:
        package = package_of(request)
        updates = [entry for entry in package.get("repair_requests") or ()
                   if entry["request"].get("trigger_source") == "REQUIREMENTS_UPDATE"]
        if updates and "replaced" not in seen:
            seen["replaced"] = True
            [other] = [item for item in package["views"]["goals"]
                       if item["form"] == "primitive" and item["task_id"] != race["task_id"]]
            subject = next(row for row in package["planning_subjects"] if row["task_id"] == other["task_id"])
            visible = {(row["kind"], row["id"]): row for row in package["visible_refs"]}
            [task_type] = [row for row in package["successor_types"]
                           if row["statement"] == other["statement"] and row["form"] == "primitive"]
            return decision(subject["subject_key"], "REPAIR", {
                "repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible[("task", other["task_id"])],
                "obligation_ref": visible[("obligation", other["obligation_id"])],
                "goal_type_ref": task_type["task_type_ref"], "bindings": dict(other["params"])},
                "要求改了：只换掉另一步。")
        stuck = [entry["request"]["context"] for entry in package.get("repair_requests") or ()
                 if entry["request"].get("trigger_source") == "NO_DISPATCHABLE_WORK"]
        if stuck and race["task_id"] in (stuck[0].get("admitted_not_dispatched") or ()) and "retried" not in seen:
            # 留下的那一步上次结果被归档、在等规划器定：原样重做
            seen["retried"] = True
            attempts = race["world"].store.list_attempts(race["task_id"])
            latest = max(attempts, key=lambda attempt: attempt.ordinal)
            subject = next(row for row in package["planning_subjects"] if row["task_id"] == race["task_id"])
            goal = next(item for item in package["views"]["goals"]
                        if item["form"] == "compound" and item.get("adopted_method"))
            instance = next(row for row in package["visible_refs"] if row["kind"] == "method_instance"
                            and row["id"] == goal["adopted_method"]["method_instance_id"])
            return decision(subject["subject_key"], "REPAIR", {
                "repair_kind": "RETRY_SAME_METHOD", "failed_attempt_id": latest.id,
                "method_instance_ref": instance}, "这一步按旧版要求做的结果被归档，原样重做。")
        return base(request)

    async def case():
        provider = SlowSecondStep(planner=planner)
        async with product_world(tmp_path / "root", provider) as world:
            world.provider = provider
            race["world"] = world
            mission_id = world.create({"goal": "写 a.md 和 b.md", "idempotency_key": "amend-kept-mid-check",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            race["mission_id"] = mission_id
            try:
                # 有时限：结果不归档时会被反复重验、主循环一直不空闲（改坏后的样子），不能挂住
                mission = await asyncio.wait_for(world.run_until_settled(mission_id, rounds=60), 240)
            except TimeoutError as error:
                raise AssertionError("the mission never settled: the result keeps being re-verified") from error
            except Exception as error:  # noqa: BLE001 - 冲出主循环就是这条要抓的缺陷
                raise AssertionError(f"main loop crashed: {type(error).__name__}: {error}") from error
            events = list(world.store.list_events(mission_id))
            assert race["task_id"] and seen.get("replaced") and seen.get("retried")
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            set_aside = [e.payload for e in events if e.type == "ResultRejected"
                         and e.payload.get("reason") == "superseded" and e.task_id == race["task_id"]]
            assert set_aside and set_aside[0]["detail"]["error"] == "completion_scope_stale", set_aside
            assert len(world.store.list_attempts(race["task_id"])) >= 2  # 留下的那一步在新计划下重做
            [judged] = [e.payload for e in events if e.type == "MissionSuccessJudged"]
            assert [j["criterion"] for j in judged["judgments"]] == ["file:a.md", "file:b2.md"] and judged["met"]

    asyncio.run(case())


def test_kept_old_step_is_reviewed_again_not_rerun(tmp_path):
    """改要求后规划器只换掉了没做完的那一步，已按旧版通过的那一步原样留在计划里（TaskGraph 补全
    第四批）：系统不派执行者、不建尝试，请审阅员按新版要求把同一份结果再审一次；通过后按新版多一条
    验收，任务按新版完成。

    **改坏检验**：TG4-01 重审通过却不写新版验收 → 这一步一直不算数 → 变红。"""
    from agent_orchestrator.orchestrator.assurance_validity import acceptance_id_for
    from agent_orchestrator.testing.scripted_replies import decision

    seen: dict[str, Any] = {"packages": []}
    state = {"second": False}

    def successor(package: dict[str, Any], step: dict[str, Any], why: str) -> Any:
        subject = next(row for row in package["planning_subjects"] if row["task_id"] == step["task_id"])

        def visible(kind: str, identity: str) -> Any:
            return next(row for row in package["visible_refs"] if row["kind"] == kind and row["id"] == identity)

        [task_type] = [row for row in package["successor_types"]
                       if row["statement"] == step["statement"] and row["form"] == "primitive"]
        return decision(subject["subject_key"], "REPAIR", {
            "repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible("task", step["task_id"]),
            "obligation_ref": visible("obligation", step["obligation_id"]),
            "goal_type_ref": task_type["task_type_ref"], "bindings": dict(step["params"])}, why)

    base = replanning_planner(seen)

    def planner(request: Any) -> Any:
        package = package_of(request)
        sources = {entry["request"].get("trigger_source"): entry["request"]
                   for entry in package.get("repair_requests") or ()}
        steps = [item for item in package["views"]["goals"] if item["form"] == "primitive"]
        done = {row["producer_occurrence"] for row in package["views"]["accepted_results"]}
        if "REQUIREMENTS_UPDATE" in sources and not state["second"]:
            state["second"] = True
            [unfinished] = [item for item in steps if item["occurrence_id"] not in done]
            return successor(package, unfinished, "要求改了：只换掉还没做完的那一步，做完的那步留着。")
        return base(request)

    async def case():
        provider = SlowSecondStep(planner=planner)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md 和 b.md", "idempotency_key": "amend-kept",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            htn = HtnStore(world.store)
            for _ in range(20):
                await world.drain(timeout=20)
                if htn.list_acceptances(mission_id):
                    break
            [first] = htn.list_acceptances(mission_id)
            amend(world, mission_id, [{"op": "rewrite", "criterion_id": "c-user-2", "statement": "file:b2.md"}])
            provider.go.set()
            try:
                mission = await asyncio.wait_for(world.run_until_settled(mission_id, rounds=60), 180)
            except TimeoutError as error:  # 重审通过却不算数时会一直转（改坏后的样子）
                raise AssertionError("the mission never settled: the kept step never counts") from error
            events = list(world.store.list_events(mission_id))
            assert str(mission.status.value) == "COMPLETED", (
                mission.status, mission.final_report, state,
                [(e.type, json.dumps(e.payload, ensure_ascii=False)[:300]) for e in events
                 if e.type in {"PlanningRejected", "MissionStalled", "HierarchicalMissionStalled"}][-4:])
            assert state["second"]
            task_id = str(first.task_id)
            # 留着的那一步没有被重跑：它只有当初那一次尝试
            assert len(world.store.list_attempts(task_id)) == 1
            result_id = world.store.get_task(task_id).accepted_result_id
            # 按新版重审过（第 2 版要求下的审阅员一层通过），多了一条按第 2 版的验收
            again = world.store.list_verifications(result_id, requirements_revision=2)
            assert any(row["layer"] == "critic_review" and row["status"] == "PASS" for row in again), again
            ids = {str(item.acceptance_id): int(item.requirements_revision)
                   for item in htn.list_acceptances(mission_id) if str(item.task_id) == task_id}
            assert ids == {acceptance_id_for(task_id, result_id, 1): 1, acceptance_id_for(task_id, result_id, 2): 2}
            assert [e.payload["requirements_revision"] for e in events if e.type == "CarriedResultAccepted"] == [2]
            [judged] = [e.payload for e in events if e.type == "MissionSuccessJudged"]
            assert [j["criterion"] for j in judged["judgments"]] == ["file:a.md", "file:b2.md"] and judged["met"]
            # 按新版重审写下的每一层验证记录都有自己的事件：从事件重建与库一致（改坏 TG4-08）
            from agent_orchestrator.observability.business_replay import CONSISTENT, verify_mission
            report = verify_mission(world.store, mission_id)
            assert report["status"] == CONSISTENT, report["silent_changes"]

    asyncio.run(case())


def test_inflight_reply_and_pending_question_after_amend(tmp_path):
    """①规划器作答期间用户改了要求：那份回复按"请求过期"退回，不算它答错。
    ②根终审判不下来、裁决卡等用户时改要求：题目作废（它问的是旧版要求下的事），任务按新版重排后完成。"""
    from agent_orchestrator.contracts.error_table import refusal_charges_planner
    from agent_orchestrator.orchestrator.event_handler import _refusal_codes
    from agent_orchestrator.storage.planning_human_store import PlanningHumanStore
    from agent_orchestrator.testing.scripted_replies import decision, review_input, review_reply

    holder: dict[str, Any] = {}
    seen: dict[str, Any] = {}
    replan = replanning_planner(seen)
    state = {"amended_inflight": False, "final_reviews": 0}

    def planner(request: Any) -> Any:
        package = package_of(request)
        if not state["amended_inflight"]:
            state["amended_inflight"] = True  # ① 作答期间改要求
            amend(holder["world"], holder["mission_id"], [{"op": "add", "statement": "file:b.md"}],
                  command_id="amend-inflight")
        if any(entry["request"].get("trigger_source") == "REQUIREMENTS_UPDATE"
               for entry in package.get("repair_requests") or ()) and any(
                   item["form"] == "compound" and item.get("adopted_method") for item in package["views"]["goals"]):
            return replan(request)
        contexts = package.get("method_proposal_contexts") or []
        selection = (package.get("method_selection") or [{}])[0]
        if selection.get("applicable"):
            chosen = selection["applicable"][0]
            goal = next(item for item in package["views"]["goals"] if item["open"])
            return decision(goal["subject_key"], "REFINE", {
                "method_ref": {"kind": "method", "id": chosen["method_id"],
                               "semantic_revision": chosen["method_version"],
                               "content_hash": chosen["method_content_hash"]},
                "bindings": dict(selection.get("bindings") or goal["params"])}, "采用通过审阅的做法。")
        return decision(contexts[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
            "method": parallel_method(contexts[0]), "rationale": "每条要求一步。"}}, "每条要求一步。")

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) == "MISSION_FINAL":
            state["final_reviews"] += 1
            if state["final_reviews"] <= 2:
                return "我看过了，没有问题。"  # 回复回来了但不能用：两次之后记"判不下来"并问人
        return review_reply(data)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner, reviewer=reviewer)) as world:
            mission_id = world.create({"goal": "写 a.md", "idempotency_key": "amend-inflight-question",
                                       "success_criteria": ["file:a.md"]})["mission_id"]
            holder.update(world=world, mission_id=mission_id)
            humans = PlanningHumanStore(world.store)
            for _ in range(30):
                await world.drain(timeout=20)
                if any(row["state"] == "PENDING" for row in humans.list(mission_id)):
                    break
            events = list(world.store.list_events(mission_id))
            stale = [e for e in events if e.type == "PlanningRejected" and "REQUEST_BINDING_STALE" in _refusal_codes(e)]
            assert len(stale) == 1 and not refusal_charges_planner(_refusal_codes(stale[0]))  # ①
            [question] = [row for row in humans.list(mission_id) if row["state"] == "PENDING"]
            assert str(question["decision_id"]).startswith("adjudicate-root:")
            amend(world, mission_id, [{"op": "add", "statement": "file:c.md"}], command_id="amend-question")  # ②
            mission = await world.run_until_settled(mission_id, rounds=40)
            assert humans.get(question["decision_id"])["state"] == "STALE"
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            [judged] = [e.payload for e in world.store.list_events(mission_id) if e.type == "MissionSuccessJudged"]
            assert [j["criterion"] for j in judged["judgments"]] == ["file:a.md", "file:b.md", "file:c.md"]

    asyncio.run(case())


def test_knowledge_goes_stale_when_requirements_are_amended(tmp_path):
    """第 1 步的结论被审阅员确认入库。用户改要求的那一刻它就读成"已过时"（它的依据是按旧版通过的验收）；
    新计划提交、任务完成后仍是过时。判定只有一处：完成度读取。"""
    import importlib.util
    import sys
    from pathlib import Path

    from agent_orchestrator.memory.knowledge_standing import knowledge_standing

    spec = importlib.util.spec_from_file_location(
        "_knowledge_script", Path(__file__).with_name("test_knowledge_confirm.py"))
    script = importlib.util.module_from_spec(spec)
    sys.modules["_knowledge_script"] = script
    spec.loader.exec_module(script)
    seen: dict[str, Any] = {}

    async def case():
        provider = SlowSecondStep(planner=replanning_planner(seen), worker=script.two_claims,
                                  reviewer=script.confirming(script.first_claim_by_artifact))
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md 和 b.md", "idempotency_key": "amend-knowledge",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            store = world.store
            for _ in range(20):
                await world.drain(timeout=20)
                if store.list_knowledge(mission_id):
                    break
            [record] = store.list_knowledge(mission_id)
            assert knowledge_standing(store, record) == "CURRENT"
            amend(world, mission_id, [{"op": "add", "statement": "file:extra.md"}])
            assert knowledge_standing(store, record) == "STALE:step_no_longer_rests_on_this_acceptance"
            provider.go.set()
            mission = await world.run_until_settled(mission_id, rounds=40)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            assert knowledge_standing(store, store.get_knowledge(record.id)).startswith("STALE:")

    asyncio.run(case())


def _script(name: str) -> Any:
    """另一份用例文件里的剧本（同目录），按文件载入。"""
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(f"_amend_{name}", Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_amend_passes_the_same_door_as_creating_a_mission(tmp_path):
    """改要求过的是建任务的同一道门（阶段 E 核验阻断 2）：写错的 ``action:``、部署不会执行的操作在门口
    就拒掉，一样都不写——不会等到规划时才把正在跑的任务弄成失败。"""
    from agent_orchestrator.governance.policies import DeploymentPolicy
    from agent_orchestrator.runtime.connectors_publish import FilePublishConnector

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner_reply),
                                 connectors={"file_publish": connector}, deployment_policy=policy) as world:
            mission_id = world.create({"goal": "写一份周报", "success_criteria": ["file:reports/weekly.md"],
                                       "idempotency_key": "amend-door"})["mission_id"]
            before = written(world.store, mission_id)
            for bad in ("action:file_publish", "action:file_publish.delete:reports/weekly.md",
                        "action:no_such_connector.publish:x"):
                with pytest.raises(FacadeError) as refused:
                    amend(world, mission_id, [{"op": "add", "statement": bad}], command_id="bad-" + bad)
                assert refused.value.code in {"AMEND_REQUIREMENT_REFUSED", "invalid_request"}, bad
            assert written(world.store, mission_id) == before

    asyncio.run(case())


def test_amend_that_adds_a_publish_is_judged_as_an_operation_mission(tmp_path):
    """建任务时只有内容要求，后来加了一条"发布这个文件"：带操作的那一版等人确认；发布批准并生效后，
    收尾按现行要求走"先核操作、再逐条判定"，任务完成（阶段 E 核验阻断 1：收尾曾按建任务时的要求判）。"""
    from agent_orchestrator.governance.policies import DeploymentPolicy
    from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
    from agent_orchestrator.testing.scripted_replies import decision, one_step_method

    operation = _script("test_operation")

    def planner(request: Any) -> Any:  # 有可用做法就采用，没有就提一步做法；"要求已更新"此时没有旧计划可换
        package = package_of(request)
        goals = [item for item in package["views"]["goals"] if item["open"]]
        if not goals:
            return planner_reply(request)
        selection = (package.get("method_selection") or [{}])[0]
        if selection.get("applicable"):
            chosen = selection["applicable"][0]
            return decision(goals[0]["subject_key"], "REFINE", {
                "method_ref": {"kind": "method", "id": chosen["method_id"],
                               "semantic_revision": chosen["method_version"],
                               "content_hash": chosen["method_content_hash"]},
                "bindings": dict(selection.get("bindings") or goals[0]["params"])}, "采用通过审阅的做法。")
        context = (package.get("method_proposal_contexts") or [None])[0]
        return decision(context["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
            "method": one_step_method(context), "rationale": "一步写出周报。"}}, "一步写出周报。")

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner),
                                 connectors={"file_publish": connector}, deployment_policy=policy) as world:
            mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                                       "success_criteria": ["file:" + operation.TARGET],
                                       "idempotency_key": "amend-adds-publish"})["mission_id"]
            amend(world, mission_id, [{"op": "add", "statement": operation.PUBLISH}])
            await world.drain()
            # 带操作的那一版系统不代确认：等人
            assert operation._workspace(world, mission_id)["state"] == "CONFIRMATION_REQUIRED"
            operation._confirm_completion(world, mission_id)
            approvals: list[dict[str, Any]] = []
            for _ in range(20):
                await world.drain()
                approvals = [a for a in world.control.approvals(mission_id) if a.get("state") == "PENDING"]
                if approvals:
                    break
            assert approvals, world.store.get_mission(mission_id).status
            world.control.decide(approvals[0]["request_id"], "approve")
            mission = await world.run_until_settled(mission_id, rounds=20)
            events = list(world.store.list_events(mission_id))
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            assert not [e.payload for e in events if e.type == "MissionRoundFault"]
            [judged] = [e.payload for e in events if e.type == "MissionSuccessJudged"]
            assert [j["criterion"] for j in judged["judgments"]] == ["file:" + operation.TARGET, operation.PUBLISH]

    asyncio.run(case())


def test_amend_refused_while_closing_out_and_after_the_end(tmp_path, monkeypatch):
    """任务已在收尾（根结论已采纳、还没写完成）→ ``AMEND_AFTER_CLOSEOUT``；任务已结束 →
    ``AMEND_MISSION_TERMINAL``；两种都一样不写（阶段 E 欠的断言，HTN 补齐 F1）。

    **改坏检验**：去掉收尾判断 → 收尾中的任务被改了要求 → 变红。"""
    import agent_orchestrator.orchestrator.assurance_assembly as assembly
    import agent_orchestrator.orchestrator.assurance_final_writer as final_writer

    real = final_writer.finalize_assured_mission
    held = {"on": True}

    def finalize(*args: Any, **kwargs: Any) -> Any:
        if held["on"]:
            raise final_writer.AssuranceError("RECHECK_REQUIRED", "held by the test")
        return real(*args, **kwargs)

    monkeypatch.setattr(final_writer, "finalize_assured_mission", finalize)
    monkeypatch.setattr(assembly, "finalize_assured_mission", finalize)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner_reply)) as world:
            mission_id = world.create({"goal": "写一份笔记", "idempotency_key": "amend-closing",
                                       "success_criteria": ["file:notes.md"]})["mission_id"]
            htn = HtnStore(world.store)
            for _ in range(20):
                await world.drain(timeout=20)
                if htn.adopted_goal_resolution(mission_id, f"user-duty-{mission_id}") is not None:
                    break
            assert htn.adopted_goal_resolution(mission_id, f"user-duty-{mission_id}") is not None
            assert str(world.store.get_mission(mission_id).status.value) == "ACTIVE"
            before = written(world.store, mission_id)
            with pytest.raises(FacadeError) as refused:
                amend(world, mission_id, [{"op": "add", "statement": "file:more.md"}], command_id="closing")
            assert refused.value.code == "AMEND_AFTER_CLOSEOUT"
            assert written(world.store, mission_id) == before

            held["on"] = False
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert str(mission.status.value) == "COMPLETED"
            before = written(world.store, mission_id)
            with pytest.raises(FacadeError) as refused:
                amend(world, mission_id, [{"op": "add", "statement": "file:more.md"}], command_id="ended")
            assert refused.value.code == "AMEND_MISSION_TERMINAL"
            assert written(world.store, mission_id) == before

    asyncio.run(case())
