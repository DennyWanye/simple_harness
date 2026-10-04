# SPDX-License-Identifier: Apache-2.0
"""改要求后沿用的步骤与它的下游（TaskGraph 补全第五批）。

两步接力：s1 写 a.md，s2 读 s1 的产出写 b.md。用户在 s2 做完之前改了 b.md 那条要求；规划器只
换掉 s2，s1 留着、由审阅员按新版重审。

* 重审通过：换上来的 s2 开工时冻结的输入引用的是 s1 **按新版要求**的那条验收，不是旧版那条。
* 重审没过、下游还没通过：规划器把 s1 换成后继步骤，下游改读后继的产出，任务按新版完成。
* 重审没过、下游已通过（三步局面，s2 早已做完）：单换 s1 被退回（``REPAIR_NOT_ALLOWED``），
  规划器改用换做法，全部按新版重做，任务完成。

步骤的产出只有一个版本——重做一律是后继步骤；数据边一律跟随现行算数的验收（没有"钉住"）。

**改坏检验**：TG5-01 下游取验收时不筛"现行算数的" → 第一条变红。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.orchestrator.assurance_validity import acceptance_id_for
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import decision, planner_reply


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


amend_script = _load("_amend_script_redo", "test_requirements_amend.py")
carried = _load("_carried_script_redo", "test_carried_review.py")
relay_script = _load("_relay_script_redo", "test_input_revisions.py")


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _frozen_inputs(world: Any, mission_id: str, task_id: str) -> list[dict[str, Any]]:
    """这个任务每次尝试冻结的输入（按尝试先后）。"""
    return [binding for (raw,) in world.store.connection.execute(
        "SELECT m.manifest_json FROM taskgraph_attempt_inputs b"
        " JOIN input_manifests m ON m.manifest_hash=b.manifest_hash"
        " JOIN attempts a ON a.attempt_id=b.attempt_id"
        " WHERE b.mission_id=? AND a.task_id=? ORDER BY a.ordinal", (mission_id, task_id))
        for binding in json.loads(raw)["bindings"]]


def _relay_planner(state: dict[str, Any], *, on_rejected: Any = None):
    """提两步接力的做法并采用；改要求后只换掉没做完的那一步；重审打回时交给 ``on_rejected``。"""

    def planner(request: Any) -> Any:
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        requests = package.get("repair_requests") or ()
        sources = {entry["request"].get("trigger_source") for entry in requests}
        if contexts and not state.get("root"):
            state["root"] = True
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": relay_script.relay(contexts[0]), "rationale": "先写 a.md，再接着写 b.md。"}}, "两步接力。")
        steps = [item for item in package["views"]["goals"] if item["form"] == "primitive"]
        done = {row["producer_occurrence"] for row in package["views"]["accepted_results"]}
        if "REQUIREMENTS_UPDATE" in sources and not state.get("second"):
            state["second"] = True
            [unfinished] = [item for item in steps if item["occurrence_id"] not in done]
            return carried.successor(package, unfinished, "要求改了：只换掉还没做完的那一步。")
        rejected = carried._carried_rejections(package)
        if rejected and on_rejected is not None:
            reply = on_rejected(package, rejected, steps)
            if reply is not None:
                return reply
        if requests:
            return None
        return planner_reply(request)

    return planner


async def _until_first_acceptance(world: Any, mission_id: str) -> Any:
    htn = HtnStore(world.store)
    for _ in range(20):
        await world.drain(timeout=20)
        if htn.list_acceptances(mission_id):
            break
    [first] = htn.list_acceptances(mission_id)
    return first


async def _settled(world: Any, mission_id: str, state: dict[str, Any]) -> Any:
    try:
        return await asyncio.wait_for(world.run_until_settled(mission_id, rounds=60), 240)
    except TimeoutError as error:
        raise AssertionError(f"the mission never settled: {state}") from error


def test_the_replaced_downstream_reads_the_kept_step_under_its_new_acceptance(tmp_path):
    state: dict[str, Any] = {}

    async def case():
        provider = carried.HeldStep("b.md", planner=_relay_planner(state))
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md 再写 b.md", "idempotency_key": "redo-follow",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            first = await _until_first_acceptance(world, mission_id)
            amend_script.amend(world, mission_id,
                               [{"op": "rewrite", "criterion_id": "c-user-2", "statement": "file:b2.md"}])
            provider.go.set()
            mission = await _settled(world, mission_id, state)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report, state)
            kept = str(first.task_id)
            result_id = world.store.get_task(kept).accepted_result_id
            assert len(world.store.list_attempts(kept)) == 1  # s1 没重做
            [downstream] = [task for task in world.store.list_tasks(mission_id) if tuple(task.outputs) == ("b2.md",)]
            bound = _frozen_inputs(world, mission_id, downstream.id)
            assert bound, "the replaced downstream step froze no input"
            assert {item["bound_input"]["acceptance_id"] for item in bound} == {
                acceptance_id_for(kept, result_id, 2)}  # 新版验收，不是旧版那条
            assert {item["bound_input"]["producer_result_id"] for item in bound} == {result_id}

    asyncio.run(case())


def test_a_rejected_kept_step_is_redone_by_a_successor_and_the_waiting_downstream_reads_it(tmp_path):
    state: dict[str, Any] = {}
    reviews: dict[str, int] = {}

    def on_rejected(package: dict[str, Any], rejected: list[dict[str, Any]], steps: list[dict[str, Any]]) -> Any:
        if state.get("redone"):
            return None
        state["redone"] = True
        kept = next(item for item in steps if item["occurrence_id"] == rejected[0]["context"]["occurrence_id"])
        return carried.successor(package, kept, "沿用的 a.md 那一步按新版没过：换掉重做。")

    async def case():
        provider = carried.HeldStep("b.md", planner=_relay_planner(state, on_rejected=on_rejected),
                                    reviewer=carried.reject_second_review_of("c-user-1", reviews))
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md 再写 b.md", "idempotency_key": "redo-successor",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            first = await _until_first_acceptance(world, mission_id)
            amend_script.amend(world, mission_id,
                               [{"op": "rewrite", "criterion_id": "c-user-2", "statement": "file:b2.md"}])
            provider.go.set()
            mission = await _settled(world, mission_id, state)
            rejections = [e.payload for e in world.store.list_events(mission_id)
                          if e.type == "PlanningDecisionEvaluated" and e.payload.get("status") == "REJECTED"]
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report, state,
                                                              rejections[-2:])
            assert state.get("redone")
            old = str(first.task_id)
            writers = [task for task in world.store.list_tasks(mission_id) if tuple(task.outputs) == ("a.md",)]
            [redo] = [task for task in writers if task.id != old]  # 后继步骤：一个新任务，旧任务留作历史
            assert len(world.store.list_attempts(old)) == 1
            [downstream] = [task for task in world.store.list_tasks(mission_id) if tuple(task.outputs) == ("b2.md",)]
            bound = _frozen_inputs(world, mission_id, downstream.id)
            assert bound and {item["bound_input"]["producer_result_id"] for item in bound} == {
                redo.accepted_result_id}  # 下游读的是后继步骤的产出

    asyncio.run(case())


def test_replacing_a_kept_step_alone_is_refused_when_its_downstream_already_passed(tmp_path):
    state: dict[str, Any] = {"root": False, "second": False, "tried": False, "proposed": None, "replaced": False}
    reviews: dict[str, int] = {}

    def planner(request: Any) -> Any:
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        requests = package.get("repair_requests") or ()
        sources = {entry["request"].get("trigger_source") for entry in requests}
        if contexts and not state["root"]:
            state["root"] = True
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": carried.relay_then_third(contexts[0]), "rationale": "先写、接着写，另写一份。"}}, "三步。")
        steps = [item for item in package["views"]["goals"] if item["form"] == "primitive"]
        done = {row["producer_occurrence"] for row in package["views"]["accepted_results"]}
        if "REQUIREMENTS_UPDATE" in sources and not state["second"]:
            state["second"] = True
            [unfinished] = [item for item in steps if item["occurrence_id"] not in done]
            return carried.successor(package, unfinished, "要求改了：只换掉还没做完的那一步。")
        rejected = carried._carried_rejections(package)
        if rejected and not state["tried"]:
            state["tried"] = True  # 先试单换这一步：下游 s2 已通过，应被退回
            kept = next(item for item in steps if item["occurrence_id"] == rejected[0]["context"]["occurrence_id"])
            return carried.successor(package, kept, "单换没过的那一步。")
        if state["tried"] and not state["replaced"]:
            goal = next(item for item in package["views"]["goals"]
                        if item["form"] == "compound" and item.get("adopted_method"))
            if state["proposed"] is None:
                [context] = [item for item in contexts if item["subject_key"] == goal["subject_key"]]
                method = carried.relay_then_third(context)
                state["proposed"] = (method["method_id"], method["method_version"])
                return decision(goal["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                    "method": method, "rationale": "连同下游一起按新版重做。"}}, "换做法。")
            fresh = [item["method_ref"] for item in package["views"]["methods"]
                     if (item["method_ref"]["id"], item["method_ref"]["semantic_revision"]) == state["proposed"]
                     and (item.get("review") or {}).get("outcome") == "PASSED"]
            if fresh:
                state["replaced"] = True
                instance = next(item for item in package["visible_refs"] if item["kind"] == "method_instance"
                                and item["id"] == goal["adopted_method"]["method_instance_id"])
                return decision(goal["subject_key"], "REPAIR", {
                    "repair_kind": "REPLACE_METHOD", "rejected_method_instance": instance,
                    "replacement_method_ref": dict(fresh[-1]), "bindings": goal["params"]}, "换成按新要求写的做法。")
            return None
        if requests:
            return None
        return planner_reply(request)

    def reviewer(request: Any) -> Any:
        base = carried.reject_second_review_of("c-user-1", reviews)
        return base(request)

    async def case():
        provider = carried.HeldStep("c.md", planner=planner, reviewer=reviewer)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md，据它写 b.md，另写 c.md", "idempotency_key": "redo-refused",
                                       "success_criteria": ["file:a.md", "file:b.md", "file:c.md"]})["mission_id"]
            htn = HtnStore(world.store)
            for _ in range(40):
                await world.drain(timeout=20)
                if len(htn.list_acceptances(mission_id)) >= 2:
                    break
            assert len(htn.list_acceptances(mission_id)) == 2
            amend_script.amend(world, mission_id,
                               [{"op": "rewrite", "criterion_id": "c-user-3", "statement": "file:c2.md"}])
            provider.go.set()
            mission = await _settled(world, mission_id, state)
            evaluated = [e.payload for e in world.store.list_events(mission_id)
                         if e.type == "PlanningDecisionEvaluated" and e.payload.get("status") != "COMMITTED"
                         and e.payload.get("decision_type") == "REPAIR"]
            refused = [item for item in evaluated if "REPAIR_NOT_ALLOWED" in json.dumps(item, ensure_ascii=False)]
            assert state["tried"] and refused, (state, [json.dumps(i, ensure_ascii=False)[:500] for i in evaluated[-3:]])
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report, state,
                                                              evaluated[-2:])
            assert state["replaced"]
            [judged] = [e.payload for e in world.store.list_events(mission_id) if e.type == "MissionSuccessJudged"]
            assert [j["criterion"] for j in judged["judgments"]] == ["file:a.md", "file:b.md", "file:c2.md"]
            assert judged["met"]

    asyncio.run(case())
